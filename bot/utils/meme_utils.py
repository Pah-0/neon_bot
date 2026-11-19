"""
Random Reddit memes. Posts come from Arctic Shift's archive of Reddit, from a
random point in the subreddit's history, so any meme it ever had can come up;
meme-api.com, which only keeps a few dozen of each subreddit's current hot
posts, is the fallback.
"""

import html
import random
import re
import time
from dataclasses import dataclass

import aiohttp

from bot import bot, jid
from bot.pkgs.reddit_dl import HEADERS
from bot.utils.bot_utils import png_to_jpg
from bot.utils.db_utils import save2db2
from bot.utils.log_utils import logger
from bot.workers.auto.schedule import scheduler2

ARCTIC_SHIFT_API = "https://arctic-shift.photon-reddit.com/api"
MEME_API = "https://meme-api.com/gimme"
# Picked from when no subreddit is given
DEFAULT_SUBS = ("memes", "dankmemes", "me_irl", "wholesomememes")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".gif")
# Newer posts are skipped: their scores haven't settled yet
SETTLE_SECONDS = 7 * 24 * 3600
# A random pick from this many of the best-scored posts around the chosen time
TOP_POSTS = 10
TIMEOUT = aiohttp.ClientTimeout(total=30)

# Links of memes sent since the last restart, so they don't come up again soon
sent = set()
# Subreddit name -> (created_utc, over18), from Arctic Shift
sub_info = {}


class NoMeme(Exception):
    """No meme to send, with the reason to show"""


@dataclass
class Meme:
    title: str
    url: str
    link: str
    author: str
    subreddit: str
    nsfw: bool

    @property
    def caption(self) -> str:
        nsfw = "*🔞 NSFW*\n" if self.nsfw else ""
        return (
            f"{nsfw}*{self.title.strip()}*\n{self.link}\n\n"
            f"By u/{self.author} in r/{self.subreddit}"
        )


def remember(link: str):
    if len(sent) > 10000:
        sent.clear()
    sent.add(link)


async def get_sub_info(session, sub: str) -> tuple[int, bool] | None:
    if sub.casefold() in sub_info:
        return sub_info[sub.casefold()]
    async with session.get(
        f"{ARCTIC_SHIFT_API}/subreddits/search",
        params={"subreddit": sub, "fields": "created_utc,over18"},
    ) as resp:
        resp.raise_for_status()
        found = (await resp.json()).get("data") or []
    if not found:
        return None
    info = sub_info[sub.casefold()] = (
        int(found[0]["created_utc"]),
        bool(found[0].get("over18")),
    )
    return info


async def image_is_up(session, url: str) -> bool:
    # Deleted i.redd.it images are 404s; deleted imgur ones redirect
    try:
        async with session.head(url, allow_redirects=False) as resp:
            return resp.status == 200 and resp.content_type.startswith("image/")
    except Exception:
        return False


async def arctic_meme(session, sub: str, allow_nsfw: bool) -> Meme | None:
    """A meme from a random point in the subreddit's history, or None"""
    info = await get_sub_info(session, sub)
    if not info:
        return None
    created, over18 = info
    if over18 and not allow_nsfw:
        raise NoMeme("NSFW is blocked!")
    latest = time.time() - SETTLE_SECONDS
    blocked = False
    # A second try in case the first window had nothing usable
    for _ in range(2):
        after = int(random.uniform(created, max(created, latest)))
        async with session.get(
            f"{ARCTIC_SHIFT_API}/posts/search",
            params={"subreddit": sub, "after": after, "sort": "asc", "limit": 100},
        ) as resp:
            resp.raise_for_status()
            posts = (await resp.json()).get("data") or []
        posts = [
            p
            for p in posts
            if (p.get("url") or "").split("?")[0].lower().endswith(IMAGE_EXTS)
            and not p.get("removed_by_category")
            and p.get("author") != "[deleted]"
            and f"https://redd.it/{p['id']}" not in sent
        ]
        if not allow_nsfw:
            blocked = blocked or any(p.get("over_18") for p in posts)
            posts = [p for p in posts if not p.get("over_18")]
        posts.sort(key=lambda p: p.get("score") or 0, reverse=True)
        posts = posts[:TOP_POSTS]
        random.shuffle(posts)
        for post in posts[:5]:
            if not await image_is_up(session, post["url"]):
                continue
            return Meme(
                title=html.unescape(post.get("title") or ""),
                url=post["url"],
                link=f"https://redd.it/{post['id']}",
                author=post.get("author"),
                subreddit=post.get("subreddit") or sub,
                nsfw=bool(post.get("over_18")),
            )
    if blocked:
        raise NoMeme("NSFW is blocked!")
    return None


async def meme_api_meme(session, sub: str | None, allow_nsfw: bool) -> Meme:
    # It only keeps a few dozen posts per subreddit, so get them all at once
    # and pick one not sent yet; once all are, start over.
    link = f"{MEME_API}/{sub}/50" if sub else f"{MEME_API}/50"
    async with session.get(link) as resp:
        result = await resp.json()
    memes = [m for m in result.get("memes") or [] if m.get("title")]
    if not memes:
        raise NoMeme(result.get("message") or "Request Failed!")
    if not allow_nsfw:
        if not (sfw := [m for m in memes if not m.get("nsfw")]):
            raise NoMeme("NSFW is blocked!")
        memes = sfw
    unseen = [m for m in memes if m.get("postLink") not in sent]
    if not unseen:
        for m in memes:
            sent.discard(m.get("postLink"))
        unseen = memes
    result = random.choice(unseen)
    return Meme(
        title=result["title"],
        url=result.get("url"),
        link=result.get("postLink"),
        author=result.get("author"),
        subreddit=result.get("subreddit"),
        nsfw=bool(result.get("nsfw")),
    )


async def random_meme(sub: str | None = None, allow_nsfw: bool = True) -> Meme:
    """
    A random meme from the subreddit (default: one of DEFAULT_SUBS); raises
    NoMeme when there's none to send
    """
    async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
        meme = None
        try:
            meme = await arctic_meme(
                session, sub or random.choice(DEFAULT_SUBS), allow_nsfw
            )
        except NoMeme:
            raise
        except Exception:
            await logger(Exception)
        if not meme:
            meme = await meme_api_meme(session, sub, allow_nsfw)
    remember(meme.link)
    return meme


async def meme_media(meme: Meme) -> str | bytes:
    """What to send: the URL, or a PNG converted to JPEG"""
    if meme.url.lower().endswith(".png"):
        async with aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT) as session:
            async with session.get(meme.url) as resp:
                resp.raise_for_status()
                return await png_to_jpg(await resp.read())
    return meme.url


# Auto-posting memes to groups

INTERVAL_RE = re.compile(r"(\d+)([mhd])")
UNITS = {"m": 1, "h": 60, "d": 24 * 60}
MIN_MINUTES = 15
MAX_MINUTES = 7 * 24 * 60


def parse_interval(text: str) -> int | None:
    """Minutes in an interval like 30m, 2h or 1d, or None"""
    match = INTERVAL_RE.fullmatch(text.strip().lower())
    if not match:
        return None
    return int(match.group(1)) * UNITS[match.group(2)]


def format_interval(minutes: int) -> str:
    for unit in ("d", "h"):
        if minutes % UNITS[unit] == 0:
            return f"{minutes // UNITS[unit]}{unit}"
    return f"{minutes}m"


async def post_meme(chat_jid, sub: str | None):
    # Never NSFW: nobody asked for this one
    meme = await random_meme(sub, allow_nsfw=False)
    media = await meme_media(meme)
    if meme.url.lower().endswith(".gif"):
        await bot.client.send_video(
            chat_jid, media, meme.caption, gifplayback=True, is_gif=True
        )
    else:
        await bot.client.send_image(chat_jid, media, meme.caption)


async def run_automemes():
    """Posts a meme to each group whose auto-meme is due"""
    now = time.time()
    for chat_id, group in list(bot.group_dict.items()):
        if not isinstance(group, dict) or not (auto := group.get("automeme")):
            continue
        if auto.get("next", 0) > now:
            continue
        # Counted from now, so a bot that was down posts once, not a backlog
        auto["next"] = now + auto["every"] * 60
        await save2db2(bot.group_dict, "groups")
        try:
            await post_meme(jid.build_jid(chat_id, "g.us"), auto.get("sub"))
        except NoMeme:
            pass
        except Exception:
            await logger(Exception)


scheduler2.add_job(
    id="automeme",
    func=run_automemes,
    trigger="cron",
    minute="*",
    misfire_grace_time=None,
    max_instances=1,
)

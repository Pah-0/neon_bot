import random
import time
from inspect import getdoc
from urllib.parse import quote_plus

from bot.config import bot, conf
from bot.fun.stickers import SMIRNOFF, random_sticker
from bot.utils.bot_utils import get_json, space_commands
from bot.utils.db_utils import save2db2
from bot.utils.log_utils import logger
from bot.utils.meme_utils import (
    MAX_MINUTES,
    MIN_MINUTES,
    NoMeme,
    format_interval,
    meme_media,
    parse_interval,
    random_meme,
)
from bot.utils.msg_utils import (
    chat_is_allowed,
    clean_reply,
    construct_msg_and_evt,
    user_is_admin,
    user_is_allowed,
    user_is_privileged,
)

from .media_dl import youtube_reply


async def fun(event, args, client):
    """
    Shows the list of fun commands.

    Arguments: None
    """
    try:
        pre = conf.CMD_PREFIX
        s = "\n"
        msg = (
            f"{pre}cat - *Get a random cat pic/gif*{s}"
            f"{pre}coub [topic] - *Get a random coub video link (default: Anime)*{s}"
            f"{pre}dog - *Get a random dog pic/video*{s}"
            f"{pre}gif [search] - *Get a random GIF matching search (default: anime)*{s}"
            f"{pre}gsticker ({pre}gs) [search] - *Get a random sticker matching search (default: anime)*{s}"
            f"{pre}meme [subreddit] - *Get a random reddit meme*{s}"
            f"{pre}automeme <interval> [subreddit] - *[Owner | Sudo | Admin] Post a random meme to the group every so often, e.g. 2h (off to stop)*{s}"
            f"{pre}smirnoff - *Get a random Smirnoff sticker*"
        )
        await event.reply(space_commands(msg, pre))
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def getmeme(event, args, client):
    """
    Sends a random reddit meme as a photo or gif, from any time in the
    subreddit's history (via Arctic Shift; meme-api.com when that fails).
    NSFW memes are blocked in groups when NSFW blocking is on; otherwise in groups they are sent as view-once.

    Arguments:
        subreddit: [Optional] subreddit to fetch from (default: memes, dankmemes, me_irl or wholesomememes; numeric input is ignored)
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        async with event.react("🌐"):
            sub = args.strip().removeprefix("r/") if args else None
            group = event.chat.is_group
            try:
                meme = await random_meme(
                    None if not sub or sub.isdigit() else sub,
                    allow_nsfw=not (
                        group
                        and bot.block_nsfw
                        and bot.group_dict.get(event.chat.id, {}).get("nsfw", True)
                    ),
                )
            except NoMeme as e:
                return await event.reply(f"*{e}*")
            viewonce = meme.nsfw and group
            media = await meme_media(meme)
            if meme.url.lower().endswith(".gif"):
                return await event.reply_gif(
                    caption=meme.caption,
                    gif=media,
                    viewonce=viewonce,
                    as_gif=True,
                )
            await event.reply_photo(
                caption=meme.caption,
                photo=media,
                viewonce=viewonce,
            )
    except Exception as e:
        await logger(Exception)
        return await event.reply(f"*Error:*\n{e}")


async def automeme(event, args, client):
    """
    [Owner | Sudo | Admin] Post a random meme to this group every so often.
    Auto-posted memes are never NSFW. Group only. With no arguments, shows
    the setting.

    Usage: automeme <interval> [subreddit]
               e.g. automeme 2h, automeme 30m dankmemes
           automeme off
        interval: like 30m, 2h or 1d (15m to 7d)
        subreddit: default: memes, dankmemes, me_irl or wholesomememes
    """
    try:
        if not event.chat.is_group:
            return await event.react("🚫")
        user = event.from_user.id
        if not user_is_privileged(user):
            group_info = await client.get_group_info(event.chat.jid)
            if not user_is_admin(user, group_info.Participants):
                return await event.react("🚫")
        group = bot.group_dict.setdefault(event.chat.id, {})
        words = (args or "").split()
        if words == ["off"]:
            group.pop("automeme", None)
            await save2db2(bot.group_dict, "groups")
            return await event.reply("*Auto memes are off.*")
        if words:
            minutes = parse_interval(words[0])
            if (
                len(words) > 2
                or not minutes
                or not MIN_MINUTES <= minutes <= MAX_MINUTES
            ):
                return await event.reply(getdoc(automeme))
            sub = words[1].removeprefix("r/") if len(words) > 1 else None
            group["automeme"] = {
                "every": minutes,
                "sub": sub,
                "next": time.time() + minutes * 60,
            }
            await save2db2(bot.group_dict, "groups")
        if not (auto := group.get("automeme")):
            return await event.reply(f"*No auto memes here.*\n\n{getdoc(automeme)}")
        source = f"r/{auto['sub']}" if auto.get("sub") else "Reddit"
        await event.reply(
            f"*A random meme from {source} is posted here every "
            f"{format_interval(auto['every'])}.*"
        )
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def cat(event, args, client):
    """
    Fetches a random cat pic or gif from thecatapi.com.

    Arguments: None
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        async with event.react(random.choice(("🐈‍⬛", "🐈"))):
            result = await get_json("https://api.thecatapi.com/v1/images/search")
            if not result:
                return await event.reply("*Request Failed!*")
            url = result[0]["url"]
            if url.endswith(".gif"):
                await event.reply_gif(
                    url,
                    caption="*Meow!*",
                    as_gif=True,
                )
            else:
                await event.reply_photo(url, caption="*Meow!*")
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def coub(event, args, client):
    """
    Searches coub for a topic and replies with the title and link of a random result.
    If the group has ytdl enabled and the coub has a download link, the video is also uploaded.

    Arguments:
        topic: [Optional] what to search for (default: Anime)
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        async with event.react("🎦"):
            await coub_helper(event, args, client)
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def coub_helper(event, args, client):
    if not args:
        args = "Anime"
    random_ = ""
    page = 0
    while True:
        if page:
            random_ = f"&page={page}"
        result = await get_json(
            f"https://coub.com/api/v2/search/coubs?q={quote_plus(args)}{random_}"
        )
        if not result:
            return await event.reply("*Request Failed!*")
        if page:
            break
        total_pages = max(result["total_pages"] or 1, 1)
        page = random.randint(1, total_pages)

    try:
        content = random.choice(result["coubs"])
        dl_link = None
        external_dl = content["external_download"]
        permalink = content["permalink"]
        if external_dl:
            dl_link = external_dl["url"]
        title = content["title"]
    except IndexError:
        await event.reply("Couldn't fetch video…")
    else:
        ytdl = bot.group_dict.get(event.chat.id, {}).get("ytdl")
        dl_msg = "\n\n*Attempting to upload…*" if ytdl and dl_link else ""
        text = f"*Title:* {title}\n*Link:* https://coub.com/view/{permalink}{dl_msg}"
        rep = await event.reply(text)
        if dl_msg:
            event_ = construct_msg_and_evt(
                event.chat.id, bot.client.me.JID.User, rep.id, text, event.chat.server
            )
            await youtube_reply(event_, dl_link, client)


async def dog(event, args, client):
    """
    Fetches a random dog pic or video from random.dog.

    Arguments: None
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        async with event.react("🐾"):
            result = await get_json("https://random.dog/woof.json")
            if not result:
                return await event.reply("*Request Failed!*")
            url = result["url"]
            if url.casefold().endswith(".mp4"):
                return await event.reply_video(
                    url,
                    caption="*Woof!*",
                )
            await event.reply_photo(url, caption="*Woof!*")
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def gif(event, args, client):
    """
    Sends a random GIF from Klipy matching a search term; requires KLIPY_API_KEY.
    If used as a reply, the GIF is sent in reply to the replied message.

    Arguments:
        search_term: [Optional] what to search for (default: anime)
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        async with event.react("🔎"):
            await gif_helper(event, args, client)
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def klipy_search(query, pages=2, **params):
    """
    Search Klipy's Tenor-compatible API, following `next` for up to `pages`
    pages (50 results each) so random picks have a bigger pool.
    Klipy stops at 2 pages (100 results) per query.
    Returns None if the first request fails.
    """
    url = (
        f"https://api.klipy.com/v2/search?"
        f"key={conf.KLIPY_API_KEY}"
        f"&q={quote_plus(query)}"
        f"&limit=50"
        f"&client_key=neon"
    ) + "".join(f"&{k}={v}" for k, v in params.items())
    results, seen, pos = [], set(), None
    for _ in range(pages):
        result = await get_json(f"{url}&pos={quote_plus(pos)}" if pos else url)
        if not result:
            return None if not results else results
        for item in result.get("results") or []:
            if item.get("id") not in seen:
                seen.add(item.get("id"))
                results.append(item)
        # an empty or "0" next means there are no more results
        if not (pos := result.get("next")) or pos == "0":
            break
    return results


async def gif_helper(event, args, client):
    """Prevents indentation hell"""
    if not conf.KLIPY_API_KEY:
        return await event.reply("KLIPY_API_KEY is needed for this function to work.")

    if not args:
        args = "anime"

    results = await klipy_search(args)

    if results is None:
        return await event.reply("*Request Failed!*")

    if not results:
        return await event.reply("*No results!*")

    gif_link = random.choice(results)["media_formats"]["gif"]["url"]

    await clean_reply(
        event,
        event.reply_to_message,
        "reply_gif",
        gif_link,
    )


async def sticker(event, args, client):
    """
    Sends a random sticker from Klipy matching a search term; requires KLIPY_API_KEY.
    If used as a reply, the sticker is sent in reply to the replied message.

    Arguments:
        search_term: [Optional] what to search for (default: anime)
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        async with event.react("🔍"):
            await sticker_helper(event, args, client)
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def sticker_helper(event, args, client):
    """Prevents indentation hell"""
    if not conf.KLIPY_API_KEY:
        return await event.reply("KLIPY_API_KEY is needed for this function to work.")

    if not args:
        args = "anime"

    results = await klipy_search(args, searchfilter="sticker")

    if results is None:
        return await event.reply("*Request Failed!*")

    if not results:
        return await event.reply("*No results!*")

    sticker = random.choice(results)

    duration = sticker["media_formats"]["gif"].get("duration", 0)
    animated = bool(duration)

    media = (
        sticker["media_formats"].get("gif_transparent")
        or sticker["media_formats"]["gif"]
    )

    link = media["url"]

    me = await bot.client.get_me()

    await clean_reply(
        event,
        event.reply_to_message,
        "reply_sticker",
        link,
        name=args,
        packname=me.PushName,
        enforce_not_broken=True,
        animated_gif=animated,
    )


async def smirnoff(event, args, client):
    """
    Sends a random sticker of Smirnoff.
    If used as a reply, the sticker is sent in reply to the replied message.
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        sticker = await random_sticker(pick=lambda: random.choice(SMIRNOFF))
        if not sticker:
            return await event.reply("*Request Failed!*")
        me = await bot.client.get_me()
        await clean_reply(
            event,
            event.reply_to_message,
            "reply_sticker",
            sticker,
            name="Smirnoff",
            packname=me.PushName,
        )
    except Exception:
        await logger(Exception)
        await event.react("❌")


bot.add_handler(fun, "fun")
bot.add_handler(cat, "cat")
bot.add_handler(coub, "coub")
bot.add_handler(dog, "dog")
bot.add_handler(gif, "gif")
bot.add_handler(sticker, "gsticker")
bot.add_handler(smirnoff, "smirnoff")
# bot.add_handler(meme, "meme")

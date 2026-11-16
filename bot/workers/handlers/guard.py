import asyncio
import datetime
import io
import re
from inspect import getdoc
from zoneinfo import ZoneInfo, available_timezones

from bot import bot, jid
from bot.others.exceptions import StopAutoHandlers
from bot.utils.db_utils import save2db2
from bot.utils.events import SEQUENTIAL_AUTO, Event
from bot.utils.log_utils import logger
from bot.utils.msg_utils import get_args
from bot.workers.auto.schedule import scheduler2
from bot.workers.handlers.members import admin_check, find_member, other_ids, remove

CAPTCHA_MINUTES = 5
CAPTCHA_TRIES = 3
# chat id -> {user id: pending captcha}, for members who joined and haven't
# solved theirs; kept in memory, so a restart lets them through
captchas: dict[str, dict[str, dict]] = {}


def settings(chat_id: str) -> dict:
    return bot.group_dict.setdefault(chat_id, {})


def parse_time(text: str) -> str | None:
    """A time like 22:00, 7:30, 10pm or 7am as HH:MM, or None"""
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)?", text.strip().lower())
    if not match:
        return None
    hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if half:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if half == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def local_zone() -> str:
    return datetime.datetime.now().astimezone().tzname() or "local time"


def parse_zone(text: str) -> str | None:
    """
    A time zone as stored: an IANA name (Africa/Lagos, in any case) or a UTC
    offset (UTC+1, GMT-5:30, +01:00) as UTC+HH:MM; None if it isn't one
    """
    match = re.fullmatch(
        r"(?:utc|gmt)?\s*([+-])(\d{1,2})(?::?(\d{2}))?", text.strip().lower()
    )
    if match:
        hours, minutes = int(match.group(2)), int(match.group(3) or 0)
        if hours > 14 or minutes > 59:
            return None
        return f"UTC{match.group(1)}{hours:02d}:{minutes:02d}"
    if text.strip().lower() in ("utc", "gmt", "z"):
        return "UTC+00:00"
    # IANA names are case-sensitive; match them case-insensitively
    names = {name.lower(): name for name in available_timezones()}
    if name := names.get(text.strip().lower()):
        return name
    return None


def now_in(zone: str | None) -> datetime.datetime:
    """The current time in a stored zone, or the bot's own"""
    if not zone:
        return datetime.datetime.now()
    if match := re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", zone):
        offset = datetime.timedelta(
            hours=int(match.group(2)), minutes=int(match.group(3))
        )
        if match.group(1) == "-":
            offset = -offset
        return datetime.datetime.now(datetime.timezone(offset))
    return datetime.datetime.now(ZoneInfo(zone))


async def autolock(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Make this group admins-only every day between two
    times, e.g. at night, and open it again after. Group only; the bot must be
    a group admin. With no arguments, shows the schedule.

    Usage: autolock <lock time> <unlock time> [time zone]
               e.g. autolock 22:00 7:00, autolock 10pm 7am Africa/Lagos
           autolock off
        time zone: a name like Africa/Lagos or Europe/London, or an offset
            like UTC+1 or GMT-5:30 (default: the bot's time zone)
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    group = settings(event.chat.id)
    words = (args or "").split()
    if words == ["off"]:
        group.pop("autolock", None)
        await save2db2(bot.group_dict, "groups")
        return await event.reply("*Scheduled lock is off.*")
    if words:
        zone = None
        if len(words) == 3:
            if not (zone := parse_zone(words[2])):
                return await event.reply(
                    f"*{words[2]} isn't a time zone I know.* Use a name like "
                    "Africa/Lagos or an offset like UTC+1."
                )
            words = words[:2]
        times = [parse_time(w) for w in words]
        if len(times) != 2 or None in times or times[0] == times[1]:
            return await event.reply(getdoc(autolock))
        group["autolock"] = {"lock": times[0], "unlock": times[1], "zone": zone}
        await save2db2(bot.group_dict, "groups")
    if not (schedule := group.get("autolock")):
        return await event.reply(f"*No scheduled lock.*\n\n{getdoc(autolock)}")
    await event.reply(
        f"*This group goes admins-only at {schedule['lock']} and opens at "
        f"{schedule['unlock']} ({schedule.get('zone') or local_zone()}).*"
    )


async def run_autolocks():
    """Locks and unlocks groups whose scheduled time is this minute"""
    for chat_id, group in list(bot.group_dict.items()):
        if not isinstance(group, dict) or not (schedule := group.get("autolock")):
            continue
        try:
            now = now_in(schedule.get("zone")).strftime("%H:%M")
        except Exception:
            await logger(Exception)
            continue
        if now not in (schedule["lock"], schedule["unlock"]):
            continue
        lock = now == schedule["lock"]
        try:
            chat = jid.build_jid(chat_id, "g.us")
            await bot.client.set_group_announce(chat, lock)
            await bot.client.send_message(
                chat,
                (
                    "🌙 *The group is now admins-only until " f"{schedule['unlock']}.*"
                    if lock
                    else "☀️ *The group is open again.*"
                ),
            )
        except Exception:
            await logger(Exception)


async def captcha(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Make new members solve a captcha. Someone who
    joins through a link or a join request gets a picture of a code; their
    other messages are deleted until they send the code, and they're removed
    after 3 wrong answers or when time runs out. People added by a member,
    or whose join request an admin approved, don't get one. Group only; the bot must be a group admin. With no
    arguments, shows the setting.

    Usage: captcha on | off [-t minutes]
        -t minutes : time to solve it (default 5)
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    flags, rest = get_args("-t", to_parse=args or "", get_unknown=True)
    group = settings(event.chat.id)
    config = group.setdefault("captcha", {"on": False, "minutes": CAPTCHA_MINUTES})
    word = rest.strip().lower()
    if word in ("on", "off"):
        config["on"] = word == "on"
    elif word:
        return await event.reply(getdoc(captcha))
    if flags.t is not None:
        if not flags.t.isdigit() or not 1 <= int(flags.t) <= 60:
            return await event.reply("*-t takes 1 to 60 minutes.*")
        config["minutes"] = int(flags.t)
    await save2db2(bot.group_dict, "groups")
    state = "on" if config["on"] else "off"
    await event.reply(
        f"*Join captcha is {state}* ({config['minutes']} minutes to solve it)."
    )


def make_captcha() -> tuple[bytes, str]:
    """(PNG of a code, the code)"""
    from multicolorcaptcha import CaptchaGenerator

    result = CaptchaGenerator(2).gen_captcha_image(difficult_level=2)
    # Older versions return a dict, newer ones an object
    image = result["image"] if isinstance(result, dict) else result.image
    code = result["characters"] if isinstance(result, dict) else result.characters
    out = io.BytesIO()
    image.save(out, format="png")
    return out.getvalue(), str(code)


async def start_captchas(gc_msg, skip: set):
    """
    Sends a captcha to members who just joined by themselves, where the
    captcha is on; skip holds ids already dealt with (banned)
    """
    chat_id = gc_msg.JID.User
    config = bot.group_dict.get(chat_id, {}).get("captcha") or {}
    if not config.get("on"):
        return
    # Added by someone else, or approved by an admin: they vouched for them
    if gc_msg.Sender.User and all(gc_msg.Sender.User != j.User for j in gc_msg.Join):
        return
    minutes = config.get("minutes", CAPTCHA_MINUTES)
    for joined in gc_msg.Join:
        if joined.User in skip:
            continue
        try:
            png, code = await asyncio.to_thread(make_captcha)
            sent = await bot.client.send_image(
                gc_msg.JID,
                png,
                f"Welcome @{joined.User}! Send the code in this picture within "
                f"{minutes} minutes to stay in the group.",
                mentions_are_lids=joined.Server == "lid",
            )
        except Exception:
            await logger(Exception)
            continue
        pending = {
            "code": code,
            "tries": CAPTCHA_TRIES,
            "jid": joined,
            "message": getattr(sent, "ID", None),
        }
        for user in await other_ids(joined.User):
            captchas.setdefault(chat_id, {})[user] = pending
        asyncio.create_task(expire_captcha(gc_msg.JID, joined.User, pending, minutes))


async def finish_captcha(chat_jid, pending: dict, kick: bool, note: str):
    """Ends a pending captcha: deletes it, removes the member if kick"""
    group = captchas.get(chat_jid.User, {})
    for user in [u for u, p in group.items() if p is pending]:
        group.pop(user, None)
    if pending.get("message"):
        try:
            await bot.client.revoke_message(
                chat_jid, bot.client.me.JID, pending["message"]
            )
        except Exception:
            pass
    joined = pending["jid"]
    if kick:
        try:
            info = await bot.client.get_group_info(chat_jid)
            if member := find_member(joined.User, info.Participants):
                await remove(bot.client, chat_jid, [member])
        except Exception:
            await logger(Exception)
    await bot.client.send_message(
        chat_jid, f"@{joined.User} {note}", mentions_are_lids=joined.Server == "lid"
    )


async def expire_captcha(chat_jid, user: str, pending: dict, minutes: int):
    await asyncio.sleep(minutes * 60)
    if captchas.get(chat_jid.User, {}).get(user) is pending:
        await finish_captcha(
            chat_jid,
            pending,
            True,
            "*didn't solve the captcha in time and was removed.*",
        )


async def captcha_gate(event: Event, _, client):
    """
    Holds back members with an unsolved captcha: their messages are deleted,
    and the right code lets them in.
    AUTO FUNCTION
    """
    if not event.chat.is_group:
        return
    pending = captchas.get(event.chat.id, {}).get(event.from_user.id)
    if not pending:
        return
    answer = re.sub(r"\s+", "", (event.text or event.caption or "")).lower()
    try:
        await event.delete()
    except Exception:
        pass
    if answer == pending["code"].lower():
        await finish_captcha(
            event.chat.jid, pending, False, "*solved the captcha. Welcome!*"
        )
    else:
        pending["tries"] -= 1
        if pending["tries"] <= 0:
            await finish_captcha(
                event.chat.jid,
                pending,
                True,
                "*got the captcha wrong too many times and was removed.*",
            )
    raise StopAutoHandlers("member has an unsolved captcha")


# Imported before moderation, so the captcha gate runs before its checks
bot.add_handler(captcha_gate, SEQUENTIAL_AUTO)
scheduler2.add_job(
    id="autolock",
    func=run_autolocks,
    trigger="cron",
    minute="*",
    misfire_grace_time=None,
    max_instances=1,
)

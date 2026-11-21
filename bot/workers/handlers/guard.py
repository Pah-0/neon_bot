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
from bot.utils.group_utils import admin_check, find_member, other_ids, remove, start_captchas, captchas, CAPTCHA_TRIES, finish_captcha, CAPTCHA_MINUTES


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


from bot.utils.group_utils import start_captchas, captchas, CAPTCHA_TRIES, finish_captcha

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

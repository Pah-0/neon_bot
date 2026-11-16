import asyncio
import datetime
import io
import re
from inspect import getdoc

from bot import bot, jid
from bot.others.exceptions import StopAutoHandlers
from bot.utils.db_utils import save2db2
from bot.utils.events import SEQUENTIAL_AUTO, Event
from bot.utils.log_utils import logger
from bot.utils.msg_utils import get_args, user_is_admin, user_is_privileged
from bot.workers.auto.schedule import scheduler2
from bot.workers.handlers.members import (
    admin_check,
    find_member,
    other_ids,
    protected,
    remove,
    tag,
    targets,
)

DEFAULT_WARN_LIMIT = 3
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


async def autolock(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Make this group admins-only every day between two
    times, e.g. at night, and open it again after. Group only; the bot must be
    a group admin. Times are in the bot's time zone. With no arguments, shows
    the schedule.

    Usage: autolock <lock time> <unlock time>, e.g. autolock 22:00 7:00
               (or 10pm 7am)
           autolock off
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
        times = [parse_time(w) for w in words]
        if len(times) != 2 or None in times or times[0] == times[1]:
            return await event.reply(getdoc(autolock))
        group["autolock"] = {"lock": times[0], "unlock": times[1]}
        await save2db2(bot.group_dict, "groups")
    if not (schedule := group.get("autolock")):
        return await event.reply(f"*No scheduled lock.*\n\n{getdoc(autolock)}")
    await event.reply(
        f"*This group goes admins-only at {schedule['lock']} and opens at "
        f"{schedule['unlock']} ({local_zone()}).*"
    )


async def run_autolocks():
    """Locks and unlocks groups whose scheduled time is this minute"""
    now = datetime.datetime.now().strftime("%H:%M")
    for chat_id, group in list(bot.group_dict.items()):
        if not isinstance(group, dict) or not (schedule := group.get("autolock")):
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


def filter_matches(text: str, trigger: str, rule: dict) -> bool:
    text = text.casefold()
    if rule.get("all"):
        words = re.findall(r"\w+", text)
        return all(w in words for w in trigger.split())
    if rule.get("word"):
        return re.search(rf"(?<!\w){re.escape(trigger)}(?!\w)", text) is not None
    return trigger in text


async def pfilter(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Add a punishing filter: a message from a member
    that contains the text is deleted and they're warned, and they're removed
    when they reach the warn limit (warnlimit). Group only; the bot must be a
    group admin. Owner, sudoers and group admins aren't affected.

    Usage: pfilter <text> [-r reason] [-a] [-m]
        Or reply to a message: its text is the reason shown in the warning.
        -r reason : the reason shown in the warning (quote it if it has
            spaces: -r "No slurs here")
        -a : match if all the words appear anywhere, in any order
        -m : match the text only as whole word(s)
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    flags, trigger = get_args(
        "-r",
        ["-a", "store_true"],
        ["-m", "store_true"],
        to_parse=args or "",
        get_unknown=True,
    )
    trigger = trigger.strip().casefold()
    replied = event.reply_to_message
    reason = flags.r or (replied and (replied.text or replied.caption)) or ""
    if not trigger or (len(trigger) < 3 and not (flags.a or flags.m)):
        return await event.reply(getdoc(pfilter))
    filters = settings(event.chat.id).setdefault("pfilters", {})
    filters[trigger] = {"reason": reason.strip(), "all": flags.a, "word": flags.m}
    await save2db2(bot.group_dict, "groups")
    note = f"*Messages with* “{trigger}” *will be deleted and their sender warned.*"
    if reason:
        note += f"\n*Reason:* {reason.strip()}"
    await event.reply(note)


async def pfilters(event: Event, args, client):
    """
    [Owner | Sudo | Admin] List this group's punishing filters and the warn
    limit. Group only.
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    group = settings(event.chat.id)
    filters = group.get("pfilters") or {}
    limit = group.get("warn_limit", DEFAULT_WARN_LIMIT)
    kick = f"removed at {limit} warnings" if limit else "never removed"
    if not filters:
        return await event.reply(f"*No punishing filters.* Members are {kick}.")
    lines = []
    for trigger, rule in sorted(filters.items()):
        mode = (
            " (all words)"
            if rule.get("all")
            else " (whole word)" if rule.get("word") else ""
        )
        reason = f" — {rule['reason']}" if rule.get("reason") else ""
        lines.append(f"• {trigger}{mode}{reason}")
    await event.reply(f"*Punishing filters* (members are {kick}):\n" + "\n".join(lines))


async def del_pfilter(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Remove a punishing filter, or all of them. Group
    only.

    Usage: del_pfilter <text> | all
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    trigger = (args or "").strip().casefold()
    filters = settings(event.chat.id).get("pfilters") or {}
    if not trigger:
        return await event.reply(getdoc(del_pfilter))
    if trigger == "all":
        filters.clear()
    elif filters.pop(trigger, None) is None:
        return await event.reply(f"*No punishing filter for* “{trigger}”.")
    await save2db2(bot.group_dict, "groups")
    await event.reply("*Removed.*")


def warn_key(member) -> str:
    """The id warnings are counted under: the LID if known, else the JID"""
    return member.LID.User or member.JID.User


async def give_warning(
    chat_jid, chat_id: str, member, participants, reason: str, lids: bool
):
    """Warns a member, and removes them at the warn limit"""
    group = settings(chat_id)
    warns = group.setdefault("warns", {})
    key = warn_key(member)
    warns[key] = warns.get(key, 0) + 1
    limit = group.get("warn_limit", DEFAULT_WARN_LIMIT)
    mention = tag(member)
    why = f"\n*Reason:* {reason}" if reason else ""
    if limit and warns[key] >= limit:
        warns.pop(key, None)
        failed = await remove(bot.client, chat_jid, [member])
        text = (
            f"{mention} *reached {limit} warnings and couldn't be removed.*{why}"
            if failed
            else f"{mention} *reached {limit} warnings and was removed.*{why}"
        )
    else:
        count = f"{warns[key]}/{limit}" if limit else str(warns[key])
        text = f"⚠️ {mention} *has been warned ({count}).*{why}"
    await save2db2(bot.group_dict, "groups")
    await bot.client.send_message(chat_jid, text, mentions_are_lids=lids)


async def warn(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Warn members; at the warn limit (warnlimit) they're
    removed. Group only; the bot must be a group admin.

    Usage: warn user_id/@mention ... [reason] [-d]
        Or reply to a member's message: with no reason given, the message
        itself is the reason.
        -d : delete the replied message too
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    flags, rest = get_args(["-d", "store_true"], to_parse=args or "", get_unknown=True)
    ids = targets(event, rest)
    if not ids:
        return await event.reply(getdoc(warn))
    # What isn't an id or mention is the reason
    reason = " ".join(
        w for w in rest.split() if not w.lstrip("@").lstrip("+").isdigit()
    ).strip()
    replied = event.reply_to_message
    if not reason and replied and (quoted := replied.text or replied.caption):
        reason = f"“{quoted[:200]}”"
    notes = []
    for user in ids:
        member = find_member(user, group_info.Participants)
        if member is None:
            notes.append(f"*{user} isn't in this group.*")
            continue
        if protected(member, group_info.Participants):
            notes.append("*Owner, sudoers, group admins and the bot can't be warned.*")
            continue
        await give_warning(
            event.chat.jid,
            event.chat.id,
            member,
            group_info.Participants,
            reason,
            event.lid_address,
        )
    if flags.d and replied:
        try:
            await replied.delete()
        except Exception:
            pass
    if notes:
        await event.reply("\n".join(dict.fromkeys(notes)))


async def warns(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Show warnings: a member's, or everyone's with any.
    Group only.

    Usage: warns [user_id/@mention] (or reply to a member's message)
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    group = settings(event.chat.id)
    counts = group.get("warns") or {}
    limit = group.get("warn_limit", DEFAULT_WARN_LIMIT)
    of = f"/{limit}" if limit else ""
    if ids := targets(event, args or ""):
        lines = []
        for user in ids:
            member = find_member(user, group_info.Participants)
            count = counts.get(warn_key(member), 0) if member else counts.get(user, 0)
            lines.append(f"{tag(member) if member else user}: {count}{of}")
        return await event.reply("\n".join(lines), mentions_are_lids=event.lid_address)
    if not counts:
        return await event.reply("*No one has warnings.*")
    lines = [f"@{u}: {c}{of}" for u, c in sorted(counts.items(), key=lambda x: -x[1])]
    await event.reply("*Warnings:*\n" + "\n".join(lines), mentions_are_lids=True)


async def resetwarns(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Clear members' warnings, or everyone's. Group only.

    Usage: resetwarns user_id/@mention ... | all (or reply to a member's message)
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    counts = settings(event.chat.id).setdefault("warns", {})
    if (args or "").strip() == "all":
        counts.clear()
    elif ids := targets(event, args or ""):
        for user in ids:
            member = find_member(user, group_info.Participants)
            counts.pop(warn_key(member) if member else user, None)
    else:
        return await event.reply(getdoc(resetwarns))
    await save2db2(bot.group_dict, "groups")
    await event.reply("*Warnings cleared.*")


async def warnlimit(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Set how many warnings remove a member (default 3);
    0 never removes them. Group only. With no arguments, shows it.

    Usage: warnlimit [number]
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    group = settings(event.chat.id)
    value = (args or "").strip()
    if value:
        if not value.isdigit() or int(value) > 100:
            return await event.reply(getdoc(warnlimit))
        group["warn_limit"] = int(value)
        await save2db2(bot.group_dict, "groups")
    limit = group.get("warn_limit", DEFAULT_WARN_LIMIT)
    await event.reply(
        f"*Members are removed at {limit} warnings.*"
        if limit
        else "*Warnings never remove members.*"
    )


async def punish_filtered(event: Event, _, client):
    """
    Deletes messages that match a punishing filter and warns their sender.
    AUTO FUNCTION
    """
    if not event.chat.is_group:
        return
    group = bot.group_dict.get(event.chat.id, {})
    if not (filters := group.get("pfilters")):
        return
    if not (text := (event.text or event.caption)):
        return
    hit = next((t for t, r in filters.items() if filter_matches(text, t, r)), None)
    if hit is None or user_is_privileged(event.from_user.id):
        return
    try:
        group_info = await client.get_group_info(event.chat.jid)
    except Exception:
        await logger(Exception)
        return
    participants = group_info.Participants
    member = find_member(event.from_user.id, participants)
    if (
        member is None
        or protected(member, participants)
        or not user_is_admin(bot.client.me.JID.User, participants)
    ):
        return
    try:
        await event.delete()
    except Exception:
        await logger(Exception)
    await give_warning(
        event.chat.jid,
        event.chat.id,
        member,
        participants,
        filters[hit].get("reason", ""),
        event.lid_address,
    )
    raise StopAutoHandlers("message matched a punishing filter")


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


# The captcha gate runs before the punishing filters
bot.add_handler(captcha_gate, SEQUENTIAL_AUTO)
bot.add_handler(punish_filtered, SEQUENTIAL_AUTO)
scheduler2.add_job(
    id="autolock",
    func=run_autolocks,
    trigger="cron",
    minute="*",
    misfire_grace_time=None,
    max_instances=1,
)

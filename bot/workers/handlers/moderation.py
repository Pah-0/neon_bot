import re
from inspect import getdoc
from urllib.parse import urlsplit

from bot import bot
from bot.others.exceptions import StopAutoHandlers
from bot.utils.db_utils import save2db2
from bot.utils.events import SEQUENTIAL_AUTO, Event
from bot.utils.log_utils import logger
from bot.utils.msg_utils import get_args, user_is_admin, user_is_privileged
from bot.workers.handlers.members import (
    admin_check,
    find_member,
    protected,
    remove,
    tag,
    targets,
)

DEFAULT_WARN_LIMIT = 3
# Links with a scheme or www., or bare domains on common TLDs (chat.whatsapp.com…);
# not email addresses
LINK_RE = re.compile(
    r"(?i)\b(?:https?://|www\.)[^\s<>]+"
    r"|(?<![@\w.-])(?:[a-z0-9-]+\.)+(?:com|net|org|io|gg|me|ly|co|xyz|app|dev|link|tv|info"
    r"|ru|in|us|uk|be|to|site|online|store|shop|club|live|top|cc|ws)\b(?:/[^\s<>]*)?"
)


def settings(chat_id: str) -> dict:
    return bot.group_dict.setdefault(chat_id, {})


def link_hosts(text: str) -> list[str]:
    """The host of each link in text, lowercased and without www."""
    hosts = []
    for match in LINK_RE.finditer(text):
        link = match.group(0)
        if "://" not in link:
            link = "http://" + link
        host = (urlsplit(link).hostname or "").removeprefix("www.")
        if host:
            hosts.append(host)
    return hosts


def is_allowed(host: str, allowed: set) -> bool:
    """Whether host is an allowed domain or a subdomain of one"""
    return any(host == a or host.endswith("." + a) for a in allowed)


async def antilink(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Auto-delete messages with links in this group.
    Group only; the bot must be a group admin. Owner, sudoers and group
    admins can still post links. With no arguments, shows the setting.

    Usage: antilink on | off
           antilink -allow domain ... : let links to these domains (and their
               subdomains) through, e.g. -allow youtube.com x.com
           antilink -rm domain ... : stop allowing them
    """
    group_info, _ = await admin_check(event, client)
    if not group_info:
        return
    flags, rest = get_args(
        ["-allow", "store_true"],
        ["-rm", "store_true"],
        to_parse=args or "",
        get_unknown=True,
    )
    settings = bot.group_dict.setdefault(event.chat.id, {})
    allowed: set = settings.setdefault("link_allow", set())
    words = rest.split()
    if flags.allow or flags.rm:
        domains = {
            d.lower().removeprefix("www.").strip("/")
            for w in words
            for d in [urlsplit(w if "://" in w else "http://" + w).hostname or ""]
            if d
        }
        if not domains:
            return await event.reply(getdoc(antilink))
        if flags.allow:
            allowed |= domains
        else:
            allowed -= domains
        await save2db2(bot.group_dict, "groups")
    elif words and words[0].lower() in ("on", "off"):
        settings["antilink"] = words[0].lower() == "on"
        await save2db2(bot.group_dict, "groups")
    elif words:
        return await event.reply(getdoc(antilink))
    state = "on" if settings.get("antilink") else "off"
    note = f"*Link auto-delete is {state}.*"
    if allowed:
        note += f"\n*Allowed:* {', '.join(sorted(allowed))}"
    await event.reply(note)


async def delete_links(event: Event, _, client):
    """
    Deletes messages with links from members who aren't owner, sudoers or
    group admins, where antilink is on and the bot is a group admin.
    AUTO FUNCTION
    """
    if not event.chat.is_group:
        return
    settings = bot.group_dict.get(event.chat.id, {})
    if not settings.get("antilink"):
        return
    if not (text := (event.text or event.caption)):
        return
    allowed = settings.get("link_allow") or set()
    if not [h for h in link_hosts(text) if not is_allowed(h, allowed)]:
        return
    user = event.from_user.id
    if user_is_privileged(user):
        return
    try:
        group_info = await client.get_group_info(event.chat.jid)
    except Exception:
        # Never delete when we can't check for admins
        await logger(Exception)
        return
    participants = group_info.Participants
    if user_is_admin(user, participants) or not user_is_admin(
        bot.client.me.JID.User, participants
    ):
        return
    try:
        await event.delete()
    except Exception:
        await logger(Exception)
        return
    raise StopAutoHandlers("message with a link was deleted")


def filter_matches(text: str, trigger: str, rule: dict) -> bool:
    text = text.casefold()
    if rule.get("all"):
        words = re.findall(r"\w+", text)
        return all(w in words for w in trigger.split())
    if rule.get("word"):
        return re.search(rf"(?<!\w){
            re.escape(trigger)}(?!\w)", text) is not None
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


bot.add_handler(delete_links, SEQUENTIAL_AUTO)
bot.add_handler(punish_filtered, SEQUENTIAL_AUTO)

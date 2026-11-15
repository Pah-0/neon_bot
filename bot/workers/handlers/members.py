from inspect import getdoc

from neonize.utils.enum import ParticipantChange

from bot import bot, jid
from bot.utils.db_utils import save2db2
from bot.utils.events import Event
from bot.utils.log_utils import logger
from bot.utils.msg_utils import get_mentioned, user_is_admin, user_is_privileged

# Error codes WhatsApp gives per participant when adding one fails
ADD_ERRORS = {
    403: "only lets contacts add them to groups",
    408: "left recently and can't be added back yet",
    409: "is already in the group",
    500: "can't be added; the group is full",
}


def find_member(user: str, participants):
    """The participant whose JID, LID or phone number is user, or None"""
    for member in participants:
        if user and user in (
            member.JID.User,
            member.LID.User,
            member.PhoneNumber.User,
        ):
            return member
    return None


def member_ids(member) -> set:
    """Every id a participant goes by (JID, LID, phone number)"""
    return {u for u in (member.JID.User, member.LID.User, member.PhoneNumber.User) if u}


def targets(event: Event, args: str) -> list[str]:
    """
    The users a command is aimed at: ids or @mentions in args (a leading +
    is dropped), or the replied message's sender
    """
    ids = [a.lstrip("@").lstrip("+") for a in args.split()]
    ids = [i for i in ids if i.isdigit()] + get_mentioned(args)
    if not ids and event.reply_to_message:
        ids = [event.reply_to_message.from_user.id]
    return list(dict.fromkeys(ids))


def tag(member) -> str:
    """A participant's mention, by the id the group knows them by"""
    return f"@{member.JID.User}"


async def other_ids(user: str) -> set:
    """user with the LID of their number, or the number of their LID"""
    ids = {user}
    for lookup, server in (
        (bot.client.get_lid_from_pn, "s.whatsapp.net"),
        (bot.client.get_pn_from_lid, "lid"),
    ):
        try:
            if found := (await lookup(jid.build_jid(user, server))).User:
                ids.add(found)
        except Exception:
            pass
    return ids


async def admin_check(event: Event, client):
    """
    The group info if the sender may manage members and the bot is a group
    admin; otherwise replies or reacts and returns None
    """
    if not event.chat.is_group:
        await event.react("🚫")
        return None
    group_info = await client.get_group_info(event.chat.jid)
    user = event.from_user.id
    if not user_is_privileged(user) and not user_is_admin(
        user, group_info.Participants
    ):
        await event.react("🚫")
        return None
    if not user_is_admin(bot.client.me.JID.User, group_info.Participants):
        await event.reply("*I need to be a group admin for that to work.*")
        return None
    return group_info


def protected(member, participants) -> bool:
    """Owner, sudoers, group admins and the bot can't be kicked or banned"""
    ids = member_ids(member)
    me = bot.client.me
    return (
        any(user_is_privileged(i) for i in ids)
        or user_is_admin(member.JID.User, participants)
        or bool(ids & {me.JID.User, getattr(me, "LID", me.JID).User})
    )


async def remove(event: Event, client, members: list) -> list:
    """Removes members from the group; those it couldn't remove"""
    result = await client.update_group_participants(
        event.chat.jid, [m.JID for m in members], ParticipantChange.REMOVE
    )
    # The answer may name a member by their other id (number or LID)
    failed = set().union(*(member_ids(p) for p in result if p.Error))
    return [m for m in members if member_ids(m) & failed]


async def kick(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Remove members from this group. Group only; the
    bot must be a group admin. They can rejoin; use gban to keep them out.

    Usage: kick user_id/@mention ... (or reply to a member's message)

    user_id: phone number with country code (leading + optional) or lid
    """
    if not (group_info := await admin_check(event, client)):
        return
    ids = targets(event, args or "")
    if not ids:
        return await event.reply(getdoc(kick))
    try:
        await kick_or_ban(event, client, group_info, ids, ban=False)
    except Exception as e:
        await logger(Exception)
        await event.reply(f"*Error:*\n{e}")


async def kick_or_ban(event: Event, client, group_info, ids: list, ban: bool):
    participants = group_info.Participants
    to_remove, notes = [], []
    banned: set = (
        bot.group_dict.setdefault(event.chat.id, {}).setdefault("gbanned", set())
        if ban
        else set()
    )
    for user in ids:
        member = find_member(user, participants)
        if member is None:
            if not ban:
                notes.append(f"*{user} isn't in this group.*")
                continue
            # Banned before they join: keep both their number and LID
            banned.update(await other_ids(user))
            notes.append(f"*{user} isn't in the group, but is banned from it.*")
            continue
        if protected(member, participants):
            notes.append("*Owner, sudoers, group admins and the bot can't be removed.*")
            continue
        if ban:
            banned.update(member_ids(member))
        to_remove.append(member)
    failed = await remove(event, client, to_remove) if to_remove else []
    for member in to_remove:
        mention = tag(member)
        if member in failed:
            notes.append(f"*Couldn't remove* {mention}.")
        elif ban:
            notes.append(f"{mention} *is banned from this group.*")
        else:
            notes.append(f"{mention} *was removed.*")
    if ban:
        await save2db2(bot.group_dict, "groups")
    if notes:
        await event.reply(
            "\n".join(dict.fromkeys(notes)), mentions_are_lids=event.lid_address
        )


async def gban(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Ban members from this group: they're removed, and
    removed again whenever they rejoin, until gunban. Group only; the bot must
    be a group admin. Someone who isn't in the group yet can be banned by
    number. With no arguments, lists the banned.

    Usage: gban user_id/@mention ... (or reply to a member's message)

    user_id: phone number with country code (leading + optional) or lid
    """
    if not (group_info := await admin_check(event, client)):
        return
    ids = targets(event, args or "")
    if not ids:
        banned = bot.group_dict.get(event.chat.id, {}).get("gbanned")
        if not banned:
            return await event.reply(
                f"*No one is banned from this group.*\n\n{getdoc(gban)}"
            )
        listed = "\n".join(f"• {u}" for u in sorted(banned))
        return await event.reply(f"*Banned from this group (ids):*\n{listed}")
    try:
        await kick_or_ban(event, client, group_info, ids, ban=True)
    except Exception as e:
        await logger(Exception)
        await event.reply(f"*Error:*\n{e}")


async def gunban(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Lift a group ban, so the user can rejoin. Group
    only. It doesn't add them back; use add for that.

    Usage: gunban user_id ... (as listed by gban)
    """
    if not event.chat.is_group:
        return await event.react("🚫")
    group_info = await client.get_group_info(event.chat.jid)
    user = event.from_user.id
    if not user_is_privileged(user) and not user_is_admin(
        user, group_info.Participants
    ):
        return await event.react("🚫")
    ids = targets(event, args or "")
    if not ids:
        return await event.reply(getdoc(gunban))
    banned: set = bot.group_dict.get(event.chat.id, {}).get("gbanned", set())
    notes = []
    for user in ids:
        if user not in banned:
            notes.append(f"*{user} isn't banned from this group.*")
            continue
        # Their other id (number or LID) goes too
        banned.difference_update(await other_ids(user))
        notes.append(f"*{user} can join this group again.*")
    await save2db2(bot.group_dict, "groups")
    await event.reply("\n".join(notes))


async def add(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Add people to this group by phone number. Group
    only; the bot must be a group admin. If someone's privacy settings stop
    the bot from adding them, they're sent the group's invite link instead.
    Banned users (gban) must be unbanned first.

    Usage: add phone_number ... (with country code, leading + optional)
    """
    if not (group_info := await admin_check(event, client)):
        return
    numbers = [a.lstrip("+") for a in (args or "").split()]
    numbers = [n for n in numbers if n.isdigit()]
    if not numbers:
        return await event.reply(getdoc(add))
    banned = bot.group_dict.get(event.chat.id, {}).get("gbanned", set())
    notes = []
    if blocked := [n for n in numbers if n in banned]:
        notes += [
            f"*{n} is banned from this group; gunban them first.*" for n in blocked
        ]
        numbers = [n for n in numbers if n not in blocked]
    try:
        if numbers:
            result = await client.update_group_participants(
                event.chat.jid,
                [jid.build_jid(n, "s.whatsapp.net") for n in numbers],
                ParticipantChange.ADD,
            )
            # Results aren't promised in the order sent; match them by number
            by_number = {}
            for participant in result:
                for user in member_ids(participant):
                    by_number[user] = participant
            link = None
            for number in numbers:
                if not (participant := by_number.get(number)):
                    notes.append(f"*No answer from WhatsApp for {number}.*")
                    continue
                if not participant.Error:
                    notes.append(f"*Added {number}.*")
                    continue
                why = ADD_ERRORS.get(participant.Error, f"couldn't be added (error {
                        participant.Error})")
                if participant.Error == 403:
                    link = link or await client.get_group_invite_link(event.chat.jid)
                    await client.send_message(
                        jid.build_jid(number, "s.whatsapp.net"),
                        f"You're invited to join *{group_info.GroupName.Name}*:\n{link}",
                    )
                    why += "; they were sent the invite link"
                notes.append(f"*{number} {why}.*")
        await event.reply("\n".join(notes))
    except Exception as e:
        await logger(Exception)
        await event.reply(f"*Error:*\n{e}")


async def keep_out_banned(gc_msg) -> set:
    """
    Removes group-banned users who just joined; the ids of those removed, so
    they aren't welcomed
    """
    banned = bot.group_dict.get(gc_msg.JID.User, {}).get("gbanned")
    if not banned:
        return set()
    joined = [j for j in gc_msg.Join if (await other_ids(j.User)) & banned]
    if not joined:
        return set()
    try:
        await bot.client.update_group_participants(
            gc_msg.JID, joined, ParticipantChange.REMOVE
        )
    except Exception:
        await logger(Exception)
        return set()
    return {j.User for j in joined}

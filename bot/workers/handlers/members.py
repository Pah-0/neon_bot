from inspect import getdoc

import aiohttp
from neonize.proto.Neonize_pb2 import GetProfilePictureParams
from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import GroupInviteMessage, Message
from neonize.utils.enum import ParticipantChange, ParticipantRequestChange

from bot import bot, jid
from bot.utils.db_utils import save2db2
from bot.utils.events import Event
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    get_args,
    get_mentioned,
    user_is_admin,
    user_is_privileged,
)

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


def parse(args: str):
    """(community flag, the rest of args)"""
    flags, rest = get_args(["-c", "store_true"], to_parse=args or "", get_unknown=True)
    return flags.c, rest


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


def community_of(group_info):
    """The JID of the community a group belongs to, or None"""
    parent = group_info.GroupLinkedParent.LinkedParentJID
    return parent if parent.User else None


def bans(chat_id: str, community: bool = False) -> set:
    """The ban list of a group, or of a community (by its JID's user)"""
    key = "community_gbanned" if community else "gbanned"
    return bot.group_dict.setdefault(chat_id, {}).setdefault(key, set())


async def banned_for(group_jid) -> set:
    """Everyone banned from a group: its own bans and its community's"""
    banned = set(bot.group_dict.get(group_jid.User, {}).get("gbanned") or ())
    # Only look up the community when some community has bans
    if any(
        g.get("community_gbanned")
        for g in bot.group_dict.values()
        if isinstance(g, dict)
    ):
        try:
            info = await bot.client.get_group_info(group_jid)
            if community := community_of(info):
                banned |= (
                    bot.group_dict.get(community.User, {}).get("community_gbanned")
                    or set()
                )
        except Exception:
            await logger(Exception)
    return banned


async def admin_check(event: Event, client, community: bool = False):
    """
    (group info, community JID or None) if the sender may manage members and
    the bot is a group admin; with community, the group must be in one and
    the sender must be a community admin. Otherwise replies or reacts and
    returns (None, None).
    """
    if not event.chat.is_group:
        await event.react("🚫")
        return None, None
    group_info = await client.get_group_info(event.chat.jid)
    user = event.from_user.id
    parent = community_of(group_info)
    if community and not parent:
        await event.reply("*This group isn't part of a community.*")
        return None, None
    allowed = user_is_privileged(user)
    if not allowed:
        if community:
            try:
                parent_info = await client.get_group_info(parent)
                allowed = user_is_admin(user, parent_info.Participants)
            except Exception:
                allowed = False
        else:
            allowed = user_is_admin(user, group_info.Participants)
    if not allowed:
        await event.react("🚫")
        return None, None
    if not community and not user_is_admin(
        bot.client.me.JID.User, group_info.Participants
    ):
        await event.reply("*I need to be a group admin for that to work.*")
        return None, None
    return group_info, parent


def protected(member, participants) -> bool:
    """Owner, sudoers, group admins and the bot can't be kicked or banned"""
    ids = member_ids(member)
    me = bot.client.me
    return (
        any(user_is_privileged(i) for i in ids)
        or user_is_admin(member.JID.User, participants)
        or bool(ids & {me.JID.User, getattr(me, "LID", me.JID).User})
    )


async def remove(client, group_jid, members: list) -> list:
    """Removes members from a group; those it couldn't remove"""
    result = await client.update_group_participants(
        group_jid, [m.JID for m in members], ParticipantChange.REMOVE
    )
    # The answer may name a member by their other id (number or LID)
    failed = set().union(*(member_ids(p) for p in result if p.Error))
    return [m for m in members if member_ids(m) & failed]


async def kick(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Remove members from this group. Group only; the
    bot must be a group admin. They can rejoin; use gban to keep them out.

    Usage: kick user_id/@mention ... [-c] (or reply to a member's message)
        -c : remove them from every group of this group's community, and
            from the community itself (community admins only)

    user_id: phone number with country code (leading + optional) or lid
    """
    community, rest = parse(args)
    group_info, parent = await admin_check(event, client, community)
    if not group_info:
        return
    ids = targets(event, rest)
    if not ids:
        return await event.reply(getdoc(kick))
    try:
        if community:
            await community_remove(event, client, parent, ids, ban=False)
        else:
            await kick_or_ban(event, client, group_info, ids, ban=False)
    except Exception as e:
        await logger(Exception)
        await event.reply(f"*Error:*\n{e}")


async def kick_or_ban(event: Event, client, group_info, ids: list, ban: bool):
    participants = group_info.Participants
    to_remove, notes = [], []
    banned = bans(event.chat.id) if ban else set()
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
    failed = await remove(client, event.chat.jid, to_remove) if to_remove else []
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


async def community_remove(event: Event, client, parent, ids: list, ban: bool):
    """
    Removes ids from every group of the community the bot is admin in, then
    from the community itself; with ban, they're kept out of all of them
    """
    removed = {user: [] for user in ids}
    skipped, protected_ids = [], set()
    # Each user's JID as the groups know it, for removing them from the
    # community
    known = {}
    groups = await client.get_sub_groups(parent)
    for group in groups:
        try:
            info = await client.get_group_info(group.JID)
        except Exception:
            skipped.append(group.GroupName.Name)
            continue
        if not user_is_admin(bot.client.me.JID.User, info.Participants):
            skipped.append(info.GroupName.Name)
            continue
        members = {}
        for user in ids:
            member = find_member(user, info.Participants)
            if member is None:
                continue
            known.setdefault(user, member.JID)
            if protected(member, info.Participants):
                protected_ids.add(user)
                continue
            members[user] = member
        if not members:
            continue
        failed = await remove(client, group.JID, list(members.values()))
        for user, member in members.items():
            if member not in failed:
                removed[user].append(info.GroupName.Name)
    # Out of the community itself too; groups the bot couldn't reach included
    leaving = [
        known.get(u) or jid.build_jid(u, "s.whatsapp.net")
        for u in ids
        if u not in protected_ids
    ]
    if leaving:
        try:
            await client.update_group_participants(
                parent, leaving, ParticipantChange.REMOVE
            )
        except Exception:
            pass
    notes = []
    if ban:
        banned = bans(parent.User, community=True)
        for user in ids:
            if user not in protected_ids:
                banned.update(await other_ids(user))
        await save2db2(bot.group_dict, "groups")
    for user in ids:
        if user in protected_ids:
            notes.append("*Owner, sudoers, group admins and the bot can't be removed.*")
            continue
        where = (
            f"from {len(removed[user])} group(s)" if removed[user] else "from no groups"
        )
        what = "banned from the community and removed" if ban else "removed"
        notes.append(f"*{user}:* {what} {where}.")
    if skipped:
        notes.append(f"_Skipped (I'm not admin there): {', '.join(skipped)}_")
    await event.reply("\n".join(dict.fromkeys(notes)))


async def gban(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Ban members from this group: they're removed,
    removed again whenever they rejoin, and their requests to join are
    declined, until gunban. Group only; the bot must be a group admin.
    Someone who isn't in the group yet can be banned by number. With no
    arguments, lists the banned.

    Usage: gban user_id/@mention ... [-c] (or reply to a member's message)
        -c : ban them from this group's whole community: removed from every
            group of it the bot is admin in, and kept out of all of them
            (community admins only)

    user_id: phone number with country code (leading + optional) or lid
    """
    community, rest = parse(args)
    group_info, parent = await admin_check(event, client, community)
    if not group_info:
        return
    ids = targets(event, rest)
    if not ids:
        banned = bot.group_dict.get(
            parent.User if community else event.chat.id, {}
        ).get("community_gbanned" if community else "gbanned")
        where = "this community" if community else "this group"
        if not banned:
            return await event.reply(
                f"*No one is banned from {where}.*\n\n{getdoc(gban)}"
            )
        listed = "\n".join(f"• {u}" for u in sorted(banned))
        return await event.reply(f"*Banned from {where} (ids):*\n{listed}")
    try:
        if community:
            await community_remove(event, client, parent, ids, ban=True)
        else:
            await kick_or_ban(event, client, group_info, ids, ban=True)
    except Exception as e:
        await logger(Exception)
        await event.reply(f"*Error:*\n{e}")


async def gunban(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Lift a group ban, so the user can rejoin. Group
    only. It doesn't add them back; use add for that.

    Usage: gunban user_id ... [-c] (as listed by gban)
        -c : lift a community ban (community admins only)
    """
    community, rest = parse(args)
    if not event.chat.is_group:
        return await event.react("🚫")
    group_info = await client.get_group_info(event.chat.jid)
    parent = community_of(group_info)
    if community and not parent:
        return await event.reply("*This group isn't part of a community.*")
    user = event.from_user.id
    if not user_is_privileged(user):
        try:
            admins_of = await client.get_group_info(parent) if community else group_info
            allowed = user_is_admin(user, admins_of.Participants)
        except Exception:
            allowed = False
        if not allowed:
            return await event.react("🚫")
    ids = targets(event, rest)
    if not ids:
        return await event.reply(getdoc(gunban))
    banned = bans(parent.User, community=True) if community else bans(event.chat.id)
    where = "this community" if community else "this group"
    notes = []
    for user in ids:
        if user not in banned:
            notes.append(f"*{user} isn't banned from {where}.*")
            continue
        # Their other id (number or LID) goes too
        banned.difference_update(await other_ids(user))
        notes.append(f"*{user} can join {where} again.*")
    await save2db2(bot.group_dict, "groups")
    await event.reply("\n".join(notes))


async def add(event: Event, args, client):
    """
    [Owner | Sudo | Admin] Add people to this group by phone number. Group
    only; the bot must be a group admin. If someone's privacy settings stop
    the bot from adding them, they're sent an invite to join that only they
    can use (WhatsApp's own, which doesn't show the group's link), or the
    invite link if WhatsApp gives no invite for them.
    Banned users (gban) must be unbanned first.

    Usage: add phone_number ... [-c] (with country code, leading + optional)
        -c : add them to this group's community instead, which puts them in
            its announcement group (community admins only)
    """
    community, rest = parse(args)
    group_info, parent = await admin_check(event, client, community)
    if not group_info:
        return
    numbers = [a.lstrip("+") for a in rest.split()]
    numbers = [n for n in numbers if n.isdigit()]
    if not numbers:
        return await event.reply(getdoc(add))
    banned = await banned_for(event.chat.jid)
    if community:
        banned |= bot.group_dict.get(parent.User, {}).get("community_gbanned") or set()
    notes = []
    if blocked := [n for n in numbers if n in banned]:
        notes += [f"*{n} is banned; gunban them first.*" for n in blocked]
        numbers = [n for n in numbers if n not in blocked]
    target = parent if community else event.chat.jid
    try:
        if numbers:
            info = await client.get_group_info(parent) if community else group_info
            notes += await add_numbers(client, target, info.GroupName.Name, numbers)
        await event.reply("\n".join(notes))
    except Exception as e:
        await logger(Exception)
        await event.reply(f"*Error:*\n{e}")


async def group_thumbnail(client, target) -> bytes | None:
    """The group's picture as a small JPEG, or None if it has none"""
    try:
        info = await client.get_profile_picture(
            target, GetProfilePictureParams(Preview=True)
        )
        if not getattr(info, "URL", None):
            return None
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(info.URL) as resp:
                return await resp.read() if resp.status == 200 else None
    except Exception:
        return None


async def send_invite(client, target, name: str, number: str, request, thumb):
    """
    WhatsApp's invite to join for someone who can't be added: it uses the
    code made for them, so the group's invite link isn't shared
    """
    invite = GroupInviteMessage(
        groupJID=f"{target.User}@{target.Server}",
        inviteCode=request.Code,
        inviteExpiration=int(request.Expiration),
        groupName=name,
        caption=f"You're invited to join {name}.",
    )
    if thumb:
        invite.JPEGThumbnail = thumb
    await client.send_message(
        jid.build_jid(number, "s.whatsapp.net"), Message(groupInviteMessage=invite)
    )


async def add_numbers(client, target, name: str, numbers: list) -> list:
    """Adds numbers to a group or community (named name); a note on each"""
    result = await client.update_group_participants(
        target,
        [jid.build_jid(n, "s.whatsapp.net") for n in numbers],
        ParticipantChange.ADD,
    )
    # Results aren't promised in the order sent; match them by number
    by_number = {}
    for participant in result:
        for user in member_ids(participant):
            by_number[user] = participant
    notes, link, thumb = [], None, False
    for number in numbers:
        if not (participant := by_number.get(number)):
            notes.append(f"*No answer from WhatsApp for {number}.*")
            continue
        if not participant.Error:
            notes.append(f"*Added {number}.*")
            continue
        why = ADD_ERRORS.get(
            participant.Error, f"couldn't be added (error {participant.Error})"
        )
        if participant.Error == 403 and participant.AddRequest.Code:
            if thumb is False:
                thumb = await group_thumbnail(client, target)
            await send_invite(
                client, target, name, number, participant.AddRequest, thumb
            )
            why += "; they were sent an invite to join"
        elif participant.Error == 403:
            link = link or await client.get_group_invite_link(target)
            await client.send_message(
                jid.build_jid(number, "s.whatsapp.net"),
                f"You're invited to join {name}:\n{link}",
            )
            why += "; they were sent the invite link"
        notes.append(f"*{number} {why}.*")
    return notes


async def keep_out_banned(gc_msg) -> set:
    """
    Removes users banned from the group or its community who just joined;
    the ids of those removed, so they aren't welcomed
    """
    banned = await banned_for(gc_msg.JID)
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


async def decline_banned_request(gc_msg):
    """Rejects a banned user's request to join (the event's Sender)"""
    banned = await banned_for(gc_msg.JID)
    requester = gc_msg.Sender
    if not banned or not (await other_ids(requester.User)) & banned:
        return
    # Needs neonize with update_group_request_participants; until then a
    # banned user whose request is approved is removed on joining
    if not hasattr(bot.client, "update_group_request_participants"):
        return
    try:
        await bot.client.update_group_request_participants(
            gc_msg.JID, [requester], ParticipantRequestChange.REJECT
        )
    except Exception:
        await logger(Exception)

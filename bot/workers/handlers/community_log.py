"""
Member changes: .communitylog gives a group a line for each member who
joins, leaves or is removed from its community or any of the community's
groups the bot is in, and each group counts who rejoins it (shown by .id)
"""

import asyncio
import time
from inspect import getdoc

from bot import bot, jid
from bot.utils.db_utils import save2db2
from bot.utils.events import Event
from bot.utils.log_utils import logger

from .members import admin_check, community_of

# A group's community and name, cached: they rarely change, and every join
# or leave needs them
GROUP_TTL = 60 * 60
_groups: dict[str, tuple[float, str | None, str]] = {}
# Changes are held this long and merged into one line: removing someone from
# a community comes as a leave in each of its groups
MERGE_DELAY = 5
_pending: dict[tuple, list[str]] = {}

JOIN_REASONS = {"invite": "via invite link"}


async def group_details(group) -> tuple[str | None, str]:
    """(the community's JID user or None, the group's name) for a group"""
    now = time.time()
    if (cached := _groups.get(group.User)) and now - cached[0] < GROUP_TTL:
        return cached[1], cached[2]
    info = await bot.client.get_group_info(group)
    parent = community_of(info)
    community = parent.User if parent else None
    if info.GroupParent.IsParent:
        community = group.User
    _groups[group.User] = (now, community, info.GroupName.Name)
    return community, info.GroupName.Name


async def as_lid(user) -> str | None:
    """A JID's LID user, so one person is the same key in every group"""
    if user.Server == "lid":
        return user.User
    try:
        return (await bot.client.get_lid_from_pn(user)).User or None
    except Exception:
        return None


def is_me(user) -> bool:
    me = bot.client.me
    return user.User in (me.JID.User, me.LID.User)


async def log_member_changes(gc_msg):
    """Queues a line for each join or leave in a group whose community logs them"""
    try:
        community, name = await group_details(gc_msg.JID)
        if not community:
            return
        if not (log_chat := bot.group_dict.get(community, {}).get("community_log")):
            return
        sender = await as_lid(gc_msg.Sender) if gc_msg.Sender.User else None
        changes = [(u, "join") for u in gc_msg.Join] + [
            (u, "leave") for u in gc_msg.Leave
        ]
        for user, action in changes:
            if is_me(user) or not (lid := await as_lid(user)):
                continue
            by = sender if sender and sender != lid else None
            if action == "join":
                detail = JOIN_REASONS.get(gc_msg.JoinReason) or (
                    f"added by @{by}" if by else ""
                )
            else:
                action, detail = ("remove", by) if by else ("leave", "")
            key = (log_chat, action, lid, detail)
            if key in _pending:
                if name not in _pending[key]:
                    _pending[key].append(name)
                continue
            _pending[key] = [name]
            asyncio.create_task(send_after_delay(key))
    except Exception:
        await logger(Exception)


async def count_rejoins(gc_msg):
    """Counts a join by someone who left this group before as a rejoin"""
    try:
        settings = bot.group_dict.setdefault(gc_msg.JID.User, {})
        left = settings.setdefault("has_left", set())
        rejoins = settings.setdefault("rejoins", {})
        for user in gc_msg.Leave:
            if not is_me(user) and (lid := await as_lid(user)):
                left.add(lid)
        for user in gc_msg.Join:
            if (lid := await as_lid(user)) in left:
                rejoins[lid] = rejoins.get(lid, 0) + 1
        await save2db2(bot.group_dict, "groups")
    except Exception:
        await logger(Exception)


async def send_after_delay(key: tuple):
    await asyncio.sleep(MERGE_DELAY)
    log_chat, action, user, detail = key
    groups = ", ".join(f"*{n}*" for n in _pending.pop(key, []))
    if action == "join":
        line = f"➕ @{user} joined {groups}" + (f" ({detail})" if detail else "")
    elif action == "remove":
        line = f"🚫 @{detail} removed @{user} from {groups}"
    else:
        line = f"➖ @{user} left {groups}"
    chat, _, server = log_chat.partition(":")
    try:
        await bot.client.send_message(
            jid.build_jid(chat, server), line, mentions_are_lids=True
        )
    except Exception:
        await logger(Exception)


async def community_log(event: Event, args, client):
    """
    [Owner | Sudo | Community admin] Log in this group every member who
    joins, leaves or is removed from this group's community or any of its
    groups the bot is in. Group only; the group must be in a community. With
    no arguments, shows the setting.

    Usage: communitylog on | off
        on : log the community's member changes here (replaces any other
            log group)
        off : stop logging them
    """
    if args.casefold() not in ("", "on", "off"):
        return await event.reply(getdoc(community_log))
    group_info, parent = await admin_check(event, client, community=True)
    if not group_info:
        return
    try:
        settings = bot.group_dict.setdefault(parent.User, {})
        here = f"{event.chat.id}:{event.chat.server}"
        if args.casefold() == "on":
            settings["community_log"] = here
            await save2db2(bot.group_dict, "groups")
            return await event.reply(
                "*This community's member changes will be logged here.*"
            )
        if args.casefold() == "off":
            if not settings.pop("community_log", None):
                return await event.reply("*This community isn't being logged.*")
            await save2db2(bot.group_dict, "groups")
            return await event.reply("*Stopped logging this community.*")
        current = settings.get("community_log")
        if not current:
            msg = "*This community's member changes aren't logged.*"
        elif current == here:
            msg = "*This community's member changes are logged here.*"
        else:
            chat, _, server = current.partition(":")
            try:
                name = (
                    await client.get_group_info(jid.build_jid(chat, server))
                ).GroupName.Name
            except Exception:
                name = chat
            msg = f"*This community's member changes are logged in {name}.*"
        await event.reply(msg)
    except Exception:
        await logger(Exception)
        await event.react("❌")

"""
Member changes: .communitylog gives a group a line for each member who
joins, leaves or is removed from its community or any of the community's
groups the bot is in, and each group counts who rejoins it (shown by .id)
"""

from inspect import getdoc

from bot import bot, jid
from bot.utils.db_utils import save2db2
from bot.utils.events import Event
from bot.utils.log_utils import logger

from bot.utils.group_utils import admin_check, community_of, log_member_changes, count_rejoins


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

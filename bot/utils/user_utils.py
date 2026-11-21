"""
User ID resolution and display utilities. Moved from handlers/manage.py to break
handler-to-handler import cycles.
"""

import re

from bot import bot, jid
from bot.utils.group_utils import group_standing
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    chat_is_allowed,
    get_user_info,
    user_is_allowed,
    user_is_privileged,
)


async def ids_from_lid(lid: str) -> tuple[str, str | None]:
    """(LID, phone number if WhatsApp has it)"""
    try:
        number = (await bot.client.get_pn_from_lid(jid.build_jid(lid, "lid"))).User
    except Exception:
        number = None
    return lid, number or None


async def ids_from_number(number: str) -> tuple[str | None, str]:
    """(LID if WhatsApp has it, phone number)"""
    try:
        pn = jid.build_jid(number, "s.whatsapp.net")
        lid = (await bot.client.get_lid_from_pn(pn)).User
    except Exception:
        lid = None
    return lid or None, number


async def target_user(event, args: str) -> tuple[str | None, str | None, str]:
    """
    (LID, phone number, the rest of args) for the user named first in args
    (a mention, LID or +number), else the replied user, else (None, None, args)
    """
    first, _, rest = args.partition(" ")
    if first and re.fullmatch(r"@?\+?[0-9]{5,16}", first):
        token = first.lstrip("@")
        # As .sudo reads ids: +number, or a LID where chats address by LID
        if token.startswith("+") or not event.lid_address:
            lid, number = await ids_from_number(token.lstrip("+"))
        else:
            lid, number = await ids_from_lid(token)
        return lid, number, rest.strip()
    if replied := event.reply_to_message:
        user = replied.from_user
        if number := getattr(user, "ph", None):
            lid, number = await ids_from_number(number)
            return lid or user.id or None, number, args
        lid, number = await ids_from_lid(user.id)
        return lid, number, args
    return None, None, args


async def show_ids(event, args, client):
    """
    Show your WhatsApp ids, or the replied or mentioned user's, and the chat's;
    in a group, also their roles there, the other groups of its community
    the bot sees them in, and how often they've rejoined it (from 2 times).
    Phone numbers are only shown in a private chat with the bot.
    Usage: id [@user]
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        lid, number, _ = await target_user(event, (args or "").strip())
        if not lid:
            lid, number = user, getattr(event.from_user, "ph", None)
        info = await get_user_info(lid, "lid")
        name = info.PushName or info.FullName or "Unknown"
        lines = [f"*{name}*", f"- *LID:* {lid}"]
        # Groups hide numbers behind LIDs; only show them in private
        if number and not event.chat.is_group:
            lines.append(f"- *Number:* +{number}")
        lines.append(f"- *Chat:* {event.chat.id}@{event.chat.server}")
        if event.chat.is_group:
            lines += await group_standing(event, client, {lid} | {number} - {None})
            # Once is shown as nothing, like never
            rejoins = bot.group_dict.get(event.chat.id, {}).get("rejoins", {})
            if (times := rejoins.get(lid, 0)) > 1:
                lines.append(f"- *Rejoined:* {times} times")
        await event.reply("\n".join(lines))
    except Exception:
        await logger(Exception)
        await event.react("❌")

"""
User ID resolution utilities. Moved from handlers/manage.py to break
handler-to-handler import cycles.
"""

import re

from bot import bot, jid


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
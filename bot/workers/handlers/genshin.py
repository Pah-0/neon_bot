from inspect import getdoc

from bot.config import bot
from bot.utils.bot_utils import list_to_str
from bot.utils.db_utils import save2db2
from bot.utils.gift_utils import fetch_codes, get_msg_from_codes
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    chat_is_allowed,
    user_is_admin,
    user_is_allowed,
    user_is_privileged,
)


async def manage_autogift_chat(event, args, client):
    try:
        action, _, chat = args.strip().partition(" ")
        chat = chat.strip()
        chats = bot.gift_dict["chats"]
        pinned = bot.gift_dict["pin"]
        this_chat = f"{event.chat.id}:{event.chat.server}"
        user = event.from_user.id
        if not user_is_privileged(user):
            # Group admins may only manage their own group
            if not event.chat.is_group:
                return await event.react("🚫")
            group_info = await client.get_group_info(event.chat.jid)
            if not user_is_admin(user, group_info.Participants):
                return await event.react("🚫")
            if action == "-get":
                if this_chat not in chats:
                    return await event.reply("*New codes aren't posted here.*")
                pin = " and pinned for a day" if this_chat in pinned else ""
                return await event.reply(f"*New codes are posted here{pin}.*")
            if chat and chat not in (".", this_chat, event.chat.id):
                return await event.reply("*Admins can only manage this chat (.)!*")
        if action == "-get":
            if not chats:
                return await event.reply("*No chat set!*")
            return await event.reply(
                list_to_str(
                    [
                        (c or "default") + (" (pinned)" if c in pinned else "")
                        for c in chats
                    ],
                    sep=", ",
                )
            )
        if action not in ("-add", "-rm", "-pin", "-unpin") or not chat:
            return await event.reply(getdoc(gift_codes))
        if chat in (".", event.chat.id):
            chat = this_chat
        elif chat.casefold() == "default":
            chat = None
        if action == "-add":
            if chat in chats:
                return await event.reply("*Chat already added!*")
            chats.append(chat)
            done = "added"
        elif chat not in chats:
            return await event.reply("*Given chat was never added!*")
        elif action == "-rm":
            chats.remove(chat)
            if chat in pinned:
                pinned.remove(chat)
            done = "removed"
        elif action == "-pin":
            if chat in pinned:
                return await event.reply("*Already pinning in this chat!*")
            pinned.append(chat)
            done = "set to pin new codes for a day (if I'm an admin there)"
        else:
            if chat not in pinned:
                return await event.reply("*Wasn't pinning in this chat!*")
            pinned.remove(chat)
            done = "set to stop pinning new codes"
        await save2db2(bot.gift_dict, "gift")
        await event.reply(f"*{chat or 'default'}* has been {done}.")
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def gift_codes(event, args, client):
    """
    Fetches the latest Genshin Impact redeem codes.
    Uses hoyo-codes.seria.moe, or api.ennead.cc when that's down.

    Arguments:
        -add <chat> : [Owner | Sudo | Admin] post new codes to a chat automatically
        -rm <chat> : [Owner | Sudo | Admin] stop posting new codes to a chat
        -get : [Owner | Sudo | Admin] list the chats new codes are posted to
        -pin <chat> : [Owner | Sudo | Admin] pin new codes posted to an added chat
            for a day; needs the bot to be an admin there
        -unpin <chat> : [Owner | Sudo | Admin] stop pinning them
    chat: . for this chat, default for RSS_CHAT, or chat_id:server
    Group admins can only manage the group they're in.
    New codes are checked for every 30 minutes.
    """
    if args:
        return await manage_autogift_chat(event, args, client)
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    status = None
    try:
        status = await event.reply("*Fetching latest giftcodes…*")
        codes = await fetch_codes()
        await event.reply(
            get_msg_from_codes(codes) if codes else "*No active codes right now.*"
        )
        await status.delete()
    except Exception:
        await logger(Exception)
        await event.react("❌")
        if status:
            await status.edit("*Couldn't fetch the codes right now.*")

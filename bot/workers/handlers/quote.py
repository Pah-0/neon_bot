import io
from inspect import getdoc

from PIL import Image

from bot.config import bot
from bot.utils.log_utils import logger
from bot.utils.msg_store import get_messages
from bot.utils.msg_utils import (
    chat_is_allowed,
    get_user_info,
    user_is_allowed,
    user_is_privileged,
)
from bot.utils.quote_card import quote_card
from bot.workers.handlers.wa import get_member_label


async def quote(event, args, client):
    """
    Turns the replied message into a quote sticker: the sender's profile
    picture and their message in a chat bubble, emoji and all. Photos and
    stickers in the message are included, and if it was itself a reply, the
    message it answered is shown at the top for context.

    Usage: reply to a message with quote [-img]
        -img : send it as a picture instead of a sticker
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    replied = event.reply_to_message
    if not replied:
        return await event.reply(getdoc(quote))
    try:
        async with event.react("🎨"):
            png, name = await make_quote(event, replied)
            if "-img" in (args or "").casefold().split():
                # A photo has no transparency; put the bubble on a chat-like
                # backdrop
                image = Image.open(io.BytesIO(png)).convert("RGBA")
                backdrop = Image.new(
                    "RGBA", (image.width + 60, image.height + 60), (239, 234, 226, 255)
                )
                backdrop.alpha_composite(image, (30, 30))
                out = io.BytesIO()
                backdrop.convert("RGB").save(out, format="jpeg", quality=92)
                return await event.reply_photo(out.getvalue())
            me = bot.client.me or await bot.client.get_me()
            await event.reply_sticker(png, quote=True, name=name, packname=me.PushName)
    except NothingToQuote:
        await event.reply("*Reply to a text, photo or sticker message.*")
    except Exception:
        await logger(Exception)
        await event.react("❌")


class NothingToQuote(Exception):
    pass


async def sender_name(user) -> str:
    info = await get_user_info(user.id, user.server)
    return info.PushName or (f"+{user.id}" if user.server != "lid" else "Unknown")


def preview_text(message) -> str:
    """A message's text, or what it holds when it has none"""
    if text := (message.text or message.caption):
        return text
    if message.image:
        return "📷 Photo"
    if message.sticker:
        return "Sticker"
    if message.video:
        return "🎥 Video"
    if message.audio:
        return "🎤 Voice message"
    if message.document:
        return "📄 Document"
    return "Message"


async def make_quote(event, replied) -> tuple[bytes, str]:
    """The quote image and the quoted sender's name"""
    text = replied.text or replied.caption or ""
    media = None
    if replied.image or replied.sticker:
        try:
            media = await replied.download()
        except Exception:
            media = None
    if not text and media is None:
        raise NothingToQuote
    sender = replied.from_user
    # The quoted copy drops what it replied to; the stored message keeps it
    reply = None
    try:
        stored = await get_messages(event.chat.id, replied.id)
        answered = stored[0].reply_to_message if stored else None
    except Exception:
        answered = None
    if answered is not None:
        reply = {
            "user": answered.from_user.id,
            "name": await sender_name(answered.from_user),
            "text": preview_text(answered),
            "tag": get_member_label(event.chat.id, answered.from_user),
        }
    name = await sender_name(sender)
    tag = get_member_label(event.chat.id, sender)
    png = await quote_card(sender.id, sender.jid, name, text, media, reply, tag)
    return png, name

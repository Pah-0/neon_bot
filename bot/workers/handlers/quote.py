import io
from inspect import getdoc

from PIL import Image

from bot.config import bot
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    chat_is_allowed,
    get_user_info,
    user_is_allowed,
    user_is_privileged,
)
from bot.utils.quote_card import quote_card


async def quote(event, args, client):
    """
    Turns the replied message into a quote sticker: the sender's profile
    picture and their message in a chat bubble, emoji and all. Photos and
    stickers in the message are included.

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
        text = replied.text or replied.caption or ""
        media = None
        if replied.image or replied.sticker:
            try:
                media = await replied.download()
            except Exception:
                media = None
        if not text and media is None:
            return await event.reply("*Reply to a text, photo or sticker message.*")
        sender = replied.from_user
        info = await get_user_info(sender.id, sender.server)
        name = info.PushName or (
            f"+{sender.id}" if sender.server != "lid" else "Unknown"
        )
        png = await quote_card(sender.id, sender.jid, name, text, media)
        if "-img" in (args or "").casefold().split():
            # A photo has no transparency; put the bubble on a dark backdrop
            image = Image.open(io.BytesIO(png)).convert("RGBA")
            backdrop = Image.new(
                "RGBA", (image.width + 60, image.height + 60), (11, 20, 26, 255)
            )
            backdrop.alpha_composite(image, (30, 30))
            out = io.BytesIO()
            backdrop.convert("RGB").save(out, format="jpeg", quality=92)
            return await event.reply_photo(out.getvalue())
        me = bot.client.me or await bot.client.get_me()
        await event.reply_sticker(png, quote=True, name=name, packname=me.PushName)
    except Exception:
        await logger(Exception)
        await event.react("❌")

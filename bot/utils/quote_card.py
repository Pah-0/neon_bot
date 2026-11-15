"""
.quote: a replied message drawn as a chat bubble with the sender's profile
picture, emoji and all, wrapped so nothing is clipped.
"""

import asyncio
import io
import re
import zlib

from PIL import Image, ImageDraw, ImageFilter

from .bot_utils import sync_to_async
from .card_text import draw_rich, load_emojis, profile_picture, rich_width, wrap
from .hoyo_cards import ensure_fonts, paste_fit
from .rank_card import avatar_fallback, fit_rich

AVATAR = 84
GAP = 16
PAD = 26
NAME_SIZE, TEXT_SIZE = 28, 32
MAX_TEXT_W = 720
MEDIA_MAX_W, MEDIA_MAX_H = 720, 720
# A white bubble reads on both light and dark chat backgrounds
BUBBLE = (255, 255, 255)
TEXT = (17, 27, 33, 255)
# The replied-to message, shown at the top of the bubble
REPLY_BG = (240, 242, 245, 255)
REPLY_TEXT = (84, 101, 111, 255)
REPLY_SIZE = 24
SHADOW = 10
# WhatsApp-like colours for names in groups, dark enough for white
NAME_COLOURS = [
    (2, 132, 199),
    (234, 88, 12),
    (124, 58, 237),
    (5, 150, 105),
    (219, 39, 119),
    (180, 120, 0),
    (77, 124, 15),
    (220, 38, 38),
]


def name_colour(user: str) -> tuple:
    return NAME_COLOURS[zlib.crc32(user.encode()) % len(NAME_COLOURS)]


def strip_markup(text: str) -> str:
    """WhatsApp's *bold* _italic_ ~strike~ ```mono``` markers, removed"""
    text = re.sub(r"```(.+?)```", r"\1", text, flags=re.S)
    for mark in ("*", "_", "~"):
        m = re.escape(mark)
        text = re.sub(rf"(?<![\w{m}]){m}(?=\S)(.+?)(?<=\S){m}(?![\w{m}])", r"\1", text)
    return text


def _draw_quote(
    name: str, colour: tuple, text: str, media, avatar, emojis, reply: dict | None
) -> bytes:
    lines = wrap(text, TEXT_SIZE, False, MAX_TEXT_W, emojis) if text else []
    line_h = int(TEXT_SIZE * 1.42)
    name_w = rich_width(name, NAME_SIZE, True, emojis)
    text_w = max(
        (rich_width(line, TEXT_SIZE, False, emojis) for line in lines), default=0
    )
    media_size = (0, 0)
    if media is not None:
        media = media.copy()
        media.thumbnail((MEDIA_MAX_W, MEDIA_MAX_H), Image.Resampling.LANCZOS)
        media_size = media.size
    reply_w = reply_h = 0
    if reply:
        reply["name"] = fit_rich(
            reply["name"], REPLY_SIZE, True, MAX_TEXT_W - 40, emojis
        )
        reply["text"] = fit_rich(
            reply["text"], REPLY_SIZE, False, MAX_TEXT_W - 40, emojis
        )
        reply_w = 40 + max(
            rich_width(reply["name"], REPLY_SIZE, True, emojis),
            rich_width(reply["text"], REPLY_SIZE, False, emojis),
        )
        reply_h = REPLY_SIZE * 2 + 34
    inner_w = max(name_w, text_w, media_size[0], reply_w, 160)
    inner_h = NAME_SIZE + 14
    if reply:
        inner_h += reply_h + 14
    if media is not None:
        inner_h += media_size[1] + (14 if lines else 0)
    inner_h += line_h * len(lines)

    m = SHADOW  # room for the shadow all round
    bubble_x = AVATAR + GAP + m
    width = int(bubble_x + inner_w + PAD * 2 + m)
    height = int(max(AVATAR, inner_h + PAD * 2) + m * 2)
    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    bx0, by0, bx1, by1 = bubble_x, m, width - m, height - m
    tail = [(bx0 - 12, by1), (bx0 + 18, by1 - 30), (bx0 + 30, by1)]

    # A soft shadow so the white bubble stands out on light chats too
    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    shade = ImageDraw.Draw(shadow)
    shade.rounded_rectangle((bx0, by0 + 3, bx1, by1 + 3), 28, fill=(0, 0, 0, 70))
    shade.polygon([(x, y + 3) for x, y in tail], fill=(0, 0, 0, 70))
    card.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    draw = ImageDraw.Draw(card, "RGBA")

    # Profile picture, bottom left like a chat
    paste_fit(
        card, avatar, (m, height - AVATAR - m, m + AVATAR, height - m), circle=True
    )

    # Bubble with a tail toward the picture
    draw.rounded_rectangle((bx0, by0, bx1, by1), 28, fill=BUBBLE + (255,))
    draw.polygon(tail, fill=BUBBLE + (255,))

    x, y = bx0 + PAD, by0 + PAD - 6
    draw_rich(card, draw, (x, y), name, NAME_SIZE, True, colour + (255,), emojis)
    y += NAME_SIZE + 14
    if reply:
        # Like WhatsApp: a tinted box with a bar in the replied sender's colour
        rx1 = x + inner_w
        draw.rounded_rectangle((x, y, rx1, y + reply_h), 14, fill=REPLY_BG)
        draw.rounded_rectangle(
            (x, y, x + 8, y + reply_h), 4, fill=reply["colour"] + (255,)
        )
        draw_rich(
            card,
            draw,
            (x + 22, y + 10),
            reply["name"],
            REPLY_SIZE,
            True,
            reply["colour"] + (255,),
            emojis,
        )
        draw_rich(
            card,
            draw,
            (x + 22, y + 18 + REPLY_SIZE),
            reply["text"],
            REPLY_SIZE,
            False,
            REPLY_TEXT,
            emojis,
        )
        y += reply_h + 14
    if media is not None:
        mask = Image.new("L", media_size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *media_size), 18, fill=255)
        layer = Image.new("RGBA", media_size, (0, 0, 0, 0))
        layer.paste(media, (0, 0), media if media.mode == "RGBA" else None)
        card.paste(
            layer, (int(x), int(y)), Image.composite(layer.getchannel("A"), mask, mask)
        )
        y += media_size[1] + 14
    for line in lines:
        draw_rich(card, draw, (x, y), line, TEXT_SIZE, False, TEXT, emojis)
        y += line_h

    output = io.BytesIO()
    card.save(output, format="png")
    return output.getvalue()


async def quote_card(
    user: str,
    jid,
    name: str,
    text: str,
    media: bytes | None,
    reply: dict | None = None,
) -> bytes:
    """
    A PNG (transparent around the bubble) of a message quoted as a chat
    bubble. reply: {user, name, text} of the message it answered, if any.
    """
    text = strip_markup(text or "").strip()
    if reply:
        first = strip_markup(reply.get("text") or "").strip().split("\n")[0]
        reply = {**reply, "text": first, "colour": name_colour(reply["user"])}
    reply_texts = (reply["name"], reply["text"]) if reply else ()
    await ensure_fonts(name, text, *reply_texts)
    image = None
    if media:
        try:
            image = Image.open(io.BytesIO(media)).convert("RGBA")
        except Exception:
            image = None
    picture, emojis = await asyncio.gather(
        profile_picture(jid), load_emojis(name, text, *reply_texts)
    )
    avatar = picture or avatar_fallback(name, name_colour(user))
    return await sync_to_async(
        _draw_quote, name, name_colour(user), text, image, avatar, emojis, reply
    )

"""
.quote: a replied message drawn as a chat bubble with the sender's profile
picture, emoji and all, wrapped so nothing is clipped.
"""

import asyncio
import io
import re
import zlib

from PIL import Image, ImageDraw

from .bot_utils import sync_to_async
from .card_text import draw_rich, load_emojis, profile_picture, rich_width, wrap
from .hoyo_cards import ensure_fonts, paste_fit
from .rank_card import avatar_fallback

AVATAR = 84
GAP = 16
PAD = 26
NAME_SIZE, TEXT_SIZE = 28, 32
MAX_TEXT_W = 720
MEDIA_MAX_W, MEDIA_MAX_H = 720, 720
BUBBLE = (31, 44, 52)
TEXT = (233, 237, 239, 255)
# WhatsApp-like colours for names in groups
NAME_COLOURS = [
    (83, 189, 235),
    (255, 157, 92),
    (167, 145, 255),
    (6, 207, 156),
    (255, 114, 161),
    (252, 201, 83),
    (124, 207, 79),
    (229, 115, 115),
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


def _draw_quote(name: str, colour: tuple, text: str, media, avatar, emojis) -> bytes:
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
    inner_w = max(name_w, text_w, media_size[0], 160)
    inner_h = NAME_SIZE + 14
    if media is not None:
        inner_h += media_size[1] + (14 if lines else 0)
    inner_h += line_h * len(lines)

    bubble_x = AVATAR + GAP
    width = int(bubble_x + inner_w + PAD * 2 + 8)
    height = int(max(AVATAR, inner_h + PAD * 2) + 8)
    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card, "RGBA")

    # Profile picture, bottom left like a chat
    paste_fit(card, avatar, (0, height - AVATAR - 4, AVATAR, height - 4), circle=True)

    # Bubble with a tail toward the picture
    bx0, by0, bx1, by1 = bubble_x, 4, width - 4, height - 4
    draw.rounded_rectangle((bx0, by0, bx1, by1), 28, fill=BUBBLE + (255,))
    draw.polygon(
        [(bx0 - 12, by1), (bx0 + 18, by1 - 30), (bx0 + 30, by1)], fill=BUBBLE + (255,)
    )

    x, y = bx0 + PAD, by0 + PAD - 6
    draw_rich(card, draw, (x, y), name, NAME_SIZE, True, colour + (255,), emojis)
    y += NAME_SIZE + 14
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
    user: str, jid, name: str, text: str, media: bytes | None
) -> bytes:
    """A PNG (transparent around the bubble) of a message quoted as a chat bubble"""
    text = strip_markup(text or "").strip()
    await ensure_fonts(name, text)
    image = None
    if media:
        try:
            image = Image.open(io.BytesIO(media)).convert("RGBA")
        except Exception:
            image = None
    picture, emojis = await asyncio.gather(
        profile_picture(jid), load_emojis(name, text)
    )
    avatar = picture or avatar_fallback(name, name_colour(user))
    return await sync_to_async(
        _draw_quote, name, name_colour(user), text, image, avatar, emojis
    )

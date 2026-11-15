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
# A member's group tag, after their name like WhatsApp shows it
TAG_COLOUR = (102, 119, 129, 255)
TAG_SIZE = 22
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


def tail_points(bx0: float, by1: float) -> list[tuple[float, float]]:
    """
    The bubble's tail, like WhatsApp's: its left side bends gradually out
    from about 46px up to a point by the profile picture, and its bottom
    runs straight back under the bubble
    """
    p0, p1, p2, p3 = (
        (bx0, by1 - 46),
        (bx0, by1 - 18),
        (bx0 - 6, by1 - 2),
        (bx0 - 22, by1),
    )
    curve = []
    for i in range(25):
        t = i / 24
        a, b, c, d = (1 - t) ** 3, 3 * (1 - t) ** 2 * t, 3 * (1 - t) * t**2, t**3
        curve.append(
            (
                a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
            )
        )
    return [*curve, (bx0 + 34, by1), (bx0 + 34, by1 - 46)]


def _draw_name(card, draw, x, y, name, size, colour, tag, tag_size, emojis):
    """A name and, after it, the member's tag in grey"""
    x = draw_rich(card, draw, (x, y), name, size, True, colour, emojis)
    if tag:
        draw_rich(
            card,
            draw,
            (x + 12, y + (size - tag_size) * 0.6),
            tag,
            tag_size,
            False,
            TAG_COLOUR,
            emojis,
        )


def _draw_quote(
    name: str,
    colour: tuple,
    text: str,
    media,
    avatar,
    emojis,
    reply: dict | None,
    tag: str = "",
) -> bytes:
    lines = wrap(text, TEXT_SIZE, False, MAX_TEXT_W, emojis) if text else []
    line_h = int(TEXT_SIZE * 1.42)
    name_w = rich_width(name, NAME_SIZE, True, emojis)
    if tag:
        tag = fit_rich(tag, TAG_SIZE, False, 260, emojis)
        name_w += 12 + rich_width(tag, TAG_SIZE, False, emojis)
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
        reply_tag = reply.get("tag") or ""
        if reply_tag:
            reply["tag"] = reply_tag = fit_rich(
                reply_tag, REPLY_SIZE - 4, False, 220, emojis
            )
        reply_w = 40 + max(
            rich_width(reply["name"], REPLY_SIZE, True, emojis)
            + (
                12 + rich_width(reply_tag, REPLY_SIZE - 4, False, emojis)
                if reply_tag
                else 0
            ),
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
    tail = tail_points(bx0, by1)

    # A soft shadow so the white bubble stands out on light chats too
    shadow = Image.new("RGBA", card.size, (0, 0, 0, 0))
    shade = ImageDraw.Draw(shadow)
    shade.rounded_rectangle(
        (bx0, by0 + 3, bx1, by1 + 3),
        28,
        fill=(0, 0, 0, 70),
        corners=(True, True, True, False),
    )
    shade.polygon([(x, y + 3) for x, y in tail], fill=(0, 0, 0, 70))
    card.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(6)))
    draw = ImageDraw.Draw(card, "RGBA")

    # Profile picture, bottom left like a chat
    paste_fit(
        card, avatar, (m, height - AVATAR - m, m + AVATAR, height - m), circle=True
    )

    # Bubble with a tail toward the picture
    # Square at the bottom left, where the tail joins
    draw.rounded_rectangle(
        (bx0, by0, bx1, by1),
        28,
        fill=BUBBLE + (255,),
        corners=(True, True, True, False),
    )
    draw.polygon(tail, fill=BUBBLE + (255,))

    x, y = bx0 + PAD, by0 + PAD - 6
    _draw_name(
        card, draw, x, y, name, NAME_SIZE, colour + (255,), tag, TAG_SIZE, emojis
    )
    y += NAME_SIZE + 14
    if reply:
        # Like WhatsApp: a tinted box with a bar in the replied sender's colour
        rx1 = x + inner_w
        draw.rounded_rectangle((x, y, rx1, y + reply_h), 14, fill=REPLY_BG)
        draw.rounded_rectangle(
            (x, y, x + 8, y + reply_h), 4, fill=reply["colour"] + (255,)
        )
        _draw_name(
            card,
            draw,
            x + 22,
            y + 10,
            reply["name"],
            REPLY_SIZE,
            reply["colour"] + (255,),
            reply.get("tag"),
            REPLY_SIZE - 4,
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
    tag: str = "",
) -> bytes:
    """
    A PNG (transparent around the bubble) of a message quoted as a chat
    bubble. reply: {user, name, text, tag} of the message it answered, if
    any; tag: the sender's group tag.
    """
    text = strip_markup(text or "").strip()
    if reply:
        first = strip_markup(reply.get("text") or "").strip().split("\n")[0]
        reply = {**reply, "text": first, "colour": name_colour(reply["user"])}
    reply_texts = (
        (reply["name"], reply["text"], reply.get("tag") or "") if reply else ()
    )
    await ensure_fonts(name, text, tag, *reply_texts)
    image = None
    if media:
        try:
            image = Image.open(io.BytesIO(media)).convert("RGBA")
        except Exception:
            image = None
    picture, emojis = await asyncio.gather(
        profile_picture(jid), load_emojis(name, text, tag, *reply_texts)
    )
    avatar = picture or avatar_fallback(name, name_colour(user))
    return await sync_to_async(
        _draw_quote, name, name_colour(user), text, image, avatar, emojis, reply, tag
    )

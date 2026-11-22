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
from .msg_utils import get_user_info
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
# @mentions are drawn in this colour, like WhatsApp's tags
MENTION_COLOUR = (2, 132, 199, 255)
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


async def resolve_mentions(text: str, mentions: list[str]) -> tuple[str, list[str]]:
    """
    Replaces @<digits> in text with @<name> for each "digits@server" in
    mentions. Returns the new text and the "@name" labels to highlight.
    """

    async def lookup(mention: str):
        uid, _, server = mention.partition("@")
        try:
            info = await get_user_info(uid, server or "s.whatsapp.net")
        except Exception:
            info = None
        name = getattr(info, "PushName", "") or getattr(info, "FullName", "") or ""
        return uid, re.sub(r"\s+", " ", name).strip() or "unknown"

    labels = []
    for uid, name in await asyncio.gather(*(lookup(m) for m in mentions)):
        label = f"@{name}"
        text = re.sub(rf"@{re.escape(uid)}(?!\d)", lambda _: label, text)
        labels.append(label)
    return text, labels


def draw_marked(card, draw, xy, text, size, bold, colour, emojis, labels):
    """draw_rich, with every @label in labels in the mention colour. Returns the end x"""
    x, y = xy
    if not labels:
        return draw_rich(card, draw, (x, y), text, size, bold, colour, emojis)
    pattern = "|".join(re.escape(l) for l in sorted(set(labels), key=len, reverse=True))
    for i, part in enumerate(re.split(f"({pattern})", text)):
        if not part:
            continue
        x = draw_rich(
            card,
            draw,
            (x, y),
            part,
            size,
            bold,
            MENTION_COLOUR if i % 2 == 1 else colour,
            emojis,
        )
    return x


# Pillow draws shapes without anti-aliasing; they're drawn this many times
# larger and shrunk, which smooths their edges
SS = 4


def smooth(size: tuple, draw) -> Image.Image:
    """An RGBA layer of size with shapes drawn by draw(ImageDraw, scale)"""
    big = Image.new("RGBA", (size[0] * SS, size[1] * SS), (0, 0, 0, 0))
    draw(ImageDraw.Draw(big), SS)
    return big.resize(size, Image.Resampling.LANCZOS)


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
    mentions: list[str] = (),
) -> bytes:
    lines = wrap(text, TEXT_SIZE, False, MAX_TEXT_W, emojis) if text else []
    line_h = int(TEXT_SIZE * 1.42)
    name_w = rich_width(name, NAME_SIZE, True, emojis)
    # The sender's tag: a pill under their name, as WhatsApp shows it
    tag_w = tag_h = 0
    if tag:
        tag = fit_rich(tag, TAG_SIZE, False, MAX_TEXT_W - 36, emojis)
        tag_w = rich_width(tag, TAG_SIZE, False, emojis) + 36
        tag_h = TAG_SIZE + 22
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
    inner_w = max(name_w, tag_w, text_w, media_size[0], reply_w, 160)
    inner_h = NAME_SIZE + 14 + (tag_h + 12 if tag else 0)
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

    def bubble(fill, dy=0):
        def draw_bubble(d, k):
            # Square at the bottom left, where the tail joins
            d.rounded_rectangle(
                (bx0 * k, (by0 + dy) * k, bx1 * k, (by1 + dy) * k),
                28 * k,
                fill=fill,
                corners=(True, True, True, False),
            )
            d.polygon([(px * k, (py + dy) * k) for px, py in tail], fill=fill)

        return smooth(card.size, draw_bubble)

    # A soft shadow so the white bubble stands out on light chats too
    card.alpha_composite(
        bubble((0, 0, 0, 70), dy=3).filter(ImageFilter.GaussianBlur(6))
    )
    draw = ImageDraw.Draw(card, "RGBA")

    # Profile picture, bottom left like a chat
    paste_fit(
        card, avatar, (m, height - AVATAR - m, m + AVATAR, height - m), circle=True
    )

    # Bubble with a tail toward the picture
    card.alpha_composite(bubble(BUBBLE + (255,)))

    x, y = bx0 + PAD, by0 + PAD - 6
    draw_rich(card, draw, (x, y), name, NAME_SIZE, True, colour + (255,), emojis)
    y += NAME_SIZE + 14
    if tag:
        # Tinted in the name's colour, with a fainter border; on its own
        # layer, since drawing on the transparent card wouldn't blend
        tx, ty = x, y
        card.alpha_composite(
            smooth(
                card.size,
                lambda d, k: d.rounded_rectangle(
                    (tx * k, ty * k, (tx + tag_w) * k, (ty + tag_h) * k),
                    tag_h // 2 * k,
                    fill=colour + (34,),
                    outline=colour + (90,),
                    width=2 * k,
                ),
            )
        )
        draw_rich(
            card, draw, (x + 18, y + 6), tag, TAG_SIZE, False, colour + (255,), emojis
        )
        y += tag_h + 12
    if reply:
        # Like WhatsApp: a tinted box with a bar in the replied sender's colour
        rx0, ry0, rx1 = x, y, x + inner_w

        def draw_reply_box(d, k):
            d.rounded_rectangle(
                (rx0 * k, ry0 * k, rx1 * k, (ry0 + reply_h) * k), 14 * k, fill=REPLY_BG
            )
            d.rounded_rectangle(
                (rx0 * k, ry0 * k, (rx0 + 8) * k, (ry0 + reply_h) * k),
                4 * k,
                fill=reply["colour"] + (255,),
            )

        card.alpha_composite(smooth(card.size, draw_reply_box))
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
        draw_marked(
            card,
            draw,
            (x + 22, y + 18 + REPLY_SIZE),
            reply["text"],
            REPLY_SIZE,
            False,
            REPLY_TEXT,
            emojis,
            reply.get("labels", []),
        )
        y += reply_h + 14
    if media is not None:
        big = Image.new("L", (media_size[0] * SS, media_size[1] * SS), 0)
        ImageDraw.Draw(big).rounded_rectangle((0, 0, *big.size), 18 * SS, fill=255)
        mask = big.resize(media_size, Image.Resampling.LANCZOS)
        layer = Image.new("RGBA", media_size, (0, 0, 0, 0))
        layer.paste(media, (0, 0), media if media.mode == "RGBA" else None)
        card.paste(
            layer, (int(x), int(y)), Image.composite(layer.getchannel("A"), mask, mask)
        )
        y += media_size[1] + 14
    for line in lines:
        draw_marked(
            card, draw, (x, y), line, TEXT_SIZE, False, TEXT, emojis, mentions
        )
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
    mentions: list[str] | None = None,
) -> bytes:
    """
    A PNG (transparent around the bubble) of a message quoted as a chat
    bubble. reply: {user, name, text, tag} of the message it answered, if
    any; tag: the sender's group tag. mentions: ["digits@lid", ...] tagged
    in text; reply may carry its own "mentions" list for the replied message.
    """
    text = strip_markup(text or "").strip()
    text, labels = await resolve_mentions(text, mentions or [])
    if reply:
        first = strip_markup(reply.get("text") or "").strip().split("\n")[0]
        first, reply_labels = await resolve_mentions(first, reply.get("mentions") or [])
        reply = {
            **reply,
            "text": first,
            "colour": name_colour(reply["user"]),
            "labels": reply_labels,
        }
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
        _draw_quote,
        name,
        name_colour(user),
        text,
        image,
        avatar,
        emojis,
        reply,
        tag,
        labels,
    )

"""
The message leaderboard as an image, drawn with the same helpers and look as
the .gi/.hsr/.zzz cards, with profile pictures and emoji in names.
"""

import asyncio
import colorsys

from PIL import Image, ImageDraw, ImageFilter

from .bot_utils import human_format_num, sync_to_async
from .card_text import draw_rich, load_emojis, profile_picture, rich_width
from .hoyo_cards import (
    FAINT,
    GOLD,
    SOFT,
    WHITE,
    ensure_fonts,
    font,
    gradient,
    panel,
    paste_fit,
    put,
    text_width,
    to_jpeg,
)

CW = 1100
HEADER_H = 200
ROW_H, ROW_GAP = 94, 10
# Rows drawn on the card; a longer -f ranking says how many more there are
MAX_ROWS = 30
BACKGROUND = (24, 26, 36)
ACCENT = (118, 96, 220)
# The card sent when a ranking resets: the period's final standings
FINAL_ACCENT = (196, 146, 40)
PLACE_COLOURS = {1: (214, 170, 60), 2: (168, 176, 190), 3: (196, 124, 72)}
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def fit_rich(text: str, size: int, bold: bool, width: float, emojis: dict) -> str:
    """text cut with "…" to fit width"""
    if rich_width(text, size, bold, emojis) <= width:
        return text
    while text and rich_width(text + "…", size, bold, emojis) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def avatar_fallback(name: str, colour: tuple) -> Image.Image:
    """A coloured circle with the name's first letter, for hidden pictures"""
    size = 120
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, size - 1, size - 1), fill=colour + (255,))
    letter = next((c for c in name if c.isalnum()), "?").upper()
    face = font(56, True, letter)
    draw.text(((size - face.getlength(letter)) / 2, 18), letter, font=face, fill=WHITE)
    return image


def picture_accent(picture: Image.Image | None) -> tuple:
    """
    The group picture's most common vivid colour, brightened or dimmed to sit
    behind white text; ACCENT if the picture is unset or mostly grey
    """
    if picture is None:
        return ACCENT
    small = picture.convert("RGB").resize((64, 64))
    palette = small.quantize(8).convert("RGB").getcolors(64 * 64) or []
    best, score = None, 0
    for count, colour in palette:
        _, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in colour))
        # Grey, near-black and near-white areas say little about the picture
        if s < 0.25 or v < 0.2:
            continue
        if count * s > score:
            best, score = colour, count * s
    if best is None:
        return ACCENT
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in best))
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.45), min(max(v, 0.6), 0.85))
    return int(r * 255), int(g * 255), int(b * 255)


def blurred_backdrop(picture: Image.Image, size: tuple) -> Image.Image:
    """The picture filling size, blurred and dimmed under the card's rows"""
    w, h = size
    # Blurred small and scaled up: as smooth, and cheap on tall cards
    sw, sh = max(w // 8, 1), max(h // 8, 1)
    image = picture.convert("RGB")
    scale = max(sw / image.width, sh / image.height)
    image = image.resize(
        (max(int(image.width * scale), sw), max(int(image.height * scale), sh)),
        Image.Resampling.LANCZOS,
    )
    left, top = (image.width - sw) // 2, (image.height - sh) // 2
    image = image.crop((left, top, left + sw, top + sh))
    image = image.filter(ImageFilter.GaussianBlur(6))
    image = image.resize((w, h), Image.Resampling.BICUBIC)
    return Image.blend(image, Image.new("RGB", (w, h), BACKGROUND), 0.72)


def put_medal(
    card, draw, emojis: dict, x: float, y: float, place: int, size: int, text_size: int
):
    """
    The place's medal emoji in a size square at x, y; a plain disc with the
    place on it if the emoji couldn't be fetched
    """
    image = emojis.get(MEDALS[place])
    if image is not None:
        paste_fit(card, image, (x, y, x + size, y + size))
        return
    colour = PLACE_COLOURS[place]
    draw.ellipse((x, y, x + size, y + size), fill=colour + (255,))
    face = font(text_size, True)
    draw.text(
        (x + size / 2, y + size / 2),
        str(place),
        font=face,
        anchor="mm",
        fill=(20, 20, 30, 255),
    )


def _medal(card, draw, emojis: dict, x: float, y: float, place: int, count: int):
    """A gold/silver/bronze medal with its count; returns the right edge"""
    # Kept clear of the share bar above
    put_medal(card, draw, emojis, x, y + 1, place, 22, 12)
    text = f"×{count}"
    face = font(20)
    draw.text((x + 26, y), text, font=face, fill=SOFT)
    return x + 26 + text_width(text, face) + 14


def _draw_ranking(
    data: dict, avatars: dict, emojis: dict, picture: Image.Image | None
) -> bytes:
    rows = data["rows"][:MAX_ROWS]
    more = len(data["rows"]) - len(rows)
    height = HEADER_H + len(rows) * (ROW_H + ROW_GAP) + (60 if more else 30)
    # The group picture, blurred, behind everything, and its colour as accent
    accent = picture_accent(picture)
    if picture is not None:
        card = blurred_backdrop(picture, (CW, height))
    else:
        card = Image.new("RGB", (CW, height), BACKGROUND)
    draw = ImageDraw.Draw(card, "RGBA")

    # Header: title, group and period, totals
    final = data.get("final")
    if picture is not None:
        # Faded to clear, so the backdrop shows through below and to the right
        put(
            card,
            gradient((CW, HEADER_H), accent + (190,), accent + (0,), horizontal=True),
        )
        put(card, gradient((CW, HEADER_H), (0, 0, 0, 0), BACKGROUND + (90,)))
    else:
        put(
            card,
            gradient(
                (CW, HEADER_H), accent + (255,), BACKGROUND + (255,), horizontal=True
            ),
        )
        put(card, gradient((CW, HEADER_H), (0, 0, 0, 0), BACKGROUND + (255,)))
    title = "Final Standings" if final else "Message Leaderboard"
    draw.text((40, 34), title, font=font(46, True), fill=WHITE)
    if final:
        label = final.upper()
        face = font(20, True)
        lw = text_width(label, face) + 32
        panel(
            card, (CW - 40 - lw, 44, CW - 40, 80), radius=18, fill=FINAL_ACCENT + (255,)
        )
        draw.text((CW - 40 - lw + 16, 49), label, font=face, fill=(20, 20, 30, 255))
    subtitle = " · ".join(p for p in (data.get("group"), data.get("period")) if p)
    if subtitle:
        subtitle = fit_rich(subtitle, 26, False, CW - 84, emojis)
        draw_rich(card, draw, (42, 98), subtitle, 26, False, SOFT, emojis)
    totals = (
        f"{human_format_num(data['total'])} messages  ·  "
        f"{human_format_num(data['members'])} members"
    )
    draw.text((42, 140), totals, font=font(24), fill=FAINT)

    top = max((r["count"] for r in rows), default=1) or 1
    # Names and bars run up to the widest count on the right
    count_w = max(
        [text_width(human_format_num(r["count"]), font(34, True)) for r in rows]
        + [text_width("messages", font(18))]
    )
    name_w = CW - 54 - count_w - 32 - 192
    for i, row in enumerate(rows):
        y = HEADER_H + i * (ROW_H + ROW_GAP)
        place = row["place"]
        colour = PLACE_COLOURS.get(place)
        panel(
            card,
            (30, y, CW - 30, y + ROW_H),
            radius=18,
            fill=(255, 255, 255, 24 if colour else 14),
        )

        # Place badge
        if colour:
            put_medal(card, draw, emojis, 44, y + 21, place, 52, 24)
        else:
            draw.ellipse((46, y + 23, 94, y + 71), fill=(60, 62, 76, 255))
            face = font(24 if place < 100 else 18, True)
            text = str(place)
            draw.text(
                (70 - text_width(text, face) / 2, y + 31), text, font=face, fill=WHITE
            )

        # Profile picture, ringed in the place colour for the top three
        avatar = avatars.get(row["user"]) or avatar_fallback(row["name"], accent)
        if colour:
            draw.ellipse((108, y + 13, 176, y + 81), fill=colour + (255,))
        paste_fit(card, avatar, (112, y + 17, 172, y + 77), circle=True)

        # Name, share bar and medals
        name = fit_rich(row["name"], 26, True, name_w, emojis)
        draw_rich(card, draw, (192, y + 9), name, 26, True, WHITE, emojis)
        if data.get("track", False):
            draw.rounded_rectangle(
                (192, y + 49, 192 + name_w, y + 59), 5, fill=(255, 255, 255, 30)
            )
        share = max(row["count"] / top, 0.02)
        draw.rounded_rectangle(
            (192, y + 49, 192 + name_w * share, y + 59),
            5,
            fill=(colour or accent) + (255,),
        )
        mx = 192
        for medal_place in (1, 2, 3):
            if row["medals"].get(medal_place):
                mx = _medal(
                    card,
                    draw,
                    emojis,
                    mx,
                    y + 65,
                    medal_place,
                    row["medals"][medal_place],
                )

        # Count, right-aligned
        count = human_format_num(row["count"])
        face = font(34, True)
        draw.text(
            (CW - 54 - text_width(count, face), y + 17),
            count,
            font=face,
            fill=GOLD if colour else WHITE,
        )
        unit = "message" if row["count"] == 1 else "messages"
        face = font(18)
        draw.text(
            (CW - 54 - text_width(unit, face), y + 59), unit, font=face, fill=FAINT
        )

    if more:
        text = f"+{more} more"
        face = font(22)
        draw.text(
            ((CW - text_width(text, face)) / 2, height - 46),
            text,
            font=face,
            fill=FAINT,
        )
    return to_jpeg(card)


async def ranking_card(data: dict) -> bytes:
    """
    The leaderboard card. data: {group, jid, period, total, members, rows:
    [{user, jid, place, name, count, medals: {1: n, 2: n, 3: n}}]}; jid is the
    group's, for its picture
    """
    rows = data["rows"][:MAX_ROWS]
    names = [data.get("group") or "", *(r["name"] for r in rows)]
    await ensure_fonts(*names)
    group_jid = data.get("jid")
    group_picture = profile_picture(group_jid) if group_jid else asyncio.sleep(0)
    pictures, emojis, picture = await asyncio.gather(
        asyncio.gather(*(profile_picture(r["jid"]) for r in rows)),
        load_emojis(*names, *MEDALS.values()),
        group_picture,
    )
    avatars = {r["user"]: p for r, p in zip(rows, pictures)}
    return await sync_to_async(_draw_ranking, data, avatars, emojis, picture)

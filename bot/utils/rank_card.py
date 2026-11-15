"""
The message leaderboard as an image, drawn with the same helpers and look as
the .gi/.hsr/.zzz cards, with profile pictures and emoji in names.
"""

import asyncio

from PIL import Image, ImageDraw

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
ROW_H, ROW_GAP = 84, 10
NAME_W = 540
# Rows drawn on the card; a longer -f ranking says how many more there are
MAX_ROWS = 30
BACKGROUND = (24, 26, 36)
ACCENT = (118, 96, 220)
# The card sent when a ranking resets: the period's final standings
FINAL_ACCENT = (196, 146, 40)
PLACE_COLOURS = {1: (214, 170, 60), 2: (168, 176, 190), 3: (196, 124, 72)}


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


def _medal(draw, x: float, y: float, place: int, count: int) -> float:
    """A gold/silver/bronze dot with its count; returns the right edge"""
    colour = PLACE_COLOURS[place]
    draw.ellipse((x, y + 3, x + 20, y + 23), fill=colour + (255,))
    face = font(13, True)
    draw.text(
        (x + 10 - text_width(str(place), face) / 2, y + 5),
        str(place),
        font=face,
        fill=(20, 20, 30, 255),
    )
    text = f"×{count}"
    face = font(20)
    draw.text((x + 26, y), text, font=face, fill=SOFT)
    return x + 26 + text_width(text, face) + 14


def _draw_ranking(data: dict, avatars: dict, emojis: dict) -> bytes:
    rows = data["rows"][:MAX_ROWS]
    more = len(data["rows"]) - len(rows)
    height = HEADER_H + len(rows) * (ROW_H + ROW_GAP) + (60 if more else 30)
    card = Image.new("RGB", (CW, height), BACKGROUND)
    draw = ImageDraw.Draw(card, "RGBA")

    # Header: title, group and period, totals
    final = data.get("final")
    accent = FINAL_ACCENT if final else ACCENT
    put(
        card,
        gradient((CW, HEADER_H), accent + (255,), BACKGROUND + (255,), horizontal=True),
    )
    put(card, gradient((CW, HEADER_H), (0, 0, 0, 0), BACKGROUND + (255,)))
    title = "Final Standings" if final else "Message Leaderboard"
    draw.text((40, 34), title, font=font(46, True), fill=WHITE)
    if final:
        label = final.upper()
        face = font(20, True)
        lw = text_width(label, face) + 32
        panel(card, (CW - 40 - lw, 44, CW - 40, 80), radius=18, fill=accent + (255,))
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
        draw.ellipse((46, y + 18, 94, y + 66), fill=(colour or (60, 62, 76)) + (255,))
        face = font(24 if place < 100 else 18, True)
        text = str(place)
        draw.text(
            (70 - text_width(text, face) / 2, y + 26),
            text,
            font=face,
            fill=(20, 20, 30, 255) if colour else WHITE,
        )

        # Profile picture, ringed in the place colour for the top three
        avatar = avatars.get(row["user"]) or avatar_fallback(row["name"], ACCENT)
        if colour:
            draw.ellipse((108, y + 8, 176, y + 76), fill=colour + (255,))
        paste_fit(card, avatar, (112, y + 12, 172, y + 72), circle=True)

        # Name, share bar and medals
        name = fit_rich(row["name"], 26, True, NAME_W, emojis)
        draw_rich(card, draw, (192, y + 8), name, 26, True, WHITE, emojis)
        draw.rounded_rectangle(
            (192, y + 48, 192 + NAME_W, y + 56), 4, fill=(255, 255, 255, 30)
        )
        share = max(row["count"] / top, 0.02)
        draw.rounded_rectangle(
            (192, y + 48, 192 + NAME_W * share, y + 56),
            4,
            fill=(colour or ACCENT) + (255,),
        )
        mx = 192
        for medal_place in (1, 2, 3):
            if row["medals"].get(medal_place):
                mx = _medal(draw, mx, y + 58, medal_place, row["medals"][medal_place])

        # Count, right-aligned
        count = human_format_num(row["count"])
        face = font(34, True)
        draw.text(
            (CW - 54 - text_width(count, face), y + 12),
            count,
            font=face,
            fill=GOLD if colour else WHITE,
        )
        unit = "message" if row["count"] == 1 else "messages"
        face = font(18)
        draw.text(
            (CW - 54 - text_width(unit, face), y + 54), unit, font=face, fill=FAINT
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
    The leaderboard card. data: {group, period, total, members, rows: [{user,
    jid, place, name, count, medals: {1: n, 2: n, 3: n}}]}
    """
    rows = data["rows"][:MAX_ROWS]
    names = [data.get("group") or "", *(r["name"] for r in rows)]
    await ensure_fonts(*names)
    pictures, emojis = await asyncio.gather(
        asyncio.gather(*(profile_picture(r["jid"]) for r in rows)),
        load_emojis(*names),
    )
    avatars = {r["user"]: p for r, p in zip(rows, pictures)}
    return await sync_to_async(_draw_ranking, data, avatars, emojis)

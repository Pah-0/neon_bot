"""
Pillow cards for .gi, .hsr and .zzz: a player summary and a character build
card, from Enka.Network showcases.

The build card follows the layout of the EnkaCard "template one" card (splash
art with stats, constellations and talents on the left, weapon and artifacts
on the right), drawn from scratch: frames are plain shapes, art and icons come
from Enka, and the font is Noto Sans. Each game's data is turned into a plain
description (a dict) that one renderer draws.
"""

import asyncio
import hashlib
import io
import json
import math
import os
import re
import time

import aiohttp
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .bot_utils import sync_to_async

CACHE_DIR = os.path.join(".cache", "hoyo")
FONT_URL = "https://raw.githubusercontent.com/google/fonts/main/ofl/"
FONTS = {
    "latin": "notosans/NotoSans%5Bwdth,wght%5D.ttf",
    # Only fetched when a nickname or signature needs them
    "sc": "notosanssc/NotoSansSC%5Bwght%5D.ttf",
    "kr": "notosanskr/NotoSansKR%5Bwght%5D.ttf",
}
ENKA_UI = "https://enka.network/ui/"
AMBER_UI = "https://gi.yatta.moe/assets/UI/"
TIMEOUT = aiohttp.ClientTimeout(total=60)

WHITE = (255, 255, 255, 255)
SOFT = (255, 255, 255, 170)
FAINT = (255, 255, 255, 110)
GOLD = (255, 211, 110, 255)
TEAL = (110, 225, 210, 255)
PANEL = (0, 0, 0, 115)
ELEMENT_COLOURS = {
    "Fire": (176, 70, 48),
    "Water": (44, 102, 176),
    "Wind": (40, 140, 118),
    "Electric": (118, 74, 172),
    "Grass": (82, 136, 44),
    "Ice": (72, 146, 178),
    "Rock": (172, 128, 44),
    # Star Rail and ZZZ
    "Physical": (110, 112, 124),
    "Thunder": (118, 74, 172),
    "Quantum": (82, 70, 160),
    "Imaginary": (176, 146, 44),
    "Ether": (178, 64, 120),
    "Physics": (110, 112, 124),
    "Elec": (118, 74, 172),
    "FireFrost": (72, 146, 178),
    "AuricEther": (178, 64, 120),
    "Lumen": (176, 146, 44),
}
DEFAULT_COLOUR = (90, 90, 110)
STYGIAN_DIFFICULTIES = {
    1: "Normal",
    2: "Advancing",
    3: "Hard",
    4: "Menacing",
    5: "Fearless",
    6: "Dire",
    # Dire's second medal; the game has six difficulties
    7: "Dire",
}

_font_lock = asyncio.Lock()
_font_paths = {}


async def _fetch(url: str) -> bytes | None:
    """url's bytes, kept on disk under CACHE_DIR"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest())
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    return data


async def fetch_image(url: str | None) -> Image.Image | None:
    if not url:
        return None
    data = await _fetch(url)
    if not data:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None


async def fetch_images(urls: dict) -> dict:
    keys = list(urls)
    images = await asyncio.gather(*(fetch_image(urls[k]) for k in keys))
    return dict(zip(keys, images))


def _script(text: str) -> str:
    """Which font a text needs: latin, sc (Chinese, Japanese) or kr"""
    if re.search(r"[ᄀ-ᇿ㄰-㆏가-힯]", text):
        return "kr"
    if re.search(r"[⺀-鿿豈-﫿＀-￯]", text):
        return "sc"
    return "latin"


async def ensure_fonts(*texts: str):
    """Downloads the fonts these texts need, once"""
    needed = {"latin"} | {_script(t) for t in texts if t}
    async with _font_lock:
        for name in needed - set(_font_paths):
            data = await _fetch(FONT_URL + FONTS[name])
            if not data:
                if name == "latin":
                    raise RuntimeError("Couldn't download the card font")
                continue
            _font_paths[name] = os.path.join(
                CACHE_DIR, hashlib.sha1((FONT_URL + FONTS[name]).encode()).hexdigest()
            )


_fonts = {}


def font(size: int, bold: bool = False, text: str = "") -> ImageFont.FreeTypeFont:
    script = _script(text)
    if script not in _font_paths:
        script = "latin"
    key = (script, size, bold)
    if key not in _fonts:
        face = ImageFont.truetype(_font_paths[script], size)
        try:
            axes = face.get_variation_axes()
            values = []
            for axis in axes:
                name = axis.get("name")
                name = name.decode() if isinstance(name, bytes) else str(name)
                if name.casefold() == "weight":
                    values.append(700 if bold else 400)
                else:
                    values.append(axis.get("default", axis.get("minimum")))
            face.set_variation_by_axes(values)
        except (OSError, AttributeError):
            pass
        _fonts[key] = face
    return _fonts[key]


def text_width(text: str, face) -> float:
    return face.getlength(text)


def fit_font(text: str, size: int, width: int, bold: bool = False):
    """The largest font up to size that fits text in width"""
    while size > 12:
        face = font(size, bold, text)
        if text_width(text, face) <= width:
            return face
        size -= 2
    return font(size, bold, text)


def put(card: Image.Image, image: Image.Image, pos: tuple = (0, 0)):
    """An RGBA image over the (RGB) card, blended by its alpha"""
    card.paste(image, (int(pos[0]), int(pos[1])), image)


def panel(card: Image.Image, box: tuple, radius: int = 18, fill=PANEL):
    ImageDraw.Draw(card, "RGBA").rounded_rectangle(box, radius, fill=fill)


def paste_fit(
    card: Image.Image, image: Image.Image | None, box: tuple, circle: bool = False
):
    """image scaled to fit box, centred; optionally cut to a circle"""
    if image is None:
        return
    x0, y0, x1, y1 = box
    image = image.copy()
    image.thumbnail((x1 - x0, y1 - y0), Image.Resampling.LANCZOS)
    x = x0 + (x1 - x0 - image.width) // 2
    y = y0 + (y1 - y0 - image.height) // 2
    if circle:
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).ellipse(
            (0, 0, image.width - 1, image.height - 1), fill=255
        )
        image.putalpha(Image.composite(image.getchannel("A"), mask, mask))
    put(card, image, (x, y))


def star(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill=GOLD):
    """A five-pointed star centred on x, y"""
    points = []
    for i in range(10):
        angle = math.pi / 2 + i * math.pi / 5
        radius = r if i % 2 == 0 else r * 0.45
        points.append((x + radius * math.cos(angle), y - radius * math.sin(angle)))
    draw.polygon(points, fill=fill)


def heart(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill=WHITE):
    """A heart centred on x, y"""
    draw.ellipse((x - r, y - r * 0.9, x, y + r * 0.1), fill=fill)
    draw.ellipse((x, y - r * 0.9, x + r, y + r * 0.1), fill=fill)
    draw.polygon(
        [(x - r * 0.97, y - 0.2 * r), (x + r * 0.97, y - 0.2 * r), (x, y + r)],
        fill=fill,
    )


def stars(draw, x: float, y: float, count: int, r: float = 10, fill=GOLD):
    for i in range(count):
        star(draw, x + r + i * r * 2.2, y + r, r, fill)


def gradient(
    size: tuple, start: tuple, end: tuple, horizontal: bool = False
) -> Image.Image:
    """An RGBA gradient from start to end colour"""
    w, h = size
    steps = w if horizontal else h
    line = Image.new("RGBA", (steps, 1) if horizontal else (1, steps))
    for i in range(steps):
        t = i / max(steps - 1, 1)
        colour = tuple(int(a + (b - a) * t) for a, b in zip(start, end))
        line.putpixel((i, 0) if horizontal else (0, i), colour)
    return line.resize(size)


def to_jpeg(card: Image.Image) -> bytes:
    output = io.BytesIO()
    card.convert("RGB").save(output, format="jpeg", quality=92)
    return output.getvalue()


def clear_time(seconds: int | None) -> str:
    if not seconds:
        return ""
    return f"{seconds // 60}:{seconds % 60:02d}"


def crit_value(stats) -> float:
    """
    CRIT Rate ×2 + CRIT DMG from a piece's main stat and substats (a crit
    circlet or body counts, as in the EnkaCard fork)
    """
    total = 0.0
    for stat in stats:
        name = getattr(stat, "name", "")
        if name not in ("CRIT Rate", "CRIT DMG"):
            continue
        try:
            value = float(str(stat.formatted_value).rstrip("%").replace(",", ""))
        except ValueError:
            continue
        total += value * 2 if name == "CRIT Rate" else value
    return round(total, 1)


# ---------------------------------------------------------------- build card
#
# A build is described as a dict, with art referred to by key:
#   colour, splash, name, line, heart (int or None), stars or rank, cv,
#   stats: [(name, value)], column: [{icon, label, on}],
#   skills: [{icon, label, level, boosted}],
#   weapon: {icon, name, stars or rank, line, stats: [str]},
#   pieces: [{icon, level, stars or rank, main_name, main_value,
#             subs: [(text, highlight)], cv} or None], sets, owner

W, H = 1500, 1180
SPLASH_W = 950
RIGHT_X = 966
OUTLINE = {"stroke_width": 2, "stroke_fill": (0, 0, 0, 120)}
RARITY_FILL = {5: (176, 112, 48), 4: (127, 103, 161), 3: (84, 134, 169)}
RANK_RARITY = {"S": 5, "A": 4, "B": 3}


def _rarity_fill(item: dict) -> tuple:
    rarity = item.get("stars") or RANK_RARITY.get(item.get("rank"), 0)
    return RARITY_FILL.get(rarity, (110, 110, 110))


def _rarity_mark(draw, item: dict, x: float, y: float, r: float):
    """Stars, or a ZZZ rank letter"""
    if item.get("stars"):
        stars(draw, x, y, item["stars"], r=r)
    elif item.get("rank"):
        face = font(int(r * 2.6), True)
        colour = {"S": GOLD, "A": (200, 160, 255, 255)}.get(item["rank"], SOFT)
        draw.text((x, y - r * 0.6), item["rank"], font=face, fill=colour, **OUTLINE)


def _background(card, art, colour):
    put(card, gradient((W, H), (255, 255, 255, 40), (0, 0, 0, 90), horizontal=True))
    splash = art.get("splash")
    if splash is not None:
        splash = splash.copy()
        scale = (H - 40) / splash.height
        splash = splash.resize(
            (int(splash.width * scale), H - 40), Image.Resampling.LANCZOS
        )
        if splash.width > SPLASH_W + 300:
            left = (splash.width - SPLASH_W - 300) // 2
            splash = splash.crop((left, 0, left + SPLASH_W + 300, splash.height))
        shadow = Image.new("RGBA", splash.size, (0, 0, 0, 0))
        shadow.putalpha(splash.getchannel("A").point(lambda a: a * 0.45))
        shadow = shadow.filter(ImageFilter.GaussianBlur(18))
        x = (SPLASH_W - splash.width) // 2 + 60
        put(card, shadow, (x + 14, 34))
        put(card, splash, (x, 20))
    put(card, gradient((W, 260), (0, 0, 0, 0), (0, 0, 0, 170)), (0, H - 260))
    put(
        card,
        gradient((W - SPLASH_W + 80, H), (0, 0, 0, 0), (0, 0, 0, 120), horizontal=True),
        (SPLASH_W - 80, 0),
    )


def _circle_item(card, draw, box, item, colour, art):
    """A round icon (or label) with an element-coloured ring"""
    on = item.get("on", True)
    ring = colour + (255,) if on else (255, 255, 255, 60)
    draw.ellipse(box, fill=(0, 0, 0, 140), outline=ring, width=4)
    x0, y0, x1, y1 = box
    pad = 4 if item.get("fill") else (x1 - x0) * 0.18
    icon = art.get(item.get("icon"))
    if icon is not None:
        if not on:
            icon = icon.copy()
            icon.putalpha(icon.getchannel("A").point(lambda a: a * 0.4))
        paste_fit(
            card,
            icon,
            (int(x0 + pad), int(y0 + pad), int(x1 - pad), int(y1 - pad)),
            circle=bool(item.get("fill")),
        )
    elif item.get("label"):
        label = item["label"]
        face = fit_font(label, int((y1 - y0) * 0.3), int(x1 - x0 - pad), True)
        tw = text_width(label, face)
        fill = WHITE if on else FAINT
        draw.text(
            ((x0 + x1 - tw) / 2, (y0 + y1) / 2 - face.size * 0.62),
            label,
            font=face,
            fill=fill,
        )
    if item.get("badge"):
        badge = item["badge"]
        face = font(20, True)
        bw = text_width(badge, face) + 14
        bx, by = x1 - bw + 4, y1 - 26
        panel(card, (bx, by, bx + bw, by + 28), radius=14, fill=(20, 20, 30, 230))
        draw.text((bx + 7, by + 1), badge, font=face, fill=WHITE if on else FAINT)


PILL_FONT = 18


def _pill(card, draw, x: float, y: float, text: str, fill, colour=WHITE) -> float:
    """A rounded label; returns its right edge"""
    face = font(PILL_FONT, True)
    w = text_width(text, face) + 22
    panel(card, (x, y, x + w, y + 30), radius=15, fill=fill)
    draw.text((x + 11, y + 4), text, font=face, fill=colour)
    return x + w


# The rank sits right-aligned over the art, above the character's name
RANK_RIGHT, RANK_BOTTOM, RANK_MAX_W = 930, 398, 400
TILE, TILE_GAP = 48, 8
AKASHA_ELEMENTS = {
    "Pyro": "Fire",
    "Hydro": "Water",
    "Anemo": "Wind",
    "Electro": "Electric",
    "Dendro": "Grass",
    "Cryo": "Ice",
    "Geo": "Rock",
}


def _top_share(board: dict) -> str:
    top = board["rank"] / board["out"] * 100
    if top < 0.01:
        return "<0.01"
    if top < 1:
        return f"{top:.2f}"
    if top < 10:
        return f"{top:.1f}"
    return f"{top:.0f}"


def _draw_rank(card, draw, board: dict, art: dict, colour: tuple):
    """
    The Akasha / Inter-Knot rank, laid out like Akasha's: the board's team, a
    top-% pill and the board's short name, then the rank
    """
    team = board.get("team") or []
    face = font(PILL_FONT, True)
    top_text = f"TOP {_top_share(board)}%"
    label = " ".join(p for p in (board["name"], board.get("variant")) if p).upper()
    room = RANK_MAX_W - 24 - 22
    top_w = text_width(top_text, face) + 22
    label_w = text_width(label, face) + 22 if label else 0
    pills_w = top_w + (6 + label_w if label else 0)
    wrap = pills_w > RANK_MAX_W - 24
    # A long board name wraps onto more pill lines at word breaks, never cut
    lines = []
    for word in label.split():
        if lines and text_width(f"{lines[-1]} {word}", face) <= room:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    rank, out = f"{board['rank']:,}", f"/{board['out']:,}"
    rface, oface = font(28, True), font(22)
    rank_w = text_width(rank, rface) + text_width(out, oface)
    team_w = TILE * 4 + TILE_GAP * 3 if team else 0
    line_ws = [text_width(line, face) + 22 for line in lines]
    inner = max(team_w, max([top_w, *line_ws]) if wrap else pills_w, rank_w)
    width = min(inner, RANK_MAX_W - 24) + 24
    height = 10 + (TILE + 12 if team else 0) + 30 + (36 * len(lines) if wrap else 0)
    height += 8 + 34 + 6
    x0, y0 = RANK_RIGHT - width, RANK_BOTTOM - height
    centre = x0 + width / 2
    panel(card, (x0, y0, RANK_RIGHT, RANK_BOTTOM), radius=16)

    cy = y0 + 10
    if team:
        tx = centre - team_w / 2
        for i, mate in enumerate(team[:4]):
            bx = tx + i * (TILE + TILE_GAP)
            box = (bx, cy, bx + TILE, cy + TILE)
            # Each tile in its own character's (or flex slot's) element
            element = AKASHA_ELEMENTS.get(mate.get("element") or "", "")
            tile = ELEMENT_COLOURS.get(element, colour)
            draw.rounded_rectangle(box, 10, fill=tile + (220,))
            if mate.get("icon"):
                paste_fit(card, art.get(f"team{i}"), tuple(map(int, box)))
                paste_fit(
                    card,
                    art.get(f"team_extra{i}"),
                    (
                        int(bx - 4),
                        int(cy + TILE - 20),
                        int(bx + 20),
                        int(cy + TILE + 4),
                    ),
                )
            else:
                # A flex slot: any character of this element
                pad = TILE * 0.2
                paste_fit(
                    card,
                    art.get(f"team{i}"),
                    (
                        int(bx + pad),
                        int(cy + pad),
                        int(bx + TILE - pad),
                        int(cy + TILE - pad),
                    ),
                )
            cons = mate.get("constellation")
            if cons:
                draw.text(
                    (bx + TILE - 11, cy + 1),
                    str(cons),
                    font=font(15, True),
                    fill=WHITE,
                    **OUTLINE,
                )
        cy += TILE + 12

    light = tuple(min(255, c + 70) for c in colour) + (230,)
    dark = (20, 20, 30, 255)
    if wrap:
        _pill(card, draw, centre - top_w / 2, cy, top_text, light, dark)
        for line, line_w in zip(lines, line_ws):
            cy += 36
            _pill(card, draw, centre - line_w / 2, cy, line, light, dark)
    else:
        x = _pill(card, draw, centre - pills_w / 2, cy, top_text, light, dark)
        if label:
            _pill(card, draw, x + 6, cy, label, light, dark)
    cy += 38

    rx = centre - rank_w / 2
    draw.text((rx, cy), rank, font=rface, fill=WHITE)
    draw.text((rx + text_width(rank, rface), cy + 6), out, font=oface, fill=FAINT)


def _draw_build(spec: dict, art: dict) -> bytes:
    colour = spec["colour"]
    card = Image.new("RGB", (W, H), colour)
    draw = ImageDraw.Draw(card, "RGBA")
    _background(card, art, colour)

    # Stats, top left
    rows = spec["stats"][:10]
    stats_bottom = 32 + 46 * len(rows)
    panel(card, (16, 16, 436, stats_bottom))
    bold = font(24, True)
    for i, (name, value) in enumerate(rows):
        y = 28 + 46 * i
        draw.text((36, y), name, font=fit_font(name, 24, 250), fill=SOFT)
        draw.text((416 - text_width(value, bold), y), value, font=bold, fill=WHITE)

    # Constellations / eidolons / mindscapes down the left edge
    for i, item in enumerate(spec["column"][:6]):
        y = 520 + i * 104
        _circle_item(card, draw, (24, y, 112, y + 88), item, colour, art)

    if spec.get("leaderboard"):
        _draw_rank(card, draw, spec["leaderboard"], art, colour)

    # Name, level line and rarity, right-aligned over the art
    name = spec["name"]
    face = fit_font(name, 58, 460, True)
    draw.text(
        (930 - text_width(name, face), 410),
        name,
        font=face,
        fill=WHITE,
        stroke_width=3,
        stroke_fill=(0, 0, 0, 120),
    )
    face = font(28)
    x = 930
    if spec.get("heart") is not None:
        friendship = str(spec["heart"])
        x -= text_width(friendship, face)
        draw.text((x, 486), friendship, font=face, fill=WHITE, **OUTLINE)
        heart(draw, x - 20, 504, 11, fill=(255, 140, 160, 255))
        x -= 36
    line = spec["line"]
    draw.text((x - text_width(line, face), 486), line, font=face, fill=WHITE, **OUTLINE)
    if spec.get("stars"):
        stars(draw, 930 - spec["stars"] * 26, 530, spec["stars"], r=11)
    else:
        _rarity_mark(draw, spec, 900, 536, 14)

    # Crit value badge
    cv = spec["cv"]
    badge = f"{cv:.1f} CV"
    face = font(26, True)
    bw = text_width(badge, face) + 36
    panel(card, (930 - bw, 572, 930, 616), radius=22, fill=(0, 0, 0, 150))
    draw.text((930 - bw + 18, 578), badge, font=face, fill=GOLD if cv >= 200 else WHITE)

    # Talents / traces / skills by the right panel, in two columns past four
    skills = spec["skills"]
    cols = 1 if len(skills) <= 4 else 2
    per_col = -(-len(skills) // cols)
    spacing = min(150, 500 // max(per_col, 1))
    size = min(100, spacing - 40)
    for i, item in enumerate(skills):
        col, row = i // per_col, i % per_col
        cx = 862 - (cols - 1 - col) * (size + 34)
        y = 650 + row * spacing
        box = (cx - size / 2, y, cx + size / 2, y + size)
        _circle_item(card, draw, box, item, colour, art)
        level = str(item["level"])
        face = font(24, True)
        lw = text_width(level, face) + 22
        panel(
            card,
            (cx - lw / 2, y + size - 14, cx + lw / 2, y + size + 20),
            radius=17,
            fill=(20, 20, 30, 230),
        )
        draw.text(
            (cx - lw / 2 + 11, y + size - 13),
            level,
            font=face,
            fill=TEAL if item.get("boosted") else WHITE,
        )

    if spec.get("weapon"):
        _draw_weapon(card, draw, spec["weapon"], art)
    _draw_pieces(card, draw, spec["pieces"], art)

    # Set bonuses
    y = 1094
    panel(card, (RIGHT_X, y, W - 16, H - 16))
    text = spec["sets"] or "No set bonus"
    face = fit_font(text, 22, W - 16 - RIGHT_X - 32, True)
    draw.text((RIGHT_X + 16, y + 22), text, font=face, fill=WHITE)

    # Owner, bottom left
    owner = spec["owner"]
    draw.text((140, H - 50), owner, font=font(24, text=owner), fill=SOFT, **OUTLINE)
    return to_jpeg(card)


def _draw_weapon(card, draw, weapon: dict, art):
    x0, y0, x1, y1 = RIGHT_X, 16, W - 16, 216
    panel(card, (x0, y0, x1, y1))
    panel(
        card,
        (x0 + 14, y0 + 14, x0 + 186, y1 - 14),
        radius=14,
        fill=_rarity_fill(weapon) + (200,),
    )
    paste_fit(card, art.get(weapon["icon"]), (x0 + 22, y0 + 18, x0 + 178, y1 - 18))
    tx = x0 + 204
    face = fit_font(weapon["name"], 30, x1 - tx - 16, True)
    draw.text((tx, y0 + 16), weapon["name"], font=face, fill=WHITE)
    _rarity_mark(draw, weapon, tx, y0 + 60, 10)
    draw.text((tx, y0 + 88), weapon["line"], font=font(24), fill=SOFT)
    for i, text in enumerate(weapon["stats"][:2]):
        draw.text(
            (tx, y0 + 124 + i * 34),
            text,
            font=fit_font(text, 24, x1 - tx - 16, True),
            fill=WHITE,
        )


def _draw_pieces(card, draw, pieces: list, art):
    """Five artifacts, or six relics or discs, down the right"""
    count = len(pieces)
    gap = 12
    h = (1082 - 230 - gap * (count - 1)) // count
    icon = min(130, h - 30)
    for i, piece in enumerate(pieces):
        x0, y0 = RIGHT_X, 230 + i * (h + gap)
        x1, y1 = W - 16, y0 + h
        panel(card, (x0, y0, x1, y1))
        if piece is None:
            continue
        paste_fit(
            card, art.get(piece["icon"]), (x0 + 8, y0 + 4, x0 + 8 + icon, y0 + 4 + icon)
        )
        level = piece["level"]
        face = font(20, True)
        lw = text_width(level, face) + 18
        panel(
            card,
            (x0 + 14, y1 - 38, x0 + 14 + lw, y1 - 10),
            radius=12,
            fill=(20, 20, 30, 230),
        )
        draw.text((x0 + 23, y1 - 37), level, font=face, fill=WHITE)
        _rarity_mark(draw, piece, x0 + 20 + lw, y1 - 33, 5.5)

        tx = x0 + 148
        draw.text(
            (tx, y0 + 10),
            piece["main_name"],
            font=fit_font(piece["main_name"], 22, 230),
            fill=SOFT,
        )
        draw.text(
            (tx, y0 + 34),
            piece["main_value"],
            font=font(34 if h > 140 else 30, True),
            fill=WHITE,
        )
        if piece["cv"]:
            text = f"{piece['cv']:.1f} CV"
            face = font(20, True)
            draw.text(
                (x1 - 16 - text_width(text, face), y0 + 12),
                text,
                font=face,
                fill=GOLD if piece["cv"] >= 40 else SOFT,
            )
        row_h = 30 if h > 140 else 27
        for j, (text, highlight) in enumerate(piece["subs"][:4]):
            sx = tx + (j % 2) * 186
            sy = y1 - 12 - row_h * (2 - j // 2)
            draw.text(
                (sx, sy),
                text,
                font=fit_font(text, 22, 180),
                fill=GOLD if highlight else WHITE,
            )


def _count_sets(names: list[str], sizes=(4, 2)) -> str:
    """'4× Set A   2× Set B' from the set name of each piece"""
    counts = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    parts = []
    for name, count in sorted(counts.items(), key=lambda c: -c[1]):
        for size in sizes:
            if count >= size:
                parts.append(f"{size}× {name}")
                break
    return "   ".join(parts)


def _short_stat(name: str) -> str:
    return (
        name.replace("Elemental Mastery", "EM")
        .replace("Energy Recharge", "ER")
        .replace("Effect Hit Rate", "EHR")
        .replace("Effect RES", "RES")
        .replace("Break Effect", "BE")
        .replace("Anomaly Proficiency", "AP")
        .replace("Percent ", "")
    )


def _subs(stats, rolls: list[int] | None = None) -> list[tuple[str, bool]]:
    """Substat lines, each with "+N" for the upgrades that went into it"""
    subs = []
    for i, stat in enumerate(stats):
        text = f"{_short_stat(stat.name)} {stat.formatted_value}"
        if rolls and i < len(rolls) and rolls[i] > 1:
            text += f" +{rolls[i] - 1}"
        subs.append((text, stat.name in ("CRIT Rate", "CRIT DMG")))
    return subs


def _gi_rolls(artifact) -> list[int]:
    """
    How many times each Genshin substat rolled, in substat order: each roll
    id is a substat's id ×10 plus its roll tier, listed in the order the
    substats were added
    """
    counts = {}
    for roll in artifact.sub_stat_ids or []:
        counts[roll // 10] = counts.get(roll // 10, 0) + 1
    return list(counts.values())


def _element(value) -> str:
    return str(getattr(value, "value", value))


# Genshin

GI_PIECE_ORDER = (
    "EQUIP_BRACER",
    "EQUIP_NECKLACE",
    "EQUIP_SHOES",
    "EQUIP_RING",
    "EQUIP_DRESS",
)


def _gi_stat_rows(character) -> list[tuple[str, str]]:
    stats = {str(getattr(k, "name", k)): v for k, v in character.stats.items()}

    def get(key):
        stat = stats.get(key)
        return stat.formatted_value if stat is not None else "0"

    rows = [
        ("Max HP", get("FIGHT_PROP_MAX_HP")),
        ("ATK", get("FIGHT_PROP_CUR_ATTACK")),
        ("DEF", get("FIGHT_PROP_CUR_DEFENSE")),
        ("Elemental Mastery", get("FIGHT_PROP_ELEMENT_MASTERY")),
        ("CRIT Rate", get("FIGHT_PROP_CRITICAL")),
        ("CRIT DMG", get("FIGHT_PROP_CRITICAL_HURT")),
        ("Energy Recharge", get("FIGHT_PROP_CHARGE_EFFICIENCY")),
    ]
    dmg = getattr(character, "highest_dmg_bonus_stat", None)
    if dmg is not None and dmg.value:
        rows.append((dmg.name, dmg.formatted_value))
    heal = stats.get("FIGHT_PROP_HEAL_ADD")
    if heal is not None and heal.value:
        rows.append((heal.name, heal.formatted_value))
    return rows


def gi_build(character, owner: str) -> tuple[dict, dict]:
    """(art URLs, build description) for a Genshin character"""
    costume = getattr(character, "costume", None)
    splash = getattr(costume.icon, "gacha", None) if costume is not None else None
    urls = {
        "splash": splash or character.icon.gacha,
        "splash_fallback": character.icon.front,
        "weapon": character.weapon.icon,
    }
    by_slot = {_element(a.equip_type): a for a in character.artifacts}
    pieces = []
    for slot in GI_PIECE_ORDER:
        artifact = by_slot.get(slot)
        if artifact is None:
            pieces.append(None)
            continue
        urls[f"piece_{slot}"] = artifact.icon
        stats = [artifact.main_stat, *artifact.sub_stats]
        pieces.append(
            {
                "icon": f"piece_{slot}",
                "level": f"+{artifact.level}",
                "stars": artifact.rarity,
                "main_name": artifact.main_stat.name,
                "main_value": artifact.main_stat.formatted_value,
                "subs": _subs(artifact.sub_stats, _gi_rolls(artifact)),
                "cv": crit_value(stats),
            }
        )
    column = []
    for i, const in enumerate(character.constellations[:6]):
        urls[f"const{i}"] = const.icon
        column.append({"icon": f"const{i}", "on": const.unlocked})
    skills = []
    for i, talent in enumerate(character.talents[:3]):
        urls[f"talent{i}"] = talent.icon
        skills.append(
            {"icon": f"talent{i}", "level": talent.level, "boosted": talent.is_upgraded}
        )
    cons = sum(1 for c in character.constellations if c.unlocked)
    weapon = character.weapon
    spec = {
        "colour": ELEMENT_COLOURS.get(_element(character.element), DEFAULT_COLOUR),
        "name": character.name,
        "line": f"Lv. {character.level}/{max(character.level, character.max_level)}  ·  C{cons}  ·  ",
        "heart": character.friendship_level,
        "stars": character.rarity,
        "cv": sum(p["cv"] for p in pieces if p),
        "stats": _gi_stat_rows(character),
        "column": column,
        "skills": skills,
        "weapon": {
            "icon": "weapon",
            "name": weapon.name,
            "stars": weapon.rarity,
            "line": f"Lv. {weapon.level}  ·  R{weapon.refinement}",
            "stats": [f"{s.name}  {s.formatted_value}" for s in weapon.stats],
        },
        "pieces": pieces,
        "sets": _count_sets([a.set_name for a in character.artifacts]),
        "owner": owner,
    }
    return urls, spec


# Honkai: Star Rail

HSR_YATTA = "https://sr.yatta.moe/hsr/assets/UI/avatar/large/"


def hsr_build(character, owner: str) -> tuple[dict, dict]:
    urls = {
        # Yatta's art is the full figure; Enka's is a framed card
        "splash": f"{HSR_YATTA}{character.id}.png",
        "splash_fallback": character.icon.gacha,
    }
    stats = [
        (s.name.replace("Energy Regeneration Rate", "Energy Regen"), s.formatted_value)
        for s in (
            character.stats.values()
            if isinstance(character.stats, dict)
            else character.stats
        )
        if s.value
    ]
    pieces = []
    by_type = {int(getattr(r.type, "value", r.type)): r for r in character.relics}
    for slot in range(1, 7):
        relic = by_type.get(slot)
        if relic is None:
            pieces.append(None)
            continue
        urls[f"piece{slot}"] = relic.icon
        pieces.append(
            {
                "icon": f"piece{slot}",
                "level": f"+{relic.level}",
                "stars": relic.rarity,
                "main_name": relic.main_stat.name,
                "main_value": relic.main_stat.formatted_value,
                "subs": _subs(
                    relic.sub_stats, [a.cnt for a in relic.sub_affix_list or []]
                ),
                "cv": crit_value([relic.main_stat, *relic.sub_stats]),
            }
        )
    column = []
    for i, eidolon in enumerate(character.eidolons[:6]):
        urls[f"eidolon{i}"] = eidolon.icon
        column.append({"icon": f"eidolon{i}", "on": eidolon.unlocked})
    skills = []
    for i, trace in enumerate(t for t in character.traces if t.max_level > 1):
        if i >= 6:
            break
        urls[f"trace{i}"] = trace.icon
        skills.append(
            {"icon": f"trace{i}", "level": trace.level, "boosted": trace.boosted}
        )
    weapon = None
    cone = character.light_cone
    if cone is not None:
        urls["weapon"] = getattr(cone.icon, "image", None) or getattr(
            cone.icon, "item", None
        )
        weapon = {
            "icon": "weapon",
            "name": cone.name,
            "stars": cone.rarity,
            "line": f"Lv. {cone.level}  ·  S{cone.superimpose}",
            "stats": [
                "   ".join(
                    f"{_short_stat(s.name).replace('Base ', '')} {s.formatted_value}"
                    for s in cone.stats
                )
            ],
        }
    planar = [
        r.set_name
        for r in character.relics
        if int(getattr(r.type, "value", r.type)) > 4
    ]
    cavern = [
        r.set_name
        for r in character.relics
        if int(getattr(r.type, "value", r.type)) <= 4
    ]
    sets = "   ".join(
        x for x in (_count_sets(cavern), _count_sets(planar, sizes=(2,))) if x
    )
    spec = {
        "colour": ELEMENT_COLOURS.get(_element(character.element), DEFAULT_COLOUR),
        "name": character.name,
        "line": f"Lv. {character.level}/{max(character.level, character.max_level)}  ·  E{character.eidolons_unlocked}",
        "heart": None,
        "stars": character.rarity,
        "cv": sum(p["cv"] for p in pieces if p),
        "stats": stats,
        "column": column,
        "skills": skills,
        "weapon": weapon,
        "pieces": pieces,
        "sets": sets,
        "owner": owner,
    }
    return urls, spec


# Zenless Zone Zero

ZZZ_ASSETS = "https://static.nanoka.cc/assets/zzz/"
ZZZ_DATA = "https://static.nanoka.cc/"
ZZZ_SKILLS = {0: "Basic", 1: "Special", 2: "Dodge", 3: "Chain", 5: "Core", 6: "Assist"}
# The game's skill-type icons, from Hakushin (Enka has none)
ZZZ_SKILL_ICONS = {
    0: "Icon_Normal",
    1: "Icon_SpecialReady",
    2: "Icon_Evade",
    3: "Icon_UltimateReady",
    5: "Icon_CoreSkill",
    6: "Icon_Switch",
}
_disc_icons = {"time": 0.0, "icons": {}}


async def zzz_disc_icons() -> dict:
    """Drive disc set id -> icon URL, from Hakushin (Enka has none)"""
    if _disc_icons["icons"] and time.time() - _disc_icons["time"] < 6 * 3600:
        return _disc_icons["icons"]
    try:
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            async with session.get(ZZZ_DATA + "manifest.json") as resp:
                version = (await resp.json(content_type=None))["zzz"]["live"]
            async with session.get(f"{ZZZ_DATA}zzz/{version}/equipment.json") as resp:
                discs = await resp.json(content_type=None)
    except Exception:
        return _disc_icons["icons"]
    icons = {}
    for set_id, disc in discs.items():
        name = disc.get("icon", "").rsplit("/", 1)[-1].split(".", 1)[0]
        if name:
            icons[str(set_id)] = f"{ZZZ_ASSETS}{name}.webp"
    _disc_icons.update(time=time.time(), icons=icons)
    return icons


def zzz_build(agent, owner: str, disc_icons: dict) -> tuple[dict, dict]:
    urls = {"splash": agent.icon.image, "splash_fallback": agent.icon.round}
    stats = [
        (s.name, s.formatted_value)
        for s in (
            agent.stats.values() if isinstance(agent.stats, dict) else agent.stats
        )
        if s.value
    ]
    pieces = []
    by_slot = {d.slot: d for d in agent.discs}
    for slot in range(1, 7):
        disc = by_slot.get(slot)
        if disc is None:
            pieces.append(None)
            continue
        urls[f"piece{slot}"] = disc_icons.get(str(disc.set_id))
        pieces.append(
            {
                "icon": f"piece{slot}",
                "level": f"+{disc.level}",
                "rank": disc.rarity,
                "main_name": disc.main_stat.name,
                "main_value": disc.main_stat.formatted_value,
                "subs": _subs(
                    disc.sub_stats, [s.roll_times or 1 for s in disc.sub_stats]
                ),
                "cv": crit_value([disc.main_stat, *disc.sub_stats]),
            }
        )
    urls["mindscape"] = agent.icon.round
    column = [
        {
            "icon": "mindscape",
            "label": f"M{i}",
            "badge": str(i),
            "fill": True,
            "on": i <= agent.mindscape,
        }
        for i in range(1, 7)
    ]
    skills = []
    for skill in sorted(
        agent.skills,
        key=lambda s: (
            list(ZZZ_SKILLS).index(int(s.type)) if int(s.type) in ZZZ_SKILLS else 9
        ),
    ):
        kind = int(getattr(skill.type, "value", skill.type))
        level = agent.core_skill_level if kind == 5 else skill.level
        icon = ZZZ_SKILL_ICONS.get(kind)
        if icon:
            urls[f"skill{kind}"] = f"{ZZZ_ASSETS}{icon}.webp"
        # The label stays as a fallback if the icon doesn't load
        skills.append(
            {"icon": f"skill{kind}", "label": ZZZ_SKILLS.get(kind, "?"), "level": level}
        )
    weapon = None
    engine = agent.w_engine
    if engine is not None:
        urls["weapon"] = engine.icon
        weapon = {
            "icon": "weapon",
            "name": engine.name,
            "rank": engine.rarity,
            "line": f"Lv. {engine.level}  ·  P{engine.phase}",
            "stats": [
                f"{engine.main_stat.name}  {engine.main_stat.formatted_value}",
                f"{_short_stat(engine.sub_stat.name)}  {engine.sub_stat.formatted_value}",
            ],
        }
    elements = [_element(e) for e in agent.elements] or ["Physical"]
    spec = {
        "colour": ELEMENT_COLOURS.get(elements[0], DEFAULT_COLOUR),
        "name": agent.name,
        "line": f"Lv. {agent.level}  ·  M{agent.mindscape}  ·  {agent.specialty}",
        "heart": None,
        "rank": agent.rarity,
        "cv": sum(p["cv"] for p in pieces if p),
        "stats": stats,
        "column": column,
        "skills": skills,
        "weapon": weapon,
        "pieces": pieces,
        "sets": _count_sets([d.set_name for d in agent.discs]),
        "owner": owner,
    }
    return urls, spec


# Akasha's Cloudflare turns away curl's default User-Agent; a plain bot name
# from the bot's server gets through
BOT_UA = "neon_bot/1.0"
RANK_TTL = 5 * 60
AKASHA_API = "https://akasha.cv/api/"
INTERKNOT_API = "https://data.interknot.space/"
_akasha = {}
_interknot = {}


def _parse_rank(value) -> int:
    """Akasha writes estimated ranks as "~123" """
    try:
        return int(str(value).lstrip("~").replace(",", ""))
    except ValueError:
        return 0


async def akasha_ranks(uid) -> dict:
    """
    A Genshin player's Akasha (akasha.cv) damage leaderboard ranks: character
    id -> {name, variant, rank, out, calculation} for each character's
    best-fitting board. Empty when the player isn't on Akasha or it can't be
    reached.
    """
    cached = _akasha.get(str(uid))
    if cached and time.time() - cached[0] < RANK_TTL:
        return cached[1]
    ranks = {}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30), headers={"User-Agent": BOT_UA}
        ) as session:
            # Has Akasha load the player's current showcase; it rate limits
            # this itself, so a failure here still leaves the older ranks
            try:
                async with session.get(f"{AKASHA_API}user/refresh/{uid}") as resp:
                    await resp.read()
            except aiohttp.ClientError:
                pass
            url = f"{AKASHA_API}getCalculationsForUser/{uid}"
            async with session.get(url) as resp:
                entries = (await resp.json(content_type=None)).get("data") or []
        for entry in entries:
            fit = (entry.get("calculations") or {}).get("fit") or {}
            rank, out = _parse_rank(fit.get("ranking")), _parse_rank(fit.get("outOf"))
            character = _parse_rank(entry.get("characterId"))
            if rank and out and character:
                variant = fit.get("variant") or ""
                if isinstance(variant, dict):
                    variant = variant.get("displayName") or variant.get("name") or ""
                ranks[character] = {
                    # Akasha's own short board names: "MELT", "HYPER"…
                    "name": fit.get("short") or fit.get("name") or "",
                    "variant": str(variant),
                    "rank": rank,
                    "out": out,
                    "calculation": str(fit.get("calculationId") or ""),
                }
    except Exception:
        return {}
    _akasha[str(uid)] = (time.time(), ranks)
    return ranks


_akasha_teams = {}
TEAM_TTL = 6 * 60 * 60


async def akasha_team(character_id: int, calculation: str) -> list[dict]:
    """
    The team an Akasha board assumes: [{icon, constellation, extra}] where extra
    is the teammate's artifact set icon, or their weapon's. Empty if unknown.
    """
    cached = _akasha_teams.get(character_id)
    if not (cached and time.time() - cached[0] < TEAM_TTL):
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"User-Agent": BOT_UA},
            ) as session:
                url = f"{AKASHA_API}v2/leaderboards/categories"
                params = {"characterId": str(character_id)}
                async with session.get(url, params=params) as resp:
                    data = await resp.json(content_type=None)
        except Exception:
            return []
        boards = {}
        categories = data.get("data", data) if isinstance(data, dict) else data
        for category in categories or []:
            for board in category.get("weapons") or []:
                boards[str(board.get("calculationId"))] = board.get("teammates") or []
        cached = (time.time(), boards)
        _akasha_teams[character_id] = cached
    team = []
    for mate in cached[1].get(calculation, [])[:4]:
        character, weapon = mate.get("character") or {}, mate.get("weapon") or {}
        extra = character.get("artifactSetIcon") or ""
        if not extra or extra.endswith("undefined.png"):
            extra = weapon.get("icon") or ""
        team.append(
            {
                "icon": character.get("icon"),
                "element": character.get("element"),
                "constellation": character.get("constellation"),
                "extra": extra,
            }
        )
    return team


async def interknot_ranks(uid) -> dict:
    """
    A ZZZ player's leaderboard ranks from Inter-Knot (interknot.space), the
    ZZZ counterpart of Akasha: agent id -> {name, rank, out}, best board per
    agent. Empty when the player isn't on Inter-Knot or it can't be reached.
    """
    cached = _interknot.get(str(uid))
    if cached and time.time() - cached[0] < RANK_TTL:
        return cached[1]
    ranks = {}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": BOT_UA},
        ) as session:
            # update=true has Inter-Knot refresh the player from Enka first
            url = f"{INTERKNOT_API}leaderboards/{uid}"
            async with session.get(url, params={"update": "true"}) as resp:
                entries = (await resp.json(content_type=None)).get("data") or []
        for entry in entries:
            board = entry.get("Leaderboard") or {}
            agent = (board.get("Character") or {}).get("Id")
            rank, out = entry.get("Rank"), board.get("Total")
            if not (agent and rank and out):
                continue
            best = ranks.get(agent)
            if best is None or rank / out < best["rank"] / best["out"]:
                ranks[agent] = {
                    "name": board.get("Name") or board.get("FullName") or "",
                    "rank": rank,
                    "out": out,
                }
    except Exception:
        return {}
    _interknot[str(uid)] = (time.time(), ranks)
    return ranks


async def build_card(game: str, character, owner: str, uid=None) -> bytes:
    """The build card for a showcase character of game (gi, hsr or zzz)"""
    if game == "gi":
        urls, spec = gi_build(character, owner)
        board = (await akasha_ranks(uid)).get(character.id) if uid else None
        if board:
            team = await akasha_team(character.id, board["calculation"])
            spec["leaderboard"] = {**board, "team": team}
            for i, mate in enumerate(team):
                element = AKASHA_ELEMENTS.get(mate.get("element") or "", "")
                urls[f"team{i}"] = mate["icon"] or (
                    # Enka has no Cryo one; Amber has all seven
                    f"{AMBER_UI}UI_Buff_Element_{element}.png"
                    if element
                    else None
                )
                urls[f"team_extra{i}"] = mate["extra"]
    elif game == "hsr":
        urls, spec = hsr_build(character, owner)
    else:
        urls, spec = zzz_build(character, owner, await zzz_disc_icons())
        if uid:
            spec["leaderboard"] = (await interknot_ranks(uid)).get(character.id)
    await ensure_fonts(owner, spec["name"])
    art = await fetch_images(urls)
    if art.get("splash") is None:
        art["splash"] = art.get("splash_fallback")
    return await sync_to_async(_draw_build, spec, art)


# ------------------------------------------------------------- profile card
#
# Described as: banner, avatar, name, title ((text, colour) or None), line,
# signature, tiles: [{title, value, extra, icon, stars}],
# characters: [{icon, name, line, colour}]

PW = 1500


def _tile(card, draw, box, tile: dict, art):
    panel(card, box)
    x0, y0, x1, y1 = box
    tx = x0 + 20
    icon = art.get(tile.get("icon"))
    if icon is not None:
        paste_fit(card, icon, (x0 + 10, y0 + 10, x0 + 110, y1 - 10))
        tx = x0 + 120
    draw.text(
        (tx, y0 + 18),
        tile["title"],
        font=fit_font(tile["title"], 22, x1 - tx - 12),
        fill=SOFT,
    )
    value = tile["value"]
    face = fit_font(value, 34, x1 - tx - 16, True)
    draw.text((tx, y0 + 48), value, font=face, fill=WHITE)
    if tile.get("stars"):
        sx = tx + text_width(value, face) + 14
        star(draw, sx + 12, y0 + 70, 12)
        draw.text(
            (sx + 30, y0 + 52), str(tile["stars"]), font=font(28, True), fill=GOLD
        )
    if tile.get("extra"):
        draw.text((tx, y0 + 94), tile["extra"], font=font(22), fill=SOFT)


def _draw_profile(spec: dict, art: dict) -> bytes:
    characters = spec["characters"]
    rows = -(-len(characters) // 4)
    height = 640 + rows * 170 + (30 if rows else 0)
    card = Image.new("RGB", (PW, height), (24, 26, 36))
    draw = ImageDraw.Draw(card, "RGBA")

    banner = art.get("banner")
    if banner is not None:
        # Fill the header; very wide namecards (ZZZ's) are cropped to the
        # middle
        scale = max(PW / banner.width, 420 / banner.height)
        banner = banner.resize(
            (int(banner.width * scale), int(banner.height * scale)),
            Image.Resampling.LANCZOS,
        )
        left = (banner.width - PW) // 2
        put(card, banner.crop((left, 0, left + PW, 420)))
    elif spec.get("banner_colour"):
        put(
            card,
            gradient(
                (PW, 420),
                spec["banner_colour"] + (255,),
                (24, 26, 36, 255),
                horizontal=True,
            ),
        )
    put(card, gradient((PW, 420), (24, 26, 36, 60), (24, 26, 36, 255)))

    # Avatar, name, title and levels
    draw.ellipse((40, 90, 240, 290), fill=(0, 0, 0, 120))
    paste_fit(card, art.get("avatar"), (48, 98, 232, 282), circle=True)
    name = spec["name"]
    face = fit_font(name, 60, PW - 320, True)
    draw.text((270, 100), name, font=face, fill=WHITE)
    if spec.get("title"):
        text, colour = spec["title"]
        tx = 270 + text_width(name, face) + 24
        draw.text(
            (tx, 124), text, font=fit_font(text, 26, PW - tx - 40, True), fill=colour
        )
    draw.text((272, 182), spec["line"], font=font(30), fill=SOFT)
    if spec.get("signature"):
        sig = spec["signature"]
        draw.text((272, 232), sig, font=fit_font(sig, 26, PW - 320), fill=FAINT)

    y = 330
    for i, tile in enumerate(spec["tiles"][:4]):
        x = 40 + i * 360
        _tile(card, draw, (x, y, x + 340, y + 150), tile, art)

    # Showcase characters, numbered for picking
    if characters:
        draw.text((40, 510), "Showcase", font=font(30, True), fill=WHITE)
        draw.text(
            (210, 516),
            "Add -c to pick characters for build cards, or a number for one",
            font=font(22),
            fill=FAINT,
        )
    for i, character in enumerate(characters):
        col, row = i % 4, i // 4
        x0, y0 = 40 + col * 360, 570 + row * 170
        panel(card, (x0, y0, x0 + 340, y0 + 150), fill=character["colour"] + (150,))
        paste_fit(
            card, art.get(f"char{i}"), (x0 + 8, y0 + 8, x0 + 142, y0 + 142), circle=True
        )
        draw.text((x0 + 152, y0 + 18), f"{i + 1}", font=font(24, True), fill=GOLD)
        draw.text(
            (x0 + 152, y0 + 46),
            character["name"],
            font=fit_font(character["name"], 28, 178, True),
            fill=WHITE,
        )
        draw.text((x0 + 152, y0 + 92), character["line"], font=font(24), fill=SOFT)
    return to_jpeg(card)


def _gi_profile(player, characters, uid_text) -> tuple[dict, dict]:
    urls = {
        "banner": getattr(player.namecard, "full", None),
        "avatar": getattr(player.profile_picture_icon, "circle", None)
        or getattr(player.profile_picture_icon, "front", None),
    }
    tiles = [{"title": "Achievements", "value": f"{player.achievements:,}"}]
    if player.abyss_floor:
        value = f"{player.abyss_floor}-{player.abyss_level}"
        tiles.append(
            {"title": "Spiral Abyss", "value": value, "stars": player.abyss_stars}
        )
    else:
        tiles.append({"title": "Spiral Abyss", "value": "—"})
    if player.theater_act:
        value = f"Act {player.theater_act}"
        tiles.append(
            {
                "title": "Imaginarium Theater",
                "value": value,
                "stars": player.theater_stars,
            }
        )
    else:
        tiles.append({"title": "Imaginarium Theater", "value": "—"})
    difficulty = player.stygian_difficulty
    if difficulty:
        urls["medal"] = f"{ENKA_UI}UI_LeyLineChallenge_Medal_{difficulty}.png"
        seconds = player.stygian_clear_time
        tiles.append(
            {
                "title": "Stygian Onslaught",
                "value": STYGIAN_DIFFICULTIES.get(difficulty, "Dire"),
                "extra": f"Cleared in {
                    clear_time(seconds)}" if seconds else None,
                "icon": "medal",
            }
        )
    else:
        tiles.append({"title": "Stygian Onslaught", "value": "—"})
    chars = []
    for i, character in enumerate(characters):
        urls[f"char{i}"] = character.icon.front
        cons = sum(1 for c in character.constellations if c.unlocked)
        chars.append(
            {
                "name": character.name,
                "line": f"Lv. {character.level} · C{cons}",
                "colour": ELEMENT_COLOURS.get(
                    _element(character.element), DEFAULT_COLOUR
                ),
            }
        )
    spec = {
        "name": player.nickname,
        "line": f"AR {
            player.level}  ·  WL {
            player.world_level}  ·  {uid_text}",
        "signature": player.signature,
        "tiles": tiles,
        "characters": chars,
    }
    return urls, spec


def _hsr_profile(player, characters, uid_text) -> tuple[dict, dict]:
    urls = {"avatar": player.icon}
    stats = player.stats
    tiles = [
        {"title": "Achievements", "value": f"{stats.achievement_count:,}"},
        {"title": "Characters", "value": f"{stats.character_count:,}"},
        {"title": "Light Cones", "value": f"{stats.light_cone_count:,}"},
        {
            "title": "Simulated Universe",
            "value": (
                f"World {stats.max_simulated_universe_world}"
                if stats.max_simulated_universe_world
                else "—"
            ),
        },
    ]
    chars = []
    for i, character in enumerate(characters):
        urls[f"char{i}"] = character.icon.round
        chars.append(
            {
                "name": character.name,
                "line": f"Lv. {
                    character.level} · E{
                    character.eidolons_unlocked}",
                "colour": ELEMENT_COLOURS.get(
                    _element(character.element), DEFAULT_COLOUR
                ),
            }
        )
    first = chars[0]["colour"] if chars else DEFAULT_COLOUR
    spec = {
        "banner_colour": first,
        "name": player.nickname,
        "line": f"TL {
            player.level}  ·  EQ {
            player.equilibrium_level}  ·  {uid_text}",
        "signature": player.signature,
        "tiles": tiles,
        "characters": chars,
    }
    return urls, spec


ZZZ_MEDALS = {
    # MedalType, from Enka's API docs
    1: "Shiyu Defense",
    2: "Simulated Battle Tower",
    3: "Deadly Assault",
    4: "Battle Tower: Last Stand",
}
ZZZ_MEDAL_STORE = "https://raw.githubusercontent.com/EnkaNetwork/API-docs/master/store/zzz/medals.json"


async def zzz_medal_icons() -> dict:
    data = await _fetch(ZZZ_MEDAL_STORE)
    if not data:
        return {}
    try:
        medals = json.loads(data)
    except ValueError:
        return {}
    return {
        k: "https://enka.network" + v["Icon"]
        for k, v in medals.items()
        if v.get("Icon")
    }


def _zzz_profile(player, agents, uid_text, medal_icons: dict) -> tuple[dict, dict]:
    urls = {"banner": getattr(player.namecard, "icon", None), "avatar": player.avatar}
    medals = {m.type: m for m in player.medals}
    tiles = []
    for kind, title in ZZZ_MEDALS.items():
        medal = medals.get(kind)
        if medal is None:
            tiles.append({"title": title, "value": "—"})
            continue
        urls[f"medal{kind}"] = medal_icons.get(str(medal.icon_id))
        tiles.append(
            {"title": title, "value": f"{medal.value:,}", "icon": f"medal{kind}"}
        )
    chars = []
    for i, agent in enumerate(agents):
        urls[f"char{i}"] = agent.icon.round
        elements = [_element(e) for e in agent.elements] or ["Physical"]
        chars.append(
            {
                "name": agent.name,
                "line": f"Lv. {agent.level} · M{agent.mindscape}",
                "colour": ELEMENT_COLOURS.get(elements[0], DEFAULT_COLOUR),
            }
        )
    title = None
    if player.title is not None and player.title.text:
        colour = getattr(player.title, "color1", None) or "#ffd36e"
        rgb = tuple(int(colour.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
        title = (player.title.text, rgb + (255,))
    spec = {
        "name": player.nickname,
        "title": title,
        "line": f"Inter-Knot Lv. {player.level}  ·  {uid_text}",
        "signature": player.signature,
        "tiles": tiles,
        "characters": chars,
    }
    return urls, spec


async def profile_card(game: str, player, characters: list, uid_text: str) -> bytes:
    """A player's summary card with their showcase characters"""
    if game == "gi":
        urls, spec = _gi_profile(player, characters, uid_text)
    elif game == "hsr":
        urls, spec = _hsr_profile(player, characters, uid_text)
    else:
        urls, spec = _zzz_profile(player, characters, uid_text, await zzz_medal_icons())
    title = spec["title"][0] if spec.get("title") else ""
    await ensure_fonts(
        spec["name"],
        spec.get("signature") or "",
        title,
        *(c["name"] for c in spec["characters"]),
    )
    art = await fetch_images(urls)
    return await sync_to_async(_draw_profile, spec, art)

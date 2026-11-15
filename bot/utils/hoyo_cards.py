"""
Pillow cards for .profile: a player summary and a character build card.

The build card follows the layout of the EnkaCard "template one" card (splash
art with stats, constellations and talents on the left, weapon and artifacts
on the right), drawn from scratch: frames are plain shapes, art and icons come
from Enka, and the font is Noto Sans.
"""

import asyncio
import hashlib
import io
import math
import os
import re

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


def crit_value(artifact) -> float:
    """
    CRIT Rate ×2 + CRIT DMG from an artifact's substats and main stat
    (a crit circlet counts, as in the EnkaCard fork)
    """
    total = 0.0
    for stat in [artifact.main_stat, *artifact.sub_stats]:
        kind = str(getattr(stat.type, "value", stat.type))
        if kind == "FIGHT_PROP_CRITICAL":
            total += stat.value * 2
        elif kind == "FIGHT_PROP_CRITICAL_HURT":
            total += stat.value
    return round(total, 1)


# ---------------------------------------------------------------- build card

W, H = 1500, 1180
SPLASH_W = 950
RIGHT_X = 966


def _stat_rows(character) -> list[tuple[str, str]]:
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


def _draw_build(character, art: dict, owner: str) -> bytes:
    element = str(getattr(character.element, "value", character.element))
    colour = ELEMENT_COLOURS.get(element, DEFAULT_COLOUR)
    card = Image.new("RGB", (W, H), colour)
    draw = ImageDraw.Draw(card, "RGBA")

    # Background: light from the top left, the splash art, then shade for text
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

    # Stats, top left
    rows = _stat_rows(character)
    panel(card, (16, 16, 436, 32 + 46 * len(rows)))
    face, bold = font(24), font(24, True)
    for i, (name, value) in enumerate(rows):
        y = 28 + 46 * i
        draw.text((36, y), name, font=face, fill=SOFT)
        draw.text((416 - text_width(value, bold), y), value, font=bold, fill=WHITE)

    # Constellations down the left edge
    for i, const in enumerate(character.constellations[:6]):
        y = 520 + i * 104
        box = (24, y, 112, y + 88)
        ring = colour + (255,) if const.unlocked else (255, 255, 255, 60)
        draw.ellipse(box, fill=(0, 0, 0, 140), outline=ring, width=4)
        icon = art.get(f"const{i}")
        if icon is not None:
            if not const.unlocked:
                icon = icon.copy()
                icon.putalpha(icon.getchannel("A").point(lambda a: a * 0.4))
            paste_fit(card, icon, (38, y + 14, 98, y + 74))

    # Name, level, friendship and rarity, right-aligned over the art
    name = character.name
    face = fit_font(name, 58, 460, True)
    draw.text(
        (930 - text_width(name, face), 410),
        name,
        font=face,
        fill=WHITE,
        stroke_width=3,
        stroke_fill=(0, 0, 0, 120),
    )
    cons = sum(1 for c in character.constellations if c.unlocked)
    max_level = max(character.level, character.max_level)
    line = f"Lv. {character.level}/{max_level}  ·  C{cons}  ·  "
    friendship = str(character.friendship_level)
    face = font(28)
    fx = 930 - text_width(friendship, face)
    heart(draw, fx - 20, 504, 11, fill=(255, 140, 160, 255))
    draw.text(
        (fx, 486),
        friendship,
        font=face,
        fill=WHITE,
        stroke_width=2,
        stroke_fill=(0, 0, 0, 120),
    )
    draw.text(
        (fx - 36 - text_width(line, face), 486),
        line,
        font=face,
        fill=WHITE,
        stroke_width=2,
        stroke_fill=(0, 0, 0, 120),
    )
    stars(draw, 930 - character.rarity * 26, 530, character.rarity, r=11)

    # Crit value badge
    cv = sum(crit_value(a) for a in character.artifacts)
    badge = f"{cv:.1f} CV"
    face = font(26, True)
    bw = text_width(badge, face) + 36
    panel(card, (930 - bw, 572, 930, 616), radius=22, fill=(0, 0, 0, 150))
    draw.text((930 - bw + 18, 578), badge, font=face, fill=GOLD if cv >= 200 else WHITE)

    # Talents in a column by the right panel
    for i, talent in enumerate(character.talents[:3]):
        y = 660 + i * 150
        box = (812, y, 912, y + 100)
        draw.ellipse(box, fill=(0, 0, 0, 140), outline=colour + (255,), width=4)
        paste_fit(card, art.get(f"talent{i}"), (830, y + 18, 894, y + 82))
        level = str(talent.level)
        face = font(26, True)
        lw = text_width(level, face) + 24
        panel(
            card,
            (862 - lw / 2, y + 86, 862 + lw / 2, y + 122),
            radius=18,
            fill=(20, 20, 30, 230),
        )
        draw.text(
            (862 - lw / 2 + 12, y + 88),
            level,
            font=face,
            fill=TEAL if talent.is_upgraded else WHITE,
        )

    _draw_weapon(card, draw, character.weapon, art.get("weapon"))
    sets = _draw_artifacts(card, draw, character.artifacts, art)

    # Set bonuses
    y = 1094
    panel(card, (RIGHT_X, y, W - 16, H - 16))
    face = font(22, True)
    text = "   ".join(f"{n}× {s}" for s, n in sets) or "No set bonus"
    face = fit_font(text, 22, W - 16 - RIGHT_X - 32, True)
    draw.text((RIGHT_X + 16, y + 22), text, font=face, fill=WHITE)

    # Owner, bottom left
    face = font(24, text=owner)
    draw.text(
        (140, H - 50),
        owner,
        font=face,
        fill=SOFT,
        stroke_width=2,
        stroke_fill=(0, 0, 0, 120),
    )
    return to_jpeg(card)


def _draw_weapon(card, draw, weapon, icon):
    x0, y0, x1, y1 = RIGHT_X, 16, W - 16, 216
    panel(card, (x0, y0, x1, y1))
    rarity_colour = {5: (176, 112, 48), 4: (127, 103, 161), 3: (84, 134, 169)}.get(
        weapon.rarity, (110, 110, 110)
    )
    panel(
        card,
        (x0 + 14, y0 + 14, x0 + 186, y1 - 14),
        radius=14,
        fill=rarity_colour + (200,),
    )
    paste_fit(card, icon, (x0 + 22, y0 + 18, x0 + 178, y1 - 18))
    tx = x0 + 204
    face = fit_font(weapon.name, 30, x1 - tx - 16, True)
    draw.text((tx, y0 + 16), weapon.name, font=face, fill=WHITE)
    stars(draw, tx, y0 + 60, weapon.rarity, r=10)
    line = f"Lv. {weapon.level}  ·  R{weapon.refinement}"
    draw.text((tx, y0 + 88), line, font=font(24), fill=SOFT)
    for i, stat in enumerate(weapon.stats[:2]):
        text = f"{stat.name}  {stat.formatted_value}"
        draw.text((tx, y0 + 124 + i * 34), text, font=font(24, True), fill=WHITE)


ARTIFACT_ORDER = (
    "EQUIP_BRACER",
    "EQUIP_NECKLACE",
    "EQUIP_SHOES",
    "EQUIP_RING",
    "EQUIP_DRESS",
)


def _draw_artifacts(card, draw, artifacts, art) -> list[tuple[str, int]]:
    by_slot = {str(getattr(a.equip_type, "value", a.equip_type)): a for a in artifacts}
    counts = {}
    for i, slot in enumerate(ARTIFACT_ORDER):
        x0, y0 = RIGHT_X, 230 + i * 172
        x1, y1 = W - 16, y0 + 160
        panel(card, (x0, y0, x1, y1))
        artifact = by_slot.get(slot)
        if artifact is None:
            continue
        counts[artifact.set_name] = counts.get(artifact.set_name, 0) + 1
        paste_fit(
            card, art.get(f"artifact_{slot}"), (x0 + 6, y0 + 6, x0 + 136, y0 + 136)
        )
        level = f"+{artifact.level}"
        face = font(20, True)
        lw = text_width(level, face) + 18
        panel(
            card,
            (x0 + 14, y0 + 118, x0 + 14 + lw, y0 + 146),
            radius=12,
            fill=(20, 20, 30, 230),
        )
        draw.text((x0 + 23, y0 + 119), level, font=face, fill=WHITE)
        stars(draw, x0 + 20 + lw, y0 + 125, artifact.rarity, r=5.5)

        main = artifact.main_stat
        draw.text((x0 + 148, y0 + 14), main.name, font=font(22), fill=SOFT)
        draw.text(
            (x0 + 148, y0 + 40), main.formatted_value, font=font(36, True), fill=WHITE
        )
        cv = crit_value(artifact)
        if cv:
            text = f"{cv:.1f} CV"
            face = font(20, True)
            draw.text(
                (x1 - 16 - text_width(text, face), y0 + 14),
                text,
                font=face,
                fill=GOLD if cv >= 40 else SOFT,
            )
        for j, sub in enumerate(artifact.sub_stats[:4]):
            sx = x0 + 148 + (j % 2) * 186
            sy = y0 + 92 + (j // 2) * 32
            kind = str(getattr(sub.type, "value", sub.type))
            crit = kind in ("FIGHT_PROP_CRITICAL", "FIGHT_PROP_CRITICAL_HURT")
            name = sub.name.replace("Elemental Mastery", "EM").replace(
                "Energy Recharge", "ER"
            )
            text = f"{name} {sub.formatted_value}"
            draw.text(
                (sx, sy),
                text,
                font=fit_font(text, 22, 180),
                fill=GOLD if crit else WHITE,
            )
    sets = [(s, 4 if n >= 4 else 2) for s, n in counts.items() if n >= 2]
    return sorted(sets, key=lambda s: -s[1])


async def build_card(character, owner: str) -> bytes:
    """The build card for a Genshin character from an Enka showcase"""
    splash = None
    costume = getattr(character, "costume", None)
    if costume is not None:
        splash = getattr(costume.icon, "gacha", None)
    urls = {
        "splash": splash or character.icon.gacha,
        "splash_fallback": character.icon.front,
        "weapon": character.weapon.icon,
    }
    for i, const in enumerate(character.constellations[:6]):
        urls[f"const{i}"] = const.icon
    for i, talent in enumerate(character.talents[:3]):
        urls[f"talent{i}"] = talent.icon
    for artifact in character.artifacts:
        slot = str(getattr(artifact.equip_type, "value", artifact.equip_type))
        urls[f"artifact_{slot}"] = artifact.icon
    await ensure_fonts(owner, character.name)
    art = await fetch_images(urls)
    if art.get("splash") is None:
        art["splash"] = art.get("splash_fallback")
    return await sync_to_async(_draw_build, character, art, owner)


# ------------------------------------------------------------- profile card

PW = 1500


def _tile(card, draw, box, title, value, extra=None, icon=None, star_count=None):
    panel(card, box)
    x0, y0, x1, y1 = box
    tx = x0 + 20
    if icon is not None:
        paste_fit(card, icon, (x0 + 10, y0 + 10, x0 + 110, y1 - 10))
        tx = x0 + 120
    draw.text((tx, y0 + 18), title, font=font(22), fill=SOFT)
    draw.text(
        (tx, y0 + 48), value, font=fit_font(value, 34, x1 - tx - 16, True), fill=WHITE
    )
    if star_count:
        face = font(34, True)
        sx = tx + text_width(value, face) + 14
        star(draw, sx + 12, y0 + 70, 12)
        draw.text((sx + 30, y0 + 52), str(star_count), font=font(28, True), fill=GOLD)
    if extra:
        draw.text((tx, y0 + 94), extra, font=font(22), fill=SOFT)


def _draw_profile(player, characters, art: dict, uid_text: str) -> bytes:
    rows = (len(characters) + 3) // 4 if characters else 0
    height = 640 + rows * 170 + (30 if rows else 0)
    card = Image.new("RGB", (PW, height), (24, 26, 36))
    draw = ImageDraw.Draw(card, "RGBA")

    banner = art.get("namecard")
    if banner is not None:
        scale = PW / banner.width
        banner = banner.resize(
            (PW, int(banner.height * scale)), Image.Resampling.LANCZOS
        )
        put(card, banner.crop((0, 0, PW, min(banner.height, 420))))
    put(card, gradient((PW, 420), (24, 26, 36, 60), (24, 26, 36, 255)))

    # Avatar, name and levels
    draw.ellipse((40, 90, 240, 290), fill=(0, 0, 0, 120))
    paste_fit(card, art.get("avatar"), (48, 98, 232, 282), circle=True)
    name = player.nickname
    draw.text((270, 110), name, font=fit_font(name, 60, PW - 320, True), fill=WHITE)
    line = f"AR {player.level}  ·  WL {player.world_level}  ·  {uid_text}"
    draw.text((272, 192), line, font=font(30), fill=SOFT)
    if player.signature:
        sig = player.signature
        draw.text((272, 240), sig, font=fit_font(sig, 26, PW - 320), fill=FAINT)

    # Achievements and endgame modes
    y = 330
    tiles = [
        (40, y, 380, y + 150),
        (400, y, 740, y + 150),
        (760, y, 1100, y + 150),
        (1120, y, 1460, y + 150),
    ]
    _tile(card, draw, tiles[0], "Achievements", f"{player.achievements:,}")
    if player.abyss_floor:
        _tile(
            card,
            draw,
            tiles[1],
            "Spiral Abyss",
            f"{player.abyss_floor}-{player.abyss_level}",
            star_count=player.abyss_stars,
        )
    else:
        _tile(card, draw, tiles[1], "Spiral Abyss", "—")
    if player.theater_act:
        _tile(
            card,
            draw,
            tiles[2],
            "Imaginarium Theater",
            f"Act {player.theater_act}",
            star_count=player.theater_stars,
        )
    else:
        _tile(card, draw, tiles[2], "Imaginarium Theater", "—")
    difficulty = player.stygian_difficulty
    if difficulty:
        _tile(
            card,
            draw,
            tiles[3],
            "Stygian Onslaught",
            STYGIAN_DIFFICULTIES.get(difficulty, "Dire"),
            extra=(
                f"Cleared in {clear_time(player.stygian_clear_time)}"
                if player.stygian_clear_time
                else None
            ),
            icon=art.get("medal"),
        )
    else:
        _tile(card, draw, tiles[3], "Stygian Onslaught", "—")

    # Showcase characters, numbered for picking
    if characters:
        draw.text((40, 510), "Showcase", font=font(30, True), fill=WHITE)
        draw.text(
            (210, 516),
            "Pick one from the poll, or add its number to the command",
            font=font(22),
            fill=FAINT,
        )
    for i, character in enumerate(characters):
        col, row = i % 4, i // 4
        x0, y0 = 40 + col * 360, 570 + row * 170
        element = str(getattr(character.element, "value", character.element))
        colour = ELEMENT_COLOURS.get(element, DEFAULT_COLOUR)
        panel(card, (x0, y0, x0 + 340, y0 + 150), fill=colour + (150,))
        paste_fit(
            card, art.get(f"char{i}"), (x0 + 8, y0 + 8, x0 + 142, y0 + 142), circle=True
        )
        draw.text((x0 + 152, y0 + 18), f"{i + 1}", font=font(24, True), fill=GOLD)
        draw.text(
            (x0 + 152, y0 + 46),
            character.name,
            font=fit_font(character.name, 28, 178, True),
            fill=WHITE,
        )
        cons = sum(1 for c in character.constellations if c.unlocked)
        draw.text(
            (x0 + 152, y0 + 92),
            f"Lv. {character.level} · C{cons}",
            font=font(24),
            fill=SOFT,
        )
    return to_jpeg(card)


async def profile_card(player, characters: list, uid_text: str) -> bytes:
    """A Genshin player's summary card with their showcase characters"""
    urls = {
        "namecard": getattr(player.namecard, "full", None),
        "avatar": getattr(player.profile_picture_icon, "circle", None)
        or getattr(player.profile_picture_icon, "front", None),
    }
    if player.stygian_difficulty:
        urls["medal"] = f"{ENKA_UI}UI_LeyLineChallenge_Medal_{
                player.stygian_difficulty}.png"
    for i, character in enumerate(characters):
        urls[f"char{i}"] = character.icon.front
    await ensure_fonts(
        player.nickname, player.signature or "", *(c.name for c in characters)
    )
    art = await fetch_images(urls)
    return await sync_to_async(_draw_profile, player, characters, art, uid_text)

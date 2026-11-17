"""
Text with emoji for the image cards (.msg_ranking, .quote): emoji are drawn
inline as Apple's emoji images, the closest to WhatsApp's own (Twemoji for
any Apple's set lacks), since Noto Sans has none, and text is measured and
wrapped so nothing is clipped.
"""

import asyncio
import io
import re

import aiohttp
from PIL import Image

from bot.config import bot

from .hoyo_cards import fetch_image, font, font_runs, text_width

APPLE_EMOJI = (
    "https://cdn.jsdelivr.net/npm/emoji-datasource-apple@16.0.0/img/apple/64/{}.png"
)
TWEMOJI = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/{}.png"
# An emoji relative to its font's size
EMOJI_SCALE = 1.2

_part = "[\U0001f000-\U0001faff☀-➿⬀-⯿⌀-⏿←-⇿" "〰〽㊗㊙‼⁉™ℹ©®]"
_mods = "️?[\U0001f3fb-\U0001f3ff]?"
_emoji = re.compile(
    "[\U0001f1e6-\U0001f1ff]{2}"  # flags
    "|[0-9#*]️?⃣"  # keycaps
    "|\U0001f3f4[\U000e0020-\U000e007f]+"  # subdivision flags
    f"|{_part}{_mods}(?:‍{_part}{_mods})*"
)
# Symbols that are plain text unless written with the emoji selector
_text_symbols = set("©®™ℹ←↑→↓↔")


def runs(text: str) -> list[tuple[str, str]]:
    """text split into ("text", s) and ("emoji", s) runs"""
    out, pos = [], 0
    for match in _emoji.finditer(text):
        emoji = match.group(0)
        if emoji in _text_symbols:
            continue
        if match.start() > pos:
            out.append(("text", text[pos : match.start()]))
        out.append(("emoji", emoji))
        pos = match.end()
    if pos < len(text):
        out.append(("text", text[pos:]))
    return out


def twemoji_url(emoji: str) -> str:
    codes = [f"{ord(c):x}" for c in emoji]
    # Twemoji names drop the emoji selector unless the sequence is joined
    if "200d" not in codes:
        codes = [c for c in codes if c != "fe0f"]
    return TWEMOJI.format("-".join(codes))


def apple_names(emoji: str) -> list[str]:
    """
    Apple image names to try for emoji: as written, then without and with the
    emoji selector (Apple's names have it wherever the emoji takes it, e.g.
    2764-fe0f, which isn't always typed)
    """
    codes = [f"{ord(c):x}" for c in emoji]
    bare = [c for c in codes if c != "fe0f"]
    names = [
        "-".join(codes),
        "-".join(bare),
        "-".join(bare[:1] + ["fe0f"] + bare[1:]),
    ]
    return list(dict.fromkeys(names))


async def emoji_image(emoji: str) -> Image.Image | None:
    for name in apple_names(emoji):
        if (image := await fetch_image(APPLE_EMOJI.format(name))) is not None:
            return image
    # Emoji newer than Apple's set
    return await fetch_image(twemoji_url(emoji))


async def load_emojis(*texts: str) -> dict[str, Image.Image]:
    """The emoji images for every emoji in texts (cached on disk)"""
    emojis = sorted({e for t in texts if t for kind, e in runs(t) if kind == "emoji"})
    images = await asyncio.gather(*(emoji_image(e) for e in emojis))
    return {e: i for e, i in zip(emojis, images) if i is not None}


def rich_width(text: str, size: int, bold: bool, emojis: dict) -> float:
    width = 0.0
    for kind, part in runs(text):
        if kind == "emoji" and part in emojis:
            width += size * EMOJI_SCALE + 2
        else:
            # Each character in the font that has it (symbols, fancy letters…)
            for name, run in font_runs(part):
                width += text_width(run, font(size, bold, name=name))
    return width


def wrap(text: str, size: int, bold: bool, width: float, emojis: dict) -> list[str]:
    """text in lines no wider than width; a word too long for a line is broken"""

    def fits(line):
        return rich_width(line, size, bold, emojis) <= width

    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in re.split(r"(\s+)", paragraph):
            if not word:
                continue
            if fits(line + word):
                line += word
                continue
            if line.strip():
                lines.append(line.rstrip())
            line = word.lstrip()
            while not fits(line):
                cut = len(line) - 1
                while cut > 1 and not fits(line[:cut]):
                    cut -= 1
                lines.append(line[:cut])
                line = line[cut:]
        lines.append(line.rstrip())
    return lines


def draw_rich(card, draw, xy, text: str, size: int, bold: bool, fill, emojis: dict):
    """text at xy (1× units) with emoji drawn inline as images"""
    # Imported here so this works with or without the scaled helpers
    from .hoyo_cards import paste_fit

    x, y = xy
    for kind, part in runs(text):
        if kind == "emoji" and part in emojis:
            side = size * EMOJI_SCALE
            top = y + size * 0.18
            paste_fit(
                card,
                emojis[part],
                (int(x + 1), int(top), int(x + 1 + side), int(top + side)),
            )
            x += side + 2
        else:
            for name, run in font_runs(part):
                face = font(size, bold, name=name)
                draw.text((x, y), run, font=face, fill=fill)
                x += text_width(run, face)
    return x


async def profile_picture(jid) -> Image.Image | None:
    """A WhatsApp user's profile picture, or None if it's hidden or unset"""
    try:
        from neonize.proto.Neonize_pb2 import GetProfilePictureParams

        info = await bot.client.get_profile_picture(
            jid, GetProfilePictureParams(Preview=False)
        )
        if not getattr(info, "URL", None):
            return None
        # Not through the disk cache: these links change every time
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(info.URL) as resp:
                if resp.status != 200:
                    return None
                data = await resp.read()
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None

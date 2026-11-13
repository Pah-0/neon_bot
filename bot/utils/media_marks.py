"""
Tells whether a media is something the bot itself compressed, sanitized
or upscaled, so it isn't processed again.

Two checks, the first cheap and the second for what it misses:
- bot.processed_dict, saved with the other dicts: the SHA256 of each
  output -> its kind. WhatsApp keeps a file's SHA256 when it's forwarded
  or resent, so this catches those before anything is downloaded.
- A tag written into the output file itself ("neon-bot:<kind>" in a PNG
  tEXt chunk, a JPEG comment or the container's comment), found once the
  media is downloaded. It catches outputs the record no longer holds, for
  example after a database reset or once old entries were pruned.
"""

import hashlib
import struct
import time
import zlib

from bot.config import bot

from .bot_utils import probe_video
from .db_utils import save2db2

COMPRESSED = "compressed"
SANITIZED = "sanitized"
UPSCALED = "upscaled"

TAG = "neon-bot:"
# Oldest records are dropped past this many
MAX_RECORDS = 5000


def _sha_hex(media) -> str | None:
    """
    Hex SHA256 of a media message (its fileSHA256) or of raw bytes;
    None for anything else, such as a text message
    """
    if isinstance(media, (bytes, bytearray)):
        return hashlib.sha256(media).hexdigest()
    sha = getattr(media, "fileSHA256", None)
    return bytes(sha).hex() if sha else None


def processed_kind(media) -> str | None:
    """The kind of processing the bot did to produce this media, if any"""
    record = bot.processed_dict.get(_sha_hex(media))
    return record[0] if record else None


async def mark_processed(kind: str, *medias):
    """Records sent media messages (or the bytes sent) as made by `kind`"""
    now = int(time.time())
    for media in medias:
        if sha := _sha_hex(media):
            bot.processed_dict[sha] = [kind, now]
    excess = len(bot.processed_dict) - MAX_RECORDS
    if excess > 0:
        oldest = sorted(bot.processed_dict.items(), key=lambda kv: kv[1][1])
        for key, _ in oldest[:excess]:
            del bot.processed_dict[key]
    await save2db2(bot.processed_dict, "processed")


def _kind_in(text: str | bytes | None) -> str | None:
    if not text:
        return None
    if isinstance(text, bytes):
        text = text.decode("latin-1")
    _, found, rest = text.partition(TAG)
    if not found:
        return None
    # In an image the tag runs straight into the bytes that follow it
    return next(
        (k for k in (COMPRESSED, SANITIZED, UPSCALED) if rest.startswith(k)), None
    )


def tag_image(data: bytes, kind: str) -> bytes:
    """PNG or JPEG bytes with "neon-bot:<kind>" added; others are returned as is"""
    text = (TAG + kind).encode()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        # A tEXt chunk right after IHDR (8 byte signature + 25 byte chunk)
        body = b"Comment\x00" + text
        chunk = (
            struct.pack(">I", len(body))
            + b"tEXt"
            + body
            + struct.pack(">I", zlib.crc32(b"tEXt" + body))
        )
        return data[:33] + chunk + data[33:]
    if data.startswith(b"\xff\xd8"):
        # A COM segment right after SOI
        return (
            data[:2] + b"\xff\xfe" + struct.pack(">H", len(text) + 2) + text + data[2:]
        )
    return data


def image_mark(data: bytes) -> str | None:
    """The kind tag_image wrote into these image bytes, if any"""
    return _kind_in(data[:512])


def video_tag_args(kind: str) -> str:
    """ffmpeg output options that tag a video as made by `kind`"""
    return f'-metadata comment="{TAG}{kind}"'


async def video_mark(path: str) -> str | None:
    """The kind video_tag_args wrote into this video, if any"""
    try:
        info = await probe_video(path)
    except Exception:
        return None
    tags = info.get("format", {}).get("tags", {})
    return next(
        (_kind_in(v) for k, v in tags.items() if k.casefold() == "comment"), None
    )

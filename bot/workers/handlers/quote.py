import asyncio
import io
import os
import tempfile
from inspect import getdoc

from PIL import Image, ImageSequence

from bot.config import bot
from bot.utils.bot_utils import sync_to_async
from bot.utils.group_utils import get_member_label
from bot.utils.log_utils import logger
from bot.utils.msg_store import get_messages
from bot.utils.msg_utils import (
    chat_is_allowed,
    get_args,
    get_user_info,
    user_is_allowed,
    user_is_privileged,
)
from bot.utils.quote_card import quote_card


async def quote(event, args, client):
    """
    Turns the replied message into a quote sticker: the sender's profile
    picture and their message in a chat bubble, emoji and all. Photos,
    stickers, GIFs and videos in the message are included (moving ones as an
    animated sticker), and if it was itself a reply, the message it answered
    is shown at the top for context.

    Usage: reply to a message with quote [-img]
        -img : send it as a picture instead of a sticker
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    replied = event.reply_to_message
    if not replied:
        return await event.reply(getdoc(quote))
    try:
        async with event.react("🎨"):
            cards, durations, name = await make_quote(event, replied)
            if get_args(["-img", "store_true"], to_parse=args or "").img:
                # A photo has no transparency; put the bubble on a chat-like
                # backdrop. An animation's first frame stands for it.
                image = cards[0]
                backdrop = Image.new(
                    "RGBA", (image.width + 60, image.height + 60), (239, 234, 226, 255)
                )
                backdrop.alpha_composite(image, (30, 30))
                out = io.BytesIO()
                backdrop.convert("RGB").save(out, format="jpeg", quality=92)
                return await event.reply_photo(out.getvalue())
            me = bot.client.me or await bot.client.get_me()
            if len(cards) > 1:
                # Already a sized WebP sticker, so it isn't converted again
                webp = await sync_to_async(animated_sticker, cards, durations)
                return await event.reply_sticker(
                    webp,
                    quote=True,
                    name=name,
                    packname=me.PushName,
                    passthrough=True,
                )
            await event.reply_sticker(
                sticker_size(cards[0]), quote=True, name=name, packname=me.PushName
            )
    except NothingToQuote:
        await event.reply("*Reply to a text, photo, sticker, GIF or video message.*")
    except Exception:
        await logger(Exception)
        await event.react("❌")


class NothingToQuote(Exception):
    pass


async def sender_name(user) -> str:
    info = await get_user_info(user.id, user.server)
    return info.PushName or info.FullName or "Unknown"


def preview_text(message) -> str:
    """A message's text, or what it holds when it has none"""
    if text := (message.text or message.caption):
        return text
    if message.image:
        return "📷 Photo"
    if message.sticker:
        return "Sticker"
    if message.video:
        return "🎥 Video"
    if message.audio:
        return "🎤 Voice message"
    if message.document:
        return "📄 Document"
    return "Message"


# An animated quote's length and frame rate, which keep the sticker within
# WhatsApp's size limit
MAX_MS = 6000
FPS = 20
MAX_STICKER_BYTES = 950_000


def sticker_frames(webp: bytes) -> tuple[list[Image.Image], list[int]]:
    """An animated sticker's frames, the first MAX_MS of them, at most FPS"""
    frames, durations = [], []
    elapsed = last_start = 0
    step = 1000 / FPS
    with Image.open(io.BytesIO(webp)) as image:
        for frame in ImageSequence.Iterator(image):
            if elapsed >= MAX_MS:
                break
            # A frame's duration is only read once it's loaded
            frame.load()
            duration = frame.info.get("duration") or 100
            if not frames or elapsed - last_start >= step - 1:
                frames.append(frame.convert("RGBA"))
                durations.append(duration)
                last_start = elapsed
            else:
                # Too soon after the last kept frame: it holds a bit longer
                durations[-1] += duration
            elapsed += duration
    return frames, durations


async def video_frames(video: bytes) -> tuple[list[Image.Image], list[int]]:
    """A video's (or GIF's) frames: the first MAX_MS of it at FPS"""
    with tempfile.TemporaryDirectory() as folder:
        source = os.path.join(folder, "video")
        with open(source, "wb") as file:
            file.write(video)
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-v",
            "error",
            "-t",
            str(MAX_MS / 1000),
            "-i",
            source,
            "-an",
            "-vf",
            f"fps={FPS},scale=720:720:force_original_aspect_ratio=decrease",
            os.path.join(folder, "%03d.png"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode:
            raise RuntimeError(f"ffmpeg failed: {stderr.decode()}")
        frames = []
        for png in sorted(f for f in os.listdir(folder) if f.endswith(".png")):
            with Image.open(os.path.join(folder, png)) as frame:
                frames.append(frame.convert("RGBA"))
    return frames, [round(1000 / FPS)] * len(frames)


async def quote_media(replied) -> tuple[bytes | list | None, list[int] | None]:
    """
    The replied message's media: an image's bytes, or a moving one's frames
    and their durations in ms
    """
    sticker = replied.sticker
    if not (replied.image or sticker or replied.video):
        return None, None
    try:
        media = await replied.download()
    except Exception:
        return None, None
    try:
        if replied.video:
            frames, durations = await video_frames(media)
        elif sticker and sticker.isAnimated:
            frames, durations = await sync_to_async(sticker_frames, media)
        else:
            return media, None
    except Exception:
        await logger(Exception)
        return None, None
    if len(frames) < 2:
        return (frames or None), None
    return frames, durations


async def make_quote(event, replied) -> tuple[list[Image.Image], list | None, str]:
    """
    The quote, as one image or an animation's frames, the frames' durations
    and the quoted sender's name
    """
    text = replied.text or replied.caption or ""
    media, durations = await quote_media(replied)
    if not text and media is None:
        raise NothingToQuote
    sender = replied.from_user
    # The quoted copy drops what it replied to; the stored message keeps it
    mentions: [str] = []
    reply = None
    try:
        stored = await get_messages(event.chat.id, replied.id)
        answered = stored[0].reply_to_message if stored else None
    except Exception:
        answered = None
    if answered is not None:
        reply = {
            "user": answered.from_user.id,
            "name": await sender_name(answered.from_user),
            "text": preview_text(answered),
            "tag": get_member_label(event.chat.id, answered.from_user),
        }
        if answered.media:
            reply["mentions"] = list(
                answered.media.contextInfo.mentionedJID
            )
    name = await sender_name(sender)
    tag = get_member_label(event.chat.id, sender)
    if m := event.reply_to_message.media:
        mentions = list(m.contextInfo.mentionedJID)
    cards = await quote_card(
        sender.id, sender.jid, name, text, media, reply, tag, mentions
    )
    return cards, durations, name


def fit_sticker(image: Image.Image) -> Image.Image:
    """
    The quote fitted into WhatsApp's 512×512 sticker with high-quality
    resampling, so the sticker conversion has nothing left to resize
    """
    image = image.copy()
    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    square = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    square.alpha_composite(image, ((512 - image.width) // 2, (512 - image.height) // 2))
    return square


def sticker_size(image: Image.Image) -> bytes:
    out = io.BytesIO()
    fit_sticker(image).save(out, format="png")
    return out.getvalue()


def animated_sticker(cards: list[Image.Image], durations: list[int]) -> bytes:
    """
    The quote's frames as an animated WebP sticker: lossless if it fits,
    else at lower quality and then fewer frames until it's within WhatsApp's
    size limit
    """
    frames = [fit_sticker(card) for card in cards]
    # Lossy leaves faint traces of earlier frames on the bubble; lossless
    # keeps a sticker's flat colours small enough
    lossless = True
    while True:
        for quality in ([None] if lossless else []) + [70, 50, 30]:
            out = io.BytesIO()
            frames[0].save(
                out,
                format="webp",
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=0,
                lossless=quality is None,
                quality=quality or 80,
                method=4,
            )
            if out.tell() <= MAX_STICKER_BYTES:
                return out.getvalue()
        if len(frames) < 4:
            if out.tell() <= 1_000_000:
                return out.getvalue()
            # Still too big: scale the frames down and try again
            frames = [
                f.resize(
                    (f.width * 3 // 4, f.height * 3 // 4), Image.Resampling.LANCZOS
                )
                for f in frames
            ]
            if frames[0].width < 200:
                return out.getvalue()
            lossless = False
            continue
        lossless = False
        # Drop every third frame; the one before it is shown longer
        kept_frames, kept_durations = [], []
        for i, (frame, duration) in enumerate(zip(frames, durations)):
            if i % 3 == 2:
                kept_durations[-1] += duration
            else:
                kept_frames.append(frame)
                kept_durations.append(duration)
        frames, durations = kept_frames, kept_durations

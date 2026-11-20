"""
Reddit downloads that don't need reddit.com, which blocks server IPs: posts
are looked up in Arctic Shift's archive (a few minutes behind Reddit) and
their media fetched from v.redd.it and i.redd.it, which aren't blocked. Only
share links (/s/) need reddit.com; where it blocks the server, they resolve
only as a logged-in user (the way past the block Reddit offers), with a
Reddit account's cookies from the cookie file.
"""

import asyncio
import html
import logging
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

import aiofiles
import aiohttp
import magic

from .twitter_dl import load_netscape_cookies

_log_ = logging.getLogger(__name__)

ARCTIC_SHIFT_API = "https://arctic-shift.photon-reddit.com/api/posts/ids"
ARCTIC_SHIFT_COMMENTS_API = "https://arctic-shift.photon-reddit.com/api/comments/ids"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
}
# The tallest video track picked, like the bot's default yt-dlp quality
MAX_HEIGHT = 720

POST_RE = re.compile(
    r"https?://(?:[\w-]+\.)?reddit\.com/(?:(?:r|u|user)/[\w-]+/)?"
    r"(?:comments|gallery)/(?P<id>[a-z0-9]+)"
)
SHORT_RE = re.compile(r"https?://redd\.it/(?P<id>[a-z0-9]+)")
# What the app's share button gives; only reddit.com knows the post
SHARE_RE = re.compile(r"https?://(?:[\w-]+\.)?reddit\.com/r/[\w-]+/s/[\w-]+")
VIDEO_RE = re.compile(r"https?://v\.redd\.it/(?P<id>[a-z0-9]+)")
IMAGE_RE = re.compile(r"https?://i\.redd\.it/[\w.-]+")
# Comment links: /comments/post_id/comment/comment_id/
COMMENT_RE = re.compile(
    r"https?://(?:[\w-]+\.)?reddit\.com/(?:(?:r|u|user)/[\w-]+/)?"
    r"comments/[a-z0-9]+/comment/(?P<id>[a-z0-9]+)"
)


@dataclass
class DownloadResult:
    local_path: str
    caption: str
    media_type: str  # "video", "gif" or "image"
    source_url: str  # a v.redd.it video ID for "video"
    thumbnail_url: str
    width: Optional[int] = None
    height: Optional[int] = None


def is_valid_reddit_url(url: str) -> bool:
    """
    True for a post, gallery, share, redd.it, v.redd.it or comment link
    (resolve a share link with resolve_share_url first)
    """
    return any(
        r.match(url)
        for r in (POST_RE, SHARE_RE, SHORT_RE, VIDEO_RE, COMMENT_RE)
    )


def is_reddit_share_url(url: str) -> bool:
    return bool(SHARE_RE.match(url))


def _reddit_cookies(cookie_file: str | None) -> dict[str, str]:
    if not (cookie_file and os.path.exists(cookie_file)):
        return {}
    jar = load_netscape_cookies(cookie_file)
    return {c.name: c.value for c in jar if c.domain.endswith("reddit.com")}


async def resolve_share_url(url: str, cookie_file: str | None = None) -> str:
    """
    The post URL a share link redirects to, sending the cookie file's Reddit
    cookies if it has any
    """
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(
            url, cookies=_reddit_cookies(cookie_file), allow_redirects=False
        ) as resp:
            location = urllib.parse.urljoin(url, resp.headers.get("Location", ""))
            status = resp.status
    if POST_RE.match(location):
        return location
    raise RuntimeError(f"Reddit didn't resolve the share link (HTTP {status})")


async def _get_post(session: aiohttp.ClientSession, post_id: str) -> dict:
    async with session.get(ARCTIC_SHIFT_API, params={"ids": post_id}) as resp:
        if resp.status >= 400:
            try:
                error = (await resp.json()).get("error")
            except Exception:
                error = None
            raise RuntimeError(f"Arctic Shift: {
                error or f'HTTP {
                    resp.status}'}")
        posts = (await resp.json()).get("data") or []
    if not posts:
        raise RuntimeError(
            "Post not found in Arctic Shift's archive (if it's new, try again"
            " in a few minutes)"
        )
    return posts[0]


async def _get_comment(session: aiohttp.ClientSession, comment_id: str) -> dict:
    async with session.get(ARCTIC_SHIFT_COMMENTS_API, params={"ids": comment_id}) as resp:
        if resp.status >= 400:
            try:
                error = (await resp.json()).get("error")
            except Exception:
                error = None
            raise RuntimeError(f"Arctic Shift: {
                error or f'HTTP {
                    resp.status}'}")
        comments = (await resp.json()).get("data") or []
    if not comments:
        raise RuntimeError(
            "Comment not found in Arctic Shift's archive (if it's new, try again"
            " in a few minutes)"
        )
    return comments[0]


def _comment_media(comment: dict) -> List[tuple[str, str]]:
    """Extract media from a comment's body (can contain image/gif/video links)"""
    body = comment.get("body", "")
    items = []
    # Check for i.redd.it image/gif links in the body
    for m in IMAGE_RE.finditer(body):
        items.append(("image", m.group(0)))
    # Check for v.redd.it video links in the body
    for m in VIDEO_RE.finditer(body):
        items.append(("video", m.group("id")))
    return items


def _gallery(post: dict) -> List[tuple[str, str]]:
    found = []
    metadata = post.get("media_metadata") or {}
    for entry in (post.get("gallery_data") or {}).get("items", []):
        meta = metadata.get(entry.get("media_id")) or {}
        if meta.get("status", "valid") != "valid":
            continue
        source = meta.get("s") or {}
        if meta.get("e") == "Image" and source.get("u"):
            # The full-size original, under the preview's extension (.jpeg)
            path = urllib.parse.urlparse(html.unescape(source["u"])).path
            found.append(("image", f"https://i.redd.it/{os.path.basename(path)}"))
        elif meta.get("e") == "AnimatedImage" and (
            src := source.get("mp4") or source.get("gif")
        ):
            found.append(("gif", html.unescape(src)))
    return found


async def _post_media(
    session: aiohttp.ClientSession, post: dict, depth: int = 0
) -> tuple[dict, List[tuple[str, str]]]:
    """(the post the media is from, [(media type, URL or video ID)])"""
    if post.get("removed_by_category"):
        raise RuntimeError("This post was removed on Reddit")
    url = post.get("url_overridden_by_dest") or post.get("url") or ""
    if m := VIDEO_RE.match(url):
        return post, [("video", m.group("id"))]
    if items := _gallery(post):
        return post, items
    if IMAGE_RE.match(url):
        return post, [("image", url)]
    video = (post.get("secure_media") or post.get("media") or {}).get("reddit_video")
    if video and (m := VIDEO_RE.match(video.get("fallback_url", ""))):
        return post, [("video", m.group("id"))]
    # A crosspost: the media is on the original post
    parent = (post.get("crosspost_parent") or "").removeprefix("t3_")
    if parent and depth < 2:
        return await _post_media(session, await _get_post(session, parent), depth + 1)
    raise RuntimeError("This post has no Reddit-hosted media")


def _tracks(mpd: str) -> tuple[List[tuple[int, str]], List[tuple[int, str]]]:
    """A DASH playlist's ([(height, file)] videos, [(bandwidth, file)] audio)"""
    videos, audio = [], []
    for aset in ET.fromstring(mpd).iter():
        if not aset.tag.endswith("AdaptationSet"):
            continue
        for rep in aset:
            if not rep.tag.endswith("Representation"):
                continue
            base = next((c.text for c in rep if c.tag.endswith("BaseURL")), None)
            if not base:
                continue
            kind = " ".join(
                filter(
                    None,
                    (
                        aset.get("contentType"),
                        aset.get("mimeType"),
                        rep.get("mimeType"),
                    ),
                )
            )
            if "audio" in kind:
                audio.append((int(rep.get("bandwidth") or 0), base.strip()))
            elif rep.get("height"):
                videos.append((int(rep.get("height")), base.strip()))
    return videos, audio


async def _download_file(
    session: aiohttp.ClientSession,
    url: str,
    dest: str,
    progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
) -> None:
    async with session.get(url) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0))
        done = 0
        async with aiofiles.open(dest, "wb") as f:
            async for chunk in resp.content.iter_chunked(65536):
                await f.write(chunk)
                done += len(chunk)
                if progress_callback:
                    await progress_callback(done, total, dest)


async def _download_video(
    session: aiohttp.ClientSession,
    video_id: str,
    dest: str,
    progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
) -> None:
    """
    Downloads a v.redd.it video's tracks and muxes them locally (ffmpeg is
    never given a URL)
    """
    base = f"https://v.redd.it/{video_id}/"
    async with session.get(base + "DASHPlaylist.mpd") as resp:
        resp.raise_for_status()
        videos, audio = _tracks(await resp.text())
    if not videos:
        raise RuntimeError("No video tracks in the DASH playlist")
    video = max((v for v in videos if v[0] <= MAX_HEIGHT), default=min(videos))
    video_file = dest + ".video"
    await _download_file(
        session, urllib.parse.urljoin(base, video[1]), video_file, progress_callback
    )
    if not audio:
        os.replace(video_file, dest)
        return
    audio_file = dest + ".audio"
    await _download_file(
        session,
        urllib.parse.urljoin(base, max(audio)[1]),
        audio_file,
        progress_callback,
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_file,
        "-i",
        audio_file,
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        dest,
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    for f in (video_file, audio_file):
        if os.path.exists(f):
            os.remove(f)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg mux failed: {stderr.decode().strip()}")


async def download_reddit(
    url: str,
    output_dir: str = ".",
    quiet: bool = False,
    progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
) -> List[DownloadResult]:
    """
    Download the video, image(s) or gallery of a Reddit post or comment.

    Args:
        url:        Post, gallery, redd.it, v.redd.it or comment link.
        output_dir: Directory to save the file(s).
        quiet:      Suppress console output.
        progress_callback:
            Async callable (downloaded_bytes, total_bytes, file_path)
            called after each chunk.

    Returns:
        List of DownloadResult objects (one per image or video).
    """
    os.makedirs(output_dir, exist_ok=True)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        if m := VIDEO_RE.match(url):
            # Where it's posted is only known to reddit.com
            caption, media = "", [("video", m.group("id"))]
        elif m := COMMENT_RE.match(url):
            comment_id = m.group("id")
            comment = await _get_comment(session, comment_id)
            media = _comment_media(comment)
            caption = f"Comment by u/{comment.get('author', 'unknown')} in r/{comment.get('subreddit', 'unknown')}"
            if not media:
                raise RuntimeError("This comment has no Reddit-hosted media")
        else:
            if not (m := POST_RE.match(url) or SHORT_RE.match(url)):
                raise ValueError(f"Not a supported Reddit URL: {url}")
            post_id = m.group("id")
            post, media = await _post_media(session, await _get_post(session, post_id))
            caption = "\n".join(
                filter(
                    None,
                    (
                        html.unescape(post.get("title") or "").strip(),
                        f"r/{post['subreddit']}" if post.get("subreddit") else "",
                    ),
                )
            )
        if not quiet:
            print(f"[*] {len(media)} item(s): {caption!r}")

        items = []
        for i, (kind, source) in enumerate(media):
            dest = os.path.join(output_dir, f"reddit_{i + 1:02d}")
            if kind == "video":
                dest += ".mp4"
                await _download_video(session, source, dest, progress_callback)
            else:
                path = urllib.parse.urlparse(source).path
                dest += os.path.splitext(path)[1].lower() or ".jpg"
                await _download_file(session, source, dest, progress_callback)
                if magic.from_file(dest, mime=True) == "image/gif":
                    kind = "gif"
            if not quiet:
                print(f"[ok] -> {dest}  ({kind})")
            items.append(
                DownloadResult(
                    local_path=dest,
                    caption=caption,
                    media_type=kind,
                    source_url=source,
                    thumbnail_url=source,
                )
            )
            if i < len(media) - 1:
                await asyncio.sleep(0.5)

    return items

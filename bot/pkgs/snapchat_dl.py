import asyncio
import html
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional

import aiofiles
import aiohttp
import magic

_log_ = logging.getLogger(__name__)

SNAP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
}
# Snaps sent per story or highlight link
MAX_ITEMS = 30

_HOST = r"https?://(?:www\.|story\.)?snapchat\.com"
SPOTLIGHT_RE = re.compile(_HOST + r"/(?:@[\w.-]+/)?spotlight/(?P<id>[\w-]+)")
HIGHLIGHT_RE = re.compile(_HOST + r"/@(?P<user>[\w.-]+)/highlight/(?P<id>[\w-]+)")
# A profile's public story, or one snap of it
STORY_RE = re.compile(
    _HOST + r"/@(?P<user>[\w.-]+)/(?:story(?=[/?#]|$)|(?P<id>[\w-]{20,}))"
)
SHORT_RE = re.compile(
    r"https?://(?:t\.snapchat\.com/[\w-]+|(?:www\.)?snapchat\.com/t/[\w-]+)"
)
MIME_EXTS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@dataclass
class DownloadResult:
    local_path: str
    caption: str
    media_type: str  # "video" or "image"
    source_url: str
    thumbnail_url: str
    width: Optional[int] = None
    height: Optional[int] = None


def is_valid_snapchat_url(url: str) -> bool:
    """True for a Spotlight, story, story snap, highlight or short share link"""
    return any(
        r.match(url) for r in (SPOTLIGHT_RE, HIGHLIGHT_RE, STORY_RE, SHORT_RE)
    )


def _ld_json(page: str) -> List[dict]:
    found = []
    for m in re.finditer(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>', page, re.S
    ):
        try:
            found.append(json.loads(m.group(1)))
        except ValueError:
            continue
    return found


def _flight_data(page: str) -> str:
    """The page's React Server Components payload, unescaped and joined"""
    chunks = []
    for m in re.finditer(r"self\.__next_f\.push\((\[.*?\])\)</script>", page, re.S):
        try:
            chunk = json.loads(m.group(1))
        except ValueError:
            continue
        if len(chunk) > 1 and isinstance(chunk[1], str):
            chunks.append(chunk[1])
    return "".join(chunks)


def _sequences(data: str) -> List[tuple[str, List[dict]]]:
    """
    Every list of snaps in the payload, as (the kind of playback it's in,
    or "", its snaps)
    """
    decoder = json.JSONDecoder()
    found = []
    for m in re.finditer(r'(?:"kind":"(\w+)",)?"sequence":\{"items":', data):
        try:
            items, _ = decoder.raw_decode(data, m.end())
        except ValueError:
            continue
        items = [i for i in items if i.get("kind") in ("video", "image") and i.get("src")]
        if items:
            found.append((m.group(1) or "", items))
    return found


def _title(page: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", page, re.S)
    title = html.unescape(m.group(1)).strip() if m else ""
    # "Name (@user) | Highlight on Snapchat"
    return re.sub(r"\s*\|[^|]*Snapchat$", "", title)


def _results(items: List[dict], caption: str) -> List[DownloadResult]:
    if len(items) > MAX_ITEMS:
        caption += f"\n(the first {MAX_ITEMS} of {len(items)} snaps)"
        items = items[:MAX_ITEMS]
    return [
        DownloadResult(
            local_path="",
            caption=caption,
            media_type=item["kind"],
            source_url=item["src"],
            thumbnail_url=item.get("thumbnail") or item["src"],
        )
        for item in items
    ]


def _parse_spotlight(page: str, snap_id: str) -> List[DownloadResult]:
    videos = [d for d in _ld_json(page) if d.get("@type") == "VideoObject"]
    video = next(
        (v for v in videos if v.get("url", "").rstrip("/").endswith(snap_id)),
        videos[0] if videos else None,
    )
    if not video or not video.get("contentUrl"):
        return []
    lines = []
    if (name := video.get("name", "").strip()) and name != "Spotlight Snap":
        lines.append(name)
    if user := (video.get("creator") or {}).get("alternateName"):
        lines.append(f"@{user}")
    return [
        DownloadResult(
            local_path="",
            caption="\n".join(lines),
            media_type="video",
            source_url=video["contentUrl"],
            thumbnail_url=video.get("thumbnailUrl") or video["contentUrl"],
            width=int(video["width"]) if str(video.get("width")).isdigit() else None,
            height=(
                int(video["height"]) if str(video.get("height")).isdigit() else None
            ),
        )
    ]


def _parse_story(page: str, user: str, snap_id: str | None) -> List[DownloadResult]:
    sequences = _sequences(_flight_data(page))
    story = next((items for kind, items in sequences if kind == "story"), [])
    if snap_id:
        # The one snap linked to, wherever the page lists it
        for _, items in sequences:
            if snap := next((i for i in items if i.get("id") == snap_id), None):
                return _results([snap], f"@{user}'s story")
        return []
    return _results(story, f"@{user}'s story")


def _parse_highlight(page: str) -> List[DownloadResult]:
    sequences = _sequences(_flight_data(page))
    # The page's own highlight comes first; others may be listed after it
    items = next((items for kind, items in sequences if kind != "story"), [])
    return _results(items, _title(page))


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


async def download_snapchat(
    url: str,
    output_dir: str = ".",
    quiet: bool = False,
    progress_callback: Optional[Callable[[int, int, str], Awaitable[None]]] = None,
) -> List[DownloadResult]:
    """
    Download a Snapchat Spotlight, public story, story snap or highlight.

    Args:
        url:        Snapchat link (a short share link is followed first).
        output_dir: Directory to save the file(s).
        quiet:      Suppress console output.
        progress_callback:
            Async callable (downloaded_bytes, total_bytes, file_path)
            called after each chunk.

    Returns:
        List of DownloadResult objects (one per snap; at most MAX_ITEMS).
    """
    os.makedirs(output_dir, exist_ok=True)

    async with aiohttp.ClientSession(headers=SNAP_HEADERS) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            # A short link ends up on the snap's page
            url = str(resp.url)
            page = await resp.text()
        if not quiet:
            print(f"[*] {url}")

        if m := SPOTLIGHT_RE.match(url):
            items = _parse_spotlight(page, m.group("id"))
        elif m := HIGHLIGHT_RE.match(url):
            items = _parse_highlight(page)
        elif m := STORY_RE.match(url):
            items = _parse_story(page, m.group("user"), m.group("id"))
        else:
            raise ValueError(f"Not a supported Snapchat URL: {url}")
        if not items:
            raise RuntimeError("No downloadable media found (expired or private?)")

        for i, item in enumerate(items):
            base = os.path.join(output_dir, f"snap_{i + 1:02d}")
            dest = base + (".mp4" if item.media_type == "video" else "")
            if not quiet:
                print(f"\n[down] {dest}  ({item.media_type})")
            await _download_file(session, item.source_url, dest, progress_callback)
            if item.media_type == "image":
                ext = MIME_EXTS.get(magic.from_file(dest, mime=True), "jpg")
                os.rename(dest, f"{dest}.{ext}")
                dest = f"{dest}.{ext}"
            item.local_path = dest
            if not quiet:
                print(f"[ok] -> {dest}")
            if i < len(items) - 1:
                await asyncio.sleep(0.5)

    return items

import json
import re
from dataclasses import dataclass

import httpx

TRACK_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(?:embed/)?track/([A-Za-z0-9]{22})"
)
COLLECTION_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(?:embed/)?(playlist|album)/([A-Za-z0-9]{22})"
)
SHORT_RE = re.compile(r"https?://spotify(?:\.app)?\.link/[A-Za-z0-9]+")

NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)

WEB_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


@dataclass
class SpotifyTrack:
    title: str
    artists: list[str]


@dataclass
class SpotifyCollection:
    kind: str  # "playlist" or "album"
    name: str
    # open.spotify.com track links; the embed page lists at most 100
    tracks: list[str]


def is_valid_spotify_url(url: str) -> bool:
    """
    Returns True if the URL is a Spotify track, playlist or album link,
    or a Spotify short link.
    """
    return bool(
        TRACK_RE.search(url) or COLLECTION_RE.search(url) or SHORT_RE.search(url)
    )


def is_spotify_collection(url: str) -> bool:
    """Returns True if the URL is a Spotify playlist or album link."""
    return bool(COLLECTION_RE.search(url))


async def resolve_spotify_short_url(url: str) -> str:
    """Where a Spotify short link leads; other links are returned as they are."""
    if not SHORT_RE.search(url):
        return url
    async with httpx.AsyncClient(
        http2=True, timeout=20.0, follow_redirects=True, headers=WEB_HEADERS
    ) as client:
        resp = await client.get(url)
    return str(resp.url)


def _embed_entity(html: str, url: str) -> dict:
    data = NEXT_DATA_RE.search(html)
    if not data:
        raise ValueError(f"No data in Spotify embed page: {url}")
    try:
        return json.loads(data.group(1))["props"]["pageProps"]["state"]["data"][
            "entity"
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ValueError(f"Spotify item not found: {url}")


async def get_spotify_collection(url: str) -> SpotifyCollection:
    """
    Read a playlist's or album's name and tracks from Spotify's embed page
    (no login or API key needed). The page lists at most 100 tracks;
    episodes and local files in a playlist are left out.

    Raises:
        ValueError: The link isn't a playlist or album, or it wasn't found.
    """
    match = COLLECTION_RE.search(url)
    if not match:
        raise ValueError(f"Not a Spotify playlist or album link: {url}")
    kind, item_id = match.groups()
    async with httpx.AsyncClient(
        http2=True, timeout=20.0, follow_redirects=True, headers=WEB_HEADERS
    ) as client:
        resp = await client.get(f"https://open.spotify.com/embed/{kind}/{item_id}")
        resp.raise_for_status()

    entity = _embed_entity(resp.text, url)
    tracks = []
    for item in entity.get("trackList") or []:
        uri = item.get("uri") or ""
        if uri.startswith("spotify:track:"):
            tracks.append(f"https://open.spotify.com/track/{uri.split(':')[-1]}")
    return SpotifyCollection(
        kind=kind,
        name=entity.get("name") or entity.get("title") or kind.capitalize(),
        tracks=tracks,
    )


async def get_spotify_track(url: str) -> SpotifyTrack:
    """
    Read a track's title and artists from Spotify's embed page (no login or
    API key needed).

    Raises:
        ValueError: The link doesn't lead to a track, or the track wasn't found.
    """
    async with httpx.AsyncClient(
        http2=True, timeout=20.0, follow_redirects=True, headers=WEB_HEADERS
    ) as client:
        match = TRACK_RE.search(url)
        if not match:
            # short link: the track url is where the redirects end
            resp = await client.get(url)
            match = TRACK_RE.search(str(resp.url))
            if not match:
                raise ValueError(f"Not a Spotify track link: {url}")

        resp = await client.get(
            f"https://open.spotify.com/embed/track/{match.group(1)}"
        )
        resp.raise_for_status()

    entity = _embed_entity(resp.text, url)

    title = entity.get("name") or entity.get("title")
    if not title:
        raise ValueError(f"Spotify track has no title: {url}")
    return SpotifyTrack(
        title=title,
        artists=[a["name"] for a in entity.get("artists") or [] if a.get("name")],
    )

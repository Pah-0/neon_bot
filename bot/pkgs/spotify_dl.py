import json
import re
from dataclasses import dataclass

import httpx

TRACK_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?(?:embed/)?track/([A-Za-z0-9]{22})"
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


def is_valid_spotify_url(url: str) -> bool:
    """Returns True if the URL is a Spotify track link or a Spotify short link."""
    return bool(TRACK_RE.search(url) or SHORT_RE.search(url))


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

    data = NEXT_DATA_RE.search(resp.text)
    if not data:
        raise ValueError(f"No track data in Spotify embed page: {url}")
    try:
        entity = json.loads(data.group(1))["props"]["pageProps"]["state"]["data"][
            "entity"
        ]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise ValueError(f"Spotify track not found: {url}")

    title = entity.get("name") or entity.get("title")
    if not title:
        raise ValueError(f"Spotify track has no title: {url}")
    return SpotifyTrack(
        title=title,
        artists=[a["name"] for a in entity.get("artists") or [] if a.get("name")],
    )

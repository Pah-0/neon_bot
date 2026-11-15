"""
Lookups for manga and light novels (AniList), films and TV series
(Cinemeta, Stremio's open metadata API, with IMDb ids and ratings) and web
novels (MangaUpdates' open API). None of them needs an API key.
"""

import html
import re
from urllib.parse import quote

import flag

from bot import bot

ANILIST = "https://graphql.anilist.co"
CINEMETA = "https://v3-cinemeta.strem.io"
MANGAUPDATES = "https://api.mangaupdates.com/v1"
HEADERS = {"User-Agent": "neon_bot/1.0"}
# AniList formats for .manga and .manga -ln
MANGA_FORMATS = ["MANGA", "ONE_SHOT"]
NOVEL_FORMATS = ["NOVEL"]
MAX_RESULTS = 12
MAX_SYNOPSIS = 700

manga_search_query = """
query ($search: String, $formats: [MediaFormat]) {
  Page (perPage: 12) {
    media (search: $search, type: MANGA, format_in: $formats) {
      id
      title { romaji english }
      startDate { year }
    }
  }
}
"""

manga_query = """
query ($id: Int, $idMal: Int) {
  Media (id: $id, idMal: $idMal, type: MANGA) {
    id
    idMal
    title { romaji english native }
    format
    status
    description (asHtml: false)
    startDate { year month day }
    chapters
    volumes
    countryOfOrigin
    coverImage { extraLarge }
    genres
    averageScore
    isAdult
    staff (sort: RELEVANCE, perPage: 3) {
      edges { role node { name { full } } }
    }
    siteUrl
  }
}
"""


def plain(text: str | None, limit: int = MAX_SYNOPSIS) -> str:
    """Description text without HTML, shortened to limit"""
    if not text:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    # Markdown (MangaUpdates): bold as WhatsApp bold, links as their text
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # AniList's notes after the synopsis are often long lists
    text = re.split(r"(?m)^\s*Notes?:", text)[0]
    text = "\n".join(line.strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0] + "…"
    return text


def pretty(value: str | None) -> str:
    """An API constant like RELEASING as Releasing"""
    return value.replace("_", " ").capitalize() if value else "N/A"


async def get_json(url: str, **kwargs):
    method = bot.requests.post if "json" in kwargs else bot.requests.get
    async with method(url, headers=HEADERS, **kwargs) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def anilist(query: str, variables: dict) -> dict:
    result = await get_json(ANILIST, json={"query": query, "variables": variables})
    if errors := result.get("errors"):
        raise Exception(errors[0].get("message"))
    return result["data"]


async def search_manga(query: str, novel: bool = False) -> list[tuple[str, str]]:
    """(AniList id, label) for each manga, or light novel if novel"""
    formats = NOVEL_FORMATS if novel else MANGA_FORMATS
    data = await anilist(manga_search_query, {"search": query, "formats": formats})
    results = []
    for media in data["Page"]["media"]:
        title = media["title"]["english"] or media["title"]["romaji"]
        year = media["startDate"]["year"]
        results.append((str(media["id"]), f"{title} ({year})" if year else title))
    return results


async def manga_info(media_id: str, mal: bool = False) -> tuple[str, str]:
    """(cover URL, caption) for a manga or light novel"""
    key = "idMal" if mal else "id"
    media = (await anilist(manga_query, {key: int(media_id)}))["Media"]
    title = media["title"]
    country = media.get("countryOfOrigin")
    lines = [f"[{flag.flag(country) if country else '?'}] *{title['romaji']}*"]
    if title["english"] and title["english"] != title["romaji"]:
        lines.append(f"_{title['english']}_")
    if title["native"]:
        lines.append(title["native"])
    lines.append("")
    lines.append(f"*ID | MAL ID:* {media['id']} | {media['idMal'] or 'N/A'}")
    lines.append(f"➤ *TYPE:* {pretty(media['format'])}")
    lines.append(f"➤ *STATUS:* {pretty(media['status'])}")
    if media["genres"]:
        lines.append(f"➤ *GENRES:* {', '.join(media['genres'])}")
    start = media["startDate"]
    if start["year"]:
        date = "/".join(str(start[k]) for k in ("day", "month", "year") if start[k])
        lines.append(f"➤ *STARTED:* {date}")
    if media["chapters"] or media["volumes"]:
        lines.append(
            f"➤ *CHAPTERS | VOLUMES:* {media['chapters'] or '?'} | "
            f"{media['volumes'] or '?'}"
        )
    staff = [
        f"{e['node']['name']['full']} ({e['role']})" for e in media["staff"]["edges"]
    ]
    if staff:
        lines.append(f"➤ *STAFF:* {', '.join(staff)}")
    if media["averageScore"]:
        lines.append(f"➤ *SCORE:* {media['averageScore']}% 🌟")
    lines.append(f"➤ *ADULT RATED:* {media['isAdult']}")
    if synopsis := plain(media["description"]):
        lines.append(f"\n{synopsis}")
    lines.append(f"\n{media['siteUrl']}")
    return media["coverImage"]["extraLarge"], "\n".join(lines)


async def search_titles(query: str, kinds: tuple[str, ...]) -> list[tuple[str, str]]:
    """
    ("type:IMDb id", label) for each film or series; kinds holds "movie",
    "series" or both
    """
    results = []
    for kind in kinds:
        url = f"{CINEMETA}/catalog/{kind}/top/search={quote(query)}.json"
        for meta in (await get_json(url)).get("metas", []):
            label = "Film" if kind == "movie" else "Series"
            year = meta.get("releaseInfo")
            name = f"{meta['name']} ({year})" if year else meta["name"]
            results.append((f"{kind}:{meta['id']}", f"{name} · {label}"))
    if len(kinds) > 1:
        # Alternate films and series so both show in a short poll
        movies = [r for r in results if r[0].startswith("movie:")]
        series = [r for r in results if r[0].startswith("series:")]
        results = [r for pair in zip(movies, series) for r in pair]
        longer = movies if len(movies) > len(series) else series
        results += longer[min(len(movies), len(series)) :]
    return results[:MAX_RESULTS]


async def title_info(key: str) -> tuple[str | None, str]:
    """(poster URL, caption) for "type:IMDb id", or a bare IMDb id"""
    kinds = [key.split(":")[0]] if ":" in key else ["movie", "series"]
    imdb_id = key.split(":")[-1]
    meta = None
    for kind in kinds:
        meta = (await get_json(f"{CINEMETA}/meta/{kind}/{imdb_id}.json")).get("meta")
        if meta:
            break
    if not meta:
        raise Exception(f"Nothing on IMDb has the id {imdb_id}.")
    series = meta["type"] == "series"
    year = meta.get("releaseInfo") or meta.get("year")
    lines = [f"*{meta['name']}*" + (f" ({year})" if year else ""), ""]
    lines.append(f"➤ *TYPE:* {'Series' if series else 'Film'}")
    if meta.get("imdbRating"):
        lines.append(f"➤ *IMDb RATING:* {meta['imdbRating']}/10 ⭐")
    if meta.get("genres"):
        lines.append(f"➤ *GENRES:* {', '.join(meta['genres'])}")
    if meta.get("runtime"):
        lines.append(f"➤ *RUNTIME:* {meta['runtime']}")
    if series and meta.get("status"):
        lines.append(f"➤ *STATUS:* {meta['status']}")
    if meta.get("country"):
        lines.append(f"➤ *COUNTRY:* {meta['country']}")
    for field, name in (("director", "DIRECTOR"), ("writer", "WRITER")):
        if people := meta.get(field):
            lines.append(f"➤ *{name}:* {', '.join(people)}")
    if meta.get("cast"):
        lines.append(f"➤ *CAST:* {', '.join(meta['cast'])}")
    if meta.get("awards"):
        lines.append(f"➤ *AWARDS:* {meta['awards']}")
    trailer = next(
        (t["source"] for t in meta.get("trailers") or [] if t.get("source")), None
    )
    if trailer:
        lines.append(f"🎬 *Trailer:* https://youtu.be/{trailer}")
    if synopsis := plain(meta.get("description")):
        lines.append(f"\n{synopsis}")
    lines.append(f"\nhttps://www.imdb.com/title/{imdb_id}/")
    poster = meta.get("poster")
    if poster:
        # The search gives the small poster; the medium one is sharper
        poster = poster.replace("/poster/small/", "/poster/medium/")
    return poster, "\n".join(lines)


async def search_novels(query: str) -> list[tuple[str, str]]:
    """(MangaUpdates id, label) for each novel"""
    result = await get_json(
        f"{MANGAUPDATES}/series/search",
        json={"search": query, "type": ["Novel"], "perpage": MAX_RESULTS},
    )
    results = []
    # The API ignores perpage, so the list is cut here
    for hit in result.get("results", [])[:MAX_RESULTS]:
        record = hit["record"]
        title = html.unescape(record["title"]).removesuffix(" (Novel)")
        year = record.get("year")
        results.append(
            (str(record["series_id"]), f"{title} ({year})" if year else title)
        )
    return results


async def novel_info(series_id: str) -> tuple[str | None, str]:
    """(cover URL, caption) for a MangaUpdates novel"""
    series = await get_json(f"{MANGAUPDATES}/series/{series_id}")
    title = html.unescape(series["title"]).removesuffix(" (Novel)")
    lines = [f"*{title}*" + (f" ({series['year']})" if series.get("year") else "")]
    lines.append("")
    if status := plain(series.get("status"), 300):
        parts = [" ".join(line.split()) for line in status.splitlines()]
        lines.append(f"➤ *STATUS:* {'; '.join(p for p in parts if p)}")
    lines.append(f"➤ *COMPLETED:* {'Yes' if series.get('completed') else 'No'}")
    if series.get("genres"):
        lines.append(f"➤ *GENRES:* {', '.join(g['genre'] for g in series['genres'])}")
    if authors := series.get("authors"):
        names = dict.fromkeys(a["name"] for a in authors)
        lines.append(f"➤ *AUTHOR:* {', '.join(names)}")
    if publishers := series.get("publishers"):
        names = dict.fromkeys(p["publisher_name"] for p in publishers)
        lines.append(f"➤ *PUBLISHER:* {', '.join(list(names)[:3])}")
    lines.append(
        f"➤ *LICENSED IN ENGLISH:* {'Yes' if series.get('licensed') else 'No'}"
    )
    if series.get("bayesian_rating"):
        votes = series.get("rating_votes")
        lines.append(
            f"➤ *RATING:* {series['bayesian_rating']}/10 ⭐"
            + (f" ({votes} votes)" if votes else "")
        )
    if synopsis := plain(series.get("description")):
        lines.append(f"\n{synopsis}")
    lines.append(f"\n{series['url']}")
    image = (series.get("image") or {}).get("url", {}).get("original")
    return image, "\n".join(lines)

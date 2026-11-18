"""
Builds the sticker pack the bot downloads instead of fetching each reply
sticker from its host: every URL in bot/fun/stickers.py is downloaded once,
kept if it's a real image, and stored under the name the bot's sticker cache
uses (the SHA-1 of the URL). Run by .github/workflows/stickers.yml.

Usage: python scripts/build_sticker_pack.py <out.zip>
"""

import hashlib
import io
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

STICKERS = Path(__file__).resolve().parent.parent / "bot" / "fun" / "stickers.py"
# Hosts that answer 429 to a dozen requests at once; fetched one at a time
SLOW_HOSTS = {"upload.wikimedia.org"}
USER_AGENT = "neon_bot sticker mirror (+https://github.com/Pah-0/neon_bot)"


def fetch(url: str, tries: int = 5) -> tuple[str, bytes | None, str]:
    """
    (url, image bytes or None, why it was left out); a failed download is
    tried again, since hosts time out now and then, and a host that asks us
    to slow down (429) is given the time it asks for
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    why = ""
    for attempt in range(tries):
        wait = 2 * (attempt + 1)
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                data = resp.read()
            Image.open(io.BytesIO(data)).verify()
            return url, data, ""
        except urllib.error.HTTPError as e:
            why = str(e)
            if e.code == 429:
                retry_after = e.headers.get("Retry-After", "")
                asked = int(retry_after) if retry_after.isdigit() else 0
                wait = min(max(asked, 15 * (attempt + 1)), 60)
        except Exception as e:
            why = str(e)
        if attempt < tries - 1:
            time.sleep(wait)
    return url, None, why


def fetch_one_by_one(urls: list[str]) -> list[tuple[str, bytes | None, str]]:
    """fetch() for hosts that refuse many requests at once, with a pause between"""
    results = []
    for url in urls:
        results.append(fetch(url))
        time.sleep(1)
    return results


def main(out: str) -> int:
    # The sticker lists' URLs, not the pack's own address
    source = STICKERS.read_text()
    urls = re.findall(r'"(https?://[^"]+)"', source)
    urls = [url for url in dict.fromkeys(urls) if "/releases/download/" not in url]
    slow = [url for url in urls if urlparse(url).hostname in SLOW_HOSTS]
    fast = [url for url in urls if url not in slow]
    with ThreadPoolExecutor(max_workers=12) as pool:
        slow_results = pool.submit(fetch_one_by_one, slow)
        results = list(pool.map(fetch, fast)) + slow_results.result()
    kept = [(url, data) for url, data, _ in results if data]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as pack:
        for url, data in kept:
            pack.writestr(hashlib.sha1(url.encode()).hexdigest(), data)
    failed = [(url, why) for url, data, why in results if not data]
    print(f"{len(kept)} of {len(urls)} stickers packed into {out}")
    for url, why in failed:
        print(f"left out: {url} ({why})")
    return 0 if kept else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "stickers.zip"))

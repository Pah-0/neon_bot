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
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

STICKERS = Path(__file__).resolve().parent.parent / "bot" / "fun" / "stickers.py"
USER_AGENT = "neon_bot sticker mirror (+https://github.com/Pah-0/neon_bot)"


def fetch(url: str) -> tuple[str, bytes | None, str]:
    """(url, image bytes or None, why it was left out)"""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = resp.read()
        Image.open(io.BytesIO(data)).verify()
    except Exception as e:
        return url, None, str(e)
    return url, data, ""


def main(out: str) -> int:
    # Only the URLs in ran_stick()'s list, not the pack's own address
    source = STICKERS.read_text()
    listing = source[source.index("stickers = (") : source.index("\n    )\n")]
    urls = list(dict.fromkeys(re.findall(r'"(https?://[^"]+)"', listing)))
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(fetch, urls))
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

"""
Game data lookups for .search: Genshin Impact from Project Amber
(gi.yatta.moe), Honkai: Star Rail from Project Yatta (sr.yatta.moe) and
Zenless Zone Zero from Hakushin (static.nanoka.cc).
"""

import asyncio
import difflib
import io
import re
import time

import aiohttp
from PIL import Image

from .bot_utils import sync_to_async

GI_API = "https://gi.yatta.moe/api/v2/en/"
GI_ASSETS = "https://gi.yatta.moe/assets/UI/"
HSR_API = "https://sr.yatta.moe/api/v2/en/"
HSR_ASSETS = "https://sr.yatta.moe/hsr/assets/UI/"
ZZZ_DATA = "https://static.nanoka.cc/"
ZZZ_ASSETS = "https://static.nanoka.cc/assets/zzz/"

GAMES = {"gi": "Genshin Impact", "hsr": "Honkai: Star Rail", "zzz": "Zenless Zone Zero"}
KINDS = {
    "gi": {"character": "Character", "weapon": "Weapon", "set": "Artifact Set"},
    "hsr": {"character": "Character", "weapon": "Light Cone", "set": "Relic Set"},
    "zzz": {"character": "Agent", "weapon": "W-Engine", "set": "Drive Disc"},
}
# Sections .search can add with a flag, per game: flag -> title
SECTIONS = {
    "gi": {"t": "Talents", "c": "Constellations"},
    "hsr": {"t": "Traces", "c": "Eidolons"},
    "zzz": {"t": "Skills", "c": "Mindscapes"},
}
# Names change with patches; a few hours is fresh enough
CACHE_TTL = 6 * 60 * 60
TIMEOUT = aiohttp.ClientTimeout(total=60)

# Item art goes on its rarity's colour, as in-game (Genshin_bot's palette)
RARITY_COLOURS = {
    1: (126, 126, 128),
    2: (78, 126, 110),
    3: (84, 134, 169),
    4: (127, 103, 161),
    5: (176, 112, 48),
}
GI_ELEMENTS = {
    "Fire": "Pyro",
    "Water": "Hydro",
    "Wind": "Anemo",
    "Electric": "Electro",
    "Grass": "Dendro",
    "Ice": "Cryo",
    "Rock": "Geo",
}
GI_REGIONS = {
    "MONDSTADT": "Mondstadt",
    "LIYUE": "Liyue",
    "INAZUMA": "Inazuma",
    "SUMERU": "Sumeru",
    "FONTAINE": "Fontaine",
    "NATLAN": "Natlan",
    "NODKRAI": "Nod-Krai",
    "SNEZHNAYA": "Snezhnaya",
    "FATUI": "Snezhnaya",
}
HSR_PATHS = {
    "Warrior": "Destruction",
    "Rogue": "The Hunt",
    "Mage": "Erudition",
    "Shaman": "Harmony",
    "Warlock": "Nihility",
    "Knight": "Preservation",
    "Priest": "Abundance",
    "Memory": "Remembrance",
}
ZZZ_RANKS = {2: "B", 3: "A", 4: "S"}
# Item colour for a ZZZ rank: B blue, A purple, S gold
ZZZ_RANK_RARITY = {2: 3, 3: 4, 4: 5}
ZZZ_BUTTONS = {
    "Icon_Normal": "Basic Attack",
    "Icon_Evade": "Dodge",
    "Icon_Special": "Special Attack",
    "Icon_SpecialReady": "EX Special Attack",
    "Icon_UltimateReady": "Ultimate",
    "Icon_Switch": "Switch",
}

_cache = {"time": 0.0, "index": None, "zzz_version": None}
_lock = asyncio.Lock()


async def get_json(session: aiohttp.ClientSession, url: str):
    async with session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


def clean(text: str | None) -> str:
    """Game markup to plain text"""
    if not text:
        return ""
    text = text.replace("\\n", "\n")
    # Genshin: {LAYOUT_MOBILE#Tap}{LAYOUT_PC#Press}{LAYOUT_PS#Press}
    text = re.sub(
        r"\{LAYOUT_MOBILE#[^}]*\}\{LAYOUT_PC#([^}]*)\}\{LAYOUT_PS#[^}]*\}", r"\1", text
    )
    text = re.sub(r"\{LINK#[^}]*\}|\{/LINK\}", "", text)
    text = re.sub(
        r"<IconMap:([^>]+)>",
        lambda m: ZZZ_BUTTONS.get(m.group(1), m.group(1).removeprefix("Icon_")),
        text,
    )
    text = re.sub(r"</?(color|unbreak|i|b|u|size)[^>]*>", "", text)
    return text.strip()


# A number, or numbers joined by slashes like 8/16/28%
_number = re.compile(r"\d+(?:\.\d+)?%?(?:/\d+(?:\.\d+)?%?)*")
# Numbers, words and the space or punctuation between them
_token = re.compile(r"\d+(?:\.\d+)?%?(?:/\d+(?:\.\d+)?%?)*|\w+|\W+")


def merge_levels(texts: list[str]) -> str:
    """
    One text from a passive's text at each refinement or superimposition,
    the numbers that change written *R1/R2/R3/R4/R5*.
    """
    texts = [clean(t) for t in texts if t]
    if not texts:
        return ""
    if len(set(texts)) == 1:
        return texts[0]
    # Line the other levels' words up with the first's; a number that one
    # level has in place of another's gets its value for that level
    base = _token.findall(texts[0])
    values = {i: [t] for i, t in enumerate(base) if _number.fullmatch(t)}
    for text in texts[1:]:
        other = _token.findall(text)
        matcher = difflib.SequenceMatcher(None, base, other, autojunk=False)
        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            for offset in range(i2 - i1):
                i = i1 + offset
                if i not in values:
                    continue
                j = j1 + offset
                same = op == "equal"
                swapped = op == "replace" and i2 - i1 == j2 - j1
                if (same or swapped) and _number.fullmatch(other[j]):
                    values[i].append(other[j])
                else:
                    values[i].append(None)
    for i, found in values.items():
        if None in found or len(set(found)) == 1:
            continue
        if any("/" in v for v in found):
            # Genshin_bot's way: (8/16/28%)/(10/20/35%)/…
            base[i] = "*" + "/".join(f"({v})" for v in found) + "*"
            continue
        percent = all(v.endswith("%") for v in found)
        found = [v.rstrip("%") for v in found]
        base[i] = f"*{'/'.join(found)}{'%' if percent else ''}*"
    return "".join(base)


def _format_param(value: float, fmt: str, percent: bool) -> str:
    if percent:
        value *= 100
    if fmt.startswith("f"):
        number = f"{value:.{int(fmt[1:] or 0)}f}"
    else:
        number = str(round(value))
    return number + ("%" if percent else "")


def fill_params(text: str | None, params: list) -> str:
    """HSR text with #1[i]% style placeholders filled from params"""

    def repl(m):
        index, fmt, percent = int(m.group(1)), m.group(2), bool(m.group(3))
        if index > len(params) or params[index - 1] is None:
            return m.group(0)
        return _format_param(params[index - 1], fmt, percent)

    return clean(re.sub(r"#(\d+)\[(i|f\d*)\](%?)", repl, text or ""))


def short(text: str | None, limit: int = 400) -> str:
    """Lore cut to its first paragraphs or sentences within limit"""
    text = clean(text)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind("\n"), cut.rfind(". "))
    return (cut[: end + 1] if end > limit // 3 else cut).rstrip() + "…"


def stars(rarity: int) -> str:
    return "⭐" * rarity


def label(entry: dict) -> str:
    """How an entry is shown in result lists"""
    extra = f" ({entry['note']})" if entry.get("note") else ""
    return f"{entry['name']}{extra}"


async def _gi_index(session) -> list[dict]:
    avatars, weapons, sets = await asyncio.gather(
        get_json(session, GI_API + "avatar"),
        get_json(session, GI_API + "weapon"),
        get_json(session, GI_API + "reliquary"),
    )
    entries = []
    for key, char in avatars["data"]["items"].items():
        note = None
        if char["name"] == "Traveler":
            if not key.startswith("10000007"):
                # Aether and Lumine share everything but their art
                continue
            note = GI_ELEMENTS.get(char["element"], char["element"])
        entries.append(
            {
                "kind": "character",
                "id": key,
                "name": char["name"],
                "rarity": char["rank"],
                "note": note,
            }
        )
    seen = set()
    for key, weapon in weapons["data"]["items"].items():
        if weapon["name"] in seen:
            continue
        seen.add(weapon["name"])
        entries.append(
            {
                "kind": "weapon",
                "id": key,
                "name": weapon["name"],
                "rarity": weapon["rank"],
            }
        )
    for key, item in sets["data"]["items"].items():
        entries.append(
            {
                "kind": "set",
                "id": key,
                "name": item["name"],
                "rarity": max(item.get("levelList") or [5]),
            }
        )
    return entries


async def _hsr_index(session) -> list[dict]:
    avatars, cones, relics = await asyncio.gather(
        get_json(session, HSR_API + "avatar"),
        get_json(session, HSR_API + "equipment"),
        get_json(session, HSR_API + "relic"),
    )
    entries = []
    chars = avatars["data"]["items"]
    names = [c["name"] for c in chars.values()]
    seen = set()
    for key, char in chars.items():
        types = char["types"]
        note = None
        if names.count(char["name"]) > 1:
            path = HSR_PATHS.get(types["pathType"], types["pathType"])
            note = f"{types['combatType']} · {path}"
            if (char["name"], note) in seen:
                # The Trailblazer comes as a pair, one per gender
                continue
            seen.add((char["name"], note))
        entries.append(
            {
                "kind": "character",
                "id": key,
                "name": char["name"],
                "rarity": char["rank"],
                "note": note,
            }
        )
    for key, cone in cones["data"]["items"].items():
        entries.append(
            {"kind": "weapon", "id": key, "name": cone["name"], "rarity": cone["rank"]}
        )
    for key, relic in relics["data"]["items"].items():
        note = "Planar Ornament" if relic.get("isPlanarSuit") else None
        entries.append(
            {
                "kind": "set",
                "id": key,
                "name": relic["name"],
                "rarity": max(relic.get("levelList") or [5]),
                "note": note,
            }
        )
    return entries


async def _zzz_index(session) -> list[dict]:
    manifest = await get_json(session, ZZZ_DATA + "manifest.json")
    # The live version leaves out leaks from upcoming patches
    version = manifest["zzz"]["live"]
    _cache["zzz_version"] = version
    base = f"{ZZZ_DATA}zzz/{version}/"
    chars, weapons, discs = await asyncio.gather(
        get_json(session, base + "character.json"),
        get_json(session, base + "weapon.json"),
        get_json(session, base + "equipment.json"),
    )
    entries = []
    for key, char in chars.items():
        if char.get("en"):
            entries.append(
                {
                    "kind": "character",
                    "id": key,
                    "name": char["en"],
                    "rarity": ZZZ_RANK_RARITY.get(char["rank"], 4),
                }
            )
    for key, weapon in weapons.items():
        if weapon.get("en"):
            entries.append(
                {
                    "kind": "weapon",
                    "id": key,
                    "name": weapon["en"],
                    "rarity": ZZZ_RANK_RARITY.get(weapon["rank"], 4),
                }
            )
    for key, disc in discs.items():
        name = (disc.get("en") or {}).get("name")
        if name:
            entries.append({"kind": "set", "id": key, "name": name, "rarity": 5})
    return entries


async def get_index() -> list[dict]:
    """Every searchable entry of the three games, cached for CACHE_TTL"""
    async with _lock:
        if _cache["index"] and time.time() - _cache["time"] < CACHE_TTL:
            return _cache["index"]
        index = []
        async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
            results = await asyncio.gather(
                _gi_index(session),
                _hsr_index(session),
                _zzz_index(session),
                return_exceptions=True,
            )
        for game, result in zip(GAMES, results):
            if isinstance(result, Exception):
                if _cache["index"]:
                    # Keep the last good copy of a game whose source is down
                    index += [e for e in _cache["index"] if e["game"] == game]
                continue
            index += [{**e, "game": game} for e in result]
        if not index:
            raise results[0]
        _cache["index"], _cache["time"] = index, time.time()
        return index


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def search(
    index: list[dict], query: str, game: str | None = None, kind: str | None = None
) -> list[dict]:
    """Entries matching query, best first: exact, then prefix, words, close spellings"""
    query_n = _normalise(query)
    if not query_n:
        return []
    entries = [
        e
        for e in index
        if (not game or e["game"] == game) and (not kind or e["kind"] == kind)
    ]
    exact, prefix, contains = [], [], []
    for entry in entries:
        name = _normalise(entry["name"])
        if name == query_n or _normalise(label(entry)) == query_n:
            exact.append(entry)
        elif name.startswith(query_n):
            prefix.append(entry)
        elif query_n in name or all(_normalise(w) in name for w in query.split()):
            contains.append(entry)
    if exact:
        return exact
    if prefix or contains:
        return prefix + contains
    names = {_normalise(e["name"]): e for e in entries}
    close = difflib.get_close_matches(query_n, names, n=8, cutoff=0.7)
    return [names[n] for n in close]


async def download(session: aiohttp.ClientSession, url: str) -> bytes | None:
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            return await resp.read()
    except aiohttp.ClientError:
        return None


def _compose(raws: list[bytes], rarity: int, width: int = 1280) -> bytes:
    """
    Images side by side on the rarity colour, as a JPEG at most width wide;
    a single image keeps its own shape (Genshin_bot's artifact layout).
    """
    images = [Image.open(io.BytesIO(r)).convert("RGBA") for r in raws]
    colour = RARITY_COLOURS.get(rarity, RARITY_COLOURS[5])
    if len(images) == 1:
        image = images[0]
        image.thumbnail((width, width), Image.Resampling.LANCZOS)
        background = Image.new("RGBA", image.size, colour)
        background.alpha_composite(image)
    else:
        height = 400
        size = int(height * 0.8)
        images = [
            i.resize((int(size * i.width / i.height), size), Image.Resampling.LANCZOS)
            for i in images
        ]
        gap = 30
        total = sum(i.width for i in images) + gap * (len(images) + 1)
        background = Image.new("RGBA", (max(total, 800), height), colour)
        x = (background.width - total) // 2 + gap
        for image in images:
            background.alpha_composite(image, (x, (height - size) // 2))
            x += image.width + gap
    output = io.BytesIO()
    background.convert("RGB").save(output, format="jpeg", quality=90)
    return output.getvalue()


async def make_image(session, urls: list[str], rarity: int) -> bytes | None:
    """The first URL that loads (or every one, for multi-piece sets) composed"""
    if not urls:
        return None
    raws = [r for r in await asyncio.gather(*(download(session, u) for u in urls)) if r]
    if not raws:
        return None
    return await sync_to_async(_compose, raws, rarity)


# Each detail builder returns (image URLs, multi-piece?, caption, {flag: text})


async def _gi_character(session, entry) -> tuple:
    char_id = entry["id"]
    data = (await get_json(session, f"{GI_API}avatar/{char_id}"))["data"]
    listing = (await get_json(session, GI_API + "avatar"))["data"]
    props, types = listing.get("props", {}), listing.get("types", {})
    fetter = data.get("fetter") or {}
    icon = data["icon"].removeprefix("UI_AvatarIcon_")
    element = GI_ELEMENTS.get(data["element"], data["element"])
    region = GI_REGIONS.get(data.get("region") or "", "")
    affiliation = (fetter.get("native") or "").strip("- ")
    month, day = (data.get("birthday") or [0, 0])[:2]

    caption = (
        f"*{label(entry)}*"
        + (f" · _{fetter['title']}_" if fetter.get("title") else "")
        + "\n"
    )
    caption += f"{stars(data['rank'])}\n\n"
    caption += f"*Element:* {element}\n"
    caption += f"*Weapon:* {
        types.get(
            data['weaponType'],
            data['weaponType'])}\n"
    if region or affiliation:
        caption += f"*Region:* {
            ' · '.join(
                x for x in (
                    region,
                    affiliation) if x)}\n"
    if data.get("specialProp"):
        caption += (
            f"*Ascension stat:* {props.get(data['specialProp'], data['specialProp'])}\n"
        )
    if fetter.get("constellation"):
        caption += f"*Constellation:* {fetter['constellation']}\n"
    if month:
        caption += f"*Birthday:* {
            time.strftime(
                '%B',
                time.strptime(
                    str(month),
                    '%m'))} {day}\n"
    if fetter.get("detail"):
        caption += f"> {short(fetter['detail'])}\n"

    talents = [t for t in (data.get("talent") or {}).values() if t.get("name")]
    consts = list((data.get("constellation") or {}).values())
    active = [t for t in talents if t.get("type") in (0, 1)]
    if active:
        caption += f"\n*Talents:* {', '.join(t['name'] for t in active)}\n"
    if consts:
        caption += (
            "*Constellations:* "
            + ", ".join(f"C{i} {c['name']}" for i, c in enumerate(consts, 1))
            + "\n"
        )

    sections = {
        "t": "\n\n".join(
            f"*{t['name']}*\n{clean(t.get('description'))}" for t in talents
        ),
        "c": "\n\n".join(
            f"*C{i} · {c['name']}*\n{clean(c.get('description'))}"
            for i, c in enumerate(consts, 1)
        ),
    }
    urls = [
        f"{GI_ASSETS}UI_Gacha_AvatarImg_{icon}.png",
        f"{GI_ASSETS}{data['icon']}.png",
    ]
    return urls, False, caption, sections


async def _gi_weapon(session, entry) -> tuple:
    data, curves = await asyncio.gather(
        get_json(session, f"{GI_API}weapon/{entry['id']}"),
        get_json(session, "https://gi.yatta.moe/api/v2/static/weaponCurve"),
    )
    data, curves = data["data"], curves["data"]
    listing = (await get_json(session, GI_API + "avatar"))["data"]
    props, types = listing.get("props", {}), listing.get("types", {})
    upgrade = data["upgrade"]
    max_level = upgrade["promote"][-1].get("unlockMaxLevel", 90)
    curve = curves[str(max_level)]["curveInfos"]
    promote_add = upgrade["promote"][-1].get("addProps") or {}

    caption = f"*{entry['name']}*\n{stars(data['rank'])}\n\n"
    caption += f"*Type:* {types.get(data['type'], data['type'])}\n"
    for prop in upgrade["prop"]:
        if not prop.get("propType"):
            continue
        base = prop["initValue"]
        top = base * curve.get(prop["type"], 1) + promote_add.get(prop["propType"], 0)
        if prop["propType"] == "FIGHT_PROP_BASE_ATTACK":
            name = "Base ATK"
        else:
            name = props.get(prop["propType"], prop["propType"])
        if prop["propType"] == "FIGHT_PROP_BASE_ATTACK":
            caption += f"*{name}:* {
                round(base)} ➜ {
                round(top)} _(Lv. {max_level})_\n"
        elif base < 1:
            caption += (
                f"*{name}:* {base * 100:.1f}% ➜ {top * 100:.1f}% _(Lv. {max_level})_\n"
            )
        else:
            caption += f"*{name}:* {
                round(base)} ➜ {
                round(top)} _(Lv. {max_level})_\n"
    if data.get("description"):
        caption += f"> {short(data['description'])}\n"
    for affix in (data.get("affix") or {}).values():
        texts = [affix["upgrade"][k] for k in sorted(affix["upgrade"], key=int)]
        caption += f"\n*{affix['name']}*\n{merge_levels(texts)}\n"
    icon = data["icon"]
    urls = [
        f"{GI_ASSETS}{icon.replace('UI_EquipIcon', 'UI_Gacha_EquipIcon')}.png",
        f"{GI_ASSETS}{icon}.png",
    ]
    return urls, False, caption, {}


async def _gi_set(session, entry) -> tuple:
    data = (await get_json(session, GI_API + "reliquary"))["data"]["items"][entry["id"]]
    affixes = [clean(a) for a in (data.get("affixList") or {}).values()]
    caption = f"*{entry['name']}*\n{stars(entry['rarity'])}\n\n"
    pieces = ("1-Pc",) if len(affixes) == 1 else ("2-Pc", "4-Pc")
    for piece, text in zip(pieces, affixes):
        caption += f"*{piece}:* {text}\n"
    # Flower, plume, sands, goblet, circlet; sets with only a circlet keep
    # just that
    urls = [
        f"{GI_ASSETS}reliquary/UI_RelicIcon_{entry['id']}_{n}.png"
        for n in (4, 2, 5, 1, 3)
    ]
    return urls, True, caption, {}


async def _hsr_character(session, entry) -> tuple:
    data = (await get_json(session, f"{HSR_API}avatar/{entry['id']}"))["data"]
    types = data["types"]
    fetter = data.get("fetter") or {}
    caption = f"*{entry['name']}*\n{stars(data['rank'])}\n\n"
    caption += f"*Path:* {types['pathType']['name']}\n"
    caption += f"*Type:* {types['combatType']['name']}\n"
    if fetter.get("faction"):
        caption += f"*Faction:* {fetter['faction']}\n"
    if fetter.get("description"):
        caption += f"> {short(fetter['description'])}\n"

    eidolons = sorted(
        (data.get("eidolons") or {}).values(), key=lambda e: e.get("rank", 0)
    )
    skills = []
    for point in (data.get("traces") or {}).get("mainSkills", {}).values():
        for skill in (point.get("skillList") or {}).values():
            if skill.get("name"):
                skills.append({**skill, "cap": point.get("maxLevel")})
    if skills:
        caption += f"\n*Abilities:* {', '.join(s['name'] for s in skills)}\n"
    if eidolons:
        caption += (
            "*Eidolons:* "
            + ", ".join(f"E{e['rank']} {e['name']}" for e in eidolons)
            + "\n"
        )

    def skill_text(skill):
        params = skill.get("params") or {}
        # Abilities at their highest level without Eidolon boosts
        level = skill.get("cap") or skill.get("maxLevel") or 1
        top = [
            values[min(level, len(values)) - 1] if values else None
            for _, values in sorted(params.items(), key=lambda p: int(p[0]))
        ]
        kind = f" _({skill['type']})_" if skill.get("type") else ""
        return f"*{
            skill['name']}*{kind}\n{
            fill_params(
                skill.get('description'),
                top)}"

    sections = {
        "t": "\n\n".join(skill_text(s) for s in skills),
        "c": "\n\n".join(
            f"*E{e['rank']} · {e['name']}*\n{fill_params(e.get('description'), e.get('params') or [])}"
            for e in eidolons
        ),
    }
    urls = [
        f"{HSR_ASSETS}avatar/large/{data['icon']}.png",
        f"{HSR_ASSETS}avatar/{data['icon']}.png",
    ]
    return urls, False, caption, sections


async def _hsr_weapon(session, entry) -> tuple:
    data = (await get_json(session, f"{HSR_API}equipment/{entry['id']}"))["data"]
    top = data["upgrade"][-1]
    level = top.get("maxLevel", 80)
    base, add = top["skillBase"], top["skillAdd"]

    def stat(k):
        return round(
            base.get(f"{k}Base", 0) + add.get(f"{k}Add", 0) * (level - 1)
        )  # noqa: E731

    caption = f"*{entry['name']}*\n{stars(data['rank'])}\n\n"
    caption += f"*Path:* {data['types']['pathType']['name']}\n"
    caption += f"*Lv. {level}:* HP {
        stat('hP')} · ATK {
        stat('attack')} · DEF {
            stat('defence')}\n"
    skill = data.get("skill") or {}
    if skill.get("name"):
        params = skill.get("params") or {}
        levels = max((len(v) for v in params.values()), default=1)
        texts = [
            fill_params(
                skill["description"],
                [
                    params.get(str(i), [None] * levels)[n]
                    for i in range(1, len(params) + 1)
                ],
            )
            for n in range(levels)
        ]
        caption += f"\n*{skill['name']}*\n{merge_levels(texts)}\n"
    if data.get("description"):
        caption += f"\n> {short(data['description'])}\n"
    urls = [
        f"{HSR_ASSETS}equipment/large/{data['icon']}.png",
        f"{HSR_ASSETS}equipment/medium/{data['icon']}.png",
    ]
    return urls, False, caption, {}


async def _hsr_set(session, entry) -> tuple:
    data = (await get_json(session, f"{HSR_API}relic/{entry['id']}"))["data"]
    caption = f"*{label(entry)}*\n{stars(entry['rarity'])}\n\n"
    for pieces, skill in sorted(
        (data.get("skillList") or {}).items(), key=lambda s: int(s[0])
    ):
        params = [
            v[0]
            for _, v in sorted(
                (skill.get("params") or {}).items(), key=lambda p: int(p[0])
            )
        ]
        caption += f"*{pieces}-Pc:* {
            fill_params(
                skill.get('description'),
                params)}\n"
    pieces = [
        p.get("icon") for p in (data.get("suite") or {}).values() if p.get("icon")
    ]
    urls = [f"{HSR_ASSETS}relic/{p}.png" for p in pieces] or [
        f"{HSR_ASSETS}relic/{data['icon']}.png"
    ]
    return urls, len(urls) > 1, caption, {}


async def _zzz_character(session, entry) -> tuple:
    version = _cache["zzz_version"]
    data = await get_json(
        session, f"{ZZZ_DATA}zzz/{version}/en/character/{entry['id']}.json"
    )
    partner = data.get("partner_info") or {}

    def first(d):
        return next(iter((d or {}).values()), "")  # noqa: E731

    rank = ZZZ_RANKS.get(data.get("rarity"), "")
    full_name = partner.get("full_name")
    caption = (
        f"*{entry['name']}*"
        + (f" ({full_name})" if full_name and full_name != entry["name"] else "")
        + "\n"
    )
    caption += f"*Rank {rank}*\n\n" if rank else "\n"
    caption += f"*Attribute:* {
        first(
            data.get('special_element_type')) or first(
            data.get('element_type'))}\n"
    caption += f"*Specialty:* {first(data.get('weapon_type'))}\n"
    if data.get("hit_type"):
        caption += f"*Attack type:* {first(data['hit_type'])}\n"
    if data.get("camp"):
        caption += f"*Faction:* {first(data['camp'])}\n"
    if partner.get("birthday"):
        caption += f"*Birthday:* {partner['birthday'].title()}\n"
    if partner.get("profile_desc"):
        caption += f"> {short(partner['profile_desc'])}\n"

    mindscapes = [
        m
        for _, m in sorted((data.get("talent") or {}).items(), key=lambda t: int(t[0]))
    ]
    if mindscapes:
        caption += (
            "\n*Mindscapes:* "
            + ", ".join(f"M{i} {m['name']}" for i, m in enumerate(mindscapes, 1))
            + "\n"
        )
    skills = []
    for group, skill in (data.get("skill") or {}).items():
        for part in skill.get("description") or []:
            if part.get("desc"):
                skills.append(f"*{part['name']}*\n{clean(part['desc'])}")
    sections = {
        "t": "\n\n".join(skills),
        "c": "\n\n".join(
            f"*M{i} · {m['name']}*\n{clean(m.get('desc'))}"
            for i, m in enumerate(mindscapes, 1)
        ),
    }
    # IconRole is the full-body portrait; Mindscape art is a cinema crop
    urls = [
        f"{ZZZ_ASSETS}{data['icon']}.webp",
        f"{ZZZ_ASSETS}Mindscape_{entry['id']}_3.webp",
    ]
    return urls, False, caption, sections


async def _zzz_weapon(session, entry) -> tuple:
    version = _cache["zzz_version"]
    data = await get_json(
        session, f"{ZZZ_DATA}zzz/{version}/en/weapon/{entry['id']}.json"
    )
    rank = ZZZ_RANKS.get(data.get("rarity"), "")
    caption = f"*{entry['name']}*\n" + (f"*Rank {rank}*\n\n" if rank else "\n")
    caption += (
        f"*Specialty:* {next(iter((data.get('weapon_type') or {}).values()), '')}\n"
    )
    levels, star_levels = data.get("level") or {}, data.get("stars") or {}
    if levels and star_levels:
        top_level = max(levels, key=int)
        level_rate = levels[top_level]["rate"] / 10000
        top_star = star_levels[max(star_levels, key=int)]
        base = data["base_property"]
        atk = base["value"] * (1 + level_rate + top_star["star_rate"] / 10000)
        line = f"{base['name']} {round(atk)}"
        rand = data.get("rand_property") or {}
        if rand.get("value"):
            value = rand["value"] * (1 + top_star["rand_rate"] / 10000)
            value = (
                f"{value / 100:g}%"
                if "%" in rand.get("format", "")
                else f"{round(value)}"
            )
            line += f" · {rand['name']} {value}"
        caption += f"*Lv. {int(top_level)}:* {line}\n"
    talents = [
        t
        for _, t in sorted((data.get("talents") or {}).items(), key=lambda t: int(t[0]))
    ]
    if talents:
        caption += f"\n*{talents[0]['name']}*\n{merge_levels([t.get('desc') for t in talents])}\n"
    if data.get("desc3"):
        caption += f"\n> {short(data['desc3'])}\n"
    icon = data["icon"].rsplit("/", 1)[-1].split(".", 1)[0]
    return [f"{ZZZ_ASSETS}{icon}.webp"], False, caption, {}


async def _zzz_set(session, entry) -> tuple:
    version = _cache["zzz_version"]
    data = await get_json(
        session, f"{ZZZ_DATA}zzz/{version}/en/equipment/{entry['id']}.json"
    )
    caption = f"*{entry['name']}*\n\n"
    caption += f"*2-Pc:* {clean(data.get('desc2'))}\n"
    caption += f"*4-Pc:* {clean(data.get('desc4'))}\n"
    if data.get("story"):
        caption += f"\n> {short(data['story'])}\n"
    icon = data["icon"].rsplit("/", 1)[-1].split(".", 1)[0]
    return [f"{ZZZ_ASSETS}{icon}.webp"], False, caption, {}


BUILDERS = {
    ("gi", "character"): _gi_character,
    ("gi", "weapon"): _gi_weapon,
    ("gi", "set"): _gi_set,
    ("hsr", "character"): _hsr_character,
    ("hsr", "weapon"): _hsr_weapon,
    ("hsr", "set"): _hsr_set,
    ("zzz", "character"): _zzz_character,
    ("zzz", "weapon"): _zzz_weapon,
    ("zzz", "set"): _zzz_set,
}


async def get_details(entry: dict) -> tuple[bytes | None, str, dict]:
    """(image, caption, {flag: section text}) for a search result"""
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        urls, multi, caption, sections = await BUILDERS[(entry["game"], entry["kind"])](
            session, entry
        )
        if multi:
            image = await make_image(session, urls, entry["rarity"])
        else:
            image = None
            for url in urls:
                if image := await make_image(session, [url], entry["rarity"]):
                    break
    caption += f"\n_{GAMES[entry['game']]} · {KINDS[entry['game']][entry['kind']]}_"
    return image, caption.strip(), {k: v for k, v in sections.items() if v}

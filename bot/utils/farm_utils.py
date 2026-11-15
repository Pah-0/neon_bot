import asyncio
import time
from datetime import datetime, timedelta, timezone

import aiohttp

from bot.config import bot, conf
from bot.workers.auto.schedule import scheduler2

from .log_utils import log
from .msg_utils import send_rss

AMBR_API = "https://gi.yatta.moe/api/v2/en/"
WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
# Server -> UTC offset; days roll over at 04:00 server time
REGIONS = {"asia": 8, "eu": 1, "na": -5}
REGION_NAMES = {"asia": "Asia/TW", "eu": "Europe", "na": "America"}
DEFAULT_REGION = "eu"
RESET_HOUR = 4
# Amber's data changes with game patches; a few hours is fresh enough
CACHE_TTL = 6 * 60 * 60
CITY_NAMES = {
    "MONDSTADT": "Mondstadt",
    "LIYUE": "Liyue",
    "INAZUMA": "Inazuma",
    "SUMERU": "Sumeru",
    "FONTAINE": "Fontaine",
    "NATLAN": "Natlan",
    "NODKRAI": "Nod-Krai",
    "SNEZHNAYA": "Snezhnaya",
}
TRAVELER_IDS = ("10000005", "10000007")

_cache = {"time": 0.0, "data": None}
_lock = asyncio.Lock()


async def _fetch(session: aiohttp.ClientSession, endpoint: str) -> dict:
    async with session.get(AMBR_API + endpoint) as resp:
        resp.raise_for_status()
        return (await resp.json(content_type=None))["data"]


def _series_name(materials: dict, reward_ids: list) -> str:
    """'Freedom' from Teachings of Freedom, or the lowest weapon material's name"""
    items = [materials[str(i)] for i in reward_ids if str(i) in materials]
    items = [
        i
        for i in items
        if i.get("type") in ("characterTalentMaterial", "weaponAscensionMaterial")
    ]
    if not items:
        return ""
    name = min(items, key=lambda i: i.get("rank", 0))["name"]
    return name.removeprefix("Teachings of ").removeprefix("the ")


def _city_names(dungeons: dict, users: dict) -> dict:
    """
    Amber city id -> name, from the home region of the characters each city's
    talent domains serve; Amber gives cities only as numbers.
    """
    votes = {}
    for day in dungeons.values():
        for domain in day.values():
            for region in users.get(domain["id"], {}).get("regions", []):
                city = votes.setdefault(domain["city"], {})
                city[region] = city.get(region, 0) + 1
    names = {}
    for city, regions in votes.items():
        if regions:
            names[city] = CITY_NAMES[max(regions, key=regions.get)]
    return names


def _build(dungeons, upgrades, avatars, weapons, materials) -> dict:
    """weekday -> [{city, kind, name, series, users}], users best first"""
    item_users = {}
    for key, char in upgrades["avatar"].items():
        char_id = key.partition("-")[0]
        info = avatars.get(char_id, {})
        name = "Traveler" if char_id in TRAVELER_IDS else char["name"]
        region = info.get("region", "").partition("_")[0]
        for item in char["items"]:
            item_users.setdefault(item, {})[name] = (
                char["rank"],
                region if region in CITY_NAMES else None,
            )
    for key, weapon in upgrades["weapon"].items():
        if key not in weapons:
            continue
        for item in weapon["items"]:
            item_users.setdefault(item, {})[weapon["name"]] = (weapon["rank"], None)

    domains = {}
    for day in dungeons.values():
        for domain in day.values():
            users = {}
            for reward in domain["reward"]:
                users.update(item_users.get(str(reward), {}))
            mastery = "Mastery" in domain["name"]
            domains[domain["id"]] = {
                "kind": "talent" if mastery else "weapon",
                "name": domain["name"].partition(": ")[2] or domain["name"],
                "series": _series_name(materials, domain["reward"]),
                "users": sorted(users, key=lambda u: (-users[u][0], u)),
                "ranks": {u: users[u][0] for u in users},
                "regions": [r for _, r in users.values() if r] if mastery else [],
            }
    cities = _city_names(dungeons, domains)

    schedule = {}
    for weekday, day in dungeons.items():
        schedule[weekday] = [
            {
                **domains[d["id"]],
                "city": cities.get(d["city"], f"Region {d['city']}"),
                "city_id": d["city"],
            }
            for d in sorted(
                day.values(),
                key=lambda d: (
                    d["city"],
                    domains[d["id"]]["kind"] != "talent",
                    d["id"],
                ),
            )
        ]
    return schedule


async def get_schedule() -> dict:
    """The farm schedule from Project Amber, cached for CACHE_TTL"""
    async with _lock:
        if _cache["data"] and time.time() - _cache["time"] < CACHE_TTL:
            return _cache["data"]
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            dungeons, upgrades, avatars, weapons, materials = await asyncio.gather(
                _fetch(session, "dailyDungeon"),
                _fetch(session, "upgrade"),
                _fetch(session, "avatar"),
                _fetch(session, "weapon"),
                _fetch(session, "material"),
            )
        _cache["data"] = _build(
            dungeons,
            upgrades,
            avatars["items"],
            weapons["items"],
            materials["items"],
        )
        _cache["time"] = time.time()
        return _cache["data"]


def server_now(region: str) -> datetime:
    return datetime.now(timezone(timedelta(hours=REGIONS[region])))


def server_weekday(region: str) -> int:
    """Today on the region's server, which rolls over at RESET_HOUR"""
    return (server_now(region) - timedelta(hours=RESET_HOUR)).weekday()


def city_list(schedule: dict) -> list[str]:
    return list(dict.fromkeys(d["city"] for d in schedule[WEEKDAYS[0]]))


def get_farm_msg(
    schedule: dict,
    weekday: int,
    region: str,
    city: str | None = None,
    auto: bool = False,
) -> str:
    day = WEEKDAYS[weekday]
    msg = f"*Genshin Farm Schedule: {day.title()}*\n"
    msg += (
        f"_{REGION_NAMES[region]} server, resets at 04:00 UTC{REGIONS[region]:+d}_\n\n"
    )
    if day == "sunday" and not city:
        msg += "*Every talent and weapon domain is open today.*\n"
        msg += "_Add a city to see what it covers, e.g. sunday liyue._"
    else:
        cities = {}
        for domain in schedule[day]:
            if city and domain["city"] != city:
                continue
            cities.setdefault(domain["city"], []).append(domain)
        for name, domains in cities.items():
            msg += f"*{name}*\n"
            for domain in domains:
                icon = "📖" if domain["kind"] == "talent" else "⚔️"
                users = domain["users"]
                if domain["kind"] == "weapon":
                    # 3★ and below would bury the ones people farm for
                    users = [u for u in users if domain["ranks"][u] >= 4]
                msg += f"{icon} *{domain['series']}* ({domain['name']})\n"
                msg += f"> {', '.join(users) or 'Nothing yet'}\n"
            msg += "\n"
    msg = msg.strip()
    if auto:
        msg += "\n\n_I'm a bot and this action was performed automatically._"
    return msg


async def farm_monitor():
    """Posts the day's schedule to each added chat at its server's reset"""
    try:
        chats = bot.gift_dict["farm"]
        if not chats:
            return
        due = [
            (chat, region)
            for chat, region in chats.items()
            if server_now(region).hour == RESET_HOUR
        ]
        if not due:
            return
        schedule = await get_schedule()
        for chat, region in due:
            msg = get_farm_msg(schedule, server_weekday(region), region, auto=True)
            for c in [chat] if chat else conf.RSS_CHAT.split():
                c, _, server = c.partition(":")
                try:
                    await send_rss(msg, c, [], server or "s.whatsapp.net")
                    await asyncio.sleep(5)
                except Exception:
                    log(Exception)
    except Exception:
        log(Exception)


scheduler2.add_job(
    farm_monitor,
    id="farm_monitor",
    trigger="cron",
    minute=0,
    misfire_grace_time=15 * 60,
    max_instances=1,
    replace_existing=True,
)

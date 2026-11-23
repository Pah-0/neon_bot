"""Werewolf leaderboards drawn as cards, from the global player stats."""

from bot import bot, jid as wa_jid
from bot.utils.msg_utils import get_user_info
from bot.utils.rank_card import ranking_card

CATEGORY_CONFIG = {
    "most_lynched": {
        "label": "Most Lynched",
        "sort": "lynched",
        "unit": "lynch",
        "unit_plural": "lynches",
        "title": "Werewolf Leaderboard",
        "subtitle": "{period} · Global Stats",
        "totals": "Games Played: {total}",
    },
    "most_clocked": {
        "label": "Most Clocked Villains",
        "sort": "clocked",
        "unit": "villain",
        "unit_plural": "villains",
        "title": "Werewolf Leaderboard",
        "subtitle": "{period} · Global Stats",
        "totals": "Games Played: {total}",
    },
    "most_wrong_lynched": {
        "label": "Most Wrongly Lynched",
        "sort": "wrong_lynched",
        "unit": "wrong lynch",
        "unit_plural": "wrong lynches",
        "title": "Werewolf Leaderboard",
        "subtitle": "{period} · Global Stats",
        "totals": "Games Played: {total}",
    },
    "top_shooters": {
        "label": "Top Shooters",
        "sort": "shots",
        "unit": "kill",
        "unit_plural": "kills",
        "title": "Werewolf Leaderboard",
        "subtitle": "{period} · Global Stats",
        "totals": "Games Played: {total}",
    },
    "most_wins": {
        "label": "Most Wins",
        "sort": "wins",
        "unit": "win",
        "unit_plural": "wins",
        "title": "Werewolf Leaderboard",
        "subtitle": "{period} · Global Stats",
        "totals": "Games Played: {total}",
    },
    "most_losses": {
        "label": "Most Losses",
        "sort": "losses",
        "unit": "loss",
        "unit_plural": "losses",
        "title": "Werewolf Leaderboard",
        "subtitle": "{period} · Global Stats",
        "totals": "Games Played: {total}",
    },
    "most_deaths": {
        "label": "Most Deaths",
        "sort": "deaths",
        "unit": "death",
        "unit_plural": "deaths",
        "title": "Werewolf Leaderboard",
        "subtitle": "{period} · Global Stats",
        "totals": "Games Played: {total}",
    },
    "most_games": {
        "label": "Most Games",
        "sort": "games",
        "unit": "game",
        "unit_plural": "games",
        "title": "Werewolf Leaderboard",
        "subtitle": "{period} · Global Stats",
        "totals": "Players Tracked: {members}",
    },
}


async def _build_rows(category: str, limit: int = 10) -> list[dict]:
    """Build ranking rows for a given category from bot.werewolf_stats."""
    sort_key = CATEGORY_CONFIG[category]["sort"]
    entries = sorted(
        bot.werewolf_stats.items(),
        key=lambda kv: kv[1].get(sort_key, 0),
        reverse=True,
    )[:limit]

    rows = []
    for i, (user_id, stats) in enumerate(entries, start=1):
        try:
            info = await get_user_info(user_id)
        except Exception:
            info = None
        name = (
            (
                getattr(info, "PushName", None)
                or getattr(info, "FullName", None)
                or "Unknown"
            )
            if info
            else "Unknown"
        )
        count = stats.get(sort_key, 0)
        rows.append(
            {
                "user": user_id,
                "jid": wa_jid.build_jid(user_id, "s.whatsapp.net"),
                "place": i,
                "name": name,
                "count": count,
                "medals": {},
            }
        )
    return rows


async def send_ww_rank(chat, category: str, final: bool = False) -> str:
    """Send a werewolf leaderboard card for the given category."""
    cfg = CATEGORY_CONFIG[category]
    rows = await _build_rows(category)
    if not rows:
        return "No players have stats yet."

    total = len(bot.werewolf_stats)
    members = len(rows)

    subtitle = cfg["subtitle"].format(period="Global", total=total)
    totals_text = (
        cfg["totals"].format(total=total, members=members)
        if "{members}" in cfg["totals"]
        else cfg["totals"].format(total=total)
    )

    data = {
        "rows": rows,
        "title": cfg["title"],
        "subtitle": subtitle,
        "totals": totals_text,
        "unit": cfg["unit"],
        "unit_plural": cfg["unit_plural"],
    }

    card_bytes = await ranking_card(data)
    try:
        await bot.client.send_image(chat, card_bytes, "", mentions_are_lids=False)
        return "Leaderboard sent."
    except Exception as e:
        return f"Failed to send leaderboard: {e}"

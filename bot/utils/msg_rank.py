import asyncio
import datetime

from bot import jid
from bot.config import bot
from bot.workers.auto.schedule import scheduler2
from bot.workers.handlers.wa import get_ranking_data, get_ranking_msg

from .bot_utils import same_month, same_week
from .db_utils import save2db2
from .log_utils import logger
from .rank_card import ranking_card


async def auto_rank():
    groups = bot.group_dict
    today = datetime.datetime.today()
    tomorrow = today + datetime.timedelta(days=1)

    # Weekly window (evaluate once)
    weekly_clear = groups.get("last_rank_clear")
    weekly_window = not weekly_clear or not same_week(weekly_clear, 1)

    # Monthly window: first weekly window whose new week starts in a new month
    monthly_clear = groups.get("last_monthly_rank_clear")
    monthly_window = weekly_window and (
        not same_month(monthly_clear, 1) if monthly_clear else tomorrow.day <= 7
    )

    for group in list(groups):
        try:
            if group in ("last_rank_clear", "last_monthly_rank_clear"):
                continue

            group_info = groups[group]

            if not group_info.get("msg_chat"):
                continue
            if group_info.get("left"):
                continue

            ranking = group_info.setdefault("msg_ranking", {})
            period = ranking.get("period")
            # groups enabled without a period stored ""; treat anything unknown
            # as weekly
            if period not in ("weekly", "monthly"):
                period = ranking["period"] = "weekly"

            # Every group gets its card on Sundays (the weekly window) only;
            # monthly groups see the standings each week until their reset
            if not weekly_window:
                continue

            reset = (period == "weekly" and weekly_window) or (
                period == "monthly" and monthly_window
            )
            server = ranking.get("server")
            # On reset, the final standings, captioned with the reset notice
            await send_ranking(
                group, period, mentions_are_lids=(server == 0), final=reset
            )

            await asyncio.sleep(3)

            # ---------- RESET LOGIC ----------
            if not reset:
                continue

            saved_config = {
                "period": ranking.get("period", "weekly"),
                "server": ranking.get("server"),
                # Where the next period's final standings start their dates
                "since": today.date().isoformat(),
            }
            update_users_rank(group)
            ranking.clear()
            ranking.update(saved_config)

        except Exception:
            await logger(
                e=f"Error occurred while handling message ranking for group with id: {group}",
                error=True,
            )
            await logger(Exception)

    try:
        if weekly_window:
            groups["last_rank_clear"] = today + datetime.timedelta(days=2)
        if monthly_window:
            groups["last_monthly_rank_clear"] = tomorrow

        if weekly_window or monthly_window:
            await save2db2(bot.group_dict, "groups")

    except Exception:
        await logger(Exception)


async def save_msg_ranking():
    """Saves the counts rec_msg_ranking added since the last save"""
    if not bot.msg_leaderboard_counter:
        return
    bot.msg_leaderboard_counter = 0
    try:
        await save2db2(bot.group_dict, "groups")
    except Exception:
        await logger(Exception)


async def send_ranking(group, period, mentions_are_lids, final=False):
    """
    The leaderboard card, captioned with the top ten tagged; on reset (final)
    the final-standings card, captioned with the reset notice. Text if
    drawing fails.
    """
    chat = jid.build_jid(group, "g.us")
    notice = f"*{period.capitalize()} message ranking has been reset.*"
    try:
        data = await get_ranking_data(group)
        if not data:
            return
        tags = "  ".join(f"{r['place']}. @{r['user']}" for r in data["rows"])
        if final:
            data["final"] = "week ended" if period == "weekly" else "month ended"
            since = bot.group_dict.get(group, {}).get("msg_ranking", {}).get("since")
            data["period"] = period_dates(since, period, datetime.date.today())
            caption = f"{notice}\n{tags}"
        else:
            caption = f"📈 *{period.capitalize()} message leaderboard*\n{tags}"
        await bot.client.send_image(
            chat, await ranking_card(data), caption, mentions_are_lids=mentions_are_lids
        )
    except Exception:
        await logger(Exception)
        msg = await get_ranking_msg(group, tag=True)
        if msg:
            await bot.client.send_message(
                chat, msg, mentions_are_lids=mentions_are_lids
            )
        if final:
            await bot.client.send_message(chat, notice)


def period_dates(since: str | None, period: str, end: datetime.date) -> str:
    """
    The dates a ranking period covered, e.g. "9 – 15 Nov", "28 Oct – 3 Nov".
    Rankings from before "since" was kept get the last week, or the month.
    """
    try:
        start = datetime.date.fromisoformat(since)
    except (TypeError, ValueError):
        if period == "weekly":
            start = end - datetime.timedelta(days=6)
        else:
            # Monthly resets come in the first days of the new month
            first = end.replace(day=1)
            start = (
                (first - datetime.timedelta(days=1)).replace(day=1)
                if end.day <= 7
                else first
            )
    if start.year != end.year:
        return f"{start:%-d %b %Y} – {end:%-d %b %Y}"
    if start.month != end.month:
        return f"{start:%-d %b} – {end:%-d %b}"
    return f"{start:%-d} – {end:%-d %b}"


def update_users_rank(chat_id):
    group = bot.group_dict.setdefault(chat_id, {})
    msg_rank_dict = group.setdefault("msg_ranking", {})

    sorted_ms_rank_dict = dict(
        sorted(
            ((k, v) for k, v in msg_rank_dict.items() if isinstance(v, int)),
            key=lambda item: item[1],
            reverse=True,
        )
    )

    sorted_ms_rank_dict.pop("total", None)
    sorted_ms_rank_dict.pop("server", None)
    sorted_ms_rank_dict.pop("period", None)

    t_three = [1, 2, 3]
    for i, user in zip(t_three, list(sorted_ms_rank_dict.keys())):
        user_rank = group.setdefault("msg_stats", {}).setdefault(user, {})
        user_rank[i] = user_rank.setdefault(i, 0) + 1


scheduler2.add_job(
    id="msg_ranking",
    func=auto_rank,
    trigger="cron",
    hour=20,
    misfire_grace_time=None,
    max_instances=1,
)

# Counts change with every group message; saving them on a timer keeps
# that from rewriting the group records every few seconds
scheduler2.add_job(
    id="save_msg_ranking",
    func=save_msg_ranking,
    trigger="interval",
    minutes=10,
    misfire_grace_time=None,
    max_instances=1,
)

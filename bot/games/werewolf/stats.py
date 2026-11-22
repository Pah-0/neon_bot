from bot.config import bot


def record(user_id, **increments):
    """Add to a player's global werewolf stats."""
    stats = bot.werewolf_stats.setdefault(str(user_id), {})
    for key, amount in increments.items():
        stats[key] = stats.get(key, 0) + amount


def get(user_id):
    return bot.werewolf_stats.get(str(user_id), {})


def get_all():
    return dict(bot.werewolf_stats)

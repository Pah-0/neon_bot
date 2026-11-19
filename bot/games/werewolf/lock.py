"""
Restricted games lock the group: it becomes admins-only and the living
players who aren't admins are promoted, then demoted as they die. The real
admins are left alone (their messages are deleted while they're dead or not
playing). What was changed is saved first, so a game cut short by a crash or
restart is undone on startup.
"""

from neonize.utils.enum import ParticipantChange

from bot import jid
from bot.config import bot
from bot.utils.db_utils import save2db2
from bot.utils.log_utils import logger


def _ids(member) -> set:
    return {u for u in (member.JID.User, member.LID.User, member.PhoneNumber.User) if u}


async def _save():
    await save2db2(bot.group_dict, "groups")


async def lock_group(game) -> bool:
    """Locks the game's group; False if the bot isn't an admin there or it fails"""
    try:
        return await _lock_group(game)
    except Exception:
        await logger(Exception)
        # undo whatever got done before it failed
        await unlock_group(game.chat_id)
        return False


async def _lock_group(game) -> bool:
    info = await bot.client.get_group_info(game.chat_jid)
    me = bot.client.me
    my_ids = {me.JID.User, getattr(me, "LID", me.JID).User}
    if not any(m.IsAdmin and _ids(m) & my_ids for m in info.Participants):
        return False
    alive = {p.user_id for p in game.players_alive_list}
    promote = [m for m in info.Participants if not m.IsAdmin and _ids(m) & alive]
    state = {
        "announce": info.GroupAnnounce.IsAnnounce,
        # player id: [user, server] of the JID they were promoted by
        "promoted": {
            next(iter(_ids(m) & alive)): [m.JID.User, m.JID.Server] for m in promote
        },
    }
    bot.group_dict.setdefault(game.chat_id, {})["ww_lock"] = state
    await _save()
    if promote:
        await bot.client.update_group_participants(
            game.chat_jid, [m.JID for m in promote], ParticipantChange.PROMOTE
        )
    if not state["announce"]:
        await bot.client.set_group_announce(game.chat_jid, True)
    return True


async def demote_dead(game, player):
    """Takes back the admin rights a dead player got for the game."""
    state = bot.group_dict.get(game.chat_id, {}).get("ww_lock")
    if not state or not (promoted := state["promoted"].pop(player.user_id, None)):
        return
    await _save()
    try:
        await bot.client.update_group_participants(
            game.chat_jid, [jid.build_jid(*promoted)], ParticipantChange.DEMOTE
        )
    except Exception:
        await logger(Exception)


async def unlock_group(chat_id):
    """Undoes lock_group: demotes who's left and restores who can send."""
    state = bot.group_dict.get(chat_id, {}).get("ww_lock")
    if not state:
        return
    chat = jid.build_jid(chat_id, "g.us")
    if state["promoted"]:
        try:
            # fails only for those who've left the group since
            await bot.client.update_group_participants(
                chat,
                [jid.build_jid(*p) for p in state["promoted"].values()],
                ParticipantChange.DEMOTE,
            )
        except Exception:
            await logger(Exception)
    if not state["announce"]:
        try:
            await bot.client.set_group_announce(chat, False)
            await bot.client.send_message(chat, "🔓 *The group is open again.*")
        except Exception:
            # kept, so the next startup tries again
            return await logger(Exception)
    bot.group_dict[chat_id].pop("ww_lock", None)
    await _save()


async def unlock_stale_groups():
    """Unlocks groups left locked by games a crash or restart cut short."""
    for chat_id, group in list(bot.group_dict.items()):
        if isinstance(group, dict) and group.get("ww_lock"):
            await unlock_group(chat_id)

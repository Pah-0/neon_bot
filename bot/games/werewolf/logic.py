import asyncio
import random
from datetime import datetime, timedelta

from bot import bot, sudo_btn_lock
from bot.config import conf
from bot.utils.buttons import choose
from bot.utils.db_utils import save2db2
from bot.utils.log_utils import logger
from bot.utils.sudo_button_utils import (
    active_poll_dict,
    create_sudo_button,
    wait_for_button_response,
)

from .defaults import (
    ACTUAL_WOLVES,
    COMMANDS_FOR_ROLE,
    DAY_COMMANDS,
    DETECTIVE_REVEAL_CHANCE,
    DRUNK_HEADSHOT,
    DRUNK_INJURE,
    DRUNK_MISS,
    DRUNK_SUICIDE,
    GUNNER_HEADSHOT,
    GUNNER_INJURE,
    GUNNER_MISS,
    GUNNER_REVENGE_WOLF,
    GUNNER_SUICIDE,
    PLAYER_TIMEOUT,
    PLAYER_TIMEOUT2,
    SHAMAN_TOTEMS,
    WOLF_SHAMAN_TOTEMS,
    WOLFCHAT_ROLES,
    lang,
)
from .lock import lock_group, unlock_group
from .roles import roles
from .stats import record

NIGHT_ACTIONS = {
    cmd: roles_ for cmd,
    roles_ in COMMANDS_FOR_ROLE.items() if cmd not in DAY_COMMANDS}
SHAMANS = ["shaman", "wolf shaman", "crazed shaman"]
# roles that can't choose the same player two nights in a row
NO_REPEAT_TARGET = SHAMANS + ["guardian angel"]
# totems (and a doomsayer's lycanthropy) that act the night after they're given
DELAYED_EFFECTS = [
    "luck_totem",
    "misdirection_totem",
    "deceit_totem",
    "lycanthropy"]


def win_condition(game):
    teams = {"village": 0, "wolf": 0, "neutral": 0}
    players_alive = game.players_alive_list
    if not players_alive:
        return "no win", "Everyone died. The town sits abandoned, collecting dust.", []

    def get_plurals(item, sgl="", plr="s", invert=False):
        if invert:
            return "" if item > 1 else "s"
        return plr if item > 1 else sgl

    living_monster_num = len([p for p in players_alive if p.role == "monster"])
    living_cultists_minions = [
        p for p in players_alive if p.role in ["cultist", "minion"]
    ]
    living_sk_num = len(
        [p for p in players_alive if p.role == "serial killer"])

    for player in players_alive:
        # injured villagers can't vote so they don't count against the wolves
        if player.is_injured and player.actual_team != "wolf":
            continue
        if (player.role in ["cultist", "minion"]
                ) and game.mode != "evilvillage":
            teams["village"] += 1
        else:
            teams[player.actual_team] += 1

    winners, win_team, win_lore = [], "", ""
    all_lovers = (len(players_alive) > 1) and all(
        p.loves and any(game.get_player_by_user_id(l_id).is_alive for l_id in p.loves)
        for p in players_alive
    )

    if all_lovers:
        win_team, win_lore = (
            "lovers",
            "Game over! The remaining villagers through their inseparable love for each other have agreed to stop all of this senseless violence and coexist in peace forever more. All remaining players win.",
        )
    elif len(
        [p for p in players_alive if p.role == "succubus" or "entranced" in p.other]
    ) == len(players_alive):
        win_team = "succubi"
        win_lore = "Game over! The succub{} completely enthralled the village, making them officers in an ever-growing army set on spreading their control and influence throughout the entire world.".format(
            "i" if len([p for p in game.players.values() if p.original_role == "succubus"]) > 1 else "us")
    elif len(
        [p for p in players_alive if p.role == "piper" or "charmed" in p.other]
    ) == len(players_alive):
        win_team = "pipers"
        p_num = len([p for p in game.players.values()
                    if p.original_role == "piper"])
        win_lore = "Game over! Everyone has fallen victim to the charms of the piper{0}. The piper{0} lead{1} the villagers away from the village, never to return...".format(
            get_plurals(p_num), get_plurals(p_num, invert=True))
    elif living_sk_num >= len(players_alive) / 2:
        if living_monster_num > 0:
            win_team, win_lore = (
                "monster",
                "Game over! The serial killer{0} stabbed all those in the village, except the monster{1}, causing the monster{1} to win.".format(
                    get_plurals(living_sk_num), get_plurals(living_monster_num)
                ),
            )
        else:
            win_team = "serial killers"
            win_lore = "Game over! The serial killer{0} stabbed all those in the village! The serial killer{0} walk{1} off, in the hope to successfully do the same at another location.".format(
                get_plurals(living_sk_num), get_plurals(living_sk_num, invert=True))
    elif teams["village"] + teams["neutral"] <= teams["wolf"] and not (
        game.mode == "evilvillage" and teams["village"]
    ):
        if game.mode == "evilvillage":
            if not teams["village"]:
                if living_monster_num > 0:
                    win_team = "monster"
                    win_lore = "Game over! All the villagers are dead! As the cultists rejoice, they get destroyed by the monster{0}, causing the monster{0} to win.".format(
                        get_plurals(living_monster_num))
                elif not (len(players_alive) - len(living_cultists_minions)):
                    win_team = "no win"
                    win_lore = "Game over! All the villagers are dead, but the cult needed to sacrifice the wolves to accomplish that. The cult disperses shortly thereafter, and nobody wins."
                else:
                    win_team = "wolf"
                    win_lore = "Game over! All the villagers are dead! The cultists rejoice with their wolf buddies and start plotting to take over the next village."
        elif living_monster_num > 0:
            win_team = "monster"
            win_lore = "Game over! The number of uninjured villagers is equal or less than the number of living wolves! The wolves overpower the villagers but then get destroyed by the monster{0}, causing the monster{0} to win.".format(
                get_plurals(living_monster_num))
        else:
            win_team = "wolf"
            win_lore = "The number of uninjured villagers is equal or less than the number of living wolves! The wolves overpower the remaining villagers and devour them whole."
    elif (
        len(
            [
                p
                for p in players_alive
                if p.team == "wolf" and p.role not in ["cultist", "minion"]
            ]
        )
        == 0
        and living_sk_num == 0
    ):
        if living_monster_num > 0:
            win_team = "monster"
            win_lore = "Game over! All the wolves are dead! As the villagers start preparing the BBQ, the monster{0} quickly kill{1} the remaining villagers, causing the monster{0} to win.".format(
                get_plurals(living_monster_num), get_plurals(living_monster_num, invert=True), )
        else:
            if (
                len(living_cultists_minions) == teams["wolf"]
                and game.mode == "evilvillage"
            ):
                win_team = "village"
                win_lore = "Game over! All the wolves are dead! The villagers round up the remaining cultists, hang them, and live happily ever after."
            else:
                win_team = "village"
                win_lore = "All the wolves are dead! The surviving villagers gather the bodies of the dead wolves, roast them, and have a BBQ in celebration."
    elif teams["village"] >= teams["wolf"] and game.mode == "evilvillage":
        if living_monster_num > 0:
            win_team = "monster"
            win_lore = "Game over! The number of uninjured cultists is equal or less than the number of living villagers! as the villagers regain control over the village, the monster{0} quickly kill{1} the remaining villagers, causing the monster{0} to win.".format(
                get_plurals(living_monster_num), get_plurals(living_monster_num, invert=True), )
        elif not living_cultists_minions:
            win_team = "village"
            win_lore = "Game over! All the cultists are dead! The now-exposed wolves are captured and killed by the remaining villagers. A BBQ party commences shortly thereafter."
        else:
            win_team = "village"
            win_lore = "Game over! The number of uninjured cultists is equal or less than the number of living villagers! They manage to regain control of the village and dispose of the remaining cultists."
    else:
        return None

    for p in game.players.values():
        role, other, is_winner = p.role, p.other, False
        if p.actual_team == win_team:
            is_winner = True
        elif role == "lycan" and win_team == "village":
            is_winner = True
        elif role == "amnesiac" and win_team == "village":
            is_winner = True
        elif role == "piper" and win_team == "pipers":
            is_winner = True
        elif (role == "succubus" or "entranced" in other) and win_team == "succubi":
            is_winner = True
        elif role == "jester" and "lynched" in other:
            is_winner = True
        elif role == "monster" and win_team == "monster":
            is_winner = True
        elif role == "serial killer" and win_team == "serial killers":
            is_winner = True
        elif role == "executioner" and "won" in other:
            is_winner = True
        elif role == "vengeful ghost" and not p.is_alive:
            swore_against = getattr(p, "vengeance_against", None)
            if swore_against and win_team != swore_against:
                is_winner = True
        if not is_winner and p.loves:
            if win_team == "lovers" or all(
                game.get_player_by_user_id(l_id).is_alive for l_id in p.loves
            ):
                is_winner = True
        if is_winner:
            winners.append(p.user_id)
        if (
            (win_team != "succubi" and "entranced" in other)
            or (win_team != "pipers" and "charmed" in other)
            or role == "hot potato"
        ):
            if p.user_id in winners:
                winners.remove(p.user_id)
    return win_team, win_lore, winners


async def idle_watch(game):
    """
    Warns living players who've sent nothing for PLAYER_TIMEOUT seconds, and
    kills those still silent PLAYER_TIMEOUT2 seconds later.
    """
    while not game.ended:
        await asyncio.sleep(10)
        now = datetime.now()
        for p in game.players_alive_list:
            if not p.is_alive:  # a lover who just died of grief
                continue
            idle = (now - game.last_seen.get(p.user_id, now)).total_seconds()
            if idle >= PLAYER_TIMEOUT + PLAYER_TIMEOUT2:
                game.idle_warned.discard(p.user_id)
                extra = await game.kill(p, "idle")
                await game.send_lobby(
                    f"*{p.name}* didn't get out of bed for a very long time and has "
                    f"been found dead. The survivors bury the *{p.death_role}*.\n{extra}".rstrip()
                )
                await game.check_traitor()
                await game.update_status()
            elif idle >= PLAYER_TIMEOUT and p.user_id not in game.idle_warned:
                game.idle_warned.add(p.user_id)
                await game.send_lobby(
                    f"@{p.user_id}, you have been idling for a while. "
                    "Say something soon or you might be declared dead.",
                    mentions=True,
                )
                await game.send_pm(
                    p,
                    "You have been idling in the game for a while. "
                    "Say something there soon or you might be declared dead.",
                )


async def game_loop(game):
    idle_task = asyncio.create_task(idle_watch(game))
    try:
        if game.restricted:
            if await lock_group(game):
                await game.send_lobby(
                    "🔒 *Restricted game:* only living players can talk until it ends."
                )
            else:
                await game.send_lobby(
                    "I can only lock the group for a restricted game if I created it "
                    "(its super admin), so no player can demote me. I'll delete messages "
                    "from dead players and non-players instead (as an admin)."
                )
        await game.send_lobby(
            f"Welcome to Werewolf! Using the *{game.mode}* game mode with *{game.total_players}* players."
        )
        for p in game.players.values():
            if p.role == "executioner":
                villagers = [
                    v
                    for v in game.players.values()
                    if v.team == "villager" and v.user_id != p.user_id
                ]
                if villagers:
                    target = random.choice(villagers)
                    p.execution_target = target.user_id
        for p in game.players.values():
            await game.send_pm(p, game.role_text(p))
        await game.update_status()
        await night_phase(game, is_night_zero=True)
        while not game.ended:
            if win := win_condition(game):
                await end_game(game, win)
                break
            if game.night:
                await day_phase(game)
            else:
                await night_phase(game)
    except Exception:
        await logger(Exception)
        await game.send_lobby("*An error occurred, the game has been stopped.*")
    finally:
        idle_task.cancel()
        game.ended = True
        game.close()
        await game.unpin_status()
        if game.restricted:
            await unlock_group(game.chat_id)


def has_night_action(game, p, is_night_zero):
    if "hexed" in p.other:
        return False
    if is_night_zero:
        return p.role in ["matchmaker", "clone"]
    if p.role == "vengeful ghost":
        return not p.is_alive and bool(getattr(p, "vengeance_against", None))
    if not p.is_alive:
        return False
    if p.role == "hunter" and "hunted" in p.other:
        return False
    if p.role == "turncoat" and "sided2" in p.other:
        return False
    return any(p.role in roles_ for roles_ in NIGHT_ACTIONS.values())


async def night_phase(game, is_night_zero=False):
    game.night, game.day = True, False
    if not is_night_zero:
        game.night_num += 1
    game.night_start_time = datetime.now()
    game.protected = set()
    await game.send_lobby("It is now *nighttime*.")
    await game.update_status()
    for p in game.players.values():
        p.target = ""
        p.totem = p.totem_target = p.observe_target = ""
        p.is_injured = False  # injuries and illness last a day
        p.remove_other("guarded")
        for t in DELAYED_EFFECTS:
            p.remove_other(f"{t}2")
            if t in p.other:
                p.remove_other(t)
                p.add_other(f"{t}2")
        for t in SHAMAN_TOTEMS + WOLF_SHAMAN_TOTEMS:
            p.remove_other(t)
        # a turncoat who switched sides must sit out the next night
        if "sided2" in p.other:
            p.remove_other("sided2")
        elif "sided" in p.other:
            p.remove_other("sided")
            p.add_other("sided2")
    if not is_night_zero:
        await night_start_effects(game)
    for p in game.players_alive_list:
        if p.role in SHAMANS and "hexed" not in p.other and not is_night_zero:
            if p.role == "shaman":
                p.totem = random.choice(SHAMAN_TOTEMS)
            elif p.role == "wolf shaman":
                p.totem = random.choice(WOLF_SHAMAN_TOTEMS)
            else:
                p.totem = random.choice(SHAMAN_TOTEMS + WOLF_SHAMAN_TOTEMS)
            await game.send_pm(
                p, f"Your totem tonight is: *{p.totem.replace('_', ' ')}*."
            )
    tasks = [
        asyncio.create_task(request_night_action(game, p, is_night_zero))
        for p in game.players.values()
        if has_night_action(game, p, is_night_zero)
    ]
    for p in game.players_alive_list:
        if "hexed" in p.other:
            continue
        if p.role == "doomsayer" and not is_night_zero:
            tasks.append(asyncio.create_task(request_doom(game, p)))
        # roles with a second action besides voting with the wolves
        if p.role == "wolf shaman" and p.totem:
            tasks.append(asyncio.create_task(request_totem(game, p)))
        if p.role == "werecrow" and not is_night_zero:
            tasks.append(asyncio.create_task(request_observe(game, p)))
        if "assassin" in p.templates and not p.assassin_target:
            tasks.append(asyncio.create_task(request_assassin_target(game, p)))
    try:
        if tasks:
            await asyncio.wait(tasks, timeout=game.night_timeout + 5)
    finally:
        # don't let late answers leak into the next phase (or a stopped game)
        for task in tasks:
            task.cancel()
    if is_night_zero:
        await process_night_zero(game)
    else:
        await process_night(game)
    await game.check_traitor()


async def night_start_effects(game):
    """Amnesia clearing, mystic senses and other news at the start of a night"""
    for p in game.players_alive_list:
        if p.role == "amnesiac" and game.night_num == 2 and p.hidden_role:
            # the third night, counting night zero
            p.role, p.team = p.hidden_role, roles[p.hidden_role][0]
            p.description = roles[p.role][2]
            p.add_other("amnesiac")
            await game.send_pm(
                p,
                f"Your amnesia clears and you now remember that you are a *{p.role}*!\n\n{p.description}",
            )
            if p.role in WOLFCHAT_ROLES:
                await game.wolfchat(f"{p.name} is now a *{p.role}*!")
        if "hexed" in p.other:
            continue
        if p.role == "mystic":
            wolves = sum(
                x.actual_team == "wolf" for x in game.players_alive_list)
            await game.send_pm(p, f"You sense that there are *{wolves}* wolves.")
        elif p.role == "wolf mystic":
            powered = sum(
                (x.actual_team == "village" and x.role != "villager")
                or x.role in ["fool", "monster", "succubus", "piper", "serial killer"]
                for x in game.players_alive_list
            )
            await game.send_pm(
                p, f"You sense that there are *{powered}* villagers with powers."
            )
        elif p.role == "turncoat" and "sided2" not in p.other:
            await game.send_pm(p, "You can switch sides tonight.")
        elif p.role == "doomsayer":
            p.doom = random.choice(["sick", "lycan", "death"])
    game.wolf_kills = 1 + any(
        "angry" in w.other
        for w in game.players_alive_list
        if w.role in COMMANDS_FOR_ROLE["kill"] and w.team == "wolf"
    )
    if game.wolf_kills > 1:
        await game.wolfchat("You are enraged tonight: choose *two* players to kill.")


def redirect(game, actor, target):
    """Misdirection (on the actor) or luck (on the target) sends an action elsewhere"""
    if "misdirection_totem2" in actor.other or "luck_totem2" in target.other:
        others = [
            p for p in game.players_alive_list if p not in (
                actor, target)]
        if others:
            return random.choice(others)
    return target


def seen_as(seer, target):
    """What target looks like to a seer; deceit on either side flips it"""
    seen = target.display_role
    if ("deceit_totem2" in seer.other) != ("deceit_totem2" in target.other):
        seen = "villager" if seen == "wolf" else "wolf"
    return seen


def _label(p):
    return f"{p.name} (#{p.id})"


async def request_doom(game, player):
    """The doomsayer picks who the night's bad luck befalls."""
    options = {
        p.user_id: _label(p)
        for p in game.players_alive_list
        if p is not player and p.role not in WOLFCHAT_ROLES
    }
    try:
        choice = await choose(
            player.jid,
            "Choose who bad luck befalls tonight (doomsayer):",
            options,
            poll_user=player.user_id,
            timeout=game.night_timeout,
        )
        if not (choice and player.is_alive and game.night):
            return
        target = redirect(game, player, game.get_player_by_user_id(choice))
        doom = getattr(player, "doom", "lycan")
        if doom == "lycan":
            target.add_other("lycanthropy")
            vision = "is transforming into a savage beast tomorrow night"
        elif doom == "death":
            player.doom_target = target.user_id
            vision = "will meet an untimely end tonight"
        else:
            target.add_other("sick")
            vision = "will become incredibly ill tomorrow and unable to do anything"
        await game.send_pm(player, f"You have a vision that *{target.name}* {vision}.")
    except asyncio.CancelledError:
        raise
    except Exception:
        await logger(Exception)


async def request_assassin_target(game, player):
    """The assassin picks who they'll take with them when they die."""
    options = {p.user_id: _label(p)
               for p in game.players_alive_list if p is not player}
    try:
        choice = await choose(
            player.jid,
            "Choose your target (assassin). If you die, you take them with you:",
            options,
            poll_user=player.user_id,
            timeout=game.night_timeout,
        )
        if choice and (target := game.get_player_by_user_id(choice)):
            player.assassin_target = choice
            await game.send_pm(player, f"Your target is *{target.name}*.")
    except asyncio.CancelledError:
        raise
    except Exception:
        await logger(Exception)


async def request_bless(game, player):
    """The priest may bless someone, once a game, during the day."""
    options = {p.user_id: _label(p)
               for p in game.players_alive_list if p is not player}
    options["pass"] = "Not today"
    try:
        choice = await choose(
            player.jid,
            "Bless someone to keep them from being killed (priest, once a game):",
            options,
            poll_user=player.user_id,
            timeout=game.discussion + game.day_timeout,
        )
        target = game.get_player_by_user_id(choice) if choice else None
        if not (target and target.is_alive and player.is_alive and game.day):
            return
        if "bless_used" in player.other:
            return
        player.add_other("bless_used")
        target.add_other("blessed")
        await game.send_pm(player, f"You have given a blessing to *{target.name}*.")
    except asyncio.CancelledError:
        raise
    except Exception:
        await logger(Exception)


async def _request_second_target(game, player, prompt, options, attr):
    """Asks for a target besides the wolf kill vote and stores it in attr"""
    try:
        choice = await choose(
            player.jid,
            prompt,
            options,
            poll_user=player.user_id,
            timeout=game.night_timeout,
        )
        if choice and (target := game.get_player_by_user_id(choice)):
            target = redirect(game, player, target)
            setattr(player, attr, target.user_id)
            await game.send_pm(player, f"Target set to: {target.name}")
    except asyncio.CancelledError:
        raise
    except Exception:
        await logger(Exception)


async def request_totem(game, player):
    """The wolf shaman picks who gets their totem."""
    options = {
        p.user_id: _label(p)
        for p in game.players_alive_list
        if p.user_id != player.last_target
    }
    await _request_second_target(
        game,
        player,
        f"Choose who receives your {player.totem.replace('_', ' ')} (wolf shaman):",
        options,
        "totem_target",
    )


async def request_observe(game, player):
    """The werecrow picks who to watch."""
    options = {p.user_id: _label(p)
               for p in game.players_alive_list if p is not player}
    await _request_second_target(
        game,
        player,
        "Choose who to observe tonight (werecrow):",
        options,
        "observe_target",
    )


async def request_night_action(game, player, is_night_zero):
    if player.role == "turncoat":
        choice = await choose(
            player.jid,
            "Choose your side tonight (turncoat). You can switch every other night:",
            {"villagers": "The villagers", "wolves": "The wolves", "pass": "Pass"},
            poll_user=player.user_id,
            timeout=game.night_timeout,
        )
        if choice:
            player.target = choice
            await game.send_pm(player, f"Action: {choice}.")
        return
    wolf_killer = player.team == "wolf" and player.role in COMMANDS_FOR_ROLE["kill"]
    options = {}
    if player.role == "matchmaker":
        for p in game.players.values():
            options[p.user_id] = [f"{p.name} (#{p.id})"]
    elif player.role == "clone":
        for p in game.players.values():
            if p.user_id != player.user_id:
                options[p.user_id] = [f"{p.name} (#{p.id})"]
    elif player.role == "piper":
        for p in game.players_alive_list:
            if p.user_id != player.user_id and "charmed" not in p.other:
                options[p.user_id] = [f"{p.name} (#{p.id})"]
    elif player.role == "vengeful ghost":
        against = getattr(player, "vengeance_against", "wolf")
        for p in game.players_alive_list:
            if p.actual_team == against:
                options[p.user_id] = [f"{p.name} (#{p.id})"]
    elif player.role == "warlock":
        for p in game.players_alive_list:
            if (
                p.team != "wolf" or p.role in ["minion", "cultist"]
            ) and "cursed villager" not in p.templates:
                options[p.user_id] = [f"{p.name} (#{p.id})"]
    elif wolf_killer:
        # killing a fellow wolf would be a waste
        for p in game.players_alive_list:
            if p.role not in WOLFCHAT_ROLES:
                options[p.user_id] = [f"{p.name} (#{p.id})"]
    else:
        for p in game.players_alive_list:
            if player.role in NO_REPEAT_TARGET and p.user_id == player.last_target:
                continue
            if p.user_id == player.user_id and player.role not in [
                "harlot",
                "succubus",
                "guardian angel",
                "bodyguard",
                "shaman",
                "wolf shaman",
                "crazed shaman",
            ]:
                continue
            options[p.user_id] = [f"{p.name} (#{p.id})"]
    if not options:
        return
    if player.role in [
        "hunter",
        "serial killer",
        "harlot",
        "succubus",
        "guardian angel",
        "bodyguard",
        "piper",
        "warlock",
        "hot potato",
    ]:
        options["pass"] = ["Pass"]
    prompt = f"Choose target ({player.role}):"
    if player.role in ["shaman", "crazed shaman"]:
        prompt = f"Choose who receives your {
            player.totem.replace(
                '_', ' ')} ({
            player.role}):"
    if player.role == "matchmaker":
        prompt = "Select 2 lovers, then Confirm:"
    elif player.role == "piper":
        prompt = "Select up to 2 to charm, then Confirm:"
    elif wolf_killer and getattr(game, "wolf_kills", 1) > 1:
        prompt = "You are enraged: select 2 to kill, then Confirm:"
    selectable, conf_btn = 1, None
    if player.role in [
        "matchmaker",
            "piper"] or prompt.startswith("You are enraged"):
        # every vote change is a new poll update; wait for an explicit confirm
        conf_btn = "Confirm"
        options["confirm"] = [conf_btn]
        selectable = 3
    msg_id = None
    try:
        if selectable == 1:
            # One target: buttons (or a poll if they can't be sent), in the
            # player's DM so only they can press
            choice = await choose(
                player.jid,
                prompt,
                {key: value[0] for key, value in options.items()},
                poll_user=player.user_id,
                timeout=game.night_timeout,
            )
            response = [choice] if choice else []
        else:
            # Several targets need a poll
            _, msg_ = await create_sudo_button(
                prompt,
                options,
                player.jid,
                player.user_id,
                selectable=selectable,
                conf_btn=conf_btn,
            )
            msg_id = msg_.ID
            response = await wait_for_button_response(
                msg_id, timeout=game.night_timeout
            )
        response = [r for r in (response or []) if r and r != "confirm"]
        if not response:
            return
        if "pass" in response:
            player.target = "pass"
            return await game.send_pm(player, "Action: Passed.")
        if player.role == "matchmaker" and len(response) != 2:
            return await game.send_pm(player, "You must select exactly 2 lovers.")
        response = response[:2]
        if not is_night_zero:
            response = [
                redirect(game, player, game.get_player_by_user_id(uid)).user_id
                for uid in response
            ]
        player.target = ",".join(response)
        target_names = ", ".join(
            game.get_player_by_user_id(uid).name for uid in response
        )
        await game.send_pm(player, f"Target set to: {target_names}")
        if is_night_zero or not player.is_alive:
            return
        target = game.get_player_by_user_id(response[0])
        if player.role == "warlock":
            # the curse takes effect as soon as it's cast
            target.templates.append("cursed villager")
            await game.wolfchat(f"*{player.name}* has cast a curse on *{target.name}*.")
        elif player.role == "seer":
            await game.send_pm(
                player,
                f"Your vision reveals that {target.name} is a *{seen_as(player, target)}*.",
            )
        elif player.role == "oracle":
            await game.send_pm(
                player,
                f"Your vision reveals that {target.name} is {'a wolf' if seen_as(player, target) == 'wolf' else 'not a wolf'}.",
            )
        elif player.role == "augur":
            aura = (
                "red"
                if target.actual_team == "wolf"
                else ("grey" if target.actual_team == "neutral" else "blue")
            )
            await game.send_pm(player, f"{target.name} has a {aura} aura.")
        elif player.role == "sorcerer":
            is_info = (
                "an info role"
                if target.role in ["seer", "oracle", "augur"]
                else "not an info role"
            )
            await game.send_pm(
                player, f"Your observation reveals that {target.name} is {is_info}."
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        await logger(Exception)
    finally:
        if msg_id:
            async with sudo_btn_lock:
                active_poll_dict.pop(msg_id, None)


async def process_night_zero(game):
    for p in game.players.values():
        if p.role == "matchmaker":
            uids = p.target.split(",") if p.target else []
            if len(uids) != 2 or uids[0] == uids[1]:
                # no choice: random lovers, and the matchmaker isn't told who
                uids = [
                    x.user_id for x in random.sample(
                        list(
                            game.players.values()),
                        2)]
            if len(uids) == 2:
                p1, p2 = game.get_player_by_user_id(
                    uids[0]
                ), game.get_player_by_user_id(uids[1])
                if p1 and p2:
                    p1.loves.append(p2.user_id)
                    p2.loves.append(p1.user_id)
                    await game.send_pm(
                        p1,
                        f"You are in love with *{p2.name}*. If they die, you will commit suicide.",
                    )
                    await game.send_pm(
                        p2,
                        f"You are in love with *{p1.name}*. If they die, you will commit suicide.",
                    )
        elif p.role == "clone" and p.target:
            p.cloning_target = p.target
            await game.send_pm(
                p,
                f"You are cloning *{game.get_player_by_user_id(p.target).name}*. If they die, you take their role.",
            )


def _target(game, p):
    """Returns the player targeted by p, if any."""
    if not p.target or p.target in ("pass", "villagers", "wolves"):
        return None
    return game.get_player_by_user_id(p.target.split(",")[0])


async def process_night(game):
    killed = []
    # hexes only last for the night after they were cast
    for p in game.players.values():
        p.remove_other("hexed")
    for p in game.players_alive_list:
        if p.role == "hag" and (target := _target(game, p)):
            target.add_other("hexed")
    for p in game.players_alive_list:
        if p.role not in SHAMANS or not p.totem:
            continue
        if p.role == "wolf shaman":
            target = game.get_player_by_user_id(p.totem_target)
        else:
            target = _target(game, p)
        if not (target and target.is_alive):
            # an ungiven totem goes to someone at random
            others = [
                x for x in game.players_alive_list if x.user_id != p.last_target]
            if not others:
                continue
            target = random.choice(others)
        target.add_other(p.totem)
        if p.totem == "silence_totem":
            target.add_other("hexed")
        p.last_target = target.user_id
    for p in game.players_alive_list:
        if p.role == "guardian angel":
            target = _target(game, p)
            p.last_target = target.user_id if target else ""
    for p in game.players_alive_list:
        if p.role == "werecrow" and (
            target := game.get_player_by_user_id(p.observe_target)
        ):
            status = "out of bed" if target.target else "in bed"
            await game.send_pm(
                p,
                f"Your observation reveals that {target.name} was {status} last night.",
            )
    protected_uids = []
    for p in game.players_alive_list:
        if p.role in [
                "guardian angel",
                "bodyguard"] and (
                target := _target(
                game,
                p)):
            protected_uids.append(target.user_id)
        if "protection_totem" in p.other:
            protected_uids.append(p.user_id)
    game.protected = set(protected_uids)
    wolf_targets = {}
    wolves = [
        p
        for p in game.players_alive_list
        if p.team == "wolf" and p.role in COMMANDS_FOR_ROLE["kill"]
    ]
    for w in wolves:
        for uid in w.target.split(
                ",") if w.target and w.target != "pass" else []:
            if (target := game.get_player_by_user_id(uid)) and target.is_alive:
                wolf_targets[uid] = wolf_targets.get(uid, 0) + 1
    # a dead wolf cub's rage is spent tonight
    for p in game.players.values():
        p.remove_other("angry")
    chosen = sorted(wolf_targets, key=wolf_targets.get, reverse=True)
    wolf_victims = []
    for target_uid in chosen[: getattr(game, "wolf_kills", 1)]:
        if game.get_player_by_user_id(target_uid).role == "serial killer":
            continue  # too dangerous for the wolves to kill
        if target_uid in protected_uids:
            for p in game.players_alive_list:
                if p.role == "bodyguard" and p.target == target_uid:
                    killed.append(p.user_id)
                    break
        else:
            killed.append(target_uid)
            wolf_victims.append(target_uid)
    for p in game.players_alive_list:
        if p.role in [
                "serial killer",
                "hunter"] and (
                target := _target(
                game,
                p)):
            if p.role == "hunter":
                p.add_other("hunted")
            if target.user_id not in protected_uids:
                killed.append(target.user_id)
        if "death_totem" in p.other:
            killed.append(p.user_id)
        if p.role == "doomsayer" and (
            doomed := getattr(
                p,
                "doom_target",
                None)):
            p.doom_target = None
            if doomed not in protected_uids:
                killed.append(doomed)
    for p in game.players_alive_list:
        if p.role == "harlot" and (target := _target(game, p)):
            if target.actual_team == "wolf" or target.user_id in killed:
                killed.append(p.user_id)
    for p in game.players_alive_list:
        if p.role == "succubus" and (target := _target(game, p)):
            if "bishop" not in target.templates:
                target.add_other("entranced")
        if p.role == "piper" and p.target and p.target != "pass":
            for t_id in p.target.split(","):
                if target := game.get_player_by_user_id(t_id):
                    target.add_other("charmed")
    for p in game.players.values():
        if (
            not p.is_alive
            and p.role == "vengeful ghost"
            and (target := _target(game, p))
        ):
            killed.append(target.user_id)
    for p in game.players_alive_list:
        if p.role == "turncoat" and p.target in ("villagers", "wolves"):
            p.remove_other("side:villagers")
            p.remove_other("side:wolves")
            p.add_other(f"side:{p.target}")
            p.add_other("sided")
    killed_msg = ""
    for uid in dict.fromkeys(killed):
        p = game.get_player_by_user_id(uid)
        if p and p.is_alive:
            if p.is_blessed and killed.count(uid) < 2:
                # a blessing wards off one attack
                await game.send_pm(
                    p, "You were attacked last night, but your blessing kept you safe."
                )
                continue
            if (
                p.role == "lycan"
                or "lycanthropy_totem" in p.other
                or "lycanthropy2" in p.other
            ):
                p.role, p.team = "wolf", "wolf"
                p.description = roles["wolf"][2]
                await game.send_pm(p, "HOOOOOOOOOWL. You have become... a wolf!")
                await game.wolfchat(f"{p.name} is now a *wolf*!")
                continue
            killed_msg += (
                f"The dead body of *{p.name}*, a *{p.death_role}*, was found.\n"
            )
            killed_msg += await game.kill(p, "night")
    killed_msg += await gunner_revenge(game, wolf_victims, wolves)
    # the hot potato swaps identities with their (living) choice
    for potato in game.players_alive_list:
        if potato.role != "hot potato" or not (
                target := _target(game, potato)):
            continue
        if not target.is_alive or target is potato:
            continue
        for attr in (
            "role",
            "team",
            "description",
            "templates",
            "other",
            "bullet_count",
            "assassin_target",
        ):
            a, b = getattr(potato, attr), getattr(target, attr)
            setattr(potato, attr, b)
            setattr(target, attr, a)
        await game.send_pm(
            target, f"You are now a *hot potato*!\n\n{target.description}"
        )
        await game.send_pm(
            potato, f"You are now a *{potato.role}*!\n\n{potato.description}"
        )
    if not killed_msg:
        killed_msg = random.choice(lang["nokills"]) + "\n"
    await game.send_lobby(
        f"Night lasted long enough. The villagers wake up and search the village.\n\n{killed_msg}"
    )


def shot_outcome(shooter, target):
    """
    What a gunshot does, as in the original game: miss, suicide (the gun
    explodes), killvictim, injure, or killwolf; the village drunk is a worse shot
    """
    sharpshooter = "sharpshooter" in shooter.templates
    if shooter.role == "village drunk":
        miss, suicide, headshot, injure = (
            DRUNK_MISS,
            DRUNK_SUICIDE,
            DRUNK_HEADSHOT,
            DRUNK_INJURE,
        )
    else:
        miss, suicide, headshot, injure = (
            GUNNER_MISS,
            GUNNER_SUICIDE,
            GUNNER_HEADSHOT,
            GUNNER_INJURE,
        )
    if shooter.role in WOLFCHAT_ROLES:
        if target.role in WOLFCHAT_ROLES:
            return "miss"  # on purpose
    elif target.role in ACTUAL_WOLVES:
        if target.role != "werekitten":
            return "killwolf"
        # too cute to hit
        if sharpshooter:
            return "miss"
        return random.choice(
            ["suicide"] * suicide + ["miss"] * (miss + headshot + injure)
        )
    elif target.role == "succubus":
        return random.choice(["suicide"] * suicide + ["miss"]
                             * (miss + headshot) + ["injure"] * injure)
    if sharpshooter:
        return "killvictim"
    return random.choice(
        ["miss"] * miss
        + ["suicide"] * suicide
        + ["killvictim"] * headshot
        + ["injure"] * injure
    )


async def gunner_revenge(game, victims, wolves):
    """
    A gunner the wolves killed may shoot one of them; a gun with bullets left
    is then found by someone on the wolf team. Returns what the village sees.
    """
    msg = ""
    for uid in victims:
        victim = game.get_player_by_user_id(uid)
        armed = "gunner" in victim.templates or "sharpshooter" in victim.templates
        if victim.is_alive or not armed or victim.bullet_count <= 0:
            continue
        shot = None
        if random.random() < GUNNER_REVENGE_WOLF:
            attackers = [
                w
                for w in wolves
                if w.is_alive
                and w.role != "wolf cub"
                and uid in (w.target or "").split(",")
            ]
            if attackers:
                shot = random.choice(attackers)
                victim.bullet_count -= 1
                msg += (
                    f"Fortunately *{victim.name}* had bullets and *{shot.name}*, "
                    f"a *{shot.death_role}*, was shot dead.\n"
                )
                msg += await game.kill(shot, "gunner revenge")
        finders = [
            w
            for w in game.players_alive_list
            if w.role in WOLFCHAT_ROLES and w is not shot
        ]
        if victim.bullet_count > 0 and finders:
            finder = random.choice(finders)
            if "gunner" not in finder.templates:
                finder.templates.append("gunner")
            finder.bullet_count += 1
            await game.send_pm(
                finder,
                f"While searching through *{victim.name}*'s belongings, you discover a gun "
                "loaded with 1 silver bullet! You may only use it during the day. If you "
                "shoot at a wolf, you will intentionally miss. If you shoot a villager, it "
                "is likely that they will be injured.",
            )
    return msg


def discussion_seconds(game):
    """How long the village talks before voting: 10s a living player, 1-3 min"""
    if game.day_timeout <= 60:  # the dead time lord's hurry leaves no time to talk
        return 0
    return min(180, max(60, 10 * game.players_alive_count))


def _duration(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    if not minutes:
        return f"{seconds}s"
    return f"{minutes} min" + (f" {seconds}s" if seconds else "")


async def day_phase(game):
    game.night, game.day, game.detective_acted = False, True, False
    game.day_num += 1
    game.voting_open = False
    game.discussion = discussion_seconds(game)
    game.voting_opens_at = datetime.now() + timedelta(seconds=game.discussion)
    game.day_start_time = datetime.now()
    game.protected = set()
    for p in game.players.values():
        p.vote = ""
        if "sick" in p.other:
            # the doomsayer's illness: no vote today, no powers until tomorrow
            p.remove_other("sick")
            p.is_injured = True
            p.add_other("hexed")
            await game.send_pm(
                p,
                "You are staying home due to your illness and cannot vote or use any powers.",
            )
    if game.discussion:
        await game.send_lobby(
            "It is now *daytime*. Discuss who to lynch: voting opens in "
            f"*{_duration(game.discussion)}*."
        )
    await game.update_status()
    # detectives pick who to identify in their PM, so the group can't see it
    id_tasks = [
        asyncio.create_task(request_detective_id(game, p))
        for p in game.players_alive_list
        if p.role == "detective" and "hexed" not in p.other
    ] + [
        asyncio.create_task(request_bless(game, p))
        for p in game.players_alive_list
        if p.role == "priest" and "bless_used" not in p.other and "hexed" not in p.other
    ]
    lynched_uid, warned = None, False
    try:
        while datetime.now() < game.voting_opens_at:
            if win_condition(game) or game.ended:
                return
            await asyncio.sleep(1)
        game.voting_open = True
        game.day_start_time = datetime.now()
        await game.send_lobby(
            f"{'Voting is open!' if game.discussion else 'It is now *daytime*.'} "
            f"Use `{conf.CMD_PREFIX}lynch <player>` to vote to lynch"
            " (a #number, name, @mention, or reply to their message)."
            f" *{game.votes_to_lynch}* votes lynch at once; `{conf.CMD_PREFIX}votes` shows them."
            f" *{_duration(game.day_timeout)}* to vote."
        )
        while (
            elapsed := (datetime.now() - game.day_start_time).total_seconds()
        ) < game.day_timeout:
            if win_condition(game):
                return
            counts = game.vote_counts()
            # half the voters abstaining, or a majority on someone, ends the
            # day
            if (
                counts.get("abstain", 0)
                and counts["abstain"] >= len(game.able_voters) / 2
            ):
                lynched_uid = "abstain"
                break
            on_players = {
                uid: n for uid,
                n in counts.items() if uid != "abstain"}
            if on_players and (
                top := max(
                    on_players.values())) >= game.votes_to_lynch:
                lynched_uid = random.choice(
                    [uid for uid, n in on_players.items() if n == top]
                )
                break
            # nothing left to wait for once everyone has voted
            if all(p.vote for p in game.able_voters):
                break
            if not warned and elapsed >= game.day_warning:
                warned = True
                await game.send_lobby(
                    f"*{int(game.day_timeout - elapsed)}s* left to vote before the day "
                    "ends! Then the most votes win; no one is lynched on a tie."
                )
            await asyncio.sleep(1)
    finally:
        for task in id_tasks:
            task.cancel()
    if not lynched_uid:
        counts = game.vote_counts()
        if not counts:
            return await game.send_lobby(
                "Not enough votes were cast to lynch a player."
            )
        top = max(counts.values())
        leaders = [uid for uid, n in counts.items() if n ==
                   top and uid != "abstain"]
        if not leaders:
            lynched_uid = "abstain"
        elif len(leaders) > 1:
            return await game.send_lobby("The vote was tied, nobody was lynched today.")
        else:
            lynched_uid = leaders[0]
    if lynched_uid == "abstain":
        return await game.send_lobby(
            "The village has agreed to not lynch anyone today."
        )
    p = game.get_player_by_user_id(lynched_uid)
    if "revealing_totem" in p.other:
        return await game.send_lobby(
            f"As the villagers prepare to lynch *{p.name}*, their totem emits a brilliant "
            f"flash of light! When the villagers can see again, they find that *{p.name}* "
            f"has escaped, and the totem has taken the shape of a *{p.role}*."
        )
    if "mayor" in p.templates and "revealed_mayor" not in p.other:
        p.add_other("revealed_mayor")
        return await game.send_lobby(
            f"While being dragged to the gallows, *{p.name}* reveals that they are the *mayor*. The village agrees to let them live for now."
        )
    p.is_lynched = True
    p.add_other("lynched")
    record(p.user_id, lynched=1)
    clockers = [
        v.user_id for v in game.players_alive_list if v.vote == p.user_id]
    if p.actual_team != "village":
        # lynching a nefarious role: voters "clock" them
        for v_id in clockers:
            record(v_id, clocked=1)
    else:
        # village player wrongly lynched
        record(p.user_id, wrong_lynched=1)
    msg = random.choice(lang["lynched"]).format(p.name, p.death_role)
    extra = await game.kill(p, "lynch")
    voters = [v for v in game.players_alive_list if v.vote == p.user_id]
    if "desperation_totem" in p.other and voters and p.role != "fool":
        # the last to vote for them pays for it
        last = max(voters, key=lambda v: v.vote_at)
        extra += (
            f"As the noose is fitted, *{p.name}*'s totem emits a brilliant flash of light. "
            f"When the villagers can see again, *{last.name}*, a *{last.death_role}*, "
            "has fallen over dead.\n"
        )
        extra += await game.kill(last, "desperation")
    await game.send_lobby(f"{msg}\n{extra}" if extra else msg)
    for exe in game.players_alive_list:
        if exe.role == "executioner" and exe.execution_target == p.user_id:
            exe.add_other("won")
    if p.role == "fool":
        await end_game(
            game,
            (
                "fool",
                "The fool has been lynched, causing them to win!",
                [p.user_id],
            ),
        )
    await game.check_traitor()


async def request_detective_id(game, player):
    """Sends the detective buttons to pick a player to identify today."""
    options = {
        p.user_id: f"{p.name} (#{p.id})"
        for p in game.players_alive_list
        if p.user_id != player.user_id
    }
    try:
        choice = await choose(
            player.jid,
            "Choose who to identify today (detective):",
            options,
            poll_user=player.user_id,
            timeout=game.discussion + game.day_timeout,
        )
        if choice and (target := game.get_player_by_user_id(choice)):
            await detective_identify(game, player, target)
    except asyncio.CancelledError:
        raise
    except Exception:
        await logger(Exception)


async def detective_identify(game, player, target):
    """The detective learns target's role, once per day."""
    if not (game.day and player.is_alive and target.is_alive):
        return await game.send_pm(player, "You can't identify anyone right now.")
    if game.detective_acted:
        return await game.send_pm(player, "You have already used your ability today.")
    game.detective_acted = True
    await game.send_pm(
        player, f"Your investigation reveals that {target.name} is a *{target.role}*."
    )
    if random.random() < DETECTIVE_REVEAL_CHANCE:
        await game.wolfchat(f"The detective has been revealed! It is {player.name}.")


async def end_game(game, win_data):
    game.ended = True
    win_team, win_lore, winners_ids = win_data
    msg = f"*GAME OVER!*\n\n{win_lore}\n\n*Winners:*\n"
    for uid in winners_ids:
        p = game.get_player_by_user_id(uid)
        msg += f"- {p.name} ({p.role})\n"
    if not winners_ids:
        msg += "- Nobody\n"
    msg += "\n*Final player list:*\n"
    for p in game.players.values():
        msg += f"- {p.name}: {p.role} ({'Alive' if p.is_alive else 'Dead'})\n"
    for p in game.players.values():
        record(p.user_id, games=1)
        if p.user_id in winners_ids:
            record(p.user_id, wins=1)
        elif win_team != "no win":
            record(p.user_id, losses=1)
    await save2db2(bot.werewolf_stats, "werewolf_stats")
    await game.send_lobby(msg)
    game.close()

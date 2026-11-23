import asyncio
import re
import time
import uuid
from datetime import datetime

from bot import sudo_btn_lock
from bot.config import bot, conf
from bot.games.register import register_for_a_game
from bot.games.werewolf.defaults import MAX_PLAYERS, MIN_PLAYERS
from bot.games.werewolf.game import Game
from bot.games.werewolf.logic import (
    detective_identify,
    game_loop,
    redirect,
    shot_outcome,
)
from bot.games.werewolf.rank import CATEGORY_CONFIG, send_ww_rank
from bot.games.werewolf.roles import gamemodes, roles
from bot.games.werewolf.stats import get as stats_get
from bot.games.werewolf.stats import record
from bot.utils.buttons import on_button, send_buttons
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    get_args,
    get_user_info,
    user_is_admin,
    user_is_privileged,
)
from bot.utils.sudo_button_utils import active_poll_dict, create_sudo_button
from bot.utils.user_utils import show_ids, target_user


async def werewolf_handler(event, args, client):
    """
    Plays a game of Werewolf in the chat; without arguments shows the game help.
    A lobby auto-starts after 3 minutes without joins/leaves, or once it is full.

    Arguments (only the first of -j, -l, -s, --start, --mode, --role given is used):
        -j : create a lobby (you join it) or join the existing one
        -l : leave the lobby or game
        -s : show lobby or game status
        --start : start the lobby now (needs the mode's minimum players)
        --mode <mode> : set the lobby's mode, creating a lobby if none exists (default: chosen by chance)
        --mode all : list available modes
        --role <name> : show one role's team and description (e.g. --role succubus)
        --role all : list all roles grouped by team
        -r : restricted mode: while the game runs, only living players can talk; the group
            goes admins-only and living players who aren't admins are made admins until they
            die or the game ends (needs the bot to be the group's super admin, its creator;
            otherwise dead players' and non-players' messages are deleted instead). Messages from dead or
            non-playing real admins are deleted. (Dead players' messages are always deleted.)
            (can be combined with -j, --start or --mode, or used alone on an existing lobby)

    In-game commands (alive players only):
        (a player is named by #id, name, @mention or LID, or by replying to their message)
        lynch <player> : vote to lynch a player, during the day
        id <player> : detective only, in PM with the bot, learn a player's role, once per day
            (the detective also gets buttons for it each day; outside that, .id shows WhatsApp ids)
        shoot <player> : gunner/sharpshooter only, shoot a player during the day
        myrole : I send you your role (and totem, lovers, targets) in PM again
        vote <player> : same as lynch; with no player, shows the votes
        votes : show today's lynch votes (in a lobby: the mode votes)
        retract : take back your lynch vote (in a lobby: your mode vote)
        abstain : vote to lynch no one today (not on the first day, nor in evilvillage)
    Each day starts with 1-3 minutes of discussion (10s a living player) before
    voting opens. A majority of the players able to vote lynches at once; when the day runs out,
    the most votes win, and no one is lynched on a tie.
    Players who send nothing for 5 minutes are warned, then killed a minute later.

    In a lobby with enough players, a poll lets players vote for a mode:
    a majority picks it, fewer votes make it likelier. vote <mode> works too.

    fstop [reason] : [Owner | Sudo | Admin] stop the lobby or game in this chat
    """
    cmd = event.text.split()[0].removeprefix(conf.CMD_PREFIX).casefold()
    if cmd == "fstop":
        return await force_stop(event, args, client)
    if cmd == "myrole":
        return await my_role(event)
    if cmd in ("vote", "votes", "retract") and (
        game := bot.current_games_dict.get("werewolf", {}).get(event.chat.id)
    ):
        if not isinstance(game, dict) and game.waiting:
            return await lobby_vote(game, event, cmd, (args or "").strip())
    if cmd in ("lynch", "vote", "votes", "retract", "abstain", "id", "shoot"):
        game = bot.current_games_dict.get("werewolf", {}).get(event.chat.id)
        if cmd == "id" and not event.chat.is_group:
            # a detective identifies from their PM with the bot
            game = detective_game(event.from_user.id) or game
        if game and not isinstance(game, dict) and not game.waiting:
            player = game.get_player_by_user_id(event.from_user.id)
            # .id is the Detective's identify only for a living Detective
            if cmd != "id" or (
                player and player.is_alive and player.role == "detective"
            ):
                return await handle_game_command(game, event, cmd)
        if cmd == "id":
            return await show_ids(event, args, client)
        return
    if cmd == "wwstats":
        return await show_wwstats(event)
    if cmd in ("wwrank", "wwrankings"):
        return await send_ww_rank_buttons(event)
    if not args:
        return await were_info(event, args, client)
    arg, args = get_args(
        "--mode",
        "--role",
        ["-j", "store_true"],
        ["-l", "store_true"],
        ["-s", "store_true"],
        ["--start", "store_true"],
        ["-r", "store_true"],
        to_parse=args,
        get_unknown=True,
    )
    res = register_for_a_game("werewolf", event)
    if not res:
        return
    if res == "started":
        game = bot.current_games_dict.get("werewolf", {}).get(event.chat.id)
        if arg.s:
            return await game.status(event)
        return await event.reply("A game is already in progress in this chat.")

    current_games = bot.current_games_dict.setdefault("werewolf", {})
    game = current_games.get(event.chat.id)

    if arg.j:
        if not game or isinstance(game, dict):
            game = Game(event)
            if arg.r:
                game.restricted = True
            current_games[event.chat.id] = game
            asyncio.create_task(auto_start_manager(event, game))
            return await event.reply("Game lobby created! Use `-j` to join.")
        return await game.join(event)
    if arg.l:
        if not game or isinstance(game, dict):
            return await event.reply("No game found in chat! Use `-j` to start & join.")
        return await game.leave(event)

    if arg.r and game and not isinstance(game, dict):
        game.restricted = True
        await event.reply("will restrict dead/non-players")
    elif arg.r and not (arg.s or arg.start or arg.mode):
        return await event.reply("No game lobby created. Use `-j` to start one.")

    if arg.s:
        if not game or isinstance(game, dict):
            return await event.reply("No game in progress in this chat.")
        return await game.status(event)

    if arg.start:
        if not game or isinstance(game, dict):
            return await event.reply("No game lobby created. Use `-j` to start one.")
        if not game.waiting:
            return await event.reply("Game is already in progress.")

        if arg.r:
            game.restricted = True

        return await start_game(event, game)

    if arg.role:
        role_query = arg.role.strip().casefold()
        if role_query == "all":
            msg = "*Werewolf Roles:*\n"
            by_team = {}
            for name, (team, plural, desc) in roles.items():
                if team == "template":
                    continue
                by_team.setdefault(team, []).append((name, plural, desc))
            for team in ("wolf", "villager", "neutral"):
                if team in by_team:
                    msg += f"\n*{team.title()} team:*\n"
                    for name, plural, desc in sorted(by_team[team], key=lambda x: x[0]):
                        msg += f"- *{name}* ({plural}): {desc}\n"
            return await event.reply(msg)
        # exact match on role name or plural first
        exact = [
            (n, t, p, d)
            for n, (t, p, d) in roles.items()
            if role_query in (n.casefold(), p.casefold())
        ]
        if exact:
            n, team, plural, desc = exact[0]
            return await event.reply(f"*{n}* (team: {team}): {desc}")
        # else substring match on the name
        partial = [
            (n, t, p, d) for n, (t, p, d) in roles.items() if role_query in n.casefold()
        ]
        if len(partial) == 1:
            n, team, plural, desc = partial[0]
            return await event.reply(f"*{n}* (team: {team}): {desc}")
        if len(partial) > 1:
            names = ", ".join(f"*{n}*" for n, *_ in partial)
            return await event.reply(
                f"Multiple roles match *{arg.role}*: {names}. Be more specific."
            )
        return await event.reply(f"Role *{arg.role}* not found.")

    if arg.mode:
        if game and not isinstance(game, dict) and not game.waiting:
            return await event.reply("Cannot change mode after game started.")

        mode_name = arg.mode.casefold()
        if mode_name == "all":
            msg = "*Available Gamemodes:*\n"
            for m, data in gamemodes.items():
                if not data.get("roles"):
                    continue
                msg += f"- *{m}* ({
                    data['min_players']}-{
                    data['max_players']} players): {
                    data['description']}\n"
            return await event.reply(msg)

        # modes without roles aren't implemented and would end instantly
        if not gamemodes.get(mode_name, {}).get("roles"):
            return await event.reply(f"Mode *{mode_name}* not found.")

        if not game or isinstance(game, dict):
            game = Game(event, mode=mode_name)
            if arg.r:
                game.restricted = True
            current_games[event.chat.id] = game
            asyncio.create_task(auto_start_manager(event, game))
            return await event.reply(
                f"Game lobby created with mode *{mode_name}*! Use `-j` to join."
            )
        else:
            game.requested_mode = mode_name
            return await event.reply(f"Mode set to *{mode_name}*.")

    return await event.reply(
        f"Unknown argument `{args}`. Use `{conf.CMD_PREFIX}werewolf` for help."
    )


async def start_game(event, game):
    """Validates the lobby, assigns roles and starts the game loop."""
    game.set_each_role_numbers_and_pool()
    if game.total_players < game.min_players:
        return await event.reply(
            f"Not enough players for mode *{game.mode}*! Need at least {game.min_players}."
        )
    max_players = gamemodes[game.mode].get("max_players", MAX_PLAYERS)
    if game.total_players > max_players:
        return await event.reply(
            f"Too many players for mode *{game.mode}*! Maximum is {max_players}."
        )
    # assign before the task starts so the game can't be started twice
    game.assign_roles()
    game.activity.set()  # wake the lobby manager so it can clean up
    game.task = asyncio.create_task(game_loop(game))
    return await event.reply(
        f"Game starting with mode *{game.mode}*! Restricted: {game.restricted}. Check your PMs for roles."
    )


async def find_target(game, event, target_str):
    """
    The player named by an @mention, LID or +number, else by #id or name,
    else the one whose message was replied to
    """
    if target_str and not re.fullmatch(r"@?\+?[0-9]{5,16}", target_str.split()[0]):
        return game.get_player_by_id(target_str.lstrip("#")) or next(
            (
                p
                for p in game.players_alive_list
                if target_str.lower() in p.name.lower()
            ),
            None,
        )
    lid, number, _ = await target_user(event, target_str or "")
    return game.get_player_by_user_id(lid) or game.get_player_by_user_id(number)


async def my_role(event):
    """Sends a player their role (and totem, lovers, targets...) in PM."""
    games = bot.current_games_dict.get("werewolf", {})
    if event.chat.is_group:
        games = {event.chat.id: games.get(event.chat.id)}
    for game in games.values():
        if not game or isinstance(game, dict) or game.waiting:
            continue
        if player := game.get_player_by_user_id(event.from_user.id):
            await game.send_pm(player, game.role_text(player))
            if event.chat.is_group:
                await event.react("📩")
            return
    await event.reply("You're not in a running werewolf game.")


def detective_game(user_id):
    """The running game in which user_id is a living detective"""
    for game in bot.current_games_dict.get("werewolf", {}).values():
        if isinstance(game, dict) or game.waiting:
            continue
        player = game.get_player_by_user_id(user_id)
        if player and player.is_alive and player.role == "detective":
            return game
    return None


async def handle_game_command(game, event, cmd_type):
    """Runs the in-game commands; only votes is open to everyone."""
    args = event.text.split(maxsplit=1)
    target_str = args[1].strip() if len(args) > 1 else None
    voting = cmd_type in ("lynch", "vote", "retract", "abstain")
    if cmd_type == "votes":
        if not game.day:
            return await event.reply("Votes are only cast during the day.")
        return await event.reply(game.votes_text())

    player = game.get_player_by_user_id(event.from_user.id)
    if not player or not player.is_alive:
        return

    if voting:
        if not game.day:
            return await event.reply("You can only vote during the day!")
        if not game.voting_open:
            wait = (game.voting_opens_at - datetime.now()).total_seconds()
            return await event.reply(
                f"Voting opens in *{max(1, int(wait))}s*; discuss who to lynch first."
            )
        if player.is_injured:
            return await event.reply("You are injured and unable to vote.")

    if cmd_type in ("lynch", "vote"):
        if not (target_str or event.reply_to_message):
            return await event.reply(game.votes_text())

        target = await find_target(game, event, target_str)
        if not target or not target.is_alive:
            return await event.reply("Invalid target.")

        player.vote = target.user_id
        player.vote_at = time.monotonic()
        await event.reply(f"You voted to lynch {target.name}!")

    elif cmd_type == "retract":
        if not player.vote:
            return await event.reply("You haven't voted today.")
        player.vote = ""
        await event.reply("You took back your vote.")

    elif cmd_type == "abstain":
        if game.mode == "evilvillage":
            return await event.reply("The evilvillage cannot abstain.")
        if game.day_num == 1:
            return await event.reply("The village may not abstain on the first day.")
        player.vote = "abstain"
        await game.send_lobby(f"*{player.name}* votes to not lynch anyone today.")

    elif cmd_type == "id":
        if not game.day:
            return await event.reply("You can only use id during the day!")
        if player.role != "detective":
            return
        if game.detective_acted:
            return await event.reply("You have already used your ability today.")
        if not (target_str or event.reply_to_message):
            return await event.reply(
                f"Usage: {conf.CMD_PREFIX}id <id or name>, here in PM (or the buttons I sent you)"
            )

        target = await find_target(game, event, target_str)
        if not target or not target.is_alive:
            return await event.reply("Invalid target.")

        await detective_identify(game, player, target)

    elif cmd_type == "shoot":
        if not game.day:
            return await event.reply("You can only shoot during the day!")
        if "gunner" not in player.templates and "sharpshooter" not in player.templates:
            return
        if player.bullet_count <= 0:
            return await event.reply("You are out of bullets!")
        if not (target_str or event.reply_to_message):
            return await event.reply(
                f"Usage: {conf.CMD_PREFIX}shoot <id, name or @mention>, or reply to them"
            )

        target = await find_target(game, event, target_str)
        if not target or not target.is_alive:
            return await event.reply("Invalid target.")

        if target is player:
            return await event.reply("You are holding it the wrong way.")
        if "hexed" in player.other:
            return await event.reply(
                "You have been silenced, and are unable to use any special powers."
            )

        target = redirect(game, player, target)
        player.bullet_count -= 1
        outcome = shot_outcome(player, target)
        if outcome in ("injure", "killvictim", "killwolf"):
            msg = f"*{player.name}* shoots *{target.name}* with a bullet!\n\n"
        else:
            msg = ""
        dead = None
        if outcome == "miss":
            msg += f"*{player.name}* is a lousy shooter and missed!"
        elif outcome == "killwolf":
            msg += f"*{target.name}* is a *{target.death_role}* and is dying from the silver bullet!"
            dead = target
        elif outcome == "suicide":
            msg += (
                f"Oh no! *{player.name}*'s gun was poorly maintained and has exploded! "
                f"The village mourns a *gunner-{player.death_role}*."
            )
            dead = player
        elif outcome == "killvictim":
            msg += (
                f"*{target.name}* is not a wolf but was fatally injured. "
                f"The village has sacrificed a *{target.death_role}*."
            )
            dead = target
        else:
            msg += (
                f"*{target.name}* is a villager and was injured. "
                "Luckily the injury is minor and will heal after a day of rest."
            )
            target.is_injured = True
        if dead:
            msg += "\n" + await game.kill(dead, "shot")
            if dead is target:
                record(player.user_id, shots=1)
        await game.send_lobby(msg.rstrip())
        if dead:
            await game.check_traitor()
        await game.update_status()


def note_activity(event):
    """Anything a player sends, in the game's group or to the bot, keeps them from idling out."""
    games = bot.current_games_dict.get("werewolf", {})
    if event.chat.is_group:
        games = {event.chat.id: games.get(event.chat.id)}
    for game in games.values():
        if game and not isinstance(game, dict) and not game.waiting:
            game.note_activity(event.from_user.id)


async def werewolf_restriction_handler(client, event):
    note_activity(event)
    if not event.chat.is_group:
        return
    current_games = bot.current_games_dict.get("werewolf", {})
    game = current_games.get(event.chat.id)
    if not game or isinstance(game, dict) or game.waiting:
        return
    if game.ended or not game.players:
        return
    player = game.get_player_by_user_id(event.from_user.id)
    # at night delete ALL players' messages (alive or dead); during restricted day
    # delete non-players' messages too
    if game.night:
        try:
            await event.delete()
        except Exception:
            pass
    elif (player and not player.is_alive) or (not player and game.restricted):
        try:
            await event.delete()
        except Exception:
            pass


async def show_wwstats(event):
    """Shows a player's global werewolf stats."""
    stats = stats_get(event.from_user.id)
    if not stats:
        return await event.reply(
            f"You have no werewolf stats yet! Play a game with `{conf.CMD_PREFIX}werewolf -j`."
        )
    user_info = await get_user_info(event.from_user.id)
    name = user_info.PushName or user_info.FullName or event.from_user.name
    msg = f"*{name}'s Werewolf Stats*\n"
    for key in (
        "games",
        "wins",
        "losses",
        "lynched",
        "deaths",
        "clocked",
        "wrong_lynched",
        "shots",
    ):
        label_map = {
            "games": "Games",
            "wins": "Wins",
            "losses": "Losses",
            "lynched": "Lynched",
            "deaths": "Deaths",
            "clocked": "Clocked it",
            "wrong_lynched": "Wrongly Lynched",
            "shots": "Shots",
        }
        label = label_map.get(key, key.capitalize())
        msg += f"{label}: {stats.get(key, 0)}\n"
    if stats.get("games", 0) > 0:
        wins = stats.get("wins", 0)
        win_rate = wins / stats["games"] * 100
        msg += f"Win rate: {win_rate:.1f}%"
    return await event.reply(msg)


async def send_ww_rank_buttons(event):
    """Send a button message for ranking category selection."""
    await send_buttons(
        event.chat.jid,
        "🏆 *Werewolf Rankings:* select a category:",
        [(f"wwrank:{cat}", cfg["label"]) for cat, cfg in CATEGORY_CONFIG.items()],
    )


@on_button("wwrank")
async def wwrank_button_handler(event, data: str):
    """Handle a press on a werewolf rankings button."""
    _, _, cat = data.partition(":")
    if cat not in CATEGORY_CONFIG:
        return
    try:
        await send_ww_rank(event.chat, cat)
    except Exception:
        await event.reply(f"Failed to load {cat} ranking.")


async def were_info(event, args, client):
    """Replies with the werewolf help message."""
    msg = "*Werewolf Game*\n\n"
    msg += f"To create/join a lobby: `{conf.CMD_PREFIX}werewolf -j`\n"
    msg += f"To leave: `{conf.CMD_PREFIX}werewolf -l`\n"
    msg += f"To see status: `{conf.CMD_PREFIX}werewolf -s`\n"
    msg += f"To start now: `{conf.CMD_PREFIX}werewolf --start`\n"
    msg += f"To set mode: `{conf.CMD_PREFIX}werewolf --mode <mode>`\n"
    msg += f"To see modes: `{conf.CMD_PREFIX}werewolf --mode all`\n"
    msg += f"To see a role: `{conf.CMD_PREFIX}werewolf --role <name>`\n"
    msg += f"To see all roles: `{conf.CMD_PREFIX}werewolf --role all`\n"
    msg += "Dead players' messages are deleted while a game runs (I need to be an admin).\n"
    msg += "Restricted mode (only living players can talk: the group goes admins-only and they're made admins until they die; I must have created the group): add `-r` to `-j`, `--start` or `--mode`\n"
    msg += "Lobbies auto-start after 3 minutes without joins/leaves.\n\n"
    msg += "*In-game (during the day):*\n"
    msg += f"Vote to lynch: `{conf.CMD_PREFIX}lynch <player>`\n"
    msg += f"Detective, once per day, in PM with me: `{
        conf.CMD_PREFIX}id <player>` or the buttons I send\n"
    msg += f"Gunner/sharpshooter: `{conf.CMD_PREFIX}shoot <player>`\n"
    msg += f"Your role again, in PM: `{conf.CMD_PREFIX}myrole`\n"
    msg += f"See or take back votes: `{
        conf.CMD_PREFIX}votes`, `{
        conf.CMD_PREFIX}retract`\n"
    msg += f"Lynch no one: `{conf.CMD_PREFIX}abstain` (not on the first day)\n"
    msg += "A player is their #number, name, @mention, or reply to their message.\n"
    msg += "Each day starts with 1-3 minutes of discussion before voting opens.\n"
    msg += "A majority lynches at once; when the day ends, the most votes win (none on a tie).\n"
    msg += (
        "Players silent for 5 minutes are warned, then found dead a minute later.\n\n"
    )
    msg += "*In a lobby:*\n"
    msg += f"Vote for a mode in the poll, or `{
        conf.CMD_PREFIX}vote <mode>`; a majority picks it, fewer votes make it likelier\n"
    msg += f"Stop a lobby or game (admins): `{conf.CMD_PREFIX}fstop [reason]`"
    return await event.reply(msg)


# Open lobbies with Join/Leave buttons: token -> game
_lobbies: dict[str, Game] = {}


@on_button("wwlobby")
async def lobby_button(event, data: str):
    """Join or Leave pressed on a lobby's buttons"""
    token, _, action = data.partition(":")
    if not (game := _lobbies.get(token)) or not game.waiting:
        return
    if action == "join":
        await game.join(event, notify=True)
    elif action == "leave":
        await game.leave(event, notify=True)


async def auto_start_manager(event, game):
    options = {
        "join": ("Join",),
        "leave": ("Leave",),
    }

    async def btn_callback(event, poll_msg_key):
        selected = await bot.client.decrypt_poll_vote(event.message)
        if not (poll_info := active_poll_dict.get(poll_msg_key.ID)):
            return
        actions = [poll_info.get(s.hex()) for s in selected.selectedOptions]
        if "join" in actions:
            return await game.join(event, notify=True)
        elif "leave" in actions:
            return await game.leave(event, notify=True)

    msg = mode_poll = None
    token = uuid.uuid4().hex[:10]
    sent = []
    try:
        # Join/Leave buttons; a poll if they can't be sent
        try:
            sent = await send_buttons(
                event.chat.jid,
                "Werewolf Game Lobby",
                [
                    (f"wwlobby:{token}:join", "Join"),
                    (f"wwlobby:{token}:leave", "Leave"),
                ],
            )
            _lobbies[token] = game
        except Exception:
            await logger(Exception)
            _, msg = await create_sudo_button(
                name="Werewolf Game Lobby",
                options=options,
                chat_jid=event.chat.jid,
                user_id="",
                selectable=1,
                callback=btn_callback,
            )
        # keep the lobby in view; it's deleted (and so unpinned) when it ends
        try:
            await bot.client.pin_message(
                event.chat.jid, bot.client.me.JID, sent[0] if sent else msg.ID, 86400
            )
        except Exception:
            await logger(Exception)
        # start once nobody has joined/left for a while
        while game.waiting and len(game.player_ids) < MAX_PLAYERS:
            if not game.player_ids or game.ended:  # everyone left, or stopped
                return
            if not mode_poll and game.total_players >= MIN_PLAYERS:
                mode_poll = await send_mode_poll(game)
            try:
                await asyncio.wait_for(game.activity.wait(), timeout=180)
            except asyncio.TimeoutError:
                break
            game.activity.clear()
        if not game.waiting or game.ended:  # started elsewhere, or stopped
            return
        if not game.player_ids:
            return
        await start_game(event, game)
        if game.waiting:  # couldn't start
            game.close()
            await event.reply("The game lobby has been closed.")
    except Exception:
        await logger(Exception)
    finally:
        _lobbies.pop(token, None)
        for message_id in sent:
            try:
                await bot.client.revoke_message(
                    event.chat.jid, bot.client.me.JID, message_id
                )
            except Exception:
                pass
        for poll in (msg, mode_poll):
            if not poll:
                continue
            async with sudo_btn_lock:
                active_poll_dict.pop(poll.ID, None)
            try:
                await bot.client.revoke_message(
                    event.chat.jid, bot.client.me.JID, poll.ID
                )
            except Exception:
                pass


async def send_mode_poll(game):
    """Posts the lobby's mode poll; returns its message, or None if it can't."""
    if game.requested_mode:
        return None

    async def callback(event, poll_msg_key):
        if event.from_user.id not in game.player_ids:
            return
        selected = await bot.client.decrypt_poll_vote(event.message)
        if not (poll_info := active_poll_dict.get(poll_msg_key.ID)):
            return
        modes = [poll_info.get(s.hex()) for s in selected.selectedOptions]
        if mode := next((m for m in modes if m), None):
            game.mode_votes[event.from_user.id] = mode
        else:  # vote taken back
            game.mode_votes.pop(event.from_user.id, None)

    options = {
        m: (
            f"{m} ({gamemodes[m]['min_players']}-{gamemodes[m]['max_players']} players)",
        )
        for m in game.votable_modes
    }
    try:
        _, msg = await create_sudo_button(
            name="Vote for the werewolf mode (a majority picks it, "
            "fewer votes make it likelier)",
            options=options,
            chat_jid=game.chat_jid,
            user_id="",
            selectable=1,
            callback=callback,
        )
        return msg
    except Exception:
        await logger(Exception)
        return None


async def lobby_vote(game, event, cmd, mode):
    """vote <mode>, votes and retract in a lobby"""
    if cmd == "votes" or (cmd == "vote" and not mode):
        return await event.reply(game.mode_votes_text())
    if event.from_user.id not in game.player_ids:
        return await event.reply("Join the lobby to vote for a mode.")
    if cmd == "retract":
        if game.mode_votes.pop(event.from_user.id, None):
            return await event.reply("You took back your mode vote.")
        return await event.reply("You haven't voted for a mode.")
    mode = mode.casefold()
    if not gamemodes.get(mode, {}).get("roles"):
        return await event.reply(
            f"Mode *{mode}* not found. See `{conf.CMD_PREFIX}werewolf --mode all`."
        )
    game.mode_votes[event.from_user.id] = mode
    await event.reply(f"You voted for the *{mode}* mode.")


async def force_stop(event, args, client):
    """[Owner | Sudo | Admin] fstop [reason]: stops the lobby or game here."""
    game = bot.current_games_dict.get("werewolf", {}).get(event.chat.id)
    if not game or isinstance(game, dict):
        return await event.reply("No werewolf game in this chat.")
    user = event.from_user.id
    if not user_is_privileged(user):
        if not event.chat.is_group:
            return await event.react("🚫")
        group_info = await client.get_group_info(event.chat.jid)
        if not user_is_admin(user, group_info.Participants):
            return await event.react("🚫")
    msg = f"Game forcibly stopped by *{event.from_user.name}*"
    msg += f" for reason: {args.strip()}." if args and args.strip() else "."
    if game.players:
        msg += "\n\n*Roles:*\n" + "\n".join(
            f"- {p.name}: {p.role} ({'Alive' if p.is_alive else 'Dead'})"
            for p in game.players.values()
        )
    game.stop()
    await event.reply(msg)

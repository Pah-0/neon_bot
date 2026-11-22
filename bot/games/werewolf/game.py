import asyncio
import random
from datetime import datetime

from bot import Message
from bot.config import bot
from bot.utils.log_utils import logger

from .defaults import (
    ACTUAL_WOLVES,
    AMNESIAC_EXCLUDED,
    DEFAULT_DAY_TIMEOUT,
    DEFAULT_DAY_WARNING,
    DEFAULT_NIGHT_TIMEOUT,
    DEFAULT_NIGHT_WARNING,
    DRUNK_MULTIPLIER,
    GUNNER_MULTIPLIER,
    MAX_PLAYERS,
    MIN_PLAYERS,
    SHARPSHOOTER_MULTIPLIER,
    WOLFCHAT_ROLES,
)
from .lock import demote_dead
from .player import Player
from .roles import gamemodes, roles
from .stats import record


class Game:
    def __init__(self, event, mode=None):
        self.game_id = 0
        self.chat_id = event.chat.id
        self.chat_jid = event.chat.jid

        self.requested_mode = mode
        self.mode = mode or "default"

        self.player_ids = [event.from_user.id]
        self.player_names = {event.from_user.id: event.from_user.name}
        self.player_jids = {event.from_user.id: event.from_user.jid}
        self.players = {}  # user_id: Player object
        self.newly_killed = []  # user_ids

        self.day = False
        self.night = False
        self.waiting = True
        self.in_progress = False
        self.restricted = False
        self.ended = False
        # set whenever someone joins/leaves, used by the lobby auto start
        self.activity = asyncio.Event()

        self.start_time = None
        self.night_start_time = None
        self.day_start_time = None
        # lynch votes wait for the day's discussion to end
        self.voting_open = False
        self.voting_opens_at = None
        self.discussion = 0  # seconds

        self.night_num = 0
        self.day_num = 0
        self.protected = set()  # user_ids guarded or protected tonight
        self.task = None  # the running game_loop
        self.mode_votes = {}  # user_id: mode, from the lobby's mode poll or .vote
        self.mode_poll_id = None
        self.last_seen = {}  # user_id: when they last sent anything, for idle kicks
        self.idle_warned = set()  # user_ids
        self.status_msg_id = None  # the pinned in-game status message
        self.detective_acted = False

        self.day_warning = DEFAULT_DAY_WARNING
        self.day_timeout = DEFAULT_DAY_TIMEOUT
        self.night_warning = DEFAULT_NIGHT_WARNING
        self.night_timeout = DEFAULT_NIGHT_TIMEOUT

    def mode_fits(self, mode):
        data = gamemodes.get(mode, {})
        return bool(data.get("roles")) and (
            data.get("min_players", MIN_PLAYERS)
            <= self.total_players
            <= data.get("max_players", MAX_PLAYERS)
        )

    @property
    def votable_modes(self):
        """The modes picked by chance, which players can vote for"""
        return [m for m, data in gamemodes.items() if data.get("chance", 0) > 0]

    def select_mode_by_chance(self):
        """
        The requested mode, else one a majority of players voted for, else one
        picked by chance, each mode's chance raised by its share of the votes
        """
        if self.requested_mode:
            return self.requested_mode
        votes = {}
        for uid, mode in self.mode_votes.items():
            if uid in self.player_ids:
                votes[mode] = votes.get(mode, 0) + 1
        for mode, count in votes.items():
            if count >= self.total_players // 2 + 1 and self.mode_fits(mode):
                return mode
        available_modes = []
        chances = []
        for m in self.votable_modes:
            if self.mode_fits(m):
                available_modes.append(m)
                chances.append(
                    gamemodes[m]["chance"]
                    + int(votes.get(m, 0) / self.total_players * 200)
                )
        if not available_modes:
            return "default"
        return random.choices(available_modes, weights=chances, k=1)[0]

    @property
    def min_players(self):
        if self.requested_mode:
            return gamemodes[self.requested_mode].get("min_players", MIN_PLAYERS)
        return MIN_PLAYERS

    @property
    def total_players(self):
        return len(self.player_ids)

    @property
    def players_alive_list(self):
        return [p for p in self.players.values() if p.is_alive]

    @property
    def players_alive_count(self):
        return len(self.players_alive_list)

    def get_player_by_id(self, p_id):
        for p in self.players.values():
            if str(p.id) == str(p_id):
                return p
        return None

    def get_player_by_user_id(self, user_id):
        return self.players.get(user_id)

    def set_each_role_numbers_and_pool(self):
        self.mode = self.select_mode_by_chance()
        mode_data = gamemodes.get(self.mode)
        self.role_pool = []
        self.template_pool = []
        idx = self.total_players - 4
        for role_name, counts in mode_data.get("roles", {}).items():
            if idx < 0:
                count = counts[0] if counts else 0
            else:
                count = counts[idx] if idx < len(counts) else counts[-1]
            if count <= 0:
                continue
            if roles.get(role_name)[0] == "template":
                for _ in range(count):
                    self.template_pool.append(role_name)
            else:
                for _ in range(count):
                    self.role_pool.append(role_name)

    def assign_roles(self):
        while not self.game_id:
            _id = random.randint(1000, 9980)
            if _id not in bot.current_games_dict.get("werewolf_ids", []):
                self.game_id = _id
                bot.current_games_dict.setdefault("werewolf_ids", []).append(_id)

        to_assign = list(self.player_ids)
        random.shuffle(to_assign)
        random.shuffle(self.role_pool)

        while len(self.role_pool) < len(to_assign):
            self.role_pool.append("villager")

        for i, user_id in enumerate(to_assign):
            role = self.role_pool[i]
            player_name = self.player_names.get(user_id, "Unknown")
            player = Player(
                role, i + 1, user_id, player_name, self.player_jids.get(user_id)
            )
            self.players[user_id] = player
            if role == "amnesiac":
                player.hidden_role = random.choice(
                    [
                        r
                        for r, data in roles.items()
                        if data[0] != "template" and r not in AMNESIAC_EXCLUDED
                    ]
                )

        for template in self.template_pool:
            eligible_players = [
                p for p in self.players.values() if template not in p.templates
            ]
            random.shuffle(eligible_players)
            for player in eligible_players:
                if template == "cursed villager":
                    if player.role in ACTUAL_WOLVES + ["seer", "fool"]:
                        continue
                elif template == "assassin":
                    if player.role in ACTUAL_WOLVES + ["oracle", "seer", "augur"]:
                        continue
                elif template in ["mayor", "bishop"]:
                    if player.team != "villager":
                        continue

                player.templates.append(template)
                if template == "gunner":
                    player.bullet_count = int(
                        GUNNER_MULTIPLIER * self.total_players + 1
                    )
                    if player.role == "village drunk":
                        player.bullet_count *= DRUNK_MULTIPLIER
                elif template == "sharpshooter":
                    player.bullet_count = int(
                        SHARPSHOOTER_MULTIPLIER * self.total_players + 1
                    )
                elif template == "assassin" and player.role == "village drunk":
                    # too drunk to choose, the target is picked for them
                    player.assassin_target = random.choice(
                        [u for u in self.players if u != player.user_id]
                    )
                break

        self.in_progress = True
        self.waiting = False
        self.start_time = datetime.now()
        self.last_seen = dict.fromkeys(self.players, self.start_time)

    async def send_lobby(self, message, mentions=False):
        """Sends message to the group; mentions makes its @<user_id>s tags."""
        await bot.client.send_message(
            self.chat_jid, message, mentions_are_lids=mentions
        )

    @property
    def able_voters(self):
        """Living players who can vote; the injured can't"""
        return [p for p in self.players_alive_list if not p.is_injured]

    @property
    def votes_to_lynch(self):
        return len(self.able_voters) // 2 + 1

    def tally_votes(self):
        """{user_id voted for, or "abstain": [voters]} of today's valid votes"""
        tally = {}
        for p in self.able_voters:
            target = self.get_player_by_user_id(p.vote)
            if p.vote == "abstain" or (target and target.is_alive):
                tally.setdefault(p.vote, []).append(p)
        return tally

    def vote_counts(self):
        """
        tally_votes() as weights; impatience and influence add a vote,
        pacifism takes one
        """
        counts = {}
        for vote, voters in self.tally_votes().items():
            weight = sum(
                max(
                    0,
                    1
                    + ("impatience_totem" in p.other)
                    + ("influence_totem" in p.other)
                    - ("pacifism_totem" in p.other),
                )
                for p in voters
            )
            if weight:
                counts[vote] = weight
        return counts

    def votes_text(self):
        able = self.able_voters
        tally = self.tally_votes()
        abstainers = tally.pop("abstain", [])
        msg = (
            f"*{self.players_alive_count}* living players, "
            f"*{self.votes_to_lynch}* votes needed to lynch, "
            f"*{len(able)}* can vote, *{len(abstainers)}* abstained.\n"
        )
        if not tally:
            return msg + "No one has voted to lynch yet."
        msg += "*Current votes:*\n"
        for uid, voters in sorted(tally.items(), key=lambda kv: -len(kv[1])):
            target = self.get_player_by_user_id(uid)
            names = ", ".join(v.name for v in voters)
            msg += f"- {target.name} (#{target.id}): {len(voters)} ({names})\n"
        if abstainers:
            msg += f"- Abstain: {
                len(abstainers)} ({
                ', '.join(
                    p.name for p in abstainers)})\n"
        return msg.rstrip()

    def mode_votes_text(self):
        votes = {}
        for uid, mode in self.mode_votes.items():
            if uid in self.player_ids:
                votes.setdefault(mode, []).append(self.player_names.get(uid, "?"))
        msg = (
            f"*{self.total_players}* players in the lobby, "
            f"*{self.total_players // 2 + 1}* votes choose a mode outright; "
            "fewer only make it likelier.\n"
        )
        if self.requested_mode:
            msg += f"The mode is set to *{
                self.requested_mode}*, so votes don't count.\n"
        if not votes:
            return msg + "No one has voted for a mode yet."
        for mode, names in sorted(votes.items(), key=lambda kv: -len(kv[1])):
            unfit = "" if self.mode_fits(mode) else " - wrong player count for it"
            msg += f"- {mode}: {len(names)} ({', '.join(names)}){unfit}\n"
        return msg.rstrip()

    def note_activity(self, user_id):
        """Marks a player as active, clearing an idle warning."""
        if user_id in self.last_seen:
            self.last_seen[user_id] = datetime.now()
            self.idle_warned.discard(user_id)

    def stop(self):
        """Ends the lobby or game at once."""
        self.ended = True
        self.activity.set()  # lets the lobby manager finish
        if self.task:
            self.task.cancel()  # game_loop closes the game when cancelled
        else:
            self.close()

    def status_text(self):
        phase = "Day" if self.day else "Night" if self.night else "Starting"
        msg = f"*Werewolf Game Status ({self.mode})*\n"
        msg += f"Players alive: {self.players_alive_count}/{len(self.players)}\n"
        msg += f"Phase: {phase}\n"
        msg += f"Restricted: {self.restricted}\n"
        msg += "*Players:*\n"
        for p in self.players.values():
            status = "💀" if not p.is_alive else "🟢"
            msg += f"{status} {p.name} (#{p.id})\n"
        return msg.rstrip()

    async def update_status(self):
        """Posts and pins the status message, or edits it once posted."""
        try:
            if self.status_msg_id:
                return await bot.client.edit_message(
                    self.chat_jid,
                    self.status_msg_id,
                    Message(conversation=self.status_text()),
                )
            resp = await bot.client.send_message(self.chat_jid, self.status_text())
            self.status_msg_id = resp.ID
            await bot.client.pin_message(
                self.chat_jid, bot.client.me.JID, resp.ID, 86400
            )
        except Exception:
            await logger(Exception)

    async def unpin_status(self):
        if not self.status_msg_id:
            return
        try:
            await bot.client.pin_message(
                self.chat_jid, bot.client.me.JID, self.status_msg_id, 1
            )
        except Exception:
            await logger(Exception)

    def role_text(self, p):
        """What player p knows about their role, for the start of the game and myrole"""
        msg = f"Your role is *{p.role}*.\n\n{p.description}"
        # being cursed is kept hidden
        if templates := [t for t in p.templates if t != "cursed villager"]:
            msg += f"\n\nTemplates: {', '.join(templates)}"
        if p.bullet_count:
            msg += f"\nBullets left: {p.bullet_count}"
        if p.totem:
            msg += f"\n\nYour totem tonight is: *{p.totem.replace('_', ' ')}*."
        if p.role == "minion":
            msg += "\n\nThe wolves are: " + ", ".join(
                f"{w.name} (#{w.id})"
                for w in self.players.values()
                if w.team == "wolf" and w.role not in ["minion", "cultist"]
            )
        if p.role == "turncoat":
            side = "wolves" if "side:wolves" in p.other else "villagers"
            msg += f"\n\nYou are siding with the *{side}*."
        if p.role == "executioner" and (
            target := self.get_player_by_user_id(p.execution_target)
        ):
            msg += f"\n\nYour target for lynch is *{target.name}*."
        if target := self.get_player_by_user_id(p.assassin_target):
            msg += f"\n\nYour assassination target is *{target.name}*."
        if lovers := [self.get_player_by_user_id(u) for u in p.loves]:
            msg += "\n\nYou are in love with: " + ", ".join(
                f"*{x.name}*" for x in lovers if x
            )
        wolfchat = [w for w in self.players_alive_list if w.role in WOLFCHAT_ROLES]
        if p in wolfchat and len(wolfchat) > 1:
            msg += "\n\nYour team: " + ", ".join(
                f"{w.name} ({w.role})" for w in wolfchat if w is not p
            )
        if not p.is_alive:
            msg += "\n\nYou are dead."
        return msg

    async def send_pm(self, player, message):
        try:
            await bot.client.send_message(player.jid, message)
        except Exception:
            pass

    async def wolfchat(self, message):
        for p in self.players.values():
            if p.is_alive and p.role in WOLFCHAT_ROLES:
                await self.send_pm(p, f"[WOLFCHAT] {message}")

    async def kill(self, player, cause="night"):
        """
        Kills a player and handles everything tied to their death;
        returns a message describing deaths caused as a side effect.
        """
        msg = ""
        if not player.is_alive:
            return msg
        player.is_alive = False
        record(player.user_id, deaths=1)
        if self.restricted:
            await demote_dead(self, player)
        # idling out has none of the effects a death by a player has
        by_player = cause != "idle"
        if player.role == "wolf cub" and by_player:
            for wolf in self.players_alive_list:
                if wolf.role in ACTUAL_WOLVES + ["traitor"]:
                    wolf.add_other("angry")
            await self.wolfchat(
                "The wolf cub has died! You are enraged and will kill two players tomorrow night."
            )
        if player.role == "time lord" and by_player and self.day_timeout > 60:
            self.day_warning, self.day_timeout = 45, 60
            self.night_warning, self.night_timeout = 20, 30
            msg += (
                "The time lord has died. Night will now only last *30* seconds, "
                "and day *60* seconds. Better be speedy!\n"
            )
        for p in self.players_alive_list:
            if p.assassin_target == player.user_id:
                p.assassin_target = None  # they may choose another target
                if p.role == "village drunk":
                    p.assassin_target = random.choice(
                        [x.user_id for x in self.players_alive_list if x is not p]
                    )
        if (
            "assassin" in player.templates
            and by_player
            and (target := self.get_player_by_user_id(player.assassin_target))
            and target.is_alive
        ):
            if self.is_safe(target):
                msg += f"Before dying, *{
                    player.name}* tries to slit *{
                    target.name}*'s throat, but fails.\n"
            else:
                msg += (
                    f"Before dying, *{player.name}* quickly slits *{target.name}*'s throat. "
                    f"The village mourns the loss of a *{target.death_role}*.\n"
                )
                msg += await self.kill(target, "assassination")
        if player.role == "mad scientist" and by_player:
            for target in self.mad_scientist_targets(player):
                if self.is_safe(target):
                    msg += f"*{player.name}*'s chemical misses *{target.name}*.\n"
                    continue
                msg += (
                    f"*{player.name}* throws a potent chemical into the crowd. "
                    f"*{target.name}*, a *{target.death_role}*, is hit and dies.\n"
                )
                msg += await self.kill(target, "mad scientist")
        if player.role == "vengeful ghost":
            # the ghost haunts the team that killed it
            player.vengeance_against = "village" if cause == "lynch" else "wolf"
        for cloner in self.players_alive_list:
            if cloner.role == "clone" and cloner.cloning_target == player.user_id:
                cloner.role, cloner.team = player.role, player.team
                await self.send_pm(
                    cloner, f"Your clone target died. You are now a *{cloner.role}*!"
                )
        for l_id in player.loves:
            lover = self.get_player_by_user_id(l_id)
            if lover and lover.is_alive:
                msg += (
                    f"*{lover.name}* committed suicide out of grief for their lover.\n"
                )
                msg += await self.kill(lover, "suicide")
        return msg

    def is_safe(self, player):
        """Whether player escapes an assassin or mad scientist"""
        return (
            player.is_blessed
            or "protection_totem" in player.other
            or player.user_id in self.protected
        )

    def mad_scientist_targets(self, scientist):
        """
        The players beside the mad scientist in #id order, if alive; past the
        dead to the nearest living ones in big, random or rapidfire games
        """
        order = sorted(self.players.values(), key=lambda p: p.id)
        skip_dead = len(order) > 15 or self.mode in ["random", "rapidfire"]
        i = order.index(scientist)
        targets = []
        for step in (-1, 1):
            j = (i + step) % len(order)
            while skip_dead and not order[j].is_alive and order[j] is not scientist:
                j = (j + step) % len(order)
            if order[j].is_alive and order[j] not in targets:
                targets.append(order[j])
        return targets

    def close(self):
        """Removes the game from the running games."""
        games = bot.current_games_dict.get("werewolf", {})
        if games.get(self.chat_id) is self:
            games.pop(self.chat_id)
        if self.game_id in bot.current_games_dict.get("werewolf_ids", []):
            bot.current_games_dict["werewolf_ids"].remove(self.game_id)

    async def join(self, event, notify=False):
        if not self.waiting:
            return await event.reply("The game has already started!")
        if event.from_user.id in self.player_ids:
            return await event.reply("You've already joined the game!")
        if len(self.player_ids) >= MAX_PLAYERS:
            return await event.reply("The game is full!")
        self.player_ids.append(event.from_user.id)
        self.player_names[event.from_user.id] = event.from_user.name
        self.player_jids[event.from_user.id] = event.from_user.jid
        self.activity.set()
        return (
            await event.reply(
                f"{event.from_user.name} has joined the game. ({self.total_players}/{MAX_PLAYERS})"
            )
            if not notify
            else await event.reply(
                f"@{event.user.id} has joined the game. ({self.total_players}/{MAX_PLAYERS})",
                quote=False,
            )
        )

    async def leave(self, event, notify=False):
        if not self.waiting:
            return await event.reply("The game has already started!")
        if event.from_user.id not in self.player_ids:
            return await event.reply("You've not joined the game!")
        self.player_ids.remove(event.from_user.id)
        self.mode_votes.pop(event.from_user.id, None)
        self.player_names.pop(event.from_user.id, None)
        self.player_jids.pop(event.from_user.id, None)
        self.activity.set()
        if not self.player_ids:
            self.close()
            return await event.reply("Everyone left, the game lobby has been closed.")
        return (
            await event.reply(
                f"{event.from_user.name} has left the game. ({self.total_players}/{MAX_PLAYERS})"
            )
            if not notify
            else await event.reply(
                f"@{event.user.id} has left the game. ({self.total_players}/{MAX_PLAYERS})",
                quote=False,
            )
        )

    async def status(self, event):
        if self.waiting:
            msg = f"*Werewolf Game Lobby*\n"
            msg += f"Mode: {
                self.requested_mode or 'Auto (by chance and votes)'}\n"
            msg += f"Players: {self.total_players}\n"
            msg += "*Joined players:*\n"
            for p_id in self.player_ids:
                name = self.player_names.get(p_id, "Unknown")
                msg += f"- {name}\n"
            await event.reply(msg)
        else:
            await event.reply(self.status_text())

    async def check_traitor(self):
        wolves = [p for p in self.players_alive_list if p.role in ACTUAL_WOLVES]
        if wolves and all(p.role == "wolf cub" for p in wolves):
            # orphaned cubs grow up to avenge their parents
            for cub in wolves:
                cub.role = "wolf"
                await self.send_pm(
                    cub,
                    "You have grown up into a wolf and vowed to take revenge for your dead parents!",
                )
            await self.send_lobby(
                "*The villagers listen horrified as they hear growling deepen in pitch. "
                "The wolf will do whatever it takes to avenge their parents!*"
            )
        if not wolves:
            traitors = [p for p in self.players_alive_list if p.role == "traitor"]
            for t in traitors:
                t.role = "wolf"
                t.team = "wolf"
                await self.send_pm(
                    t, "All other wolves are dead! You have turned into a *wolf*!"
                )
                await self.wolfchat(f"{t.name} has turned into a wolf!")

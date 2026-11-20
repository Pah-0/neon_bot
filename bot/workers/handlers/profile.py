import asyncio
import time
import uuid
from inspect import getdoc

import enka

from bot.config import bot, conf
from bot.utils.buttons import on_button, send_buttons, sender_ids
from bot.utils.db_utils import save2db2
from bot.utils.hoyo_cards import build_card, profile_card
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    chat_is_allowed,
    construct_msg_and_evt,
    get_args,
    target_user,
    user_is_allowed,
    user_is_privileged,
)
from bot.utils.sudo_button_utils import active_poll_dict, create_sudo_button

# A WhatsApp poll takes at most 12 options, as many as a showcase holds
MAX_CHOICES = 12
# With no room for a Done option, cards go out once the ticking stops
PICK_IDLE = 15
GAMES = {
    "gi": {
        "name": "Genshin",
        "client": lambda: enka.GenshinClient(enka.gi.Language.ENGLISH),
        "uid_lengths": (9, 10),
        "level": "AR",
        "cons": "C",
    },
    "hsr": {
        "name": "Star Rail",
        "client": lambda: enka.HSRClient(enka.hsr.Language.ENGLISH),
        "uid_lengths": (9, 10),
        "level": "TL",
        "cons": "E",
    },
    "zzz": {
        "name": "ZZZ",
        "client": lambda: enka.ZZZClient(enka.zzz.Language.ENGLISH),
        "uid_lengths": (8, 9, 10),
        "level": "Inter-Knot Lv.",
        "cons": "M",
    },
}
ERRORS = {
    enka.errors.WrongUIDFormatError: "*That isn't a valid {name} UID.*",
    enka.errors.ProfileUnavailableError: "*That player's profile is private.*",
    enka.errors.PlayerDoesNotExistError: "*No {name} player has that UID.*",
    enka.errors.GameMaintenanceError: "*{name} is under maintenance; try again later.*",
    enka.errors.RateLimitedError: "*Enka.Network is busy; try again in a minute.*",
}

_clients = {}
_client_lock = asyncio.Lock()
# Profiles whose character buttons can still be pressed: token -> showcase
# and how to draw its cards; the oldest go first, and they expire
PROFILE_BUTTONS_MAX = 50
PROFILE_BUTTONS_TTL = 3600
_profile_buttons: dict[str, dict] = {}


async def get_client(game: str):
    """One Enka client per game, so its assets download only once"""
    async with _client_lock:
        if game not in _clients:
            client = GAMES[game]["client"]()
            await client.start()
            _clients[game] = client
        return _clients[game]


def showcase_of(game: str, showcase) -> list:
    return showcase.agents if game == "zzz" else showcase.characters


def find_character(characters: list, choice: str):
    """A showcase character by its number or (part of) its name"""
    if choice.isdigit():
        index = int(choice) - 1
        return characters[index] if 0 <= index < len(characters) else None
    choice = choice.casefold()
    for character in characters:
        if character.name.casefold().startswith(choice):
            return character
    for character in characters:
        if choice in character.name.casefold():
            return character
    return None


async def pick_characters(event, characters: list) -> list:
    """
    Sends a poll of the showcase where several can be ticked; the choice is
    taken PICK_IDLE seconds after the last tick. The chosen characters in
    showcase order, or [] if nothing was ticked within 5 minutes.
    """
    options = {
        i: [f"{i + 1}. {c.name} (Lv. {c.level})", c.name]
        for i, c in enumerate(characters[:MAX_CHOICES])
    }
    state = {"chosen": [], "changed": asyncio.Event()}

    async def on_vote(vote, poll_key):
        if vote.from_user.id != event.from_user.id:
            return
        info = active_poll_dict.get(poll_key.ID) or {}
        selected = await bot.client.decrypt_poll_vote(vote.message)
        state["chosen"] = [
            info[o.hex()] for o in selected.selectedOptions if o.hex() in info
        ]
        state["changed"].set()

    title = (
        f"{event.from_user.name}, tick the characters you want build cards for. "
        f"They're sent {PICK_IDLE} seconds after your last tick."
    )
    poll_msg_, msg_ = await create_sudo_button(
        title,
        options,
        event.chat.jid,
        event.from_user.id,
        len(options),
        None,
        event.message,
        callback=on_vote,
    )
    poll_msg = construct_msg_and_evt(
        event.chat.id,
        bot.client.me.JID.User,
        msg_.ID,
        None,
        event.chat.server,
        bot.client.me.JID.Server,
        poll_msg_,
    )
    try:
        await asyncio.wait_for(state["changed"].wait(), timeout=300)
        while True:
            state["changed"].clear()
            try:
                await asyncio.wait_for(state["changed"].wait(), timeout=PICK_IDLE)
            except asyncio.TimeoutError:
                break
    except asyncio.TimeoutError:
        pass
    finally:
        active_poll_dict.pop(msg_.ID, None)
        await poll_msg.delete()
    return [
        characters[i] for i in sorted(k for k in state["chosen"] if isinstance(k, int))
    ]


async def send_character_buttons(event, game, characters, owner, uid, scale):
    """Buttons under a profile card, one per showcase character"""
    now = time.time()
    for token in [
        t for t, p in _profile_buttons.items() if now - p["at"] > PROFILE_BUTTONS_TTL
    ]:
        _profile_buttons.pop(token, None)
    while len(_profile_buttons) >= PROFILE_BUTTONS_MAX:
        _profile_buttons.pop(next(iter(_profile_buttons)))
    token = uuid.uuid4().hex[:10]
    _profile_buttons[token] = {
        "game": game,
        "characters": list(characters),
        "owner": owner,
        "uid": uid,
        "scale": scale,
        "at": now,
        # Only whoever sent the command can press them
        "by": sender_ids(event),
    }
    await send_buttons(
        event.chat.jid,
        "Build cards:",
        [(f"profile:{token}:{i}", c.name) for i, c in enumerate(characters)],
    )


@on_button("profile")
async def character_button(event, data: str):
    """Sends the build card of the character whose button was pressed"""
    token, _, index = data.partition(":")
    profile = _profile_buttons.get(token)
    if profile and not sender_ids(event) & profile["by"]:
        return await event.react("🙅")
    if not profile or time.time() - profile["at"] > PROFILE_BUTTONS_TTL:
        name = GAMES.get(profile["game"] if profile else "", {}).get("name", "")
        return await event.reply(
            f"*That profile has expired; send {conf.CMD_PREFIX}gi, "
            f"{conf.CMD_PREFIX}hsr or {conf.CMD_PREFIX}zzz again.*"
            if not name
            else f"*That {name} profile has expired; send it again.*"
        )
    characters = profile["characters"]
    if not index.isdigit() or int(index) >= len(characters):
        return
    character = characters[int(index)]
    try:
        async with event.react("⏳"):
            await event.reply_photo(
                await build_card(
                    profile["game"],
                    character,
                    profile["owner"],
                    profile["uid"],
                    profile["scale"],
                )
            )
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        await event.reply(f"*Error:*\n{e}")


async def save_uid(event, game: str, uid: str):
    user = bot.user_dict.setdefault(event.from_user.hid, {})
    name = GAMES[game]["name"]
    if uid:
        user[f"{game}_uid"] = uid
        msg = f"*Saved your {name} UID:* {uid}\nNow just send {conf.CMD_PREFIX}{game}."
    else:
        user.pop(f"{game}_uid", None)
        msg = f"*Your saved {name} UID has been removed.*"
    await save2db2(bot.user_dict, "users")
    await event.reply(msg)


async def game_profile(event, args, game: str):
    """The .gi/.hsr/.zzz command for game; each wrapper below holds its help"""
    info = GAMES[game]
    help_text = getdoc(info["handler"])
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    flags, rest = get_args(
        "-set",
        ["-unset", "store_true"],
        ["-hide", "store_true"],
        # -h hides too after a UID; alone it's the bot's help
        ["-h", "store_true"],
        ["-c", "store_true"],
        ["-2x", "store_true"],
        to_parse=args,
        get_unknown=True,
    )
    if flags.set is not None or flags.unset:
        uid = flags.set or ""
        if flags.set is not None and not (
            uid.isdigit() and len(uid) in info["uid_lengths"]
        ):
            return await event.reply(f"*Give a {info['name']} UID to save.*")
        return await save_uid(event, game, uid)
    hide = flags.hide or flags.h
    pick = flags.c
    # -2x draws the cards at twice the resolution
    scale = 2 if getattr(flags, "2x") else 1
    words = rest.split()
    if words and words[0].isdigit() and len(words[0]) in info["uid_lengths"]:
        uid, words = words[0], words[1:]
    elif (words and words[0][0] in "@+") or event.reply_to_message:
        # Someone else's saved UID: @mention, +number, or a reply to them
        lid, number, rest = await target_user(event, " ".join(words))
        words = rest.split()
        if not (lid or number):
            return await event.reply("*Mention them, or reply to their message.*")
        saved = bot.user_dict.get(lid, {}) or bot.user_dict.get(number, {})
        if not (uid := saved.get(f"{game}_uid")):
            return await event.reply(
                f"*They haven't saved a {info['name']} UID* "
                f"(with {conf.CMD_PREFIX}{game} -set <uid>)."
            )
    else:
        uid = bot.user_dict.get(event.from_user.hid, {}).get(f"{game}_uid")
    if not uid:
        return await event.reply(help_text)
    choice = " ".join(words)
    try:
        # A reaction while working, rather than status messages
        async with event.react("⏳"):
            client = await get_client(game)
            try:
                showcase = await client.fetch_showcase(uid)
            except enka.errors.EnkaAPIError as e:
                for error, msg in ERRORS.items():
                    if isinstance(e, error):
                        return await event.reply(msg.format(name=info["name"]))
                raise
        player, characters = showcase.player, showcase_of(game, showcase)
        uid_text = "UID: [Hidden]" if hide else f"UID {uid}"
        owner = f"{player.nickname} · {uid_text}"

        if choice:
            character = find_character(characters, choice)
            if character is None:
                return await event.reply(
                    f"*{choice} isn't in {player.nickname}'s showcase.*"
                )
            async with event.react("⏳"):
                return await event.reply_photo(
                    await build_card(game, character, owner, uid, scale)
                )

        if pick:
            if not characters:
                return await event.reply(
                    "*No characters are public in their showcase.*"
                )
            # With one character there's nothing to pick
            chosen = (
                characters
                if len(characters) == 1
                else await pick_characters(event, characters)
            )
            for character in chosen:
                async with event.react("⏳"):
                    await event.reply_photo(
                        await build_card(game, character, owner, uid, scale)
                    )
            return

        caption = f"*{
            player.nickname}* · {
            info['level']} {
            player.level} · {uid_text}"
        caption += (
            "\nTap a character below for their build card, or add -c to pick several."
            if characters
            else "\n_No characters are public in their showcase._"
        )
        async with event.react("⏳"):
            card = await profile_card(game, player, characters, uid_text, scale)
            await event.reply_photo(card, caption)
        if characters:
            await send_character_buttons(event, game, characters, owner, uid, scale)
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        await event.reply(f"*Error:*\n{e}")


async def gi_profile(event, args, client):
    """
    Shows a Genshin Impact player's profile card from Enka.Network: Adventure
    Rank, Spiral Abyss, Imaginarium Theater and Stygian Onslaught progress, and
    the characters in their in-game showcase. Build cards show stats, weapon,
    artifacts with crit value and roll counts, and the Akasha rank.

    Usage: gi [uid] [character] [-c] [-2x] [-hide]
        uid: a Genshin UID; leave it out to use the one you saved
            (or @mention someone, or reply to them, to use the UID they saved)
        character: a showcase number or name, to go straight to its build card
            (or tap a character's button under the profile card)
        -c : pick characters for build cards from a poll; tick as many as you
            like, and their cards are sent 15 seconds after your last tick.
            With only one in the showcase, its card is sent straight away
        -2x : draw the cards at twice the resolution, for a sharper image
        -hide : hide the UID on the cards
        -set <uid> : save your Genshin UID
        -unset : forget your saved Genshin UID
    Characters only show if "Show Character Details" is on in-game.
    """
    await game_profile(event, args, "gi")


async def hsr_profile(event, args, client):
    """
    Shows a Honkai: Star Rail player's profile card from Enka.Network:
    Trailblaze and Equilibrium levels, achievements and collection, and the
    characters in their in-game showcase. Build cards show stats, light cone,
    relics with crit value and roll counts, traces and eidolons.

    Usage: hsr [uid] [character] [-c] [-2x] [-hide]
        uid: a Star Rail UID; leave it out to use the one you saved
            (or @mention someone, or reply to them, to use the UID they saved)
        character: a showcase number or name, to go straight to its build card
            (or tap a character's button under the profile card)
        -c : pick characters for build cards from a poll; tick as many as you
            like, and their cards are sent 15 seconds after your last tick.
            With only one in the showcase, its card is sent straight away
        -2x : draw the cards at twice the resolution, for a sharper image
        -hide : hide the UID on the cards
        -set <uid> : save your Star Rail UID
        -unset : forget your saved Star Rail UID
    Characters only show if they're in the in-game showcase.
    """
    await game_profile(event, args, "hsr")


async def zzz_profile(event, args, client):
    """
    Shows a Zenless Zone Zero player's profile card from Enka.Network:
    Inter-Knot level, title, Shiyu Defense, Simulated Battle Tower and Deadly
    Assault medals, and the agents in their in-game showcase. Build cards show
    stats, W-Engine, drive discs with crit value and roll counts, skills,
    mindscapes and the Inter-Knot rank.

    Usage: zzz [uid] [agent] [-c] [-2x] [-hide]
        uid: a ZZZ UID; leave it out to use the one you saved
            (or @mention someone, or reply to them, to use the UID they saved)
        agent: a showcase number or name, to go straight to its build card
            (or tap an agent's button under the profile card)
        -c : pick agents for build cards from a poll; tick as many as you
            like, and their cards are sent 15 seconds after your last tick.
            With only one in the showcase, its card is sent straight away
        -2x : draw the cards at twice the resolution, for a sharper image
        -hide : hide the UID on the cards
        -set <uid> : save your ZZZ UID
        -unset : forget your saved ZZZ UID
    Agents only show if they're in the in-game showcase.
    """
    await game_profile(event, args, "zzz")


GAMES["gi"]["handler"] = gi_profile
GAMES["hsr"]["handler"] = hsr_profile
GAMES["zzz"]["handler"] = zzz_profile

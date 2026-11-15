import asyncio
from inspect import getdoc

import enka

from bot.config import bot, conf
from bot.utils.db_utils import save2db2
from bot.utils.hoyo_cards import build_card, profile_card
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    chat_is_allowed,
    construct_msg_and_evt,
    user_is_allowed,
    user_is_privileged,
)
from bot.utils.sudo_button_utils import create_sudo_button, wait_for_button_response

# A WhatsApp poll takes at most 12 options, as many as a showcase holds
MAX_CHOICES = 12
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


async def pick_character(event, characters: list):
    """Sends a poll of the showcase; the chosen character, or None"""
    options = {
        i: [f"{i + 1}. {c.name} (Lv. {c.level})", c.name]
        for i, c in enumerate(characters[:MAX_CHOICES])
    }
    title = f"{event.from_user.name}, pick a character for their build card."
    poll_msg_, msg_ = await create_sudo_button(
        title, options, event.chat.jid, event.from_user.id, 1, None, event.message
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
    choice = await wait_for_button_response(msg_.ID)
    await poll_msg.delete()
    return characters[choice[0]] if choice else None


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
    words = args.split()
    if words and words[0].casefold() in ("-set", "-unset"):
        setting = words[0].casefold() == "-set"
        uid = words[1] if setting and len(words) > 1 else ""
        if setting and not (uid.isdigit() and len(uid) in info["uid_lengths"]):
            return await event.reply(f"*Give a {info['name']} UID to save.*")
        return await save_uid(event, game, uid)
    hide = any(w.casefold() in ("-hide", "-h") for w in words)
    words = [w for w in words if w.casefold() not in ("-hide", "-h")]
    if words and words[0].isdigit() and len(words[0]) in info["uid_lengths"]:
        uid, words = words[0], words[1:]
    else:
        uid = bot.user_dict.get(event.from_user.hid, {}).get(f"{game}_uid")
    if not uid:
        return await event.reply(help_text)
    choice = " ".join(words)
    status = None
    try:
        shown = uid if not hide else "that UID"
        status = await event.reply(
            f"*Fetching the {info['name']} profile for {shown}…*"
        )
        client = await get_client(game)
        try:
            showcase = await client.fetch_showcase(uid)
        except enka.errors.EnkaAPIError as e:
            for error, msg in ERRORS.items():
                if isinstance(e, error):
                    await status.edit(msg.format(name=info["name"]))
                    status = None
                    return
            raise
        player, characters = showcase.player, showcase_of(game, showcase)
        uid_text = "UID: [Hidden]" if hide else f"UID {uid}"
        owner = f"{player.nickname} · {uid_text}"

        if choice:
            character = find_character(characters, choice)
            if character is None:
                await status.edit(f"*{choice} isn't in {player.nickname}'s showcase.*")
                status = None
                return
            await status.edit(f"*Drawing {character.name}'s card…*")
            return await event.reply_photo(
                await build_card(game, character, owner, uid)
            )

        await status.edit("*Drawing the profile card…*")
        caption = f"*{
            player.nickname}* · {
            info['level']} {
            player.level} · {uid_text}"
        caption += (
            "\nPick a character from the poll for their build card."
            if characters
            else "\n_No characters are public in their showcase._"
        )
        card = await profile_card(game, player, characters, uid_text)
        await event.reply_photo(card, caption)
        await status.delete()
        status = None
        if not characters:
            return
        character = await pick_character(event, characters)
        if character is None:
            return
        status = await event.reply(f"*Drawing {character.name}'s card…*")
        await event.reply_photo(await build_card(game, character, owner, uid))
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        if status:
            await status.edit(f"*Error:*\n{e}")
            status = None
    finally:
        if status:
            await status.delete()


async def gi_profile(event, args, client):
    """
    Shows a Genshin Impact player's profile card from Enka.Network: Adventure
    Rank, Spiral Abyss, Imaginarium Theater and Stygian Onslaught progress, and
    the characters in their in-game showcase. Pick one from the poll for its
    build card (stats, weapon, artifacts with crit value).

    Usage: gi [uid] [character] [-hide]
        uid: a Genshin UID; leave it out to use the one you saved
        character: a showcase number or name, to go straight to its build card
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
    characters in their in-game showcase. Pick one from the poll for its build
    card (stats, light cone, relics with crit value, traces, eidolons).

    Usage: hsr [uid] [character] [-hide]
        uid: a Star Rail UID; leave it out to use the one you saved
        character: a showcase number or name, to go straight to its build card
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
    Assault medals, and the agents in their in-game showcase. Pick one from the
    poll for its build card (stats, W-Engine, drive discs with crit value,
    skills, mindscapes).

    Usage: zzz [uid] [agent] [-hide]
        uid: a ZZZ UID; leave it out to use the one you saved
        agent: a showcase number or name, to go straight to its build card
        -hide : hide the UID on the cards
        -set <uid> : save your ZZZ UID
        -unset : forget your saved ZZZ UID
    Agents only show if they're in the in-game showcase.
    """
    await game_profile(event, args, "zzz")


GAMES["gi"]["handler"] = gi_profile
GAMES["hsr"]["handler"] = hsr_profile
GAMES["zzz"]["handler"] = zzz_profile

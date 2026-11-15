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
ERRORS = {
    enka.errors.WrongUIDFormatError: "*That isn't a valid Genshin UID.*",
    enka.errors.ProfileUnavailableError: "*That player's profile is private.*",
    enka.errors.PlayerDoesNotExistError: "*No player has that UID.*",
    enka.errors.GameMaintenanceError: "*Genshin is under maintenance; try again later.*",
    enka.errors.RateLimitedError: "*Enka.Network is busy; try again in a minute.*",
}

_client = None
_client_lock = asyncio.Lock()


async def get_client() -> enka.GenshinClient:
    """One Enka client for the bot, so game assets download only once"""
    global _client
    async with _client_lock:
        if _client is None:
            client = enka.GenshinClient(enka.gi.Language.ENGLISH)
            await client.start()
            _client = client
        return _client


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


async def save_uid(event, uid: str):
    user = bot.user_dict.setdefault(event.from_user.hid, {})
    if uid:
        user["gi_uid"] = uid
        msg = (
            f"*Saved your Genshin UID:* {uid}\nNow just send {conf.CMD_PREFIX}profile."
        )
    else:
        user.pop("gi_uid", None)
        msg = "*Your saved Genshin UID has been removed.*"
    await save2db2(bot.user_dict, "users")
    await event.reply(msg)


async def profile(event, args, client):
    """
    Shows a Genshin Impact player's profile from Enka.Network: Adventure Rank,
    Spiral Abyss, Imaginarium Theater and Stygian Onslaught progress, and the
    characters in their in-game showcase. Pick one from the poll for its build
    card.

    Usage: {pre}profile [uid] [character] [-h]
        uid: a Genshin UID; leave it out to use the one you saved
        character: a showcase number or name, to go straight to its build card
        -h : hide the UID on the cards
        -set <uid> : save your UID
        -unset : forget your saved UID
    Characters only show if "Show Character Details" is on in-game.
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    words = args.split()
    if words and words[0].casefold() in ("-set", "-unset"):
        uid = words[1] if len(words) > 1 and words[0].casefold() == "-set" else ""
        if words[0].casefold() == "-set" and not (
            uid.isdigit() and 9 <= len(uid) <= 10
        ):
            return await event.reply("*Give a 9 or 10 digit Genshin UID to save.*")
        return await save_uid(event, uid)
    hide = "-h" in (w.casefold() for w in words)
    words = [w for w in words if w.casefold() != "-h"]
    if words and words[0].isdigit() and len(words[0]) >= 9:
        uid, words = words[0], words[1:]
    else:
        uid = bot.user_dict.get(event.from_user.hid, {}).get("gi_uid")
    if not uid:
        return await event.reply(getdoc(profile).format(pre=conf.CMD_PREFIX))
    choice = " ".join(words)
    status = None
    try:
        status = await event.reply(
            f"*Fetching the profile for {uid if not hide else 'that UID'}…*"
        )
        enka_client = await get_client()
        try:
            showcase = await enka_client.fetch_showcase(uid)
        except enka.errors.EnkaAPIError as e:
            for error, msg in ERRORS.items():
                if isinstance(e, error):
                    await status.edit(msg)
                    status = None
                    return
            raise
        player, characters = showcase.player, showcase.characters
        uid_text = "UID: [Hidden]" if hide else f"UID {uid}"
        owner = f"{player.nickname} · {uid_text}"

        if choice:
            character = find_character(characters, choice)
            if character is None:
                await status.edit(f"*{choice} isn't in {player.nickname}'s showcase.*")
                status = None
                return
            await status.edit(f"*Drawing {character.name}'s card…*")
            return await event.reply_photo(await build_card(character, owner))

        await status.edit("*Drawing the profile card…*")
        caption = f"*{player.nickname}* · AR {player.level} · {uid_text}"
        if characters:
            caption += "\nPick a character from the poll for their build card."
        elif player.showcase_characters:
            caption += "\n_Their character details are hidden in-game._"
        await event.reply_photo(
            await profile_card(player, characters, uid_text), caption
        )
        await status.delete()
        status = None
        if not characters:
            return
        character = await pick_character(event, characters)
        if character is None:
            return
        status = await event.reply(f"*Drawing {character.name}'s card…*")
        await event.reply_photo(await build_card(character, owner))
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        if status:
            await status.edit(f"*Error:*\n{e}")
            status = None
    finally:
        if status:
            await status.delete()

import asyncio
from inspect import getdoc

from bot.config import bot
from bot.utils.bot_utils import split_text
from bot.utils.hoyo_utils import (
    GAMES,
    KINDS,
    SECTIONS,
    get_details,
    get_index,
    label,
    search,
)
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    chat_is_allowed,
    construct_msg_and_evt,
    user_is_allowed,
    user_is_privileged,
)
from bot.utils.sudo_button_utils import create_sudo_button, wait_for_button_response

# A WhatsApp poll takes at most 12 options
MAX_CHOICES = 12
KIND_FLAGS = {"-char": "character", "-weapon": "weapon", "-set": "set"}


async def pick_result(event, results: list[dict]) -> dict | None:
    """Sends a poll to choose between results; None on timeout"""
    options = {}
    for i, entry in enumerate(results[:MAX_CHOICES]):
        kind = KINDS[entry["game"]][entry["kind"]]
        text = f"{label(entry)} ({entry['game'].upper()} {kind})"
        options[i] = [f"{i + 1}. {text}", text]
    title = f"{event.from_user.name} please select the one you meant."
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
    return results[choice[0]] if choice else None


async def hoyo_search(event, args, client):
    """
    Looks up a character, weapon or set in Genshin Impact, Honkai: Star Rail
    or Zenless Zone Zero. If a name matches several, a poll is sent to pick one.
    Data from Project Amber, Project Yatta and Hakushin.

    Usage: search <name> [flags]
    Flags:
        -gi, -hsr, -zzz : only search this game
        -char, -weapon, -set : only search characters, weapons (light cones,
            W-Engines) or sets (artifacts, relics, drive discs)
        -t : also send a character's talents, traces or skills
        -c : also send a character's constellations, eidolons or mindscapes
        -full (or -f) : send both, like -t -c
        -lv <n> : the talent/trace/skill level for -t's scaling (default:
            10 for Genshin, each trace's cap for Star Rail, 12 for ZZZ)
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    game = kind = level = None
    sections, words = [], []
    tokens = args.split()
    while tokens:
        word = tokens.pop(0)
        flag = word.casefold()
        if flag.startswith("-lv"):
            number = flag[3:] or (tokens.pop(0) if tokens else "")
            if number.isdigit() and 1 <= int(number) <= 15:
                level = int(number)
            else:
                return await event.reply("*-lv takes a level from 1 to 15.*")
        elif flag.lstrip("-") in GAMES and flag.startswith("-"):
            game = flag.lstrip("-")
        elif flag in KIND_FLAGS:
            kind = KIND_FLAGS[flag]
        elif flag in ("-t", "-c"):
            sections.append(flag[1])
        elif flag in ("-full", "-f"):
            sections += ["t", "c"]
        else:
            words.append(word)
    query = " ".join(words)
    if not query:
        return await event.reply(getdoc(hoyo_search))
    status = None
    try:
        status = await event.reply(f"*Searching for {query}…*")
        results = search(await get_index(), query, game, kind)
        if not results:
            await status.edit(f"*Nothing found.*\nYou searched for *{query}*.")
            status = None
            return
        if len(results) == 1:
            entry = results[0]
        else:
            await status.delete()
            status = None
            entry = await pick_result(event, results)
            if not entry:
                return await event.reply("Time out!")
            status = await event.reply(f"*Fetching {label(entry)}…*")
        image, caption, extra = await get_details(entry, level)
        if sections and entry["kind"] == "character":
            names = SECTIONS[entry["game"]]
            caption += (
                "\n_"
                + ", ".join(names[s] for s in dict.fromkeys(sections))
                + " below._"
            )
        if image:
            await event.reply_photo(image, caption)
        else:
            await event.reply(caption)
        for section in dict.fromkeys(sections):
            if not (text := extra.get(section)):
                continue
            title = f"*{label(entry)}: {SECTIONS[entry['game']][section]}*\n\n"
            for chunk in split_text(
                title + text, "\n\n", list_size=bot.max_message_length - 100
            ):
                await event.reply(chunk.strip())
                await asyncio.sleep(1)
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        if status:
            await status.edit(f"*Error:*\n{e}")
            status = None
    finally:
        if status:
            await status.delete()

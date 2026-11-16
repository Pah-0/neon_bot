import asyncio
import re
import time
import uuid
from inspect import getdoc

from bot.config import bot, conf
from bot.utils.bot_utils import split_text
from bot.utils.buttons import on_button, send_buttons, sender_ids
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
    get_args,
    user_is_allowed,
    user_is_privileged,
)
from bot.utils.sudo_button_utils import create_sudo_button, wait_for_button_response

# Matches offered as buttons (10 per message); a poll takes at most 12
MAX_CHOICES = 20
MAX_POLL_CHOICES = 12
KIND_FLAGS = {"-char": "character", "-weapon": "weapon", "-set": "set"}
# Searches whose buttons can still be pressed: token -> what they need; the
# oldest go first, and they expire
SEARCH_BUTTONS_MAX = 50
SEARCH_BUTTONS_TTL = 3600
_search_buttons: dict[str, dict] = {}


async def pick_result(event, results: list[dict]) -> dict | None:
    """Sends a poll to choose between results; None on timeout"""
    options = {}
    for i, entry in enumerate(results[:MAX_POLL_CHOICES]):
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


async def pick_by_poll(event, results: list[dict], level, sections: list[str]):
    entry = await pick_result(event, results)
    if not entry:
        return await event.reply("Time out!")
    await show_entry(event, entry, level, sections)


def remember(event, **data) -> str:
    """Keeps data for buttons under a token; only event's sender can use them"""
    now = time.time()
    for token in [
        t for t, d in _search_buttons.items() if now - d["at"] > SEARCH_BUTTONS_TTL
    ]:
        _search_buttons.pop(token, None)
    while len(_search_buttons) >= SEARCH_BUTTONS_MAX:
        _search_buttons.pop(next(iter(_search_buttons)))
    token = uuid.uuid4().hex[:10]
    _search_buttons[token] = {**data, "at": now, "by": sender_ids(event)}
    return token


async def show_entry(
    event,
    entry: dict,
    level,
    sections: list[str],
    card: bool = True,
    sent: tuple[str, ...] = (),
):
    """
    Sends an entry's card (unless not card) and the sections asked for; under
    a character's, buttons for the sections not sent yet (sent holds those
    sent earlier)
    """
    status = await event.reply(f"*Fetching {label(entry)}…*")
    try:
        image, caption, extra = await get_details(entry, level)
        names = SECTIONS[entry["game"]]
        if card:
            if sections and entry["kind"] == "character":
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
            title = f"*{label(entry)}: {names[section]}*\n\n"
            for chunk in split_text(
                title + text, "\n\n", list_size=bot.max_message_length - 100
            ):
                await event.reply(chunk.strip())
                await asyncio.sleep(1)
        done = set(sent) | set(sections)
        missing = [s for s in ("t", "c") if s not in done and extra.get(s)]
        if entry["kind"] == "character" and missing:
            token = remember(event, entry=entry, level=level, sent=tuple(done))
            buttons = [(f"search:{token}:sec:{s}", names[s]) for s in missing]
            if len(missing) == 2:
                buttons.append((f"search:{token}:sec:tc", "Both"))
            await send_buttons(event.chat.jid, f"More on {label(entry)}:", buttons)
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        await event.reply(f"*Error:*\n{e}")
    finally:
        await status.delete()


@on_button("search")
async def search_button(event, data: str):
    """A pressed .search button: a match picked, or a character's sections"""
    token, _, rest = data.partition(":")
    action, _, value = rest.partition(":")
    saved = _search_buttons.get(token)
    if saved and not sender_ids(event) & saved["by"]:
        return
    if not saved or time.time() - saved["at"] > SEARCH_BUTTONS_TTL:
        return await event.reply(
            f"*That search has expired; send {conf.CMD_PREFIX}search again.*"
        )
    if action == "pick" and value.isdigit() and int(value) < len(saved["results"]):
        entry = saved["results"][int(value)]
        await show_entry(event, entry, saved["level"], saved["sections"])
    elif action == "sec" and value in ("t", "c", "tc"):
        # Only the sections: the card was sent with the buttons
        await show_entry(
            event,
            saved["entry"],
            saved["level"],
            list(value),
            card=False,
            sent=saved.get("sent", ()),
        )


async def hoyo_search(event, args, client):
    """
    Looks up a character, weapon or set in Genshin Impact, Honkai: Star Rail
    or Zenless Zone Zero. If a name matches several, buttons are sent to pick
    one (or a poll, with -poll or if buttons can't be sent); under a
    character, buttons send its talents, traces or skills and its
    constellations, eidolons or mindscapes. Data from Project Amber, Project
    Yatta and Hakushin.

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
        -poll : pick between matches with a poll instead of buttons
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    # "-lv13" is "-lv 13"
    args = re.sub(r"(?i)(?<!\S)-lv(\d+)", r"-lv \1", args)
    flags, query = get_args(
        *([f"-{g}", "store_true"] for g in GAMES),
        *([f, "store_true"] for f in KIND_FLAGS),
        ["-t", "store_true"],
        ["-c", "store_true"],
        ["-full", "store_true"],
        ["-f", "store_true"],
        ["-poll", "store_true"],
        "-lv",
        to_parse=args,
        get_unknown=True,
    )
    game = next((g for g in GAMES if getattr(flags, g)), None)
    kind = next(
        (k for f, k in KIND_FLAGS.items() if getattr(flags, f.lstrip("-"))), None
    )
    sections = [s for s in ("t", "c") if getattr(flags, s) or flags.full or flags.f]
    level = None
    if flags.lv is not None:
        if not (flags.lv.isdigit() and 1 <= int(flags.lv) <= 15):
            return await event.reply("*-lv takes a level from 1 to 15.*")
        level = int(flags.lv)
    query = query.strip()
    if not query:
        return await event.reply(getdoc(hoyo_search))
    try:
        results = search(await get_index(), query, game, kind)
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        return await event.reply(f"*Error:*\n{e}")
    if not results:
        return await event.reply(f"*Nothing found.*\nYou searched for *{query}*.")
    if len(results) == 1:
        return await show_entry(event, results[0], level, sections)
    if flags.poll:
        return await pick_by_poll(event, results, level, sections)
    results = results[:MAX_CHOICES]
    token = remember(event, results=results, level=level, sections=sections)
    buttons = [
        (f"search:{token}:pick:{i}", f"{label(entry)} ({entry['game'].upper()})")
        for i, entry in enumerate(results)
    ]
    try:
        await send_buttons(
            event.chat.jid, f"*{len(results)} matches for {query}.* Tap one:", buttons
        )
    except Exception:
        # Buttons couldn't be sent: fall back to the poll
        await logger(Exception)
        _search_buttons.pop(token, None)
        await pick_by_poll(event, results, level, sections)

import re
from inspect import getdoc

from bot.config import bot
from bot.utils.log_utils import logger
from bot.utils.lookup_utils import (
    manga_info,
    novel_info,
    search_manga,
    search_novels,
    search_titles,
    title_info,
)
from bot.utils.msg_utils import (
    chat_is_allowed,
    construct_msg_and_evt,
    get_args,
    user_is_allowed,
    user_is_privileged,
)
from bot.utils.sudo_button_utils import create_sudo_button, wait_for_button_response


async def allowed(event) -> bool:
    user = event.from_user.id
    if user_is_privileged(user):
        return True
    if not chat_is_allowed(event):
        return False
    if not user_is_allowed(user):
        await event.react("⛔")
        return False
    return True


async def pick(event, results: list[tuple[str, str]], what: str) -> str | None:
    """
    The id of one of results ((id, label) pairs): the only one, or the one
    picked from a poll; None on timeout
    """
    if len(results) == 1:
        return results[0][0]
    options = {
        key: [f"{i}. {label}", label] for i, (key, label) in enumerate(results, 1)
    }
    title = f"{event.from_user.name} please select the {what} you meant."
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
    return choice[0] if choice else None


async def look_up(event, query: str, what: str, search, info, direct=None):
    """
    Searches with search(query), lets the user pick, and replies with
    info(id)'s picture and caption; direct, if given, is an id to skip the
    search
    """
    status = None
    try:
        if direct:
            key = direct
        else:
            status = await event.reply(f"*Searching for {query}…*")
            results = await search(query)
            await status.delete()
            status = None
            if not results:
                return await event.reply(
                    f"*No {what} found.*\nYou searched for *{query}*."
                )
            if not (key := await pick(event, results, what)):
                return await event.reply("Time out!")
        image, caption = await info(key)
        if image:
            await event.reply_photo(image, caption)
        else:
            await event.reply(caption)
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        await event.reply(f"*Error:*\n{e}")
    finally:
        if status:
            await status.delete()


async def manga(event, args, client):
    """
    Looks up a manga, manhwa or manhua on AniList, or a light novel with -ln.
    If a title matches several, a poll is sent to pick one.

    Usage: manga <title or AniList id> [-ln] [-m]
        -ln : look up light novels instead
        -m : treat the id as a MyAnimeList id
    """
    if not await allowed(event):
        return
    flags, query = get_args(
        ["-ln", "store_true"],
        ["-m", "store_true"],
        to_parse=args or "",
        get_unknown=True,
    )
    query = query.strip()
    if not query:
        return await event.reply(getdoc(manga))
    what = "light novel" if flags.ln else "manga"
    await look_up(
        event,
        query,
        what,
        lambda q: search_manga(q, flags.ln),
        lambda key: manga_info(key, flags.m and query.isdigit()),
        direct=query if query.isdigit() else None,
    )


async def imdb(event, args, client):
    """
    Looks up a film or TV series, with its IMDb rating, cast and plot. If a
    title matches several, a poll is sent to pick one. Data from Cinemeta.

    Usage: imdb <title or IMDb id (tt…)> [-movie | -tv]
        -movie : only search films
        -tv : only search series
    """
    if not await allowed(event):
        return
    flags, query = get_args(
        ["-movie", "store_true"],
        ["-tv", "store_true"],
        to_parse=args or "",
        get_unknown=True,
    )
    query = query.strip()
    if not query:
        return await event.reply(getdoc(imdb))
    kinds = (
        ("movie",) if flags.movie else ("series",) if flags.tv else ("movie", "series")
    )
    await look_up(
        event,
        query,
        "title",
        lambda q: search_titles(q, kinds),
        title_info,
        direct=query if re.fullmatch(r"tt\d+", query) else None,
    )


async def novel(event, args, client):
    """
    Looks up a web or light novel (Chinese, Korean, Japanese and more) on
    MangaUpdates. If a title matches several, a poll is sent to pick one.

    Usage: novel <title>
    """
    if not await allowed(event):
        return
    query = (args or "").strip()
    if not query:
        return await event.reply(getdoc(novel))
    await look_up(event, query, "novel", search_novels, novel_info)

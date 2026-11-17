import re
from inspect import getdoc

from bot.utils.buttons import choose, sender_ids
from bot.utils.log_utils import logger
from bot.utils.lookup_utils import (
    ao3_info,
    get_photo,
    manga_info,
    novel_info,
    search_ao3,
    search_manga,
    search_novels,
    search_titles,
    search_wattpad,
    title_info,
    wattpad_info,
)
from bot.utils.msg_utils import (
    chat_is_allowed,
    get_args,
    user_is_allowed,
    user_is_privileged,
)


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
    picked with buttons (or a poll if they can't be sent); None on timeout
    """
    if len(results) == 1:
        return results[0][0]
    return await choose(
        event.chat.jid,
        f"{event.from_user.name} please select the {what} you meant.",
        dict(results),
        users=sender_ids(event),
        poll_user=event.from_user.id,
        quoted=event.message,
    )


async def look_up(event, query: str, what: str, search, info, direct=None):
    """
    Searches with search(query), lets the user pick, and replies with
    info(id)'s picture and caption; direct, if given, is an id to skip the
    search
    """
    try:
        if direct:
            key = direct
        else:
            # A reaction while working, rather than a status message
            async with event.react("⏳"):
                results = await search(query)
            if not results:
                return await event.reply(
                    f"*No {what} found.*\nYou searched for *{query}*."
                )
            if not (key := await pick(event, results, what)):
                return await event.reply("Time out!")
        async with event.react("⏳"):
            image, caption = await info(key)
            # A missing or unsendable picture leaves just the caption
            photo = await get_photo(image) if image else None
            if photo:
                await event.reply_photo(photo, caption)
            else:
                await event.reply(caption)
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        await event.reply(f"*Error:*\n{e}")


async def manga(event, args, client):
    """
    Looks up a manga, manhwa or manhua on AniList, or a light novel with -ln.
    If a title matches several, buttons are sent to pick one (or a poll if
    they can't be sent).

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
    title matches several, buttons are sent to pick one (or a poll if they
    can't be sent). Data from Cinemeta.

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
    MangaUpdates, or a Wattpad story with -wp. If a title matches several,
    buttons are sent to pick one (or a poll if they can't be sent).

    Usage: novel <title> [-wp]
        -wp : look up Wattpad stories instead
    """
    if not await allowed(event):
        return
    flags, query = get_args(
        ["-wp", "store_true"], to_parse=args or "", get_unknown=True
    )
    query = query.strip()
    if not query:
        return await event.reply(getdoc(novel))
    if flags.wp:
        return await look_up(event, query, "story", search_wattpad, wattpad_info)
    await look_up(event, query, "novel", search_novels, novel_info)


async def fic(event, args, client):
    """
    Looks up a fanfic on Archive of Our Own (AO3): fandom, rating, warnings,
    relationships, words, chapters, kudos and summary. The most-kudosed works
    matching the search are offered as buttons (or a poll if they can't be
    sent); a work's id or link goes straight to it. AO3 refuses bots at
    times, so this may not always work.

    Usage: fic <title, search words, work id or AO3 link>
    """
    if not await allowed(event):
        return
    query = (args or "").strip()
    if not query:
        return await event.reply(getdoc(fic))
    work = re.search(r"archiveofourown\.org/works/(\d+)", query)
    direct = work.group(1) if work else query if query.isdigit() else None
    await look_up(event, query, "work", search_ao3, ao3_info, direct=direct)

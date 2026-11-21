import anilist

from bot import bot
from bot.utils.ani_utils import airing_anim, anime_arch
from bot.utils.buttons import choose, sender_ids
from bot.utils.log_utils import logger
from bot.utils.msg_utils import get_args, is_adult_rated, user_is_allowed

ani_client = anilist.AsyncClient()


async def airing(event, args, client):
    """
    Get the airing schedule of an anime from AniList.
    If a title matches several anime, buttons are sent to pick one
    (or a poll if they can't be sent).

    Arguments:
        anime_title - Title of the anime
                or:
        anime_id - AniList id of the anime
    """
    if not user_is_allowed(event.from_user.id):
        return await event.react("⛔")
    try:
        if not args.isdigit():
            args = await anime_search(event, args, client)
            if args == "":
                return await event.reply("Yh, i give up!")
            if not args:
                return await event.reply("*Couldn't find any anime with that name.*")
        img, out = await airing_anim(args)
        # NSFW check for groups with NSFW blocking enabled
        group = event.chat.is_group and bot.group_dict.get(event.chat.id, {})
        if group and bot.block_nsfw and group.get("nsfw", True):
            if is_adult_rated(out):
                return await event.reply(
                    "*This anime is adult-rated and NSFW is blocked in this group.*"
                )
        await event.reply_photo(img, out)
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        await event.reply(f"Error:\n> {e}")


async def anime(event, args, client):
    """
    Fetch anime info from AniList.
    If a title matches several anime, buttons are sent to pick one
    (or a poll if they can't be sent).

    Arguments:
        anime_title - Title of the anime
                or:
        anime_id - AniList id of the anime
        -m - Treat anime_id as a MyAnimeList id
    """
    if not user_is_allowed(event.from_user.id):
        return await event.react("⛔")
    try:
        arg, args = get_args(
            ["-m", "store_true"],
            to_parse=args,
            get_unknown=True,
        )
        if not args.isdigit() and not arg.m:
            args = await anime_search(event, args, client)
            if args == "":
                return await event.reply("Yh, i give up!")
            if not args:
                return await event.reply("*Couldn't find any anime with that name.*")
        img, out = await anime_arch(args, arg)
        # NSFW check for groups with NSFW blocking enabled
        group = event.chat.is_group and bot.group_dict.get(event.chat.id, {})
        if group and bot.block_nsfw and group.get("nsfw", True):
            if is_adult_rated(out):
                return await event.reply(
                    "*This anime is adult-rated and NSFW is blocked in this group.*"
                )
        await event.reply_photo(img, out)
    except Exception as e:
        await logger(Exception)
        await event.react("❌")
        await event.reply(f"Error:\n> {e}")


async def anime_search(event, args, client):

    # query = args.replace(" ", "%20")
    search_result, pages = await ani_client.search_anime(args, limit=11)

    if not search_result:
        return None

    options = {}
    for anime in search_result:
        options[anime.id] = (
            anime.title.english or anime.title.romaji or anime.title.native
        )
    if len(options) == 1:
        return f"{next(iter(options))}"
    # Buttons, or a poll if they can't be sent
    choice = await choose(
        event.chat.jid,
        f"{event.from_user.name} please select the anime you want to fetch info for.",
        options,
        users=sender_ids(event),
        poll_user=event.from_user.id,
        quoted=event.message,
    )
    if not choice:
        await event.reply("Time out!")
        return ""
    return f"{choice}"

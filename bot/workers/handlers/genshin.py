from inspect import getdoc

from bot.config import bot, conf
from bot.utils.bot_utils import list_to_str
from bot.utils.db_utils import save2db2
from bot.utils.farm_utils import (
    DEFAULT_REGION,
    REGION_NAMES,
    REGIONS,
    WEEKDAYS,
    city_list,
    get_farm_msg,
    get_schedule,
    server_weekday,
)
from bot.utils.gift_utils import fetch_codes, get_msg_from_codes
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    chat_is_allowed,
    user_is_admin,
    user_is_allowed,
    user_is_privileged,
)


async def manage_autogift_chat(event, args, client):
    try:
        action, _, chat = args.strip().partition(" ")
        chat = chat.strip()
        chats = bot.gift_dict["chats"]
        pinned = bot.gift_dict["pin"]
        this_chat = f"{event.chat.id}:{event.chat.server}"
        user = event.from_user.id
        if not user_is_privileged(user):
            # Group admins may only manage their own group
            if not event.chat.is_group:
                return await event.react("🚫")
            group_info = await client.get_group_info(event.chat.jid)
            if not user_is_admin(user, group_info.Participants):
                return await event.react("🚫")
            if action == "-get":
                if this_chat not in chats:
                    return await event.reply("*New codes aren't posted here.*")
                pin = " and pinned for a day" if this_chat in pinned else ""
                return await event.reply(f"*New codes are posted here{pin}.*")
            if chat and chat not in (".", this_chat, event.chat.id):
                return await event.reply("*Admins can only manage this chat (.)!*")
        if action == "-get":
            if not chats:
                return await event.reply("*No chat set!*")
            return await event.reply(
                list_to_str(
                    [
                        (c or "default") + (" (pinned)" if c in pinned else "")
                        for c in chats
                    ],
                    sep=", ",
                )
            )
        if action not in ("-add", "-rm", "-pin", "-unpin") or not chat:
            return await event.reply(getdoc(gift_codes))
        if chat in (".", event.chat.id):
            chat = this_chat
        elif chat.casefold() == "default":
            chat = None
        if action == "-add":
            if chat in chats:
                return await event.reply("*Chat already added!*")
            chats.append(chat)
            done = "added"
        elif chat not in chats:
            return await event.reply("*Given chat was never added!*")
        elif action == "-rm":
            chats.remove(chat)
            if chat in pinned:
                pinned.remove(chat)
            done = "removed"
        elif action == "-pin":
            if chat in pinned:
                return await event.reply("*Already pinning in this chat!*")
            pinned.append(chat)
            done = "set to pin new codes for a day (if I'm an admin there)"
        else:
            if chat not in pinned:
                return await event.reply("*Wasn't pinning in this chat!*")
            pinned.remove(chat)
            done = "set to stop pinning new codes"
        await save2db2(bot.gift_dict, "gift")
        await event.reply(f"*{chat or 'default'}* has been {done}.")
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def gift_codes(event, args, client):
    """
    Fetches the latest Genshin Impact redeem codes.
    Uses hoyo-codes.seria.moe, or api.ennead.cc when that's down.

    Arguments:
        -add <chat> : [Owner | Sudo | Admin] post new codes to a chat automatically
        -rm <chat> : [Owner | Sudo | Admin] stop posting new codes to a chat
        -get : [Owner | Sudo | Admin] list the chats new codes are posted to
        -pin <chat> : [Owner | Sudo | Admin] pin new codes posted to an added chat
            for a day; needs the bot to be an admin there
        -unpin <chat> : [Owner | Sudo | Admin] stop pinning them
    chat: . for this chat, default for RSS_CHAT, or chat_id:server
    Group admins can only manage the group they're in.
    New codes are checked for every 30 minutes.
    """
    if args:
        return await manage_autogift_chat(event, args, client)
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    status = None
    try:
        status = await event.reply("*Fetching latest giftcodes…*")
        codes = await fetch_codes()
        await event.reply(
            get_msg_from_codes(codes) if codes else "*No active codes right now.*"
        )
        await status.delete()
    except Exception:
        await logger(Exception)
        await event.react("❌")
        if status:
            await status.edit("*Couldn't fetch the codes right now.*")


async def manage_farm_chat(event, args, client):
    try:
        action, _, rest = args.strip().partition(" ")
        chat, _, region = rest.strip().partition(" ")
        region = region.strip().casefold() or DEFAULT_REGION
        chats = bot.gift_dict["farm"]
        this_chat = f"{event.chat.id}:{event.chat.server}"
        user = event.from_user.id
        if not user_is_privileged(user):
            # Group admins may only manage their own group
            if not event.chat.is_group:
                return await event.react("🚫")
            group_info = await client.get_group_info(event.chat.jid)
            if not user_is_admin(user, group_info.Participants):
                return await event.react("🚫")
            if action == "-get":
                if this_chat not in chats:
                    return await event.reply("*The farm schedule isn't posted here.*")
                return await event.reply(
                    f"*The farm schedule is posted here at the {REGION_NAMES[chats[this_chat]]} server's reset.*"
                )
            if chat and chat not in (".", this_chat, event.chat.id):
                return await event.reply("*Admins can only manage this chat (.)!*")
        if action == "-get":
            if not chats:
                return await event.reply("*No chat set!*")
            return await event.reply(
                list_to_str(
                    [f"{c or 'default'} ({REGION_NAMES[r]})" for c, r in chats.items()],
                    sep=", ",
                )
            )
        if action not in ("-add", "-rm") or not chat or region not in REGIONS:
            return await event.reply(getdoc(farm).format(pre=conf.CMD_PREFIX))
        if chat in (".", event.chat.id):
            chat = this_chat
        elif chat.casefold() == "default":
            chat = ""
        if action == "-add":
            if chats.get(chat) == region:
                return await event.reply("*Chat already added!*")
            chats[chat] = region
            done = f"set to get the farm schedule at the {
                REGION_NAMES[region]} server's reset"
        elif chat not in chats:
            return await event.reply("*Given chat was never added!*")
        else:
            chats.pop(chat)
            done = "removed"
        await save2db2(bot.gift_dict, "gift")
        await event.reply(f"*{chat or 'default'}* has been {done}.")
    except Exception:
        await logger(Exception)
        await event.react("❌")


def parse_farm_args(args: str, cities: list[str]):
    """(weekday, region, city) from words in any order; weekday None for today"""
    weekday = region = city = None
    offset = 0
    names = {c.casefold().replace("-", ""): c for c in cities}
    for word in args.casefold().split():
        if word in REGIONS:
            region = word
        elif word in ("today", "tomorrow"):
            offset = int(word == "tomorrow")
        elif len(word) >= 3 and (
            match := [i for i, d in enumerate(WEEKDAYS) if d.startswith(word)]
        ):
            weekday = match[0]
        elif match := [
            c for n, c in names.items() if n.startswith(word.replace("-", ""))
        ]:
            city = match[0]
        else:
            raise ValueError(word)
    region = region or DEFAULT_REGION
    if weekday is None:
        weekday = (server_weekday(region) + offset) % 7
    return weekday, region, city


async def farm(event, args, client):
    """
    Shows the Genshin Impact talent and weapon domains open today, and the
    characters and weapons (4★ and up) they're for. Data from Project Amber.

    Usage: {pre}farm [day] [city] [region]
        day: monday…sunday (or mon…sun), today or tomorrow; defaults to today
        city: only show this city's domains, e.g. liyue
        region: asia, eu or na; sets when the day rolls over (04:00 server time)

    Arguments:
        -add <chat> [region] : [Owner | Sudo | Admin] post the schedule to a chat
            at the region's daily reset
        -rm <chat> : [Owner | Sudo | Admin] stop posting it
        -get : [Owner | Sudo | Admin] list the chats it's posted to
    chat: . for this chat, default for RSS_CHAT, or chat_id:server
    Group admins can only manage the group they're in.
    """
    if args.startswith("-"):
        return await manage_farm_chat(event, args, client)
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        schedule = await get_schedule()
        try:
            weekday, region, city = parse_farm_args(args, city_list(schedule))
        except ValueError as e:
            return await event.reply(
                f"*Unknown day, city or region:* {e}\n\n"
                + getdoc(farm).format(pre=conf.CMD_PREFIX)
            )
        await event.reply(get_farm_msg(schedule, weekday, region, city))
    except Exception:
        await logger(Exception)
        await event.react("❌")

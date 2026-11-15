import re
from inspect import getdoc

from bot.config import bot
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
    get_args,
    user_is_admin,
    user_is_allowed,
    user_is_privileged,
)

CHAT_FLAGS = ("add", "rm", "pin", "unpin", "get")
# ".", "default", or a chat id with an optional :server
_chat = re.compile(r"[0-9A-Za-z][0-9A-Za-z._@-]*(:[0-9A-Za-z.]+)?")


def parse_chat_flags(args: str):
    """(flags, words): -add -rm -pin -unpin -get, and the rest (chat…)"""
    flags, rest = get_args(
        *([f"-{f}", "store_true"] for f in CHAT_FLAGS), to_parse=args, get_unknown=True
    )
    return flags, rest.split()


def valid_chat(chat: str) -> bool:
    return chat in (".",) or chat.casefold() == "default" or bool(_chat.fullmatch(chat))


def drop_junk_chats(chats, pinned) -> list:
    """
    Entries saved from flags read as a chat name (like "-pin ." before
    several flags could be given at once); removed and returned
    """
    junk = [c for c in chats if isinstance(c, str) and c.startswith("-")]
    for c in junk:
        if isinstance(chats, dict):
            chats.pop(c)
        else:
            chats.remove(c)
        if c in pinned:
            pinned.remove(c)
    return junk


async def manage_autogift_chat(event, args, client):
    try:
        flags, words = parse_chat_flags(args)
        chat = words[0] if words else ""
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
            if flags.get:
                if this_chat not in chats:
                    return await event.reply("*New codes aren't posted here.*")
                pin = " and pinned for a day" if this_chat in pinned else ""
                return await event.reply(f"*New codes are posted here{pin}.*")
            if chat and chat not in (".", this_chat, event.chat.id):
                return await event.reply("*Admins can only manage this chat (.)!*")
        junk = drop_junk_chats(chats, pinned)
        if flags.get:
            if junk:
                await save2db2(bot.gift_dict, "gift")
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
        actions = [f for f in ("add", "rm", "pin", "unpin") if getattr(flags, f)]
        # -rm stands alone; -pin and -unpin together make no sense
        if (
            not actions
            or not chat
            or not valid_chat(chat)
            or len(words) > 1
            or (flags.rm and len(actions) > 1)
            or (flags.pin and flags.unpin)
        ):
            return await event.reply(getdoc(gift_codes))
        if chat in (".", event.chat.id):
            chat = this_chat
        elif chat.casefold() == "default":
            chat = None
        done = []
        if flags.add:
            if chat in chats:
                done.append("already added")
            else:
                chats.append(chat)
                done.append("added")
        if chat not in chats:
            return await event.reply("*Given chat was never added!*")
        if flags.rm:
            chats.remove(chat)
            if chat in pinned:
                pinned.remove(chat)
            done.append("removed")
        if flags.pin:
            if chat in pinned:
                done.append("already pinning new codes")
            else:
                pinned.append(chat)
                done.append("set to pin new codes for a day (if I'm an admin there)")
        if flags.unpin:
            if chat not in pinned:
                done.append("not pinning new codes")
            else:
                pinned.remove(chat)
                done.append("set to stop pinning new codes")
        await save2db2(bot.gift_dict, "gift")
        msg = f"*{chat or 'default'}*: " + ", ".join(done) + "."
        if junk:
            msg += "\n_Removed broken entries: " + ", ".join(junk) + "_"
        await event.reply(msg)
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
    Flags can be combined, e.g. -add -pin . to add this chat and pin there.
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
        flags, words = parse_chat_flags(args)
        chat = words[0] if words else ""
        region = words[1].casefold() if len(words) > 1 else DEFAULT_REGION
        chats = bot.gift_dict["farm"]
        pinned = bot.gift_dict.setdefault("farm_pin", [])
        this_chat = f"{event.chat.id}:{event.chat.server}"
        user = event.from_user.id
        if not user_is_privileged(user):
            # Group admins may only manage their own group
            if not event.chat.is_group:
                return await event.react("🚫")
            group_info = await client.get_group_info(event.chat.jid)
            if not user_is_admin(user, group_info.Participants):
                return await event.react("🚫")
            if flags.get:
                if this_chat not in chats:
                    return await event.reply("*The farm schedule isn't posted here.*")
                pin = " and pinned for a day" if this_chat in pinned else ""
                return await event.reply(
                    f"*The farm schedule is posted here at the {REGION_NAMES[chats[this_chat]]} server's reset{pin}.*"
                )
            if chat and chat not in (".", this_chat, event.chat.id):
                return await event.reply("*Admins can only manage this chat (.)!*")
        junk = drop_junk_chats(chats, pinned)
        if flags.get:
            if junk:
                await save2db2(bot.gift_dict, "gift")
            if not chats:
                return await event.reply("*No chat set!*")
            return await event.reply(
                list_to_str(
                    [
                        f"{c or 'default'} ({REGION_NAMES[r]}"
                        + (", pinned" if c in pinned else "")
                        + ")"
                        for c, r in chats.items()
                    ],
                    sep=", ",
                )
            )
        actions = [f for f in ("add", "rm", "pin", "unpin") if getattr(flags, f)]
        # -rm stands alone; -pin and -unpin together make no sense
        if (
            not actions
            or not chat
            or not valid_chat(chat)
            or len(words) > 2
            or region not in REGIONS
            or (flags.rm and len(actions) > 1)
            or (flags.pin and flags.unpin)
        ):
            return await event.reply(getdoc(farm))
        if chat in (".", event.chat.id):
            chat = this_chat
        elif chat.casefold() == "default":
            chat = ""
        done = []
        if flags.add:
            if chats.get(chat) == region:
                done.append("already added")
            else:
                chats[chat] = region
                done.append(f"set to get the farm schedule at the {
                        REGION_NAMES[region]} server's reset")
        if chat not in chats:
            return await event.reply("*Given chat was never added!*")
        if flags.rm:
            chats.pop(chat)
            if chat in pinned:
                pinned.remove(chat)
            done.append("removed")
        if flags.pin:
            if chat in pinned:
                done.append("already pinning the farm schedule")
            else:
                pinned.append(chat)
                done.append(
                    "set to pin the farm schedule for a day (if I'm an admin there)"
                )
        if flags.unpin:
            if chat not in pinned:
                done.append("not pinning the farm schedule")
            else:
                pinned.remove(chat)
                done.append("set to stop pinning the farm schedule")
        await save2db2(bot.gift_dict, "gift")
        msg = f"*{chat or 'default'}*: " + ", ".join(done) + "."
        if junk:
            msg += "\n_Removed broken entries: " + ", ".join(junk) + "_"
        await event.reply(msg)
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

    Usage: farm [day] [city] [region]
        day: monday…sunday (or mon…sun), today or tomorrow; defaults to today
        city: only show this city's domains, e.g. liyue
        region: asia, eu or na; sets when the day rolls over (04:00 server time)

    Arguments:
        -add <chat> [region] : [Owner | Sudo | Admin] post the schedule to a chat
            at the region's daily reset
        -rm <chat> : [Owner | Sudo | Admin] stop posting it
        -pin <chat> : [Owner | Sudo | Admin] pin the schedule posted to an
            added chat for a day; needs the bot to be an admin there
        -unpin <chat> : [Owner | Sudo | Admin] stop pinning it
        -get : [Owner | Sudo | Admin] list the chats it's posted to
    chat: . for this chat, default for RSS_CHAT, or chat_id:server
    Flags can be combined, e.g. -add -pin . eu to add this chat and pin there.
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
                f"*Unknown day, city or region:* {e}\n\n" + getdoc(farm)
            )
        await event.reply(get_farm_msg(schedule, weekday, region, city))
    except Exception:
        await logger(Exception)
        await event.react("❌")

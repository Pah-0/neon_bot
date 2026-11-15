from datetime import datetime as dt

from bot.config import conf
from bot.utils.bot_utils import get_date_from_ts
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    chat_is_allowed,
    tag_admins,
    user_is_admin,
    user_is_allowed,
    user_is_privileged,
)


async def getcmds(event, args, client):
    """
    List the bot's main commands.
    Game, lookup, tools, fun, role and management commands are listed by
    games, lookup, tools, fun, roles and manage.

    Arguments: None
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        pre = conf.CMD_PREFIX
        msg = f"""{pre}start - *Hi!*
{pre}ping - *Check if bot is alive*
{pre}afk - *Enable AFK mode*
{pre}games - *Get game commands: Werewolf, Genshin, Star Rail and ZZZ*
{pre}lookup - *Get commands to look up anime, manga, novels, films and TV*
{pre}fun - *Get fun commands*
{pre}tools - *Get tools commands*
{pre}roles - *Get commands for roles*
{pre}manage - *Get bot and group management commands*"""
        await event.reply(msg)
    except Exception as e:
        await logger(Exception)
        return await event.reply(f"*Error:*\n{e}")


async def game_cmds(event, args, client):
    """
    List the game commands: Werewolf, and those for Genshin Impact, Honkai:
    Star Rail and Zenless Zone Zero.

    Arguments: None
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        pre = conf.CMD_PREFIX
        msg = f"""{pre}werewolf - *Play a game of Werewolf*

*Genshin Impact:*
{pre}gicodes - *Get Genshin Impact redeem codes ([Owner | Sudo | Admin] -add/-rm/-get chats to auto-post new ones, -pin/-unpin to pin them for a day)*
{pre}farm - *Genshin domains open today, or on a given day ([Owner | Sudo | Admin] -add/-rm/-get chats to post it daily, -pin/-unpin to pin it for a day)*

*Genshin, Star Rail & ZZZ:*
{pre}gi / {pre}hsr / {pre}zzz - *Genshin, Star Rail or ZZZ profile and character build cards from Enka.Network (-set <uid> to save yours)*
{pre}search - *Look up a Genshin, Star Rail or ZZZ character, weapon or set (-t talents, -c constellations)*"""
        await event.reply(msg)
    except Exception as e:
        await logger(Exception)
        return await event.reply(f"*Error:*\n{e}")


async def lookup_cmds(event, args, client):
    """
    List the commands for looking up anime, manga, light and web novels,
    films and TV series.

    Arguments: None
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        pre = conf.CMD_PREFIX
        msg = f"""{pre}anime - *Fetch anime info from anilist*
{pre}airing - *Fetch anime airing info from anilist*
{pre}manga - *Fetch manga info from AniList (-ln for light novels)*
{pre}novel - *Fetch web and light novel info from MangaUpdates*
{pre}imdb - *Fetch film and TV series info with IMDb ratings (-movie or -tv to narrow it)*"""
        await event.reply(msg)
    except Exception as e:
        await logger(Exception)
        return await event.reply(f"*Error:*\n{e}")


async def manage_cmds(event, args, client):
    """
    List the commands for managing the bot and groups: the ones for owners,
    sudoers, group admins and devs.

    Arguments: None
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    try:
        pre = conf.CMD_PREFIX
        msg = f"""{pre}sudo - *[Owner] View/Modify sudoers*
{pre}pause - *[Owner] Pauses/Unpauses bot*
{pre}logs - *[Owner] Get bot logs*
{pre}ban - *[Owner | Sudo] Prevent a user from using bot*
{pre}unban - *[Owner | Sudo] Unban a user*
{pre}call - *[Owner | Sudo] Play replied audio or a link in a voice call (test)*
{pre}endcall - *[Owner | Sudo] Hang up the voice call*
{pre}rss - *[Owner | Sudo] Setup bot to auto post RSS feeds*
{pre}update - *[Owner | Sudo] Update & restarts bot, or test a pull request with -pr <number>*
{pre}restart - *[Owner | Sudo] Restarts bot*
{pre}ytdl_* - *[Owner | Sudo | Admin] Disables/Enables auto media download (anyone in PM)*
{pre}amr_* - *[Owner | Sudo | Admin] Disables/Enables Auto msg ranking*
{pre}mute - *[Owner | Sudo | Admin] Auto-delete messages from users, a role's members (-role) or everyone outside a role (-except)*
{pre}enable_atd - *[Owner | Sudo | Admin] Enables Auto deletion of @all tags*
{pre}disable_atd - *[Owner | Sudo | Admin] Disables Auto deletion of @all tags*
{pre}disable - *[Owner | Sudo | Admin] Disable bot in a GC*
{pre}enable - *[Owner | Sudo | Admin] Enable bot in a GC*
{pre}del - *[Owner | Sudo | Admin] Delete the replied message*
{pre}gc_info - *[Owner | Sudo | Admin] Get group info*
{pre}bash - *[Owner | Dev] Run bash commands*
{pre}eval - *[Owner | Dev] Evaluate python code*

* expands to _ytdl_enable/ytdl_disable_
* expands to _amr_enable/amr_disable_"""
        await event.reply(msg)
    except Exception as e:
        await logger(Exception)
        return await event.reply(f"*Error:*\n{e}")


async def gc_info(event, args, client):
    """
    [Owner | Sudo | Admin] Show group info. Group only.
    Replies with the group owner, creation date, group id and a list of admins.

    Arguments: None
    """
    if not event.chat.is_group:
        return await event.react("🚫")
    try:
        group_info = await client.get_group_info(event.chat.jid)
        user = event.from_user.id
        if not user_is_privileged(user):
            if not user_is_admin(user, group_info.Participants):
                return await event.react("🚫")
        gc_owner = f"@{group_info.OwnerJID.User}" if group_info.OwnerJID.User else "MIA"
        tags = ""
        for tag in tag_admins(group_info.Participants).split():
            tags += f"- {tag}\n"
        tags = tags.rstrip("\n")
        return await event.reply(
            f"*Owner:* {gc_owner}\n"
            f"*Created at:* {get_date_from_ts(group_info.GroupCreated)}\n"
            f"*Group Id:* {event.chat.id}\n\n"
            f"*Admins:*\n"
            f"{tags}"
        )
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def hello(event, args, client):
    """
    Reply with "Hi!".

    Arguments: None
    """
    try:
        await event.reply("Hi!")
    except Exception:
        await logger(Exception)
        await event.react("❌")


async def up(event, args, client):
    """
    Check if the bot is alive; replies with two response times in ms.

    Arguments: None
    """
    user = event.from_user.id
    if not user_is_privileged(user):
        if not chat_is_allowed(event):
            return
        if not user_is_allowed(user):
            return await event.react("⛔")
    ist = dt.now()
    msg = await event.reply("…")
    st = dt.now()
    ims = (st - ist).microseconds / 1000
    msg1 = "*Pong! ——* _{}ms_"
    st = dt.now()
    await msg.edit(msg1.format(ims))
    ed = dt.now()
    ms = (ed - st).microseconds / 1000
    await msg.edit(f"1. {msg1.format(ims)}\n2. {msg1.format(ms)}")

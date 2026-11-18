from datetime import datetime as dt

from bot.config import conf
from bot.utils.bot_utils import get_date_from_ts, space_commands
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
{pre}id - *Show your ids, or a replied/mentioned user's, with their roles and other community groups*
{pre}games - *Get game commands: Werewolf, Genshin, Star Rail and ZZZ*
{pre}lookup - *Get commands to look up anime, manga, novels, fanfics, films and TV*
{pre}fun - *Get fun commands*
{pre}tools - *Get tools commands*
{pre}roles - *Get commands for roles*
{pre}manage - *Get bot and group management commands*"""
        await event.reply(space_commands(msg, pre))
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
{pre}search - *Look up a Genshin, Star Rail, ZZZ or Wuthering Waves character, weapon or set (-t talents, -c constellations; matches and sections come as buttons, -poll for a poll)*

*Wuthering Waves:*
{pre}wuwa - *Look up a resonator, weapon, echo or sonata effect (-t forte, -c resonance chain), or -codes for the active redeem codes*"""
        await event.reply(space_commands(msg, pre))
    except Exception as e:
        await logger(Exception)
        return await event.reply(f"*Error:*\n{e}")


async def lookup_cmds(event, args, client):
    """
    List the commands for looking up anime, manga, light and web novels,
    Wattpad stories, AO3 fanfics, films and TV series.

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
{pre}novel - *Fetch web and light novel info from MangaUpdates (-wp for Wattpad stories)*
{pre}fic - *Look up a fanfic on AO3*
{pre}imdb - *Fetch film and TV series info with IMDb ratings (-movie or -tv to narrow it)*"""
        await event.reply(space_commands(msg, pre))
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
        msg = f"""*Bot:*
{pre}sudo - *[Owner] View/Modify sudoers*
{pre}setname - *[Owner | Sudo] Set the name the bot calls someone by*
{pre}pause - *[Owner] Pauses/Unpauses bot*
{pre}logs - *[Owner] Get bot logs*
{pre}ban - *[Owner | Sudo] Prevent a user from using bot*
{pre}unban - *[Owner | Sudo] Unban a user*
{pre}rss - *[Owner | Sudo] Setup bot to auto post RSS feeds*
{pre}update - *[Owner | Sudo] Update & restarts bot, or test a pull request with -pr <number>*
{pre}restart - *[Owner | Sudo] Restarts bot*
{pre}call - *[Owner | Sudo] Play replied audio or a link in a voice call (test)*
{pre}endcall - *[Owner | Sudo] Hang up the voice call*

*Members:*
{pre}kick - *[Owner | Sudo | Admin] Remove members from the group (-c from the whole community)*
{pre}gban - *[Owner | Sudo | Admin] Ban members from the group: removed now and whenever they rejoin (-c for the whole community; no arguments lists them)*
{pre}gunban - *[Owner | Sudo | Admin] Lift a group ban (-c a community ban)*
{pre}add - *[Owner | Sudo | Admin] Add people to the group by number, or -c to the community (sends an invite to join if their privacy blocks it)*
{pre}mute - *[Owner | Sudo | Admin] Auto-delete messages from users, a role's members (-role) or everyone outside a role (-except)*

*Warnings & filters:*
{pre}warn - *[Owner | Sudo | Admin] Warn members (reply: the message is the reason); removed at the warn limit*
{pre}warns / {pre}resetwarns / {pre}warnlimit - *[Owner | Sudo | Admin] Show / clear warnings, set the limit (default 3, 0 = never remove)*
{pre}pfilter - *[Owner | Sudo | Admin] Punishing filter: delete and warn on a word; reply to a message for the reason (-r reason, -a, -m)*
{pre}pfilters / {pre}del_pfilter - *[Owner | Sudo | Admin] List / remove punishing filters*

*Protection:*
{pre}antilink - *[Owner | Sudo | Admin] Auto-delete links from members (on/off, -allow/-rm domains)*
{pre}captcha - *[Owner | Sudo | Admin] New members must solve a captcha or be removed (on/off, -t minutes)*
{pre}autolock - *[Owner | Sudo | Admin] Make the group admins-only every day between two times, e.g. 22:00 7:00 Africa/Lagos (time zone optional; off to stop)*
{pre}enable_atd - *[Owner | Sudo | Admin] Enables Auto deletion of @all tags*
{pre}disable_atd - *[Owner | Sudo | Admin] Disables Auto deletion of @all tags*

*Group:*
{pre}enable - *[Owner | Sudo | Admin] Enable bot in a GC*
{pre}disable - *[Owner | Sudo | Admin] Disable bot in a GC*
{pre}ytdl_* - *[Owner | Sudo | Admin] Disables/Enables auto media download (anyone in PM)*
{pre}amr_* - *[Owner | Sudo | Admin] Disables/Enables Auto msg ranking*
{pre}mt_alerts - *[Owner | Sudo | Admin] Announce members' group tag changes (on/off)*
{pre}mt_set - *[Owner | Sudo | Admin | Self] Set the bot's group tag (-rm to remove it)*
{pre}del - *[Owner | Sudo | Admin] Delete the replied message*
{pre}gc_info - *[Owner | Sudo | Admin] Get group info*

*Dev:*
{pre}bash - *[Owner | Dev] Run bash commands*
{pre}eval - *[Owner | Dev] Evaluate python code*

* expands to _ytdl_enable/ytdl_disable_
* expands to _amr_enable/amr_disable_"""
        await event.reply(space_commands(msg, pre))
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

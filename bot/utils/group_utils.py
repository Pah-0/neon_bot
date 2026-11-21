"""
Group member and community utilities. Moved from handlers/members.py to break
handler-to-handler import cycles.
"""

import asyncio
import time

from neonize.utils.enum import ParticipantChange, ParticipantRequestChange

from bot import bot, jid
from bot.utils.bot_utils import LimitedDict
from bot.utils.db_utils import save2db2
from bot.utils.log_utils import logger
from bot.utils.msg_utils import (
    get_args,
    get_mentioned,
    user_is_admin,
    user_is_privileged,
)

# Error codes WhatsApp gives per participant when adding one fails
ADD_ERRORS = {
    403: "only lets contacts add them to groups",
    408: "left recently and can't be added back yet",
    409: "is already in the group",
    500: "can't be added; the group is full",
}


def find_member(user: str, participants) -> object | None:
    """The participant whose JID, LID or phone number is user, or None"""
    for member in participants:
        if user and user in (
            member.JID.User,
            member.LID.User,
            member.PhoneNumber.User,
        ):
            return member
    return None


def member_ids(member) -> set:
    """Every id a participant goes by (JID, LID, phone number)"""
    return {u for u in (member.JID.User, member.LID.User, member.PhoneNumber.User) if u}


def targets(event, args: str) -> list[str]:
    """
    The users a command is aimed at: ids or @mentions in args (a leading +
    is dropped), or the replied message's sender
    """
    ids = [a.lstrip("@").lstrip("+") for a in args.split()]
    ids = [i for i in ids if i.isdigit()] + get_mentioned(args)
    if not ids and event.reply_to_message:
        ids = [event.reply_to_message.from_user.id]
    return list(dict.fromkeys(ids))


def tag(member) -> str:
    """A participant's mention, by the id the group knows them by"""
    return f"@{member.JID.User}"


def parse(args: str) -> tuple[bool, str]:
    """(community flag, the rest of args)"""
    flags, rest = get_args(["-c", "store_true"], to_parse=args or "", get_unknown=True)
    return flags.c, rest


async def other_ids(user: str) -> set:
    """user with the LID of their number, or the number of their LID"""
    ids = {user}
    for lookup, server in (
        (bot.client.get_lid_from_pn, "s.whatsapp.net"),
        (bot.client.get_pn_from_lid, "lid"),
    ):
        try:
            if found := (await lookup(jid.build_jid(user, server))).User:
                ids.add(found)
        except Exception:
            pass
    return ids


def community_of(group_info) -> object | None:
    """The JID of the community a group belongs to, or None"""
    parent = group_info.GroupLinkedParent.LinkedParentJID
    return parent if parent.User else None


def bans(chat_id: str, community: bool = False) -> set:
    """The ban list of a group, or of a community (by its JID's user)"""
    key = "community_gbanned" if community else "gbanned"
    return bot.group_dict.setdefault(chat_id, {}).setdefault(key, set())


async def banned_for(group_jid) -> set:
    """Everyone banned from a group: its own bans and its community's"""
    banned = set(bot.group_dict.get(group_jid.User, {}).get("gbanned") or ())
    # Only look up the community when some community has bans
    if any(
        g.get("community_gbanned")
        for g in bot.group_dict.values()
        if isinstance(g, dict)
    ):
        try:
            info = await bot.client.get_group_info(group_jid)
            if community := community_of(info):
                banned |= (
                    bot.group_dict.get(community.User, {}).get("community_gbanned")
                    or set()
                )
        except Exception:
            await logger(Exception)
    return banned


async def admin_check(event, client, community: bool = False):
    """
    (group info, community JID or None) if the sender may manage members and
    the bot is a group admin; with community, the group must be in one and
    the sender must be a community admin. Otherwise replies or reacts and
    returns (None, None).
    """
    if not event.chat.is_group:
        await event.react("🚫")
        return None, None
    group_info = await client.get_group_info(event.chat.jid)
    user = event.from_user.id
    parent = community_of(group_info)
    if community and not parent:
        await event.reply("*This group isn't part of a community.*")
        return None, None
    allowed = user_is_privileged(user)
    if not allowed:
        if community:
            try:
                parent_info = await client.get_group_info(parent)
                allowed = user_is_admin(user, parent_info.Participants)
            except Exception:
                allowed = False
        else:
            allowed = user_is_admin(user, group_info.Participants)
    if not allowed:
        await event.react("🚫")
        return None, None
    if not community and not user_is_admin(
        bot.client.me.JID.User, group_info.Participants
    ):
        await event.reply("*I need to be a group admin for that to work.*")
        return None, None
    return group_info, parent


def protected(member, participants) -> bool:
    """Owner, sudoers, group admins and the bot can't be kicked or banned"""
    ids = member_ids(member)
    me = bot.client.me
    return (
        any(user_is_privileged(i) for i in ids)
        or user_is_admin(member.JID.User, participants)
        or bool(ids & {me.JID.User, getattr(me, "LID", me.JID).User})
    )


async def remove(client, group_jid, members: list) -> list:
    """Removes members from a group; those it couldn't remove"""
    result = await client.update_group_participants(
        group_jid, [m.JID for m in members], ParticipantChange.REMOVE
    )
    # The answer may name a member by their other id (number or LID)
    failed = set().union(*(member_ids(p) for p in result if p.Error))
    return [m for m in members if member_ids(m) & failed]


def member_label_keys(user) -> set:
    """Every id a member's tag is saved under (phone number and LID forms)"""
    return {
        k for k in (user.id, getattr(user, "hid", None), getattr(user, "ph", None)) if k
    }


def get_member_label(chat_id, user) -> str:
    labels = bot.group_dict.get(chat_id, {}).get("member_labels", {})
    for key in member_label_keys(user):
        if labels.get(key):
            return labels[key]
    return ""


# Messages already taken off the leaderboard, so a deletion the bot makes
# isn't taken off again if its revoke comes back as a message
uncounted = LimitedDict(1000)


def uncount_messages(msgs: list):
    """
    Takes deleted messages (as stored) off their senders' message counts,
    the ones counted this week
    """
    import datetime

    from bot.utils.bot_utils import same_week

    for msg in msgs:
        key = (msg.chat.id, msg.id)
        if key in uncounted or not (msg.text or msg.caption or msg.audio):
            continue
        uncounted[key] = True
        if not (ts := msg.message.Info.Timestamp):
            continue
        if not same_week(datetime.datetime.fromtimestamp(ts / 1000), 0, 4):
            continue
        group = bot.group_dict.get(msg.chat.id, {})
        if (msg_rank := group.get("msg_ranking")) is None:
            continue
        user = msg.user.id
        if msg_rank.get(user, 0) <= 0:
            continue
        # Off the leaderboard at 0
        msg_rank[user] -= 1
        if msg_rank[user] <= 0:
            msg_rank.pop(user)
        msg_rank["total"] = max(msg_rank.get("total", 0) - 1, 0)
        bot.msg_leaderboard_counter += 1


# Community log functions
# A group's community and name, cached: they rarely change, and every join
# or leave needs them
GROUP_TTL = 60 * 60
_groups: dict[str, tuple[float, str | None, str]] = {}
# Changes are held this long and merged into one line: removing someone from
# a community comes as a leave in each of its groups
MERGE_DELAY = 5
_pending: dict[tuple, list[str]] = {}

JOIN_REASONS = {"invite": "via invite link"}


async def as_lid(user) -> str | None:
    """A participant's LID, or None if it's the bot or not found"""
    if not user.User:
        return None
    if user.Server == "lid":
        return user.User
    try:
        return (
            await bot.client.get_lid_from_pn(jid.build_jid(user.User, "s.whatsapp.net"))
        ).User
    except Exception:
        return None


def is_me(user) -> bool:
    me = bot.client.me
    return user.User in (me.JID.User, me.LID.User)


async def group_details(group) -> tuple[str | None, str]:
    """(the community's JID user or None, the group's name) for a group"""
    now = time.time()
    if (cached := _groups.get(group.User)) and now - cached[0] < GROUP_TTL:
        return cached[1], cached[2]
    info = await bot.client.get_group_info(group)
    parent = community_of(info)
    community = parent.User if parent else None
    if info.GroupParent.IsParent:
        community = group.User
    _groups[group.User] = (now, community, info.GroupName.Name)
    return community, info.GroupName.Name


async def log_member_changes(gc_msg):
    """Queues a line for each join or leave in a group whose community logs them"""
    try:
        community, name = await group_details(gc_msg.JID)
        if not community:
            return
        if not (log_chat := bot.group_dict.get(community, {}).get("community_log")):
            return
        sender = await as_lid(gc_msg.Sender) if gc_msg.Sender.User else None
        changes = [(u, "join") for u in gc_msg.Join] + [
            (u, "leave") for u in gc_msg.Leave
        ]
        for user, action in changes:
            if is_me(user) or not (lid := await as_lid(user)):
                continue
            by = sender if sender and sender != lid else None
            if action == "join":
                detail = JOIN_REASONS.get(gc_msg.JoinReason) or (
                    f"added by @{by}" if by else ""
                )
            else:
                action, detail = ("remove", by) if by else ("leave", "")
            key = (log_chat, action, lid, detail)
            if key in _pending:
                if name not in _pending[key]:
                    _pending[key].append(name)
                continue
            _pending[key] = [name]
            asyncio.create_task(send_after_delay(key))
    except Exception:
        await logger(Exception)


async def count_rejoins(gc_msg):
    """Counts a join by someone who left this group before as a rejoin"""
    try:
        settings = bot.group_dict.setdefault(gc_msg.JID.User, {})
        left = settings.setdefault("has_left", set())
        rejoins = settings.setdefault("rejoins", {})
        for user in gc_msg.Leave:
            if not is_me(user) and (lid := await as_lid(user)):
                left.add(lid)
        for user in gc_msg.Join:
            if (lid := await as_lid(user)) in left:
                rejoins[lid] = rejoins.get(lid, 0) + 1
        await save2db2(bot.group_dict, "groups")
    except Exception:
        await logger(Exception)


async def send_after_delay(key: tuple):
    await asyncio.sleep(MERGE_DELAY)
    log_chat, action, user, detail = key
    groups = ", ".join(f"*{n}*" for n in _pending.pop(key, []))
    if action == "join":
        line = f"➕ @{user} joined {groups}" + (f" ({detail})" if detail else "")
    else:
        line = (
            f"{'➖' if action == 'leave' else '⛔'} @{user} {action}ed from {groups}"
            + (f" ({detail})" if detail else "")
        )
    await bot.client.send_message(
        jid.build_jid(log_chat, "g.us"), line, mentions=[user]
    )


async def keep_out_banned(gc_msg) -> set:
    """
    Removes users banned from the group or its community who just joined;
    the ids of those removed, so they aren't welcomed
    """
    banned = await banned_for(gc_msg.JID)
    if not banned:
        return set()
    joined = [j for j in gc_msg.Join if (await other_ids(j.User)) & banned]
    if not joined:
        return set()
    try:
        await bot.client.update_group_participants(
            gc_msg.JID, joined, ParticipantChange.REMOVE
        )
    except Exception:
        await logger(Exception)
        return set()
    return {j.User for j in joined}


async def decline_banned_request(gc_msg):
    """Rejects a banned user's request to join (the event's Sender)"""
    banned = await banned_for(gc_msg.JID)
    requester = gc_msg.Sender
    if not banned or not (await other_ids(requester.User)) & banned:
        return
    # Needs neonize with update_group_request_participants; until then a
    # banned user whose request is approved is removed on joining
    if not hasattr(bot.client, "update_group_request_participants"):
        return
    try:
        await bot.client.update_group_request_participants(
            gc_msg.JID, [requester], ParticipantRequestChange.REJECT
        )
    except Exception:
        await logger(Exception)


# Captcha settings
CAPTCHA_MINUTES = 5
CAPTCHA_TRIES = 3
# chat id -> {user id: pending captcha}, for members who joined and haven't
# solved theirs; kept in memory, so a restart lets them through
captchas: dict[str, dict[str, dict]] = {}


async def make_captcha() -> tuple[bytes, str]:
    """Generates a captcha image and code"""
    import io

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    code = "".join(str(i) for i in __import__("random").randint(1000, 9999))
    image = Image.new("RGB", (200, 80), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40
        )
    except Exception:
        font = ImageFont.load_default()
    draw.text((20, 15), code, font=font, fill=(0, 0, 0))
    # Add noise
    for _ in range(100):
        x = __import__("random").randint(0, 200)
        y = __import__("random").randint(0, 80)
        draw.point(
            (x, y),
            fill=(
                __import__("random").randint(0, 255),
                __import__("random").randint(0, 255),
                __import__("random").randint(0, 255),
            ),
        )
    image = image.filter(ImageFilter.GaussianBlur(1))
    out = io.BytesIO()
    image.save(out, format="png")
    return out.getvalue(), code


async def start_captchas(gc_msg, skip: set):
    """
    Sends a captcha to members who just joined by themselves, where the
    captcha is on; skip holds ids already dealt with (banned)
    """
    chat_id = gc_msg.JID.User
    config = bot.group_dict.get(chat_id, {}).get("captcha") or {}
    if not config.get("on"):
        return
    # Added by someone else, or approved by an admin: they vouched for them
    if gc_msg.Sender.User and all(gc_msg.Sender.User != j.User for j in gc_msg.Join):
        return
    minutes = config.get("minutes", CAPTCHA_MINUTES)
    for joined in gc_msg.Join:
        if joined.User in skip:
            continue
        try:
            png, code = await make_captcha()
            sent = await bot.client.send_image(
                gc_msg.JID,
                png,
                f"Welcome @{joined.User}! Send the code in this picture within "
                f"{minutes} minutes to stay in the group.",
                mentions_are_lids=joined.Server == "lid",
            )
        except Exception:
            await logger(Exception)
            continue
        pending = {
            "code": code,
            "tries": CAPTCHA_TRIES,
            "jid": joined,
            "message": getattr(sent, "ID", None),
        }
        for user in await other_ids(joined.User):
            captchas.setdefault(chat_id, {})[user] = pending
        asyncio.create_task(expire_captcha(gc_msg.JID, joined.User, pending, minutes))


async def finish_captcha(chat_jid, pending: dict, kick: bool, note: str):
    """Ends a pending captcha: deletes it, removes the member if kick"""
    group = captchas.get(chat_jid.User, {})
    for user in [u for u, p in group.items() if p is pending]:
        group.pop(user, None)
    if pending.get("message"):
        try:
            await bot.client.revoke_message(
                chat_jid, bot.client.me.JID, pending["message"]
            )
        except Exception:
            pass
    joined = pending["jid"]
    if kick:
        try:
            await bot.client.update_group_participants(
                chat_jid, [joined.JID], ParticipantChange.REMOVE
            )
        except Exception:
            await logger(Exception)
    await bot.client.send_message(
        chat_jid, f"@{joined.User} {note}", mentions_are_lids=joined.Server == "lid"
    )


async def expire_captcha(chat_jid, user: str, pending: dict, minutes: int):
    await asyncio.sleep(minutes * 60)
    if captchas.get(chat_jid.User, {}).get(user) is pending:
        await finish_captcha(
            chat_jid,
            pending,
            True,
            "*didn't solve the captcha in time and was removed.*",
        )


async def group_standing(event, client, ids: set) -> list[str]:
    """
    .id lines for a group: its community's id, the user's roles here, and
    the other groups of the community the bot sees them in
    """
    lines = []
    parent = community_of(await client.get_group_info(event.chat.jid))
    if parent:
        lines.append(f"- *Community:* {parent.User}@{parent.Server}")
    roles = bot.group_dict.get(event.chat.id, {}).get("roles", {})
    theirs = sorted(
        name for name, role in roles.items() if ids & set(role.get("members", []))
    )
    if theirs:
        lines.append(f"- *Roles:* {', '.join(theirs)}")
    if not parent:
        return lines
    groups = []
    for group in await client.get_sub_groups(parent):
        if group.JID.User == event.chat.id:
            continue
        try:
            info = await client.get_group_info(group.JID)
        except Exception:
            continue
        if any(find_member(u, info.Participants) for u in ids):
            groups.append(info)
    if groups:
        lines.append("- *Also in:*")
        groups.sort(key=lambda info: info.GroupName.Name.casefold())
        lines += [f"   - @{info.JID.User}@g.us" for info in groups]
    return lines

"""
Real WhatsApp buttons: native-flow quick replies. A button's id is
"<prefix>:<data>"; when one is pressed, the handler registered for the
prefix gets the press event and the data. Sent unwrapped: wrapped in a
view-once message, WhatsApp doesn't render them.
"""

import asyncio
import json
import uuid

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import InteractiveMessage

from bot import Message, bot
from bot.utils.events import add_handler
from bot.utils.log_utils import logger

# WhatsApp shows at most 10 quick replies per message, with short labels
MAX_BUTTONS = 10
MAX_LABEL = 20

_handlers = {}
# choose() calls waiting for a press: token -> what they wait for
_waiting: dict[str, dict] = {}


def on_button(prefix: str):
    """Registers an async handler(event, data) for buttons with this prefix"""

    def register(func):
        _handlers[prefix] = func
        return func

    return register


def sender_ids(event) -> set:
    """Every id a message's sender goes by (LID and number), to match presses"""
    return {u for u in (event.user.id, event.alt_user.id, event.from_user.id) if u}


def label(text: str) -> str:
    return text if len(text) <= MAX_LABEL else text[: MAX_LABEL - 1] + "…"


async def send_buttons(
    chat, text: str, buttons: list[tuple[str, str]], footer: str | None = None
) -> list[str]:
    """
    Sends buttons ((id, label) pairs) under text, MAX_BUTTONS per message;
    the ids of the messages sent
    """
    sent = []
    for start in range(0, len(buttons), MAX_BUTTONS):
        chunk = buttons[start : start + MAX_BUTTONS]
        interactive = InteractiveMessage(
            body=InteractiveMessage.Body(text=text if not start else "More:"),
            nativeFlowMessage=InteractiveMessage.NativeFlowMessage(
                buttons=[
                    InteractiveMessage.NativeFlowMessage.NativeFlowButton(
                        name="quick_reply",
                        buttonParamsJSON=json.dumps(
                            {"display_text": label(name), "id": key}
                        ),
                    )
                    for key, name in chunk
                ],
                messageVersion=1,
            ),
        )
        if footer:
            interactive.footer.text = footer
        response = await bot.client.send_message(
            chat, Message(interactiveMessage=interactive)
        )
        sent.append(response.ID)
    return sent


def pressed_button(msg) -> tuple[str, str] | None:
    """
    (button id, id of the message it was on) if msg is a button press, in
    whichever form the presser's WhatsApp sends it: some send native-flow
    replies as interactiveResponseMessage, others as the older
    templateButtonReplyMessage or buttonsResponseMessage
    """
    if msg.HasField("interactiveResponseMessage"):
        reply = msg.interactiveResponseMessage
        try:
            key = json.loads(reply.nativeFlowResponseMessage.paramsJSON).get("id")
        except (ValueError, AttributeError):
            key = None
    elif msg.HasField("templateButtonReplyMessage"):
        reply = msg.templateButtonReplyMessage
        key = reply.selectedID
    elif msg.HasField("buttonsResponseMessage"):
        reply = msg.buttonsResponseMessage
        key = reply.selectedButtonID
    else:
        return None
    return (key or "", reply.contextInfo.stanzaID)


async def button_press(event, args, client):
    """
    Passes a pressed button's data to the handler of its prefix.
    AUTO FUNCTION
    """
    if not (pressed := pressed_button(event.message.Message)):
        return
    prefix, _, data = pressed[0].partition(":")
    if not (handler := _handlers.get(prefix)):
        return
    try:
        await handler(event, data)
    except Exception:
        await logger(Exception)


async def choose(
    chat,
    title: str,
    options: dict,
    users: set | None = None,
    poll_user: str = "",
    quoted=None,
    timeout: float = 300.0,
):
    """
    Lets someone pick one of options ({key: label}) with buttons and returns
    its key, or None on timeout; users holds the ids allowed to press (all if
    empty). The labels are listed in full above the buttons, which only fit
    short ones. If the buttons can't be sent, a poll is used instead
    (poll_user and quoted are for it). The buttons are deleted afterwards.
    """
    keys = list(options)
    token = uuid.uuid4().hex[:10]
    future = asyncio.get_running_loop().create_future()
    _waiting[token] = {"future": future, "keys": keys, "users": users or set()}
    listing = "\n".join(f"{i}. {name}" for i, name in enumerate(options.values(), 1))
    sent = []
    try:
        sent = await send_buttons(
            chat,
            f"{title}\n\n{listing}",
            [
                (f"choose:{token}:{i}", f"{i + 1}. {name}")
                for i, name in enumerate(options.values())
            ],
        )
    except Exception:
        await logger(Exception)
        _waiting.pop(token, None)
        return await choose_by_poll(chat, title, options, poll_user, quoted, timeout)
    try:
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        return None
    finally:
        _waiting.pop(token, None)
        for message_id in sent:
            try:
                await bot.client.revoke_message(chat, bot.client.me.JID, message_id)
            except Exception:
                pass


async def choose_by_poll(chat, title, options: dict, user: str, quoted, timeout):
    """choose()'s fallback: the same choice as a single-answer poll"""
    from bot.utils.sudo_button_utils import create_sudo_button, wait_for_button_response

    poll = {
        key: [f"{i}. {name}", name] for i, (key, name) in enumerate(options.items(), 1)
    }
    _, msg = await create_sudo_button(title, poll, chat, user, 1, None, quoted)
    try:
        choice = await wait_for_button_response(msg.ID, timeout=timeout)
    finally:
        try:
            await bot.client.revoke_message(chat, bot.client.me.JID, msg.ID)
        except Exception:
            pass
    return choice[0] if choice else None


@on_button("choose")
async def chosen(event, data: str):
    """A press on choose()'s buttons, from someone allowed to press them"""
    token, _, index = data.partition(":")
    waiting = _waiting.get(token)
    if not waiting or waiting["future"].done() or not index.isdigit():
        return
    if waiting["users"] and not sender_ids(event) & waiting["users"]:
        # Someone else's buttons: a no on their press
        return await event.react("🚫")
    if int(index) < len(waiting["keys"]):
        waiting["future"].set_result(waiting["keys"][int(index)])


# Through events directly: this module can be imported before events has
# attached add_handler to bot (startup -> msg_rank -> wa -> here)
add_handler(button_press)

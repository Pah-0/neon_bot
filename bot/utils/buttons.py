"""
Real WhatsApp buttons: native-flow quick replies. A button's id is
"<prefix>:<data>"; when one is pressed, the handler registered for the
prefix gets the press event and the data. Sent unwrapped: wrapped in a
view-once message, WhatsApp doesn't render them.
"""

import json

from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import InteractiveMessage

from bot import Message, bot
from bot.utils.log_utils import logger

# WhatsApp shows at most 10 quick replies per message, with short labels
MAX_BUTTONS = 10
MAX_LABEL = 20

_handlers = {}
# Ids of the button messages sent, to spot replies to them that aren't
# recognised as presses (logged while button presses are looked into)
_sent: set[str] = set()


def on_button(prefix: str):
    """Registers an async handler(event, data) for buttons with this prefix"""

    def register(func):
        _handlers[prefix] = func
        return func

    return register


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
        _sent.add(response.ID)
    return sent


async def button_press(event, args, client):
    """
    Passes a pressed button's data to the handler of its prefix.
    AUTO FUNCTION
    """
    msg = event.message.Message
    if not msg.HasField("interactiveResponseMessage"):
        context = getattr(event, "_context_info", None)
        if context is not None and context.stanzaID in _sent:
            # A reply to a button message that isn't a press: log what came
            await logger(
                e=f"Unrecognised reply to a button message (from me: "
                f"{event.outgoing}, sender: {event.from_user.id}):\n{msg}"
            )
        return
    try:
        params = msg.interactiveResponseMessage.nativeFlowResponseMessage.paramsJSON
        key = json.loads(params).get("id") or ""
    except (ValueError, AttributeError):
        return
    if event.outgoing:
        # Presses from the bot's own account: logged while they're looked into
        await logger(e=f"Button press from me: {key!r}")
    prefix, _, data = key.partition(":")
    if not (handler := _handlers.get(prefix)):
        return
    try:
        await handler(event, data)
    except Exception:
        await logger(Exception)


bot.add_handler(button_press)

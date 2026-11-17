import re

from bot.utils.bot_utils import get_date_from_ts

# Stand-ins while converting, so new markers aren't matched again
BOLD, ITALIC, STRIKE, HOLD = "\x02", "\x03", "\x04", "\x05"
RULE = "──────────"

CODE_BLOCK = re.compile(r"```[^\S\n]*[\w+#.-]*\n?(.*?)```", re.S)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
ESCAPED = re.compile(r"\\([\\`*_~|#>\-\[\]()<:+.!])")
HELD = re.compile(HOLD + r"(\d+)" + HOLD)

HEADER = re.compile(r"(?m)^[^\S\n]{0,3}#{1,6}[^\S\n]+(.*?)(?:[^\S\n]+#+)?[^\S\n]*$")
SUBTEXT = re.compile(r"(?m)^-#[^\S\n]+(.*)$")
H_RULE = re.compile(r"(?m)^[^\S\n]*([-*_])(?:[^\S\n]*\1){2,}[^\S\n]*$")
BULLET = re.compile(r"(?m)^([^\S\n]*)[*+](?=[^\S\n])")
LONG_QUOTE = re.compile(r"(?m)^>>>[^\S\n]?(.*)", re.S)

IMAGE = re.compile(r"!\[([^\]]*)\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
LINK = re.compile(r"\[([^\]]+)\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")
ANGLE_URL = re.compile(r"<(https?://[^>\s]+)>")

# Discord: <t:1700000000:R>, <:emoji:123>, <@123>, <#123>, <@&123>, </cmd:123>
TIMESTAMP = re.compile(r"<t:(-?\d+)(?::[tTdDfFR])?>")
CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")
DISCORD_REF = re.compile(r"<(?:@[!&]?|#|/[\w -]+:)\d+>")

BOLD_ITALIC = re.compile(r"\*\*\*(?!\s)(.+?)(?<!\s)\*\*\*")
STRONG = re.compile(r"\*\*(?!\s)(.+?)(?<!\s)\*\*")
UNDERLINE = re.compile(r"(?<!\w)__(?!\s)(.+?)(?<!\s)__(?!\w)")
EMPHASIS = re.compile(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])")
STRIKETHROUGH = re.compile(r"~~(?!\s)(.+?)(?<!\s)~~")
SPOILER = re.compile(r"\|\|(.+?)\|\|", re.S)


def _link(match: re.Match) -> str:
    text, url = match.group(1).strip(), match.group(2)
    if not text or text.rstrip("/") == url.rstrip("/"):
        return url
    return f"{text}: {url}"


def _long_quote(text: str) -> str:
    """Discord's ">>> " quotes the rest of the message; WhatsApp's "> " a line"""
    if not (match := LONG_QUOTE.search(text)):
        return text
    lines = match.group(1).split("\n")
    return text[: match.start()] + "\n".join(f"> {line}" for line in lines)


def _wrap(marker: str, text: str) -> str:
    """text in marker, without the markers already in it"""
    text = text.replace(marker, "").strip()
    return f"{marker}{text}{marker}" if text else ""


def md_to_whatsapp(text: str) -> str:
    """
    Markdown, as Discord, GitHub and other sites write it, as WhatsApp text:
    headers in bold, **bold** as *bold*, *italic* as _italic_, ~~strike~~ as
    ~strike~, links as "text: url", Discord's mentions, custom emojis and
    spoilers dropped and its timestamps written out
    """
    held = []

    def hold(value: str) -> str:
        held.append(value)
        return f"{HOLD}{len(held) - 1}{HOLD}"

    text = text.replace("\r\n", "\n")
    # Code is left as it is, but as WhatsApp's monospace
    text = CODE_BLOCK.sub(lambda m: hold(f"```{
                m.group(1).strip(
                    chr(10))}```"), text)
    text = INLINE_CODE.sub(lambda m: hold(f"`{m.group(1)}`"), text)
    text = ESCAPED.sub(lambda m: hold(m.group(1)), text)

    text = TIMESTAMP.sub(lambda m: str(get_date_from_ts(int(m.group(1)))), text)
    text = CUSTOM_EMOJI.sub("", text)
    text = DISCORD_REF.sub("", text)
    text = IMAGE.sub(lambda m: m.group(2), text)
    text = LINK.sub(lambda m: hold(_link(m)), text)
    text = ANGLE_URL.sub(lambda m: hold(m.group(1)), text)
    text = SPOILER.sub(r"\1", text)

    # Line markers first, so "* item" and "***" aren't read as emphasis
    text = H_RULE.sub(RULE, text)
    text = BULLET.sub(r"\1-", text)
    text = _long_quote(text)

    text = BOLD_ITALIC.sub(lambda m: f"{BOLD}{ITALIC}{
            m.group(1)}{ITALIC}{BOLD}", text)
    text = STRONG.sub(lambda m: f"{BOLD}{m.group(1)}{BOLD}", text)
    # WhatsApp has no underline
    text = UNDERLINE.sub(r"\1", text)
    text = EMPHASIS.sub(lambda m: f"{ITALIC}{m.group(1)}{ITALIC}", text)
    text = STRIKETHROUGH.sub(lambda m: f"{STRIKE}{m.group(1)}{STRIKE}", text)

    text = HEADER.sub(lambda m: _wrap(BOLD, m.group(1)), text)
    text = SUBTEXT.sub(lambda m: _wrap(ITALIC, m.group(1)), text)

    text = text.translate({ord(BOLD): "*", ord(ITALIC): "_", ord(STRIKE): "~"})
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"(?<=\S)[^\S\n]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    while HELD.search(text):
        text = HELD.sub(lambda m: held[int(m.group(1))], text)
    return text

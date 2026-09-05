# tgsave — Telegram post link parsing
#
# Handles every link shape Telegram hands out:
#   t.me/channelname/55            public channel post
#   t.me/channelname/12/55         public forum topic post
#   t.me/c/1234567890/55           private channel post
#   t.me/c/1234567890/12/55        private forum topic post
#   t.me/s/channelname/55          web-preview link
#   ...with ?single, ?comment=, &t= query junk on the end
#
# The message id is always the LAST numeric path segment — that single rule is
# what keeps forum-topic links from being read as post ids.

import re
from urllib.parse import urlparse

HOSTS = {"t.me", "www.t.me", "telegram.me", "www.telegram.me", "telegram.dog"}

FINDER = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/\S+",
    re.IGNORECASE,
)


class BadLink(Exception):
    """Raised when a string is not a usable Telegram post link."""


def parse(url: str):
    """Return (chat, message_id).

    chat is an int like -1001234567890 for private /c/ links, otherwise the
    username string. Raises BadLink with a plain-English reason.
    """
    raw = (url or "").strip().rstrip(").,'\"")
    if not raw:
        raise BadLink("empty link")
    if "://" not in raw:
        raw = "https://" + raw.lstrip("/")

    bits = urlparse(raw)
    if bits.netloc.lower() not in HOSTS:
        raise BadLink("that is not a t.me link")

    parts = [p for p in bits.path.split("/") if p]
    if parts and parts[0] == "s":       # t.me/s/name/55 web preview
        parts = parts[1:]
    if not parts:
        raise BadLink("link has no post id")

    if parts[0] == "c":                 # private channel
        parts = parts[1:]
        if not parts or not parts[0].lstrip("-").isdigit():
            raise BadLink("private link is missing the channel id")
        chat = int(parts[0])
        if chat > 0:                    # some clients paste the -100 form already
            chat = int(f"-100{chat}")
        tail = parts[1:]
    else:                              # public username
        chat = parts[0]
        if chat.startswith("+") or chat.lower() == "joinchat":
            raise BadLink("that is an invite link, not a post link")
        tail = parts[1:]

    ids = [p for p in tail if p.isdigit()]
    if not ids:
        raise BadLink("link has no post id")
    return chat, int(ids[-1])


def find(text: str) -> list:
    """Pull every t.me link out of a blob of text, in order."""
    return FINDER.findall(text or "")


def short(chat) -> str:
    """Readable label for a chat, for status messages."""
    return f"@{chat}" if isinstance(chat, str) else str(chat)

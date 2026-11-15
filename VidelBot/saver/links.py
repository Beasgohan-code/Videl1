"""Parse supported Telegram post links without treating other URL types as posts."""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

_URL = re.compile(r"(?<![a-zA-Z0-9_./@-])https?://t\.me/[^\s<>]+", re.IGNORECASE)
_NAME = re.compile(r"[A-Za-z0-9_]{1,64}\Z")
_RANGE = re.compile(r"([0-9]{1,10})(?:-([0-9]{1,10}))?\Z")
_PRIVATE = re.compile(r"[0-9]{1,16}\Z")
_MAX_MESSAGE_ID = 2**31 - 1
_MAX_CHAT_ID = 2**63 - 1


@dataclass(frozen=True)
class PostLink:
    kind: str  # public, private, or bot
    chat: str | int
    first: int
    last: int


def parse_post_link(text: str) -> PostLink:
    """Return the first supported post link or raise ValueError.

    Only explicit public, private channel and bot post links are accepted. Public
    web-preview links (/s/name/id) resolve to the same post as /name/id.
    Story, invite, topic and other ambiguous paths are deliberately not guessed.
    Only the non-targeting ?single query is accepted. No link is opened here.
    """
    match = _URL.search(text or "")
    if not match or len(match.group()) > 2048:
        raise ValueError("Invalid post link")
    try:
        url = urlsplit(match.group())
    except ValueError as exc:
        raise ValueError("Invalid post link") from exc
    if url.scheme.lower() not in ("http", "https") or url.netloc.lower() != "t.me" or url.fragment or url.query not in ("", "single", "single=1"):
        raise ValueError("Invalid post link")
    parts = url.path.rstrip("/").split("/")[1:]
    kind = "public"
    if len(parts) == 3 and parts[0].lower() == "s":
        parts = parts[1:]
    elif len(parts) == 3 and parts[0].lower() in ("c", "b"):
        kind = "private" if parts[0].lower() == "c" else "bot"
        parts = parts[1:]
    if len(parts) != 2:
        raise ValueError("Unsupported post link")
    target, post = parts
    if kind == "private":
        if not _PRIVATE.fullmatch(target) or int(target) == 0 or target.startswith("0"):
            raise ValueError("Invalid channel")
        chat = -int("100" + target)
        if -chat > _MAX_CHAT_ID:
            raise ValueError("Invalid channel")
    else:
        if not _NAME.fullmatch(target) or target.lower() in ("c", "b", "s"):
            raise ValueError("Invalid username")
        chat = target
    ids = _RANGE.fullmatch(post)
    if not ids:
        raise ValueError("Invalid message range")
    first = int(ids.group(1))
    last = int(ids.group(2)) if ids.group(2) else first
    if not 1 <= first <= last <= _MAX_MESSAGE_ID:
        raise ValueError("Invalid message range")
    return PostLink(kind, chat, first, last)

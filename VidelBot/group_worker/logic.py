"""Pure moderation logic for the clone Group Management worker.

Everything here is dependency-free (no Pyrogram, no database) so it can
be unit-tested in isolation. The worker's commands and data model are
designed for clones only; they are not shared with the main bot's
group-management plugin.
"""
from __future__ import annotations

import re
import time
from collections import OrderedDict
from datetime import timedelta

_DURATION_RE = re.compile(r"^(\d{1,4})([mhdw])$", re.IGNORECASE)
_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_LINK_RE = re.compile(r"(https?://|t\.me/|telegram\.me/|www\.)", re.IGNORECASE)

# Guard kinds the clone worker can police on its own.
GUARD_KINDS = ("links", "media", "stickers", "forwards", "bots", "all")


def parse_duration(token: str) -> timedelta | None:
    """Parse ``30m`` / ``12h`` / ``7d`` / ``2w`` into a timedelta."""
    m = _DURATION_RE.match((token or "").strip())
    if not m:
        return None
    amount = int(m.group(1))
    if amount <= 0:
        return None
    return timedelta(seconds=amount * _UNIT_SECONDS[m.group(2).lower()])


def default_settings() -> dict:
    """Fresh per-(clone bot, chat) settings for the group worker."""
    return {
        "shield": False,          # passive guard master switch
        "guard_kinds": [],        # subset of GUARD_KINDS
        "greetings": False,       # welcome/goodbye messages
        "greet_text": "👋 Welcome {name} to {chat}!",
        "farewell_text": "👋 {name} left {chat}.",
        "warn_limit": 3,          # warns before warn_action fires
        "warn_action": "mute",    # mute | kick | ban
        "flood_limit": 0,         # 0 = off; otherwise messages per window
        "flood_window": 10.0,     # seconds
        "flood_action": "mute",   # mute | kick | ban
        "rules": "",              # group rules text (/rules)
        "replies": {},            # keyword -> auto-reply (/filter)
        "dm_alerts": True,        # DM the clone owner on moderation actions
        "notes": {},              # trigger -> saved note (/save, #trigger)
        "night_on": False,        # quiet hours: delete member messages
        "night_start": 23,        # UTC hour, inclusive
        "night_end": 7,           # UTC hour, exclusive
        "captcha_on": False,      # new members must tap a verify button
        "raid_on": False,         # join shield against join bursts
        "greet_dm": False,        # also DM the welcome text to new members
        "approved": {},           # user_id (str) -> reason; exempt from automation
        "log_chat": 0,            # chat ID receiving moderation log posts (0 = off)
        "greet_media": "",        # file_id of a photo/sticker welcome ("" = text only)
        "greet_media_type": "",   # photo | sticker
        "clean_welcome": False,   # delete the previous welcome when a new one posts
        "clean_service": False,   # delete Telegram service messages (joins, pins…)
        "lang": "en",             # UI language: en | hi | es
    }


LANGUAGES = ("en", "hi", "es")

_STRINGS = {
    "settings": {"en": "SETTINGS", "hi": "सेटिंग्स", "es": "AJUSTES"},
    "admins_only": {
        "en": "Only group admins can change settings.",
        "hi": "केवल ग्रुप एडमिन सेटिंग्स बदल सकते हैं।",
        "es": "Solo los administradores del grupo pueden cambiar los ajustes.",
    },
    "welcome_title": {
        "en": "GROUP MANAGEMENT",
        "hi": "ग्रुप प्रबंधन",
        "es": "GESTIÓN DE GRUPO",
    },
    "start_body": {
        "en": "Add me to a group, make me an admin, then open /settings there.\n"
              "I can greet members, guard the chat, and help admins with /warn, /mute, /kick and /ban.",
        "hi": "मुझे किसी ग्रुप में जोड़ें, एडमिन बनाएं, फिर वहाँ /settings खोलें।\n"
              "मैं सदस्यों का स्वागत कर सकता हूँ, चैट की रक्षा कर सकता हूँ और /warn, /mute, /kick, /ban में मदद कर सकता हूँ।",
        "es": "Añádeme a un grupo, hazme administrador y abre /settings allí.\n"
              "Puedo dar la bienvenida, proteger el chat y ayudar con /warn, /mute, /kick y /ban.",
    },
}


def tr(settings: dict, key: str) -> str:
    lang = (settings or {}).get("lang") or "en"
    return _STRINGS.get(key, {}).get(lang) or _STRINGS.get(key, {}).get("en", key)


def is_approved(settings: dict, user_id: int) -> bool:
    return str(int(user_id)) in (settings.get("approved") or {})


class JoinWatch:
    """Bounded join-burst tracker used by the anti-raid shield."""

    def __init__(self, max_keys: int = 1000):
        self._joins: "OrderedDict[int, list[float]]" = OrderedDict()
        self._max = max_keys
        self.lock_until: dict[int, float] = {}

    def record(self, chat_id: int, *, limit: int = 6, window: float = 60.0,
               lock_seconds: float = 600.0, now: float | None = None) -> bool:
        """Record a join; True when this join tripped (or extends) a raid lock."""
        now = time.monotonic() if now is None else now
        chat_id = int(chat_id)
        if self.lock_until.get(chat_id, 0) > now:
            return True
        bucket = [t for t in self._joins.get(chat_id, []) if now - t < window]
        bucket.append(now)
        self._joins[chat_id] = bucket
        self._joins.move_to_end(chat_id)
        while len(self._joins) > self._max:
            self._joins.popitem(last=False)
        if len(bucket) >= limit:
            self.lock_until[chat_id] = now + lock_seconds
            self._joins.pop(chat_id, None)
            return True
        return False

    def locked(self, chat_id: int, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self.lock_until.get(int(chat_id), 0) > now


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """True when *hour* falls in the [start, end) window (wraps midnight)."""
    start, end, hour = int(start) % 24, int(end) % 24, int(hour) % 24
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def match_reply(text: str, replies: dict) -> tuple[str, str] | None:
    """First keyword auto-reply matching *text* (whole-word, case-insensitive)."""
    if not text or text.startswith("/") or not replies:
        return None
    low = f" {text.lower()} "
    for keyword, reply in replies.items():
        needle = str(keyword).lower().strip()
        if needle and f" {needle} " in low:
            return str(keyword), str(reply)
    return None


def render_greeting(template: str, *, name: str, chat: str, user_id: int) -> str:
    text = (template or "")[:1000]
    return (
        text.replace("{name}", name or "member")
        .replace("{chat}", chat or "this group")
        .replace("{id}", str(user_id))
    )


class WarnLedger:
    """In-memory warn counter; the store persists counts separately."""

    def __init__(self, limit: int = 3):
        self.limit = max(1, int(limit))
        self._counts: dict[tuple[int, int], int] = {}

    def add(self, chat_id: int, user_id: int) -> tuple[int, bool]:
        key = (int(chat_id), int(user_id))
        self._counts[key] = self._counts.get(key, 0) + 1
        count = self._counts[key]
        reached = count >= self.limit
        if reached:
            self._counts.pop(key, None)
        return count, reached

    def forgive(self, chat_id: int, user_id: int) -> int:
        key = (int(chat_id), int(user_id))
        if self._counts.get(key):
            self._counts[key] -= 1
            if self._counts[key] <= 0:
                self._counts.pop(key, None)
                return 0
            return self._counts[key]
        return 0

    def reset(self, chat_id: int, user_id: int) -> None:
        self._counts.pop((int(chat_id), int(user_id)), None)

    def count(self, chat_id: int, user_id: int) -> int:
        return self._counts.get((int(chat_id), int(user_id)), 0)


class FloodWatch:
    """Bounded sliding-window flood tracker, keyed by (chat, user)."""

    def __init__(self, max_keys: int = 4000):
        self._buckets: "OrderedDict[tuple[int, int], list[float]]" = OrderedDict()
        self._max = max_keys

    def hit(self, chat_id: int, user_id: int, *, limit: int, window: float,
            now: float | None = None) -> bool:
        """Record a message; True when the sender crossed the limit."""
        if limit <= 0:
            return False
        now = time.monotonic() if now is None else now
        key = (int(chat_id), int(user_id))
        bucket = [t for t in self._buckets.get(key, []) if now - t < window]
        bucket.append(now)
        self._buckets[key] = bucket
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max:
            self._buckets.popitem(last=False)
        if len(bucket) >= limit:
            self._buckets.pop(key, None)
            return True
        return False


def content_violation(message, guard_kinds) -> str:
    """Return the first guard violation for a message-like object."""
    kinds = set(guard_kinds or [])
    if not kinds:
        return ""
    if "all" in kinds:
        return "all"
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    if "links" in kinds and _LINK_RE.search(text):
        return "links"
    if "forwards" in kinds and (getattr(message, "forward_date", None)
                                 or getattr(message, "forward_origin", None)):
        return "forwards"
    if "bots" in kinds and getattr(message, "via_bot", None):
        return "bots"
    if "stickers" in kinds and getattr(message, "sticker", None):
        return "stickers"
    if "media" in kinds and any(
        getattr(message, attr, None)
        for attr in ("photo", "video", "document", "animation", "audio", "voice")
    ):
        return "media"
    return ""


def sanitize_settings_import(data, defaults: dict) -> dict | None:
    """Validate an imported settings payload against the defaults' types.

    Returns a cleaned dict containing only known keys, or None when the
    payload is unusable. Dict values must map strings to strings.
    """
    if not isinstance(data, dict):
        return None
    out: dict = {}
    for key, default in defaults.items():
        if key not in data:
            continue
        value = data[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                return None
            out[key] = value
        elif isinstance(default, (int, float)):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            out[key] = value
        elif isinstance(default, str):
            if not isinstance(value, str):
                return None
            out[key] = value[:2000]
        elif isinstance(default, list):
            if (not isinstance(value, list) or len(value) > 100
                    or not all(isinstance(v, str) for v in value)):
                return None
            out[key] = [v[:60] for v in value]
        elif isinstance(default, dict):
            if not isinstance(value, dict) or len(value) > 200:
                return None
            if not all(isinstance(k, str) and isinstance(v, str)
                       for k, v in value.items()):
                return None
            out[key] = {k[:60]: v[:500] for k, v in value.items()}
    return out

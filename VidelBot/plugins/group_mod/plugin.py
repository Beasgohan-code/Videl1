"""
Richgram-powered Group Management & Bot API 10.3 Moderation Plugin.

Highlights:
  • Live permission verification (`verify_mod_permissions`) — queries Telegram's
    live `get_chat_member` for BOTH the caller and the bot on every admin action
    (never trusts a stale admin cache), honours anonymous group admins, and
    prevents acting against group owners/administrators.
  • Core moderation: `/gban`, `/gunban`, `/kick` (`/punch`), `/mute`, `/unmute`,
    `/warn`, `/unwarn`, `/warns`, plus Rose-style aliases `/ban`, `/tban`,
    `/dban`, `/sban`, `/unban`, `/tmute`, `/dmute`, `/smute`, `/dkick`, `/skick`,
    `/swarn`, `/dwarn`, `/rmwarn`, `/resetwarn`, `/warnings`, `/setwarnlimit`,
    `/setwarnmode`.
  • Bot API 10.3 / WzGram Layer 232 capabilities:
      - `/tag` & `/deltag` (`setChatMemberTag` / `client.set_chat_member_tag`)
      - `/clearreactions` (`/delreactions`) (`deleteMessageReaction` &
        `deleteAllMessageReactions`)
      - `/topic new|close|reopen` (forum topic lifecycle)
      - `/joinmode off|notify|auto` (join-request approval cards + raid guard)
      - `/pin`, `/unpin`, `/purge` (pin lifecycle + bounded history purge)
  • Community & protection: `/gsettings`, `/gcheck`, `/rules` (ephemeral), `/setrules`,
    `/welcome`, `/goodbye`, `/setwelcome`, `/resetwelcome`, `/lock`, `/unlock`,
    `/locks`, `/locktypes`, `/lockwarns`, `/gfilter`, `/delfilter`, `/gfilters`,
    `/filter`, `/stop`, `/filters`, `/addblocklist`, `/rmblocklist`, `/blocklist`,
    `/blocklistmode`, `/blocklistdelete`, `/save`, `/get`, `/notes`, `/clear`,
    `/setflood`, `/setfloodtimer`, `/setfloodmode`, `/flood`, `/clearflood`,
    `/captcha`, `/captchamode`, `/setcaptchatext`, `/antiraid`, `/modreport`.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import json
import logging
import secrets
import re
import time
from typing import Any

from pymongo.errors import DuplicateKeyError

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    Message,
)

from core import botapi, rich
from core.db import vdb
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from core.rich import Doc
from core.ui import styled_button

log = logging.getLogger("videl.plugin.group_mod")

PLG_FILTER = plugin_filter("group_mod")
DURATION_RE = re.compile(r"^(\d+)([mhdw])$", re.IGNORECASE)
LINK_RE = re.compile(r"(https?://\S+|t\.me/\S+|telegram\.me/\S+)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{7,}\d)")
EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]",
)
# Legacy locks (links/forwards/bots/media/all) keep their original meanings for
# backward compatibility; the remaining types are granular content locks.
VALID_LOCKS = (
    "links", "forwards", "bots", "media", "all",
    "text", "photo", "video", "document", "audio", "voice", "videonote",
    "sticker", "gif", "poll", "contact", "location", "game", "dice",
    "checklist", "album", "command", "url", "invitelink", "botlink",
    "email", "phone", "emoji", "forwarduser", "forwardbot", "forwardchannel",
    "inline", "externalreply", "spoiler",
)
LOCK_MODES = ("nothing", "warn", "kick", "ban", "tban", "mute", "tmute")
BLOCKLIST_MODES = ("nothing", "warn", "kick", "ban", "tban", "mute", "tmute")

GROUP_ANON_COMMANDS = frozenset({
    "gsettings", "gcheck", "gban", "gunban", "ban", "tban", "dban", "sban", "unban",
    "kick", "punch", "dkick", "skick", "mute", "tmute", "dmute", "smute", "unmute",
    "warn", "swarn", "dwarn", "unwarn", "rmwarn", "resetwarn", "warns", "warnings",
    "warnconfig", "warnrecover", "setwarnlimit", "setwarnmode",
    "tag", "deltag", "clearreactions", "delreactions",
    "topic", "joinmode", "rules", "setrules", "welcome", "goodbye", "setwelcome", "resetwelcome",
    "lock", "unlock", "locks", "locktypes", "lockwarns", "lockall", "unlockall",
    "whitelist", "rmwhitelist", "allowlist", "rmallowlist", "adminlist", "adminlists", "adminslist",
    "gfilter", "delfilter", "gfilters", "filter", "stop", "filters", "stopall",
    "addblocklist", "rmblocklist", "blocklist", "blocklistmode", "blocklistdelete",
    "save", "get", "notes", "clear",
    "setflood", "setfloodtimer", "setfloodmode", "flood", "clearflood",
    "captcha", "captchamode", "setcaptchatext", "resetcaptchatext",
    "pin", "unpin", "purge",
    "modlog", "modhistory",
    "setgoodbye", "resetgoodbye", "cleanwelcome", "cleanservice", "export", "import", "disable", "enable", "disabled", "permapin",
    "antiraid",
})

# Bounded short-TTL settings cache for O(1) group message checks (invalidated on write)
_CFG_CACHE: "OrderedDict[int, tuple[float, dict[str, Any]]]" = OrderedDict()
_CFG_TTL = 45.0
_CFG_MAX = 2000

# Bounded in-memory flood tracker: (chat_id, user_id) -> [timestamps]
_FLOOD_BUCKETS: "OrderedDict[tuple[int, int], list[float]]" = OrderedDict()
_FLOOD_MAX = 8000

# Avoid turning repeated failures during a raid into a log flood. Keys below
# are fixed internal event classes; no message or user content is stored.
_AUTOMATION_WARNINGS: dict[str, float] = {}
_AUTOMATION_WARNING_INTERVAL = 60.0


def _automation_warn(event: str, text: str) -> None:
    now = time.monotonic()
    last = _AUTOMATION_WARNINGS.get(event)
    if last is None or now - last >= _AUTOMATION_WARNING_INTERVAL:
        _AUTOMATION_WARNINGS[event] = now
        log.warning(text)


def _default_cfg(chat_id: int) -> dict[str, Any]:
    return {
        "_id": int(chat_id),
        "welcome_on": False,
        "welcome_text": "👋 Welcome {mention} to <b>{chat}</b>!",
        "goodbye_on": False,
        "goodbye_text": "👋 Goodbye, {first}!",
        "rules": "",
        "antiflood": False,
        "flood_limit": 7,
        "flood_window": 8.0,
        "flood_mode": "tmute 5m",
        "clearflood": False,
        "antiraid_until": 0.0,
        "antiraid_confirmed": False,
        "joinmode": "off",  # off | notify | auto
        "joinmode_auto_confirmed": False,
        "warn_limit": 3,
        "warn_action": "mute",  # mute | ban | kick | tban <time> | tmute <time>
        "locks": [],
        "lockwarns": False,
        "lock_modes": {},  # locktype -> {"mode": str, "reason": str}
        "filters": {},
        "url_whitelist": [],  # domains exempt from links/url locks
        "blocklist": {},  # trigger -> reason
        "blocklist_mode": "nothing",
        "blocklist_delete": True,
        "notes": {},  # trigger -> text
        "disabled_commands": [],  # bot commands blocked for non-admins
        "captcha_on": False,
        "captcha_text": "Click here to prove you're human",
        "cleanwelcome": False,
        "cleanservice": False,
        "last_welcome_id": 0,
    }


async def get_group_cfg(chat_id: int) -> dict[str, Any]:
    cid = int(chat_id)
    now = time.monotonic()
    hit = _CFG_CACHE.get(cid)
    if hit and (now - hit[0]) < _CFG_TTL:
        return hit[1]
    raw = await vdb.db["group_settings"].find_one({"_id": cid})
    cfg = _default_cfg(cid)
    if raw:
        cfg.update(raw)
    _CFG_CACHE[cid] = (now, cfg)
    _CFG_CACHE.move_to_end(cid)
    while len(_CFG_CACHE) > _CFG_MAX:
        _CFG_CACHE.popitem(last=False)
    return cfg


async def update_group_cfg(chat_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    cid = int(chat_id)
    await vdb.db["group_settings"].update_one({"_id": cid}, {"$set": updates}, upsert=True)
    _CFG_CACHE.pop(cid, None)
    return await get_group_cfg(cid)


_MODLOG_MAX_PER_CHAT = 1000


async def _log_mod_action(chat_id: int, action: str, target_id: int | None,
                          target_label: str = "", actor_id: int | None = None,
                          reason: str = "", silent: bool = False) -> None:
    """Record a moderation action. Best-effort: never raises into the handler."""
    try:
        col = vdb.db["group_modlog"]
        await col.insert_one({
            "chat_id": int(chat_id),
            "action": action,
            "target_id": target_id,
            "target_label": (target_label or "")[:100],
            "actor_id": actor_id,
            "reason": (reason or "")[:200],
            "silent": bool(silent),
            "ts": time.time(),
        })
        # Bound growth: keep only the newest entries per chat.
        old_ids = await col.find({"chat_id": int(chat_id)}, {"_id": 1}).sort("ts", -1).skip(_MODLOG_MAX_PER_CHAT).to_list(length=_MODLOG_MAX_PER_CHAT)
        if old_ids:
            await col.delete_many({"_id": {"$in": [d["_id"] for d in old_ids]}})
    except Exception:
        pass


def parse_duration(token: str) -> tuple[timedelta | None, str]:
    """Parse ``15m`` / ``2h`` / ``7d`` / ``1w`` into a ``timedelta`` and human label."""
    m = DURATION_RE.match((token or "").strip())
    if not m:
        return None, ""
    amount, unit = int(m.group(1)), m.group(2).lower()
    if amount <= 0:
        return None, ""
    mult = {"m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    secs = min(amount * mult, 365 * 86400)
    return timedelta(seconds=secs), f"{amount}{unit}"


def _until_dt(delta: timedelta | None) -> datetime | None:
    if not delta:
        return None
    return datetime.now(timezone.utc) + delta


def parse_mode(text: str) -> tuple[str, timedelta | None, str] | None:
    """Parse ``kick`` / ``ban`` / ``tban 3d`` / ``tmute 10m`` style modes.

    Returns (mode, delta_or_None, normalized_label) or None when invalid.
    """
    parts = (text or "").strip().split()
    if not parts:
        return None
    mode = parts[0].lower()
    if mode not in LOCK_MODES:
        return None
    if mode in ("tban", "tmute"):
        if len(parts) != 2:
            return None
        delta, label = parse_duration(parts[1])
        if delta is None:
            return None
        return mode, delta, f"{mode} {label}"
    if len(parts) != 1:
        return None
    return mode, None, mode


def _split_trigger_list(text: str) -> list[str]:
    """Split ``(a, b, "c d")`` or a single ``"phrase"`` / word into triggers."""
    s = (text or "").strip()
    if not s:
        return []
    if s.startswith("(") and s.endswith(")"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        # Split on commas that are outside quotes.
        items, buf, in_q = [], [], False
        for ch in inner:
            if ch == '"':
                in_q = not in_q
                buf.append(ch)
            elif ch == "," and not in_q:
                items.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        items.append("".join(buf).strip())
        out = []
        for it in items:
            if len(it) >= 2 and it[0] == '"' and it[-1] == '"':
                it = it[1:-1].strip()
            if it:
                out.append(it)
        return out
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return [s[1:-1].strip()] if s[1:-1].strip() else []
    return [s.split()[0]] if s.split() else []


def _trigger_matches(trigger: str, text: str) -> bool:
    """Case-insensitive match: single words use word boundaries, phrases substring."""
    if not trigger or not text:
        return False
    low_text = text.lower()
    low_trig = trigger.lower()
    if " " in low_trig:
        return low_trig in low_text
    return re.search(rf"(?<!\w){re.escape(low_trig)}(?!\w)", low_text) is not None


def _is_anon_group_admin(message: Message) -> bool:
    return bool(
        message.from_user is None
        and message.chat
        and message.sender_chat
        and message.sender_chat.id == message.chat.id
    )


def _has_privilege(member, privilege: str) -> bool:
    if member is None:
        return False
    status = getattr(member, "status", None)
    if status == enums.ChatMemberStatus.OWNER:
        return True
    if status != enums.ChatMemberStatus.ADMINISTRATOR:
        return False
    priv = getattr(member, "privileges", None)
    if priv is None:
        return False  # Missing rights are not proof of authorization.
    if privilege == "can_manage_tags":
        val = getattr(priv, "can_manage_tags", None)
        if val is not None:
            return bool(val)
        return bool(getattr(priv, "can_promote_members", False) or getattr(priv, "can_manage_chat", False))
    return bool(getattr(priv, privilege, False))


async def verify_bot_privilege(client: Client, chat_id: int, privilege: str) -> tuple[bool, str]:
    """Check the bot's current right without inferring authorization from absence."""
    bot_uid = getattr(getattr(client, "me", None), "id", None)
    if type(bot_uid) is not int or bot_uid <= 0:
        return False, "Could not verify bot identity and permissions."
    try:
        bot_mem = await client.get_chat_member(chat_id, bot_uid)
    except Exception:
        return False, "Could not verify bot permissions. Try again later."
    if not _has_privilege(bot_mem, privilege):
        pretty = privilege.replace("can_", "").replace("_", " ")
        return False, f"Promote me as admin with permission: {pretty}."
    return True, ""


_GROUP_CHECK_PRIVILEGES = (
    "can_restrict_members",
    "can_delete_messages",
    "can_pin_messages",
    "can_change_info",
    "can_invite_users",
)


async def _bot_rights_snapshot(client: Client, chat_id: int) -> dict[str, str]:
    """Read-only snapshot of this bot's key rights: granted / missing / unknown."""
    unknown = {p: "unknown" for p in _GROUP_CHECK_PRIVILEGES}
    bot_uid = getattr(getattr(client, "me", None), "id", None)
    if type(bot_uid) is not int or bot_uid <= 0:
        return unknown
    try:
        bot_mem = await client.get_chat_member(chat_id, bot_uid)
    except Exception:
        return unknown
    status = getattr(bot_mem, "status", None)
    if status == enums.ChatMemberStatus.OWNER:
        return {p: "granted" for p in _GROUP_CHECK_PRIVILEGES}
    if status != enums.ChatMemberStatus.ADMINISTRATOR:
        return {p: "missing" for p in _GROUP_CHECK_PRIVILEGES}
    if getattr(bot_mem, "privileges", None) is None:
        return unknown
    return {p: ("granted" if _has_privilege(bot_mem, p) else "missing") for p in _GROUP_CHECK_PRIVILEGES}


def _raid_active(cfg: dict[str, Any]) -> bool:
    return cfg.get("antiraid_confirmed") is True and cfg.get("antiraid_until", 0) > time.time()


def _join_mode(cfg: dict[str, Any]) -> str:
    mode = cfg.get("joinmode", "off")
    if mode == "auto" and cfg.get("joinmode_auto_confirmed") is not True:
        return "off"  # Legacy automatic approval requires explicit reconfirmation.
    return mode if mode in ("off", "notify", "auto") else "off"


async def verify_mod_permissions(
    client: Client,
    chat_id: int,
    caller_id: int | None,
    *,
    privilege: str = "can_restrict_members",
    is_anon_admin: bool = False,
    target_id: int | None = None,
    check_bot: bool = True,
) -> tuple[bool, str]:
    """
    Live Telegram permission verifier (never uses cached admin lists):
      1. Confirms caller is OWNER or ADMINISTRATOR with *privilege* (or anonymous group admin).
      2. Confirms the bot itself is ADMINISTRATOR in *chat_id* with *privilege*.
      3. When *target_id* is given, refuses self-moderation and refuses acting on group admins/owners.
    """
    if not is_anon_admin:
        if not caller_id:
            return False, "Could not verify caller identity."
        try:
            caller_mem = await client.get_chat_member(chat_id, caller_id)
        except Exception:
            return False, "Could not verify your admin status. Try again later."
        if not _has_privilege(caller_mem, privilege):
            pretty = privilege.replace("can_", "").replace("_", " ")
            return False, f"You need administrator permission: {pretty}."

    bot_uid = getattr(getattr(client, "me", None), "id", None)
    if target_id is not None and (type(target_id) is not int or target_id <= 0):
        return False, "A valid Telegram user ID is required."
    if target_id is not None and caller_id is not None and target_id == caller_id:
        return False, "You cannot moderate yourself."
    if bot_uid and target_id == bot_uid:
        return False, "I cannot moderate myself."
    if check_bot:
        ok, err = await verify_bot_privilege(client, chat_id, privilege)
        if not ok:
            return False, err

    if target_id is not None:
        try:
            t_mem = await client.get_chat_member(chat_id, target_id)
            status = getattr(t_mem, "status", None)
            if status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR):
                return False, "Cannot moderate a group owner or administrator."
            if status not in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED,
                              enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
                return False, "Could not verify the target’s group status. Try again later."
        except Exception:
            return False, "Could not verify the target’s group status. Try again later."

    return True, ""


async def extract_target_and_args(client: Client, message: Message) -> tuple[int | None, str, list[str]]:
    """Resolve target user from reply or first argument (`id` / `@username`)."""
    args = (message.command or [])[1:]
    rep = message.reply_to_message
    if rep and rep.from_user:
        u = rep.from_user
        label = u.first_name or str(u.id)
        return u.id, label, args
    if not args:
        return None, "", []
    first = args[0].strip()
    rest = args[1:]
    if first.lstrip("-").isdigit():
        uid = int(first)
        try:
            u = await client.get_users(uid)
            return uid, getattr(u, "first_name", None) or str(uid), rest
        except Exception:
            return uid, str(uid), rest
    if first.startswith("@") or re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", first):
        try:
            u = await client.get_users(first)
            if u and getattr(u, "id", None):
                return int(u.id), getattr(u, "first_name", None) or first, rest
        except Exception:
            return None, "", rest
    return None, "", args


def _normalise_domain(value: str) -> str:
    domain = (value or "").strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    domain = domain.rsplit("@", 1)[-1].split(":", 1)[0]
    return domain[4:] if domain.startswith("www.") else domain


def _url_is_whitelisted(text: str, whitelist: list[str] | None) -> bool:
    """True only when every URL in *text* belongs to a whitelisted domain."""
    allowed = {_normalise_domain(item) for item in (whitelist or [])}
    allowed.discard("")
    if not allowed:
        return False
    urls = LINK_RE.findall(text or "")
    if not urls:
        return False
    for raw_url in urls:
        candidate = raw_url if isinstance(raw_url, str) else "".join(str(part) for part in raw_url if part)
        host = _normalise_domain(candidate)
        if not any(host == domain or host.endswith("." + domain) for domain in allowed):
            return False
    return True


def match_locked_content(message: Message, locks: list[str], url_whitelist: list[str] | None = None) -> str:
    """Return the first lock hit, using the same precedence as the live guard."""
    text = message.text or message.caption or ""
    if "all" in locks:
        return "all"
    lockset = set(locks)
    url_allowed = _url_is_whitelisted(text, url_whitelist)
    # Legacy broad locks keep their original meanings.
    if "links" in lockset and not url_allowed and LINK_RE.search(text):
        return "links"
    if "forwards" in lockset and (getattr(message, "forward_date", None) or getattr(message, "forward_origin", None)):
        return "forwards"
    if "media" in lockset and any(getattr(message, k, None) for k in ("photo", "video", "document", "animation", "sticker")):
        return "media"
    if "bots" in lockset and getattr(message, "via_bot", None):
        return "bots"
    # Granular content locks.
    if "text" in lockset and text:
        return "text"
    if "command" in lockset and text.startswith("/"):
        return "command"
    if "photo" in lockset and getattr(message, "photo", None):
        return "photo"
    if "video" in lockset and getattr(message, "video", None):
        return "video"
    if "document" in lockset and getattr(message, "document", None):
        return "document"
    if "audio" in lockset and getattr(message, "audio", None):
        return "audio"
    if "voice" in lockset and getattr(message, "voice", None):
        return "voice"
    if "videonote" in lockset and getattr(message, "video_note", None):
        return "videonote"
    if "sticker" in lockset and getattr(message, "sticker", None):
        return "sticker"
    if "gif" in lockset and getattr(message, "animation", None):
        return "gif"
    if "poll" in lockset and getattr(message, "poll", None):
        return "poll"
    if "contact" in lockset and getattr(message, "contact", None):
        return "contact"
    if "location" in lockset and (getattr(message, "location", None) or getattr(message, "venue", None)):
        return "location"
    if "game" in lockset and getattr(message, "game", None):
        return "game"
    if "dice" in lockset and getattr(message, "dice", None):
        return "dice"
    if "checklist" in lockset and getattr(message, "checklist", None):
        return "checklist"
    if "album" in lockset and getattr(message, "media_group_id", None):
        return "album"
    if "inline" in lockset and getattr(message, "via_bot", None):
        return "inline"
    if "externalreply" in lockset and getattr(message, "external_reply", None):
        return "externalreply"
    if "spoiler" in lockset and getattr(message, "has_media_spoiler", None):
        return "spoiler"
    # Forward sub-types.
    if "forwarduser" in lockset and getattr(message, "forward_from", None):
        return "forwarduser"
    if "forwardbot" in lockset:
        fwd = getattr(message, "forward_from", None)
        if fwd is not None and getattr(fwd, "is_bot", None) is True:
            return "forwardbot"
    if "forwardchannel" in lockset and getattr(message, "forward_from_chat", None):
        return "forwardchannel"
    # Text-pattern locks.
    if "url" in lockset and not url_allowed and LINK_RE.search(text):
        return "url"
    if "invitelink" in lockset and re.search(r"t\.me/(?:joinchat/|\+)\S+|telegram\.me/(?:joinchat/|\+)\S+", text, re.IGNORECASE):
        return "invitelink"
    if "botlink" in lockset and re.search(r"t\.me/[A-Za-z][A-Za-z0-9_]{4,31}\b", text):
        return "botlink"
    if "email" in lockset and EMAIL_RE.search(text):
        return "email"
    if "phone" in lockset and PHONE_RE.search(text):
        return "phone"
    if "emoji" in lockset and EMOJI_RE.search(text):
        return "emoji"
    return ""


def match_keyword_filter(text: str, fmap: dict[str, str]) -> tuple[str, str] | None:
    """Return the live guard's first keyword match, without sending anything."""
    if text and not text.startswith("/"):
        low = text.lower()
        for trigger, reply in fmap.items():
            trig = trigger.lower()
            if trig.startswith("prefix:"):
                needle = trig[len("prefix:"):]
                if needle and low.startswith(needle):
                    return trigger, reply
            elif trig.startswith("exact:"):
                needle = trig[len("exact:"):]
                if needle and low.strip() == needle:
                    return trigger, reply
            elif _trigger_matches(trig, text):
                return trigger, reply
    return None


def match_blocklist(text: str, bmap: dict[str, str]) -> tuple[str, str] | None:
    """Return the first blocklist hit (locks take precedence in the guard)."""
    if text and not text.startswith("/"):
        for trigger, reason in bmap.items():
            if _trigger_matches(trigger, text):
                return trigger, reason
    return None


def _full_send_permissions(chat=None) -> ChatPermissions:
    base = getattr(chat, "permissions", None)
    if isinstance(base, ChatPermissions):
        return base
    return ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_send_polls=True,
        can_invite_users=True,
    )


def _format_greeting(template: str, user, chat_title: str) -> str:
    uid = getattr(user, "id", 0)
    first = rich.esc(getattr(user, "first_name", None) or "Member")
    mention = f'<a href="tg://user?id={uid}">{first}</a>' if uid else first
    safe_tpl = rich.esc(template)
    return (
        safe_tpl.replace("{mention}", mention)
        .replace("{first}", first)
        .replace("{chat}", rich.esc(chat_title or "Group"))
        .replace("{id}", str(uid))
    )


def build_gsettings_doc(chat_title: str, cfg: dict[str, Any]) -> tuple[Doc, InlineKeyboardMarkup]:
    raid_active = _raid_active(cfg)
    legacy_raid = cfg.get("antiraid_until", 0) > time.time() and not raid_active
    join_mode = _join_mode(cfg)
    legacy_auto = cfg.get("joinmode") == "auto" and join_mode != "auto"
    locks = cfg.get("locks") or []
    doc = Doc("🛡", "Group Settings", chat_title or "Supergroup")
    flood_state = (
        f"🟢 {cfg.get('flood_limit', 7)}/{cfg.get('flood_window', 8.0)}s → {cfg.get('flood_mode', 'tmute 5m')}"
        if cfg.get("antiflood") else "🔴 Off"
    )
    doc.kv_table([
        ("👋 Welcome", "🟢 Enabled" if cfg.get("welcome_on") else "🔴 Off"),
        ("🚪 Goodbye", "🟢 Enabled" if cfg.get("goodbye_on") else "🔴 Off"),
        ("🌊 Anti-Flood", flood_state),
        ("🚨 Anti-Raid", "🛡 Active" if raid_active else "⚠️ Previous setting inactive; confirm again" if legacy_raid else "⚪ Standby"),
        ("🚪 Join Requests", "⚠️ AUTO inactive; confirm again" if legacy_auto else join_mode.upper()),
        ("⚠️ Warn Limit", f"{cfg.get('warn_limit', 3)} → {cfg.get('warn_action', 'mute')}"),
        ("🔒 Active Locks", ", ".join(locks) if locks else "None"),
        ("💬 Keyword Filters", str(len(cfg.get("filters") or {}))),
        ("🚫 Blocklist", f"{len(cfg.get('blocklist') or {})} triggers → {cfg.get('blocklist_mode', 'nothing')}"),
        ("📝 Notes", str(len(cfg.get("notes") or {}))),
        ("🤖 CAPTCHA", "🟢 Button" if cfg.get("captcha_on") else "🔴 Off"),
        ("📜 Rules", "Configured" if cfg.get("rules") else "Not set"),
        ("✅ URL Whitelist", str(len(cfg.get("url_whitelist") or []))),
        ("🚫 Disabled Commands", str(len(cfg.get("disabled_commands") or []))),
        ("🧹 Clean Welcome", "🟢 On" if cfg.get("cleanwelcome") else "🔴 Off"),
        ("🧹 Clean Service", "🟢 On" if cfg.get("cleanservice") else "🔴 Off"),
    ], key_header="Module", val_header="State")

    kb = InlineKeyboardMarkup([
        [
            styled_button(
                f"{'🟢' if cfg.get('welcome_on') else '🔴'} Welcome",
                callback_data="plg_gmod:tog:welcome",
                style="success" if cfg.get("welcome_on") else "danger",
            ),
            styled_button(
                f"{'🟢' if cfg.get('goodbye_on') else '🔴'} Goodbye",
                callback_data="plg_gmod:tog:goodbye",
                style="success" if cfg.get("goodbye_on") else "danger",
            ),
        ],
        [
            styled_button(
                f"{'🟢' if cfg.get('antiflood') else '🔴'} Anti-Flood",
                callback_data="plg_gmod:tog:antiflood",
                style="success" if cfg.get("antiflood") else "danger",
            ),
            styled_button(
                f"🚨 Raid: {'ON' if raid_active else 'OFF'}",
                callback_data="plg_gmod:tog:antiraid",
                style="danger" if raid_active else "primary",
            ),
        ],
        [
            styled_button(
                f"🚪 Join: {join_mode}",
                callback_data="plg_gmod:tog:joinmode",
                style="primary",
            ),
            styled_button(
                f"⚠️ Action: {cfg.get('warn_action', 'mute')}",
                callback_data="plg_gmod:tog:warnact",
                style="primary",
            ),
        ],
        [Btn("❌ Close", callback_data="close_btn")],
    ])
    return doc, kb


# ───────────────────────── Dashboard & Rules ─────────────────────────
@Client.on_message(filters.group & filters.command("gsettings") & PLG_FILTER)
async def gsettings_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Access Denied").status("error", "Admin permission required", err))
    cfg = await get_group_cfg(message.chat.id)
    doc, kb = build_gsettings_doc(message.chat.title or "Group", cfg)
    return await message.reply_text(doc.classic(), reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("gcheck") & PLG_FILTER)
async def gcheck_cmd(client: Client, message: Message):
    """Read-only setup diagnostic: automation state + this bot's live rights snapshot."""
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Access Denied").status("error", "Admin permission required", err))
    cfg = await get_group_cfg(message.chat.id)
    rights = await _bot_rights_snapshot(client, message.chat.id)
    raid_active = _raid_active(cfg)
    legacy_raid = cfg.get("antiraid_until", 0) > time.time() and not raid_active
    legacy_auto = cfg.get("joinmode") == "auto" and _join_mode(cfg) != "auto"
    doc = Doc("🔍", "Group Check", message.chat.title or "Supergroup")
    flood_state = (
        f"🟢 {cfg.get('flood_limit', 7)}/{cfg.get('flood_window', 8.0)}s → {cfg.get('flood_mode', 'tmute 5m')}"
        if cfg.get("antiflood") else "🔴 Off"
    )
    doc.kv_table([
        ("🚨 Anti-Raid", "🛡 Active" if raid_active else "⚠️ Previous setting inactive; confirm again" if legacy_raid else "⚪ Standby"),
        ("🚪 Join Requests", "⚠️ AUTO inactive; confirm again" if legacy_auto else _join_mode(cfg).upper()),
        ("🌊 Anti-Flood", flood_state),
        ("⚠️ Warn Limit", f"{cfg.get('warn_limit', 3)} → {cfg.get('warn_action', 'mute')}"),
        ("🔒 Locks", ", ".join(cfg.get("locks") or []) or "None"),
        ("💬 Filters", str(len(cfg.get("filters") or {}))),
        ("🚫 Blocklist", f"{len(cfg.get('blocklist') or {})} triggers → {cfg.get('blocklist_mode', 'nothing')}"),
        ("📝 Notes", str(len(cfg.get("notes") or {}))),
        ("🤖 CAPTCHA", "🟢 Button" if cfg.get("captcha_on") else "🔴 Off"),
        ("🤖 Restrict members", rights.get("can_restrict_members", "unknown")),
        ("🗑 Delete messages", rights.get("can_delete_messages", "unknown")),
        ("📌 Pin messages", rights.get("can_pin_messages", "unknown")),
        ("✏️ Change info", rights.get("can_change_info", "unknown")),
        ("✉️ Invite users", rights.get("can_invite_users", "unknown")),
    ], key_header="Check", val_header="State")
    doc.status("info", "Read-only snapshot", "Rights are checked live; Telegram can change them at any time. No settings were changed.")
    return await rich.reply(message, doc)


@Client.on_message(filters.group & filters.command("rules") & PLG_FILTER)
async def rules_cmd(client: Client, message: Message):
    cfg = await get_group_cfg(message.chat.id)
    rules_txt = (cfg.get("rules") or "").strip()
    doc = Doc("📜", f"Rules · {message.chat.title or 'Group'}", "community guidelines")
    if not rules_txt:
        doc.status("empty", "No rules configured", "Admins can set rules with /setrules <text>.")
    else:
        doc.quote(rich.esc(rules_txt))
    return await rich.reply(message, doc)


@Client.on_message(filters.group & filters.command("setrules") & PLG_FILTER)
async def setrules_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Access Denied").status("error", "Admin permission required", err))
    parts = (message.text or "").split(maxsplit=1)
    raw = parts[1].strip() if len(parts) > 1 else ""
    if raw.lower() in ("off", "clear", "none"):
        raw = ""
    await update_group_cfg(message.chat.id, {"rules": raw[:2000]})
    doc = Doc("📜", "Group Rules Updated")
    if raw:
        doc.status("success", "Rules saved", f"{len(raw)} characters configured.")
    else:
        doc.status("info", "Rules cleared", "Group rules have been removed.")
    return await rich.reply(message, doc)


# ───────────────────────── Warning policy and atomic warning updates ─────────────────────────
_WARN_RECORD_LIMIT = 100
_WARN_UPDATE_RETRIES = 5


async def _change_warns(chat_id: int, target_id: int, reason: str | None) -> list[str]:
    """CAS updates prevent ordinary concurrent appends/removals from losing entries.

    Telegram enforcement and MongoDB cannot be transactional. A separate
    action claim limits overlap, but this does not protect against ABA changes.
    """
    col = vdb.db["group_warns"]
    key = f"{chat_id}:{target_id}"
    for _ in range(_WARN_UPDATE_RETRIES):
        current = await col.find_one({"_id": key})
        if current and current.get("enforcement") is not None:
            raise RuntimeError("Warning enforcement is pending or requires recovery; check the member's Telegram status.")
        if current and "reasons" in current and not isinstance(current["reasons"], list):
            raise ValueError("Warning record is invalid; ask an administrator to inspect it.")
        old = list((current or {}).get("reasons") or [])
        if reason is None:
            if not old:
                return []
            new = old[:-1]
        else:
            if len(old) >= _WARN_RECORD_LIMIT:
                raise ValueError("Warning record is full; resolve existing warnings before adding more.")
            new = [*old, reason]
        query: dict[str, Any] = {
            "_id": key,
            "reasons": current["reasons"] if current is not None and "reasons" in current else {"$exists": False},
            "enforcement": {"$exists": False},
        }
        try:
            result = await col.update_one(
                query, {"$set": {"chat_id": chat_id, "user_id": target_id, "reasons": new}},
                upsert=current is None,
            )
        except DuplicateKeyError:
            continue
        if result.matched_count or result.upserted_id is not None:
            return new
    raise RuntimeError("Warnings changed concurrently; reload and try again.")


async def _apply_restriction(client: Client, chat_id: int, target_id: int, mode: str,
                             delta: timedelta | None = None) -> bool:
    """Apply a Rose-style restriction mode. Returns True when confirmed."""
    try:
        if mode == "kick":
            await client.ban_chat_member(chat_id, target_id)
            await client.unban_chat_member(chat_id, target_id)
        elif mode == "ban":
            await client.ban_chat_member(chat_id, target_id)
        elif mode == "tban":
            await client.ban_chat_member(chat_id, target_id, until_date=_until_dt(delta))
        elif mode == "mute":
            await client.restrict_chat_member(chat_id, target_id, ChatPermissions())
        elif mode == "tmute":
            await client.restrict_chat_member(chat_id, target_id, ChatPermissions(),
                                               until_date=_until_dt(delta))
        else:
            return False
        return True
    except Exception:
        return False


async def _do_restriction(client: Client, message: Message, kind: str, *,
                          silent: bool = False, delete_reply: bool = False,
                          require_duration: bool = False):
    """Shared handler for ban/mute/kick aliases with silent/delete variants."""
    cmd = (message.command or [kind])[0].lower()
    target_id, label, rest = await extract_target_and_args(client, message)
    if not target_id:
        usage = {
            "ban": "/ban <user> [reason]",
            "tban": "/tban <user> <30m|2h|7d> [reason]",
            "mute": "/mute <user> [reason]",
            "tmute": "/tmute <user> <30m|2h|7d> [reason]",
            "kick": "/kick <user> [reason]",
        }.get(kind, f"/{cmd} <user> [reason]")
        return await rich.reply(message, Doc("🔒", "Restriction").status("info", "Usage", f"Reply or send {usage}"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message), target_id=target_id,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    delta, dur_lbl = (None, "")
    reason_parts = list(rest)
    if kind in ("tban", "tmute"):
        if not rest:
            return await rich.reply(message, Doc("🔒", "Restriction").status("info", "Usage", f"/{kind} <user> <30m|2h|7d> [reason]"))
        delta, dur_lbl = parse_duration(rest[0])
        if delta is None:
            return await rich.reply(message, Doc("🔒", "Restriction").status("warning", "Invalid duration", "Use 30m, 2h, 7d or 1w."))
        reason_parts = list(rest[1:])
    elif require_duration and rest:
        delta, dur_lbl = parse_duration(rest[0])
        if delta is not None:
            reason_parts = list(rest[1:])
    reason = " ".join(reason_parts).strip()[:200]
    if delete_reply and message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except Exception:
            pass
    mode = {"ban": "ban", "tban": "tban", "mute": "mute", "tmute": "tmute", "kick": "kick"}[kind]
    done = await _apply_restriction(client, message.chat.id, target_id, mode, delta)
    if done:
        await _log_mod_action(message.chat.id, kind, target_id, label, uid, reason, silent)
    if silent:
        try:
            await message.delete()
        except Exception:
            pass
        return None
    if not done:
        return await rich.reply(message, Doc("❌", "Restriction Failed").status("error", "Telegram API error", "Action was not confirmed. Check Telegram before retrying."))
    title = {"ban": "Member Banned", "tban": "Member Temporarily Banned", "mute": "Member Muted",
             "tmute": "Member Temporarily Muted", "kick": "Member Kicked"}[kind]
    doc = Doc("🔒", title, message.chat.title or "Group").kv_table([
        ("👤 User", f"{label} ({target_id})"),
        ("⏱ Duration", dur_lbl or ("Permanent" if kind in ("ban",) else "Until unmuted" if kind == "mute" else "—")),
        ("📝 Reason", reason or "Not specified"),
    ])
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("warnconfig") & PLG_FILTER)
async def warnconfig_cmd(client: Client, message: Message):
    """Review or set the threshold policy; it never changes existing warnings."""
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Warning Policy").status("error", "Permission check failed", err))
    args = (message.command or [])[1:]
    if not args:
        cfg = await get_group_cfg(message.chat.id)
        return await rich.reply(message, Doc("⚠️", "Warning Policy").kv_table([
            ("Threshold", str(cfg.get("warn_limit", 3))),
            ("Action", str(cfg.get("warn_action", "mute"))),
            ("Usage", "/warnconfig <1–20> <kick|ban|tban TIME|mute|tmute TIME>"),
        ]))
    if len(args) < 2 or len(args[0]) > 2 or not args[0].isascii() or not args[0].isdigit() or not 1 <= int(args[0]) <= 20:
        return await rich.reply(message, Doc("⚠️", "Warning Policy").status("warning", "Invalid policy", "Use /warnconfig <1–20> <kick|ban|tban TIME|mute|tmute TIME>. Existing warnings are unchanged."))
    parsed = parse_mode(" ".join(args[1:]))
    if parsed is None or parsed[0] == "nothing":
        return await rich.reply(message, Doc("⚠️", "Warning Policy").status("warning", "Invalid action", "Use kick, ban, tban TIME, mute or tmute TIME."))
    limit, action = int(args[0]), parsed[2]
    await update_group_cfg(message.chat.id, {"warn_limit": limit, "warn_action": action})
    return await rich.reply(message, Doc("⚠️", "Warning Policy Updated").status("success", f"{limit} → {action}", "Existing warnings are unchanged. Enforcement happens on a new warning."))


@Client.on_message(filters.group & filters.command("warnrecover") & PLG_FILTER)
async def warnrecover_cmd(client: Client, message: Message):
    """Explicitly unlock an abandoned claim, without retrying enforcement."""
    target_id, label, rest = await extract_target_and_args(client, message)
    if not target_id or [s.lower() for s in rest] != ["confirm"]:
        return await rich.reply(message, Doc("⚠️", "Warning Recovery").status("info", "Check Telegram first", "Reply to the member with /warnrecover confirm, or use /warnrecover <user> confirm. Only claims older than 2 minutes can be unlocked; age does not prove the operation has stopped. This does not retry or undo Telegram actions."))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(client, message.chat.id, uid, privilege="can_restrict_members", is_anon_admin=_is_anon_group_admin(message), check_bot=False)
    if not ok:
        return await rich.reply(message, Doc("🔒", "Recovery Refused").status("error", "Permission check failed", err))
    if type(target_id) is not int or target_id <= 0:
        return await rich.reply(message, Doc("⚠️", "Warning Recovery").status("error", "Invalid user", "A valid Telegram user ID is required."))
    key = f"{message.chat.id}:{target_id}"
    record = await vdb.db["group_warns"].find_one({"_id": key})
    claim = (record or {}).get("enforcement")
    if not isinstance(claim, dict) or not isinstance(claim.get("token"), str) or not isinstance(claim.get("started_ts"), (int, float)):
        return await rich.reply(message, Doc("⚠️", "Warning Recovery").status("info", "No recoverable claim", "Refresh and inspect the warning record."))
    if time.time() - claim["started_ts"] < 120:
        return await rich.reply(message, Doc("⚠️", "Warning Recovery").status("warning", "Still pending", "Wait at least 2 minutes and check Telegram before recovery."))
    result = await vdb.db["group_warns"].update_one({"_id": key, "enforcement": claim}, {"$unset": {"enforcement": ""}})
    if not result.matched_count:
        return await rich.reply(message, Doc("⚠️", "Warning Recovery").status("warning", "Claim changed", "Refresh and inspect before trying again."))
    return await rich.reply(message, Doc("✅", "Warning Claim Released").status("info", f"{label} ({target_id})", "Warnings are retained. Telegram enforcement may still be running or may have completed; check status before further action."))


# ───────────────────────── Bans, Kicks, Mutes & Warns ─────────────────────────
@Client.on_message(filters.group & filters.command("gban") & PLG_FILTER)
async def gban_cmd(client: Client, message: Message):
    target_id, label, rest = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("🚫", "Group Ban").status("info", "Usage", "Reply to a user or send /gban <user> [30m|2h|7d] [reason]"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message), target_id=target_id,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Ban Refused").status("error", "Permission check failed", err))

    delta, dur_lbl = parse_duration(rest[0]) if rest else (None, "")
    reason = " ".join(rest[1:] if delta else rest).strip()[:200]
    try:
        kw = {"until_date": _until_dt(delta)} if delta else {}
        await client.ban_chat_member(message.chat.id, target_id, **kw)
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Ban Failed").status("error", "Telegram API error", str(e)))
    await _log_mod_action(message.chat.id, "gban", target_id, label, uid, reason)

    doc = Doc("🚫", "Member Banned", message.chat.title or "Group").kv_table([
        ("👤 User", f"{label} ({target_id})"),
        ("⏱ Duration", dur_lbl or "Permanent"),
        ("📝 Reason", reason or "Not specified"),
    ])
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("gunban") & PLG_FILTER)
async def gunban_cmd(client: Client, message: Message):
    target_id, label, _ = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("✅", "Group Unban").status("info", "Usage", "Reply or send /gunban <user_id|@username>"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Unban Refused").status("error", "Permission check failed", err))
    try:
        await client.unban_chat_member(message.chat.id, target_id)
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Unban Failed").status("error", "Telegram API error", str(e)))
    await _log_mod_action(message.chat.id, "gunban", target_id, label, uid)
    doc = Doc("✅", "Member Unbanned").status("success", f"{label} ({target_id})", "May rejoin the group.")
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command(["kick", "punch"]) & PLG_FILTER)
async def kick_cmd(client: Client, message: Message):
    target_id, label, rest = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("👢", "Kick Member").status("info", "Usage", "Reply or send /kick <user> [reason]"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message), target_id=target_id,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Kick Refused").status("error", "Permission check failed", err))
    reason = " ".join(rest).strip()[:200]
    try:
        await client.ban_chat_member(message.chat.id, target_id)
        await client.unban_chat_member(message.chat.id, target_id)
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Kick Failed").status("error", "Telegram API error", str(e)))
    await _log_mod_action(message.chat.id, "kick", target_id, label, uid, reason)
    doc = Doc("👢", "Member Kicked").kv_table([
        ("👤 User", f"{label} ({target_id})"),
        ("📝 Reason", reason or "Removed by moderator"),
    ])
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("mute") & PLG_FILTER)
async def mute_cmd(client: Client, message: Message):
    target_id, label, rest = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("🔇", "Mute Member").status("info", "Usage", "Reply or send /mute <user> [15m|2h|1d] [reason]"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message), target_id=target_id,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Mute Refused").status("error", "Permission check failed", err))

    delta, dur_lbl = parse_duration(rest[0]) if rest else (None, "")
    reason = " ".join(rest[1:] if delta else rest).strip()[:200]
    try:
        kw = {"until_date": _until_dt(delta)} if delta else {}
        await client.restrict_chat_member(message.chat.id, target_id, ChatPermissions(), **kw)
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Mute Failed").status("error", "Telegram API error", str(e)))
    await _log_mod_action(message.chat.id, "mute", target_id, label, uid, reason)
    doc = Doc("🔇", "Member Muted").kv_table([
        ("👤 User", f"{label} ({target_id})"),
        ("⏱ Duration", dur_lbl or "Until unmuted"),
        ("📝 Reason", reason or "Not specified"),
    ])
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("unmute") & PLG_FILTER)
async def unmute_cmd(client: Client, message: Message):
    target_id, label, _ = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("🔊", "Unmute Member").status("info", "Usage", "Reply or send /unmute <user>"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Unmute Refused").status("error", "Permission check failed", err))
    try:
        await client.restrict_chat_member(message.chat.id, target_id, _full_send_permissions(message.chat))
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Unmute Failed").status("error", "Telegram API error", str(e)))
    await _log_mod_action(message.chat.id, "unmute", target_id, label, uid)
    doc = Doc("🔊", "Member Unmuted").status("success", f"{label} ({target_id})", "Messaging permissions restored.")
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("warn") & PLG_FILTER)
async def warn_cmd(client: Client, message: Message):
    target_id, label, rest = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("⚠️", "Warn Member").status("info", "Usage", "Reply or send /warn <user> [reason]"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message), target_id=target_id,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Warn Refused").status("error", "Permission check failed", err))

    cfg = await get_group_cfg(message.chat.id)
    limit = max(1, int(cfg.get("warn_limit", 3)))
    reason = " ".join(rest).strip()[:200] or "Rule violation"

    key = f"{message.chat.id}:{target_id}"
    try:
        reasons = await _change_warns(message.chat.id, target_id, reason)
    except (ValueError, RuntimeError) as exc:
        return await rich.reply(message, Doc("⚠️", "Warning Not Added").status("error", "Please review", str(exc)))
    await _log_mod_action(message.chat.id, "warn", target_id, label, uid, reason)
    count = len(reasons)

    if count >= limit:
        claim = {"token": secrets.token_hex(16), "started_ts": time.time()}
        col = vdb.db["group_warns"]
        acquired = await col.update_one(
            {"_id": key, "reasons": reasons, "enforcement": {"$exists": False}},
            {"$set": {"enforcement": claim}},
        )
        if not acquired.matched_count:
            return await rich.reply(message, Doc("⚠️", "Warning Recorded").status("warning", f"{count}/{limit}", "Warnings changed or another enforcement is pending; inspect the member before further action."))
        act = cfg.get("warn_action", "mute")
        parsed = parse_mode(str(act))
        try:
            if parsed is None:
                raise ValueError("invalid warn mode")
            mode, delta, label = parsed
            if mode == "mute":
                # Preserve the legacy 6-hour mute for the plain "mute" action.
                await client.restrict_chat_member(
                    message.chat.id, target_id, ChatPermissions(),
                    until_date=_until_dt(timedelta(hours=6)),
                )
                outcome = "Muted for 6h (warn threshold reached)"
            elif mode == "ban":
                await client.ban_chat_member(message.chat.id, target_id)
                outcome = "Banned (warn threshold reached)"
            elif mode == "kick":
                await client.ban_chat_member(message.chat.id, target_id)
                await client.unban_chat_member(message.chat.id, target_id)
                outcome = "Kicked (warn threshold reached)"
            elif mode == "tban":
                await client.ban_chat_member(message.chat.id, target_id, until_date=_until_dt(delta))
                outcome = f"Temporarily banned ({label}) (warn threshold reached)"
            elif mode == "tmute":
                await client.restrict_chat_member(message.chat.id, target_id, ChatPermissions(),
                                                  until_date=_until_dt(delta))
                outcome = f"Temporarily muted ({label}) (warn threshold reached)"
            else:
                raise ValueError("unsupported warn mode")
        except Exception:
            outcome = "Enforcement failed or was not confirmed; warnings and claim retained. Check Telegram, then use /warnrecover."
        else:
            try:
                cleared = await col.delete_one({"_id": key, "reasons": reasons, "enforcement": claim})
                if not cleared.deleted_count:
                    outcome += "; warnings changed concurrently and were retained. Check Telegram, then use /warnrecover."
            except Exception:
                outcome += "; warning reset failed. Check Telegram, then use /warnrecover."
        # An uncertain response, interrupted task or hard crash leaves the
        # claim for explicit recovery. Never auto-retry an unknown outcome.
        doc = Doc("🚨", "Warn Limit Reached").kv_table([
            ("👤 User", f"{label} ({target_id})"),
            ("⚠️ Warnings", f"{count}/{limit}"),
            ("⚡ Action", outcome),
            ("📝 Latest", reason),
        ])
        return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)

    doc = Doc("⚠️", "Member Warned").kv_table([
        ("👤 User", f"{label} ({target_id})"),
        ("⚠️ Progress", f"{count}/{limit}"),
        ("📝 Reason", reason),
    ])
    kb = InlineKeyboardMarkup([[
        styled_button("↩️ Remove Warn", callback_data=f"plg_gmod:unwarn:{target_id}", style="success"),
    ]])
    return await message.reply_text(doc.classic(), reply_markup=kb, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("unwarn") & PLG_FILTER)
async def unwarn_cmd(client: Client, message: Message):
    target_id, label, _ = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("↩️", "Remove Warn").status("info", "Usage", "Reply or send /unwarn <user>"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))

    try:
        current = await vdb.db["group_warns"].find_one({"_id": f"{message.chat.id}:{target_id}"})
        if not current or not current.get("reasons"):
            return await rich.reply(message, Doc("ℹ️", "No Warnings").status("info", f"{label} ({target_id})", "User has 0 active warnings."))
        reasons = await _change_warns(message.chat.id, target_id, None)
    except (ValueError, RuntimeError) as exc:
        return await rich.reply(message, Doc("⚠️", "Warning Not Removed").status("error", "Please review", str(exc)))
    doc = Doc("✅", "Warning Removed").status("success", f"{label} ({target_id})", f"Remaining warnings: {len(reasons)}")
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("warns") & PLG_FILTER)
async def warns_cmd(client: Client, message: Message):
    target_id, label, _ = await extract_target_and_args(client, message)
    if not target_id and message.from_user:
        target_id, label = message.from_user.id, message.from_user.first_name or str(message.from_user.id)
    if not target_id:
        return await rich.reply(message, Doc("⚠️", "Warnings").status("info", "Usage", "Reply or send /warns <user>"))
    cfg = await get_group_cfg(message.chat.id)
    limit = max(1, int(cfg.get("warn_limit", 3)))
    rec = await vdb.db["group_warns"].find_one({"_id": f"{message.chat.id}:{target_id}"})
    reasons = list((rec or {}).get("reasons") or [])
    doc = Doc("⚠️", f"Warnings · {label}", f"{len(reasons)}/{limit} active")
    doc.progress(len(reasons), limit, label="Threshold")
    if reasons:
        doc.items(reasons, ordered=True)
    else:
        doc.status("success", "Clean record", "No active warnings in this group.")
    return await rich.reply(message, doc)


def _fmt_modlog_entry(entry: dict[str, Any]) -> tuple[str, str, str]:
    ts = entry.get("ts") or 0
    when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M") if ts else "—"
    action = str(entry.get("action") or "action")
    target = entry.get("target_label") or str(entry.get("target_id") or "—")
    reason = entry.get("reason") or ""
    detail = f"{target}"
    if reason:
        detail += f" — {reason}"
    return when, action, detail


@Client.on_message(filters.group & filters.command(["modlog", "modhistory"]) & PLG_FILTER)
async def modlog_cmd(client: Client, message: Message):
    cmd = (message.command or ["modlog"])[0].lower()
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    col = vdb.db["group_modlog"]
    if cmd == "modhistory":
        target_id, label, _ = await extract_target_and_args(client, message)
        if not target_id:
            return await rich.reply(message, Doc("📋", "Mod History").status("info", "Usage", "Reply or send /modhistory <user>"))
        cursor = col.find({"chat_id": message.chat.id, "target_id": target_id}).sort("ts", -1).limit(20)
        entries = await cursor.to_list(length=20)
        doc = Doc("📋", f"Mod History · {label}", message.chat.title or "Group")
        if not entries:
            doc.status("empty", "No recorded actions", "No moderation actions against this user in this group.")
        else:
            doc.table([_fmt_modlog_entry(e) for e in entries], header=("When (UTC)", "Action", "Target"))
        return await rich.reply(message, doc)
    cursor = col.find({"chat_id": message.chat.id}).sort("ts", -1).limit(20)
    entries = await cursor.to_list(length=20)
    doc = Doc("📋", "Moderation Log", message.chat.title or "Group")
    if not entries:
        doc.status("empty", "No recorded actions", "Moderation actions will appear here.")
    else:
        doc.table([_fmt_modlog_entry(e) for e in entries], header=("When (UTC)", "Action", "Target"))
    return await rich.reply(message, doc)


@Client.on_message(filters.group & filters.command("ban") & PLG_FILTER)
async def ban_cmd(client: Client, message: Message):
    return await _do_restriction(client, message, "ban")


@Client.on_message(filters.group & filters.command("tban") & PLG_FILTER)
async def tban_cmd(client: Client, message: Message):
    return await _do_restriction(client, message, "tban")


@Client.on_message(filters.group & filters.command("dban") & PLG_FILTER)
async def dban_cmd(client: Client, message: Message):
    if not message.reply_to_message:
        return await rich.reply(message, Doc("🔒", "Delete + Ban").status("info", "Usage", "Reply to a message with /dban [reason]."))
    return await _do_restriction(client, message, "ban", delete_reply=True)


@Client.on_message(filters.group & filters.command("sban") & PLG_FILTER)
async def sban_cmd(client: Client, message: Message):
    return await _do_restriction(client, message, "ban", silent=True, delete_reply=True)


@Client.on_message(filters.group & filters.command("unban") & PLG_FILTER)
async def unban_cmd(client: Client, message: Message):
    target_id, label, _ = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("✅", "Unban").status("info", "Usage", "Reply or send /unban <user>"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(client, message.chat.id, uid, privilege="can_restrict_members",
                                           is_anon_admin=_is_anon_group_admin(message))
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    try:
        await client.unban_chat_member(message.chat.id, target_id)
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Unban Failed").status("error", "Telegram API error", str(e)))
    return await message.reply_text(Doc("✅", "Member Unbanned").status("success", f"{label} ({target_id})", "May rejoin the group.").classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("tmute") & PLG_FILTER)
async def tmute_cmd(client: Client, message: Message):
    return await _do_restriction(client, message, "tmute")


@Client.on_message(filters.group & filters.command("dmute") & PLG_FILTER)
async def dmute_cmd(client: Client, message: Message):
    if not message.reply_to_message:
        return await rich.reply(message, Doc("🔇", "Delete + Mute").status("info", "Usage", "Reply to a message with /dmute [reason]."))
    return await _do_restriction(client, message, "mute", delete_reply=True)


@Client.on_message(filters.group & filters.command("smute") & PLG_FILTER)
async def smute_cmd(client: Client, message: Message):
    return await _do_restriction(client, message, "mute", silent=True, delete_reply=True)


@Client.on_message(filters.group & filters.command("dkick") & PLG_FILTER)
async def dkick_cmd(client: Client, message: Message):
    if not message.reply_to_message:
        return await rich.reply(message, Doc("👢", "Delete + Kick").status("info", "Usage", "Reply to a message with /dkick [reason]."))
    return await _do_restriction(client, message, "kick", delete_reply=True)


@Client.on_message(filters.group & filters.command("skick") & PLG_FILTER)
async def skick_cmd(client: Client, message: Message):
    return await _do_restriction(client, message, "kick", silent=True, delete_reply=True)


@Client.on_message(filters.group & filters.command(["swarn", "dwarn"]) & PLG_FILTER)
async def swarn_cmd(client: Client, message: Message):
    cmd = (message.command or ["swarn"])[0].lower()
    target_id, label, rest = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("⚠️", "Warn").status("info", "Usage", "Reply or send /swarn <user> [reason]"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(client, message.chat.id, uid, privilege="can_restrict_members",
                                           is_anon_admin=_is_anon_group_admin(message), target_id=target_id)
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    reason = " ".join(rest).strip()[:200] or "Rule violation"
    if cmd == "dwarn" and message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except Exception:
            pass
    try:
        reasons = await _change_warns(message.chat.id, target_id, reason)
    except (ValueError, RuntimeError) as exc:
        return await rich.reply(message, Doc("⚠️", "Warning Not Added").status("error", "Please review", str(exc)))
    if cmd == "swarn":
        try:
            await message.delete()
        except Exception:
            pass
        return None
    cfg = await get_group_cfg(message.chat.id)
    limit = max(1, int(cfg.get("warn_limit", 3)))
    doc = Doc("⚠️", "Member Warned").kv_table([
        ("👤 User", f"{label} ({target_id})"),
        ("⚠️ Progress", f"{len(reasons)}/{limit}"),
        ("📝 Reason", reason),
    ])
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command(["rmwarn", "resetwarn"]) & PLG_FILTER)
async def rmwarn_cmd(client: Client, message: Message):
    cmd = (message.command or ["rmwarn"])[0].lower()
    target_id, label, _ = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(message, Doc("⚠️", "Remove Warnings").status("info", "Usage", f"Reply or send /{cmd} <user>"))
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(client, message.chat.id, uid, privilege="can_restrict_members",
                                           is_anon_admin=_is_anon_group_admin(message), check_bot=False)
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    try:
        if cmd == "resetwarn":
            await vdb.db["group_warns"].delete_one({"_id": f"{message.chat.id}:{target_id}"})
            remaining = []
        else:
            remaining = await _change_warns(message.chat.id, target_id, None)
    except (ValueError, RuntimeError) as exc:
        return await rich.reply(message, Doc("⚠️", "Warnings Not Changed").status("error", "Please review", str(exc)))
    return await message.reply_text(Doc("✅", "Warnings Updated").status("success", f"{label} ({target_id})", f"Remaining warnings: {len(remaining)}").classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("warnings") & PLG_FILTER)
async def warnings_cmd(client: Client, message: Message):
    cfg = await get_group_cfg(message.chat.id)
    doc = Doc("⚠️", "Warning Settings", message.chat.title or "Group").kv_table([
        ("Threshold", str(cfg.get("warn_limit", 3))),
        ("Action", str(cfg.get("warn_action", "mute"))),
        ("Usage", "/setwarnlimit <1–20> · /setwarnmode <kick|ban|tban TIME|mute|tmute TIME>"),
    ])
    return await rich.reply(message, doc)


@Client.on_message(filters.group & filters.command("setwarnlimit") & PLG_FILTER)
async def setwarnlimit_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(client, message.chat.id, uid, privilege="can_restrict_members",
                                           is_anon_admin=_is_anon_group_admin(message), check_bot=False)
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    args = (message.command or [])[1:]
    if len(args) != 1 or not args[0].isascii() or not args[0].isdigit() or not 1 <= int(args[0]) <= 20:
        return await rich.reply(message, Doc("⚠️", "Warn Limit").status("warning", "Invalid limit", "Use /setwarnlimit <1–20>."))
    await update_group_cfg(message.chat.id, {"warn_limit": int(args[0])})
    return await message.reply_text(Doc("⚠️", "Warn Limit Updated").status("success", args[0], "Existing warnings are unchanged.").classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("setwarnmode") & PLG_FILTER)
async def setwarnmode_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(client, message.chat.id, uid, privilege="can_restrict_members",
                                           is_anon_admin=_is_anon_group_admin(message), check_bot=False)
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    raw = " ".join((message.command or [])[1:]).strip()
    parsed = parse_mode(raw)
    if parsed is None or parsed[0] == "nothing":
        return await rich.reply(message, Doc("⚠️", "Warn Mode").status("warning", "Invalid mode", "Use /setwarnmode <kick|ban|tban TIME|mute|tmute TIME>."))
    await update_group_cfg(message.chat.id, {"warn_action": parsed[2]})
    return await message.reply_text(Doc("⚠️", "Warn Mode Updated").status("success", parsed[2], "Enforcement happens on a new warning.").classic(), parse_mode=enums.ParseMode.HTML)


# ───────────────────────── Bot API 10.3: Member Tags, Reactions, Topics & Join-Mode ─────────────────────────
@Client.on_message(filters.group & filters.command(["tag", "deltag"]) & PLG_FILTER)
async def tag_cmd(client: Client, message: Message):
    cmd = (message.command or ["tag"])[0].lower()
    target_id, label, rest = await extract_target_and_args(client, message)
    if not target_id:
        return await rich.reply(
            message,
            Doc("🏷", "Member Tag").status("info", "Usage", "Reply to a member with /tag <title> (<=16 chars) or /deltag."),
        )
    tag_text = None if cmd == "deltag" else (" ".join(rest).strip()[:16] or None)
    if cmd == "tag" and not tag_text:
        return await rich.reply(message, Doc("🏷", "Member Tag").status("warning", "Missing tag text", "Provide 1–16 characters, or use /deltag to clear."))

    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_manage_tags",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Tag Refused").status("error", "Permission check failed", err))

    applied = False
    if botapi.is_main(client) and botapi.enabled():
        applied = await botapi.set_chat_member_tag(message.chat.id, target_id, tag=tag_text)
    if not applied:
        try:
            applied = bool(await client.set_chat_member_tag(message.chat.id, target_id, tag=tag_text))
        except Exception as e:
            return await rich.reply(message, Doc("❌", "Tag Failed").status("error", "Telegram API error", str(e)))

    doc = Doc("🏷", "Member Tag Updated")
    if tag_text:
        doc.status("success", f"{label} → {tag_text}", "Custom Bot API 10.3 chat member tag applied.")
    else:
        doc.status("info", f"{label}", "Member tag cleared.")
    return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command(["clearreactions", "delreactions"]) & PLG_FILTER)
async def clearreactions_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_delete_messages",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))

    args = (message.command or [])[1:]
    rep = message.reply_to_message

    # Mode 1: /clearreactions all <user> (or reply + `all`) → Bot API 10.3 deleteAllMessageReactions across chat
    if args and args[0].lower() == "all":
        target_uid = None
        if rep and rep.from_user:
            target_uid = rep.from_user.id
        elif len(args) >= 2 and args[1].lstrip("-").isdigit():
            target_uid = int(args[1])
        if not target_uid:
            return await rich.reply(message, Doc("🧹", "Purge Chat Reactions").status("info", "Usage", "Reply to a user with /clearreactions all or pass /clearreactions all <user_id>."))
        done = False
        if botapi.is_main(client) and botapi.enabled():
            done = await botapi.delete_all_message_reactions(message.chat.id, user_id=target_uid)
        if not done:
            try:
                done = bool(await client.delete_all_message_reactions(message.chat.id, user_id=target_uid))
            except Exception as e:
                return await rich.reply(message, Doc("❌", "Failed").status("error", "Telegram API error", str(e)))
        doc = Doc("🧹", "Reactions Purged").status("success", f"User {target_uid}", "All reactions by this user were removed across the group.")
        return await rich.reply(message, doc)

    # Mode 2: Reply to a specific message → Bot API 10.3 deleteMessageReaction
    if not rep:
        return await rich.reply(
            message,
            Doc("🧹", "Reaction Moderation").status(
                "info",
                "Usage",
                "Reply to a message with /clearreactions [user_id] or /clearreactions all.",
            ),
        )
    target_uid = int(args[0]) if (args and args[0].lstrip("-").isdigit()) else None
    done = False
    if botapi.is_main(client) and botapi.enabled():
        done = await botapi.delete_message_reaction(message.chat.id, rep.id, user_id=target_uid)
    if not done:
        try:
            done = bool(await client.delete_message_reaction(message.chat.id, rep.id, user_id=target_uid))
        except Exception as e:
            return await rich.reply(message, Doc("❌", "Failed").status("error", "Telegram API error", str(e)))
    scope = f"user {target_uid}" if target_uid else "message"
    return await rich.reply(message, Doc("🧹", "Reactions Cleared").status("success", f"Message #{rep.id}", f"Cleared reactions ({scope})."))


@Client.on_message(filters.group & filters.command("topic") & PLG_FILTER)
async def topic_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_manage_topics",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Topics Refused").status("error", "Permission check failed", err))

    args = (message.command or [])[1:]
    if not args:
        return await rich.reply(message, Doc("💬", "Forum Topics").status("info", "Usage", "/topic new <title> · /topic close · /topic reopen"))

    sub = args[0].lower()
    if sub == "new" and len(args) >= 2:
        title = " ".join(args[1:]).strip()[:120]
        try:
            created = await client.create_forum_topic(message.chat.id, title)
            tid = getattr(created, "id", None) or getattr(created, "message_thread_id", "")
        except Exception as e:
            return await rich.reply(message, Doc("❌", "Topic Error").status("error", "Could not create topic", str(e)))
        return await message.reply_text(
            Doc("💬", "Forum Topic Created").status("success", title, f"Thread ID: {tid}").classic(),
            parse_mode=enums.ParseMode.HTML,
        )

    tid = getattr(message, "message_thread_id", None) or (int(args[1]) if len(args) >= 2 and args[1].isdigit() else None)
    if not tid:
        return await rich.reply(message, Doc("💬", "Forum Topics").status("warning", "Topic ID needed", "Run inside a topic or pass /topic close <thread_id>."))
    try:
        if sub == "close":
            await client.close_forum_topic(message.chat.id, tid)
            return await rich.reply(message, Doc("🔒", "Topic Closed").status("success", f"Topic #{tid}", "Closed successfully."))
        if sub == "reopen":
            await client.reopen_forum_topic(message.chat.id, tid)
            return await rich.reply(message, Doc("🔓", "Topic Reopened").status("success", f"Topic #{tid}", "Reopened successfully."))
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Topic Error").status("error", "Telegram API error", str(e)))
    return await rich.reply(message, Doc("💬", "Forum Topics").status("info", "Usage", "/topic new <title> · /topic close · /topic reopen"))


@Client.on_message(filters.group & filters.command("joinmode") & PLG_FILTER)
async def joinmode_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_invite_users",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Join Mode").status("error", "Permission check failed", err))
    args = [x.lower() for x in (message.command or [])[1:]]
    cfg = await get_group_cfg(message.chat.id)
    if not args:
        legacy = cfg.get("joinmode") == "auto" and _join_mode(cfg) != "auto"
        state = "Previous AUTO setting inactive; confirm again" if legacy else _join_mode(cfg).upper()
        return await rich.reply(message, Doc("🚪", "Join-Request Mode").status("info", state, "Use /joinmode off, /joinmode notify, or /joinmode auto confirm. Auto approves every request without review."))
    if args not in (["off"], ["notify"], ["auto", "confirm"]):
        return await rich.reply(message, Doc("🚪", "Join-Request Mode").status("warning", "Confirmation required or invalid command", "Use /joinmode off, /joinmode notify, or /joinmode auto confirm. Auto approves every request without review."))
    mode = args[0]
    if mode == "auto" and cfg.get("antiraid_until", 0) > time.time():
        return await rich.reply(message, Doc("🚪", "Join-Request Mode").status("warning", "Anti-raid needs review", "Turn off the active or unconfirmed anti-raid setting with /antiraid off before enabling auto approval."))
    if mode != "off":
        ok, err = await verify_bot_privilege(client, message.chat.id, "can_invite_users")
        if not ok:
            return await rich.reply(message, Doc("🔒", "Join Mode").status("error", "Bot permission check failed", err))
    await update_group_cfg(message.chat.id, {"joinmode": mode, "joinmode_auto_confirmed": mode == "auto"})
    detail = "Every join request will be approved without review when Telegram delivers it." if mode == "auto" else "New requests will be sent for review when delivered." if mode == "notify" else "Join-request automation is disabled."
    return await rich.reply(message, Doc("🚪", "Join-Request Mode Updated").status("success", mode.upper(), detail))


# ───────────────────────── Welcome, Goodbye, Locks, Filters, Raid & Report ─────────────────────────
@Client.on_message(filters.group & filters.command(["welcome", "goodbye"]) & PLG_FILTER)
async def greeting_cmd(client: Client, message: Message):
    cmd = (message.command or ["welcome"])[0].lower()
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))

    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    on_key = f"{cmd}_on"
    txt_key = f"{cmd}_text"
    if not arg:
        cfg = await get_group_cfg(message.chat.id)
        doc = Doc("👋", f"{cmd.title()} Settings").kv_table([
            ("State", "🟢 Enabled" if cfg.get(on_key) else "🔴 Disabled"),
            ("Template", cfg.get(txt_key) or "Default"),
            ("Placeholders", "{mention} · {first} · {chat} · {id}"),
        ])
        return await rich.reply(message, doc)
    if arg.lower() in ("on", "enable", "true"):
        await update_group_cfg(message.chat.id, {on_key: True})
        return await rich.reply(message, Doc("👋", cmd.title()).status("success", "Enabled", f"{cmd.title()} messages are now active."))
    if arg.lower() in ("off", "disable", "false"):
        await update_group_cfg(message.chat.id, {on_key: False})
        return await rich.reply(message, Doc("👋", cmd.title()).status("info", "Disabled", f"{cmd.title()} messages turned off."))

    await update_group_cfg(message.chat.id, {on_key: True, txt_key: arg[:1000]})
    return await rich.reply(message, Doc("👋", f"{cmd.title()} Updated").status("success", "Template saved & enabled", arg[:120]))


@Client.on_message(filters.group & filters.command(["setwelcome", "resetwelcome"]) & PLG_FILTER)
async def setwelcome_cmd(client: Client, message: Message):
    cmd = (message.command or ["setwelcome"])[0].lower()
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    if cmd == "resetwelcome":
        await update_group_cfg(message.chat.id, {"welcome_text": "👋 Welcome {mention} to <b>{chat}</b>!"})
        return await rich.reply(message, Doc("👋", "Welcome Reset").status("success", "Default template restored", "Placeholders: {mention} · {first} · {chat} · {id}"))
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        return await rich.reply(message, Doc("👋", "Set Welcome").status("info", "Usage", "/setwelcome <message> — placeholders {mention} {first} {chat} {id}"))
    await update_group_cfg(message.chat.id, {"welcome_on": True, "welcome_text": arg[:1000]})
    return await rich.reply(message, Doc("👋", "Welcome Updated").status("success", "Template saved & enabled", arg[:120]))


@Client.on_message(filters.group & filters.command(["setgoodbye", "resetgoodbye", "cleanwelcome", "cleanservice"]) & PLG_FILTER)
async def greeting_options_cmd(client: Client, message: Message):
    cmd = (message.command or ["setgoodbye"])[0].lower()
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    cfg = await get_group_cfg(message.chat.id)
    args = (message.command or [])[1:]
    if cmd in ("cleanwelcome", "cleanservice"):
        key = cmd
        if not args:
            state = "on" if cfg.get(key) else "off"
            return await rich.reply(message, Doc("🧹", cmd.title()).status("info", state, f"Use /{cmd} on or /{cmd} off."))
        enabled = args[0].lower() in ("on", "yes", "true")
        await update_group_cfg(message.chat.id, {key: enabled})
        detail = "Previous welcome messages will be deleted when a new one is sent." if key == "cleanwelcome" else "Join/leave and other service messages will be deleted."
        return await rich.reply(message, Doc("🧹", cmd.title()).status("success", "Enabled" if enabled else "Disabled", detail))
    if cmd == "resetgoodbye":
        await update_group_cfg(message.chat.id, {"goodbye_text": "👋 Goodbye, {first}!"})
        return await rich.reply(message, Doc("👋", "Goodbye Reset").status("success", "Default template restored", "Placeholders: {mention} · {first} · {chat} · {id}"))
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        return await rich.reply(message, Doc("👋", "Set Goodbye").status("info", "Usage", "/setgoodbye <message> — placeholders {mention} {first} {chat} {id}"))
    await update_group_cfg(message.chat.id, {"goodbye_on": True, "goodbye_text": arg[:1000]})
    return await rich.reply(message, Doc("👋", "Goodbye Updated").status("success", "Template saved & enabled", arg[:120]))


_EXPORT_FIELDS = (
    "welcome_on", "welcome_text", "goodbye_on", "goodbye_text", "rules",
    "warn_limit", "warn_action", "locks", "lockwarns", "lock_modes",
    "filters", "blocklist", "blocklist_mode", "blocklist_delete", "notes",
    "antiflood", "flood_limit", "flood_window", "flood_mode", "clearflood",
    "captcha_on", "captcha_text", "url_whitelist", "cleanwelcome", "cleanservice", "disabled_commands",
)


def _sanitize_import(data: Any) -> dict[str, Any] | None:
    """Validate an imported settings payload; return None when unusable."""
    if not isinstance(data, dict):
        return None
    out: dict[str, Any] = {}
    defaults = _default_cfg(0)
    for key in _EXPORT_FIELDS:
        if key not in data:
            continue
        value = data[key]
        default = defaults[key]
        if isinstance(default, bool):
            if not isinstance(value, bool):
                return None
            out[key] = value
        elif isinstance(default, (int, float)) and not isinstance(default, bool):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            out[key] = value
        elif isinstance(default, str):
            if not isinstance(value, str):
                return None
            out[key] = value[:2000]
        elif isinstance(default, list):
            if not isinstance(value, list) or len(value) > 100 or not all(isinstance(v, str) for v in value):
                return None
            out[key] = [v[:100] for v in value]
        elif isinstance(default, dict):
            if not isinstance(value, dict) or len(value) > 200:
                return None
            clean: dict[str, Any] = {}
            for k, v in value.items():
                if not isinstance(k, str):
                    return None
                if isinstance(v, str):
                    clean[k[:100]] = v[:1000]
                elif isinstance(v, dict) and all(isinstance(x, str) for x in v.values()):
                    clean[k[:100]] = {str(x)[:50]: str(y)[:200] for x, y in v.items()}
                else:
                    return None
            out[key] = clean
    # Re-validate semantic fields so a hostile payload cannot smuggle bad state.
    if "locks" in out:
        out["locks"] = [lock for lock in out["locks"] if lock in VALID_LOCKS]
    if "disabled_commands" in out:
        out["disabled_commands"] = sorted({c.lower() for c in out["disabled_commands"] if re.fullmatch(r"[a-z][a-z0-9_]{0,31}", c.lower())} - _PROTECTED_COMMANDS)
    if "warn_limit" in out:
        out["warn_limit"] = max(1, min(20, int(out["warn_limit"])))
    if "flood_limit" in out:
        out["flood_limit"] = max(2, min(50, int(out["flood_limit"])))
    if "flood_window" in out:
        out["flood_window"] = max(1.0, min(300.0, float(out["flood_window"])))
    return out


@Client.on_message(filters.group & filters.command(["export", "import"]) & PLG_FILTER)
async def group_settings_transfer_cmd(client: Client, message: Message):
    cmd = (message.command or ["export"])[0].lower()
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    cfg = await get_group_cfg(message.chat.id)
    if cmd == "export":
        payload = {key: cfg.get(key) for key in _EXPORT_FIELDS}
        text = json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
        return await rich.reply(message, Doc("📤", "Group Settings Export").text(f"<pre>{rich.esc(text)}</pre>"))
    raw = ""
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) > 1:
        raw = parts[1]
    elif message.reply_to_message is not None:
        raw = message.reply_to_message.text or message.reply_to_message.caption or ""
    raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not raw or len(raw) > 60000:
        return await rich.reply(message, Doc("📥", "Import Settings").status("warning", "No valid payload", "Reply to an exported settings message with /import, or paste the JSON after /import."))
    try:
        data = json.loads(raw)
    except Exception:
        return await rich.reply(message, Doc("📥", "Import Settings").status("error", "Invalid JSON", "The supplied settings could not be parsed; nothing was changed."))
    updates = _sanitize_import(data)
    if updates is None:
        return await rich.reply(message, Doc("📥", "Import Settings").status("error", "Unsafe or invalid settings", "The payload failed validation; nothing was changed."))
    await update_group_cfg(message.chat.id, updates)
    return await rich.reply(message, Doc("📥", "Settings Imported").status("success", f"{len(updates)} sections applied", "Review /gsettings to confirm the imported configuration."))


@Client.on_message(filters.group & filters.command(["lock", "unlock", "locks", "locktypes", "lockwarns", "lockall", "unlockall"]) & PLG_FILTER)
async def lock_cmd(client: Client, message: Message):
    cmd = (message.command or ["lock"])[0].lower()
    cfg = await get_group_cfg(message.chat.id)
    if cmd in ("lockall", "unlockall"):
        uid = message.from_user.id if message.from_user else None
        ok, err = await verify_mod_permissions(
            client, message.chat.id, uid, privilege="can_delete_messages",
            is_anon_admin=_is_anon_group_admin(message),
        )
        if not ok:
            return await rich.reply(message, Doc("🔒", "Locks Refused").status("error", "Permission check failed", err))
        if cmd == "lockall":
            await update_group_cfg(message.chat.id, {"locks": ["all"], "lock_modes": {}})
            return await message.reply_text(Doc("🔒", "All Content Locked").status("success", "All", "Only admins can send messages.").classic(), parse_mode=enums.ParseMode.HTML)
        await update_group_cfg(message.chat.id, {"locks": [], "lock_modes": {}})
        return await message.reply_text(Doc("🔓", "All Content Unlocked").status("success", "None", "All members can send messages.").classic(), parse_mode=enums.ParseMode.HTML)
    if cmd == "locks":
        args = [a.lower() for a in (message.command or [])[1:]]
        cur = cfg.get("locks") or []
        if args == ["list"]:
            doc = Doc("🔒", "All Lock Types", message.chat.title or "Group")
            doc.table([(t, "🟢 Locked" if t in cur else "🔴 Open") for t in VALID_LOCKS], header=("Type", "State"))
            return await rich.reply(message, doc)
        doc = Doc("🔒", "Active Locks", message.chat.title or "Group")
        doc.status("info", ", ".join(cur) if cur else "None", "Use /locks list for all types. /locktypes lists available types.")
        return await rich.reply(message, doc)
    if cmd == "locktypes":
        doc = Doc("🔒", "Lock Types", message.chat.title or "Group")
        doc.table([(t,) for t in VALID_LOCKS], header=("Type",))
        return await rich.reply(message, doc)
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_delete_messages",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Locks Refused").status("error", "Permission check failed", err))
    if cmd == "lockwarns":
        args = [a.lower() for a in (message.command or [])[1:]]
        if len(args) != 1 or args[0] not in ("on", "off", "yes", "no", "true", "false"):
            return await rich.reply(message, Doc("🔒", "Lock Warnings").status("info", "Usage", "/lockwarns on|off"))
        enabled = args[0] in ("on", "yes", "true")
        await update_group_cfg(message.chat.id, {"lockwarns": enabled})
        return await message.reply_text(Doc("🔒", "Lock Warnings").status("success", "On" if enabled else "Off", "Locked content will now warn users." if enabled else "Locked content will be deleted silently.").classic(), parse_mode=enums.ParseMode.HTML)

    raw = " ".join((message.command or [])[1:]).strip()
    # Custom lock mode: /lock <types> ### <reason> {mode [time]}
    custom_mode = None
    custom_reason = ""
    if cmd == "lock" and "###" in raw:
        before, _, after = raw.partition("###")
        m = re.search(r"\{([^}]+)\}\s*$", after.strip())
        if not m:
            return await rich.reply(message, Doc("🔒", "Custom Lock Mode").status("warning", "Invalid mode", "Use /lock <types> ### <reason> {kick|ban|tban TIME|mute|tmute TIME}."))
        parsed = parse_mode(m.group(1).strip())
        if parsed is None or parsed[0] == "nothing":
            return await rich.reply(message, Doc("🔒", "Custom Lock Mode").status("warning", "Invalid mode", "Use kick, ban, tban TIME, mute or tmute TIME."))
        custom_mode = parsed[2]
        custom_reason = after[:m.start()].strip()[:200]
        raw = before.strip()
    args = [a.lower() for a in raw.split() if a]
    if not args or any(a not in VALID_LOCKS for a in args):
        cur = ", ".join(cfg.get("locks") or []) or "None"
        return await rich.reply(
            message,
            Doc("🔒", "Content Locks").status("info", f"Active: {cur}", f"Options: {', '.join(VALID_LOCKS)}"),
        )
    locks = set(cfg.get("locks") or [])
    lock_modes = dict(cfg.get("lock_modes") or {})
    if cmd == "lock":
        locks.update(args)
        if custom_mode:
            for t in args:
                lock_modes[t] = {"mode": custom_mode, "reason": custom_reason}
    else:
        locks.difference_update(args)
        for t in args:
            lock_modes.pop(t, None)
    updated = sorted(locks)
    await update_group_cfg(message.chat.id, {"locks": updated, "lock_modes": lock_modes})
    detail = f"Active locks: {', '.join(updated) or 'None'}"
    if custom_mode:
        detail += f" · mode {custom_mode} for {', '.join(args)}"
    return await message.reply_text(
        Doc("🔒", f"Content {cmd.title()}ed").status("success", ", ".join(args), detail).classic(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.group & filters.command(["whitelist", "rmwhitelist", "allowlist", "rmallowlist"]) & PLG_FILTER)
async def whitelist_cmd(client: Client, message: Message):
    cmd = (message.command or ["whitelist"])[0].lower()
    cfg = await get_group_cfg(message.chat.id)
    current = sorted({_normalise_domain(item) for item in (cfg.get("url_whitelist") or []) if _normalise_domain(item)})
    args = [_normalise_domain(item) for item in (message.command or [])[1:]]
    args = [item for item in args if item]
    if cmd in ("whitelist", "allowlist") and not args:
        doc = Doc("✅", "URL Whitelist", message.chat.title or "Group")
        doc.status("info", ", ".join(current) if current else "None", "Whitelisted domains are exempt from links/url locks. Add domains with /whitelist example.com.")
        return await rich.reply(message, doc)

    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_delete_messages",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Whitelist Refused").status("error", "Permission check failed", err))
    if not args:
        return await rich.reply(message, Doc("✅", "URL Whitelist").status("warning", "No domains supplied", "Example: /whitelist example.com docs.example.com"))
    if cmd in ("whitelist", "allowlist"):
        updated = sorted(set(current) | set(args))
        verb = "Added to whitelist"
    else:
        updated = sorted(set(current) - set(args))
        verb = "Removed from whitelist"
    await update_group_cfg(message.chat.id, {"url_whitelist": updated})
    return await message.reply_text(
        Doc("✅", verb).status("success", ", ".join(args), f"Active: {', '.join(updated) or 'None'}").classic(),
        parse_mode=enums.ParseMode.HTML,
    )


async def _collect_chat_administrators(client: Client, chat_id: int) -> list[Any]:
    """Collect administrators across WzGram's list/async-iterator variants."""
    result = client.get_chat_administrators(chat_id)
    if hasattr(result, "__aiter__"):
        return [member async for member in result]
    if hasattr(result, "__await__"):
        result = await result
    if hasattr(result, "__aiter__"):
        return [member async for member in result]
    return list(result or [])


@Client.on_message(filters.group & filters.command(["adminlist", "adminlists", "adminslist"]) & PLG_FILTER)
async def adminlist_cmd(client: Client, message: Message):
    try:
        members = await _collect_chat_administrators(client, message.chat.id)
    except Exception as exc:
        return await rich.reply(message, Doc("👮", "Administrators").status("error", "Could not load administrators", str(exc)[:160]))
    rows: list[tuple[str, str]] = []
    for member in members:
        user = getattr(member, "user", None)
        if user is None:
            continue
        name = getattr(user, "first_name", None) or str(getattr(user, "id", "Admin"))
        username = getattr(user, "username", None)
        label = f"@{username}" if username else f"ID {getattr(user, 'id', '')}"
        status = getattr(member, "status", "")
        role = "Owner" if status == enums.ChatMemberStatus.OWNER else "Admin"
        rows.append((f"{role} · {name}", label))
    doc = Doc("👮", f"Administrators · {len(rows)}", message.chat.title or "Group")
    if not rows:
        doc.status("empty", "No administrators returned", "Telegram did not provide an administrator list for this chat.")
    else:
        doc.table(rows, header=("Administrator", "Contact"))
    return await rich.reply(message, doc)


@Client.on_message(filters.group & filters.command(["gfilter", "delfilter", "gfilters", "filter", "stop", "filters", "stopall"]) & PLG_FILTER)
async def gfilter_cmd(client: Client, message: Message):
    cmd = (message.command or ["gfilters"])[0].lower()
    if cmd == "stopall":
        uid = message.from_user.id if message.from_user else None
        ok, err = await verify_mod_permissions(
            client, message.chat.id, uid, privilege="can_change_info",
            is_anon_admin=_is_anon_group_admin(message), check_bot=False,
        )
        if not ok:
            return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
        cfg = await get_group_cfg(message.chat.id)
        count = len(cfg.get("filters") or {})
        await update_group_cfg(message.chat.id, {"filters": {}})
        return await message.reply_text(Doc("🗑", "All Filters Removed").status("success", f"{count} filters deleted", "Keyword filters cleared.").classic(), parse_mode=enums.ParseMode.HTML)
    if cmd in ("gfilters", "filters"):
        cfg = await get_group_cfg(message.chat.id)
        fmap: dict[str, str] = dict(cfg.get("filters") or {})
        doc = Doc("💬", f"Group Filters · {len(fmap)}")
        if not fmap:
            doc.status("empty", "No keyword filters", "Admins can add one with /filter <word|\"phrase\"> <reply>.")
        else:
            doc.table([(rich.code(k), v[:60]) for k, v in sorted(fmap.items())], header=("Keyword", "Reply"))
        return await rich.reply(message, doc)

    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))

    cfg = await get_group_cfg(message.chat.id)
    fmap: dict[str, str] = dict(cfg.get("filters") or {})
    if cmd in ("delfilter", "stop"):
        raw = " ".join((message.command or [])[1:]).strip()
        triggers = _split_trigger_list(raw)
        if not triggers:
            return await rich.reply(message, Doc("💬", "Delete Filter").status("info", "Usage", "/stop <word|\"phrase\">"))
        removed = []
        for t in triggers:
            trig = t.lower()
            if trig in fmap:
                fmap.pop(trig, None)
                removed.append(trig)
        if not removed:
            return await rich.reply(message, Doc("💬", "Not Found").status("warning", triggers[0], "No such filter in this group."))
        await update_group_cfg(message.chat.id, {"filters": fmap})
        return await rich.reply(message, Doc("🗑", "Filter Removed").status("success", ", ".join(removed), "Keyword filter deleted."))

    # /filter <trigger> <reply> — trigger may be a word, "phrase", (a, b, "c d"),
    # prefix:<text> or exact:<text>.
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await rich.reply(message, Doc("💬", "Add Filter").status("info", "Usage", "/filter <word|\"phrase\"> <reply text>"))
    rest = parts[1].strip()
    # Find the end of the trigger (quoted phrase, parenthesized list, or first word).
    if rest.startswith("("):
        depth = 0
        end = -1
        in_q = False
        for i, ch in enumerate(rest):
            if ch == '"':
                in_q = not in_q
            elif not in_q and ch == "(":
                depth += 1
            elif not in_q and ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return await rich.reply(message, Doc("💬", "Add Filter").status("warning", "Invalid trigger list", "Use /filter (a, b, \"c d\") <reply>."))
        trig_raw, reply_val = rest[:end + 1], rest[end + 1:].strip()
    elif rest.startswith('"'):
        end = rest.find('"', 1)
        if end < 0:
            return await rich.reply(message, Doc("💬", "Add Filter").status("warning", "Unclosed quote", "Use /filter \"phrase\" <reply>."))
        trig_raw, reply_val = rest[:end + 1], rest[end + 1:].strip()
    else:
        bits = rest.split(maxsplit=1)
        trig_raw = bits[0]
        reply_val = bits[1].strip() if len(bits) > 1 else ""
    if not reply_val:
        return await rich.reply(message, Doc("💬", "Add Filter").status("info", "Usage", "/filter <word|\"phrase\"> <reply text>"))
    triggers = _split_trigger_list(trig_raw)
    if not triggers:
        return await rich.reply(message, Doc("💬", "Add Filter").status("warning", "Invalid trigger", "Provide a word, \"phrase\", or (a, b) list."))
    new_trigs = [t.lower()[:64] for t in triggers if t.strip()]
    if len(fmap) + len([t for t in new_trigs if t not in fmap]) > 60:
        return await rich.reply(message, Doc("⚠️", "Limit Reached").status("warning", "60 filters max", "Delete unused filters first."))
    for trig in new_trigs:
        fmap[trig] = reply_val[:1000]
    await update_group_cfg(message.chat.id, {"filters": fmap})
    return await rich.reply(message, Doc("✅", "Filter Saved").status("success", ", ".join(new_trigs), reply_val[:80]))


@Client.on_message(filters.group & filters.command(["addblocklist", "rmblocklist", "blocklist", "blocklistmode", "blocklistdelete"]) & PLG_FILTER)
async def blocklist_cmd(client: Client, message: Message):
    cmd = (message.command or ["blocklist"])[0].lower()
    cfg = await get_group_cfg(message.chat.id)
    bmap: dict[str, str] = dict(cfg.get("blocklist") or {})
    if cmd == "blocklist":
        doc = Doc("🚫", f"Blocklist · {len(bmap)}", message.chat.title or "Group")
        if not bmap:
            doc.status("empty", "No blocklist triggers", "Admins can add one with /addblocklist <word|\"phrase\"> <reason>.")
        else:
            doc.table([(rich.code(k), v[:60]) for k, v in sorted(bmap.items())], header=("Trigger", "Reason"))
        return await rich.reply(message, doc)
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    if cmd == "blocklistmode":
        raw = " ".join((message.command or [])[1:]).strip()
        parsed = parse_mode(raw)
        if parsed is None:
            return await rich.reply(message, Doc("🚫", "Blocklist Mode").status("warning", "Invalid mode", "Use /blocklistmode <nothing|warn|kick|ban|tban TIME|mute|tmute TIME>."))
        await update_group_cfg(message.chat.id, {"blocklist_mode": parsed[2]})
        return await message.reply_text(Doc("🚫", "Blocklist Mode Updated").status("success", parsed[2], "Applies when a blocklist trigger matches.").classic(), parse_mode=enums.ParseMode.HTML)
    if cmd == "blocklistdelete":
        args = [a.lower() for a in (message.command or [])[1:]]
        if len(args) != 1 or args[0] not in ("on", "off", "yes", "no", "true", "false"):
            return await rich.reply(message, Doc("🚫", "Blocklist Delete").status("info", "Usage", "/blocklistdelete on|off"))
        enabled = args[0] in ("on", "yes", "true")
        await update_group_cfg(message.chat.id, {"blocklist_delete": enabled})
        return await message.reply_text(Doc("🚫", "Blocklist Delete").status("success", "On" if enabled else "Off", "Blocklisted messages will be deleted." if enabled else "Blocklisted messages will be kept.").classic(), parse_mode=enums.ParseMode.HTML)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        return await rich.reply(message, Doc("🚫", "Blocklist").status("info", "Usage", "/addblocklist <word|\"phrase\"> <reason>"))
    rest = parts[1].strip()
    if cmd == "rmblocklist":
        triggers = _split_trigger_list(rest)
        if not triggers:
            return await rich.reply(message, Doc("🚫", "Remove Blocklist").status("info", "Usage", "/rmblocklist <word|\"phrase\">"))
        removed = []
        for t in triggers:
            trig = t.lower()
            if trig in bmap:
                bmap.pop(trig, None)
                removed.append(trig)
        if not removed:
            return await rich.reply(message, Doc("🚫", "Not Found").status("warning", triggers[0], "No such blocklist trigger."))
        await update_group_cfg(message.chat.id, {"blocklist": bmap})
        return await rich.reply(message, Doc("🗑", "Blocklist Removed").status("success", ", ".join(removed), "Trigger deleted."))
    # /addblocklist <trigger> <reason>
    if rest.startswith("("):
        depth, end, in_q = 0, -1, False
        for i, ch in enumerate(rest):
            if ch == '"':
                in_q = not in_q
            elif not in_q and ch == "(":
                depth += 1
            elif not in_q and ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return await rich.reply(message, Doc("🚫", "Add Blocklist").status("warning", "Invalid trigger list", "Use /addblocklist (a, b, \"c d\") <reason>."))
        trig_raw, reason = rest[:end + 1], rest[end + 1:].strip()
    elif rest.startswith('"'):
        end = rest.find('"', 1)
        if end < 0:
            return await rich.reply(message, Doc("🚫", "Add Blocklist").status("warning", "Unclosed quote", "Use /addblocklist \"phrase\" <reason>."))
        trig_raw, reason = rest[:end + 1], rest[end + 1:].strip()
    else:
        bits = rest.split(maxsplit=1)
        trig_raw = bits[0]
        reason = bits[1].strip() if len(bits) > 1 else ""
    if not reason:
        reason = "Blocklist match"
    triggers = _split_trigger_list(trig_raw)
    if not triggers:
        return await rich.reply(message, Doc("🚫", "Add Blocklist").status("warning", "Invalid trigger", "Provide a word, \"phrase\", or (a, b) list."))
    new_trigs = [t.lower()[:64] for t in triggers if t.strip()]
    if len(bmap) + len([t for t in new_trigs if t not in bmap]) > 100:
        return await rich.reply(message, Doc("⚠️", "Limit Reached").status("warning", "100 blocklist triggers max", "Remove unused triggers first."))
    for trig in new_trigs:
        bmap[trig] = reason[:500]
    await update_group_cfg(message.chat.id, {"blocklist": bmap})
    return await rich.reply(message, Doc("✅", "Blocklist Saved").status("success", ", ".join(new_trigs), reason[:80]))


@Client.on_message(filters.group & filters.command(["save", "get", "notes", "clear"]) & PLG_FILTER)
async def notes_cmd(client: Client, message: Message):
    cmd = (message.command or ["notes"])[0].lower()
    cfg = await get_group_cfg(message.chat.id)
    nmap: dict[str, str] = dict(cfg.get("notes") or {})
    if cmd == "notes":
        doc = Doc("📝", f"Notes · {len(nmap)}", message.chat.title or "Group")
        if not nmap:
            doc.status("empty", "No notes", "Admins can save one with /save <trigger> <text>. Open with /get <trigger> or #<trigger>.")
        else:
            doc.table([(rich.code(k), v[:60]) for k, v in sorted(nmap.items())], header=("Trigger", "Note"))
        return await rich.reply(message, doc)
    if cmd == "get":
        args = (message.command or [])[1:]
        if not args:
            return await rich.reply(message, Doc("📝", "Open Note").status("info", "Usage", "/get <trigger>"))
        trig = args[0].lower().lstrip("#")
        if trig not in nmap:
            return await rich.reply(message, Doc("📝", "Not Found").status("warning", trig, "No such note in this group."))
        formatted = _format_greeting(nmap[trig], message.from_user, message.chat.title or "")
        return await message.reply_text(formatted, parse_mode=enums.ParseMode.HTML)
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    if cmd == "clear":
        args = (message.command or [])[1:]
        if not args:
            return await rich.reply(message, Doc("📝", "Delete Note").status("info", "Usage", "/clear <trigger>"))
        trig = args[0].lower().lstrip("#")
        if trig not in nmap:
            return await rich.reply(message, Doc("📝", "Not Found").status("warning", trig, "No such note in this group."))
        nmap.pop(trig, None)
        await update_group_cfg(message.chat.id, {"notes": nmap})
        return await rich.reply(message, Doc("🗑", "Note Deleted").status("success", trig, "Note removed."))
    # /save <trigger> <text> (text notes; attachment notes are not stored)
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        return await rich.reply(message, Doc("📝", "Save Note").status("info", "Usage", "/save <trigger> <text>"))
    trig = parts[1].lower().lstrip("#")[:32]
    if not re.fullmatch(r"[a-z0-9_]+", trig):
        return await rich.reply(message, Doc("📝", "Save Note").status("warning", "Invalid trigger", "Use a single word with letters, digits or underscore."))
    if len(nmap) >= 100 and trig not in nmap:
        return await rich.reply(message, Doc("⚠️", "Limit Reached").status("warning", "100 notes max", "Delete unused notes first."))
    nmap[trig] = parts[2].strip()[:1000]
    await update_group_cfg(message.chat.id, {"notes": nmap})
    return await rich.reply(message, Doc("✅", "Note Saved").status("success", trig, parts[2].strip()[:80]))


@Client.on_message(filters.group & filters.command(["setflood", "setfloodtimer", "setfloodmode", "flood", "clearflood"]) & PLG_FILTER)
async def flood_cmd(client: Client, message: Message):
    cmd = (message.command or ["flood"])[0].lower()
    cfg = await get_group_cfg(message.chat.id)
    if cmd == "flood":
        doc = Doc("🌊", "Anti-Flood", message.chat.title or "Group").kv_table([
            ("State", "🟢 Enabled" if cfg.get("antiflood") else "🔴 Off"),
            ("Limit", f"{cfg.get('flood_limit', 7)} messages"),
            ("Window", f"{cfg.get('flood_window', 8.0)} seconds"),
            ("Mode", str(cfg.get("flood_mode", "tmute 5m"))),
            ("Clear flood", "🟢 On" if cfg.get("clearflood") else "🔴 Off"),
            ("Usage", "/setflood <number> · /setfloodtimer <number> <seconds> · /setfloodmode <action> · /clearflood on|off"),
        ])
        return await rich.reply(message, doc)
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    args = (message.command or [])[1:]
    if cmd == "setflood":
        if len(args) != 1 or not args[0].isascii() or not args[0].isdigit() or not 2 <= int(args[0]) <= 50:
            return await rich.reply(message, Doc("🌊", "Anti-Flood").status("warning", "Invalid limit", "Use /setflood <2–50>."))
        await update_group_cfg(message.chat.id, {"flood_limit": int(args[0]), "antiflood": True})
        return await message.reply_text(Doc("🌊", "Anti-Flood Updated").status("success", f"{args[0]} messages", "Anti-flood enabled.").classic(), parse_mode=enums.ParseMode.HTML)
    if cmd == "setfloodtimer":
        if len(args) != 2 or not args[0].isascii() or not args[0].isdigit() or not 2 <= int(args[0]) <= 50:
            return await rich.reply(message, Doc("🌊", "Anti-Flood Timer").status("warning", "Invalid timer", "Use /setfloodtimer <2–50> <seconds>."))
        try:
            secs = float(args[1].rstrip("s"))
        except ValueError:
            return await rich.reply(message, Doc("🌊", "Anti-Flood Timer").status("warning", "Invalid window", "Use seconds like 10 or 10s."))
        if not 1 <= secs <= 300:
            return await rich.reply(message, Doc("🌊", "Anti-Flood Timer").status("warning", "Invalid window", "Use 1–300 seconds."))
        await update_group_cfg(message.chat.id, {"flood_limit": int(args[0]), "flood_window": secs, "antiflood": True})
        return await message.reply_text(Doc("🌊", "Anti-Flood Timer Updated").status("success", f"{args[0]} / {secs:g}s", "Anti-flood enabled.").classic(), parse_mode=enums.ParseMode.HTML)
    if cmd == "setfloodmode":
        parsed = parse_mode(" ".join(args))
        if parsed is None or parsed[0] == "nothing":
            return await rich.reply(message, Doc("🌊", "Anti-Flood Mode").status("warning", "Invalid mode", "Use /setfloodmode <kick|ban|tban TIME|mute|tmute TIME>."))
        await update_group_cfg(message.chat.id, {"flood_mode": parsed[2]})
        return await message.reply_text(Doc("🌊", "Anti-Flood Mode Updated").status("success", parsed[2], "Applies when the flood limit is exceeded.").classic(), parse_mode=enums.ParseMode.HTML)
    # /clearflood on|off
    if len(args) != 1 or args[0].lower() not in ("on", "off", "yes", "no", "true", "false"):
        return await rich.reply(message, Doc("🌊", "Clear Flood").status("info", "Usage", "/clearflood on|off"))
    enabled = args[0].lower() in ("on", "yes", "true")
    await update_group_cfg(message.chat.id, {"clearflood": enabled})
    return await message.reply_text(Doc("🌊", "Clear Flood").status("success", "On" if enabled else "Off", "Flood messages will be fully deleted." if enabled else "Only the triggering message is handled.").classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command(["captcha", "captchamode", "setcaptchatext", "resetcaptchatext"]) & PLG_FILTER)
async def captcha_cmd(client: Client, message: Message):
    cmd = (message.command or ["captcha"])[0].lower()
    cfg = await get_group_cfg(message.chat.id)
    if cmd == "captcha" and not (message.command or [])[1:]:
        doc = Doc("🤖", "CAPTCHA", message.chat.title or "Group").kv_table([
            ("State", "🟢 Button" if cfg.get("captcha_on") else "🔴 Off"),
            ("Button text", str(cfg.get("captcha_text") or "Click here to prove you're human")),
            ("Requires", "Welcome messages enabled"),
            ("Usage", "/captcha on|off · /captchamode button · /setcaptchatext <text>"),
        ])
        return await rich.reply(message, doc)
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    if cmd == "captcha":
        args = [a.lower() for a in (message.command or [])[1:]]
        if len(args) != 1 or args[0] not in ("on", "off", "yes", "no", "true", "false"):
            return await rich.reply(message, Doc("🤖", "CAPTCHA").status("info", "Usage", "/captcha on|off"))
        enabled = args[0] in ("on", "yes", "true")
        if enabled and not cfg.get("welcome_on"):
            return await rich.reply(message, Doc("🤖", "CAPTCHA").status("warning", "Welcome required", "Enable welcomes first with /welcome on."))
        await update_group_cfg(message.chat.id, {"captcha_on": enabled})
        return await message.reply_text(Doc("🤖", "CAPTCHA").status("success", "On" if enabled else "Off", "New members will be muted until they press the verify button." if enabled else "New members will not be muted.").classic(), parse_mode=enums.ParseMode.HTML)
    if cmd == "captchamode":
        args = [a.lower() for a in (message.command or [])[1:]]
        if args != ["button"]:
            return await rich.reply(message, Doc("🤖", "CAPTCHA Mode").status("warning", "Unsupported mode", "Only the button CAPTCHA is available here. Use /captchamode button."))
        return await message.reply_text(Doc("🤖", "CAPTCHA Mode").status("success", "button", "New members press the button under the welcome message.").classic(), parse_mode=enums.ParseMode.HTML)
    if cmd == "resetcaptchatext":
        await update_group_cfg(message.chat.id, {"captcha_text": "Click here to prove you're human"})
        return await message.reply_text(Doc("🤖", "CAPTCHA Text Reset").status("success", "Default", "Button text restored.").classic(), parse_mode=enums.ParseMode.HTML)
    parts = (message.text or "").split(maxsplit=1)
    text = parts[1].strip()[:64] if len(parts) > 1 else ""
    if not text:
        return await rich.reply(message, Doc("🤖", "CAPTCHA Text").status("info", "Usage", "/setcaptchatext <plain text>"))
    await update_group_cfg(message.chat.id, {"captcha_text": text})
    return await message.reply_text(Doc("🤖", "CAPTCHA Text Updated").status("success", text, "Plain text only.").classic(), parse_mode=enums.ParseMode.HTML)


_PROTECTED_COMMANDS = frozenset({"disable", "enable", "disabled"})


@Client.on_message(filters.group & filters.command(["disable", "enable", "disabled"]) & PLG_FILTER)
async def disable_cmd(client: Client, message: Message):
    cmd = (message.command or ["disabled"])[0].lower()
    cfg = await get_group_cfg(message.chat.id)
    current = sorted({str(c).lower() for c in (cfg.get("disabled_commands") or [])})
    if cmd == "disabled":
        doc = Doc("🚫", "Disabled Commands", message.chat.title or "Group")
        doc.status("info", ", ".join(current) if current else "None", "Disabled commands are deleted when non-admins use them. Admins are exempt.")
        return await rich.reply(message, doc)
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_change_info",
        is_anon_admin=_is_anon_group_admin(message), check_bot=False,
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    names = []
    for raw in (message.command or [])[1:]:
        name = raw.lower().lstrip("/").split("@", 1)[0]
        if re.fullmatch(r"[a-z][a-z0-9_]{0,31}", name) and name not in _PROTECTED_COMMANDS:
            names.append(name)
    if not names:
        return await rich.reply(message, Doc("🚫", "Disabled Commands").status("warning", "No valid commands supplied", "Example: /disable rules notes. The disable/enable commands themselves cannot be disabled."))
    if cmd == "disable":
        updated = sorted(set(current) | set(names))
        verb = "Disabled"
    else:
        updated = sorted(set(current) - set(names))
        verb = "Re-enabled"
    await update_group_cfg(message.chat.id, {"disabled_commands": updated})
    return await message.reply_text(
        Doc("🚫", f"Commands {verb}").status("success", ", ".join(names), f"Currently disabled: {', '.join(updated) or 'None'}").classic(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.group & filters.command(["permapin"]) & PLG_FILTER)
async def permapin_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_pin_messages",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    parts = (message.text or "").split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    if not arg:
        return await rich.reply(message, Doc("📌", "Permapin").status("info", "Usage", "/permapin <message> — sends and pins a permanent announcement."))
    try:
        sent = await client.send_message(message.chat.id, rich.esc(arg[:3500]), parse_mode=enums.ParseMode.HTML)
        result = await client.pin_chat_message(message.chat.id, sent.id)
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Permapin Failed").status("error", "Telegram API error", str(e)[:160]))
    if not result:
        return await rich.reply(message, Doc("❌", "Permapin Failed").status("error", "Pin was not confirmed", "The announcement was sent but Telegram did not confirm the pin."))
    return await rich.reply(message, Doc("📌", "Announcement Pinned").status("success", "Pinned", arg[:120]))


@Client.on_message(filters.group & filters.command(["pin", "unpin", "purge"]) & PLG_FILTER)
async def pin_purge_cmd(client: Client, message: Message):
    cmd = (message.command or ["pin"])[0].lower()
    uid = message.from_user.id if message.from_user else None
    privilege = "can_pin_messages" if cmd in ("pin", "unpin") else "can_delete_messages"
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege=privilege,
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Refused").status("error", "Permission check failed", err))
    if cmd == "purge":
        args = (message.command or [])[1:]
        if len(args) != 1 or not args[0].isascii() or not args[0].isdigit() or not 1 <= int(args[0]) <= 100:
            return await rich.reply(message, Doc("🧹", "Purge").status("info", "Usage", "/purge <1–100>"))
        count = int(args[0])
        try:
            ids = []
            async for m in client.get_chat_history(message.chat.id, limit=count + 1):
                if m.id != message.id:
                    ids.append(m.id)
                if len(ids) >= count:
                    break
            if ids:
                await client.delete_messages(message.chat.id, ids)
            await message.reply_text(Doc("🧹", "Purge Complete").status("success", f"{len(ids)} messages deleted", "Recent history purged.").classic(), parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            return await rich.reply(message, Doc("❌", "Purge Failed").status("error", "Telegram API error", str(e)))
        return
    rep = message.reply_to_message
    if not rep:
        return await rich.reply(message, Doc("📌", "Pin").status("info", "Usage", f"Reply to a message with /{cmd}."))
    try:
        if cmd == "pin":
            await client.pin_chat_message(message.chat.id, rep.id)
            text = "Message pinned."
        else:
            await client.unpin_chat_message(message.chat.id, rep.id)
            text = "Message unpinned."
    except Exception as e:
        return await rich.reply(message, Doc("❌", "Pin Failed").status("error", "Telegram API error", str(e)))
    return await message.reply_text(Doc("📌", "Pin Updated").status("success", text, f"Message #{rep.id}").classic(), parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.group & filters.command("antiraid") & PLG_FILTER)
async def antiraid_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else None
    anon = _is_anon_group_admin(message)
    ok, err = await verify_mod_permissions(client, message.chat.id, uid, privilege="can_restrict_members", is_anon_admin=anon, check_bot=False)
    if not ok:
        return await rich.reply(message, Doc("🔒", "Anti-Raid").status("error", "Permission check failed", err))
    args = [x.lower() for x in (message.command or [])[1:]]
    cfg = await get_group_cfg(message.chat.id)
    if not args:
        state = "Active" if _raid_active(cfg) else "Previous setting inactive; confirm again" if cfg.get("antiraid_until", 0) > time.time() else "Off"
        return await rich.reply(message, Doc("🚨", "Anti-Raid").status("info", state, "Use /antiraid <5–1440 minutes> confirm to decline all join requests and temporarily ban eligible new joins for 15 minutes. Use /antiraid off to stop."))
    if len(args) == 1 and args[0] in ("off", "disable", "0"):
        await update_group_cfg(message.chat.id, {"antiraid_until": 0.0, "antiraid_confirmed": False})
        return await rich.reply(message, Doc("🛡", "Anti-Raid Disabled").status("info", "Standby", "No new raid actions will be initiated by this worker after its settings refresh."))
    if len(args) != 2 or args[1] != "confirm" or not args[0].isascii() or not args[0].isdigit() or len(args[0]) > 4 or not 5 <= int(args[0]) <= 1440:
        return await rich.reply(message, Doc("🚨", "Anti-Raid").status("warning", "Confirmation required or invalid duration", "Use /antiraid <5–1440 minutes> confirm. It declines all join requests and attempts a 15-minute ban on eligible new joins. Use /antiraid off to stop."))
    if _join_mode(cfg) == "auto":
        return await rich.reply(message, Doc("🚨", "Anti-Raid").status("warning", "Auto approval is enabled", "Turn it off or use notification mode before activating the raid shield."))
    for privilege in ("can_restrict_members", "can_invite_users"):
        ok, err = await verify_mod_permissions(client, message.chat.id, uid, privilege=privilege, is_anon_admin=anon)
        if not ok:
            return await rich.reply(message, Doc("🔒", "Anti-Raid").status("error", "Permission check failed", err))
    minutes = int(args[0])
    await update_group_cfg(message.chat.id, {"antiraid_until": time.time() + minutes * 60, "antiraid_confirmed": True})
    return await rich.reply(message, Doc("🚨", "Anti-Raid Active").status("warning", f"Enabled for {minutes} min", "Attempts to decline all join requests and ban eligible new joins for 15 minutes. Delivery and Telegram actions are not guaranteed; /antiraid off stops further actions."))


@Client.on_message(filters.group & filters.command("modreport") & PLG_FILTER)
async def modreport_cmd(client: Client, message: Message):
    rep = message.reply_to_message
    if not rep or not rep.from_user:
        return await rich.reply(message, Doc("🚨", "Report Message").status("info", "Usage", "Reply to an offending message with /modreport [reason]."))
    reporter = message.from_user
    reason = " ".join((message.command or [])[1:]).strip()[:200] or "Reported by group member"
    target = rep.from_user
    doc = Doc("🚨", "Moderation Report", message.chat.title or "Group").kv_table([
        ("👤 Reported", f"{target.first_name or target.id} ({target.id})"),
        ("🙋 Reporter", f"{reporter.first_name if reporter else 'Member'}"),
        ("💬 Message ID", str(rep.id)),
        ("📝 Reason", reason),
    ])
    kb = InlineKeyboardMarkup([[
        styled_button("🗑 Delete", callback_data=f"plg_gmod:rep:del:{rep.id}:{target.id}", style="danger"),
        styled_button("🔇 Mute 1h", callback_data=f"plg_gmod:rep:mute:{rep.id}:{target.id}", style="primary"),
        styled_button("🚫 Ban", callback_data=f"plg_gmod:rep:ban:{rep.id}:{target.id}", style="danger"),
    ]])
    return await message.reply_text(doc.classic(), reply_markup=kb, parse_mode=enums.ParseMode.HTML)


# ───────────────────────── Join-Requests, Member Greetings & Passive Guards ─────────────────────────
@Client.on_chat_join_request(group=4)
async def group_join_request_handler(client: Client, request: ChatJoinRequest):
    if not plugin_enabled("group_mod"):
        return
    if not request.chat or request.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return
    user = getattr(request, "from_user", None)
    target_id = getattr(user, "id", None)
    if type(target_id) is not int or target_id <= 0:
        return
    cfg = await get_group_cfg(request.chat.id)
    raid_active = _raid_active(cfg)
    mode = _join_mode(cfg)
    if cfg.get("antiraid_until", 0) > time.time() and not raid_active and mode == "auto":
        # Do not auto-approve while an old, unconfirmed shield is still configured.
        _automation_warn("legacy_raid", "Join-request auto-approval skipped: legacy anti-raid setting requires review")
        return
    if not raid_active and mode == "off":
        return
    ok, _ = await verify_bot_privilege(client, request.chat.id, "can_invite_users")
    if not ok:
        _automation_warn("join_rights", "Join-request automation skipped: bot permission unavailable")
        return
    if raid_active:
        try:
            result = await client.decline_chat_join_request(request.chat.id, target_id)
            if not result:
                _automation_warn("raid_decline", "Anti-raid join-request decline was not confirmed")
        except Exception:
            _automation_warn("raid_decline", "Anti-raid join-request decline failed or was not confirmed")
        return
    if mode == "auto":
        try:
            result = await client.approve_chat_join_request(request.chat.id, target_id)
            if not result:
                _automation_warn("join_approval", "Join-request auto-approval was not confirmed")
        except Exception:
            _automation_warn("join_approval", "Join-request auto-approval failed or was not confirmed")
    elif mode == "notify":
        u = request.from_user
        doc = Doc("🚪", "Join Request", request.chat.title or "Group").kv_table([
            ("👤 User", f"{u.first_name or u.id} ({u.id})"),
            ("🔗 Username", f"@{u.username}" if getattr(u, "username", None) else "—"),
        ])
        kb = InlineKeyboardMarkup([[
            styled_button("✅ Approve", callback_data=f"plg_gmod:jr:ok:{u.id}", style="success"),
            styled_button("❌ Decline", callback_data=f"plg_gmod:jr:no:{u.id}", style="danger"),
        ]])
        try:
            result = await client.send_message(request.chat.id, doc.classic(), reply_markup=kb, parse_mode=enums.ParseMode.HTML)
            if not result:
                _automation_warn("join_notification", "Join-request notification was not confirmed")
        except Exception:
            _automation_warn("join_notification", "Join-request notification failed or was not confirmed")


@Client.on_chat_member_updated(group=4)
async def group_member_greeting_handler(client: Client, update: ChatMemberUpdated):
    if not plugin_enabled("group_mod"):
        return
    if not update.chat or update.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return
    old_s = getattr(update.old_chat_member, "status", None)
    new_s = getattr(update.new_chat_member, "status", None)
    new_u = getattr(update.new_chat_member, "user", None)
    old_u = getattr(update.old_chat_member, "user", None)

    joined = old_s in (None, enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED) and new_s == enums.ChatMemberStatus.MEMBER
    left = old_s == enums.ChatMemberStatus.MEMBER and new_s in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED)
    if not (joined or left):
        return

    cfg = await get_group_cfg(update.chat.id)
    if joined and new_u and not getattr(new_u, "is_bot", False):
        if _raid_active(cfg):
            if getattr(new_u, "is_bot", None) is not False:
                return  # Unknown bot identity is not proof of a human member.
            if old_s not in (enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
                return  # Unknown previous status is not proof of a new join.
            target_id = getattr(new_u, "id", None)
            bot_id = getattr(getattr(client, "me", None), "id", None)
            if type(target_id) is not int or target_id <= 0 or target_id == bot_id:
                return
            ok, _ = await verify_bot_privilege(client, update.chat.id, "can_restrict_members")
            if not ok:
                _automation_warn("raid_bot_rights", "Anti-raid join action skipped: bot permission unavailable")
                return
            try:
                current = await client.get_chat_member(update.chat.id, target_id)
            except Exception:
                _automation_warn("raid_member_status", "Anti-raid join action skipped: member status unavailable")
                return
            if getattr(current, "status", None) != enums.ChatMemberStatus.MEMBER:
                return  # A stale update is not proof that the user is still a non-admin member.
            try:
                result = await client.ban_chat_member(update.chat.id, target_id, until_date=_until_dt(timedelta(minutes=15)))
                if not result:
                    _automation_warn("raid_join", "Anti-raid join action was not confirmed")
            except Exception:
                _automation_warn("raid_join", "Anti-raid join action failed or was not confirmed")
            return
        if cfg.get("welcome_on"):
            txt = _format_greeting(cfg.get("welcome_text") or "👋 Welcome {mention}!", new_u, update.chat.title or "")
            uid = getattr(new_u, "id", None)
            if cfg.get("captcha_on") and type(uid) is int and uid > 0:
                ok, _ = await verify_bot_privilege(client, update.chat.id, "can_restrict_members")
                if ok:
                    try:
                        await client.restrict_chat_member(update.chat.id, uid, ChatPermissions())
                    except Exception:
                        pass
                    btn_text = str(cfg.get("captcha_text") or "Click here to prove you're human")[:64]
                    kb = InlineKeyboardMarkup([[Btn(btn_text, callback_data=f"plg_gmod:captcha:{uid}")]])
                    try:
                        sent = await client.send_message(update.chat.id, txt, parse_mode=enums.ParseMode.HTML, reply_markup=kb)
                        await update_group_cfg(update.chat.id, {"last_welcome_id": getattr(sent, "id", 0) or 0})
                    except Exception:
                        pass
                    return
            try:
                sent = await client.send_message(update.chat.id, txt, parse_mode=enums.ParseMode.HTML)
                new_id = getattr(sent, "id", 0) or 0
                if cfg.get("cleanwelcome") and cfg.get("last_welcome_id") and cfg.get("last_welcome_id") != new_id:
                    try:
                        await client.delete_messages(update.chat.id, [int(cfg["last_welcome_id"])])
                    except Exception:
                        pass
                if new_id:
                    await update_group_cfg(update.chat.id, {"last_welcome_id": new_id})
            except Exception:
                pass
    elif left and old_u and not getattr(old_u, "is_bot", False) and cfg.get("goodbye_on"):
        txt = _format_greeting(cfg.get("goodbye_text") or "👋 Goodbye, {first}!", old_u, update.chat.title or "")
        try:
            await client.send_message(update.chat.id, txt, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass


@Client.on_message(filters.group & filters.service, group=6)
async def group_service_cleaner(client: Client, message: Message):
    if not plugin_enabled("group_mod"):
        return
    cfg = await get_group_cfg(message.chat.id)
    if cfg.get("cleanservice"):
        try:
            await message.delete()
        except Exception:
            pass


@Client.on_message(filters.group & filters.incoming & ~filters.service, group=6)
async def group_passive_guard(client: Client, message: Message):
    if not plugin_enabled("group_mod"):
        return
    cfg = await get_group_cfg(message.chat.id)
    locks = cfg.get("locks") or []
    antiflood = bool(cfg.get("antiflood"))
    fmap: dict[str, str] = cfg.get("filters") or {}
    bmap: dict[str, str] = cfg.get("blocklist") or {}
    nmap: dict[str, str] = cfg.get("notes") or {}
    disabled = {str(c).lower() for c in (cfg.get("disabled_commands") or [])}
    if not (locks or antiflood or fmap or bmap or nmap or disabled):
        return

    user = message.from_user
    text = message.text or message.caption or ""
    # Disabled commands: delete non-admin invocations; admins stay exempt.
    if disabled and user and text.startswith("/"):
        invoked = text[1:].split(maxsplit=1)[0].split("@", 1)[0].lower()
        if invoked in disabled:
            try:
                mem = await client.get_chat_member(message.chat.id, user.id)
                status = getattr(mem, "status", None)
            except Exception:
                status = None
            if status in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED):
                try:
                    await message.delete()
                except Exception:
                    pass
                return
            if status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR):
                return  # Exempt; don't let other guards treat the command as content.
            return  # Unknown status: suppress quietly rather than risk acting on an admin.
    # Notes: #trigger opens a note (non-admins may open, admins manage).
    if nmap and text.startswith("#") and len(text) > 1 and not text.startswith("/"):
        trig = text[1:].split()[0].lower()
        if trig in nmap:
            formatted = _format_greeting(nmap[trig], user, message.chat.title or "")
            try:
                await message.reply_text(formatted, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
            return
    # Check locks, blocklists & anti-flood against non-admins
    if user and (locks or antiflood or bmap):
        violated = match_locked_content(message, locks, cfg.get("url_whitelist") or [])
        block_hit = match_blocklist(text, bmap) if bmap else None

        flood_hit = False
        if antiflood:
            now = time.monotonic()
            bkey = (message.chat.id, user.id)
            try:
                flood_limit = int(cfg.get("flood_limit", 7))
            except (TypeError, ValueError):
                flood_limit = 7
            try:
                flood_window = float(cfg.get("flood_window", 8.0))
            except (TypeError, ValueError):
                flood_window = 8.0
            flood_limit = max(2, min(50, flood_limit))
            flood_window = max(1.0, min(300.0, flood_window))
            bucket = [t for t in _FLOOD_BUCKETS.get(bkey, []) if now - t < flood_window]
            bucket.append(now)
            _FLOOD_BUCKETS[bkey] = bucket
            _FLOOD_BUCKETS.move_to_end(bkey)
            while len(_FLOOD_BUCKETS) > _FLOOD_MAX:
                _FLOOD_BUCKETS.popitem(last=False)
            if len(bucket) >= flood_limit:
                flood_hit = True
                _FLOOD_BUCKETS.pop(bkey, None)

        if violated or block_hit or flood_hit:
            try:
                mem = await client.get_chat_member(message.chat.id, user.id)
                member_status = getattr(mem, "status", None)
                if member_status not in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR,
                                         enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED):
                    return  # Unknown status is not proof that the sender is a non-admin.
            except Exception:
                return  # Fail closed: the sender may be an administrator.
            if member_status in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED):
                if violated:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    if cfg.get("lockwarns"):
                        try:
                            await message.reply_text(
                                f"🔒 <b>Locked content:</b> {rich.esc(user.first_name or user.id)}, {rich.esc(violated)} content is not allowed here.",
                                parse_mode=enums.ParseMode.HTML,
                            )
                        except Exception:
                            pass
                    return
                if block_hit:
                    trig, reason = block_hit
                    if cfg.get("blocklist_delete", True):
                        try:
                            await message.delete()
                        except Exception:
                            pass
                    mode_raw = str(cfg.get("blocklist_mode", "nothing"))
                    parsed = parse_mode(mode_raw)
                    if parsed is not None and parsed[0] != "nothing":
                        mode, delta, label = parsed
                        ok, _ = await verify_bot_privilege(client, message.chat.id, "can_restrict_members")
                        if ok:
                            await _apply_restriction(client, message.chat.id, user.id, mode, delta)
                        try:
                            await message.reply_text(
                                f"🚫 <b>Blocklist:</b> {rich.esc(user.first_name or user.id)} — {rich.esc(reason)} ({rich.esc(label)})",
                                parse_mode=enums.ParseMode.HTML,
                            )
                        except Exception:
                            pass
                    return
                if flood_hit:
                    ok, _ = await verify_bot_privilege(client, message.chat.id, "can_restrict_members")
                    if not ok:
                        _automation_warn("flood_bot_rights", "Anti-flood action skipped: bot permission unavailable")
                        return
                    mode_raw = str(cfg.get("flood_mode", "tmute 5m"))
                    parsed = parse_mode(mode_raw)
                    if parsed is None:
                        parsed = ("tmute", timedelta(minutes=5), "tmute 5m")
                    mode, delta, label = parsed
                    if cfg.get("clearflood"):
                        try:
                            await message.delete()
                        except Exception:
                            pass
                    try:
                        if mode == "mute":
                            result = await client.restrict_chat_member(
                                message.chat.id, user.id, ChatPermissions(),
                                until_date=_until_dt(timedelta(hours=6)),
                            )
                            done = bool(result)
                        elif mode == "ban":
                            done = bool(await client.ban_chat_member(message.chat.id, user.id))
                        elif mode == "tban":
                            done = bool(await client.ban_chat_member(message.chat.id, user.id, until_date=_until_dt(delta)))
                        elif mode == "kick":
                            await client.ban_chat_member(message.chat.id, user.id)
                            await client.unban_chat_member(message.chat.id, user.id)
                            done = True
                        elif mode == "tmute":
                            done = bool(await client.restrict_chat_member(
                                message.chat.id, user.id, ChatPermissions(),
                                until_date=_until_dt(delta),
                            ))
                        else:
                            done = False
                        if not done:
                            _automation_warn("flood_action", "Anti-flood action was not confirmed")
                            return
                        await message.reply_text(
                            f"🌊 <b>Anti-Flood:</b> {rich.esc(label)} applied to {rich.esc(user.first_name or user.id)}.",
                            parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception:
                        _automation_warn("flood_action", "Anti-flood action failed or was not confirmed")
                    return

    # Keyword filters (non-command messages)
    matched = match_keyword_filter(text, fmap)
    if matched:
        _, reply_val = matched
        formatted = _format_greeting(reply_val, user, message.chat.title or "")
        try:
            await message.reply_text(formatted, parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass


# ───────────────────────── Unified Callback Dispatcher ─────────────────────────
@Client.on_callback_query(filters.regex(r"^plg_gmod:[\w:_-]+$"))
async def group_mod_cb(client: Client, query: CallbackQuery):
    if not plugin_enabled("group_mod"):
        return await query.answer("Group management is disabled.", show_alert=True)
    if not query.message or not query.from_user or not query.message.chat or query.message.chat.type not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
        return await query.answer()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    chat_id = query.message.chat.id
    uid = query.from_user.id

    if action == "tog" and len(parts) >= 3:
        field_key = parts[2]
        privileges = {"welcome": "can_change_info", "goodbye": "can_change_info", "antiflood": "can_restrict_members", "antiraid": "can_restrict_members", "joinmode": "can_invite_users", "warnact": "can_restrict_members"}
        if len(parts) != 3 or field_key not in privileges:
            return await query.answer("Unknown setting.", show_alert=True)
        ok, err = await verify_mod_permissions(client, chat_id, uid, privilege=privileges[field_key], check_bot=False)
        if not ok:
            return await query.answer(f"🔒 {err}"[:190], show_alert=True)
        cfg = await get_group_cfg(chat_id)
        if field_key == "welcome":
            cfg = await update_group_cfg(chat_id, {"welcome_on": not cfg.get("welcome_on")})
        elif field_key == "goodbye":
            cfg = await update_group_cfg(chat_id, {"goodbye_on": not cfg.get("goodbye_on")})
        elif field_key == "antiflood":
            enabling = not cfg.get("antiflood")
            if enabling:
                ok, err = await verify_bot_privilege(client, chat_id, "can_restrict_members")
                if not ok:
                    return await query.answer(f"🔒 {err}"[:190], show_alert=True)
            cfg = await update_group_cfg(chat_id, {"antiflood": enabling})
        elif field_key == "antiraid":
            if cfg.get("antiraid_until", 0) > time.time():
                cfg = await update_group_cfg(chat_id, {"antiraid_until": 0.0, "antiraid_confirmed": False})
            else:
                return await query.answer("Use /antiraid <5–1440> confirm. It declines all requests and temporarily bans eligible new joins.", show_alert=True)
        elif field_key == "joinmode":
            current = cfg.get("joinmode", "off")
            # Dashboard cycles only between off and notify; auto requires the
            # explicit command and can always be switched off here.
            nxt = "off" if current in ("notify", "auto") else "notify"
            if nxt == "notify":
                ok, err = await verify_bot_privilege(client, chat_id, "can_invite_users")
                if not ok:
                    return await query.answer(f"🔒 {err}"[:190], show_alert=True)
            cfg = await update_group_cfg(chat_id, {"joinmode": nxt, "joinmode_auto_confirmed": False})
        elif field_key == "warnact":
            cur = str(cfg.get("warn_action", "mute"))
            nxt = {"mute": "ban", "ban": "kick", "kick": "tmute 5m", "tmute 5m": "tban 1d"}.get(cur, "mute")
            cfg = await update_group_cfg(chat_id, {"warn_action": nxt})
        await query.answer("Updated!")
        doc, kb = build_gsettings_doc(query.message.chat.title or "Group", cfg)
        return await query.message.edit_text(doc.classic(), reply_markup=kb, parse_mode=enums.ParseMode.HTML)

    if action == "captcha" and len(parts) == 3 and parts[2].lstrip("-").isdigit():
        target_id = int(parts[2])
        if uid != target_id:
            return await query.answer("Only the new member can verify.", show_alert=True)
        cfg = await get_group_cfg(chat_id)
        if not cfg.get("captcha_on"):
            return await query.answer("CAPTCHA is not active.", show_alert=True)
        try:
            await client.restrict_chat_member(chat_id, target_id, _full_send_permissions(query.message.chat))
        except Exception:
            return await query.answer("Could not unmute. Ask an admin to check.", show_alert=True)
        await query.answer("✅ Verified! Welcome to the group.")
        try:
            await query.message.edit_text("✅ <b>Verified!</b> You can now chat.", parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
        return

    if action == "unwarn" and len(parts) >= 3 and parts[2].lstrip("-").isdigit():
        ok, err = await verify_mod_permissions(client, chat_id, uid, privilege="can_restrict_members", check_bot=False)
        if not ok:
            return await query.answer(f"🔒 {err}"[:190], show_alert=True)
        target_id = int(parts[2])
        try:
            old = await vdb.db["group_warns"].find_one({"_id": f"{chat_id}:{target_id}"})
            if not old or not old.get("reasons"):
                return await query.answer("No active warnings.", show_alert=True)
            reasons = await _change_warns(chat_id, target_id, None)
        except (ValueError, RuntimeError) as exc:
            return await query.answer(str(exc)[:190], show_alert=True)
        await query.answer("↩️ Warning removed.")
        return await query.message.edit_text(
            Doc("✅", "Warning Removed").status("success", f"User {target_id}", f"Remaining warnings: {len(reasons)}").classic(),
            parse_mode=enums.ParseMode.HTML,
        )

    if action == "jr" and len(parts) >= 4 and parts[3].lstrip("-").isdigit():
        ok, err = await verify_mod_permissions(client, chat_id, uid, privilege="can_invite_users")
        if not ok:
            return await query.answer(f"🔒 {err}"[:190], show_alert=True)
        decision, target_id = parts[2], int(parts[3])
        if len(parts) != 4 or decision not in ("ok", "no") or target_id <= 0:
            return await query.answer("Invalid join request action.", show_alert=True)
        try:
            if decision == "ok":
                result = await client.approve_chat_join_request(chat_id, target_id)
                label = "Approved"
            else:
                result = await client.decline_chat_join_request(chat_id, target_id)
                label = "Declined"
            if not result:
                return await query.answer("Action was not confirmed. Check Telegram before retrying.", show_alert=True)
        except Exception:
            return await query.answer("Action failed or was not confirmed. Check Telegram before retrying.", show_alert=True)
        await query.answer(f"{label}!")
        return await query.message.edit_text(
            Doc("🚪", f"Join Request {label}").status("success" if decision == "ok" else "info", f"User {target_id}", f"Handled by {query.from_user.first_name or uid}").classic(),
            parse_mode=enums.ParseMode.HTML,
        )

    if action == "rep" and len(parts) >= 5 and parts[3].isdigit() and parts[4].lstrip("-").isdigit():
        sub, msg_id, target_id = parts[2], int(parts[3]), int(parts[4])
        if len(parts) != 5 or sub not in ("del", "mute", "ban") or msg_id <= 0 or target_id <= 0:
            return await query.answer("Invalid report action.", show_alert=True)
        ok, err = await verify_mod_permissions(
            client, chat_id, uid, privilege="can_delete_messages", target_id=target_id,
        )
        if not ok:
            return await query.answer(f"🔒 {err}"[:190], show_alert=True)
        if sub != "del":
            ok, err = await verify_mod_permissions(
                client, chat_id, uid, privilege="can_restrict_members", target_id=target_id,
            )
            if not ok:
                return await query.answer(f"🔒 {err}"[:190], show_alert=True)
        try:
            deleted = await client.delete_messages(chat_id, msg_id)
            if not deleted:
                return await query.answer("Message deletion was not confirmed; no further action taken.", show_alert=True)
        except Exception:
            return await query.answer("Message deletion failed; no further action taken.", show_alert=True)
        summary = f"Deleted message #{msg_id}"
        try:
            if sub == "mute":
                await client.restrict_chat_member(
                    chat_id, target_id, ChatPermissions(), until_date=_until_dt(timedelta(hours=1))
                )
                summary += f" & muted {target_id} (1h)"
            elif sub == "ban":
                await client.ban_chat_member(chat_id, target_id)
                summary += f" & banned {target_id}"
        except Exception:
            return await query.answer("Message deleted, but moderation failed or was not confirmed. Check Telegram before retrying.", show_alert=True)
        await query.answer("Action applied!")
        return await query.message.edit_text(
            Doc("🛡", "Report Resolved").status("success", summary, f"Moderator: {query.from_user.first_name or uid}").classic(),
            parse_mode=enums.ParseMode.HTML,
        )

    return await query.answer()


class Plugin(PluginBase):
    """Lifecycle wrapper for Group Management & Bot API 10.3 Moderation."""

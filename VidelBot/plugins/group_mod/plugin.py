"""
Richgram-powered Group Management & Bot API 10.3 Moderation Plugin.

Highlights:
  • Live permission verification (`verify_mod_permissions`) — queries Telegram's
    live `get_chat_member` for BOTH the caller and the bot on every admin action
    (never trusts a stale admin cache), honours anonymous group admins, and
    prevents acting against group owners/administrators.
  • Core moderation: `/gban`, `/gunban`, `/kick` (`/punch`), `/mute`, `/unmute`,
    `/warn`, `/unwarn`, `/warns`.
  • Bot API 10.3 / WzGram Layer 232 capabilities:
      - `/tag` & `/deltag` (`setChatMemberTag` / `client.set_chat_member_tag`)
      - `/clearreactions` (`/delreactions`) (`deleteMessageReaction` &
        `deleteAllMessageReactions`)
      - `/topic new|close|reopen` (forum topic lifecycle)
      - `/joinmode off|notify|auto` (join-request approval cards + raid guard)
  • Community & protection: `/gsettings`, `/rules` (ephemeral), `/setrules`,
    `/welcome`, `/goodbye`, `/lock`, `/unlock`, `/gfilter`, `/delfilter`,
    `/gfilters`, `/antiraid`, `/modreport`.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
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
from core.ui import is_wzgram_client, styled_button

log = logging.getLogger("videl.plugin.group_mod")

PLG_FILTER = plugin_filter("group_mod")
DURATION_RE = re.compile(r"^(\d+)([mhdw])$", re.IGNORECASE)
LINK_RE = re.compile(r"(https?://\S+|t\.me/\S+|telegram\.me/\S+)", re.IGNORECASE)
VALID_LOCKS = ("links", "forwards", "bots", "media", "all")

GROUP_ANON_COMMANDS = frozenset({
    "gsettings", "gban", "gunban", "kick", "punch", "mute", "unmute",
    "warn", "unwarn", "warns", "warnconfig", "warnrecover", "tag", "deltag", "clearreactions", "delreactions",
    "topic", "joinmode", "rules", "setrules", "welcome", "goodbye",
    "lock", "unlock", "gfilter", "delfilter", "gfilters", "antiraid",
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
        "antiraid_until": 0.0,
        "antiraid_confirmed": False,
        "joinmode": "off",  # off | notify | auto
        "joinmode_auto_confirmed": False,
        "warn_limit": 3,
        "warn_action": "mute",  # mute | ban
        "locks": [],
        "filters": {},
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


def match_locked_content(message: Message, locks: list[str]) -> str:
    """Return the first lock hit, using the same precedence as the live guard."""
    text = message.text or message.caption or ""
    if "all" in locks:
        return "all"
    if "links" in locks and LINK_RE.search(text):
        return "links"
    if "forwards" in locks and (getattr(message, "forward_date", None) or getattr(message, "forward_origin", None)):
        return "forwards"
    if "media" in locks and any(getattr(message, k, None) for k in ("photo", "video", "document", "animation", "sticker")):
        return "media"
    if "bots" in locks and getattr(message, "via_bot", None):
        return "bots"
    return ""


def match_keyword_filter(text: str, fmap: dict[str, str]) -> tuple[str, str] | None:
    """Return the live guard's first keyword match, without sending anything."""
    if text and not text.startswith("/"):
        low = text.lower()
        for trigger, reply in fmap.items():
            if re.search(rf"(?<!\w){re.escape(trigger)}(?!\w)", low):
                return trigger, reply
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
    doc.kv_table([
        ("👋 Welcome", "🟢 Enabled" if cfg.get("welcome_on") else "🔴 Off"),
        ("🚪 Goodbye", "🟢 Enabled" if cfg.get("goodbye_on") else "🔴 Off"),
        ("🌊 Anti-Flood", "🟢 Enabled" if cfg.get("antiflood") else "🔴 Off"),
        ("🚨 Anti-Raid", "🛡 Active" if raid_active else "⚠️ Previous setting inactive; confirm again" if legacy_raid else "⚪ Standby"),
        ("🚪 Join Requests", "⚠️ AUTO inactive; confirm again" if legacy_auto else join_mode.upper()),
        ("⚠️ Warn Limit", f"{cfg.get('warn_limit', 3)} → {cfg.get('warn_action', 'mute')}"),
        ("🔒 Active Locks", ", ".join(locks) if locks else "None"),
        ("💬 Keyword Filters", str(len(cfg.get("filters") or {}))),
        ("📜 Rules", "Configured" if cfg.get("rules") else "Not set"),
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
            ("Mute duration", "6 hours"),
            ("Usage", "/warnconfig <1–20> <mute|ban>"),
        ]))
    if len(args) != 2 or len(args[0]) > 2 or not args[0].isascii() or not args[0].isdigit() or not 1 <= int(args[0]) <= 20 or args[1].lower() not in ("mute", "ban"):
        return await rich.reply(message, Doc("⚠️", "Warning Policy").status("warning", "Invalid policy", "Use /warnconfig <1–20> <mute|ban>. Existing warnings are unchanged."))
    limit, action = int(args[0]), args[1].lower()
    await update_group_cfg(message.chat.id, {"warn_limit": limit, "warn_action": action})
    return await rich.reply(message, Doc("⚠️", "Warning Policy Updated").status("success", f"{limit} → {action}", "Existing warnings are unchanged. Enforcement happens on a new warning; mute lasts 6 hours."))


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
        try:
            if act == "ban":
                await client.ban_chat_member(message.chat.id, target_id)
                outcome = "Banned (warn threshold reached)"
            else:
                await client.restrict_chat_member(
                    message.chat.id, target_id, ChatPermissions(),
                    until_date=_until_dt(timedelta(hours=6)),
                )
                outcome = "Muted for 6h (warn threshold reached)"
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


@Client.on_message(filters.group & filters.command(["lock", "unlock"]) & PLG_FILTER)
async def lock_cmd(client: Client, message: Message):
    cmd = (message.command or ["lock"])[0].lower()
    uid = message.from_user.id if message.from_user else None
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_delete_messages",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Locks Refused").status("error", "Permission check failed", err))

    args = [a.lower() for a in (message.command or [])[1:]]
    if not args or any(a not in VALID_LOCKS for a in args):
        cfg = await get_group_cfg(message.chat.id)
        cur = ", ".join(cfg.get("locks") or []) or "None"
        return await rich.reply(
            message,
            Doc("🔒", "Content Locks").status("info", f"Active: {cur}", f"Options: {', '.join(VALID_LOCKS)}"),
        )
    cfg = await get_group_cfg(message.chat.id)
    locks = set(cfg.get("locks") or [])
    if cmd == "lock":
        locks.update(args)
    else:
        locks.difference_update(args)
    updated = sorted(locks)
    await update_group_cfg(message.chat.id, {"locks": updated})
    return await message.reply_text(
        Doc("🔒", f"Content {cmd.title()}ed").status("success", ", ".join(args), f"Active locks: {', '.join(updated) or 'None'}").classic(),
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.group & filters.command(["gfilter", "delfilter", "gfilters"]) & PLG_FILTER)
async def gfilter_cmd(client: Client, message: Message):
    cmd = (message.command or ["gfilters"])[0].lower()
    cfg = await get_group_cfg(message.chat.id)
    fmap: dict[str, str] = dict(cfg.get("filters") or {})

    if cmd == "gfilters":
        doc = Doc("💬", f"Group Filters · {len(fmap)}")
        if not fmap:
            doc.status("empty", "No keyword filters", "Admins can add one with /gfilter <word> <reply>.")
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

    if cmd == "delfilter":
        args = (message.command or [])[1:]
        if not args:
            return await rich.reply(message, Doc("💬", "Delete Filter").status("info", "Usage", "/delfilter <keyword>"))
        trig = args[0].lower()
        if trig not in fmap:
            return await rich.reply(message, Doc("💬", "Not Found").status("warning", trig, "No such filter in this group."))
        fmap.pop(trig, None)
        await update_group_cfg(message.chat.id, {"filters": fmap})
        return await rich.reply(message, Doc("🗑", "Filter Removed").status("success", trig, "Keyword filter deleted."))

    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        return await rich.reply(message, Doc("💬", "Add Filter").status("info", "Usage", "/gfilter <keyword> <reply text>"))
    if len(fmap) >= 60 and parts[1].lower() not in fmap:
        return await rich.reply(message, Doc("⚠️", "Limit Reached").status("warning", "60 filters max", "Delete unused filters first."))
    trig, reply_val = parts[1].lower()[:64], parts[2].strip()[:1000]
    fmap[trig] = reply_val
    await update_group_cfg(message.chat.id, {"filters": fmap})
    return await rich.reply(message, Doc("✅", "Filter Saved").status("success", trig, reply_val[:80]))


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
            try:
                await client.send_message(update.chat.id, txt, parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
    elif left and old_u and not getattr(old_u, "is_bot", False) and cfg.get("goodbye_on"):
        txt = _format_greeting(cfg.get("goodbye_text") or "👋 Goodbye, {first}!", old_u, update.chat.title or "")
        try:
            await client.send_message(update.chat.id, txt, parse_mode=enums.ParseMode.HTML)
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
    if not (locks or antiflood or fmap):
        return

    user = message.from_user
    text = message.text or message.caption or ""
    # Check locks & anti-flood against non-admins
    if user and (locks or antiflood):
        violated = match_locked_content(message, locks)

        flood_hit = False
        if antiflood:
            now = time.monotonic()
            bkey = (message.chat.id, user.id)
            bucket = [t for t in _FLOOD_BUCKETS.get(bkey, []) if now - t < 8.0]
            bucket.append(now)
            _FLOOD_BUCKETS[bkey] = bucket
            _FLOOD_BUCKETS.move_to_end(bkey)
            while len(_FLOOD_BUCKETS) > _FLOOD_MAX:
                _FLOOD_BUCKETS.popitem(last=False)
            if len(bucket) >= 7:
                flood_hit = True
                _FLOOD_BUCKETS.pop(bkey, None)

        if violated or flood_hit:
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
                    return
                if flood_hit:
                    ok, _ = await verify_bot_privilege(client, message.chat.id, "can_restrict_members")
                    if not ok:
                        _automation_warn("flood_bot_rights", "Anti-flood action skipped: bot permission unavailable")
                        return
                    try:
                        result = await client.restrict_chat_member(
                            message.chat.id, user.id, ChatPermissions(),
                            until_date=_until_dt(timedelta(minutes=5)),
                        )
                        if not result:
                            _automation_warn("flood_mute", "Anti-flood action was not confirmed")
                            return
                        await message.reply_text(
                            f"🌊 <b>Anti-Flood:</b> Muted {rich.esc(user.first_name or user.id)} for 5 minutes.",
                            parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception:
                        _automation_warn("flood_mute", "Anti-flood action failed or was not confirmed")
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
            nxt = "ban" if cfg.get("warn_action", "mute") == "mute" else "mute"
            cfg = await update_group_cfg(chat_id, {"warn_action": nxt})
        await query.answer("Updated!")
        doc, kb = build_gsettings_doc(query.message.chat.title or "Group", cfg)
        return await query.message.edit_text(doc.classic(), reply_markup=kb, parse_mode=enums.ParseMode.HTML)

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

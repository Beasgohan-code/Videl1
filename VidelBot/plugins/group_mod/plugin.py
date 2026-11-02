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
import re
import time
from typing import Any

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
    "warn", "unwarn", "warns", "tag", "deltag", "clearreactions", "delreactions",
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
        "joinmode": "off",  # off | notify | auto
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
        return True
    if privilege == "can_manage_tags":
        val = getattr(priv, "can_manage_tags", None)
        if val is not None:
            return bool(val)
        return bool(getattr(priv, "can_promote_members", False) or getattr(priv, "can_manage_chat", False))
    return bool(getattr(priv, privilege, False))


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
        except Exception as e:
            return False, f"Could not verify your admin status ({e})."
        if not _has_privilege(caller_mem, privilege):
            pretty = privilege.replace("can_", "").replace("_", " ")
            return False, f"You need administrator permission: {pretty}."

    bot_uid = getattr(getattr(client, "me", None), "id", None)
    if check_bot and bot_uid:
        if target_id and int(target_id) == int(bot_uid):
            return False, "I cannot moderate myself."
        try:
            bot_mem = await client.get_chat_member(chat_id, bot_uid)
        except Exception as e:
            return False, f"Could not verify bot permissions ({e})."
        if not _has_privilege(bot_mem, privilege):
            pretty = privilege.replace("can_", "").replace("_", " ")
            return False, f"Promote me as admin with permission: {pretty}."

    if target_id is not None:
        try:
            t_mem = await client.get_chat_member(chat_id, target_id)
            if getattr(t_mem, "status", None) in (
                enums.ChatMemberStatus.OWNER,
                enums.ChatMemberStatus.ADMINISTRATOR,
            ):
                return False, "Cannot moderate a group owner or administrator."
        except Exception:
            pass

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
    raid_active = cfg.get("antiraid_until", 0) > time.time()
    locks = cfg.get("locks") or []
    doc = Doc("🛡", "Group Settings", chat_title or "Supergroup")
    doc.kv_table([
        ("👋 Welcome", "🟢 Enabled" if cfg.get("welcome_on") else "🔴 Off"),
        ("🚪 Goodbye", "🟢 Enabled" if cfg.get("goodbye_on") else "🔴 Off"),
        ("🌊 Anti-Flood", "🟢 Enabled" if cfg.get("antiflood") else "🔴 Off"),
        ("🚨 Anti-Raid", "🛡 Active" if raid_active else "⚪ Standby"),
        ("🚪 Join Requests", str(cfg.get("joinmode", "off")).upper()),
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
                f"🚪 Join: {cfg.get('joinmode', 'off')}",
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
    rec = await vdb.db["group_warns"].find_one({"_id": key}) or {"_id": key, "reasons": []}
    reasons: list[str] = list(rec.get("reasons") or [])
    reasons.append(reason)
    count = len(reasons)

    if count >= limit:
        await vdb.db["group_warns"].delete_one({"_id": key})
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
        except Exception as e:
            outcome = f"Threshold reached (enforcement failed: {e})"
        doc = Doc("🚨", "Warn Limit Reached").kv_table([
            ("👤 User", f"{label} ({target_id})"),
            ("⚠️ Warnings", f"{count}/{limit}"),
            ("⚡ Action", outcome),
            ("📝 Latest", reason),
        ])
        return await message.reply_text(doc.classic(), parse_mode=enums.ParseMode.HTML)

    await vdb.db["group_warns"].update_one(
        {"_id": key},
        {"$set": {"chat_id": message.chat.id, "user_id": target_id, "reasons": reasons}},
        upsert=True,
    )
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

    key = f"{message.chat.id}:{target_id}"
    rec = await vdb.db["group_warns"].find_one({"_id": key})
    reasons = list((rec or {}).get("reasons") or [])
    if not reasons:
        return await rich.reply(message, Doc("ℹ️", "No Warnings").status("info", f"{label} ({target_id})", "User has 0 active warnings."))
    reasons.pop()
    if reasons:
        await vdb.db["group_warns"].update_one({"_id": key}, {"$set": {"reasons": reasons}})
    else:
        await vdb.db["group_warns"].delete_one({"_id": key})
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
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Join Mode").status("error", "Permission check failed", err))
    args = (message.command or [])[1:]
    mode = args[0].lower() if args else ""
    if mode not in ("off", "notify", "auto"):
        cfg = await get_group_cfg(message.chat.id)
        return await rich.reply(
            message,
            Doc("🚪", "Join-Request Mode").status(
                "info", f"Current: {cfg.get('joinmode', 'off')}", "Choose: /joinmode off | notify | auto"
            ),
        )
    await update_group_cfg(message.chat.id, {"joinmode": mode})
    return await message.reply_text(
        Doc("🚪", "Join-Request Mode Updated").status("success", mode.upper(), "Group join-request workflow updated.").classic(),
        parse_mode=enums.ParseMode.HTML,
    )


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
    ok, err = await verify_mod_permissions(
        client, message.chat.id, uid, privilege="can_restrict_members",
        is_anon_admin=_is_anon_group_admin(message),
    )
    if not ok:
        return await rich.reply(message, Doc("🔒", "Anti-Raid").status("error", "Permission check failed", err))
    args = (message.command or [])[1:]
    arg = args[0].lower() if args else ""
    if arg in ("off", "disable", "0"):
        await update_group_cfg(message.chat.id, {"antiraid_until": 0.0})
        return await message.reply_text(
            Doc("🛡", "Anti-Raid Disabled").status("info", "Standby", "Normal group joins resumed.").classic(),
            parse_mode=enums.ParseMode.HTML,
        )
    minutes = int(arg) if arg.isdigit() else 30
    minutes = max(5, min(1440, minutes))
    until_ts = time.time() + minutes * 60
    await update_group_cfg(message.chat.id, {"antiraid_until": until_ts})
    return await message.reply_text(
        Doc("🚨", "Anti-Raid Shield Active").status("warning", f"Enabled for {minutes} min", "New join requests and suspicious joins will be blocked.").classic(),
        parse_mode=enums.ParseMode.HTML,
    )


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
    cfg = await get_group_cfg(request.chat.id)
    raid_active = cfg.get("antiraid_until", 0) > time.time()
    mode = cfg.get("joinmode", "off")
    if raid_active:
        try:
            await client.decline_chat_join_request(request.chat.id, request.from_user.id)
        except Exception:
            pass
        return
    if mode == "auto":
        try:
            await client.approve_chat_join_request(request.chat.id, request.from_user.id)
        except Exception as e:
            log.debug(f"auto-approve join request failed: {e}")
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
            await client.send_message(request.chat.id, doc.classic(), reply_markup=kb, parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            log.debug(f"join-request notify failed: {e}")


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
        if cfg.get("antiraid_until", 0) > time.time():
            try:
                await client.ban_chat_member(update.chat.id, new_u.id, until_date=_until_dt(timedelta(minutes=15)))
            except Exception:
                pass
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
    is_cmd = text.startswith("/")

    # Check locks & anti-flood against non-admins
    if user and (locks or antiflood):
        violated = ""
        if "all" in locks:
            violated = "all"
        elif "links" in locks and LINK_RE.search(text):
            violated = "links"
        elif "forwards" in locks and (getattr(message, "forward_date", None) or getattr(message, "forward_origin", None)):
            violated = "forwards"
        elif "media" in locks and bool(
            getattr(message, "photo", None)
            or getattr(message, "video", None)
            or getattr(message, "document", None)
            or getattr(message, "animation", None)
            or getattr(message, "sticker", None)
        ):
            violated = "media"
        elif "bots" in locks and getattr(message, "via_bot", None):
            violated = "bots"

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
                is_adm = getattr(mem, "status", None) in (
                    enums.ChatMemberStatus.OWNER,
                    enums.ChatMemberStatus.ADMINISTRATOR,
                )
            except Exception:
                is_adm = False
            if not is_adm:
                if violated:
                    try:
                        await message.delete()
                    except Exception:
                        pass
                    return
                if flood_hit:
                    try:
                        await client.restrict_chat_member(
                            message.chat.id, user.id, ChatPermissions(),
                            until_date=_until_dt(timedelta(minutes=5)),
                        )
                        await message.reply_text(
                            f"🌊 <b>Anti-Flood:</b> Muted {rich.esc(user.first_name or user.id)} for 5 minutes.",
                            parse_mode=enums.ParseMode.HTML,
                        )
                    except Exception:
                        pass
                    return

    # Keyword filters (non-command messages)
    if fmap and text and not is_cmd:
        low = text.lower()
        for trig, reply_val in fmap.items():
            if re.search(rf"(?<!\w){re.escape(trig)}(?!\w)", low):
                formatted = _format_greeting(reply_val, user, message.chat.title or "")
                try:
                    await message.reply_text(formatted, parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                break


# ───────────────────────── Unified Callback Dispatcher ─────────────────────────
@Client.on_callback_query(filters.regex(r"^plg_gmod:[\w:_-]+$"))
async def group_mod_cb(client: Client, query: CallbackQuery):
    if not query.message or not query.from_user:
        return await query.answer()
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    chat_id = query.message.chat.id
    uid = query.from_user.id

    if action == "tog" and len(parts) >= 3:
        ok, err = await verify_mod_permissions(client, chat_id, uid, privilege="can_change_info", check_bot=False)
        if not ok:
            return await query.answer(f"🔒 {err}"[:190], show_alert=True)
        field_key = parts[2]
        cfg = await get_group_cfg(chat_id)
        if field_key == "welcome":
            cfg = await update_group_cfg(chat_id, {"welcome_on": not cfg.get("welcome_on")})
        elif field_key == "goodbye":
            cfg = await update_group_cfg(chat_id, {"goodbye_on": not cfg.get("goodbye_on")})
        elif field_key == "antiflood":
            cfg = await update_group_cfg(chat_id, {"antiflood": not cfg.get("antiflood")})
        elif field_key == "antiraid":
            active = cfg.get("antiraid_until", 0) > time.time()
            cfg = await update_group_cfg(chat_id, {"antiraid_until": 0.0 if active else time.time() + 1800})
        elif field_key == "joinmode":
            order = ["off", "notify", "auto"]
            nxt = order[(order.index(cfg.get("joinmode", "off")) + 1) % len(order)] if cfg.get("joinmode") in order else "notify"
            cfg = await update_group_cfg(chat_id, {"joinmode": nxt})
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
        key = f"{chat_id}:{target_id}"
        rec = await vdb.db["group_warns"].find_one({"_id": key})
        reasons = list((rec or {}).get("reasons") or [])
        if reasons:
            reasons.pop()
            if reasons:
                await vdb.db["group_warns"].update_one({"_id": key}, {"$set": {"reasons": reasons}})
            else:
                await vdb.db["group_warns"].delete_one({"_id": key})
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
        try:
            if decision == "ok":
                await client.approve_chat_join_request(chat_id, target_id)
                label = "Approved"
            else:
                await client.decline_chat_join_request(chat_id, target_id)
                label = "Declined"
        except Exception as e:
            return await query.answer(f"❌ {e}"[:190], show_alert=True)
        await query.answer(f"{label}!")
        return await query.message.edit_text(
            Doc("🚪", f"Join Request {label}").status("success" if decision == "ok" else "info", f"User {target_id}", f"Handled by {query.from_user.first_name or uid}").classic(),
            parse_mode=enums.ParseMode.HTML,
        )

    if action == "rep" and len(parts) >= 5 and parts[3].isdigit() and parts[4].lstrip("-").isdigit():
        sub, msg_id, target_id = parts[2], int(parts[3]), int(parts[4])
        req_priv = "can_delete_messages" if sub == "del" else "can_restrict_members"
        ok, err = await verify_mod_permissions(
            client, chat_id, uid, privilege=req_priv, target_id=target_id if sub != "del" else None,
        )
        if not ok:
            return await query.answer(f"🔒 {err}"[:190], show_alert=True)
        try:
            await client.delete_messages(chat_id, msg_id)
        except Exception:
            pass
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
        except Exception as e:
            return await query.answer(f"❌ {e}"[:190], show_alert=True)
        await query.answer("Action applied!")
        return await query.message.edit_text(
            Doc("🛡", "Report Resolved").status("success", summary, f"Moderator: {query.from_user.first_name or uid}").classic(),
            parse_mode=enums.ParseMode.HTML,
        )

    return await query.answer()


class Plugin(PluginBase):
    """Lifecycle wrapper for Group Management & Bot API 10.3 Moderation."""

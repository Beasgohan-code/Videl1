"""Telegram handlers for one clone running the Group Management worker.

Standard group commands (``/ban``, ``/kick``, ``/mute``, ``/warn`` …)
with a polished worker-style settings panel at ``/settings``. This worker is its
own codebase for clones — it shares no code with the main bot's
group-management plugin.
"""
from __future__ import annotations

import html
import json
import logging
import re
import secrets
import shlex
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton as TelegramInlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .logic import (
    FLOOD_LIMITS,
    FLOOD_WINDOWS,
    BLACKLIST_MODES,
    GUARD_KINDS,
    LANGUAGES,
    NEVER_DISABLE,
    FloodWatch,
    JoinWatch,
    command_blocked,
    content_violation,
    default_settings,
    in_quiet_hours,
    is_approved,
    is_external_channel_post,
    match_blacklisted_word,
    normalize_blacklist_words,
    lock_kind,
    match_reply,
    next_flood_value,
    normalize_cmd,
    parse_duration,
    parse_filter_command,
    render_greeting,
    render_filter_reply,
    sanitize_settings_import,
    sticker_blocked,
    tr,
)

log = logging.getLogger(__name__)
_ADMIN_STATUSES = (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)

_ON = "🟢"
_OFF = "🔴"


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _bounded_blacklist_duration(value) -> int:
    """Clamp restored/malformed timed-action settings to Telegram-safe bounds."""
    try:
        seconds = int(value)
    except (TypeError, ValueError, OverflowError):
        return 3600
    return max(60, min(seconds, 365 * 24 * 60 * 60))


def _button_style(text: str) -> str:
    label = str(text or "").lower()
    if any(word in label for word in ("🗑", "🔨", "❌", "ban", "delete", "close", "kick")):
        return "danger"
    if any(word in label for word in ("✅", "🟢", "add me", "keep", "save")):
        return "success"
    return "primary"


def _button(text: str, **kwargs) -> TelegramInlineKeyboardButton:
    """Build a consistently styled WzGram button with a classic-client fallback."""
    from core.ui import styled_button

    style = kwargs.pop("style", None) or _button_style(text)
    try:
        return styled_button(text, style=style, **kwargs)
    except (TypeError, ValueError):
        return TelegramInlineKeyboardButton(text, **kwargs)


def _btn(text: str, data: str, style: str | None = None, url: bool = False) -> TelegramInlineKeyboardButton:
    """Shortcut for callback and URL buttons with native color styles."""
    return _button(text, style=style, **({"url": data} if url else {"callback_data": data}))


def _bar(title: str) -> str:
    return f"<b>━━━━━━━━━━━━━━━━━━━\n{title}\n━━━━━━━━━━━━━━━━━━━</b>"


def _table(rows) -> str:
    """Aligned two-column monospace table (Telegram <pre> block)."""
    rows = [(str(label), str(value)) for label, value in rows if value is not None]
    if not rows:
        return ""
    width = max(len(label) for label, _ in rows)
    body = "\n".join(f"{label.ljust(width)}  {value}" for label, value in rows)
    return f"<pre>{_esc(body)}</pre>"


def _onoff(flag) -> str:
    return "✅ on" if flag else "▫️ off"


async def _status(client: Client, chat_id: int, user_id: int):
    try:
        member = await client.get_chat_member(chat_id, user_id)
    except Exception:
        return None
    return getattr(member, "status", None)


async def _is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    return await _status(client, chat_id, user_id) in _ADMIN_STATUSES


def _resolve(message: Message) -> tuple[int | None, str, list[str]]:
    """(target_id, label, remaining args) from reply or first argument."""
    reply = message.reply_to_message
    args = (message.command or [])[1:]
    if reply is not None and reply.from_user is not None:
        user = reply.from_user
        return int(user.id), user.first_name or str(user.id), args
    if args and args[0].lstrip("-").isdigit():
        return int(args[0]), args[0], args[1:]
    return None, "", args


class _BotHandlers:
    def __init__(self, *, bot_id: int, owner_id: int, store):
        self.bot_id = int(bot_id)
        self.owner_id = int(owner_id)
        self.store = store
        self.flood = FloodWatch()
        self.joins = JoinWatch()
        self.connections: dict[int, int] = {}  # admin user_id -> group chat_id (DM mode)
        self._last_welcome: dict[int, int] = {}  # chat_id -> last welcome message_id

    async def _apply_blacklist_action(self, client: Client, message: Message,
                                     settings: dict, *, item: str) -> None:
        """Apply one configured action after a member uses blocked content."""
        mode = str(settings.get("blacklist_mode", "del")).lower()
        if mode not in BLACKLIST_MODES:
            mode = "del"
        if mode == "off":
            return
        user = message.from_user
        if user is None:
            return
        chat_id, user_id = int(message.chat.id), int(user.id)
        label = user.first_name or str(user_id)
        reason = "blocked sticker" if item == "sticker" else "blacklisted word"
        try:
            if mode == "warn":
                try:
                    limit = int(settings.get("warn_limit", 3))
                except (TypeError, ValueError, OverflowError):
                    limit = 3
                limit = max(1, min(limit, 10))
                count, reached = await self.store.add_warn(self.bot_id, chat_id, user_id, limit)
                if not reached:
                    await message.reply_text(
                        f"⚠️ <b>{_esc(label)}</b> warned for blocked content — "
                        f"{count}/{limit}.", parse_mode=enums.ParseMode.HTML)
                    try:
                        await self.store.log_action(self.bot_id, chat_id, "blacklist_warn",
                                                    user_id, None, reason)
                    except Exception:
                        pass
                    return
                # Keep blacklist warnings consistent with /warn escalation.
                mode = str(settings.get("warn_action", "mute")).lower()
                if mode not in ("mute", "kick", "ban"):
                    mode = "mute"
            try:
                await message.delete()
            except Exception:
                if mode == "del":
                    return  # no delete acknowledgement, so do not claim the action succeeded
            if mode == "del":
                action = "del"
            elif mode in ("ban", "tban"):
                until = None
                if mode == "tban":
                    seconds = _bounded_blacklist_duration(settings.get("blacklist_duration"))
                    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                await client.ban_chat_member(chat_id, user_id, until_date=until)
                action = mode
            elif mode == "kick":
                await client.ban_chat_member(chat_id, user_id)
                await client.unban_chat_member(chat_id, user_id)
                action = mode
            else:  # mute / tmute
                until = None
                if mode == "tmute":
                    seconds = _bounded_blacklist_duration(settings.get("blacklist_duration"))
                    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
                await client.restrict_chat_member(
                    chat_id, user_id, ChatPermissions(), until_date=until)
                action = mode
        except Exception as exc:
            log.warning("blacklist action %s uncertain in chat %s (%s)",
                        mode, chat_id, type(exc).__name__)
            return
        try:
            await self.store.log_action(self.bot_id, chat_id, f"blacklist_{action}",
                                        user_id, None, reason)
        except Exception:
            pass
        try:
            await self._notify_owner(client, message, action, user_id, label,
                                     reason, chat_id=chat_id)
        except Exception:
            pass

    # ── settings (worker-style) ────────────────────────────────
    def _settings_markup(self, page: str, s: dict) -> InlineKeyboardMarkup:
        bot = self.bot_id
        def tog(on, label, data):
            return _btn(f"{'✅' if on else '▫️'} {label}", data,
                        style="success" if on else "danger")
        if page == "shield":
            kinds = set(s.get("guard_kinds") or [])
            rows = [[tog(kind in kinds, kind.title(), f"gw:{bot}:kind:{kind}")]
                    for kind in GUARD_KINDS]
            rows.append([tog(bool(s.get("shield")), "Shield enabled", f"gw:{bot}:tog:shield")])
            rows.append([_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")])
            return InlineKeyboardMarkup(rows)
        if page == "greet":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("greetings")), "Greetings enabled", f"gw:{bot}:tog:greetings")],
                [tog(bool(s.get("greet_dm")), "Also DM the welcome", f"gw:{bot}:tog:greet_dm")],
                [tog(bool(s.get("clean_welcome")), "Clean old welcomes", f"gw:{bot}:tog:clean_welcome")],
                [_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "security":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("captcha_on")), "Join CAPTCHA (verify button)", f"gw:{bot}:tog:captcha_on")],
                [tog(bool(s.get("raid_on")), "Anti-raid join shield", f"gw:{bot}:tog:raid_on")],
                [tog(bool(s.get("anti_channel")), "Remove channel posts", f"gw:{bot}:tog:anti_channel")],
                [_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "night":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("night_on")), "Night mode enabled", f"gw:{bot}:tog:night_on")],
                [_button(f"🌙 From {s.get('night_start', 23):02d}:00 UTC", callback_data=f"gw:{bot}:nightstart"),
                 _button(f"☀️ To {s.get('night_end', 7):02d}:00 UTC", callback_data=f"gw:{bot}:nightend")],
                [_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "warn":
            return InlineKeyboardMarkup([
                [_button(f"➖ {s.get('warn_limit', 3)} warns ➕", callback_data=f"gw:{bot}:warnlimit")],
                [_button(f"⚠️ On limit: {s.get('warn_action', 'mute')}", callback_data=f"gw:{bot}:warnaction")],
                [_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "flood":
            on = bool(s.get("flood_limit"))
            flood_label = f"Flood watch · {s['flood_limit']} msgs/{s['flood_window']:g}s" if on else "Flood watch"
            return InlineKeyboardMarkup([
                [tog(on, flood_label, f"gw:{bot}:tog:flood")],
                [_button(f"🎚 Limit: {s.get('flood_limit') or 6} msgs", callback_data=f"gw:{bot}:floodlimit"),
                 _button(f"⏱ Window: {s.get('flood_window', 10):g}s", callback_data=f"gw:{bot}:floodwindow")],
                [_button(f"🌊 On flood: {s.get('flood_action', 'mute')}", callback_data=f"gw:{bot}:floodaction")],
                [_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        return InlineKeyboardMarkup([
            [tog(bool(s.get("shield")), "Shield", f"gw:{bot}:page:shield"),
             tog(bool(s.get("greetings")), "Greetings", f"gw:{bot}:tog:greetings")],
            [_button("🛡 Shield types ›", callback_data=f"gw:{bot}:page:shield"),
             _button("⚠️ Warnings ›", callback_data=f"gw:{bot}:page:warn")],
            [tog(bool(s.get("dm_alerts")), "DM alerts to owner", f"gw:{bot}:tog:dm_alerts"),
             tog(bool(s.get("night_on")), "Night mode", f"gw:{bot}:page:night")],
            [_button("👋 Greetings ›", callback_data=f"gw:{bot}:page:greet"),
             _button("🌙 Night hours ›", callback_data=f"gw:{bot}:page:night")],
            [_button("🤖 Security (CAPTCHA · anti-raid) ›", callback_data=f"gw:{bot}:page:security")],
            [tog(bool(s.get("clean_service")), "Clean service msgs", f"gw:{bot}:tog:clean_service"),
             _button(f"🌐 {str(s.get('lang', 'en')).upper()}", callback_data=f"gw:{bot}:lang")],
            [_button("🌊 Flood watch ›", callback_data=f"gw:{bot}:page:flood"),
             _button("❌ Close", callback_data=f"gw:{bot}:close")],
        ])

    def _settings_text(self, page: str, s: dict, chat_title: str = "") -> str:
        head = _bar(f"⚙️ {tr(s, 'settings')}") + (f"\n<i>{_esc(chat_title)}</i>" if chat_title else "")
        if page == "security":
            return (f"{head}\n\n🤖 <b>Security</b>\n"
                    f"CAPTCHA: {'on' if s.get('captcha_on') else 'off'} · "
                    f"Anti-raid: {'on' if s.get('raid_on') else 'off'} · "
                    f"Anti-channel: {'on' if s.get('anti_channel') else 'off'}\n"
                    "<i>With CAPTCHA, new members stay muted until they tap the verify button. "
                    "The anti-raid shield temporarily bans joiners during join bursts. "
                    "Anti-channel removes external channel-profile posts; anonymous group admins are exempt.</i>")
        if page == "greet":
            return (f"{head}\n\n👋 <b>Greetings</b> — {'on' if s.get('greetings') else 'off'}\n"
                    "<i>Set texts with /setwelcome and /setgoodbye (reply to a photo/sticker "
                    "to use it as the welcome). Placeholders: {name} {chat} {id}. "
                    "Preview with /welcome.</i>")
        if page == "night":
            state = (f"{s.get('night_start', 23):02d}:00–{s.get('night_end', 7):02d}:00 UTC"
                     if s.get("night_on") else "off")
            return (f"{head}\n\n🌙 <b>Night mode</b> — {_esc(state)}\n"
                    "<i>During night hours, members' messages are quietly deleted. Admins are exempt.</i>")
        if page == "shield":
            kinds = ", ".join(s.get("guard_kinds") or []) or "none"
            return (f"{head}\n\n🛡 <b>Shield</b> — {'on' if s.get('shield') else 'off'}\n"
                    f"Blocking: {_esc(kinds)}\n\n<i>Tap a type to toggle it. Turning a type on enables the shield.</i>")
        if page == "greet":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("greetings")), "Greetings enabled", f"gw:{bot}:tog:greetings")],
                [tog(bool(s.get("greet_dm")), "Also DM the welcome", f"gw:{bot}:tog:greet_dm")],
                [tog(bool(s.get("clean_welcome")), "Clean old welcomes", f"gw:{bot}:tog:clean_welcome")],
                [_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "security":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("captcha_on")), "Join CAPTCHA (verify button)", f"gw:{bot}:tog:captcha_on")],
                [tog(bool(s.get("raid_on")), "Anti-raid join shield", f"gw:{bot}:tog:raid_on")],
                [_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "night":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("night_on")), "Night mode enabled", f"gw:{bot}:tog:night_on")],
                [_button(f"🌙 From {s.get('night_start', 23):02d}:00 UTC", callback_data=f"gw:{bot}:nightstart"),
                 _button(f"☀️ To {s.get('night_end', 7):02d}:00 UTC", callback_data=f"gw:{bot}:nightend")],
                [_button("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "warn":
            return (f"{head}\n\n⚠️ <b>Warnings</b>\n"
                    f"Members are {s.get('warn_action', 'mute')}d after <b>{s.get('warn_limit', 3)}</b> warns.\n"
                    "<i>Tap the counter to change the limit (2–10), or the action to cycle mute → kick → ban.</i>")
        if page == "flood":
            state = (f"{s['flood_limit']} messages in {s['flood_window']:g}s → {s['flood_action']}"
                     if s.get("flood_limit") else "off")
            return (f"{head}\n\n🌊 <b>Flood watch</b> — {_esc(state)}\n"
                    "<i>Members (never admins) who flood are acted on automatically.</i>")
        kinds = ", ".join(s.get("guard_kinds") or []) or "none"
        flood = (f"{s['flood_limit']}/{s['flood_window']:g}s → {s['flood_action']}"
                 if s.get("flood_limit") else "off")
        table = _table([
            ("🛡 Shield", f"{_onoff(s.get('shield'))} · {kinds}"),
            ("👋 Greetings", _onoff(s.get("greetings"))),
            ("⚠️ Warnings", f"{s.get('warn_limit', 3)} → {s.get('warn_action', 'mute')}"),
            ("🌊 Flood watch", flood),
            ("🌙 Night mode", _onoff(s.get("night_on"))),
            ("🤖 CAPTCHA", _onoff(s.get("captcha_on"))),
            ("🚨 Anti-raid", _onoff(s.get("raid_on"))),
            ("📜 Rules", "✅ set" if s.get("rules") else "▫️ not set"),
            ("💬 Auto-replies", len(s.get("replies") or {})),
            ("📝 Notes", len(s.get("notes") or {})),
            ("🤝 Approved", len(s.get("approved") or {})),
            ("🚫 Disabled", ", ".join(s.get("disabled_cmds") or []) or "none"),
            ("📋 Log chat", "✅ set" if s.get("log_chat") else "▫️ off"),
            ("🧹 Clean service", _onoff(s.get("clean_service"))),
            ("📩 DM alerts", _onoff(s.get("dm_alerts"))),
            ("🌐 Language", s.get("lang", "en")),
        ])
        return f"{head}\n\n{table}\n<i>{tr(s, 'admins_only')} Tap a button below to change things.</i>"

    # ── rich start pages ─────────────────────────────────────────
    def _start_text(self, page: str = "home") -> str:
        """Classic HTML compatibility view; Rich Doc is the single source."""
        return self._start_doc(page).classic()

    def _start_doc(self, page: str = "home"):
        from core.rich import Doc

        if page == "help":
            return (Doc("📖", "Group Management · Help", "Fast command reference")
                    .text("Most settings are managed from <code>/settings</code> in your group. "
                          "Commands shown here are for admins unless noted.")
                    .h("🛡", "Moderation")
                    .table([
                        ("/warn · /unwarn", "Warn or clear warnings"),
                        ("/mute · /tmute · /unmute", "Mute members, optionally timed"),
                        ("/kick · /ban · /tban", "Remove or ban members"),
                        ("/sban · /unban", "Silent ban and unban"),
                        ("/purge · /pin · /unpin", "Clean messages and manage pins"),
                    ], header=("Command", "What it does"))
                    .h("👑", "Admin tools")
                    .table([
                        ("/promote · /demote", "Manage admins"),
                        ("/title · /chatinfo", "Group title and information"),
                        ("/lock links · /unlock media · /locks", "Control links, media and other content"),
                        ("/adminlist · /stats", "Roster and group stats"),
                        ("/disable · /disabled", "Manage command availability"),
                    ], header=("Command", "What it does"))
                    .h("⚙️", "Group care")
                    .table([
                        ("/settings", "Buttons for shield, welcomes, flood, night mode and more"),
                        ("/rules · /setrules", "Read or update group rules"),
                        ("/filter · /mfilter · /stop · /filters", "Text and media auto-replies"),
                        ("/stopall", "Remove all auto-replies (clone owner only)"),
                        ("/save · /get · #trigger", "Saved notes"),
                        ("/welcome · /setwelcome", "Welcome messages and media"),
                    ], header=("Command", "What it does"))
                    .h("🤝", "Trust & safety")
                    .table([
                        ("/approve · /approved", "Manage trusted members"),
                        ("/report", "Reply to report a message to admins"),
                        ("/fedban · /fedinfo", "Shared federation bans"),
                        ("/modlog · /setlog", "Moderation log and destination"),
                        ("/afk · /whois · /id", "Away status, member cards and IDs"),
                        ("/export · /import", "Back up and restore group settings"),
                    ], header=("Command", "What it does"))
                    .h("✨", "Filters & discovery")
                    .table([
                        ("/blacklist · /addblacklist · /unblacklist", "Manage blocked words"),
                        ("/blsticker · /addblsticker · /unblsticker", "Manage blocked stickers"),
                        ("/antichannel · /cleaner", "Channel-post and cleanup controls"),
                        ("/anime · /manga · /character", "Search AniList titles and people"),
                        ("/browse · /airing · /topanime", "Explore AniList releases and ratings"),
                        ("/gettags · /anisettings", "AniList genres and lookup help"),
                    ], header=("Command", "What it does"))
                    .footer("Times: 30m · 12h · 7d · 2w. Open /settings in a group to manage controls."))
        if page == "about":
            return (Doc("ℹ️", "About this Group Management clone", "A practical group safety assistant")
                    .text("A full group safety toolkit you can configure from buttons.")
                    .table([
                        ("🛡 Shield & locks", "Links, media, bots and forwards"),
                        ("⚠️ Warnings", "Escalation to mute, kick or ban"),
                        ("🌊 Flood & night", "Anti-spam and quiet hours"),
                        ("🤖 CAPTCHA · 🚨 Anti-raid", "Join checks and burst protection"),
                        ("👋 Greetings", "Welcome and goodbye messages"),
                        ("📝 Notes & filters", "Saved notes and automatic replies"),
                        ("🌐 Federations", "Shared bans across groups"),
                        ("📩 DM alerts", "Admin notifications with moderation actions"),
                    ], header=("Feature", "Overview"))
                    .footer("Open /settings in your group to configure the bot."))
        return (Doc("🛡️", "Group Management", "A safer, friendlier group — managed from one place")
                .text("👋 <b>Hey! I help keep your groups safe and welcoming.</b>")
                .table([
                    ("1 · Add", "Add me to a group"),
                    ("2 · Admin", "Grant the permissions I need"),
                    ("3 · Configure", "Open /settings in the group"),
                ], header=("Step", "Get started"), caption="Quick setup")
                .text("🛡 Spam, floods, raids and bad members are easier to manage with the right permissions.")
                .footer("Tap Help for commands, or Support if you need a hand."))

    def _settings_doc(self, page: str, s: dict, chat_title: str = ""):
        from core.rich import Doc

        title = tr(s, "settings")
        doc = Doc("⚙️", title, chat_title or "Group controls")
        if page == "security":
            doc.h("🤖", "Security")
            doc.table([
                ("Join CAPTCHA", _onoff(s.get("captcha_on"))),
                ("Anti-raid", _onoff(s.get("raid_on"))),
                ("Anti-channel", _onoff(s.get("anti_channel"))),
            ], header=("Protection", "State"))
            doc.text("CAPTCHA holds new members until they verify. Anti-raid responds to join bursts. "
                     "Anti-channel removes external channel-profile posts; anonymous group admins are exempt.")
        elif page == "greet":
            doc.h("👋", "Greetings")
            doc.table([
                ("Welcome messages", _onoff(s.get("greetings"))),
                ("Send welcome by DM", _onoff(s.get("greet_dm"))),
                ("Clean old welcomes", _onoff(s.get("clean_welcome"))),
            ], header=("Option", "State"))
            doc.text("Use /setwelcome and /setgoodbye. Placeholders: {name}, {chat}, {id}. Preview with /welcome.")
        elif page == "night":
            state = (f"{s.get('night_start', 23):02d}:00–{s.get('night_end', 7):02d}:00 UTC"
                     if s.get("night_on") else "Off")
            doc.h("🌙", "Night mode")
            doc.table([("Quiet hours", state), ("Admins", "Exempt")], header=("Setting", "Value"))
            doc.text("During quiet hours, member messages are quietly deleted.")
        elif page == "shield":
            doc.h("🛡", "Content shield")
            doc.table([
                ("Shield", _onoff(s.get("shield"))),
                ("Blocked types", ", ".join(s.get("guard_kinds") or []) or "None"),
            ], header=("Setting", "Value"))
            doc.text("Tap a type below to toggle it. Enabling a type also enables the shield.")
        elif page == "warn":
            doc.h("⚠️", "Warning policy")
            doc.table([
                ("Escalate after", f"{s.get('warn_limit', 3)} warnings"),
                ("Action", s.get("warn_action", "mute")),
            ], header=("Setting", "Value"))
            doc.text("Change the warning count or action with the buttons below.")
        elif page == "flood":
            value = (f"{s.get('flood_limit')} messages in {s.get('flood_window', 10):g}s"
                     if s.get("flood_limit") else "Off")
            doc.h("🌊", "Flood watch")
            doc.table([
                ("Threshold", value),
                ("Action", s.get("flood_action", "mute")),
            ], header=("Setting", "Value"))
            doc.text("Only members are automatically acted on; admins are exempt.")
        else:
            kinds = ", ".join(s.get("guard_kinds") or []) or "None"
            flood = (f"{s.get('flood_limit')} msgs / {s.get('flood_window', 10):g}s"
                     if s.get("flood_limit") else "Off")
            doc.table([
                ("🛡 Shield", f"{_onoff(s.get('shield'))} · {kinds}"),
                ("👋 Greetings", _onoff(s.get("greetings"))),
                ("⚠️ Warning policy", f"{s.get('warn_limit', 3)} → {s.get('warn_action', 'mute')}"),
                ("🌊 Flood watch", f"{flood} → {s.get('flood_action', 'mute')}"),
                ("🌙 Night mode", _onoff(s.get("night_on"))),
                ("🤖 CAPTCHA", _onoff(s.get("captcha_on"))),
                ("🚨 Anti-raid", _onoff(s.get("raid_on"))),
                ("📜 Rules", "Set" if s.get("rules") else "Not set"),
                ("💬 Auto-replies", len(s.get("replies") or {})),
                ("📝 Notes", len(s.get("notes") or {})),
                ("🤝 Approved", len(s.get("approved") or {})),
                ("📋 Log chat", "Set" if s.get("log_chat") else "Off"),
                ("🧹 Clean service", _onoff(s.get("clean_service"))),
                ("📩 DM alerts", _onoff(s.get("dm_alerts"))),
                ("🌐 Language", s.get("lang", "en").upper()),
            ], header=("Control", "Current setting"))
            doc.text(f"{tr(s, 'admins_only')} Use the buttons below to tune this group.")
        doc.footer("Changes apply to this group only. Access to Telegram controls depends on bot permissions.")
        return doc

    @staticmethod
    def _public_start_link(value: str) -> str:
        """Accept configured public HTTPS URLs, not arbitrary callback schemes."""
        if not isinstance(value, str):
            return ""
        value = value.strip()
        try:
            parsed = urlsplit(value)
            if (parsed.scheme.lower() != "https" or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None):
                return ""
            return value
        except (TypeError, ValueError):
            return ""

    def _start_markup(self, bot_username: str) -> InlineKeyboardMarkup:
        import config

        username = (bot_username.strip().lstrip("@") if isinstance(bot_username, str) else "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username, flags=re.ASCII):
            username = ""
        rows = []
        if username:
            rows.append([_btn("➕ Add me to your group",
                              f"https://t.me/{username}?startgroup=true",
                              style="success", url=True)])
        rows.append([
            _btn("📖 Help", f"gw:{self.bot_id}:nav:help", style="primary"),
            _btn("ℹ️ About", f"gw:{self.bot_id}:nav:about", style="primary"),
        ])
        rows.append([
            _btn("👤 Owner", f"tg://user?id={self.owner_id}", url=True),
            _btn("🏠 Home", f"gw:{self.bot_id}:nav:home"),
        ])
        support_url = (self._public_start_link(getattr(config, "SUPPORT_URL", ""))
                       or self._public_start_link(getattr(config, "CONTACT_URL", "")))
        if support_url:
            support_button = _btn("💬 Support", support_url, style="primary", url=True)
        else:
            support_button = _btn("💬 Support", f"tg://user?id={self.owner_id}", url=True)
        links = [support_button]
        updates_url = (self._public_start_link(getattr(config, "UPDATES_URL", ""))
                       or "https://github.com/Beasgohan-code/Videl1/releases")
        links.append(_btn("📢 Updates", updates_url, style="primary", url=True))
        rows.append(links)
        return InlineKeyboardMarkup(rows)

    # ── moderation core ────────────────────────────────────────────
    async def _act(self, client: Client, message: Message, action: str):
        silent = action.startswith("s")
        base = action[1:] if silent and action != "sban" else action
        if action == "sban":
            base = "ban"
        chat_id = chat_id
        if message.chat.type == enums.ChatType.PRIVATE:
            chat_id = self.connections.get(message.from_user.id)
            if chat_id is None:
                return await message.reply_text(
                    "🔌 <b>Not connected.</b> Use /connect &lt;group_id&gt; first, then run "
                    "moderation commands here by user ID.",
                    parse_mode=enums.ParseMode.HTML)
        if not await _is_admin(client, chat_id, message.from_user.id):
            return await message.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
        target_id, label, rest = _resolve(message)
        if target_id is None:
            return await message.reply_text(
                "👤 Reply to a member, or give a user ID — e.g. <code>/ban 123456 reason</code>.",
                parse_mode=enums.ParseMode.HTML)
        if await _status(client, chat_id, target_id) in _ADMIN_STATUSES:
            return await message.reply_text("🛡 That member is an admin — I won't act on them.")
        settings = await self.store.get_settings(self.bot_id, chat_id)
        duration = None
        if rest and base in ("tban", "tmute"):
            duration = parse_duration(rest[0])
            if duration is None:
                return await message.reply_text("⏱ Give a duration like <code>30m</code>, <code>12h</code> or <code>7d</code>.",
                                                parse_mode=enums.ParseMode.HTML)
            rest = rest[1:]
        reason = " ".join(rest)[:200]
        until = datetime.now(timezone.utc) + duration if duration else None
        note = ""
        try:
            if base == "warn":
                count, reached = await self.store.add_warn(
                    self.bot_id, chat_id, target_id, settings.get("warn_limit", 3))
                if not reached:
                    if not silent:
                        await message.reply_text(
                            f"⚠️ <b>{_esc(label)}</b> warned — {count}/{settings.get('warn_limit', 3)}."
                            + (f"\n📝 {_esc(reason)}" if reason else ""),
                            parse_mode=enums.ParseMode.HTML)
                    return
                base = settings.get("warn_action", "mute")
                note = "Warning limit reached."
            if base == "unwarn":
                await self.store.reset_warns(self.bot_id, chat_id, target_id)
                return await message.reply_text(f"✅ Warnings cleared for <b>{_esc(label)}</b>.",
                                                parse_mode=enums.ParseMode.HTML)
            if base in ("ban", "tban"):
                await client.ban_chat_member(chat_id, target_id, until_date=until)
            elif base == "kick":
                await client.ban_chat_member(chat_id, target_id)
                await client.unban_chat_member(chat_id, target_id)
            elif base == "unban":
                await client.unban_chat_member(chat_id, target_id)
            elif base in ("mute", "tmute"):
                await client.restrict_chat_member(
                    chat_id, target_id, ChatPermissions(), until_date=until)
            elif base == "unmute":
                await client.restrict_chat_member(
                    chat_id, target_id,
                    ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                    can_send_other_messages=True, can_add_web_page_previews=True))
            else:
                return await message.reply_text("Unknown action.")
        except Exception as exc:
            return await message.reply_text(
                f"❌ <b>Telegram refused that.</b>\n{_esc(str(exc)[:120])}",
                parse_mode=enums.ParseMode.HTML)
        try:
            await client.send_reaction(message.chat.id, message.id,
                                       {"warn": "⚠️", "ban": "🔨", "tban": "🔨", "sban": "🔨",
                                        "kick": "👢", "mute": "🤐", "tmute": "🤐"}.get(base, "✅"))
        except Exception:
            pass  # reactions are decoration; never fail the action over them
        try:
            await self.store.log_action(self.bot_id, chat_id, base, target_id,
                                        message.from_user.id, reason)
        except Exception:
            pass
        await self._notify_owner(client, message, base, target_id, label, reason, chat_id=chat_id)
        if silent:
            try:
                await message.delete()
            except Exception:
                pass
            return
        when = f" · {duration}" if duration else ""
        await message.reply_text(
            f"✅ <b>{_esc(base)}</b> → {_esc(label)}{when}."
            + (f" {note}" if note else "")
            + (f"\n📝 {_esc(reason)}" if reason else ""),
            parse_mode=enums.ParseMode.HTML)

    async def _notify_owner(self, client: Client, message: Message, action: str,
                            target_id: int, label: str, reason: str = "",
                            chat_id: int | None = None) -> None:
        """Best-effort DM to the clone owner with an undo button."""
        settings = await self.store.get_settings(self.bot_id, chat_id or message.chat.id)
        if not settings.get("dm_alerts") or self.owner_id == message.from_user.id:
            return
        undo = {"ban": "unban", "tban": "unban", "mute": "unmute", "tmute": "unmute"}.get(action)
        markup = None
        if undo:
            markup = InlineKeyboardMarkup([[
                _button(f"↩️ Undo ({undo})",
                                     callback_data=f"gw:{self.bot_id}:undo:{chat_id or message.chat.id}:{target_id}:{undo}"),
                _button("✅ Keep", callback_data=f"gw:{self.bot_id}:keep"),
            ]])
        if settings.get("log_chat"):
            try:
                await client.send_message(
                    int(settings["log_chat"]),
                    f"📋 <b>Moderation log</b>\n\n"
                    f"Group: <b>{_esc(getattr(message.chat, 'title', None) or 'connected group')}</b>\n"
                    f"Action: <b>{_esc(action)}</b> → {_esc(label)} ({target_id})\n"
                    f"By: {_esc(message.from_user.first_name or message.from_user.id)}"
                    + (f"\n📝 {_esc(reason)}" if reason else ""),
                    parse_mode=enums.ParseMode.HTML)
            except Exception:
                pass
        try:
            await client.send_message(
                self.owner_id,
                f"📩 <b>Moderation alert</b>\n\n"
                f"Group: <b>{_esc(getattr(message.chat, 'title', None) or 'connected group')}</b>\n"
                f"Action: <b>{_esc(action)}</b> → {_esc(label)} ({target_id})\n"
                f"By: {_esc(message.from_user.first_name or message.from_user.id)}"
                + (f"\n📝 {_esc(reason)}" if reason else ""),
                parse_mode=enums.ParseMode.HTML, reply_markup=markup)
        except Exception:
            pass  # owner may not have started the bot; alerts are best-effort

    # ── registration ───────────────────────────────────────────────
    def attach(self, app: Client) -> None:
        bot_id = self.bot_id
        store = self.store

        @app.on_message(filters.private & filters.command("start"))
        async def _start(c, m: Message):
            me = getattr(c, "me", None)
            username = getattr(me, "username", "") or ""
            from core import rich
            await rich.reply(m, self._start_doc("home"),
                             reply_markup=self._start_markup(username))

        @app.on_message(filters.private & filters.command("help"))
        async def _dm_help(c, m: Message):
            me = getattr(c, "me", None)
            username = getattr(me, "username", "") or ""
            from core import rich
            await rich.reply(m, self._start_doc("help"),
                             reply_markup=self._start_markup(username))

        @app.on_message(filters.private & filters.command(["connect", "disconnect", "connection"]))
        async def _connect(c, m: Message):
            cmd = (m.command or ["connection"])[0].lower()
            uid = m.from_user.id
            if cmd == "disconnect":
                self.connections.pop(uid, None)
                return await m.reply_text("🔌 Disconnected.", parse_mode=enums.ParseMode.HTML)
            if cmd == "connection":
                chat_id = self.connections.get(uid)
                return await m.reply_text(
                    f"🔌 Connected to group <code>{chat_id}</code>." if chat_id
                    else "🔌 Not connected. Use /connect &lt;group_id&gt;.",
                    parse_mode=enums.ParseMode.HTML)
            args = (m.command or [])[1:]
            if len(args) != 1 or not args[0].lstrip("-").isdigit():
                return await m.reply_text(
                    "Usage: <code>/connect -1001234567890</code>\n"
                    "<i>Tip: open /settings in the group to see its ID, or forward a group message to @userinfobot-style tools.</i>",
                    parse_mode=enums.ParseMode.HTML)
            chat_id = int(args[0])
            if not await _is_admin(c, chat_id, uid):
                return await m.reply_text(
                    "🔒 I couldn't confirm you're an admin there (make sure I'm in the group too).",
                    parse_mode=enums.ParseMode.HTML)
            self.connections[uid] = chat_id
            await m.reply_text(
                f"🔌 <b>Connected to {chat_id}.</b>\n"
                "Now moderation commands here act on that group (use user IDs): "
                "/warn, /mute, /kick, /ban, /unwarn…\n"
                "Check settings with /settings here · /disconnect to stop.",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.private & filters.command("settings"))
        async def _dm_settings(c, m: Message):
            chat_id = self.connections.get(m.from_user.id)
            if chat_id is None:
                return await m.reply_text("🔌 Not connected. Use /connect &lt;group_id&gt; first.",
                                          parse_mode=enums.ParseMode.HTML)
            if not await _is_admin(c, chat_id, m.from_user.id):
                self.connections.pop(m.from_user.id, None)
                return await m.reply_text("🔒 You're no longer an admin there — disconnected.",
                                          parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, chat_id)
            from core import rich
            doc = self._settings_doc("main", s, str(chat_id))
            doc.text("ℹ️ For interactive controls, open /settings in the group.")
            await rich.reply(m, doc)

        @app.on_message(filters.group & filters.command("help"))
        async def _help(c, m: Message):
            me = getattr(c, "me", None)
            username = getattr(me, "username", "") or ""
            from core import rich
            await rich.reply(m, self._start_doc("help"),
                             reply_markup=self._start_markup(username))

        @app.on_message(filters.group & filters.command("antichannel"))
        async def _antichannel(c, m: Message):
            if m.from_user is None or not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            args = (m.command or [])[1:]
            settings = await store.get_settings(bot_id, m.chat.id)
            if len(args) > 1 or (args and args[0].lower() not in ("on", "off")):
                return await m.reply_text("Use <code>/antichannel</code>, <code>/antichannel on</code> or <code>/antichannel off</code>.",
                                          parse_mode=enums.ParseMode.HTML)
            enabled = (not bool(settings.get("anti_channel")) if not args
                       else args[0].lower() == "on")
            settings = await store.update_settings(bot_id, m.chat.id, {"anti_channel": enabled})
            state = "enabled" if settings.get("anti_channel") else "disabled"
            await m.reply_text(
                f"🛡 <b>Anti-channel {state}.</b> The bot will try to remove external channel-profile posts. "
                "Anonymous posts made as this group are exempt; deletion requires bot delete-message rights.",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("settings"))
        async def _settings(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, m.chat.id)
            from core import rich
            await rich.reply(m, self._settings_doc("main", s, m.chat.title or ""),
                             reply_markup=self._settings_markup("main", s))

        @app.on_callback_query(filters.regex(rf"^gw:{bot_id}:"))
        async def _settings_cb(c, q: CallbackQuery):
            parts = (q.data or "").split(":")
            action = parts[2] if len(parts) > 2 else ""
            if action == "nav":
                page = parts[3] if len(parts) > 3 else "home"
                me = getattr(c, "me", None)
                username = getattr(me, "username", "") or ""
                try:
                    from core import rich
                    await rich.edit(q.message, self._start_doc(page),
                                    reply_markup=self._start_markup(username))
                except Exception:
                    pass
                return await q.answer()
            if action == "rules":
                try:
                    target_chat = int(parts[3])
                except Exception:
                    return await q.answer("Bad button.", show_alert=True)
                s = await store.get_settings(bot_id, target_chat)
                if not s.get("rules"):
                    return await q.answer("No rules set yet.", show_alert=True)
                try:
                    await c.send_message(target_chat, f"{_bar('📜 GROUP RULES')}\n\n{_esc(s['rules'])}",
                                         parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                return await q.answer("Rules posted 📜")
            if action == "close":
                return await q.message.delete()
            if not await _is_admin(c, q.message.chat.id, q.from_user.id):
                return await q.answer("Admins only.", show_alert=True)
            if action == "verify":
                # data: gw:{bot}:verify:{chat}:{user}
                try:
                    target_chat, target_user = int(parts[3]), int(parts[4])
                except Exception:
                    return await q.answer("Bad verification.", show_alert=True)
                if q.from_user.id != target_user:
                    return await q.answer("This button is only for the new member.", show_alert=True)
                try:
                    await c.restrict_chat_member(
                        target_chat, target_user,
                        ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                        can_send_other_messages=True, can_add_web_page_previews=True))
                except Exception as exc:
                    return await q.answer(f"Could not verify: {str(exc)[:60]}", show_alert=True)
                try:
                    await q.message.edit_text("✅ <b>Verified — welcome!</b> You can chat now.",
                                              parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                return await q.answer("Verified ✅")
            if action == "keep":
                return await q.answer("Kept. ✅")
            if action == "act":
                # data: gw:{bot}:act:{chat}:{user}:{verb}
                try:
                    target_chat, target_user, verb = int(parts[3]), int(parts[4]), parts[5]
                except Exception:
                    return await q.answer("Bad action.", show_alert=True)
                if not await _is_admin(c, target_chat, q.from_user.id):
                    return await q.answer("Admins only.", show_alert=True)
                if await _status(c, target_chat, target_user) in _ADMIN_STATUSES:
                    return await q.answer("That member is an admin.", show_alert=True)
                try:
                    if verb == "warn":
                        s = await store.get_settings(bot_id, target_chat)
                        count, reached = await store.add_warn(bot_id, target_chat, target_user,
                                                               s.get("warn_limit", 3))
                        if reached:
                            verb = s.get("warn_action", "mute")
                        else:
                            await q.answer(f"Warned ({count}/{s.get('warn_limit', 3)}) ⚠️")
                            return
                    if verb == "mute":
                        await c.restrict_chat_member(target_chat, target_user, ChatPermissions())
                    elif verb == "kick":
                        await c.ban_chat_member(target_chat, target_user)
                        await c.unban_chat_member(target_chat, target_user)
                    elif verb == "ban":
                        await c.ban_chat_member(target_chat, target_user)
                    else:
                        return await q.answer("Unknown action.", show_alert=True)
                except Exception as exc:
                    return await q.answer(f"Failed: {str(exc)[:60]}", show_alert=True)
                await q.answer("Done ✅")
                return
            if action in ("undo", "rep"):
                # data: gw:{bot}:undo:{chat}:{user}:{undo} / gw:{bot}:rep:{chat}:{msg}:{user}:{verb}
                try:
                    target_chat = int(parts[3])
                except Exception:
                    return await q.answer("Bad alert.", show_alert=True)
                if not await _is_admin(c, target_chat, q.from_user.id) and q.from_user.id != self.owner_id:
                    return await q.answer("Admins only.", show_alert=True)
                try:
                    if action == "undo":
                        target_user, verb = int(parts[4]), parts[5]
                        if verb == "unban":
                            await c.unban_chat_member(target_chat, target_user)
                        else:
                            await c.restrict_chat_member(
                                target_chat, target_user,
                                ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                                can_send_other_messages=True, can_add_web_page_previews=True))
                        await q.answer("Undone ↩️")
                    else:
                        msg_id, target_user, verb = int(parts[4]), int(parts[5]), parts[6]
                        if verb == "del":
                            await c.delete_messages(target_chat, [msg_id])
                        else:
                            await c.ban_chat_member(target_chat, target_user)
                        await q.answer("Done ✅")
                except Exception as exc:
                    return await q.answer(f"Failed: {str(exc)[:60]}", show_alert=True)
                try:
                    await q.message.edit_text(q.message.text + "\n\n<i>Handled ✅</i>",
                                              parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
                return
            chat_id = q.message.chat.id
            s = await store.get_settings(bot_id, chat_id)
            page = "main"
            if action == "page" and len(parts) > 3:
                page = parts[3]
            elif action == "tog" and len(parts) > 3:
                key = parts[3]
                if key == "shield":
                    s = await store.update_settings(bot_id, chat_id, {"shield": not s.get("shield")})
                    page = "shield"
                elif key == "greetings":
                    s = await store.update_settings(bot_id, chat_id, {"greetings": not s.get("greetings")})
                elif key == "dm_alerts":
                    s = await store.update_settings(bot_id, chat_id, {"dm_alerts": not s.get("dm_alerts")})
                elif key == "flood":
                    s = await store.update_settings(bot_id, chat_id,
                                                    {"flood_limit": 0 if s.get("flood_limit") else 6})
                    page = "flood"
                elif key == "night_on":
                    s = await store.update_settings(bot_id, chat_id, {"night_on": not s.get("night_on")})
                    page = "night"
                elif key == "clean_service":
                    s = await store.update_settings(bot_id, chat_id, {"clean_service": not s.get("clean_service")})
                elif key in ("captcha_on", "raid_on", "anti_channel"):
                    s = await store.update_settings(bot_id, chat_id, {key: not s.get(key)})
                    page = "security"
                elif key == "greet_dm":
                    s = await store.update_settings(bot_id, chat_id, {"greet_dm": not s.get("greet_dm")})
                    page = "greet"
                elif key == "clean_welcome":
                    s = await store.update_settings(bot_id, chat_id, {"clean_welcome": not s.get("clean_welcome")})
                    page = "greet"
            elif action == "kind" and len(parts) > 3 and parts[3] in GUARD_KINDS:
                kinds = set(s.get("guard_kinds") or [])
                kinds.symmetric_difference_update({parts[3]})
                s = await store.update_settings(bot_id, chat_id,
                                                {"guard_kinds": sorted(kinds), "shield": True})
                page = "shield"
            elif action == "warnaction":
                order = ["mute", "kick", "ban"]
                nxt = order[(order.index(s.get("warn_action", "mute")) + 1) % len(order)]
                s = await store.update_settings(bot_id, chat_id, {"warn_action": nxt})
                page = "warn"
            elif action == "warnlimit":
                limit = int(s.get("warn_limit", 3)) + 1
                if limit > 10:
                    limit = 2
                s = await store.update_settings(bot_id, chat_id, {"warn_limit": limit})
                page = "warn"
            elif action == "lang":
                order = list(LANGUAGES)
                current = s.get("lang") or "en"
                nxt = order[(order.index(current) + 1) % len(order)] if current in order else "en"
                s = await store.update_settings(bot_id, chat_id, {"lang": nxt})
            elif action == "nightstart":
                s = await store.update_settings(bot_id, chat_id,
                                                {"night_start": (int(s.get("night_start", 23)) + 1) % 24})
                page = "night"
            elif action == "nightend":
                s = await store.update_settings(bot_id, chat_id,
                                                {"night_end": (int(s.get("night_end", 7)) + 1) % 24})
                page = "night"
            elif action == "floodlimit":
                value = next_flood_value(s.get("flood_limit", 6), FLOOD_LIMITS, 6)
                s = await store.update_settings(bot_id, chat_id, {"flood_limit": value})
                page = "flood"
            elif action == "floodwindow":
                value = next_flood_value(s.get("flood_window", 10), FLOOD_WINDOWS, 10)
                s = await store.update_settings(bot_id, chat_id, {"flood_window": float(value)})
                page = "flood"
            elif action == "floodaction":
                order = ["mute", "kick", "ban"]
                current = s.get("flood_action", "mute")
                nxt = order[(order.index(current) + 1) % len(order)] if current in order else "mute"
                s = await store.update_settings(bot_id, chat_id, {"flood_action": nxt})
                page = "flood"
            else:
                return await q.answer("Unknown control.", show_alert=True)
            from core import rich
            await rich.edit(q.message,
                            self._settings_doc(page, s, q.message.chat.title or ""),
                            reply_markup=self._settings_markup(page, s))
            await q.answer("Saved ✅")

        @app.on_message((filters.group | filters.private) & filters.command(
            ["warn", "unwarn", "ban", "tban", "sban", "unban",
             "kick", "mute", "tmute", "unmute"]))
        async def _mod(c, m: Message):
            await self._act(c, m, (m.command or ["warn"])[0].lower())

        @app.on_message(filters.group & filters.command("purge"))
        async def _purge(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            args = (m.command or [])[1:]
            if len(args) != 1 or not args[0].isdigit() or not 1 <= int(args[0]) <= 100:
                return await m.reply_text("Usage: <code>/purge &lt;1–100&gt;</code>",
                                          parse_mode=enums.ParseMode.HTML)
            ids = []
            async for item in c.get_chat_history(m.chat.id, limit=int(args[0]) + 1):
                if item.id != m.id:
                    ids.append(item.id)
                if len(ids) >= int(args[0]):
                    break
            if ids:
                await c.delete_messages(m.chat.id, ids)
            await m.reply_text(f"🧹 <b>Purged {len(ids)} messages.</b>", parse_mode=enums.ParseMode.HTML)

        @app.on_message((filters.group | filters.private) & filters.command("id"))
        async def _id(c, m: Message):
            target = m.reply_to_message or m
            user = (getattr(target, "from_user", None) if m.reply_to_message is not None
                    else getattr(m, "from_user", None))
            chat = getattr(target, "chat", None) or m.chat
            lines = [
                f"<b>Chat ID:</b> <code>{_esc(getattr(chat, 'id', m.chat.id))}</code>",
                f"<b>Message ID:</b> <code>{_esc(getattr(target, 'id', 'unknown'))}</code>",
            ]
            if user is not None:
                username = getattr(user, "username", None)
                lines.extend([
                    f"<b>User:</b> {_esc(getattr(user, 'first_name', '') or 'Unknown')}",
                    f"<b>User ID:</b> <code>{_esc(getattr(user, 'id', 'unknown'))}</code>",
                    f"<b>Username:</b> @{_esc(username)}" if username else "<b>Username:</b> none",
                ])
            chat_username = getattr(chat, "username", None)
            if chat_username:
                lines.append(f"<b>Chat username:</b> @{_esc(chat_username)}")
            origin = getattr(target, "forward_origin", None)
            forward_user = getattr(target, "forward_from", None)
            forward_chat = getattr(target, "forward_from_chat", None)
            if forward_user is None and origin is not None:
                forward_user = getattr(origin, "sender_user", None)
            if forward_chat is None and origin is not None:
                forward_chat = (getattr(origin, "sender_chat", None)
                                or getattr(origin, "chat", None))
            if forward_user is not None:
                forward_name = getattr(forward_user, "first_name", None) or "Forwarded user"
                forward_username = getattr(forward_user, "username", None)
                lines.append(f"<b>Forwarded from:</b> {_esc(forward_name)}"
                             f" · ID <code>{_esc(getattr(forward_user, 'id', 'unknown'))}</code>"
                             + (f" · @{_esc(forward_username)}" if forward_username else ""))
            elif forward_chat is not None:
                forward_name = getattr(forward_chat, "title", None) or "Forwarded chat/channel"
                forward_username = getattr(forward_chat, "username", None)
                lines.append(f"<b>Forwarded from:</b> {_esc(forward_name)}"
                             f" · ID <code>{_esc(getattr(forward_chat, 'id', 'unknown'))}</code>"
                             + (f" · @{_esc(forward_username)}" if forward_username else ""))
            elif origin is not None:
                hidden = getattr(origin, "sender_user_name", None)
                if hidden:
                    lines.append(f"<b>Forwarded from:</b> {_esc(hidden)} (hidden identity; no user ID available)")
            await m.reply_text("🆔 <b>Message IDs</b>\n\n" + "\n".join(lines),
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["welcome", "resetwelcome", "resetgoodbye"]))
        async def _welcome_cmd(c, m: Message):
            cmd = (m.command or ["welcome"])[0].lower()
            s = await store.get_settings(bot_id, m.chat.id)
            if cmd in ("resetwelcome", "resetgoodbye"):
                if not await _is_admin(c, m.chat.id, m.from_user.id):
                    return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
                key = "greet_text" if cmd == "resetwelcome" else "farewell_text"
                defaults = {"greet_text": "👋 Welcome {name} to {chat}!",
                            "farewell_text": "👋 {name} left {chat}."}
                updates = {key: defaults[key]}
                if cmd == "resetwelcome":
                    updates.update({"greet_media": "", "greet_media_type": ""})
                await store.update_settings(bot_id, m.chat.id, updates)
                return await m.reply_text("✅ Greeting reset to the default.", parse_mode=enums.ParseMode.HTML)
            args = [a.lower() for a in (m.command or [])[1:]]
            if args and args[0] in ("on", "off", "yes", "no", "true", "false"):
                if not await _is_admin(c, m.chat.id, m.from_user.id):
                    return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
                enabled = args[0] in ("on", "yes", "true")
                await store.update_settings(bot_id, m.chat.id, {"greetings": enabled})
                return await m.reply_text(f"👋 Greetings {'enabled' if enabled else 'disabled'}.",
                                          parse_mode=enums.ParseMode.HTML)
            name = _esc(m.from_user.first_name or "member")
            text = render_greeting(s.get("greet_text") or "", name=name,
                                   chat=_esc(m.chat.title or "this group"), user_id=m.from_user.id)
            if args and args[0] == "noformat":
                return await m.reply_text(f"<code>{_esc(s.get('greet_text') or '')}</code>",
                                          parse_mode=enums.ParseMode.HTML)
            media = s.get("greet_media") or ""
            if media and s.get("greet_media_type") == "photo":
                return await c.send_photo(m.chat.id, media, caption=text, parse_mode=enums.ParseMode.HTML)
            if media and s.get("greet_media_type") == "sticker":
                return await c.send_sticker(m.chat.id, media)
            return await m.reply_text(text, parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["setwelcome", "setgoodbye"]))
        async def _set_greeting(c, m: Message):
            cmd = (m.command or ["setwelcome"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            parts = (m.text or "").split(maxsplit=1)
            key = "greet_text" if cmd == "setwelcome" else "farewell_text"
            updates = {key: parts[1].strip()[:1000] if len(parts) > 1 else "", "greetings": True}
            replied = m.reply_to_message
            if cmd == "setwelcome" and replied is not None:
                if getattr(replied, "photo", None):
                    updates.update({"greet_media": replied.photo.file_id, "greet_media_type": "photo"})
                elif getattr(replied, "sticker", None):
                    updates.update({"greet_media": replied.sticker.file_id, "greet_media_type": "sticker"})
            if len(parts) < 2 and "greet_media" not in updates:
                return await m.reply_text(
                    f"Usage: <code>/{cmd} Hello {{name}}, welcome to {{chat}}!</code> — "
                    "or reply to a photo/sticker with it.",
                    parse_mode=enums.ParseMode.HTML)
            if not updates[key]:
                updates.pop(key)
            await store.update_settings(bot_id, m.chat.id, updates)
            await m.reply_text("✅ <b>Saved & greetings enabled.</b>", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["save", "get", "notes", "clear"]))
        async def _notes(c, m: Message):
            cmd = (m.command or ["notes"])[0].lower()
            s = await store.get_settings(bot_id, m.chat.id)
            notes = dict(s.get("notes") or {})
            if cmd == "notes":
                from core.rich import Doc
                from core import rich
                doc = Doc("📝", "Saved notes", f"{len(notes)} triggers")
                if notes:
                    doc.items([f"#{name}" for name in sorted(notes)[:100]])
                    doc.footer("Open a note with /get <trigger> or by sending #trigger.")
                else:
                    doc.text("No notes yet. Admins can add one with /save faq <text>.")
                return await rich.reply(m, doc)
            if cmd == "get":
                args = (m.command or [])[1:]
                note = notes.get(args[0].lower()) if args else None
                if note is None:
                    return await m.reply_text("That note doesn't exist.", parse_mode=enums.ParseMode.HTML)
                return await m.reply_text(_esc(note)[:3500], parse_mode=enums.ParseMode.HTML)
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            if cmd == "save":
                parts = (m.text or "").split(maxsplit=2)
                if len(parts) < 3:
                    return await m.reply_text("Usage: <code>/save &lt;trigger&gt; &lt;text&gt;</code>",
                                              parse_mode=enums.ParseMode.HTML)
                notes[parts[1].lower()[:40]] = parts[2].strip()[:3000]
                await store.update_settings(bot_id, m.chat.id, {"notes": notes})
                return await m.reply_text(f"✅ Note <b>{_esc(parts[1].lower())}</b> saved.",
                                          parse_mode=enums.ParseMode.HTML)
            args = (m.command or [])[1:]
            if not args or args[0].lower() not in notes:
                return await m.reply_text("That note doesn't exist.", parse_mode=enums.ParseMode.HTML)
            notes.pop(args[0].lower())
            await store.update_settings(bot_id, m.chat.id, {"notes": notes})
            await m.reply_text("🗑 Note deleted.", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["whois", "info"]))
        async def _whois(c, m: Message):
            target_id, label, _rest = _resolve(m)
            if target_id is None:
                target_id, label = m.from_user.id, m.from_user.first_name or str(m.from_user.id)
            status = await _status(c, m.chat.id, target_id)
            warns = await store.warn_count(bot_id, m.chat.id, target_id)
            s = await store.get_settings(bot_id, m.chat.id)
            markup = None
            if await _is_admin(c, m.chat.id, m.from_user.id) and status not in _ADMIN_STATUSES:
                markup = InlineKeyboardMarkup([[
                    _btn("⚠️ Warn", f"gw:{bot_id}:act:{m.chat.id}:{target_id}:warn", style="primary"),
                    _btn("🤐 Mute", f"gw:{bot_id}:act:{m.chat.id}:{target_id}:mute", style="primary"),
                    _btn("👢 Kick", f"gw:{bot_id}:act:{m.chat.id}:{target_id}:kick", style="danger"),
                    _btn("🔨 Ban", f"gw:{bot_id}:act:{m.chat.id}:{target_id}:ban", style="danger"),
                ]])
            from core.rich import Doc
            from core import rich
            doc = (Doc("👤", "Member card", label)
                   .table([
                       ("User ID", target_id),
                       ("Chat status", getattr(status, "value", status) or "unknown"),
                       ("Warnings", f"{warns}/{s.get('warn_limit', 3)}"),
                       ("Approved", "Yes" if str(target_id) in (s.get("approved") or {}) else "No"),
                   ], header=("Profile", "Status")))
            await rich.reply(m, doc, reply_markup=markup)

        @app.on_message(filters.group & filters.command("modlog"))
        async def _modlog(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            entries = await store.recent_actions(bot_id, m.chat.id, limit=10)
            from core.rich import Doc
            from core import rich
            if not entries:
                return await rich.reply(m, Doc("📋", "Moderation log", "No recent actions")
                                        .text("Actions taken in this group will appear here."))
            rows = [(str(e.get("action") or "unknown"), str(e.get("target_id") or "—"),
                     str(e.get("actor_id") or "—"), str(e.get("reason") or "—"))
                    for e in entries]
            doc = (Doc("📋", "Moderation log", f"Latest {len(rows)} actions")
                   .table(rows, header=("Action", "Target", "By", "Reason")))
            await rich.reply(m, doc)

        @app.on_message(filters.group & filters.command(["setlog", "logchannel", "unsetlog"]))
        async def _setlog(c, m: Message):
            cmd = (m.command or ["logchannel"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, m.chat.id)
            if cmd == "logchannel":
                current = s.get("log_chat") or 0
                return await m.reply_text(
                    f"📋 Log chat: <code>{current}</code>" if current else "📋 No log chat set. Use /setlog &lt;chat_id&gt;.",
                    parse_mode=enums.ParseMode.HTML)
            if cmd == "unsetlog":
                await store.update_settings(bot_id, m.chat.id, {"log_chat": 0})
                return await m.reply_text("📋 Log chat removed.", parse_mode=enums.ParseMode.HTML)
            args = (m.command or [])[1:]
            if len(args) != 1 or not args[0].lstrip("-").isdigit():
                return await m.reply_text(
                    "Usage: <code>/setlog -1001234567890</code>\n"
                    "<i>Add me to the log group/channel as admin first, then run this here.</i>",
                    parse_mode=enums.ParseMode.HTML)
            log_chat = int(args[0])
            try:
                await c.send_message(log_chat, "✅ <b>Log chat connected.</b> Moderation actions will be posted here.",
                                     parse_mode=enums.ParseMode.HTML)
            except Exception as exc:
                return await m.reply_text(
                    f"❌ I couldn't post there: {_esc(str(exc)[:120])}\n"
                    "Make sure I'm a member/admin of that chat and the ID is right.",
                    parse_mode=enums.ParseMode.HTML)
            await store.update_settings(bot_id, m.chat.id, {"log_chat": log_chat})
            await m.reply_text(f"✅ <b>Log chat set:</b> <code>{log_chat}</code>", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(
            ["approve", "unapprove", "approval", "approved", "unapproveall"]))
        async def _approvals(c, m: Message):
            cmd = (m.command or ["approved"])[0].lower()
            s = await store.get_settings(bot_id, m.chat.id)
            approved = dict(s.get("approved") or {})
            if cmd == "approved":
                from core.rich import Doc
                from core import rich
                doc = Doc("🤝", "Approved members", f"{len(approved)} trusted IDs")
                if approved:
                    doc.table([(uid, reason or "No reason set")
                               for uid, reason in sorted(approved.items())[:100]],
                              header=("User ID", "Reason"))
                else:
                    doc.text("No approved members yet. Use /approve to exempt a member from automation.")
                return await rich.reply(m, doc)
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            if cmd == "unapproveall":
                status = await _status(c, m.chat.id, m.from_user.id)
                if status != enums.ChatMemberStatus.OWNER:
                    return await m.reply_text("🔒 Only the group owner can clear all approvals.",
                                              parse_mode=enums.ParseMode.HTML)
                await store.update_settings(bot_id, m.chat.id, {"approved": {}})
                return await m.reply_text("🗑 All approvals cleared.", parse_mode=enums.ParseMode.HTML)
            target_id, label, rest = _resolve(m)
            if target_id is None:
                return await m.reply_text("Reply to a member or give a user ID.", parse_mode=enums.ParseMode.HTML)
            if cmd == "approve":
                approved[str(target_id)] = " ".join(rest)[:100]
                await store.update_settings(bot_id, m.chat.id, {"approved": approved})
                return await m.reply_text(
                    f"🤝 <b>{_esc(label)}</b> approved — exempt from shield, flood and night mode.",
                    parse_mode=enums.ParseMode.HTML)
            if cmd == "unapprove":
                approved.pop(str(target_id), None)
                await store.update_settings(bot_id, m.chat.id, {"approved": approved})
                return await m.reply_text(f"✅ Approval removed for <b>{_esc(label)}</b>.",
                                          parse_mode=enums.ParseMode.HTML)
            reason = approved.get(str(target_id))
            state = f"approved{f' — {_esc(reason)}' if reason else ''}" if str(target_id) in approved else "not approved"
            return await m.reply_text(f"🤝 <b>{_esc(label)}</b> is {state}.", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(
            ["blacklist", "addblacklist", "unblacklist", "blacklistmode"]))
        async def _blacklist(c, m: Message):
            if m.from_user is None or not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            cmd = ((m.command or ["blacklist"])[0].split("@", 1)[0]).lower()
            settings = await store.get_settings(bot_id, m.chat.id)
            words = normalize_blacklist_words(settings.get("blacklist_words") or [])
            mode = str(settings.get("blacklist_mode", "del")).lower()
            if mode not in BLACKLIST_MODES:
                mode = "del"
            if cmd == "blacklist":
                from core.rich import Doc
                from core import rich
                doc = Doc("🚫", "Blocked words", f"Action mode: {mode} · {len(words)} entries")
                if words:
                    doc.items(words[:40])
                    if len(words) > 40:
                        doc.footer(f"Showing 40 of {len(words)}. Use /unblacklist to remove an entry.")
                else:
                    doc.text("No words are blocked. Add terms with /addblacklist.")
                return await rich.reply(m, doc)
            if cmd == "blacklistmode":
                args = (m.command or [])[1:]
                if not args:
                    seconds = _bounded_blacklist_duration(settings.get("blacklist_duration", 3600))
                    return await m.reply_text(
                        f"⚙️ <b>Blacklist mode:</b> <code>{_esc(mode)}</code>"
                        + (f" · timed action: {seconds // 60}m" if mode in ("tban", "tmute") else "")
                        + "\nUse /blacklistmode off|del|warn|ban|kick|mute|tban|tmute [duration].",
                        parse_mode=enums.ParseMode.HTML)
                selected = args[0].lower()
                if selected not in BLACKLIST_MODES:
                    return await m.reply_text(
                        "Use /blacklistmode off|del|warn|ban|kick|mute|tban|tmute [duration].",
                        parse_mode=enums.ParseMode.HTML)
                updates = {"blacklist_mode": selected}
                if selected in ("tban", "tmute"):
                    if len(args) != 2:
                        return await m.reply_text(
                            f"Timed mode requires a duration, e.g. <code>/blacklistmode {selected} 1h</code>.",
                            parse_mode=enums.ParseMode.HTML)
                    duration = parse_duration(args[1])
                    if duration is None or duration > timedelta(days=365):
                        return await m.reply_text("Duration must be between 1 minute and 365 days.")
                    updates["blacklist_duration"] = int(duration.total_seconds())
                elif len(args) != 1:
                    return await m.reply_text("Only timed ban/mute modes take a duration.")
                await store.update_settings(bot_id, m.chat.id, updates)
                return await m.reply_text(f"✅ Blacklist mode set to <code>{_esc(selected)}</code>.",
                                          parse_mode=enums.ParseMode.HTML)

            args = (m.command or [])[1:]
            raw = m.text or m.caption or ""
            if raw.strip():
                try:
                    args = shlex.split(raw.split(maxsplit=1)[1]) if len(raw.split(maxsplit=1)) > 1 else []
                except ValueError:
                    return await m.reply_text('Use quotes for phrases, e.g. <code>/addblacklist "red flag"</code>.',
                                              parse_mode=enums.ParseMode.HTML)
            if not args and m.reply_to_message is not None:
                source = m.reply_to_message.text or m.reply_to_message.caption or ""
                args = [source]
            terms = normalize_blacklist_words(args, limit=20)
            if not terms:
                return await m.reply_text(
                    "Give one or more words, or reply to a message. Terms are matched as whole words.",
                    parse_mode=enums.ParseMode.HTML)
            if cmd == "addblacklist":
                combined = normalize_blacklist_words([*words, *terms], limit=100)
                added = len(combined) - len(words)
                await store.update_settings(bot_id, m.chat.id, {"blacklist_words": combined})
                return await m.reply_text(
                    f"✅ Added {added} word(s); {len(combined)} total. Current mode: <code>{_esc(mode)}</code>.",
                    parse_mode=enums.ParseMode.HTML)
            remaining = [word for word in words if word not in set(terms)]
            removed = len(words) - len(remaining)
            await store.update_settings(bot_id, m.chat.id, {"blacklist_words": remaining})
            await m.reply_text(f"✅ Removed {removed} word(s); {len(remaining)} remain.",
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("cleaner"))
        async def _cleaner(c, m: Message):
            if m.from_user is None or not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            args = (m.command or [])[1:]
            settings = await store.get_settings(bot_id, m.chat.id)
            current = bool(settings.get("clean_service")) or bool(settings.get("clean_commands"))
            if len(args) > 1 or (args and args[0].lower() not in ("on", "off")):
                return await m.reply_text("Use <code>/cleaner [on|off]</code>.",
                                          parse_mode=enums.ParseMode.HTML)
            enabled = args[0].lower() == "on" if args else not current
            await store.update_settings(bot_id, m.chat.id,
                                        {"clean_service": enabled, "clean_commands": enabled})
            state = "on" if enabled else "off"
            return await m.reply_text(
                f"🧹 Cleaner is <b>{state}</b> — service messages and slash-command messages "
                f"will {'be removed' if enabled else 'remain'}.", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(
            ["blocksticker", "unblocksticker", "blockedstickers", "addblsticker", "unblsticker", "blsticker"]))
        async def _stickers(c, m: Message):
            cmd = ((m.command or ["blockedstickers"])[0].split("@", 1)[0]).lower()
            cmd = {"blsticker": "blockedstickers", "addblsticker": "blocksticker",
                   "unblsticker": "unblocksticker"}.get(cmd, cmd)
            s = await store.get_settings(bot_id, m.chat.id)
            blocked = [value for value in (s.get("blocked_stickers") or [])
                       if isinstance(value, str) and value][:100]
            if cmd == "blockedstickers":
                from core.rich import Doc
                from core import rich
                doc = Doc("🚫", "Blocked stickers", f"{len(blocked)} sticker IDs")
                if blocked:
                    doc.items([value[:100] for value in blocked[:20]])
                    if len(blocked) > 20:
                        doc.footer(f"Showing 20 of {len(blocked)} blocked stickers.")
                else:
                    doc.text("No stickers are currently blocked.")
                return await rich.reply(m, doc)
            if m.from_user is None or not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            replied = m.reply_to_message
            sticker = getattr(replied, "sticker", None) if replied else None
            args = (m.command or [])[1:]
            unique = getattr(sticker, "file_unique_id", None)
            if unique is None and cmd == "unblocksticker" and len(args) == 1:
                unique = args[0].strip()
            if not unique:
                return await m.reply_text(
                    "Reply to a sticker to add/remove it, or pass its file_unique_id to /unblsticker.",
                    parse_mode=enums.ParseMode.HTML)
            if cmd == "blocksticker":
                if unique not in blocked:
                    if len(blocked) >= 100:
                        return await m.reply_text("The sticker blacklist is full (100 maximum).")
                    blocked.append(unique)
                    await store.update_settings(bot_id, m.chat.id, {"blocked_stickers": blocked})
                current_mode = str(s.get("blacklist_mode", "del")).lower()
                if current_mode not in BLACKLIST_MODES:
                    current_mode = "del"
                return await m.reply_text(
                    f"🚫 Sticker blocked; blacklist mode is <code>{_esc(current_mode)}</code>.",
                    parse_mode=enums.ParseMode.HTML)
            if unique in blocked:
                blocked.remove(unique)
                await store.update_settings(bot_id, m.chat.id, {"blocked_stickers": blocked})
            return await m.reply_text("✅ Sticker unblocked.", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("afk"))
        async def _afk(c, m: Message):
            parts = (m.text or "").split(maxsplit=1)
            reason = parts[1].strip()[:200] if len(parts) > 1 else ""
            await store.set_afk(bot_id, m.chat.id, m.from_user.id,
                                m.from_user.first_name or str(m.from_user.id), reason)
            await m.reply_text(
                f"💤 <b>{_esc(m.from_user.first_name or 'You')}</b> is now AFK."
                + (f"\n📝 {_esc(reason)}" if reason else ""),
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["export", "import"]))
        async def _transfer(c, m: Message):
            cmd = (m.command or ["export"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, m.chat.id)
            if cmd == "export":
                payload = {k: s.get(k) for k in default_settings()}
                return await m.reply_text(
                    f"{_bar('📤 SETTINGS BACKUP')}\n\n<pre>{_esc(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))}</pre>",
                    parse_mode=enums.ParseMode.HTML)
            raw = ""
            parts = (m.text or "").split(maxsplit=1)
            if len(parts) > 1:
                raw = parts[1]
            elif m.reply_to_message is not None:
                raw = m.reply_to_message.text or m.reply_to_message.caption or ""
            raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            if not raw or len(raw) > 60000:
                return await m.reply_text("📥 Reply to a backup with /import, or paste the JSON after /import.",
                                          parse_mode=enums.ParseMode.HTML)
            try:
                data = json.loads(raw)
            except Exception:
                return await m.reply_text("❌ That isn't valid JSON — nothing was changed.",
                                          parse_mode=enums.ParseMode.HTML)
            updates = sanitize_settings_import(data, default_settings())
            if updates is None:
                return await m.reply_text("❌ That backup failed validation — nothing was changed.",
                                          parse_mode=enums.ParseMode.HTML)
            await store.update_settings(bot_id, m.chat.id, updates)
            await m.reply_text(f"✅ <b>Imported {len(updates)} settings.</b> Open /settings to review.",
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["promote", "demote"]))
        async def _promote(c, m: Message):
            cmd = (m.command or ["promote"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            target_id, label, _rest = _resolve(m)
            if target_id is None:
                return await m.reply_text("Reply to a member or give a user ID.", parse_mode=enums.ParseMode.HTML)
            from pyrogram.types import ChatPrivileges
            try:
                if cmd == "promote":
                    await c.promote_chat_member(
                        m.chat.id, target_id,
                        privileges=ChatPrivileges(
                            can_manage_chat=True, can_delete_messages=True,
                            can_restrict_members=True, can_invite_users=True,
                            can_pin_messages=True, can_change_info=False,
                            can_promote_members=False))
                    text = f"⭐ <b>{_esc(label)}</b> is now an admin (moderation rights, no promoting others)."
                else:
                    if await _status(c, m.chat.id, target_id) == enums.ChatMemberStatus.OWNER:
                        return await m.reply_text("🛡 The group owner can't be demoted.", parse_mode=enums.ParseMode.HTML)
                    await c.promote_chat_member(m.chat.id, target_id, privileges=ChatPrivileges())
                    text = f"✅ <b>{_esc(label)}</b> is back to a regular member."
            except Exception as exc:
                return await m.reply_text(f"❌ <b>Telegram refused that.</b>\n{_esc(str(exc)[:120])}",
                                          parse_mode=enums.ParseMode.HTML)
            try:
                await store.log_action(bot_id, m.chat.id, cmd, target_id, m.from_user.id, "")
            except Exception:
                pass
            await m.reply_text(text, parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["lock", "unlock", "locks"]))
        async def _locks(c, m: Message):
            cmd = (m.command or ["locks"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, m.chat.id)
            kinds = set(s.get("guard_kinds") or [])
            if cmd == "locks":
                from core.rich import Doc
                from core import rich
                rows = [
                    (k.title(), "Locked" if (k in kinds or "all" in kinds) else "Open")
                    for k in GUARD_KINDS if k != "all"
                ] + [("Content shield", _onoff(s.get("shield")))]
                doc = (Doc("🔐", "Content locks", m.chat.title or "Group protection")
                       .table(rows, header=("Content type", "State"))
                       .footer("Use /lock links or /unlock media. Types: links, media, stickers, forwards, bots, all."))
                return await rich.reply(m, doc)
            args = (m.command or [])[1:]
            kind = lock_kind(args[0]) if args else ""
            if not kind:
                return await m.reply_text(
                    "Usage: <code>/lock links|media|stickers|forwards|bots|all</code>",
                    parse_mode=enums.ParseMode.HTML)
            if cmd == "lock":
                if kind == "all":
                    kinds = {"all"}
                elif "all" not in kinds:
                    kinds.add(kind)
                updates = {"guard_kinds": sorted(kinds), "shield": True}
                text = f"🔒 <b>{kind} locked.</b> Non-admin {kind} will be removed."
            else:
                if kind == "all":
                    kinds = set()
                    updates = {"guard_kinds": [], "shield": False}
                else:
                    if "all" in kinds:  # expand: everything except this kind stays locked
                        kinds = set(GUARD_KINDS) - {"all", kind}
                    kinds.discard(kind)
                    updates = {"guard_kinds": sorted(kinds)}
                text = f"🔓 <b>{kind} unlocked.</b>"
            s = await store.update_settings(bot_id, m.chat.id, updates)
            try:
                await store.log_action(bot_id, m.chat.id, cmd, 0, m.from_user.id, kind)
            except Exception:
                pass
            await m.reply_text(text, parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("title"))
        async def _title(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            parts = (m.text or "").split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return await m.reply_text("Usage: <code>/title New Group Name</code>", parse_mode=enums.ParseMode.HTML)
            try:
                await c.set_chat_title(m.chat.id, parts[1].strip()[:128])
            except Exception as exc:
                return await m.reply_text(f"❌ <b>Telegram refused that.</b>\n{_esc(str(exc)[:120])}",
                                          parse_mode=enums.ParseMode.HTML)
            await m.reply_text("✅ <b>Group title updated.</b>", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("chatinfo"))
        async def _chatinfo(c, m: Message):
            try:
                count = await c.get_chat_members_count(m.chat.id)
            except Exception:
                count = "?"
            s = await store.get_settings(bot_id, m.chat.id)
            from core.rich import Doc
            from core import rich
            doc = (Doc("💬", "Group overview", m.chat.title or "Untitled group")
                   .table([
                       ("Chat ID", m.chat.id),
                       ("Members", count),
                       ("Content shield", _onoff(s.get("shield"))),
                       ("Greetings", _onoff(s.get("greetings"))),
                       ("Federation", s.get("fed_id") or "Not connected"),
                   ], header=("Detail", "Value")))
            await rich.reply(m, doc)

        @app.on_message((filters.group | filters.private) & filters.command("createfed"))
        async def _createfed(c, m: Message):
            if m.from_user.id != self.owner_id:
                return await m.reply_text("🔒 Only this clone's owner can create a federation.",
                                          parse_mode=enums.ParseMode.HTML)
            parts = (m.text or "").split(maxsplit=1)
            name = parts[1].strip() if len(parts) > 1 else "My Federation"
            fed_id = "fed-" + secrets.token_hex(4)
            await store.create_fed(bot_id, self.owner_id, name, fed_id)
            await m.reply_text(
                f"🌐 <b>Federation created:</b> {_esc(name)}\nID: <code>{fed_id}</code>\n\n"
                "In each group that should join, an admin runs "
                f"<code>/joinfed {fed_id}</code>. Bans with /fedban then apply in every joined group.",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command([n for n, _ in GROUP_WORKER_COMMANDS]), group=-1)
        async def _disabled_gate(c, m: Message):
            """Disabled commands are swallowed for non-admins (group -1 runs first)."""
            cmd = (m.command or [""])[0].lower() if m.command else ""
            if not cmd:
                return
            s = await store.get_settings(bot_id, m.chat.id)
            if not command_blocked(s, cmd):
                return
            if await _is_admin(c, m.chat.id, m.from_user.id):
                return  # admins always bypass
            try:
                await m.delete()
            except Exception:
                pass
            raise StopPropagation

        @app.on_message(filters.group & filters.command(["disable", "enable", "disabled"]))
        async def _disable(c, m: Message):
            cmd = (m.command or ["disabled"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, m.chat.id)
            current = {normalize_cmd(x) for x in s.get("disabled_cmds") or []}
            if cmd == "disabled":
                from core.rich import Doc
                from core import rich
                doc = Doc("🚫", "Disabled commands", f"{len(current)} turned off for members")
                if current:
                    doc.items([f"/{name}" for name in sorted(current)])
                else:
                    doc.text("No commands are disabled in this group.")
                doc.footer("Admins can always use commands. /enable <command> restores one.")
                return await rich.reply(m, doc)
            args = (m.command or [])[1:]
            target = normalize_cmd(args[0]) if args else ""
            known = {n for n, _ in GROUP_WORKER_COMMANDS}
            if target in NEVER_DISABLE:
                return await m.reply_text(f"🛡 <code>/{target}</code> can never be disabled — it keeps the group manageable.",
                                          parse_mode=enums.ParseMode.HTML)
            if target not in known:
                return await m.reply_text("Usage: <code>/disable &lt;command&gt;</code> — see /help for command names.",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "disable":
                current.add(target)
                text = f"🚫 <code>/{target}</code> is now <b>off</b> for members here. Admins can still use it."
            else:
                current.discard(target)
                text = f"✅ <code>/{target}</code> is <b>back on</b> for everyone."
            await store.update_settings(bot_id, m.chat.id, {"disabled_cmds": sorted(current)})
            try:
                await store.log_action(bot_id, m.chat.id, cmd, 0, m.from_user.id, target)
            except Exception:
                pass
            await m.reply_text(text, parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("adminlist"))
        async def _adminlist(c, m: Message):
            try:
                members = [x async for x in c.get_chat_administrators(m.chat.id)]
            except Exception:
                return await m.reply_text("Couldn't fetch the admin list — am I admin here?",
                                          parse_mode=enums.ParseMode.HTML)
            from core.rich import Doc, link
            from core import rich
            owners, admins, bots = [], [], []
            for member in members:
                user = getattr(member, "user", None)
                if user is None:
                    continue
                name = user.first_name or str(user.id)
                entry = link(name, f"tg://user?id={int(user.id)}")
                status = getattr(member, "status", None)
                if getattr(user, "is_bot", False):
                    bots.append(("🤖 Bot", entry))
                elif status == enums.ChatMemberStatus.OWNER:
                    owners.append(("👑 Owner", entry))
                else:
                    admins.append(("⭐ Admin", entry))
            rows = owners + admins + bots
            doc = Doc("👑", "Admin team", f"{len(rows)} administrators and service bots")
            if rows:
                doc.table(rows, header=("Role", "Member"))
            else:
                doc.text("No administrators were returned for this group.")
            await rich.reply(m, doc)

        @app.on_message(filters.group & filters.command("stats"))
        async def _stats(c, m: Message):
            s = await store.get_settings(bot_id, m.chat.id)
            try:
                members = await c.get_chat_members_count(m.chat.id)
            except Exception:
                members = "?"
            try:
                actions = len(await store.recent_actions(bot_id, m.chat.id, limit=200))
            except Exception:
                actions = "?"
            from core.rich import Doc
            from core import rich
            doc = (Doc("📊", "Group stats", m.chat.title or "Group snapshot")
                   .table([
                       ("Members", members),
                       ("Recent logged actions", actions),
                       ("Approved members", len(s.get("approved") or {})),
                       ("Saved notes", len(s.get("notes") or {})),
                       ("Auto-replies", len(s.get("replies") or {})),
                       ("Blocked stickers", len(s.get("blocked_stickers") or [])),
                       ("Disabled commands", len(s.get("disabled_cmds") or [])),
                       ("Content shield", _onoff(s.get("shield"))),
                       ("Federation", s.get("fed_id") or "Not connected"),
                   ], header=("Metric", "Value"))
                   .footer("Counts are a snapshot and can change as the group is used."))
            await rich.reply(m, doc)

        @app.on_message(filters.group & filters.command(["joinfed", "leavefed", "fedinfo"]))
        async def _joinfed(c, m: Message):
            cmd = (m.command or ["fedinfo"])[0].lower()
            s = await store.get_settings(bot_id, m.chat.id)
            if cmd == "fedinfo":
                if not s.get("fed_id"):
                    return await m.reply_text("🌐 This group isn't in a federation.",
                                              parse_mode=enums.ParseMode.HTML)
                fed = await store.get_fed(bot_id, s["fed_id"])
                if not fed:
                    return await m.reply_text("🌐 Federation record not found.", parse_mode=enums.ParseMode.HTML)
                bans = await store.fed_bans(bot_id, s["fed_id"])
                return await m.reply_text(
                    f"🌐 <b>{_esc(fed.get('name'))}</b> (<code>{s['fed_id']}</code>)\n"
                    f"Groups: {len(fed.get('chats') or [])} · Federation bans: {len(bans)}",
                    parse_mode=enums.ParseMode.HTML)
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            if cmd == "leavefed":
                if s.get("fed_id"):
                    fed = await store.get_fed(bot_id, s["fed_id"])
                    if fed:
                        chats = [x for x in (fed.get("chats") or []) if int(x) != m.chat.id]
                        await store.set_fed_chats(bot_id, s["fed_id"], chats)
                    await store.update_settings(bot_id, m.chat.id, {"fed_id": ""})
                return await m.reply_text("🌐 This group left its federation.", parse_mode=enums.ParseMode.HTML)
            args = (m.command or [])[1:]
            if len(args) != 1:
                return await m.reply_text("Usage: <code>/joinfed &lt;federation_id&gt;</code>",
                                          parse_mode=enums.ParseMode.HTML)
            fed = await store.get_fed(bot_id, args[0])
            if not fed:
                return await m.reply_text("❌ No such federation for this clone.", parse_mode=enums.ParseMode.HTML)
            if s.get("fed_id") == args[0]:
                return await m.reply_text("Already in that federation.", parse_mode=enums.ParseMode.HTML)
            chats = [int(x) for x in (fed.get("chats") or [])]
            if m.chat.id not in chats:
                chats.append(m.chat.id)
                await store.set_fed_chats(bot_id, args[0], chats)
            await store.update_settings(bot_id, m.chat.id, {"fed_id": args[0]})
            await m.reply_text(f"🌐 Joined federation <b>{_esc(fed.get('name'))}</b>.",
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["fedban", "fedunban", "fedbans"]))
        async def _fedban(c, m: Message):
            cmd = (m.command or ["fedbans"])[0].lower()
            s = await store.get_settings(bot_id, m.chat.id)
            if not s.get("fed_id"):
                return await m.reply_text("🌐 This group isn't in a federation. See /joinfed.",
                                          parse_mode=enums.ParseMode.HTML)
            fed = await store.get_fed(bot_id, s["fed_id"])
            if cmd == "fedbans":
                bans = await store.fed_bans(bot_id, s["fed_id"])
                if not bans:
                    return await m.reply_text("🌐 No federation bans.", parse_mode=enums.ParseMode.HTML)
                lines = "\n".join(f"• <code>{b['user_id']}</code>" + (f" — {_esc(b.get('reason'))}" if b.get("reason") else "")
                                   for b in bans[:30])
                return await m.reply_text(f"{_bar('🌐 FEDERATION BANS')}\n\n{lines}",
                                          parse_mode=enums.ParseMode.HTML)
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            target_id, label, rest = _resolve(m)
            if target_id is None:
                return await m.reply_text("Reply to a member or give a user ID.", parse_mode=enums.ParseMode.HTML)
            if cmd == "fedunban":
                await store.fed_unban(bot_id, s["fed_id"], target_id)
                done = 0
                for chat_id in (fed.get("chats") or []):
                    try:
                        await c.unban_chat_member(int(chat_id), target_id)
                        done += 1
                    except Exception:
                        pass
                return await m.reply_text(f"✅ Federation unban for <b>{_esc(label)}</b> (lifted in {done} group(s)).",
                                          parse_mode=enums.ParseMode.HTML)
            reason = " ".join(rest)[:200]
            await store.fed_ban(bot_id, s["fed_id"], target_id, reason)
            done = 0
            for chat_id in (fed.get("chats") or []):
                try:
                    await c.ban_chat_member(int(chat_id), target_id)
                    done += 1
                except Exception:
                    pass
            try:
                await store.log_action(bot_id, m.chat.id, "fedban", target_id, m.from_user.id, reason)
            except Exception:
                pass
            await m.reply_text(
                f"🌐🔨 <b>Federation ban:</b> {_esc(label)} banned in {done}/{len(fed.get('chats') or [])} group(s).",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("language"))
        async def _language(c, m: Message):
            s = await store.get_settings(bot_id, m.chat.id)
            args = [a.lower() for a in (m.command or [])[1:]]
            if not args:
                return await m.reply_text(
                    f"🌐 Current language: <b>{s.get('lang', 'en')}</b>\nAvailable: {', '.join(LANGUAGES)}",
                    parse_mode=enums.ParseMode.HTML)
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            if args[0] not in LANGUAGES:
                return await m.reply_text(f"Available: {', '.join(LANGUAGES)}", parse_mode=enums.ParseMode.HTML)
            await store.update_settings(bot_id, m.chat.id, {"lang": args[0]})
            await m.reply_text(f"🌐 Language set to <b>{args[0]}</b>.", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["rules", "setrules"]))
        async def _rules(c, m: Message):
            cmd = (m.command or ["rules"])[0].lower()
            s = await store.get_settings(bot_id, m.chat.id)
            if cmd == "rules":
                if not s.get("rules"):
                    return await m.reply_text("📜 <b>No rules set yet.</b>\nAdmins can add them with /setrules.",
                                              parse_mode=enums.ParseMode.HTML)
                return await m.reply_text(f"{_bar('📜 GROUP RULES')}\n\n{_esc(s['rules'])}",
                                          parse_mode=enums.ParseMode.HTML)
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            parts = (m.text or "").split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return await m.reply_text("Usage: <code>/setrules Be kind. No spam.</code>",
                                          parse_mode=enums.ParseMode.HTML)
            await store.update_settings(bot_id, m.chat.id, {"rules": parts[1].strip()[:2000]})
            await m.reply_text("✅ <b>Rules saved.</b> Members can read them with /rules.",
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["filter", "mfilter", "stop", "filters", "stopall"]))
        async def _filters_cmd(c, m: Message):
            cmd = ((m.command or ["filters"])[0].split("@", 1)[0]).lower()
            s = await store.get_settings(bot_id, m.chat.id)
            replies = dict(s.get("replies") or {}) if isinstance(s.get("replies"), dict) else {}
            media_replies = (dict(s.get("media_replies") or {})
                             if isinstance(s.get("media_replies"), dict) else {})
            media_captions = (dict(s.get("media_reply_captions") or {})
                              if isinstance(s.get("media_reply_captions"), dict) else {})
            if cmd == "filters":
                if not replies and not media_replies:
                    return await m.reply_text(
                        "💬 No auto-replies yet. Admins: <code>/filter hello **Hi {first}!**</code> "
                        "or reply to media with <code>/mfilter photo</code>.",
                        parse_mode=enums.ParseMode.HTML)
                lines = [f"• <b>{_esc(key)}</b> → {_esc(value)[:70]}"
                         for key, value in sorted(replies.items())
                         if isinstance(key, str) and isinstance(value, str)]
                lines.extend(f"• <b>{_esc(key)}</b> → <i>{_esc(value.split(':', 1)[0])} reply</i>"
                             for key, value in sorted(media_replies.items())
                             if isinstance(key, str) and isinstance(value, str))
                body = "\n".join(lines[:50]) or "No valid auto-replies."
                if len(lines) > 50:
                    body += f"\n…and {len(lines) - 50} more."
                return await m.reply_text(f"{_bar('💬 FILTERS')}\n\n{body}",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "stopall":
                if (m.from_user is None or getattr(m.from_user, "is_bot", False)
                        or m.from_user.id != self.owner_id):
                    return await m.reply_text("🔒 <b>Owner only.</b>", parse_mode=enums.ParseMode.HTML)
                await store.update_settings(bot_id, m.chat.id,
                                           {"replies": {}, "media_replies": {}, "media_reply_captions": {}})
                return await m.reply_text("🧹 All text and media auto-replies removed.",
                                          parse_mode=enums.ParseMode.HTML)
            if m.from_user is None or not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)

            if cmd == "mfilter":
                raw = m.text or ""
                tail = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
                try:
                    args = shlex.split(tail)
                except ValueError:
                    args = []
                if len(args) != 1 or m.reply_to_message is None:
                    return await m.reply_text(
                        'Reply to a photo/video/sticker and use <code>/mfilter &lt;trigger&gt;</code>; '
                        'quote multiword triggers.', parse_mode=enums.ParseMode.HTML)
                key = " ".join(args[0].split()).casefold()[:64]
                replied = m.reply_to_message
                media_type = next((kind for kind in (
                    "photo", "video", "animation", "document", "audio", "voice", "sticker")
                    if getattr(replied, kind, None) is not None), None)
                media = getattr(replied, media_type, None) if media_type else None
                file_id = getattr(media, "file_id", None)
                if not key or not isinstance(file_id, str) or not file_id or len(file_id) > 2048:
                    return await m.reply_text("That reply has no supported media file.",
                                              parse_mode=enums.ParseMode.HTML)
                if key not in media_replies and key not in replies and len(media_replies) + len(replies) >= 100:
                    return await m.reply_text("The group filter limit is 100 triggers.")
                replies.pop(key, None)
                media_replies[key] = f"{media_type}:{file_id}"
                caption = replied.caption or ""
                if caption:
                    media_captions[key] = caption[:500]
                else:
                    media_captions.pop(key, None)
                await store.update_settings(bot_id, m.chat.id,
                                           {"replies": replies, "media_replies": media_replies,
                                            "media_reply_captions": media_captions})
                return await m.reply_text(f"✅ Media auto-reply saved for <b>{_esc(key)}</b>.",
                                          parse_mode=enums.ParseMode.HTML)

            if cmd == "filter":
                parsed = parse_filter_command(m.text or "")
                if parsed is None:
                    return await m.reply_text(
                        'Usage: <code>/filter &lt;trigger&gt; &lt;Markdown reply&gt;</code>; '
                        'quote multiword triggers, e.g. <code>/filter "price list" See {chatname}</code>.',
                        parse_mode=enums.ParseMode.HTML)
                key, template = parsed
                if not key:
                    return await m.reply_text("Trigger can't be empty.")
                if key not in replies and key not in media_replies and len(replies) + len(media_replies) >= 100:
                    return await m.reply_text("The group filter limit is 100 triggers.")
                media_replies.pop(key, None)
                media_captions.pop(key, None)
                replies[key] = template
                await store.update_settings(bot_id, m.chat.id,
                                           {"replies": replies, "media_replies": media_replies,
                                            "media_reply_captions": media_captions})
                return await m.reply_text(
                    f"✅ Markdown auto-reply saved for <b>{_esc(key)}</b>. Placeholders: "
                    "<code>{first} {last} {fullname} {username} {id} {chatname} {chatid} {mention}</code>.",
                    parse_mode=enums.ParseMode.HTML)

            raw = m.text or ""
            tail = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
            try:
                args = shlex.split(tail)
            except ValueError:
                args = []
            if len(args) != 1:
                return await m.reply_text('Use <code>/stop &lt;trigger&gt;</code>.',
                                          parse_mode=enums.ParseMode.HTML)
            key = " ".join(args[0].split()).casefold()[:64]
            was_present = key in replies or key in media_replies
            replies.pop(key, None)
            media_replies.pop(key, None)
            media_captions.pop(key, None)
            if was_present:
                await store.update_settings(bot_id, m.chat.id,
                                           {"replies": replies, "media_replies": media_replies,
                                            "media_reply_captions": media_captions})
                return await m.reply_text("🗑 Auto-reply removed.", parse_mode=enums.ParseMode.HTML)
            return await m.reply_text("That auto-reply doesn't exist.", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["pin", "unpin"]))
        async def _pin(c, m: Message):
            cmd = (m.command or ["pin"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            if cmd == "unpin":
                await c.unpin_all_chat_messages(m.chat.id)
                return await m.reply_text("📌 All pins removed.", parse_mode=enums.ParseMode.HTML)
            if m.reply_to_message is None:
                return await m.reply_text("Reply to the message you want pinned.", parse_mode=enums.ParseMode.HTML)
            await c.pin_chat_message(m.chat.id, m.reply_to_message.id)
            await m.reply_text("📌 Pinned.", parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("report"))
        async def _report(c, m: Message):
            if m.reply_to_message is None or m.reply_to_message.from_user is None:
                return await m.reply_text("Reply to the message you want to report.", parse_mode=enums.ParseMode.HTML)
            reported = m.reply_to_message
            offender = reported.from_user
            markup = InlineKeyboardMarkup([[
                _button("🗑 Delete", callback_data=f"gw:{bot_id}:rep:{m.chat.id}:{reported.id}:{offender.id}:del"),
                _button("🔨 Ban", callback_data=f"gw:{bot_id}:rep:{m.chat.id}:{reported.id}:{offender.id}:ban"),
                _button("✅ Ignore", callback_data=f"gw:{bot_id}:keep"),
            ]])
            text = (f"🚩 <b>Member report</b>\n\nGroup: <b>{_esc(m.chat.title or m.chat.id)}</b>\n"
                    f"Reported: {_esc(offender.first_name or offender.id)} ({offender.id})\n"
                    f"By: {_esc(m.from_user.first_name or m.from_user.id)}")
            sent = 0
            try:
                async for member in c.get_chat_administrators(m.chat.id):
                    user = getattr(member, "user", None)
                    if user is None or getattr(user, "is_bot", False):
                        continue
                    try:
                        await c.send_message(user.id, text, parse_mode=enums.ParseMode.HTML, reply_markup=markup)
                        sent += 1
                    except Exception:
                        pass
            except Exception:
                pass
            await m.reply_text(f"🚩 Report sent to {sent} admin(s) by DM." if sent
                               else "🚩 Report noted. (No admin could be reached by DM.)",
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.new_chat_members)
        async def _joined(c, m: Message):
            s = await store.get_settings(bot_id, m.chat.id)
            for user in m.new_chat_members or []:
                if getattr(user, "is_bot", False):
                    continue
                name = _esc(user.first_name or "member")
                chat_name = _esc(m.chat.title or "this group")
                if s.get("log_chat"):
                    try:
                        await c.send_message(int(s["log_chat"]),
                                             f"👋 <b>Joined:</b> {_esc(user.first_name or user.id)} (<code>{user.id}</code>)\nGroup: {_esc(m.chat.title or m.chat.id)}",
                                             parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass
                fed_ban = await store.get_fed_ban(bot_id, s.get("fed_id") or "", user.id)
                if fed_ban:
                    try:
                        await c.ban_chat_member(m.chat.id, user.id)
                    except Exception:
                        pass
                    continue
                if s.get("raid_on") and self.joins.record(m.chat.id):
                    try:
                        until = datetime.now(timezone.utc) + timedelta(minutes=15)
                        await c.ban_chat_member(m.chat.id, user.id, until_date=until)
                        await c.send_message(
                            m.chat.id,
                            "🚨 <b>Anti-raid shield:</b> join burst detected — new joiners are "
                            "temporarily banned for 15 minutes.",
                            parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass
                    continue
                if s.get("captcha_on"):
                    try:
                        await c.restrict_chat_member(m.chat.id, user.id, ChatPermissions())
                        markup = InlineKeyboardMarkup([[
                            _button("✅ I'm human — let me chat",
                                                 callback_data=f"gw:{bot_id}:verify:{m.chat.id}:{user.id}")]])
                        await c.send_message(
                            m.chat.id,
                            f"👋 Welcome {name}! Tap the button below to verify and start chatting.",
                            parse_mode=enums.ParseMode.HTML, reply_markup=markup)
                    except Exception:
                        pass
                    continue
                if not s.get("greetings"):
                    continue
                text = render_greeting(s.get("greet_text") or "", name=name,
                                       chat=chat_name, user_id=user.id)
                sent = None
                media = s.get("greet_media") or ""
                try:
                    if media and s.get("greet_media_type") == "photo":
                        sent = await c.send_photo(m.chat.id, media, caption=text,
                                                  parse_mode=enums.ParseMode.HTML)
                    elif media and s.get("greet_media_type") == "sticker":
                        sent = await c.send_sticker(m.chat.id, media)
                    else:
                        welcome_markup = None
                        if s.get("rules"):
                            welcome_markup = InlineKeyboardMarkup([[
                                _btn("📜 Read the rules", f"gw:{bot_id}:rules:{m.chat.id}", style="primary")]])
                        sent = await c.send_message(m.chat.id, text, parse_mode=enums.ParseMode.HTML,
                                                    reply_markup=welcome_markup)
                except Exception:
                    sent = None
                if sent is not None and getattr(sent, "id", None):
                    if s.get("clean_welcome") and self._last_welcome.get(m.chat.id):
                        try:
                            await c.delete_messages(m.chat.id, [self._last_welcome[m.chat.id]])
                        except Exception:
                            pass
                    self._last_welcome[m.chat.id] = sent.id
                if s.get("greet_dm"):
                    try:
                        await c.send_message(user.id, text, parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

        @app.on_message(filters.group & filters.left_chat_member)
        async def _left(c, m: Message):
            s = await store.get_settings(bot_id, m.chat.id)
            if getattr(m.left_chat_member, "is_bot", False):
                return
            user = m.left_chat_member
            if s.get("log_chat"):
                try:
                    await c.send_message(int(s["log_chat"]),
                                         f"👋 <b>Left:</b> {_esc(user.first_name or user.id)} (<code>{user.id}</code>)\nGroup: {_esc(m.chat.title or m.chat.id)}",
                                         parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
            if s.get("greetings"):
                text = render_greeting(s.get("farewell_text") or "", name=_esc(user.first_name or "member"),
                                       chat=_esc(m.chat.title or "this group"), user_id=user.id)
                await c.send_message(m.chat.id, text, parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.service, group=5)
        async def _service_cleaner(c, m: Message):
            s = await store.get_settings(bot_id, m.chat.id)
            if s.get("clean_service"):
                try:
                    await m.delete()
                except Exception:
                    pass

        @app.on_message(filters.group & filters.incoming & ~filters.service, group=5)
        async def _guard(c, m: Message):
            if is_external_channel_post(m):
                settings = await store.get_settings(bot_id, m.chat.id)
                if settings.get("anti_channel"):
                    try:
                        await m.delete()
                    except Exception:
                        pass
                    return
            if m.from_user is None:
                return
            s = await store.get_settings(bot_id, m.chat.id)
            if s.get("clean_commands"):
                command_text = m.text or m.caption or ""
                if command_text.startswith("/"):
                    try:
                        await m.delete()
                    except Exception:
                        pass
            blacklist_mode = str(s.get("blacklist_mode", "del")).lower()
            if blacklist_mode not in BLACKLIST_MODES:
                blacklist_mode = "del"
            # Enforce blocklists before AFK, notes or auto-replies can consume the message.
            has_blacklist = bool(s.get("blacklist_words") or s.get("blocked_stickers"))
            if blacklist_mode != "off" and has_blacklist and not is_approved(s, m.from_user.id):
                if await store.get_fed_ban(bot_id, s.get("fed_id") or "", m.from_user.id):
                    try:
                        await c.ban_chat_member(m.chat.id, m.from_user.id)
                    except Exception:
                        pass
                    return
                if await _status(c, m.chat.id, m.from_user.id) == enums.ChatMemberStatus.MEMBER:
                    blacklist_hit = match_blacklisted_word(
                        m.text or m.caption or "", s.get("blacklist_words") or [])
                    if blacklist_hit:
                        await self._apply_blacklist_action(c, m, s, item=blacklist_hit)
                        return
                    if sticker_blocked(m, s.get("blocked_stickers") or []):
                        await self._apply_blacklist_action(c, m, s, item="sticker")
                        return
            afk_doc = await store.get_afk(bot_id, m.chat.id, m.from_user.id)
            if afk_doc:
                await store.clear_afk(bot_id, m.chat.id, m.from_user.id)
                try:
                    await m.reply_text(f"👋 Welcome back, {_esc(m.from_user.first_name or 'friend')}!",
                                       parse_mode=enums.ParseMode.HTML)
                except Exception:
                    pass
            if m.reply_to_message is not None and m.reply_to_message.from_user is not None:
                other = await store.get_afk(bot_id, m.chat.id, m.reply_to_message.from_user.id)
                if other:
                    try:
                        await m.reply_text(
                            f"💤 <b>{_esc(other.get('name') or 'That member')}</b> is AFK."
                            + (f"\n📝 {_esc(other.get('reason'))}" if other.get("reason") else ""),
                            parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass
            text_in = m.text or m.caption or ""
            notes = s.get("notes") or {}
            if notes and text_in.startswith("#") and len(text_in) > 1:
                note = notes.get(text_in[1:].split()[0].lower())
                if note:
                    await m.reply_text(_esc(note)[:3500], parse_mode=enums.ParseMode.HTML)
                    return
            hit = match_reply(text_in, s.get("replies") or {})
            if hit:
                rendered = render_filter_reply(hit[1], m.from_user, m.chat)
                try:
                    await m.reply_text(rendered, parse_mode=enums.ParseMode.MARKDOWN)
                except Exception as exc:
                    log.warning("text filter reply failed in chat %s (%s)",
                                m.chat.id, type(exc).__name__)
                return
            media_hit = match_reply(text_in, s.get("media_replies") or {})
            if media_hit:
                spec = media_hit[1]
                media_type, separator, file_id = spec.partition(":")
                if (not separator or media_type not in
                        ("photo", "video", "animation", "document", "audio", "voice", "sticker")
                        or not file_id or len(file_id) > 2048):
                    return
                media_captions = s.get("media_reply_captions")
                caption_template = media_captions.get(media_hit[0], "") if isinstance(media_captions, dict) else ""
                caption = render_filter_reply(caption_template, m.from_user, m.chat) if caption_template else ""
                try:
                    sender = getattr(c, f"send_{media_type}")
                    kwargs = {"caption": caption, "parse_mode": enums.ParseMode.MARKDOWN} if caption and media_type != "sticker" else {}
                    await sender(m.chat.id, file_id, **kwargs)
                except Exception as exc:
                    log.warning("media filter reply failed in chat %s (%s)",
                                m.chat.id, type(exc).__name__)
                return
            if s.get("night_on") and not is_approved(s, m.from_user.id) and in_quiet_hours(
                    datetime.now(timezone.utc).hour,
                    int(s.get("night_start", 23)), int(s.get("night_end", 7))):
                if await _status(c, m.chat.id, m.from_user.id) == enums.ChatMemberStatus.MEMBER:
                    try:
                        await m.delete()
                    except Exception:
                        pass
                return
            if await store.get_fed_ban(bot_id, s.get("fed_id") or "", m.from_user.id):
                try:
                    await c.ban_chat_member(m.chat.id, m.from_user.id)
                except Exception:
                    pass
                return
            if is_approved(s, m.from_user.id):
                return  # approved members are exempt from automated actions
            if await _status(c, m.chat.id, m.from_user.id) != enums.ChatMemberStatus.MEMBER:
                return  # admins/restricted/unknown are never auto-punished
            violation = content_violation(m, s.get("guard_kinds") or []) if s.get("shield") else ""
            flooded = bool(s.get("flood_limit")) and self.flood.hit(
                m.chat.id, m.from_user.id,
                limit=int(s["flood_limit"]), window=float(s["flood_window"]))
            if not (violation or flooded):
                return
            try:
                await m.delete()
            except Exception:
                pass
            if flooded:
                try:
                    if s.get("flood_action") == "kick":
                        await c.ban_chat_member(m.chat.id, m.from_user.id)
                        await c.unban_chat_member(m.chat.id, m.from_user.id)
                    elif s.get("flood_action") == "ban":
                        await c.ban_chat_member(m.chat.id, m.from_user.id)
                    else:
                        await c.restrict_chat_member(m.chat.id, m.from_user.id, ChatPermissions())
                except Exception:
                    pass


def attach_group_handlers(app: Client, *, bot_id: int, owner_id: int, store) -> _BotHandlers:
    handlers = _BotHandlers(bot_id=bot_id, owner_id=owner_id, store=store)
    handlers.attach(app)
    from .anime import attach_anime_handlers
    attach_anime_handlers(app, bot_id=bot_id)
    return handlers


# Clone-worker command list (kept separate from every other bot surface).
GROUP_WORKER_COMMANDS = [
    ("start", "About this group-management clone"),
    ("help", "List group commands"),
    ("anime", "Search AniList for anime"),
    ("manga", "Search AniList for manga"),
    ("character", "Search AniList for a character"),
    ("gettags", "List AniList anime genres"),
    ("browse", "Browse trending/upcoming/latest anime"),
    ("anisettings", "AniList lookup help and settings"),
    ("airing", "Check an anime's next episode"),
    ("topanime", "Show top-rated anime"),
    ("settings", "Group settings & switches (admins)"),
    ("antichannel", "Toggle removal of channel-profile posts (admins)"),
    ("cleaner", "Toggle service and command message cleanup (admins)"),
    ("blacklist", "List blocked words (admins)"),
    ("addblacklist", "Add blocked words (admins)"),
    ("unblacklist", "Remove blocked words (admins)"),
    ("blacklistmode", "Choose action for blocked content (admins)"),
    ("blsticker", "List blocked stickers"),
    ("addblsticker", "Block a replied sticker (admins)"),
    ("unblsticker", "Unblock a sticker by reply or ID (admins)"),
    ("warn", "Warn a member (reply or user ID)"),
    ("unwarn", "Clear a member's warnings"),
    ("mute", "Mute a member (reply or user ID)"),
    ("tmute", "Timed mute: /tmute 12h"),
    ("unmute", "Unmute a member"),
    ("kick", "Remove a member (can rejoin)"),
    ("ban", "Ban a member (reply or user ID)"),
    ("tban", "Timed ban: /tban 7d"),
    ("sban", "Silent ban (no announcement)"),
    ("unban", "Unban a member"),
    ("purge", "Delete recent messages: /purge 20"),
    ("pin", "Pin the replied message (admins)"),
    ("unpin", "Remove all pins (admins)"),
    ("rules", "Show the group rules"),
    ("setrules", "Set the group rules (admins)"),
    ("filter", "Markdown auto-reply with placeholders (admins)"),
    ("mfilter", "Save a replied media auto-reply (admins)"),
    ("stop", "Remove a text or media auto-reply (admins)"),
    ("filters", "List text and media auto-replies"),
    ("stopall", "Remove all auto-replies (owner only)"),
    ("report", "Report a message to admins by DM"),
    ("welcome", "Preview greetings or turn them on/off"),
    ("resetwelcome", "Reset the welcome text (admins)"),
    ("resetgoodbye", "Reset the goodbye text (admins)"),
    ("setwelcome", "Set the welcome text (admins)"),
    ("setgoodbye", "Set the goodbye text (admins)"),
    ("save", "Save a note: /save faq text (admins)"),
    ("get", "Open a note: /get faq"),
    ("notes", "List saved notes"),
    ("clear", "Delete a note (admins)"),
    ("whois", "Member card with admin actions"),
    ("id", "Show IDs for a user, chat, reply or forward"),
    ("language", "Show or set my language"),
    ("promote", "Promote a member to admin (admins)"),
    ("demote", "Demote an admin to member (admins)"),
    ("title", "Set the group title (admins)"),
    ("chatinfo", "Group info & status"),
    ("lock", "Block a content type: /lock links (admins)"),
    ("unlock", "Allow a content type: /unlock links (admins)"),
    ("locks", "Show locked content types (admins)"),
    ("disable", "Turn a command off here: /disable afk (admins)"),
    ("enable", "Turn a command back on: /enable afk (admins)"),
    ("disabled", "List disabled commands (admins)"),
    ("adminlist", "Show the group's admin team"),
    ("stats", "Group numbers & worker status"),
    ("createfed", "Create a federation (clone owner)"),
    ("joinfed", "Join this group to a federation (admins)"),
    ("leavefed", "Remove this group from its federation"),
    ("fedinfo", "Federation info for this group"),
    ("fedban", "Ban a member in every federation group"),
    ("fedunban", "Lift a federation ban"),
    ("fedbans", "List federation bans"),
    ("afk", "Mark yourself AFK: /afk reason"),
    ("blocksticker", "Block the replied sticker (admins)"),
    ("unblocksticker", "Unblock the replied sticker (admins)"),
    ("blockedstickers", "Count blocked stickers"),
    ("export", "Back up this group's settings (admins)"),
    ("import", "Restore a settings backup (admins)"),
    ("connect", "DM: connect to a group: /connect <group_id>"),
    ("disconnect", "DM: disconnect from the group"),
    ("connection", "DM: show current connection"),
    ("approve", "Approve a member: exempt from automation"),
    ("unapprove", "Remove a member's approval"),
    ("approval", "Check a member's approval"),
    ("approved", "List approved members"),
    ("unapproveall", "Clear all approvals (group owner)"),
    ("modlog", "Recent moderation actions (admins)"),
    ("setlog", "Set the moderation log chat (admins)"),
    ("logchannel", "Show the moderation log chat"),
    ("unsetlog", "Remove the moderation log chat (admins)"),
]

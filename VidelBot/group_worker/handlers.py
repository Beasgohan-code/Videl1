"""Telegram handlers for one clone running the Group Management worker.

Standard group commands (``/ban``, ``/kick``, ``/mute``, ``/warn`` …)
with a polished worker-style settings panel at ``/settings``. This worker is its
own codebase for clones — it shares no code with the main bot's
group-management plugin.
"""
from __future__ import annotations

import html
import json
import secrets
from datetime import datetime, timedelta, timezone

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .logic import GUARD_KINDS, LANGUAGES, FloodWatch, JoinWatch, content_violation, default_settings, in_quiet_hours, is_approved, match_reply, parse_duration, render_greeting, sanitize_settings_import, sticker_blocked, tr

_ADMIN_STATUSES = (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)

_ON = "🟢"
_OFF = "🔴"


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _btn(text: str, data: str, style: str | None = None, url: bool = False) -> InlineKeyboardButton:
    """Inline button with Bot API button styling when the client supports it."""
    kwargs = {"url": data} if url else {"callback_data": data}
    if style:
        try:
            return InlineKeyboardButton(text, style=style, **kwargs)
        except TypeError:
            pass
    return InlineKeyboardButton(text, **kwargs)


def _bar(title: str) -> str:
    return f"<b>━━━━━━━━━━━━━━━━━━━\n{title}\n━━━━━━━━━━━━━━━━━━━</b>"


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
            rows.append([InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")])
            return InlineKeyboardMarkup(rows)
        if page == "greet":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("greetings")), "Greetings enabled", f"gw:{bot}:tog:greetings")],
                [tog(bool(s.get("greet_dm")), "Also DM the welcome", f"gw:{bot}:tog:greet_dm")],
                [tog(bool(s.get("clean_welcome")), "Clean old welcomes", f"gw:{bot}:tog:clean_welcome")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "security":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("captcha_on")), "Join CAPTCHA (verify button)", f"gw:{bot}:tog:captcha_on")],
                [tog(bool(s.get("raid_on")), "Anti-raid join shield", f"gw:{bot}:tog:raid_on")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "night":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("night_on")), "Night mode enabled", f"gw:{bot}:tog:night_on")],
                [InlineKeyboardButton(f"🌙 From {s.get('night_start', 23):02d}:00 UTC", callback_data=f"gw:{bot}:nightstart"),
                 InlineKeyboardButton(f"☀️ To {s.get('night_end', 7):02d}:00 UTC", callback_data=f"gw:{bot}:nightend")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "warn":
            return InlineKeyboardMarkup([
                [InlineKeyboardButton(f"➖ {s.get('warn_limit', 3)} warns ➕", callback_data=f"gw:{bot}:warnlimit")],
                [InlineKeyboardButton(f"⚠️ On limit: {s.get('warn_action', 'mute')}", callback_data=f"gw:{bot}:warnaction")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "flood":
            on = bool(s.get("flood_limit"))
            flood_label = f"Flood watch · {s['flood_limit']} msgs/{s['flood_window']:g}s" if on else "Flood watch"
            return InlineKeyboardMarkup([
                [tog(on, flood_label, f"gw:{bot}:tog:flood")],
                [InlineKeyboardButton(f"🌊 On flood: {s.get('flood_action', 'mute')}", callback_data=f"gw:{bot}:floodaction")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        return InlineKeyboardMarkup([
            [tog(bool(s.get("shield")), "Shield", f"gw:{bot}:page:shield"),
             tog(bool(s.get("greetings")), "Greetings", f"gw:{bot}:tog:greetings")],
            [InlineKeyboardButton("🛡 Shield types ›", callback_data=f"gw:{bot}:page:shield"),
             InlineKeyboardButton("⚠️ Warnings ›", callback_data=f"gw:{bot}:page:warn")],
            [tog(bool(s.get("dm_alerts")), "DM alerts to owner", f"gw:{bot}:tog:dm_alerts"),
             tog(bool(s.get("night_on")), "Night mode", f"gw:{bot}:page:night")],
            [InlineKeyboardButton("👋 Greetings ›", callback_data=f"gw:{bot}:page:greet"),
             InlineKeyboardButton("🌙 Night hours ›", callback_data=f"gw:{bot}:page:night")],
            [InlineKeyboardButton("🤖 Security (CAPTCHA · anti-raid) ›", callback_data=f"gw:{bot}:page:security")],
            [tog(bool(s.get("clean_service")), "Clean service msgs", f"gw:{bot}:tog:clean_service"),
             InlineKeyboardButton(f"🌐 {str(s.get('lang', 'en')).upper()}", callback_data=f"gw:{bot}:lang")],
            [InlineKeyboardButton("🌊 Flood watch ›", callback_data=f"gw:{bot}:page:flood"),
             InlineKeyboardButton("❌ Close", callback_data=f"gw:{bot}:close")],
        ])

    def _settings_text(self, page: str, s: dict, chat_title: str = "") -> str:
        head = _bar(f"⚙️ {tr(s, 'settings')}") + (f"\n<i>{_esc(chat_title)}</i>" if chat_title else "")
        if page == "security":
            return (f"{head}\n\n🤖 <b>Security</b>\n"
                    f"CAPTCHA: {'on' if s.get('captcha_on') else 'off'} · "
                    f"Anti-raid: {'on' if s.get('raid_on') else 'off'}\n"
                    "<i>With CAPTCHA, new members stay muted until they tap the verify button. "
                    "The anti-raid shield temporarily bans joiners during join bursts.</i>")
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
                [InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "security":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("captcha_on")), "Join CAPTCHA (verify button)", f"gw:{bot}:tog:captcha_on")],
                [tog(bool(s.get("raid_on")), "Anti-raid join shield", f"gw:{bot}:tog:raid_on")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
            ])
        if page == "night":
            return InlineKeyboardMarkup([
                [tog(bool(s.get("night_on")), "Night mode enabled", f"gw:{bot}:tog:night_on")],
                [InlineKeyboardButton(f"🌙 From {s.get('night_start', 23):02d}:00 UTC", callback_data=f"gw:{bot}:nightstart"),
                 InlineKeyboardButton(f"☀️ To {s.get('night_end', 7):02d}:00 UTC", callback_data=f"gw:{bot}:nightend")],
                [InlineKeyboardButton("⬅️ Back", callback_data=f"gw:{bot}:page:main")],
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
        return (
            f"{head}\n\n<blockquote>"
            f"🛡 <b>Shield</b> — {'on' if s.get('shield') else 'off'} · {_esc(kinds)}\n"
            f"👋 <b>Greetings</b> — {'on' if s.get('greetings') else 'off'}\n"
            f"⚠️ <b>Warnings</b> — {s.get('warn_limit', 3)} → {s.get('warn_action', 'mute')}\n"
            f"🌊 <b>Flood watch</b> — {_esc(flood)}\n"
            f"📜 <b>Rules</b> — {'set' if s.get('rules') else 'not set'} · "
            f"💬 <b>Auto-replies</b> — {len(s.get('replies') or {})}\n"
            f"📩 <b>DM alerts</b> — {'on' if s.get('dm_alerts') else 'off'} · "
            f"🌙 <b>Night</b> — {'on' if s.get('night_on') else 'off'} · "
            f"🤝 <b>Approved</b> — {len(s.get('approved') or {})} · "
            f"🤖 <b>CAPTCHA</b> — {'on' if s.get('captcha_on') else 'off'} · "
            f"🚨 <b>Anti-raid</b> — {'on' if s.get('raid_on') else 'off'} · "
            f"📝 <b>Notes</b> — {len(s.get('notes') or {})} · "
            f"📋 <b>Log chat</b> — {'set' if s.get('log_chat') else 'off'} · "
            f"🧹 <b>Clean service</b> — {'on' if s.get('clean_service') else 'off'} · "
            f"🌐 <b>Language</b> — {s.get('lang', 'en')}</blockquote>\n\n"
            f"<i>{tr(s, 'admins_only')}</i>"
        )

    # ── rich start pages ─────────────────────────────────────────
    def _start_text(self, page: str = "home") -> str:
        if page == "help":
            return (
                f"{_bar('📖 HELP — SHORT & EASY')}\n\n"
                "<b>🛡 Moderation</b> — reply to a member:\n"
                "/warn /mute /tmute 12h /kick /ban /tban 7d /sban /unban /unmute\n\n"
                "<b>👑 Admin tools</b>\n"
                "/promote /demote /title /chatinfo\n\n"
                "<b>⚙️ Group care</b>\n"
                "/settings — buttons for shield, greetings, flood, night & more\n"
                "/rules /filter /save — rules, auto-replies, notes (#trigger)\n\n"
                "<b>🤝 Trusted & safety</b>\n"
                "/approve — exempt a member from automation\n"
                "/report — (reply) alert admins by DM · /fedban — ban across your groups\n\n"
                "<b>✨ Extras</b>\n"
                "/afk /whois /purge /pin /modlog /welcome\n\n"
                "<i>Admins only for actions. Times look like 30m, 12h, 7d.</i>"
            )
        if page == "about":
            return (
                f"{_bar('ℹ️ ABOUT')}\n\n"
                "I'm a <b>Group Management clone</b> — I guard this bot's groups with "
                "moderation, greetings, anti-spam shields, CAPTCHA, anti-raid, "
                "federations and DM alerts for admins.\n\n"
                "Open /settings inside a group to switch everything with buttons. "
                "No typing long commands needed. 💪"
            )
        return (
            f"{_bar('🛡️ ' + tr({}, 'welcome_title'))}\n\n"
            "👋 <b>Hey! I keep groups safe & friendly.</b>\n\n"
            "➕ Add me to a group and make me admin\n"
            "⚙️ Open /settings there — everything is buttons\n"
            "🛡 I handle spam, floods, raids & bad members\n\n"
            "<i>Tap Help for the short command list.</i>"
        )

    def _start_markup(self, bot_username: str) -> InlineKeyboardMarkup:
        add_url = f"https://t.me/{bot_username}?startgroup=true" if bot_username else "https://t.me"
        return InlineKeyboardMarkup([
            [_btn("➕ Add me to your group", add_url, style="success", url=True)],
            [_btn("📖 Help", f"gw:{self.bot_id}:nav:help", style="primary"),
             _btn("ℹ️ About", f"gw:{self.bot_id}:nav:about", style="primary")],
            [_btn("👤 Owner", f"tg://user?id={self.owner_id}", url=True),
             _btn("🏠 Home", f"gw:{self.bot_id}:nav:home")],
        ])

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
                InlineKeyboardButton(f"↩️ Undo ({undo})",
                                     callback_data=f"gw:{self.bot_id}:undo:{chat_id or message.chat.id}:{target_id}:{undo}"),
                InlineKeyboardButton("✅ Keep", callback_data=f"gw:{self.bot_id}:keep"),
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
            await m.reply_text(self._start_text("home"),
                               reply_markup=self._start_markup(username),
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.private & filters.command("help"))
        async def _dm_help(c, m: Message):
            me = getattr(c, "me", None)
            username = getattr(me, "username", "") or ""
            await m.reply_text(self._start_text("help"),
                               reply_markup=self._start_markup(username),
                               parse_mode=enums.ParseMode.HTML)

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
            await m.reply_text(self._settings_text("main", s, str(chat_id)),
                               parse_mode=enums.ParseMode.HTML)
            await m.reply_text("ℹ️ Buttons work in the group: open /settings there to change switches.",
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("help"))
        async def _help(_c, m: Message):
            await m.reply_text(
                f"{_bar('🛡️ COMMANDS')}\n\n"
                "<b>Admins</b>\n"
                "/warn /unwarn — warn or clear warnings\n"
                "/mute /tmute 12h /unmute — silence a member\n"
                "/kick — remove (can rejoin)\n"
                "/ban /tban 7d /sban /unban — ban, timed, silent, undo\n"
                "/purge 20 — delete recent messages\n"
                "/pin /unpin — manage pins\n"
                "/rules /setrules — group rules\n"
                "/filter /stop /filters — keyword auto-replies\n"
                "/report — (reply) send a report to admins by DM\n"
                "/save /get /notes /clear — saved notes (#trigger works too)\n"
                "/welcome — preview greetings; /welcome on|off toggles them\n"
                "/setwelcome /setgoodbye — greeting texts\n"
                "/whois — member card (admins get action buttons)\n"
                "/approve /unapprove /approved — trusted members exempt from automation\n"
                "In DM: /connect &lt;group_id&gt; then moderate by ID; /settings views settings\n"
                "/setlog &lt;chat_id&gt; — post moderation logs to another chat\n"
                "/modlog — recent moderation actions\n"
                "/afk — mark yourself away; /export /import — settings backup\n"
                "/blocksticker — (reply to a sticker) block just that sticker\n"
                "/settings — group settings (buttons)\n\n"
                "<i>Reply to a member or use their user ID. Times: 30m, 12h, 7d, 2w.</i>",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("settings"))
        async def _settings(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, m.chat.id)
            await m.reply_text(self._settings_text("main", s, m.chat.title or ""),
                               reply_markup=self._settings_markup("main", s),
                               parse_mode=enums.ParseMode.HTML)

        @app.on_callback_query(filters.regex(rf"^gw:{bot_id}:"))
        async def _settings_cb(c, q: CallbackQuery):
            parts = (q.data or "").split(":")
            action = parts[2] if len(parts) > 2 else ""
            if action == "nav":
                page = parts[3] if len(parts) > 3 else "home"
                me = getattr(c, "me", None)
                username = getattr(me, "username", "") or ""
                try:
                    await q.message.edit_text(self._start_text(page),
                                              reply_markup=self._start_markup(username),
                                              parse_mode=enums.ParseMode.HTML)
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
                elif key in ("captcha_on", "raid_on"):
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
            elif action == "floodaction":
                order = ["mute", "kick", "ban"]
                nxt = order[(order.index(s.get("flood_action", "mute")) + 1) % len(order)]
                s = await store.update_settings(bot_id, chat_id, {"flood_action": nxt})
                page = "flood"
            else:
                return await q.answer("Unknown control.", show_alert=True)
            await q.message.edit_text(self._settings_text(page, s, q.message.chat.title or ""),
                                      reply_markup=self._settings_markup(page, s),
                                      parse_mode=enums.ParseMode.HTML)
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
                if not notes:
                    return await m.reply_text("📝 No notes yet. Admins: <code>/save faq &lt;text&gt;</code>",
                                              parse_mode=enums.ParseMode.HTML)
                listing = ", ".join(sorted(notes))
                return await m.reply_text(f"{_bar('📝 NOTES')}\n\n{_esc(listing)}\n\n<i>Open one with /get or #trigger.</i>",
                                          parse_mode=enums.ParseMode.HTML)
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
            await m.reply_text(
                f"{_bar('👤 MEMBER')}\n\n"
                f"Name: <b>{_esc(label)}</b>\nID: <code>{target_id}</code>\n"
                f"Status: <b>{_esc(getattr(status, 'value', status) or 'unknown')}</b>\n"
                f"Warnings: <b>{warns}</b>/{s.get('warn_limit', 3)}",
                parse_mode=enums.ParseMode.HTML, reply_markup=markup)

        @app.on_message(filters.group & filters.command("modlog"))
        async def _modlog(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            entries = await store.recent_actions(bot_id, m.chat.id, limit=10)
            if not entries:
                return await m.reply_text("📋 No moderation actions recorded yet.",
                                          parse_mode=enums.ParseMode.HTML)
            lines = []
            for e in entries:
                line = f"• <b>{_esc(e.get('action'))}</b> → <code>{e.get('target_id')}</code> by <code>{e.get('actor_id')}</code>"
                if e.get("reason"):
                    line += f" — {_esc(e['reason'])}"
                lines.append(line)
            await m.reply_text(f"{_bar('📋 MODERATION LOG')}\n\n" + "\n".join(lines),
                               parse_mode=enums.ParseMode.HTML)

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
                if not approved:
                    return await m.reply_text("🤝 No approved members yet.", parse_mode=enums.ParseMode.HTML)
                lines = "\n".join(
                    f"• <code>{uid}</code>" + (f" — {_esc(r)}" if r else "")
                    for uid, r in sorted(approved.items()))
                return await m.reply_text(f"{_bar('🤝 APPROVED MEMBERS')}\n\n{lines}",
                                          parse_mode=enums.ParseMode.HTML)
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
            ["blocksticker", "unblocksticker", "blockedstickers"]))
        async def _stickers(c, m: Message):
            cmd = (m.command or ["blockedstickers"])[0].lower()
            s = await store.get_settings(bot_id, m.chat.id)
            blocked = list(s.get("blocked_stickers") or [])
            if cmd == "blockedstickers":
                return await m.reply_text(
                    f"🚫 <b>{len(blocked)}</b> sticker(s) blocked in this group.",
                    parse_mode=enums.ParseMode.HTML)
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            replied = m.reply_to_message
            sticker = getattr(replied, "sticker", None) if replied else None
            if sticker is None:
                return await m.reply_text("Reply to the sticker you want to manage.",
                                          parse_mode=enums.ParseMode.HTML)
            unique = sticker.file_unique_id
            if cmd == "blocksticker":
                if unique not in blocked:
                    blocked.append(unique)
                    await store.update_settings(bot_id, m.chat.id, {"blocked_stickers": blocked})
                return await m.reply_text("🚫 Sticker blocked — I'll delete it here from now on.",
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
            await m.reply_text(
                f"{_bar('💬 CHAT INFO')}\n\n"
                f"Title: <b>{_esc(m.chat.title or '—')}</b>\n"
                f"ID: <code>{m.chat.id}</code>\n"
                f"Members: <b>{count}</b>\n"
                f"Shield: {'on' if s.get('shield') else 'off'} · Greetings: {'on' if s.get('greetings') else 'off'} · "
                f"Federation: {s.get('fed_id') or 'none'}",
                parse_mode=enums.ParseMode.HTML)

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

        @app.on_message(filters.group & filters.command(["filter", "stop", "filters"]))
        async def _filters_cmd(c, m: Message):
            cmd = (m.command or ["filters"])[0].lower()
            s = await store.get_settings(bot_id, m.chat.id)
            replies = dict(s.get("replies") or {})
            if cmd == "filters":
                if not replies:
                    return await m.reply_text("💬 No auto-replies yet. Admins: <code>/filter hello Hi there!</code>",
                                              parse_mode=enums.ParseMode.HTML)
                lines = "\n".join(f"• <b>{_esc(k)}</b> → {_esc(v)[:60]}" for k, v in sorted(replies.items()))
                return await m.reply_text(f"{_bar('💬 AUTO-REPLIES')}\n\n{lines}",
                                          parse_mode=enums.ParseMode.HTML)
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            parts = (m.text or "").split(maxsplit=2 if cmd == "filter" else 1)
            if cmd == "filter":
                if len(parts) < 3:
                    return await m.reply_text("Usage: <code>/filter &lt;word&gt; &lt;reply&gt;</code>",
                                              parse_mode=enums.ParseMode.HTML)
                replies[parts[1].lower()[:40]] = parts[2].strip()[:500]
                await store.update_settings(bot_id, m.chat.id, {"replies": replies})
                return await m.reply_text(f"✅ Auto-reply saved for <b>{_esc(parts[1].lower())}</b>.",
                                          parse_mode=enums.ParseMode.HTML)
            if len(parts) < 2 or parts[1].lower() not in replies:
                return await m.reply_text("That auto-reply doesn't exist.", parse_mode=enums.ParseMode.HTML)
            replies.pop(parts[1].lower())
            await store.update_settings(bot_id, m.chat.id, {"replies": replies})
            await m.reply_text("🗑 Auto-reply removed.", parse_mode=enums.ParseMode.HTML)

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
                InlineKeyboardButton("🗑 Delete", callback_data=f"gw:{bot_id}:rep:{m.chat.id}:{reported.id}:{offender.id}:del"),
                InlineKeyboardButton("🔨 Ban", callback_data=f"gw:{bot_id}:rep:{m.chat.id}:{reported.id}:{offender.id}:ban"),
                InlineKeyboardButton("✅ Ignore", callback_data=f"gw:{bot_id}:keep"),
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
                            InlineKeyboardButton("✅ I'm human — let me chat",
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
            if m.from_user is None:
                return
            s = await store.get_settings(bot_id, m.chat.id)
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
                await m.reply_text(_esc(hit[1])[:1000], parse_mode=enums.ParseMode.HTML)
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
            if sticker_blocked(m, s.get("blocked_stickers") or []):
                try:
                    await m.delete()
                except Exception:
                    pass
                return
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
    return handlers


# Clone-worker command list (kept separate from every other bot surface).
GROUP_WORKER_COMMANDS = [
    ("start", "About this group-management clone"),
    ("help", "List group commands"),
    ("settings", "Group settings & switches (admins)"),
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
    ("filter", "Auto-reply: /filter word reply (admins)"),
    ("stop", "Remove an auto-reply (admins)"),
    ("filters", "List auto-replies"),
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
    ("language", "Show or set my language"),
    ("promote", "Promote a member to admin (admins)"),
    ("demote", "Demote an admin to member (admins)"),
    ("title", "Set the group title (admins)"),
    ("chatinfo", "Group info & status"),
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

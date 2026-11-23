"""Telegram handlers for one clone running the Group Management worker.

Command names (``/panel``, ``/shield``, ``/modwarn`` …) belong to this
worker only — they are deliberately different from the main bot's
group-management plugin so clones behave like their own product.
"""
from __future__ import annotations

import html
import time
from datetime import datetime, timedelta, timezone

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .logic import FloodWatch, content_violation, parse_duration, render_greeting

_ADMIN_STATUSES = (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)
_MEMBER_STATUSES = (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED)


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


async def _status(client: Client, chat_id: int, user_id: int):
    try:
        member = await client.get_chat_member(chat_id, user_id)
    except Exception:
        return None
    return getattr(member, "status", None)


async def _is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    return await _status(client, chat_id, user_id) in _ADMIN_STATUSES


def _target_from(message: Message) -> tuple[int | None, str]:
    reply = message.reply_to_message
    if reply is not None and reply.from_user is not None:
        user = reply.from_user
        return int(user.id), user.first_name or str(user.id)
    args = (message.command or [])[1:]
    if args and args[0].lstrip("-").isdigit():
        return int(args[0]), args[0]
    return None, ""


def _reason_from(message: Message, *, skip: int = 0) -> str:
    args = (message.command or [])[1 + skip:]
    return " ".join(args)[:200]


class _BotHandlers:
    def __init__(self, *, bot_id: int, owner_id: int, store):
        self.bot_id = int(bot_id)
        self.owner_id = int(owner_id)
        self.store = store
        self.flood = FloodWatch()

    # ── panel ──────────────────────────────────────────────────────
    def _panel_markup(self, settings: dict) -> InlineKeyboardMarkup:
        shield = "🟢 Shield ON" if settings.get("shield") else "🔴 Shield OFF"
        greet = "🟢 Greetings ON" if settings.get("greetings") else "🔴 Greetings OFF"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(shield, callback_data=f"gw:{self.bot_id}:shield"),
             InlineKeyboardButton(greet, callback_data=f"gw:{self.bot_id}:greet")],
            [InlineKeyboardButton("❌ Close", callback_data=f"gw:{self.bot_id}:close")],
        ])

    async def _panel_text(self, chat_id: int) -> str:
        s = await self.store.get_settings(self.bot_id, chat_id)
        kinds = ", ".join(s.get("guard_kinds") or []) or "none"
        flood = (f"{s['flood_limit']}/{s['flood_window']:g}s → {s['flood_action']}"
                 if s.get("flood_limit") else "off")
        return (
            "🛡 <b>Group Control Room</b>\n\n"
            f"Shield: <b>{'ON' if s.get('shield') else 'OFF'}</b> (guarding: {_esc(kinds)})\n"
            f"Greetings: <b>{'ON' if s.get('greetings') else 'OFF'}</b>\n"
            f"Warnings: <b>{s.get('warn_limit', 3)}</b> → {s.get('warn_action', 'mute')}\n"
            f"Flood watch: <b>{flood}</b>\n\n"
            "Reply to a member and use /modwarn, /modmute, /modkick or /modban — "
            "or open /shield to pick what the shield blocks."
        )

    # ── moderation core ────────────────────────────────────────────
    async def _punish(self, client: Client, message: Message, action: str):
        if not await _is_admin(client, message.chat.id, message.from_user.id):
            return await message.reply_text("🔒 Only group admins can do that.")
        target_id, label = _target_from(message)
        if target_id is None:
            return await message.reply_text("Reply to a member or give their user ID.")
        if await _status(client, message.chat.id, target_id) in _ADMIN_STATUSES:
            return await message.reply_text("🛡 That member is an admin — I won't act on them.")
        reason = _reason_from(message, skip=0 if message.reply_to_message else 1)
        settings = await self.store.get_settings(self.bot_id, message.chat.id)
        try:
            if action == "warn":
                count, reached = await self.store.add_warn(
                    self.bot_id, message.chat.id, target_id, settings.get("warn_limit", 3))
                if not reached:
                    return await message.reply_text(
                        f"⚠️ {_esc(label)} warned ({count}/{settings.get('warn_limit', 3)})."
                        + (f" Reason: {_esc(reason)}" if reason else ""))
                action = settings.get("warn_action", "mute")
                note = f"Warning limit reached → {action}."
            else:
                note = ""
            if action == "mute":
                await client.restrict_chat_member(message.chat.id, target_id, ChatPermissions())
            elif action == "kick":
                await client.ban_chat_member(message.chat.id, target_id)
                await client.unban_chat_member(message.chat.id, target_id)
            elif action == "ban":
                await client.ban_chat_member(message.chat.id, target_id)
            elif action == "unban":
                await client.unban_chat_member(message.chat.id, target_id)
            else:
                return await message.reply_text("Unknown action.")
        except Exception as exc:
            return await message.reply_text(f"❌ Telegram refused that action: {_esc(str(exc)[:120])}")
        return await message.reply_text(
            f"✅ {_esc(action)} applied to {_esc(label)}."
            + (f" {note}" if note else "")
            + (f" Reason: {_esc(reason)}" if reason else ""))

    # ── registration ───────────────────────────────────────────────
    def attach(self, app: Client) -> None:
        bot_id = self.bot_id
        store = self.store

        @app.on_message(filters.private & filters.command("start"))
        async def _start(_c, m: Message):
            await m.reply_text(
                "🛡 <b>Group Management clone</b>\n\nAdd me to a group, make me an admin, "
                "then open /panel in that group to run it. /help lists what I can do.",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("panel"))
        async def _panel(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 Only group admins can open the control room.")
            s = await store.get_settings(bot_id, m.chat.id)
            await m.reply_text(await self._panel_text(m.chat.id),
                               reply_markup=self._panel_markup(s),
                               parse_mode=enums.ParseMode.HTML)

        @app.on_callback_query(filters.regex(rf"^gw:{bot_id}:"))
        async def _panel_cb(c, q: CallbackQuery):
            action = (q.data or "").rsplit(":", 1)[-1]
            if action == "close":
                return await q.message.delete()
            if not await _is_admin(c, q.message.chat.id, q.from_user.id):
                return await q.answer("Admins only.", show_alert=True)
            s = await store.get_settings(bot_id, q.message.chat.id)
            if action == "shield":
                s = await store.update_settings(bot_id, q.message.chat.id, {"shield": not s.get("shield")})
            elif action == "greet":
                s = await store.update_settings(bot_id, q.message.chat.id, {"greetings": not s.get("greetings")})
            else:
                return await q.answer("Unknown control.", show_alert=True)
            await q.message.edit_text(await self._panel_text(q.message.chat.id),
                                      reply_markup=self._panel_markup(s),
                                      parse_mode=enums.ParseMode.HTML)
            await q.answer("Saved.")

        @app.on_message(filters.group & filters.command(
            ["modwarn", "modmute", "modkick", "modban", "modunban"]))
        async def _mod(c, m: Message):
            action = (m.command or ["modwarn"])[0].lower().removeprefix("mod")
            await self._punish(c, m, action)

        @app.on_message(filters.group & filters.command("modpurge"))
        async def _purge(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 Only group admins can do that.")
            args = (m.command or [])[1:]
            if len(args) != 1 or not args[0].isdigit() or not 1 <= int(args[0]) <= 100:
                return await m.reply_text("Usage: /modpurge <1–100>")
            ids = []
            async for item in c.get_chat_history(m.chat.id, limit=int(args[0]) + 1):
                if item.id != m.id:
                    ids.append(item.id)
                if len(ids) >= int(args[0]):
                    break
            if ids:
                await c.delete_messages(m.chat.id, ids)
            await m.reply_text(f"🧹 Deleted {len(ids)} messages.")

        @app.on_message(filters.group & filters.new_chat_members)
        async def _joined(c, m: Message):
            s = await store.get_settings(bot_id, m.chat.id)
            if not s.get("greetings"):
                return
            for user in m.new_chat_members or []:
                if getattr(user, "is_bot", False):
                    continue
                text = render_greeting(s.get("greet_text") or "", name=_esc(user.first_name or "member"),
                                       chat=_esc(m.chat.title or "this group"), user_id=user.id)
                await c.send_message(m.chat.id, text, parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.left_chat_member)
        async def _left(c, m: Message):
            s = await store.get_settings(bot_id, m.chat.id)
            if not s.get("greetings") or getattr(m.left_chat_member, "is_bot", False):
                return
            user = m.left_chat_member
            text = render_greeting(s.get("farewell_text") or "", name=_esc(user.first_name or "member"),
                                   chat=_esc(m.chat.title or "this group"), user_id=user.id)
            await c.send_message(m.chat.id, text, parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.incoming & ~filters.service, group=5)
        async def _guard(c, m: Message):
            if m.from_user is None:
                return
            s = await store.get_settings(bot_id, m.chat.id)
            if not s.get("shield") and not s.get("flood_limit"):
                return
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
                action = s.get("flood_action", "mute")
                try:
                    if action == "mute":
                        await c.restrict_chat_member(m.chat.id, m.from_user.id, ChatPermissions())
                    elif action == "kick":
                        await c.ban_chat_member(m.chat.id, m.from_user.id)
                        await c.unban_chat_member(m.chat.id, m.from_user.id)
                    elif action == "ban":
                        await c.ban_chat_member(m.chat.id, m.from_user.id)
                except Exception:
                    pass


def attach_group_handlers(app: Client, *, bot_id: int, owner_id: int, store) -> _BotHandlers:
    handlers = _BotHandlers(bot_id=bot_id, owner_id=owner_id, store=store)
    handlers.attach(app)
    return handlers


# Clone-worker command list (kept separate from every other bot surface).
GROUP_WORKER_COMMANDS = [
    ("start", "About this group-management clone"),
    ("panel", "Open the group control room (admins)"),
    ("modwarn", "Warn a member (reply or user ID)"),
    ("modmute", "Mute a member (reply or user ID)"),
    ("modkick", "Remove a member (reply or user ID)"),
    ("modban", "Ban a member (reply or user ID)"),
    ("modunban", "Unban a member (reply or user ID)"),
    ("modpurge", "Delete recent messages: /modpurge 20"),
]

"""Telegram handlers for one clone running the Group Management worker.

Normal Rose-style commands (``/ban``, ``/kick``, ``/mute``, ``/warn`` …)
with a polished button control room at ``/panel``. This worker is its
own codebase for clones — it shares no code with the main bot's
group-management plugin.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .logic import GUARD_KINDS, FloodWatch, content_violation, parse_duration, render_greeting

_ADMIN_STATUSES = (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)

_ON = "🟢"
_OFF = "🔴"


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


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

    # ── control room ───────────────────────────────────────────────
    def _panel_markup(self, s: dict) -> InlineKeyboardMarkup:
        kinds = set(s.get("guard_kinds") or [])
        kind_rows = []
        row = []
        for kind in GUARD_KINDS:
            mark = _ON if kind in kinds else _OFF
            row.append(InlineKeyboardButton(f"{mark} {kind}", callback_data=f"gw:{self.bot_id}:kind:{kind}"))
            if len(row) == 3:
                kind_rows.append(row)
                row = []
        if row:
            kind_rows.append(row)
        flood_on = bool(s.get("flood_limit"))
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{_ON if s.get('shield') else _OFF} Shield",
                                  callback_data=f"gw:{self.bot_id}:shield"),
             InlineKeyboardButton(f"{_ON if s.get('greetings') else _OFF} Greetings",
                                  callback_data=f"gw:{self.bot_id}:greet")],
            *kind_rows,
            [InlineKeyboardButton(f"{_ON if flood_on else _OFF} Flood watch",
                                  callback_data=f"gw:{self.bot_id}:flood"),
             InlineKeyboardButton(f"⚠️ Warn act: {s.get('warn_action', 'mute')}",
                                  callback_data=f"gw:{self.bot_id}:warnaction")],
            [InlineKeyboardButton("❌ Close", callback_data=f"gw:{self.bot_id}:close")],
        ])

    async def _panel_text(self, chat_id: int) -> str:
        s = await self.store.get_settings(self.bot_id, chat_id)
        kinds = ", ".join(s.get("guard_kinds") or []) or "nothing yet"
        flood = (f"{s['flood_limit']} msgs / {s['flood_window']:g}s → {s['flood_action']}"
                 if s.get("flood_limit") else "off")
        return (
            f"{_bar('🛡️ GROUP CONTROL ROOM')}\n\n"
            f"🛡 <b>Shield</b> — {'on' if s.get('shield') else 'off'} · guarding: {_esc(kinds)}\n"
            f"👋 <b>Greetings</b> — {'on' if s.get('greetings') else 'off'}\n"
            f"⚠️ <b>Warnings</b> — {s.get('warn_limit', 3)} warns → {s.get('warn_action', 'mute')}\n"
            f"🌊 <b>Flood watch</b> — {_esc(flood)}\n\n"
            "<i>Tap a button to change settings. Moderation: reply to a member with "
            "/warn, /mute, /kick or /ban — or use an ID: /ban 123456 spam.</i>"
        )

    # ── moderation core ────────────────────────────────────────────
    async def _act(self, client: Client, message: Message, action: str):
        silent = action.startswith("s")
        base = action[1:] if silent and action != "sban" else action
        if action == "sban":
            base = "ban"
        if not await _is_admin(client, message.chat.id, message.from_user.id):
            return await message.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
        target_id, label, rest = _resolve(message)
        if target_id is None:
            return await message.reply_text(
                "👤 Reply to a member, or give a user ID — e.g. <code>/ban 123456 reason</code>.",
                parse_mode=enums.ParseMode.HTML)
        if await _status(client, message.chat.id, target_id) in _ADMIN_STATUSES:
            return await message.reply_text("🛡 That member is an admin — I won't act on them.")
        settings = await self.store.get_settings(self.bot_id, message.chat.id)
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
                    self.bot_id, message.chat.id, target_id, settings.get("warn_limit", 3))
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
                await self.store.reset_warns(self.bot_id, message.chat.id, target_id)
                return await message.reply_text(f"✅ Warnings cleared for <b>{_esc(label)}</b>.",
                                                parse_mode=enums.ParseMode.HTML)
            if base in ("ban", "tban"):
                await client.ban_chat_member(message.chat.id, target_id, until_date=until)
            elif base == "kick":
                await client.ban_chat_member(message.chat.id, target_id)
                await client.unban_chat_member(message.chat.id, target_id)
            elif base == "unban":
                await client.unban_chat_member(message.chat.id, target_id)
            elif base in ("mute", "tmute"):
                await client.restrict_chat_member(
                    message.chat.id, target_id, ChatPermissions(), until_date=until)
            elif base == "unmute":
                await client.restrict_chat_member(
                    message.chat.id, target_id,
                    ChatPermissions(can_send_messages=True, can_send_media_messages=True,
                                    can_send_other_messages=True, can_add_web_page_previews=True))
            else:
                return await message.reply_text("Unknown action.")
        except Exception as exc:
            return await message.reply_text(
                f"❌ <b>Telegram refused that.</b>\n{_esc(str(exc)[:120])}",
                parse_mode=enums.ParseMode.HTML)
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

    # ── registration ───────────────────────────────────────────────
    def attach(self, app: Client) -> None:
        bot_id = self.bot_id
        store = self.store

        @app.on_message(filters.private & filters.command("start"))
        async def _start(_c, m: Message):
            await m.reply_text(
                f"{_bar('🛡️ GROUP MANAGEMENT')}\n\n"
                "Add me to a group, make me an admin, then open /panel there.\n"
                "I can greet members, guard the chat, and help admins with "
                "/warn, /mute, /kick and /ban.",
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
                "/panel — button control room\n\n"
                "<i>Reply to a member or use their user ID. Times: 30m, 12h, 7d, 2w.</i>",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("panel"))
        async def _panel(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, m.chat.id)
            await m.reply_text(await self._panel_text(m.chat.id),
                               reply_markup=self._panel_markup(s),
                               parse_mode=enums.ParseMode.HTML)

        @app.on_callback_query(filters.regex(rf"^gw:{bot_id}:"))
        async def _panel_cb(c, q: CallbackQuery):
            parts = (q.data or "").split(":")
            action = parts[2] if len(parts) > 2 else ""
            if action == "close":
                return await q.message.delete()
            if not await _is_admin(c, q.message.chat.id, q.from_user.id):
                return await q.answer("Admins only.", show_alert=True)
            s = await store.get_settings(bot_id, q.message.chat.id)
            if action == "shield":
                s = await store.update_settings(bot_id, q.message.chat.id, {"shield": not s.get("shield")})
            elif action == "greet":
                s = await store.update_settings(bot_id, q.message.chat.id, {"greetings": not s.get("greetings")})
            elif action == "kind" and len(parts) > 3 and parts[3] in GUARD_KINDS:
                kinds = set(s.get("guard_kinds") or [])
                kinds.symmetric_difference_update({parts[3]})
                s = await store.update_settings(bot_id, q.message.chat.id,
                                                {"guard_kinds": sorted(kinds), "shield": True})
            elif action == "flood":
                s = await store.update_settings(
                    bot_id, q.message.chat.id,
                    {"flood_limit": 0 if s.get("flood_limit") else 6})
            elif action == "warnaction":
                order = ["mute", "kick", "ban"]
                nxt = order[(order.index(s.get("warn_action", "mute")) + 1) % len(order)]
                s = await store.update_settings(bot_id, q.message.chat.id, {"warn_action": nxt})
            else:
                return await q.answer("Unknown control.", show_alert=True)
            await q.message.edit_text(await self._panel_text(q.message.chat.id),
                                      reply_markup=self._panel_markup(s),
                                      parse_mode=enums.ParseMode.HTML)
            await q.answer("Saved ✅")

        @app.on_message(filters.group & filters.command(
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
    ("panel", "Open the group control room (admins)"),
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
]

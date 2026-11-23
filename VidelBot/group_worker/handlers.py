"""Telegram handlers for one clone running the Group Management worker.

Normal Rose-style commands (``/ban``, ``/kick``, ``/mute``, ``/warn`` …)
with a polished worker-style settings panel at ``/settings``. This worker is its
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

from .logic import GUARD_KINDS, FloodWatch, JoinWatch, content_violation, in_quiet_hours, match_reply, parse_duration, render_greeting

_ADMIN_STATUSES = (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)

_ON = "🟢"
_OFF = "🔴"


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _btn(text: str, data: str, style: str | None = None) -> InlineKeyboardButton:
    """Inline button with Bot API button styling when the client supports it."""
    if style:
        try:
            return InlineKeyboardButton(text, callback_data=data, style=style)
        except TypeError:
            pass
    return InlineKeyboardButton(text, callback_data=data)


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
            [InlineKeyboardButton("🌊 Flood watch ›", callback_data=f"gw:{bot}:page:flood"),
             InlineKeyboardButton("❌ Close", callback_data=f"gw:{bot}:close")],
        ])

    def _settings_text(self, page: str, s: dict, chat_title: str = "") -> str:
        head = _bar("⚙️ SETTINGS") + (f"\n<i>{_esc(chat_title)}</i>" if chat_title else "")
        if page == "security":
            return (f"{head}\n\n🤖 <b>Security</b>\n"
                    f"CAPTCHA: {'on' if s.get('captcha_on') else 'off'} · "
                    f"Anti-raid: {'on' if s.get('raid_on') else 'off'}\n"
                    "<i>With CAPTCHA, new members stay muted until they tap the verify button. "
                    "The anti-raid shield temporarily bans joiners during join bursts.</i>")
        if page == "greet":
            return (f"{head}\n\n👋 <b>Greetings</b> — {'on' if s.get('greetings') else 'off'}\n"
                    "<i>Set texts with /setwelcome and /setgoodbye. "
                    "Placeholders: {name} {chat} {id}.</i>")
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
            f"{head}\n\n"
            f"🛡 <b>Shield</b> — {'on' if s.get('shield') else 'off'} · {_esc(kinds)}\n"
            f"👋 <b>Greetings</b> — {'on' if s.get('greetings') else 'off'}\n"
            f"⚠️ <b>Warnings</b> — {s.get('warn_limit', 3)} → {s.get('warn_action', 'mute')}\n"
            f"🌊 <b>Flood watch</b> — {_esc(flood)}\n"
            f"📜 <b>Rules</b> — {'set' if s.get('rules') else 'not set'} · "
            f"💬 <b>Auto-replies</b> — {len(s.get('replies') or {})}\n"
            f"📩 <b>DM alerts</b> — {'on' if s.get('dm_alerts') else 'off'} · "
            f"🌙 <b>Night</b> — {'on' if s.get('night_on') else 'off'} · "
            f"🤖 <b>CAPTCHA</b> — {'on' if s.get('captcha_on') else 'off'} · "
            f"🚨 <b>Anti-raid</b> — {'on' if s.get('raid_on') else 'off'} · "
            f"📝 <b>Notes</b> — {len(s.get('notes') or {})}\n\n"
            "<i>Only group admins can change settings.</i>"
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
        try:
            await client.send_reaction(message.chat.id, message.id,
                                       {"warn": "⚠️", "ban": "🔨", "tban": "🔨", "sban": "🔨",
                                        "kick": "👢", "mute": "🤐", "tmute": "🤐"}.get(base, "✅"))
        except Exception:
            pass  # reactions are decoration; never fail the action over them
        await self._notify_owner(client, message, base, target_id, label, reason)
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
                            target_id: int, label: str, reason: str = "") -> None:
        """Best-effort DM to the clone owner with an undo button."""
        settings = await self.store.get_settings(self.bot_id, message.chat.id)
        if not settings.get("dm_alerts") or self.owner_id == message.from_user.id:
            return
        undo = {"ban": "unban", "tban": "unban", "mute": "unmute", "tmute": "unmute"}.get(action)
        markup = None
        if undo:
            markup = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"↩️ Undo ({undo})",
                                     callback_data=f"gw:{self.bot_id}:undo:{message.chat.id}:{target_id}:{undo}"),
                InlineKeyboardButton("✅ Keep", callback_data=f"gw:{self.bot_id}:keep"),
            ]])
        try:
            await client.send_message(
                self.owner_id,
                f"📩 <b>Moderation alert</b>\n\n"
                f"Group: <b>{_esc(message.chat.title or message.chat.id)}</b>\n"
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
        async def _start(_c, m: Message):
            await m.reply_text(
                f"{_bar('🛡️ GROUP MANAGEMENT')}\n\n"
                "Add me to a group, make me an admin, then open /settings there.\n"
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
                "/pin /unpin — manage pins\n"
                "/rules /setrules — group rules\n"
                "/filter /stop /filters — keyword auto-replies\n"
                "/report — (reply) send a report to admins by DM\n"
                "/save /get /notes /clear — saved notes (#trigger works too)\n"
                "/setwelcome /setgoodbye — greeting texts\n"
                "/whois — member card (admins get action buttons)\n"
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
                elif key in ("captcha_on", "raid_on"):
                    s = await store.update_settings(bot_id, chat_id, {key: not s.get(key)})
                    page = "security"
                elif key == "greet_dm":
                    s = await store.update_settings(bot_id, chat_id, {"greet_dm": not s.get("greet_dm")})
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

        @app.on_message(filters.group & filters.command(["setwelcome", "setgoodbye"]))
        async def _set_greeting(c, m: Message):
            cmd = (m.command or ["setwelcome"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            parts = (m.text or "").split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                return await m.reply_text(
                    f"Usage: <code>/{cmd} Hello {{name}}, welcome to {{chat}}!</code>",
                    parse_mode=enums.ParseMode.HTML)
            key = "greet_text" if cmd == "setwelcome" else "farewell_text"
            await store.update_settings(bot_id, m.chat.id, {key: parts[1].strip()[:1000], "greetings": True})
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
                await c.send_message(m.chat.id, text, parse_mode=enums.ParseMode.HTML)
                if s.get("greet_dm"):
                    try:
                        await c.send_message(user.id, text, parse_mode=enums.ParseMode.HTML)
                    except Exception:
                        pass

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
            if not (s.get("shield") or s.get("flood_limit") or s.get("replies")
                    or s.get("notes") or s.get("night_on")):
                return
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
            if s.get("night_on") and in_quiet_hours(
                    datetime.now(timezone.utc).hour,
                    int(s.get("night_start", 23)), int(s.get("night_end", 7))):
                if await _status(c, m.chat.id, m.from_user.id) == enums.ChatMemberStatus.MEMBER:
                    try:
                        await m.delete()
                    except Exception:
                        pass
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
    ("setwelcome", "Set the welcome text (admins)"),
    ("setgoodbye", "Set the goodbye text (admins)"),
    ("save", "Save a note: /save faq text (admins)"),
    ("get", "Open a note: /get faq"),
    ("notes", "List saved notes"),
    ("clear", "Delete a note (admins)"),
    ("whois", "Member card with admin actions"),
]

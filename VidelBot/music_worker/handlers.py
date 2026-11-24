"""Telegram handlers for one clone running the Music worker.

Queue management, settings and rich UI, plus stage-2 streaming: with
a ready player (see player.py), /play on a replied audio file
downloads and streams it into the voice chat, /pause /resume /skip
/stop control playback, and the volume setting applies live. Without
a player it queues honestly and says streaming is not set up — it
never fakes playback. Text-title streaming still needs a resolver.
"""
from __future__ import annotations

import html

from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .logic import PlayQueue, format_duration

_ADMIN_STATUSES = (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _bar(title: str) -> str:
    return f"<b>━━━━━━━━━━━━━━━━━━━\n{title}\n━━━━━━━━━━━━━━━━━━━</b>"


def _btn(text: str, data: str, style: str | None = None, url: bool = False) -> InlineKeyboardButton:
    kwargs = {"url": data} if url else {"callback_data": data}
    if style:
        try:
            return InlineKeyboardButton(text, style=style, **kwargs)
        except TypeError:
            pass
    return InlineKeyboardButton(text, **kwargs)


async def _is_admin(client: Client, chat_id: int, user_id: int) -> bool:
    try:
        member = await client.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return getattr(member, "status", None) in _ADMIN_STATUSES


class _BotHandlers:
    def __init__(self, *, bot_id: int, owner_id: int, store, player=None):
        self.bot_id = int(bot_id)
        self.owner_id = int(owner_id)
        self.store = store
        self.queue = PlayQueue()
        self.player = player  # future voice-chat engine; None in stage 1

    # ── rich pages ─────────────────────────────────────────────────
    def _home_text(self) -> str:
        return (
            f"{_bar('🎵 MUSIC')}\n\n"
            "👋 <b>Hey! I play music in group voice chats.</b>\n\n"
            "▶️ /play &lt;song or reply to audio&gt; — queue a track\n"
            "🎶 /queue — see what's playing & next\n"
            "⏭ /skip · ⏹ /stop · ⚙️ /settings\n\n"
            "<i>Short, button-first, no spam.</i>"
        )

    def _home_markup(self, bot_username: str) -> InlineKeyboardMarkup:
        add_url = f"https://t.me/{bot_username}?startgroup=true" if bot_username else "https://t.me"
        return InlineKeyboardMarkup([
            [_btn("➕ Add me to your group", add_url, style="success", url=True)],
            [_btn("👤 Owner", f"tg://user?id={self.owner_id}", url=True)],
        ])

    def _settings_markup(self, s: dict) -> InlineKeyboardMarkup:
        bot = self.bot_id
        return InlineKeyboardMarkup([
            [_btn(f"{'✅' if s.get('dj_mode') else '▫️'} DJ mode (admins only)",
                  f"mw:{bot}:dj", style="success" if s.get("dj_mode") else "danger")],
            [_btn(f"🔊 Volume: {s.get('volume', 100)}%", f"mw:{bot}:volume", style="primary")],
            [_btn("❌ Close", f"mw:{bot}:close")],
        ])

    def _player_ready(self) -> bool:
        return self.player is not None and getattr(self.player, "ready", True)

    def _settings_text(self, s: dict) -> str:
        return (
            f"{_bar('⚙️ MUSIC SETTINGS')}\n\n<blockquote>"
            f"🎧 DJ mode: {'on' if s.get('dj_mode') else 'off'}\n"
            f"🔊 Volume: {s.get('volume', 100)}%\n"
            f"🎶 Streaming engine: {'ready' if self._player_ready() else 'not set up yet'}"
            "</blockquote>"
        )

    async def _allowed(self, client: Client, message: Message) -> bool:
        s = await self.store.get_settings(self.bot_id, message.chat.id)
        if not s.get("dj_mode"):
            return True
        if await _is_admin(client, message.chat.id, message.from_user.id):
            return True
        await message.reply_text("🎧 <b>DJ mode is on</b> — only admins can control the music here.",
                                 parse_mode=enums.ParseMode.HTML)
        return False

    # ── registration ───────────────────────────────────────────────
    def attach(self, app: Client) -> None:
        bot_id = self.bot_id
        store = self.store

        @app.on_message(filters.private & filters.command("start"))
        async def _start(c, m: Message):
            me = getattr(c, "me", None)
            await m.reply_text(self._home_text(),
                               reply_markup=self._home_markup(getattr(me, "username", "") or ""),
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["play", "p"]))
        async def _play(c, m: Message):
            if not await self._allowed(c, m):
                return
            parts = (m.text or "").split(maxsplit=1)
            title = ""
            duration = None
            replied = m.reply_to_message
            if replied is not None and (getattr(replied, "audio", None) or getattr(replied, "voice", None)):
                media = replied.audio or replied.voice
                title = (getattr(media, "title", None) or getattr(media, "file_name", None)
                         or "Audio track")
                duration = getattr(media, "duration", None)
            elif len(parts) > 1:
                title = parts[1].strip()[:120]
            if not title:
                return await m.reply_text("Usage: <code>/play &lt;song name&gt;</code> or reply to an audio file.",
                                          parse_mode=enums.ParseMode.HTML)
            track = {"title": title, "duration": duration,
                     "requested_by": m.from_user.first_name or str(m.from_user.id)}
            # With a live player, a replied audio file can actually stream:
            # download it now so /skip can advance to it later.
            if replied is not None and self._player_ready():
                try:
                    track["source"] = str(await c.download_media(replied))
                except Exception:
                    track["source"] = None
            position = self.queue.enqueue(m.chat.id, track)
            note = ""
            if not self._player_ready():
                note = ("\n\n<i>🎧 Streaming engine isn't set up for this clone yet — "
                        "the track is queued and will play once it's enabled.</i>")
            elif position == 0 and track.get("source"):
                try:
                    await self.player.play(m.chat.id, track["source"])
                except Exception:
                    note = "\n\n<i>⚠️ Couldn't join the voice chat — is one started? The track stays queued.</i>"
            elif position == 0:
                note = ("\n\n<i>🎧 Text search streaming isn't wired yet — "
                        "reply /play to an audio file to stream it.</i>")
            if position == 0:
                await m.reply_text(f"▶️ <b>Now playing:</b> {_esc(title)} · {format_duration(duration)}{note}",
                                   parse_mode=enums.ParseMode.HTML)
            else:
                await m.reply_text(f"🎶 <b>Queued #{position}:</b> {_esc(title)} · {format_duration(duration)}{note}",
                                   parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["queue", "np", "nowplaying"]))
        async def _queue(c, m: Message):
            current = self.queue.now_playing(m.chat.id)
            if current is None:
                return await m.reply_text("🎶 Nothing is queued. Try /play &lt;song&gt;.",
                                          parse_mode=enums.ParseMode.HTML)
            lines = [f"▶️ <b>{_esc(current['title'])}</b> · {format_duration(current.get('duration'))} "
                     f"— by {_esc(current.get('requested_by'))}"]
            for i, track in enumerate(self.queue.upcoming(m.chat.id), start=1):
                lines.append(f"{i}. {_esc(track['title'])} · {format_duration(track.get('duration'))}")
            await m.reply_text(f"{_bar('🎶 QUEUE')}\n\n" + "\n".join(lines),
                               parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["skip", "stop", "remove", "pause", "resume"]))
        async def _control(c, m: Message):
            if not await self._allowed(c, m):
                return
            cmd = (m.command or ["skip"])[0].lower()
            if cmd in ("pause", "resume"):
                if not self._player_ready():
                    return await m.reply_text("🎧 Streaming isn't active for this clone yet.",
                                              parse_mode=enums.ParseMode.HTML)
                try:
                    ok = await (self.player.pause(m.chat.id) if cmd == "pause"
                                else self.player.resume(m.chat.id))
                except Exception:
                    ok = False
                return await m.reply_text(
                    ("⏸ <b>Paused.</b>" if cmd == "pause" else "▶️ <b>Resumed.</b>") if ok
                    else "Nothing is playing right now.",
                    parse_mode=enums.ParseMode.HTML)
            if cmd == "stop":
                cleared = self.queue.clear(m.chat.id)
                if self._player_ready():
                    try:
                        await self.player.stop(m.chat.id)
                    except Exception:
                        pass
                return await m.reply_text(f"⏹ <b>Stopped.</b> Cleared {cleared} track(s).",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "remove":
                args = (m.command or [])[1:]
                if len(args) != 1 or not args[0].isdigit():
                    return await m.reply_text("Usage: <code>/remove &lt;position&gt;</code>",
                                              parse_mode=enums.ParseMode.HTML)
                removed = self.queue.remove(m.chat.id, int(args[0]))
                return await m.reply_text(
                    f"🗑 Removed <b>{_esc(removed['title'])}</b>." if removed else "That position doesn't exist.",
                    parse_mode=enums.ParseMode.HTML)
            nxt = self.queue.skip(m.chat.id)
            await m.reply_text(
                f"⏭ Skipped. Next: <b>{_esc(nxt['title'])}</b>" if nxt else "⏭ Skipped. Queue is empty.",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command("settings"))
        async def _settings(c, m: Message):
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            s = await store.get_settings(bot_id, m.chat.id)
            await m.reply_text(self._settings_text(s), reply_markup=self._settings_markup(s),
                               parse_mode=enums.ParseMode.HTML)

        @app.on_callback_query(filters.regex(rf"^mw:{bot_id}:"))
        async def _settings_cb(c, q: CallbackQuery):
            action = (q.data or "").split(":")[2] if len((q.data or "").split(":")) > 2 else ""
            if action == "close":
                return await q.message.delete()
            if not await _is_admin(c, q.message.chat.id, q.from_user.id):
                return await q.answer("Admins only.", show_alert=True)
            s = await store.get_settings(bot_id, q.message.chat.id)
            if action == "dj":
                s = await store.update_settings(bot_id, q.message.chat.id, {"dj_mode": not s.get("dj_mode")})
            elif action == "volume":
                volume = int(s.get("volume", 100)) + 25
                if volume > 200:
                    volume = 50
                s = await store.update_settings(bot_id, q.message.chat.id, {"volume": volume})
            else:
                return await q.answer("Unknown control.", show_alert=True)
            await q.message.edit_text(self._settings_text(s), reply_markup=self._settings_markup(s),
                                      parse_mode=enums.ParseMode.HTML)
            await q.answer("Saved ✅")


def attach_music_handlers(app: Client, *, bot_id: int, owner_id: int, store, player=None) -> _BotHandlers:
    handlers = _BotHandlers(bot_id=bot_id, owner_id=owner_id, store=store, player=player)
    handlers.attach(app)
    return handlers


MUSIC_WORKER_COMMANDS = [
    ("start", "About this music clone"),
    ("play", "Queue a song: /play <name> or reply to audio"),
    ("queue", "Show the current queue"),
    ("skip", "Skip the current track"),
    ("pause", "Pause playback"),
    ("resume", "Resume playback"),
    ("remove", "Remove a queue position: /remove 2"),
    ("stop", "Stop and clear the queue"),
    ("settings", "Music settings (admins)"),
]

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
import time

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


def _table(rows) -> str:
    """Aligned two-column monospace table (Telegram <pre> block)."""
    rows = [(str(label), str(value)) for label, value in rows if value is not None]
    if not rows:
        return ""
    width = max(len(label) for label, _ in rows)
    body = "\n".join(f"{label.ljust(width)}  {value}" for label, value in rows)
    return f"<pre>{_esc(body)}</pre>"


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
        self.player = player  # VoicePlayer or None (queue-only mode)
        self._suppress_until: dict[int, float] = {}
        self._loaded_chats: set[int] = set()

    async def _ensure_loaded(self, chat_id: int) -> None:
        """Restore this chat's persisted queue once per process run."""
        if self.store is None or int(chat_id) in self._loaded_chats:
            return
        self._loaded_chats.add(int(chat_id))
        try:
            tracks = await self.store.load_queue(self.bot_id, chat_id)
            if tracks and not self.queue.snapshot(chat_id):
                self.queue.restore(chat_id, tracks)
        except Exception:
            pass

    async def _persist(self, chat_id: int) -> None:
        if self.store is None:
            return
        try:
            await self.store.save_queue(self.bot_id, chat_id, self.queue.snapshot(chat_id))
        except Exception:
            pass

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

    # Manual controls (skip/stop/play) make the old stream end too; its
    # StreamEnded event must not advance the queue a second time.
    _SUPPRESS_SECONDS = 5.0

    def _note_manual(self, chat_id: int) -> None:
        self._suppress_until[int(chat_id)] = time.monotonic() + self._SUPPRESS_SECONDS

    async def _advance(self, chat_id: int) -> None:
        """A track ended naturally: play the next queued track, if any."""
        if time.monotonic() < self._suppress_until.get(int(chat_id), 0.0):
            return
        nxt = self.queue.skip(chat_id)
        await self._persist(chat_id)
        if nxt is None:
            if self._player_ready():
                try:
                    await self.player.stop(chat_id)
                except Exception:
                    pass
            return
        if nxt.get("source") and self._player_ready():
            try:
                await self.player.play(chat_id, nxt["source"])
            except Exception:
                pass

    def _settings_text(self, s: dict) -> str:
        table = _table([
            ("🎧 DJ mode", "✅ on" if s.get("dj_mode") else "▫️ off"),
            ("🔊 Volume", f"{s.get('volume', 100)}%"),
            ("🎶 Streaming", "✅ ready" if self._player_ready() else "▫️ not set up yet"),
        ])
        return f"{_bar('⚙️ MUSIC SETTINGS')}\n\n{table}"

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
        if self.player is not None and hasattr(self.player, "bind_auto_advance"):
            self.player.bind_auto_advance(self._advance)

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
            await self._ensure_loaded(m.chat.id)
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
            # With a live player, get a streamable source: download a
            # replied audio file, or resolve a title/link via yt-dlp.
            if self._player_ready():
                if replied is not None:
                    try:
                        track["source"] = str(await c.download_media(replied))
                    except Exception:
                        track["source"] = None
                elif title:
                    from .resolver import resolve_title
                    resolved = await resolve_title(title)
                    if resolved:
                        track.update(resolved)
                        title, duration = resolved["title"], resolved["duration"]
                    else:
                        return await m.reply_text(
                            "🔎 <b>Couldn't find that track.</b> Try another title, a link, "
                            "or reply /play to an audio file.",
                            parse_mode=enums.ParseMode.HTML)
            position = self.queue.enqueue(m.chat.id, track)
            await self._persist(m.chat.id)
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
                note = ("\n\n<i>🎧 No streamable source for this one — "
                        "reply /play to an audio file to stream it.</i>")
            if position == 0:
                await m.reply_text(f"▶️ <b>Now playing:</b> {_esc(title)} · {format_duration(duration)}{note}",
                                   parse_mode=enums.ParseMode.HTML)
            else:
                await m.reply_text(f"🎶 <b>Queued #{position}:</b> {_esc(title)} · {format_duration(duration)}{note}",
                                   parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["queue", "np", "nowplaying"]))
        async def _queue(c, m: Message):
            await self._ensure_loaded(m.chat.id)
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
            await self._ensure_loaded(m.chat.id)
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
                await self._persist(m.chat.id)
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
                await self._persist(m.chat.id)
                return await m.reply_text(
                    f"🗑 Removed <b>{_esc(removed['title'])}</b>." if removed else "That position doesn't exist.",
                    parse_mode=enums.ParseMode.HTML)
            nxt = self.queue.skip(m.chat.id)
            await self._persist(m.chat.id)
            self._note_manual(m.chat.id)
            if self._player_ready():
                try:
                    if nxt and nxt.get("source"):
                        await self.player.play(m.chat.id, nxt["source"])
                    else:
                        await self.player.stop(m.chat.id)
                except Exception:
                    pass
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

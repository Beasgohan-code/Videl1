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

from .logic import PlayQueue, ffmpeg_params, format_duration

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
        self._pos: dict[int, dict] = {}  # chat_id -> {track, base, start}

    def _pos_now(self, chat_id: int) -> float:
        rec = self._pos.get(int(chat_id))
        if not rec:
            return 0.0
        if rec.get("start") is None:
            return float(rec["base"])
        return float(rec["base"]) + (time.monotonic() - rec["start"])

    def _pos_set(self, chat_id: int, track: dict, base: float = 0.0, playing: bool = True) -> None:
        self._pos[int(chat_id)] = {
            "track": track, "base": float(base),
            "start": time.monotonic() if playing else None}

    async def _play_track(self, chat_id: int, track: dict) -> bool:
        """Stream a track now (speed applied); False when it can't play."""
        if not (self._player_ready() and track and track.get("source")):
            return False
        speed = 1.0
        if self.store is not None:
            try:
                speed = float((await self.store.get_settings(self.bot_id, chat_id)).get("speed", 1.0))
            except Exception:
                speed = 1.0
        try:
            await self.player.play(chat_id, track["source"],
                                   ffmpeg_parameters=ffmpeg_params(0, speed))
        except Exception:
            return False
        self._pos_set(chat_id, track)
        return True

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
            "▶️ /play &lt;song&gt; · 🎬 /vplay · ⚡ /playforce\n"
            "⏯ /pause /resume · ⏭ /skip · ⏩ /seek 30 · 🎚 /speed\n"
            "📂 /pview playlists · 📡 /cplay channel play\n"
            "🎶 /queue · ⏹ /stop · ⚙️ /settings\n\n"
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
            [_btn(f"{'✅' if s.get('autoplay') else '▫️'} Autoplay (queue search hits)",
                  f"mw:{bot}:auto", style="success" if s.get("autoplay") else "danger")],
            [_btn(f"🎚 Speed: {float(s.get('speed', 1.0)):g}×", f"mw:{bot}:speed", style="primary")],
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
            self._pos.pop(int(chat_id), None)
            if self._player_ready():
                try:
                    await self.player.stop(chat_id)
                except Exception:
                    pass
            return
        if nxt is not None and not nxt.get("source") and self._player_ready():
            from .resolver import resolve_title
            resolved = await resolve_title(nxt["title"], video=bool(nxt.get("video")))
            if resolved:
                nxt.update(resolved)
        if nxt is not None:
            await self._play_track(chat_id, nxt)

    def _settings_text(self, s: dict) -> str:
        table = _table([
            ("🎧 DJ mode", "✅ on" if s.get("dj_mode") else "▫️ off"),
            ("🔊 Volume", f"{s.get('volume', 100)}%"),
            ("🎶 Streaming", "✅ ready" if self._player_ready() else "▫️ not set up yet"),
            ("🔁 Autoplay", "✅ on" if s.get("autoplay") else "▫️ off"),
            ("🎚 Speed", f"{float(s.get('speed', 1.0)):g}×"),
            ("📡 Channel", s.get("linked_channel") or "not linked"),
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

        @app.on_message(filters.group & filters.command(["play", "p", "vplay", "playforce", "cplay"]))
        async def _play(c, m: Message):
            if not await self._allowed(c, m):
                return
            cmd = (m.command or ["play"])[0].lower()
            settings = await store.get_settings(bot_id, m.chat.id)
            target = m.chat.id
            if cmd == "cplay":
                target = int(settings.get("linked_channel") or 0)
                if not target:
                    return await m.reply_text(
                        "📡 <b>No channel linked.</b> An admin can run /addchannel &lt;channel_id&gt; first.",
                        parse_mode=enums.ParseMode.HTML)
            await self._ensure_loaded(target)
            video = cmd == "vplay"
            force = cmd == "playforce"
            parts = (m.text or "").split(maxsplit=1)
            query = parts[1].strip()[:120] if len(parts) > 1 else ""
            title, duration = "", None
            replied = m.reply_to_message
            is_reply_audio = (replied is not None
                              and (getattr(replied, "audio", None) or getattr(replied, "voice", None)))
            if is_reply_audio:
                media = replied.audio or replied.voice
                title = (getattr(media, "title", None) or getattr(media, "file_name", None)
                         or "Audio track")
                duration = getattr(media, "duration", None)
            else:
                title = query
            if not title:
                return await m.reply_text(
                    "Usage: <code>/play &lt;song name&gt;</code> or reply to an audio file.",
                    parse_mode=enums.ParseMode.HTML)
            track = {"title": title, "duration": duration, "video": video,
                     "requested_by": m.from_user.first_name or str(m.from_user.id)}
            if self._player_ready():
                if is_reply_audio:
                    try:
                        track["source"] = str(await c.download_media(replied))
                    except Exception:
                        track["source"] = None
                elif query:
                    from .resolver import resolve_title
                    resolved = await resolve_title(query, video=video)
                    if not resolved:
                        return await m.reply_text(
                            "🔎 <b>Couldn't find that track.</b> Try another title, a link, "
                            "or reply /play to an audio file.",
                            parse_mode=enums.ParseMode.HTML)
                    track.update(resolved)
                    title, duration = resolved["title"], resolved["duration"]
            position = (self.queue.play_now(target, track) if force
                        else self.queue.enqueue(target, track))
            if settings.get("autoplay") and not force and query and not is_reply_audio:
                from .resolver import resolve_many
                for extra in (await resolve_many(query, 3))[1:]:
                    self.queue.enqueue(target, {**extra, "video": video,
                                                "requested_by": track["requested_by"]})
            await self._persist(target)
            where = "📡 <b>Channel:</b> " if cmd == "cplay" else ""
            note = ""
            if not self._player_ready():
                note = ("\n\n<i>🎧 Streaming engine isn't set up for this clone yet — "
                        "the track is queued and will play once it's enabled.</i>")
            elif position == 0:
                self._note_manual(target)
                if not await self._play_track(target, track):
                    note = ("\n\n<i>⚠️ Couldn't start playback — is a voice chat active? "
                            "The track stays queued.</i>")
            if position == 0:
                await m.reply_text(f"{where}▶️ <b>Now playing:</b> {_esc(title)} · {format_duration(duration)}{note}",
                                   parse_mode=enums.ParseMode.HTML)
            else:
                await m.reply_text(f"{where}🎶 <b>Queued #{position}:</b> {_esc(title)} · {format_duration(duration)}{note}",
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

        @app.on_message(filters.group & filters.command(
            ["skip", "stop", "remove", "pause", "resume", "clear", "seek", "seekback",
             "speed", "autoplay", "cskip", "cstop", "cpause", "cresume"]))
        async def _control(c, m: Message):
            if not await self._allowed(c, m):
                return
            raw = (m.command or ["skip"])[0].lower()
            target, cmd = m.chat.id, raw
            if raw in ("cskip", "cstop", "cpause", "cresume"):
                settings0 = await store.get_settings(bot_id, m.chat.id)
                target = int(settings0.get("linked_channel") or 0)
                if not target:
                    return await m.reply_text("📡 No channel linked. Use /addchannel first.",
                                              parse_mode=enums.ParseMode.HTML)
                cmd = raw[1:]
            await self._ensure_loaded(target)
            if cmd in ("pause", "resume"):
                if not self._player_ready():
                    return await m.reply_text("🎧 Streaming isn't active for this clone yet.",
                                              parse_mode=enums.ParseMode.HTML)
                try:
                    ok = await (self.player.pause(target) if cmd == "pause"
                                else self.player.resume(target))
                except Exception:
                    ok = False
                if ok:
                    rec = self._pos.get(int(target))
                    if rec:
                        if cmd == "pause":
                            rec["base"] = self._pos_now(target)
                            rec["start"] = None
                        else:
                            rec["start"] = time.monotonic()
                return await m.reply_text(
                    ("⏸ <b>Paused.</b>" if cmd == "pause" else "▶️ <b>Resumed.</b>") if ok
                    else "Nothing is playing right now.",
                    parse_mode=enums.ParseMode.HTML)
            if cmd == "stop":
                cleared = self.queue.clear(target)
                await self._persist(target)
                self._note_manual(target)
                self._pos.pop(int(target), None)
                if self._player_ready():
                    try:
                        await self.player.stop(target)
                    except Exception:
                        pass
                return await m.reply_text(f"⏹ <b>Stopped.</b> Cleared {cleared} track(s).",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "clear":
                dropped = self.queue.clear_upcoming(target)
                await self._persist(target)
                return await m.reply_text(f"🧹 <b>Queue cleared.</b> Dropped {dropped} upcoming track(s); current keeps playing.",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "remove":
                args = (m.command or [])[1:]
                if len(args) != 1 or not args[0].isdigit():
                    return await m.reply_text("Usage: <code>/remove &lt;position&gt;</code>",
                                              parse_mode=enums.ParseMode.HTML)
                removed = self.queue.remove(target, int(args[0]))
                await self._persist(target)
                return await m.reply_text(
                    f"🗑 Removed <b>{_esc(removed['title'])}</b>." if removed else "That position doesn't exist.",
                    parse_mode=enums.ParseMode.HTML)
            if cmd in ("seek", "seekback"):
                args = (m.command or [])[1:]
                if len(args) != 1 or not args[0].isdigit() or int(args[0]) <= 0:
                    return await m.reply_text("Usage: <code>/seek 30</code> (seconds)",
                                              parse_mode=enums.ParseMode.HTML)
                current = self.queue.now_playing(target)
                if not current or not current.get("source") or not self._player_ready():
                    return await m.reply_text("Nothing is playing right now.",
                                              parse_mode=enums.ParseMode.HTML)
                delta = int(args[0]) * (1 if cmd == "seek" else -1)
                new_pos = max(0.0, self._pos_now(target) + delta)
                settings1 = await store.get_settings(bot_id, m.chat.id)
                self._note_manual(target)
                try:
                    await self.player.play(target, current["source"], ffmpeg_parameters=ffmpeg_params(
                        new_pos, float(settings1.get("speed", 1.0))))
                except Exception:
                    return await m.reply_text("⚠️ Seek failed for this track.", parse_mode=enums.ParseMode.HTML)
                self._pos_set(target, current, base=new_pos)
                return await m.reply_text(f"⏩ <b>Position:</b> {format_duration(new_pos)}",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "speed":
                args = (m.command or [])[1:]
                try:
                    value = float(args[0]) if args else 0
                except (TypeError, ValueError):
                    value = 0
                if not 0.25 <= value <= 4.0:
                    return await m.reply_text("Usage: <code>/speed 1.5</code> (0.25–4.0)",
                                              parse_mode=enums.ParseMode.HTML)
                await store.update_settings(bot_id, m.chat.id, {"speed": value})
                current = self.queue.now_playing(target)
                if current and current.get("source") and self._player_ready():
                    pos = self._pos_now(target)
                    self._note_manual(target)
                    try:
                        await self.player.play(target, current["source"],
                                               ffmpeg_parameters=ffmpeg_params(pos, value))
                        self._pos_set(target, current, base=pos)
                    except Exception:
                        pass
                return await m.reply_text(f"🎚 <b>Speed set to {value:g}×.</b> Applies now and to upcoming tracks.",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "autoplay":
                s_now = await store.get_settings(bot_id, m.chat.id)
                s_now = await store.update_settings(bot_id, m.chat.id,
                                                    {"autoplay": not s_now.get("autoplay")})
                return await m.reply_text(
                    f"🔁 <b>Autoplay {'on' if s_now.get('autoplay') else 'off'}.</b> "
                    "When on, extra search hits queue up after a /play title.",
                    parse_mode=enums.ParseMode.HTML)
            nxt = self.queue.skip(target)
            await self._persist(target)
            self._note_manual(target)
            if self._player_ready():
                try:
                    if nxt and nxt.get("source"):
                        await self._play_track(target, nxt)
                    elif nxt:
                        from .resolver import resolve_title
                        resolved = await resolve_title(nxt["title"], video=bool(nxt.get("video")))
                        if resolved:
                            nxt.update(resolved)
                            await self._persist(target)
                            await self._play_track(target, nxt)
                        else:
                            await self.player.stop(target)
                    else:
                        self._pos.pop(int(target), None)
                        await self.player.stop(target)
                except Exception:
                    pass
            await m.reply_text(
                f"⏭ Skipped. Next: <b>{_esc(nxt['title'])}</b>" if nxt else "⏭ Skipped. Queue is empty.",
                parse_mode=enums.ParseMode.HTML)

        @app.on_message((filters.group | filters.private)
                        & filters.command(["pcreate", "padd", "premove", "pview", "pplay", "pdelete"]))
        async def _playlist(c, m: Message):
            cmd = (m.command or ["pview"])[0].lower()
            user_id = m.from_user.id
            parts = (m.text or "").split(maxsplit=2)
            name = parts[1].strip().lower() if len(parts) > 1 else ""
            rest = parts[2].strip() if len(parts) > 2 else ""
            if cmd == "pview" and not name:
                playlists = await store.list_playlists(bot_id, user_id)
                if not playlists:
                    return await m.reply_text("📂 <b>No playlists yet.</b> Create one with /pcreate &lt;name&gt;.",
                                              parse_mode=enums.ParseMode.HTML)
                table = _table([(f"📂 {p['name']}", f"{len(p['tracks'])} track(s)") for p in playlists])
                return await m.reply_text(f"{_bar('📂 YOUR PLAYLISTS')}\n\n{table}",
                                          parse_mode=enums.ParseMode.HTML)
            if not name:
                return await m.reply_text(f"Usage: <code>/{cmd} &lt;playlist name&gt;</code>",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "pcreate":
                await store.create_playlist(bot_id, user_id, name)
                return await m.reply_text(f"📂 Playlist <b>{_esc(name)}</b> ready. Add songs with /padd {_esc(name)}.",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "pdelete":
                gone = await store.delete_playlist(bot_id, user_id, name)
                return await m.reply_text(
                    f"🗑 Playlist <b>{_esc(name)}</b> deleted." if gone else "That playlist doesn't exist.",
                    parse_mode=enums.ParseMode.HTML)
            playlist = await store.get_playlist(bot_id, user_id, name)
            if cmd == "padd":
                replied = m.reply_to_message
                track = None
                if replied is not None and (getattr(replied, "audio", None) or getattr(replied, "voice", None)):
                    media = replied.audio or replied.voice
                    track = {"title": (getattr(media, "title", None) or getattr(media, "file_name", None)
                                       or "Audio track"),
                             "duration": getattr(media, "duration", None), "source": None}
                elif rest:
                    track = {"title": rest[:120], "duration": None, "source": None}
                if track is None:
                    return await m.reply_text("Usage: <code>/padd &lt;name&gt; &lt;song&gt;</code> or reply to an audio file.",
                                              parse_mode=enums.ParseMode.HTML)
                tracks = list((playlist or {}).get("tracks") or []) + [track]
                await store.set_playlist_tracks(bot_id, user_id, name, tracks)
                return await m.reply_text(f"➕ Added <b>{_esc(track['title'])}</b> to <b>{_esc(name)}</b> ({len(tracks)} track(s)).",
                                          parse_mode=enums.ParseMode.HTML)
            if playlist is None:
                return await m.reply_text("That playlist doesn't exist. Create it with /pcreate.",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "premove":
                tracks = list(playlist.get("tracks") or [])
                if not rest.isdigit() or not 1 <= int(rest) <= len(tracks):
                    return await m.reply_text("Usage: <code>/premove &lt;name&gt; &lt;number&gt;</code>",
                                              parse_mode=enums.ParseMode.HTML)
                gone = tracks.pop(int(rest) - 1)
                await store.set_playlist_tracks(bot_id, user_id, name, tracks)
                return await m.reply_text(f"🗑 Removed <b>{_esc(gone['title'])}</b> from <b>{_esc(name)}</b>.",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "pview":
                tracks = playlist.get("tracks") or []
                if not tracks:
                    return await m.reply_text(f"📂 <b>{_esc(name)}</b> is empty.", parse_mode=enums.ParseMode.HTML)
                table = _table([(f"{i}.", f"{t['title'][:40]} · {format_duration(t.get('duration'))}")
                                for i, t in enumerate(tracks, start=1)])
                return await m.reply_text(f"{_bar(f'📂 {_esc(name).upper()}')}\n\n{table}",
                                          parse_mode=enums.ParseMode.HTML)
            if cmd == "pplay":
                if getattr(m.chat, "type", None) == enums.ChatType.PRIVATE:
                    return await m.reply_text("▶️ Use /pplay inside the group where music should play.",
                                              parse_mode=enums.ParseMode.HTML)
                if not await self._allowed(c, m):
                    return
                tracks = playlist.get("tracks") or []
                if not tracks:
                    return await m.reply_text(f"📂 <b>{_esc(name)}</b> is empty.", parse_mode=enums.ParseMode.HTML)
                await self._ensure_loaded(m.chat.id)
                was_empty = self.queue.now_playing(m.chat.id) is None
                for t in tracks:
                    self.queue.enqueue(m.chat.id, {**t, "video": False,
                                                   "requested_by": m.from_user.first_name or str(user_id)})
                await self._persist(m.chat.id)
                if was_empty and self._player_ready():
                    first = self.queue.now_playing(m.chat.id)
                    if first and not first.get("source"):
                        from .resolver import resolve_title
                        resolved = await resolve_title(first["title"])
                        if resolved:
                            first.update(resolved)
                            await self._persist(m.chat.id)
                    if first:
                        self._note_manual(m.chat.id)
                        await self._play_track(m.chat.id, first)
                return await m.reply_text(f"▶️ Queued <b>{len(tracks)}</b> track(s) from <b>{_esc(name)}</b>.",
                                          parse_mode=enums.ParseMode.HTML)

        @app.on_message(filters.group & filters.command(["addchannel", "delchannel"]))
        async def _channel(c, m: Message):
            cmd = (m.command or ["addchannel"])[0].lower()
            if not await _is_admin(c, m.chat.id, m.from_user.id):
                return await m.reply_text("🔒 <b>Admins only.</b>", parse_mode=enums.ParseMode.HTML)
            if cmd == "delchannel":
                await store.update_settings(bot_id, m.chat.id, {"linked_channel": 0})
                return await m.reply_text("📡 Channel unlinked.", parse_mode=enums.ParseMode.HTML)
            args = (m.command or [])[1:]
            if len(args) != 1:
                return await m.reply_text("Usage: <code>/addchannel &lt;channel_id or @username&gt;</code>",
                                          parse_mode=enums.ParseMode.HTML)
            ref: object = int(args[0]) if args[0].lstrip("-").isdigit() else args[0]
            try:
                chat = await c.get_chat(ref)
            except Exception:
                return await m.reply_text("Couldn't find that channel — add me there as admin first.",
                                          parse_mode=enums.ParseMode.HTML)
            if getattr(chat, "type", None) != enums.ChatType.CHANNEL:
                return await m.reply_text("📡 That isn't a channel.", parse_mode=enums.ParseMode.HTML)
            await store.update_settings(bot_id, m.chat.id, {"linked_channel": int(chat.id)})
            await m.reply_text(f"📡 Linked channel: <b>{_esc(chat.title or chat.id)}</b>. "
                               "Use /cplay, /cskip, /cpause, /cstop for its voice chat.",
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
            elif action == "auto":
                s = await store.update_settings(bot_id, q.message.chat.id,
                                                {"autoplay": not s.get("autoplay")})
            elif action == "speed":
                steps = [1.0, 1.25, 1.5, 2.0, 0.5]
                cur = float(s.get("speed", 1.0))
                nxt = steps[(steps.index(cur) + 1) % len(steps)] if cur in steps else 1.0
                s = await store.update_settings(bot_id, q.message.chat.id, {"speed": nxt})
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
    ("play", "Play/queue a song: /play <name> or reply to audio"),
    ("vplay", "Play video in the voice chat"),
    ("playforce", "Play now, keep the queue"),
    ("queue", "Show the current queue"),
    ("skip", "Skip the current track"),
    ("pause", "Pause playback"),
    ("resume", "Resume playback"),
    ("seek", "Seek forward: /seek 30"),
    ("seekback", "Seek backward: /seekback 30"),
    ("speed", "Playback speed: /speed 1.5"),
    ("autoplay", "Auto-queue search hits (toggle)"),
    ("clear", "Clear upcoming tracks (keeps current)"),
    ("remove", "Remove a queue position: /remove 2"),
    ("stop", "Stop and clear the queue"),
    ("pcreate", "Create a playlist: /pcreate <name>"),
    ("padd", "Add a song: /padd <name> <song>"),
    ("premove", "Remove from playlist: /premove <name> 2"),
    ("pview", "View playlists or one playlist"),
    ("pplay", "Play a playlist in this group"),
    ("pdelete", "Delete a playlist"),
    ("addchannel", "Link a channel for channel play (admins)"),
    ("delchannel", "Unlink the channel (admins)"),
    ("cplay", "Play in the linked channel's voice chat"),
    ("cskip", "Skip in the channel"),
    ("cpause", "Pause the channel stream"),
    ("cresume", "Resume the channel stream"),
    ("cstop", "Stop the channel stream"),
    ("settings", "Music settings (admins)"),
]

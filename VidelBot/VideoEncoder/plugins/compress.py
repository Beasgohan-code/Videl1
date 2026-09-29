"""
🗜 /compress – make a video smaller, fast.

  /compress                 (reply to a video) → panel: 1080p · 720p · 480p · 360p, H.264 / H.265, level,
                            audio, MP4 / MKV, target size → 🚀 Start
  /compress 480 strong      one-shot: starts right away, no buttons
  /compress 360 50mb hevc
  /compress 480 10:00-25:00 only that part (a bare `/compress 1:30-` opens the panel with the cut set)
  /compress auto on|off     🤖 auto-compress: every video sent in private is compressed with the last choice
  quick row: 📱 Mobile · 💬 10 MB · 📧 25 MB (one tap)

📚 Reply to any video of an album → the panel offers the whole album (up to 10 videos, one tap, each video
gets its own live status card). 🖼 Every result comes with a before / after frame. 🌈 HDR sources are
tone-mapped to normal SDR colours (so they don't look washed out on phones) unless `keephdr` is given.

Before starting, the card warns when a target can't be reached for the video's length or the file is already lean,
and shows an encode-time estimate learned from this server's own past compressions. During the encode a size
watch stops it early if it's heading for a file bigger than the source (utils.compress.watch_verdict).

The panel turns into the task's live status card (no extra messages). The last choice is remembered per user.
Resolutions above the source are locked (an upscale is bigger, never better).
"""
import asyncio
import time

from pyrogram import Client, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import CallbackQuery

import config
from ..utils import compress as C
from ..utils.database.access_db import db
from .encode import _enqueue
from .tools import _allowed, _is_video_msg

PANEL_TTL = 15 * 60
_panels: dict = {}          # (chat_id, panel message id) → {"uid", "cmd": Message, "opts", "meta", "ts", …}
_auto_cache: dict = {}      # uid → (on, checked_at) – the auto filter runs on every private video
AUTO_CACHE_TTL = 60


def _prune():
    now = time.time()
    for k in [k for k, v in _panels.items() if now - v["ts"] > PANEL_TTL]:
        _panels.pop(k, None)


def _source(message):
    r = message.reply_to_message
    if r is not None and (r.video or r.document or getattr(r, "animation", None)):
        return r
    if message.video or message.document:
        return message
    return None


def _is_video(m) -> bool:
    return bool(m is not None and (m.video or getattr(m, "animation", None) or _is_video_msg(m)))


async def _last(uid: int) -> dict:
    try:
        # the raw document – get_settings() keeps only encoder keys (ffcmd.merge), so it would drop this
        return C.normalize((await db._get_user(uid) or {}).get("cmp_last") or {})
    except Exception:
        return C.normalize({})


async def _remember(uid: int, opts: dict):
    try:
        await db.update_settings(uid, cmp_last=C.remembered(opts))
    except Exception:
        pass


async def _auto_on(uid: int, fresh: bool = False) -> bool:
    hit = _auto_cache.get(uid)
    if hit and not fresh and time.time() - hit[1] < AUTO_CACHE_TTL:
        return hit[0]
    try:
        on = bool((await db._get_user(uid) or {}).get("cmp_auto"))
    except Exception:
        on = False
    _auto_cache[uid] = (on, time.time())
    if len(_auto_cache) > 5000:
        _auto_cache.clear()
    return on


async def _set_auto(uid: int, on: bool):
    _auto_cache[uid] = (bool(on), time.time())
    try:
        await db.update_settings(uid, cmp_auto=bool(on))
    except Exception:
        pass


def _fit(opts: dict, meta: dict) -> dict:
    """A remembered 1080p on a 720p file → pick the highest resolution that isn't an upscale."""
    if not C.can_pick(opts["res"], C.view(opts, meta)):
        for r in C.RES:
            if C.can_pick(r, C.view(opts, meta)):
                opts["res"] = r
                break
    return opts


async def _album(app, src) -> list:
    """The videos of the album `src` belongs to (≥ 2), else []."""
    if not getattr(src, "media_group_id", None):
        return []
    try:
        group = await app.get_media_group(src.chat.id, src.id)
    except Exception:
        return []
    vids = [m for m in group or [] if _is_video(m)]
    return vids[:C.ALBUM_MAX] if len(vids) > 1 else []


class AlbumItem:
    """One album video as its own queue task: the /compress command (user, chat, replies) pointed at that video.

    The scheduler keys tasks by (chat, message id) and downloads `reply_to_message`, so every item gets its own
    folders, duplicate check and status card, and survives a restart like any other task."""

    def __init__(self, cmd, item):
        self._cmd = cmd
        self.id = item.id
        self.reply_to_message = item
        self.reply_to_message_id = item.id
        self.video = self.document = self.animation = self.audio = None

    def __getattr__(self, name):
        return getattr(self._cmd, name)


async def _start_album(app, cmd, uid: int, items: list, opts: dict, card=None):
    """Queue every album video (as far as the user's queue limit allows); the panel becomes the summary card."""
    from ..utils import scheduler
    from core.bg import spawn
    from .encode import _is_pro
    one = C.normalize({**opts, "album": False, "cut": None})
    slots = len(items)
    if uid not in config.ADMINS:
        limit = config.ENC_MAX_TASKS_PRO if await _is_pro(uid) else config.ENC_MAX_TASKS_FREE
        slots = max(0, min(slots, limit - scheduler.user_tasks(uid)))
    todo, skipped = items[:slots], len(items) - slots
    summary = C.batch_text(one, len(todo), len(items), skipped)
    try:
        if card is not None:
            await card.edit(summary, reply_markup=None)
        else:
            await cmd.reply(summary)
    except Exception:
        pass
    for item in todo:
        try:
            status = await item.reply(C.queued_text(one, C.meta_of(item)), quote=True)
        except Exception:
            status = None
        spawn(_enqueue(AlbumItem(cmd, item), "compress", one, card=status), name="compress-album")
        await asyncio.sleep(0.3)                        # keep the album order in the queue


@Client.on_message(filters.command(["compress", "compressor", "shrink"]))
async def compress_cmd(app, message):
    if not await _allowed(app, message):
        return
    uid = message.from_user.id
    words = (message.text or message.caption or "").split()
    if len(words) > 1 and words[1].lower() == "auto":           # /compress auto [on|off]
        arg = words[2].lower() if len(words) > 2 else ""
        on = (not await _auto_on(uid, fresh=True)) if arg not in ("on", "off") else arg == "on"
        await _set_auto(uid, on)
        return await message.reply(C.auto_state_text(on, await _last(uid)))
    src = _source(message)
    if src is None or not _is_video(src):
        return await message.reply(C.usage_text())
    meta = C.meta_of(src)
    album = await _album(app, src)
    if album:
        meta["album"] = [C.meta_of(m) for m in album]
    args = C.parse_args(message.text or message.caption or "")
    opts = C.normalize({**await _last(uid), "album": bool(album), **args})
    if args.get("cut"):
        opts["album"] = False                           # a cut is about one video
    if C.is_oneshot(args):                              # one-shot: straight into the queue
        opts = _fit(opts, meta)
        await _remember(uid, opts)
        if opts["album"] and album:
            return await _start_album(app, message, uid, album, opts)
        card = await message.reply(C.queued_text(opts, meta))
        return await _enqueue(message, "compress", opts, card=card)
    opts = _fit(opts, meta)
    _prune()
    from ..utils.tasks import compress_speeds
    speeds = await compress_speeds()
    auto = await _auto_on(uid)
    panel = await message.reply(C.panel_text(opts, meta, speeds, auto), reply_markup=C.keyboard(opts, meta, auto))
    if panel is not None:
        _panels[(message.chat.id, panel.id)] = {"uid": uid, "cmd": message, "opts": opts, "meta": meta,
                                                "speeds": speeds, "album": album, "auto": auto, "ts": time.time()}


@Client.on_callback_query(filters.regex(r"^cmp:"))
async def compress_cb(app, query: CallbackQuery):
    key = (query.message.chat.id, query.message.id)
    st = _panels.get(key)
    if st is None:
        return await query.answer("⌛ This panel expired – send /compress again.", show_alert=True)
    if query.from_user.id != st["uid"] and query.from_user.id not in config.ADMINS:
        return await query.answer("🔒 Only the person who sent /compress can use these buttons.", show_alert=True)
    parts = (query.data or "").split(":")
    action, value = parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""
    st["ts"] = time.time()

    if action == "close":
        _panels.pop(key, None)
        await query.answer("Closed")
        try:
            return await query.message.delete()
        except Exception:
            return None
    if action == "lock":
        h = C.view(st["opts"], st["meta"]).get("height")
        return await query.answer(f"🔒 The video is only {h}p – going up to {value}p would make it bigger, "
                                  "not better.", show_alert=True)
    if action == "go":
        _panels.pop(key, None)
        opts = C.normalize(st["opts"])
        await _remember(st["uid"], opts)
        from core.bg import spawn                       # the encode can take minutes – free the callback now
        if opts["album"] and st.get("album"):
            await query.answer(f"🚀 {len(st['album'])} videos – queueing…")
            spawn(_start_album(app, st["cmd"], st["uid"], st["album"], opts, card=query.message),
                  name="compress-album")
            return None
        await query.answer(f"🚀 {C.label(opts, st['meta'].get('height') or 0)} – starting…")
        try:
            await query.message.edit(C.queued_text(opts, st["meta"]), reply_markup=None)
        except Exception:
            pass
        spawn(_enqueue(st["cmd"], "compress", opts, card=query.message), name="compress")
        return None

    if action == "auto":
        st["auto"] = not st.get("auto")
        await _set_auto(st["uid"], st["auto"])
        opts, toast = st["opts"], ("🤖 Auto-compress ON – every video you send me in private is compressed with "
                                   "these settings" if st["auto"] else "🤖 Auto-compress off")
        changed = True
        if st["auto"]:
            await _remember(st["uid"], opts)            # auto uses the saved choice → save what's on screen
    else:
        if action == "quick":
            opts, toast = C.apply_quick(st["opts"], value, st["meta"])
        else:
            opts, toast = C.apply(st["opts"], action, value)
        if action == "res" and not C.can_pick(opts["res"], C.view(opts, st["meta"])):
            return await query.answer("🔒 That would be an upscale.", show_alert=True)
        if action == "album":
            opts = _fit(opts, st["meta"])
        changed = opts != st["opts"]
        st["opts"] = opts
    await query.answer(toast or None, show_alert=action == "auto" and st.get("auto", False))
    if changed:
        try:
            await query.message.edit(C.panel_text(opts, st["meta"], st.get("speeds"), st.get("auto", False)),
                                     reply_markup=C.keyboard(opts, st["meta"], st.get("auto", False)))
        except MessageNotModified:
            pass


# ─────────────────────────── 🤖 auto-compress ───────────────────────────
def _busy_elsewhere(uid: int) -> bool:
    """The user is in the middle of something that wants their next file (rename sequence / prompt, clone setup)."""
    try:
        from renamer import handlers as rn
        if uid in rn._sequences or rn._get_state(uid):
            return True
    except Exception:
        pass
    try:
        from filestore.main_bot.plugins.create_bot import _creation_state
        if uid in _creation_state:
            return True
    except Exception:
        pass
    return False


async def _auto_filter(_, __, message) -> bool:
    user = message.from_user
    if not user or user.is_bot or not _is_video(message):
        return False
    if (message.caption or "").startswith("/"):
        return False                                    # a command with the video attached
    media = message.video or message.document or getattr(message, "animation", None)
    if int(getattr(media, "file_size", 0) or 0) < C.AUTO_MIN_MB * 1024 * 1024:
        return False
    if _busy_elsewhere(user.id):
        return False
    return await _auto_on(user.id)


auto_filter = filters.create(_auto_filter)


# Same handler group as auto-rename (0) and loaded before it (PLUGIN_ROOTS order), so when a user switches
# auto-compress on it takes their videos; everything else still reaches the renamer.
@Client.on_message(filters.private & filters.incoming & (filters.video | filters.document) & auto_filter)
async def auto_compress(app, message):
    if not await _allowed(app, message):
        return
    uid = message.from_user.id
    meta = C.meta_of(message)
    opts = _fit(C.normalize({**await _last(uid), "album": False, "cut": None}), meta)
    card = await message.reply(C.auto_text(opts, meta), quote=True)
    await _enqueue(message, "compress", opts, card=card)

"""
🗜 /compress – make a video smaller, fast.

  /compress                 (reply to a video) → panel: 1080p · 720p · 480p · 360p, H.264 / H.265, level,
                            audio, MP4 / MKV, target size → 🚀 Start
  /compress 480 strong      one-shot: starts right away, no buttons
  /compress 360 50mb hevc

The panel turns into the task's live status card (no extra messages). The last choice is remembered per user.
Resolutions above the source are locked (an upscale is bigger, never better).
"""
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
_panels: dict = {}          # (chat_id, panel message id) → {"uid", "cmd": Message, "opts", "meta", "ts"}


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


async def _last(uid: int) -> dict:
    try:
        # the raw document – get_settings() keeps only encoder keys (ffcmd.merge), so it would drop this
        return C.normalize((await db._get_user(uid) or {}).get("cmp_last") or {})
    except Exception:
        return C.normalize({})


async def _remember(uid: int, opts: dict):
    try:
        await db.update_settings(uid, cmp_last=C.normalize(opts))
    except Exception:
        pass


def _fit(opts: dict, meta: dict) -> dict:
    """A remembered 1080p on a 720p file → pick the highest resolution that isn't an upscale."""
    if not C.can_pick(opts["res"], meta):
        for r in C.RES:
            if C.can_pick(r, meta):
                opts["res"] = r
                break
    return opts


@Client.on_message(filters.command(["compress", "compressor", "shrink"]))
async def compress_cmd(app, message):
    if not await _allowed(app, message):
        return
    src = _source(message)
    if src is None or not (src.video or getattr(src, "animation", None) or _is_video_msg(src)):
        return await message.reply(C.usage_text())
    uid = message.from_user.id
    meta = C.meta_of(src)
    args = C.parse_args(message.text or message.caption or "")
    opts = C.normalize({**await _last(uid), **args})
    if args:                                            # one-shot: straight into the queue
        opts = _fit(opts, meta)
        await _remember(uid, opts)
        card = await message.reply(C.queued_text(opts, meta))
        return await _enqueue(message, "compress", opts, card=card)
    opts = _fit(opts, meta)
    _prune()
    panel = await message.reply(C.panel_text(opts, meta), reply_markup=C.keyboard(opts, meta))
    if panel is not None:
        _panels[(message.chat.id, panel.id)] = {"uid": uid, "cmd": message, "opts": opts, "meta": meta,
                                                "ts": time.time()}


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
        h = st["meta"].get("height")
        return await query.answer(f"🔒 The video is only {h}p – going up to {value}p would make it bigger, "
                                  "not better.", show_alert=True)
    if action == "go":
        _panels.pop(key, None)
        opts = C.normalize(st["opts"])
        await _remember(st["uid"], opts)
        await query.answer(f"🚀 {C.label(opts, st['meta'].get('height') or 0)} – starting…")
        try:
            await query.message.edit(C.queued_text(opts, st["meta"]), reply_markup=None)
        except Exception:
            pass
        from core.bg import spawn                       # the encode can take minutes – free the callback now
        spawn(_enqueue(st["cmd"], "compress", opts, card=query.message), name="compress")
        return None

    opts, toast = C.apply(st["opts"], action, value)
    if action == "res" and not C.can_pick(opts["res"], st["meta"]):
        return await query.answer("🔒 That would be an upscale.", show_alert=True)
    changed = opts != st["opts"]
    st["opts"] = opts
    await query.answer(toast or None)
    if changed:
        try:
            await query.message.edit(C.panel_text(opts, st["meta"]), reply_markup=C.keyboard(opts, st["meta"]))
        except MessageNotModified:
            pass

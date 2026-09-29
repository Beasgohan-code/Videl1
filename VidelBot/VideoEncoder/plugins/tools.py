"""
Encoder tools (Phase 15):

  /mux                 reply to a video → then send a subtitle (.srt .ass .vtt) or audio file → added losslessly
  /merge               collect 2-10 videos → /merge done → one file (lossless when they match)
  /convert <fmt>       reply to a video/audio → mp3 · m4a · opus · flac · wav · gif [start] [seconds]
  /leech <url>         Mega / Google Drive / direct link → uploaded to Telegram (auto-split above 2 GB)
  /watermark …         your own text watermark or logo (reply to a photo), position / size / opacity

Pending files (/mux track, /merge parts) are captured in group -1 by a filter that only matches while
that user has an open request in that chat, so they never reach Auto-Rename or the saver.
"""
import html
import re
import time

from pyrogram import Client, StopPropagation, filters
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup

import config
from ..utils import ffcmd
from ..utils.database.access_db import db
from ..utils.database.add_user import AddUserToDatabase
from ..utils.helper import check_chat
from .encode import _enqueue, _is_pro

PENDING_TTL = 5 * 60
MERGE_TTL = 15 * 60
MERGE_FREE, MERGE_PRO = 3, 10
VIDEO_EXT = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts", ".flv", ".wmv")

_mux: dict = {}      # (chat_id, uid) → {"cmd": Message, "ts": float}
_merge: dict = {}    # (chat_id, uid) → {"parts": [[chat, id], …], "ts": float, "names": [...]}


def _key(message):
    return message.chat.id, (message.from_user.id if message.from_user else 0)


def _file_of(message):
    return message.document or message.audio or message.video or getattr(message, "voice", None)


def _name_of(message) -> str:
    f = _file_of(message)
    return getattr(f, "file_name", "") or ""


def _is_video_msg(message) -> bool:
    if message.video:
        return True
    d = message.document
    return bool(d and (str(d.mime_type or "").startswith("video/") or (d.file_name or "").lower().endswith(VIDEO_EXT)))


def _media_reply(message):
    r = message.reply_to_message
    return r if r and (r.video or r.document or r.audio) else None


def prune():
    now = time.time()
    for store, ttl in ((_mux, PENDING_TTL), (_merge, MERGE_TTL)):
        for k in [k for k, v in store.items() if now - v["ts"] > ttl]:
            store.pop(k, None)


async def _allowed(app, message) -> bool:
    if not await check_chat(message, chat='Both'):
        return False
    await AddUserToDatabase(app, message)
    return True


# ─────────────────────────── /mux ───────────────────────────
@Client.on_message(filters.command(["mux", "addsub", "addaudio"]))
async def mux_cmd(app, message):
    if not await _allowed(app, message):
        return
    parts = (message.text or message.caption or "").split()
    if len(parts) > 1 and parts[1].lower() in ("cancel", "stop"):
        _mux.pop(_key(message), None)
        return await message.reply("❌ <b>/mux cancelled.</b>")
    video = _media_reply(message)
    if not video or not (video.video or _is_video_msg(video)):
        return await message.reply(
            "🧩 <b>Add a subtitle or audio track</b>\n\n1️⃣ Reply <code>/mux</code> to a video\n"
            "2️⃣ Send the <b>.srt / .ass / .vtt</b> subtitle or an <b>audio</b> file (mp3, m4a, aac, ac3, opus, flac …)\n\n"
            "<i>No re-encode – fast and lossless. MP4 subtitles become mov_text automatically.</i>")
    # the track can come attached to the command itself (caption /mux on a document)
    if _file_of(message) and ffcmd.track_kind(_name_of(message), getattr(_file_of(message), "mime_type", "")):
        kind = ffcmd.track_kind(_name_of(message), getattr(_file_of(message), "mime_type", ""))
        return await _enqueue(message, "mux", {"track": [message.chat.id, message.id], "kind": kind})
    prune()
    _mux[_key(message)] = {"cmd": message, "ts": time.time()}
    await message.reply("🧩 <b>Now send the subtitle or audio file</b> (within 5 minutes).\n"
                        "<i>Subtitles: .srt .ass .ssa .vtt · Audio: mp3 m4a aac ac3 eac3 opus ogg flac wav mka</i>\n"
                        "<code>/mux cancel</code> to stop.")


# ─────────────────────────── /merge ───────────────────────────
async def _merge_limit(uid: int) -> int:
    return MERGE_PRO if await _is_pro(uid) else MERGE_FREE


@Client.on_message(filters.command("merge"))
async def merge_cmd(app, message):
    if not await _allowed(app, message):
        return
    key = _key(message)
    arg = ((message.text or "").split() + [""])[1].lower()
    prune()
    state = _merge.get(key)
    if arg in ("cancel", "stop"):
        _merge.pop(key, None)
        return await message.reply("❌ <b>Merge cancelled.</b>")
    if arg in ("done", "go", "start", "now"):
        if not state or len(state["parts"]) < 2:
            have = len(state["parts"]) if state else 0
            return await message.reply(f"🔗 <b>Send at least 2 videos first</b> (you have {have}).\n"
                                       "<i>Start with /merge, send the videos, then /merge done.</i>")
        _merge.pop(key, None)
        return await _enqueue(message, "merge", {"parts": state["parts"]})
    limit = await _merge_limit(key[1])
    state = _merge[key] = {"parts": [], "names": [], "ts": time.time(), "limit": limit}
    first = _media_reply(message)
    if first and _is_video_msg(first):
        state["parts"].append([first.chat.id, first.id])
        state["names"].append(_name_of(first) or "video")
    await message.reply(
        f"🔗 <b>Merge videos</b> – send up to <b>{limit}</b> videos in order"
        + (" (1 added)" if state["parts"] else "") + ".\n\n"
        "• same codec &amp; size → <b>lossless</b> join (seconds)\n"
        "• different → matched to the first video (H.264 / AAC)\n\n"
        "When you're done: <code>/merge done</code> · stop: <code>/merge cancel</code>"
        + ("" if limit == MERGE_PRO else f"\n<i>🎬 Encoder Pro merges up to {MERGE_PRO} – /plans</i>"))


# ─────────────────────────── capture (group -1) ───────────────────────────
def _pending_filter(_, __, message) -> bool:
    if not message.from_user or not _file_of(message):
        return False
    if (message.text or message.caption or "").startswith("/"):
        return False
    key = _key(message)
    now = time.time()
    if key in _mux and now - _mux[key]["ts"] <= PENDING_TTL:
        return True
    return key in _merge and now - _merge[key]["ts"] <= MERGE_TTL


pending_filter = filters.create(_pending_filter)


@Client.on_message(pending_filter & filters.incoming, group=-1)
async def capture_pending(app, message):
    key = _key(message)
    if key in _mux:
        kind = ffcmd.track_kind(_name_of(message), getattr(_file_of(message), "mime_type", ""))
        if message.audio or getattr(message, "voice", None):
            kind = kind or "audio"
        if not kind:
            await message.reply("⚠️ <b>That's not a subtitle or audio file.</b>\n"
                                "<i>Send .srt / .ass / .vtt or an audio file – or /mux cancel.</i>")
            raise StopPropagation
        cmd = _mux.pop(key)["cmd"]
        await message.reply(f"✅ <b>{'Subtitle' if kind == 'sub' else 'Audio'} received</b> – adding it…", quote=True)
        await _enqueue(cmd, "mux", {"track": [message.chat.id, message.id], "kind": kind})
        raise StopPropagation
    state = _merge.get(key)
    if state is not None:
        if not _is_video_msg(message):
            await message.reply("⚠️ Only videos can be merged – send a video, /merge done or /merge cancel.")
            raise StopPropagation
        if len(state["parts"]) >= state.get("limit", MERGE_FREE):
            await message.reply(f"⚠️ <b>That's the maximum ({len(state['parts'])}).</b> Send <code>/merge done</code>.")
            raise StopPropagation
        state["parts"].append([message.chat.id, message.id])
        state["names"].append(_name_of(message) or "video")
        state["ts"] = time.time()
        n = len(state["parts"])
        await message.reply(f"✅ <b>Video {n} added</b>" + (" – send more or <code>/merge done</code>" if n >= 2
                                                             else " – send the next one"), quote=True)
        raise StopPropagation


# ─────────────────────────── /convert ───────────────────────────
CONVERT_HELP = ("🔄 <b>Convert</b> – reply to a video or audio:\n\n"
                "<code>/convert mp3</code> · <code>m4a</code> · <code>opus</code> · <code>flac</code> · "
                "<code>wav</code> – extract the audio\n"
                "<code>/convert gif</code> – 6 s GIF from the middle\n"
                "<code>/convert gif 1:20 8</code> – GIF from 1:20, 8 seconds (max 20)")


@Client.on_message(filters.command(["convert", "toaudio", "gif"]))
async def convert_cmd(app, message):
    if not await _allowed(app, message):
        return
    parts = (message.text or message.caption or "").split()
    cmd = parts[0].lstrip("/").split("@")[0].lower()
    args = parts[1:]
    if cmd == "gif":
        args = ["gif"] + args
    elif cmd == "toaudio" and (not args or args[0].lower() not in ffcmd.CONVERT_FORMATS):
        args = ["mp3"] + args
    fmt = args[0].lower() if args else ""
    if not _media_reply(message) and not _file_of(message) or fmt not in list(ffcmd.CONVERT_FORMATS) + ["gif"]:
        return await message.reply(CONVERT_HELP)
    extra = {"fmt": fmt}
    if fmt == "gif":
        if len(args) > 1:
            start = ffcmd.parse_timestamp(args[1])
            if start is None:
                return await message.reply(CONVERT_HELP)
            extra["start"] = start
        if len(args) > 2:
            length = ffcmd.parse_timestamp(args[2])
            if not length:
                return await message.reply(CONVERT_HELP)
            extra["length"] = min(float(ffcmd.GIF_MAX), length)
    await _enqueue(message, "convert", extra)


# ─────────────────────────── /leech ───────────────────────────
@Client.on_message(filters.command(["leech", "mirror"]))
async def leech_cmd(app, message):
    if not await _allowed(app, message):
        return
    text = (message.text or message.caption or "").split(None, 1)
    body = text[1].strip() if len(text) > 1 else ""
    if not body and message.reply_to_message:
        found = re.search(r"https?://\S+", message.reply_to_message.text or message.reply_to_message.caption or "")
        body = found.group(0) if found else ""
    url, _, name = body.partition("|")
    url, name = url.strip(), name.strip()
    if not re.match(r"^https?://", url, re.I):
        pro = await _is_pro(message.from_user.id if message.from_user else 0)
        cap = config.LEECH_PRO_GB if pro else config.LEECH_FREE_GB
        return await message.reply(
            "🌐 <b>Link uploader</b>\n\n<code>/leech https://mega.nz/file/…#key</code>\n"
            "<code>/leech https://drive.google.com/file/d/…</code>\n<code>/leech https://site/file.mkv | New name.mkv</code>\n\n"
            f"• Mega file links · public Google Drive files · direct links\n• your limit: <b>{cap:g} GB</b>"
            + ("" if pro else f" (🎬 Encoder Pro: {config.LEECH_PRO_GB:g} GB)")
            + "\n• files over 2 GB are split into parts automatically")
    await _enqueue(message, "leech", {"url": url, "name": name})


# ─────────────────────────── /watermark ───────────────────────────
def _wm_status(s: dict) -> str:
    from core.style import hdr, row, hint
    return "\n".join([
        hdr("©️", "Watermark"), "",
        row("Text", f"<code>{html.escape(s.get('wm_text') or '')}</code>" if s.get("wm_text") else "default / none"),
        row("Text watermark", "✅ on" if s.get("watermark") else "▫️ off"),
        row("Logo", ("✅ on" if s.get("logo") else "▫️ off") if s.get("logo_id") else "not set"),
        row("Position", ffcmd.WM_POS_LABEL.get(s.get("wm_pos"), "↘️ Bottom right")),
        row("Size", {"s": "Small", "m": "Medium", "l": "Large"}.get(s.get("wm_size"), "Medium")),
        row("Opacity", f"{s.get('wm_opacity', '75')}%"), "",
        hint("/watermark My Channel – set text · reply /watermark to a photo – logo · /watermark off · /watermark clear")])


@Client.on_message(filters.command(["watermark", "setwatermark", "setlogo"]))
async def watermark_cmd(app, message):
    if not await _allowed(app, message):
        return
    uid = message.from_user.id
    body = ((message.text or message.caption or "").split(None, 1) + [""])[1].strip()
    reply = message.reply_to_message
    photo = None
    for m in (message, reply):
        if m and (m.photo or (m.document and str(m.document.mime_type or "").startswith("image/"))):
            photo = m.photo or m.document
            break
    kb = InlineKeyboardMarkup([[Btn("🎨 Style", callback_data="WmSettings"),
                                Btn("🧩 Extras", callback_data="ExtraSettings")]])
    if photo is not None:
        if not await _is_pro(uid):
            return await message.reply("🖼 <b>Logo watermarks are an 🎬 Encoder Pro feature.</b>\n"
                                       "<i>Text watermarks are free: /watermark Your text · plans: /plans</i>")
        await db.update_settings(uid, logo_id=photo.file_id, logo=True)
        return await message.reply("✅ <b>Logo saved</b> – it's added to your next encodes.\n"
                                   "<i>PNG with transparency looks best. Tune it with 🎨 Style.</i>", reply_markup=kb)
    low = body.lower()
    if low in ("off", "disable"):
        await db.update_settings(uid, watermark=False, logo=False)
        return await message.reply("▫️ <b>Watermarks turned off.</b>")
    if low in ("clear", "reset", "delete"):
        await db.update_settings(uid, watermark=False, logo=False, wm_text="", logo_id=None)
        return await message.reply("🗑 <b>Watermark text and logo removed.</b>")
    if low == "on":
        await db.update_settings(uid, watermark=True)
        return await message.reply("✅ <b>Text watermark on.</b>")
    if body:
        if len(body) > 60:
            return await message.reply("⚠️ Keep it under 60 characters.")
        await db.update_settings(uid, wm_text=body, watermark=True)
        return await message.reply(f"✅ <b>Watermark text set:</b> <code>{html.escape(body)}</code>", reply_markup=kb)
    s = await db.get_settings(uid)
    await message.reply(_wm_status(s), reply_markup=kb)

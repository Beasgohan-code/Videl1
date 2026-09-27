"""Everyday utility commands added by Videl."""
import asyncio
import io
import json
import logging
import os
import shutil
import time
from urllib.parse import quote

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import SOURCE_URL
from core.ui import humanbytes, upload_to_host

log = logging.getLogger("videl.tools")
TMP = "downloads/tools"
os.makedirs(TMP, exist_ok=True)

_busy: set[int] = set()  # one heavy tool job per user


def _media(msg: Message):
    if not msg:
        return None
    for kind in ("document", "video", "audio", "voice", "animation", "photo", "video_note", "sticker"):
        m = getattr(msg, kind, None)
        if m:
            return m
    return None


def _progress_cb(status: Message, action: str):
    last = {"t": 0.0}

    async def cb(current, total):
        now = time.time()
        if now - last["t"] < 4 and current != total:
            return
        last["t"] = now
        pct = current * 100 / total if total else 0
        bar = "█" * int(pct // 10) + "░" * (10 - int(pct // 10))
        try:
            await status.edit_text(f"<b>{action}</b>\n<code>[{bar}] {pct:.1f}%</code>\n"
                                   f"{humanbytes(current)} / {humanbytes(total)}")
        except Exception:
            pass
    return cb


# ─────────────────────────── /ping ───────────────────────────
@Client.on_message(filters.command("ping"))
async def ping_cmd(client: Client, message: Message):
    t = time.perf_counter()
    m = await message.reply_text("🏓 Pinging…")
    await m.edit_text(f"🏓 <b>Pong!</b> <code>{(time.perf_counter() - t) * 1000:.0f} ms</code>")


# ─────────────────────────── /id ───────────────────────────
@Client.on_message(filters.command("id"))
async def id_cmd(client: Client, message: Message):
    lines = [f"<b>💬 Chat ID:</b> <code>{message.chat.id}</code>"]
    if message.from_user:
        lines.append(f"<b>👤 Your ID:</b> <code>{message.from_user.id}</code>")
    r = message.reply_to_message
    if r:
        if r.from_user:
            lines.append(f"<b>↩️ Replied user:</b> <code>{r.from_user.id}</code>")
        if r.forward_from:
            lines.append(f"<b>⏩ Forwarded from user:</b> <code>{r.forward_from.id}</code>")
        if r.forward_from_chat:
            lines.append(f"<b>⏩ Forwarded from chat:</b> <code>{r.forward_from_chat.id}</code>")
        if r.sender_chat:
            lines.append(f"<b>📢 Sender chat:</b> <code>{r.sender_chat.id}</code>")
        m = _media(r)
        if m and getattr(m, "file_id", None):
            lines.append(f"<b>📎 File ID:</b> <code>{m.file_id}</code>")
    await message.reply_text("\n".join(lines))


# ─────────────────────────── /info ───────────────────────────
@Client.on_message(filters.command("info"))
async def info_cmd(client: Client, message: Message):
    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user.id
    elif len(message.command) > 1:
        arg = message.command[1]
        target = int(arg) if arg.lstrip("-").isdigit() else arg
    elif message.from_user:
        target = message.from_user.id
    try:
        u = await client.get_users(target)
    except Exception as e:
        return await message.reply_text(f"❌ Couldn't find that user: <code>{e}</code>")
    status = getattr(u.status, "name", str(u.status or "")).replace("_", " ").title() if u.status else "Hidden"
    text = (
        "<b>👤 User Info</b>\n\n<blockquote>"
        f"<b>Name:</b> {u.mention}\n"
        f"<b>ID:</b> <code>{u.id}</code>\n"
        f"<b>Username:</b> @{u.username or '—'}\n"
        f"<b>DC:</b> {u.dc_id or '?'}\n"
        f"<b>Premium:</b> {'Yes' if u.is_premium else 'No'}\n"
        f"<b>Bot:</b> {'Yes' if u.is_bot else 'No'}\n"
        f"<b>Last seen:</b> {status}"
        "</blockquote>"
    )
    await message.reply_text(text)


# ─────────────────────────── /json ───────────────────────────
@Client.on_message(filters.command("json"))
async def json_cmd(client: Client, message: Message):
    target = message.reply_to_message or message
    raw = str(target)
    if len(raw) < 3900:
        return await message.reply_text(f"<pre language='json'>{raw.replace('<', '&lt;')}</pre>")
    f = io.BytesIO(raw.encode())
    f.name = f"message_{target.id}.json"
    await message.reply_document(f, caption="📄 Message JSON")


# ─────────────────────────── /short ───────────────────────────
@Client.on_message(filters.command(["short", "shorten"]))
async def short_cmd(client: Client, message: Message):
    url = message.command[1] if len(message.command) > 1 else (
        message.reply_to_message.text.strip() if message.reply_to_message and message.reply_to_message.text else "")
    if not url.startswith(("http://", "https://")):
        return await message.reply_text("<b>Usage:</b> <code>/short https://example.com/very/long/link</code>")
    results = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
        for name, api in (("is.gd", "https://is.gd/create.php?format=simple&url="),
                          ("TinyURL", "https://tinyurl.com/api-create.php?url=")):
            try:
                async with s.get(api + quote(url, safe="")) as r:
                    t = (await r.text()).strip()
                    if r.status == 200 and t.startswith("http"):
                        results.append((name, t))
            except Exception:
                pass
    if not results:
        return await message.reply_text("❌ Shortening failed, try again later.")
    await message.reply_text(
        "<b>🔗 Short links</b>\n\n" + "\n".join(f"<b>{n}:</b> <code>{u}</code>" for n, u in results),
        disable_web_page_preview=True,
    )


# ─────────────────────────── /qr ───────────────────────────
@Client.on_message(filters.command("qr"))
async def qr_cmd(client: Client, message: Message):
    text = message.text.split(None, 1)[1] if len(message.command) > 1 else (
        (message.reply_to_message.text or message.reply_to_message.caption or "") if message.reply_to_message else "")
    if not text:
        return await message.reply_text("<b>Usage:</b> <code>/qr any text or link</code>")
    try:
        import qrcode
        img = qrcode.make(text)
        bio = io.BytesIO()
        img.save(bio, "PNG")
        bio.name = "qr.png"
        bio.seek(0)
        await message.reply_photo(bio, caption=f"<b>🔳 QR for:</b>\n<code>{text[:900]}</code>")
    except Exception as e:
        log.warning(f"qr failed: {e}")
        await message.reply_text("❌ Couldn't create the QR code.")


# ─────────────────────────── /mediainfo ───────────────────────────
def _fmt_stream(st: dict) -> str:
    t = st.get("codec_type", "?")
    codec = st.get("codec_name", "?")
    lang = (st.get("tags") or {}).get("language", "")
    title = (st.get("tags") or {}).get("title", "")
    if t == "video":
        fps = st.get("avg_frame_rate", "0/1")
        try:
            n, d = fps.split("/")
            fps = f"{int(n) / int(d):.3f}".rstrip("0").rstrip(".") if int(d) else "?"
        except Exception:
            pass
        extra = f"{st.get('width')}x{st.get('height')} · {fps} fps · {st.get('pix_fmt', '')}"
        prof = st.get("profile")
        if prof:
            extra += f" · {prof}"
    elif t == "audio":
        extra = f"{st.get('channels', '?')}ch · {st.get('sample_rate', '?')} Hz"
    else:
        extra = ""
    bits = [f"<b>{t.title()}</b>: <code>{codec}</code>", extra, lang, title]
    return " · ".join(b for b in bits if b)


@Client.on_message(filters.command("mediainfo"))
async def mediainfo_cmd(client: Client, message: Message):
    r = message.reply_to_message
    media = _media(r)
    if not media or not getattr(media, "file_id", None):
        return await message.reply_text("↩️ Reply to a video / audio / document with <code>/mediainfo</code>")
    if not shutil.which("ffprobe"):
        return await message.reply_text("❌ ffprobe is not installed on the server.")
    status = await message.reply_text("🔎 <i>Reading file header…</i>")
    path = os.path.join(TMP, f"mi_{message.id}_{int(time.time())}")
    try:
        # Only the first ~8 MB is needed for container / stream info.
        with open(path, "wb") as f:
            async for chunk in client.stream_media(r, limit=8):
                f.write(chunk)
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        info = json.loads(out or b"{}")
        fmt = info.get("format", {})
        streams = info.get("streams", [])
        name = getattr(media, "file_name", None) or "file"
        size = getattr(media, "file_size", 0)
        duration = float(fmt.get("duration") or getattr(media, "duration", 0) or 0)
        header = (
            f"<b>📄 {name}</b>\n\n<blockquote>"
            f"<b>Size:</b> {humanbytes(size)}\n"
            f"<b>Container:</b> {fmt.get('format_long_name') or fmt.get('format_name', '?')}\n"
            f"<b>Duration:</b> {time.strftime('%H:%M:%S', time.gmtime(duration)) if duration else '?'}\n"
            f"<b>Mime:</b> {getattr(media, 'mime_type', '?')}</blockquote>\n"
        )
        body = "\n".join(f"• {_fmt_stream(s)}" for s in streams) or "<i>No streams detected in header.</i>"
        await status.edit_text((header + "<b>Streams</b>\n" + body)[:4000])
    except Exception as e:
        await status.edit_text(f"❌ mediainfo failed: <code>{e}</code>")
    finally:
        if os.path.exists(path):
            os.remove(path)


# ─────────────────────────── /upload ───────────────────────────
@Client.on_message(filters.command(["upload", "link"]))
async def upload_cmd(client: Client, message: Message):
    r = message.reply_to_message
    media = _media(r)
    uid = message.from_user.id if message.from_user else 0
    if not media:
        return await message.reply_text("↩️ Reply to a file (≤ 200 MB) with <code>/upload</code> to get a public link.")
    if (getattr(media, "file_size", 0) or 0) > 200 * 1024 * 1024:
        return await message.reply_text("❌ Max size for /upload is 200 MB.")
    if uid in _busy:
        return await message.reply_text("⏳ Please wait for your previous job to finish.")
    _busy.add(uid)
    status = await message.reply_text("📥 <i>Downloading…</i>")
    path = None
    try:
        path = await client.download_media(r, file_name=f"{TMP}/{uid}_{int(time.time())}/",
                                           progress=_progress_cb(status, "📥 Downloading…"))
        await status.edit_text("☁️ <i>Uploading to host…</i>")
        url = await upload_to_host(path)
        if not url:
            return await status.edit_text("❌ Upload failed, try again later.")
        await status.edit_text(
            f"<b>✅ Uploaded!</b>\n\n<b>🔗 Link:</b> <code>{url}</code>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Open", url=url)]]),
            disable_web_page_preview=True,
        )
    except Exception as e:
        await status.edit_text(f"❌ Error: <code>{e}</code>")
    finally:
        _busy.discard(uid)
        if path and os.path.exists(path):
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)


# ─────────────────────────── /rename ───────────────────────────
@Client.on_message(filters.command("rename") & filters.private)
async def rename_cmd(client: Client, message: Message):
    r = message.reply_to_message
    media = _media(r)
    uid = message.from_user.id
    if not media or len(message.command) < 2:
        return await message.reply_text("<b>Usage:</b> reply to a file with <code>/rename New Name.mkv</code>")
    new_name = message.text.split(None, 1)[1].strip().replace("/", "_")[:200]
    old_name = getattr(media, "file_name", None) or ""
    if "." not in new_name and "." in old_name:
        new_name += "." + old_name.rsplit(".", 1)[1]
    if uid in _busy:
        return await message.reply_text("⏳ Please wait for your previous job to finish.")
    _busy.add(uid)
    status = await message.reply_text("📥 <i>Downloading…</i>")
    workdir = f"{TMP}/{uid}_{int(time.time())}"
    thumb_path = None
    try:
        os.makedirs(workdir, exist_ok=True)
        path = await client.download_media(r, file_name=f"{workdir}/{new_name}",
                                           progress=_progress_cb(status, "📥 Downloading…"))
        # Re-use the user's saver thumbnail / caption if they set one.
        caption = f"<code>{new_name}</code>"
        try:
            from database.db import db as saver_db
            thumb_id = await saver_db.get_thumbnail(uid)
            if thumb_id:
                thumb_path = await client.download_media(thumb_id, file_name=f"{workdir}/thumb.jpg")
            custom = await saver_db.get_caption(uid)
            if custom:
                try:
                    caption = custom.format(filename=new_name, size=humanbytes(os.path.getsize(path)))
                except Exception:
                    caption = custom
        except Exception:
            pass
        await client.send_document(
            message.chat.id, path, file_name=new_name, caption=caption[:1024], thumb=thumb_path,
            force_document=True, reply_to_message_id=message.id,
            progress=_progress_cb(status, "📤 Uploading…"),
        )
        await status.delete()
    except Exception as e:
        await status.edit_text(f"❌ Rename failed: <code>{e}</code>")
    finally:
        _busy.discard(uid)
        shutil.rmtree(workdir, ignore_errors=True)


# ─────────────────────────── /source (AGPL §13) ───────────────────────────
@Client.on_message(filters.command(["source", "repo"]))
async def source_cmd(client: Client, message: Message):
    if not SOURCE_URL:
        return
    await message.reply_text(
        f"📦 This bot includes free software (AGPL-3.0 / MIT).\nSource code: {SOURCE_URL}",
        disable_web_page_preview=True,
    )



import asyncio
import contextvars
import html
import os
import time
from datetime import datetime
from urllib.parse import unquote_plus

from httpx import delete
from pyrogram.errors.exceptions.bad_request_400 import (MessageIdInvalid,
                                                        MessageNotModified)
from pyrogram.parser import html as pyrogram_html
from pyrogram.types import Message
from requests.utils import unquote

from .. import LOGGER, data, download_dir, video_mimetype
from .database.access_db import db
from .direct_link_generator import direct_link_generator
from .display_progress import humanbytes, progress_for_pyrogram
from .helper import delete_downloads, get_zip_folder, handle_encode, handle_extract, handle_url
from .uploads.drive import _get_file_id
from .uploads.drive.download import Downloader
from .encoding import get_media_streams
from ..video_utils.audio_selector import AudioSelect

_DEPTH = contextvars.ContextVar("videl_enc_depth", default=0)


async def on_task_complete(message=None):
    """A task ended (done / failed / cancelled): drop it, clean ITS folders, start what's next."""
    from . import scheduler
    if message is None:                       # legacy callers: the oldest running task, else the queue head
        rs = scheduler.running()
        message = rs[0] if rs else (data[0] if data else None)
    if message is not None:
        scheduler.finish(message)
        scheduler.cleanup_task(message)
        await scheduler.forget(message)
    if not data and not scheduler.running():
        delete_downloads()                    # fully idle → wipe leftovers (the source cache stays)
        try:
            from . import srccache
            srccache.prune()
        except Exception:
            pass
    await dispatch()


def _skippable(message, mode) -> bool:
    """A bare (command-less) non-video document that slipped into the queue."""
    text = message.text or message.caption or ""
    return (mode == 'tg' and not text.startswith("/") and getattr(message, "document", None) is not None
            and message.document.mime_type not in video_mimetype)


async def _spawn(message, mode):
    _DEPTH.set(0)
    await handle_tasks(message, mode)


async def dispatch(spawn_all: bool = False):
    """Fill every free worker slot with the next waiting task (priority first)."""
    from . import scheduler
    starts = []
    while scheduler.slots_free() > 0:
        nxt = scheduler.next_waiting()
        if nxt is None:
            break
        mode = scheduler.mode_of(nxt)
        if mode is None or _skippable(nxt, mode):
            scheduler.finish(nxt)
            await scheduler.forget(nxt)
            continue
        scheduler.mark_running(nxt)
        starts.append((nxt, mode))
    if not starts:
        return
    depth = _DEPTH.get()
    inline = None if spawn_all or depth > 20 else starts[0]      # the finishing worker continues with one
    for m, mode in starts:
        if inline is None or m is not inline[0]:
            from core.bg import spawn
            spawn(_spawn(m, mode), name=f"encoder-{mode}")
    if inline:
        token = _DEPTH.set(depth + 1)
        try:
            await handle_tasks(*inline)
        finally:
            _DEPTH.reset(token)


_MODE_TITLE = {'tg': "Encode", 'url': "Encode (link)", 'af': "Audio arrange", 'batch': "Batch encode",
               'sample': "Sample encode", 'trim': "Trim", 'screens': "Screenshots", 'mux': "Add track",
               'merge': "Merge videos", 'convert': "Convert", 'leech': "Link upload", 'compress': "🗜 Compress"}


async def handle_tasks(message, mode):
    msg = None
    from . import scheduler
    scheduler.mark_running(message)
    try:
        from . import jobs
        from .encoding import cancel_markup
        first = f"<b>📥 Downloading…</b>\n<i>{_MODE_TITLE.get(mode, 'Task')}</i>"
        note = scheduler.NOTES.pop(id(message), None)
        if note is not None:                  # one message per task: the queue note turns into the status card
            try:
                await note.edit(first)
                msg = note
            except Exception:
                msg = None
        if msg is None:
            msg = await message.reply_text(first)
        jobs.register(msg.id, message.from_user.id if message.from_user else 0, message.chat.id, stage="download")
        try:
            await msg.edit_reply_markup(cancel_markup(msg.id))
        except Exception:
            pass
        if mode == 'tg':
            await tg_task(message, msg)
        elif mode == 'url':
            await url_task(message, msg)
        elif mode == 'af':
            await af_task(message, msg)
        elif mode == 'sample':
            await sample_task(message, msg)
        elif mode == 'trim':
            await trim_task(message, msg)
        elif mode == 'screens':
            await screens_task(message, msg)
        elif mode == 'mux':
            await mux_task(message, msg)
        elif mode == 'merge':
            await merge_task(message, msg)
        elif mode == 'convert':
            await convert_task(message, msg)
        elif mode == 'leech':
            await leech_task(message, msg)
        elif mode == 'compress':
            await compress_task(message, msg)
        else:
            await batch_task(message, msg)
    except MessageNotModified:
        pass
    except IndexError:
        return
    except MessageIdInvalid:
        if msg is not None:
            try:
                await msg.edit('Download Cancelled!')
            except Exception:
                pass
    except FileNotFoundError:
        LOGGER.error('[FileNotFoundError]: Maybe due to cancel, hmm')
        import traceback
        LOGGER.error(traceback.format_exc())
    except Exception as e:
        import traceback
        LOGGER.error(traceback.format_exc())
        err = f"❌ <b>Error:</b> <code>{html.escape(str(e))[:300]}</code>"
        edited = False
        if msg is not None:                   # show it on the task's own card instead of a second message
            try:
                await msg.edit(err, reply_markup=None)
                edited = True
            except Exception:
                pass
        if not edited:
            await message.reply(text=err)
    finally:
        if msg is not None:
            from . import jobs
            jobs.unregister(msg.id)
        await on_task_complete(message)


async def _cancelled(msg) -> bool:
    from . import jobs
    if jobs.is_cancelled(msg.id):
        try:
            await msg.edit("🚫 <b>Task cancelled.</b>")
        except Exception:
            pass
        return True
    return False


def _dirs(message):
    from .scheduler import task_dirs
    return task_dirs(message)


async def _download(message, msg):
    filepath = await handle_tg_down(message, msg, dest_dir=_dirs(message)[0])
    if await _cancelled(msg):
        if filepath and os.path.isfile(filepath):
            os.remove(filepath)
        return None
    if not filepath:
        await msg.edit("❌ Download failed or no file found.")
        return None
    return filepath


async def tg_task(message, msg):
    filepath = await _download(message, msg)
    if not filepath:
        return
    await msg.edit('<b>🎬 Encoding…</b>')
    await handle_encode(filepath, message, msg)


async def sample_task(message, msg):
    """/sample [seconds] – encode a short clip from the middle with the current settings."""
    from .ffcmd import parse_timestamp
    parts = (message.text or message.caption or "").split()
    length = 30
    if len(parts) > 1:
        length = int(parse_timestamp(parts[1]) or 30)
    filepath = await _download(message, msg)
    if not filepath:
        return
    await msg.edit(f'<b>🧪 Encoding a {max(5, min(120, length))}s sample…</b>')
    await handle_encode(filepath, message, msg, opts={"sample": length})


def parse_trim_args(text):
    """'/trim 1:00 2:30' → (60.0, 150.0); '/trim 90' → (90.0, None); invalid → None."""
    from .ffcmd import parse_timestamp
    parts = (text or "").split()[1:]
    if not parts or len(parts) > 2:
        return None
    start = parse_timestamp(parts[0])
    end = parse_timestamp(parts[1]) if len(parts) == 2 else None
    if start is None or (len(parts) == 2 and (end is None or end <= start)):
        return None
    return start, end


async def trim_task(message, msg):
    from . import ffcmd
    from .encoding import trim
    from .uploads import upload_worker
    rng = parse_trim_args(message.text or message.caption)
    if not rng:
        await msg.edit("Usage: <code>/trim 00:01:00 00:02:30</code> (reply to a video)")
        return
    filepath = await _download(message, msg)
    if not filepath:
        return
    start, end = rng
    await msg.edit(f"<b>✂️ Trimming</b> <code>{ffcmd.fmt_ts(start)}</code> → "
                   f"<code>{ffcmd.fmt_ts(end) if end is not None else 'end'}</code>…")
    out = await trim(filepath, start, end, key=msg.id, out_dir=_dirs(message)[1])
    if await _cancelled(msg):
        return
    if not out:
        await msg.edit("❌ <b>Trim failed.</b> <i>Check the timestamps are inside the video.</i>")
        return
    size = os.path.getsize(out)
    link = await upload_worker(out, message, msg)
    if link is None and await _cancelled(msg):
        return
    from .helper import _done_markup
    from core.style import hdr, row
    await msg.edit("\n".join([hdr("✂️", "Trim complete"), f"<code>{html.escape(os.path.basename(out))[:80]}</code>", "",
                               row("Range", f"{ffcmd.fmt_ts(start)} → {ffcmd.fmt_ts(end) if end is not None else 'end'}"),
                               row("Size", humanbytes(size)),
                               row("Mode", "Lossless (stream copy, cut at keyframes)")]),
                   reply_markup=_done_markup(link), disable_web_page_preview=True)
    from core.analytics import bump_later
    bump_later("tool")


async def screens_task(message, msg):
    from pyrogram.types import InputMediaPhoto
    from . import ffcmd
    from .encoding import probe, screenshots
    parts = (message.text or message.caption or "").split()
    count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 6
    count = max(1, min(10, count))
    filepath = await _download(message, msg)
    if not filepath:
        return
    await msg.edit(f"<b>📸 Taking {count} screenshots…</b>")
    info = await probe(filepath)
    shots = await screenshots(filepath, count, info["duration"], key=msg.id, out_dir=_dirs(message)[1])
    if await _cancelled(msg):
        return
    if not shots:
        await msg.edit("❌ <b>Couldn't take screenshots from this file.</b>")
        return
    name = html.escape(os.path.basename(filepath))[:80]
    media = [InputMediaPhoto(p, caption=(f"📸 <code>{name}</code>\n" if i == 0 else "") + f"⏱ {ffcmd.fmt_ts(at)}")
             for i, (p, at) in enumerate(shots)]
    await message.reply_media_group(media)
    await msg.edit(f"✅ <b>{len(shots)} screenshots</b> from <code>{name}</code>")


async def af_task(message, msg):
    filepath = await _download(message, msg)
    if not filepath:
        return

    # Probe for streams
    streams = await asyncio.to_thread(get_media_streams, filepath)
    if not streams:
         await msg.edit("Could not retrieve media streams.")
         return

    selector = AudioSelect(message._client, message)
    await msg.delete() # Delete the downloading message as AudioSelect will send its own interface

    # AudioSelect expects streams list
    audio_map, _ = await selector.get_buttons(streams)

    if audio_map == -1:
        # Cancelled or error
        return

    # Proceed to encode with the new map
    msg = await message.reply("Encoding with new audio arrangement...")
    await handle_encode(filepath, message, msg, audio_map)


async def url_task(message, msg):
    try:
        filepath = await handle_download_url(message, msg, False, dest_dir=_dirs(message)[0])
    except RuntimeError:
        if await _cancelled(msg):
            return
        raise
    if not filepath:
        # Error handled in handle_download_url logic or implicit failure
        return
    await msg.edit_text("Encoding...")
    await handle_encode(filepath, message, msg)


async def batch_task(message, msg):
    # "/batch <url>" sent as a reply to some text used to ignore the url and fail with NO ZIP FOUND
    if message.reply_to_message and getattr(message.reply_to_message, "document", None):
        filepath = await handle_tg_down(message, msg, mode='reply', dest_dir=_dirs(message)[0])
    else:
        filepath = await handle_download_url(message, msg, True, dest_dir=_dirs(message)[0])
    if not filepath:
        await msg.edit('NO ZIP FOUND!')
        return
    if os.path.isfile(filepath):
        path = await get_zip_folder(filepath)
        await handle_extract(filepath)
        if not os.path.isdir(path):
            await msg.edit('extract failed!')
            return
        filepath = path
    if os.path.isdir(filepath):
        path = filepath
    else:
        await msg.edit('Something went wrong, hell!')
        return
    await msg.edit('<b>📕 Encode Started!</b>')
    sentfiles = []
    # Encode
    for dirpath, subdir, files_ in sorted(os.walk(path)):
        for i in sorted(files_):
            msg_ = await message.reply('Encoding')
            filepath = os.path.join(dirpath, i)
            await msg.edit('Encode Started!\nEncoding: <code>{}</code>'.format(i))
            try:
                url = await handle_encode(filepath, message, msg_)
            except Exception as e:
                await msg_.edit(str(e) + '\n\n Continuing...')
                continue
            else:
                sentfiles.append((i, url))
    text = '✨ <b>#EncodedFiles:</b> \n\n'
    quote = None
    first_index = None
    all_amount = 1
    for filename, filelink in sentfiles:
        if filelink:
            atext = f'- <a href="{filelink}">{html.escape(filename)}</a>'
        else:
            atext = f'- {html.escape(filename)} (empty)'
        atext += '\n'
        futtext = text + atext
        if all_amount > 100:
            thing = await message.reply_text(text, quote=quote, disable_web_page_preview=True)
            if first_index is None:
                first_index = thing
            quote = False
            futtext = atext
            all_amount = 1
            await asyncio.sleep(3)
        all_amount += 1
        text = futtext
    if not sentfiles:
        text = 'Files: None'
    thing = await message.reply_text(text, quote=quote, disable_web_page_preview=True)
    if first_index is None:
        first_index = thing
    await msg.edit('Encoded Files! Links: {}'.format(first_index.link), disable_web_page_preview=True)


async def handle_download_url(message, msg, batch, dest_dir=None):
    url = message.text.split(None, 1)[1].strip()
    if 'drive.google.com' in url:
        file_id = _get_file_id(url)
        n = Downloader()
        custom_file_name = n.name(file_id)
    else:
        # Default filename from URL basename
        custom_file_name = unquote_plus(os.path.basename(url))

    if "|" in url and not batch:
        url, c_file_name = url.split("|", maxsplit=1)
        url = url.strip()
        if c_file_name:
            custom_file_name = c_file_name.strip()
    elif " " in url and not batch:
        # Attempt to handle space-separated URL and filename
        # This assumes the URL itself doesn't contain unencoded spaces, which is standard.
        parts = url.split()
        if len(parts) > 1:
            url = parts[0]
            custom_file_name = " ".join(parts[1:])

    direct = await asyncio.to_thread(direct_link_generator, url)   # uses blocking requests
    if direct:
        url = direct

    # Ensure filename is safe/valid (no paths / traversal) or fallback
    custom_file_name = os.path.basename((custom_file_name or "").replace("\\", "/")).strip().lstrip(".")
    custom_file_name = "".join(ch for ch in custom_file_name if ch not in '<>:"|?*\x00')[:200]
    if not custom_file_name:
        custom_file_name = "downloaded_file"

    path = os.path.join(dest_dir or download_dir, custom_file_name)
    filepath = path
    if 'drive.google.com' in url:
        await n.handle_drive(msg, url, custom_file_name, batch)
    else:
        await handle_url(url, filepath, msg)
    return filepath


def _media(m):
    for kind in ("video", "document", "audio", "animation", "voice"):
        media = getattr(m, kind, None)
        if media is not None:
            return media
    return None


async def _fetch_cached(src_msg, dest, label, msg, started):
    """Source cache hit → instant; otherwise a (parallel) download that is cached for the next task."""
    from core import fastdl
    from . import jobs, srccache
    media = _media(src_msg)
    if media is not None:
        hit = srccache.get(media, dest)
        if hit:
            return hit
    path = await fastdl.fetch(src_msg, file_name=os.path.join(dest, ""), progress=progress_for_pyrogram,
                              progress_args=(label, msg, started))
    if path and media is not None and not jobs.is_cancelled(getattr(msg, "id", None)):
        srccache.put(media, path)
    return path


async def handle_tg_down(message, msg, mode='no_reply', dest_dir=None):
    c_time = time.time()

    # Determine what to download
    target_msg = message
    r = message.reply_to_message
    if r and (r.video or r.document or getattr(r, "audio", None)):       # audio: /convert on a music file
        target_msg = r
    elif message.video or message.document or getattr(message, "audio", None):
        target_msg = message
    elif mode == 'reply' and message.reply_to_message:
        target_msg = message.reply_to_message
    else:
        # If command was just /dl without reply and without attachment, and mode is not explicit reply
        if not (message.reply_to_message and (message.reply_to_message.video or message.reply_to_message.document)):
             return None
        target_msg = message.reply_to_message

    path = await _fetch_cached(target_msg, dest_dir or download_dir, "Downloading...", msg, c_time)

    return path


# ═════════════════════════ Phase 15 task types ═════════════════════════
async def _fetch_msg(message, ref):
    """[chat_id, msg_id] → Message (None if it's gone)."""
    try:
        m = await message._client.get_messages(int(ref[0]), int(ref[1]))
        return None if not m or getattr(m, "empty", False) else m
    except Exception:
        return None


async def _download_ref(message, msg, ref, dest, label):
    from . import jobs
    src = await _fetch_msg(message, ref)
    media = src and (src.video or src.document or src.audio or getattr(src, "voice", None))
    if not media:
        return None
    path = await _fetch_cached(src, dest, label, msg, time.time())
    if jobs.is_cancelled(msg.id):
        return None
    return path


async def _finish_card(message, msg, out, title, rows_):
    from .uploads.telegram import upload_to_tg
    from .helper import _done_markup
    from core.style import hdr, row
    size = os.path.getsize(out)
    link = await upload_to_tg(out, message, msg)
    if link is None and await _cancelled(msg):
        return
    lines = [hdr("✅", title), f"<code>{html.escape(os.path.basename(out))[:80]}</code>", "",
             row("Size", humanbytes(size))] + [row(k, v) for k, v in rows_]
    await msg.edit("\n".join(lines), reply_markup=_done_markup(link), disable_web_page_preview=True)
    from core.analytics import bump_later
    bump_later("tool")


async def mux_task(message, msg):
    """/mux – add a subtitle or audio track to a video (no re-encode)."""
    from . import ffcmd, scheduler
    from .encoding import probe, run_ffmpeg
    extra = scheduler.extra_of(message)
    dl, enc = _dirs(message)
    video = await _download(message, msg)
    if not video:
        return
    track = await _download_ref(message, msg, extra.get("track") or [0, 0], dl, "Downloading the track…")
    if await _cancelled(msg):
        return
    if not track:
        await msg.edit("❌ <b>The subtitle / audio file is gone</b> – send /mux again.")
        return
    kind = extra.get("kind") or ffcmd.track_kind(track) or "sub"
    await msg.edit(f"<b>🧩 Adding the {'subtitle' if kind == 'sub' else 'audio'} track…</b>")
    info, tinfo = await probe(video), await probe(track)
    t_codec = ((tinfo.get("audio") or [{}])[0]).get("codec_name", "") if kind == "audio" else ""
    base = os.path.splitext(os.path.basename(video))[0]
    out = os.path.join(enc, base + ".muxed" + ffcmd.mux_ext(video))
    code, err = await run_ffmpeg(ffcmd.mux_command(video, track, out, kind, info, t_codec), msg.id)
    if await _cancelled(msg):
        return
    if code != 0 or not os.path.isfile(out) or not os.path.getsize(out):
        await msg.edit("❌ <b>Couldn't add the track.</b>\n"
                       f"<blockquote expandable><code>{html.escape(err)[-280:]}</code></blockquote>")
        return
    await _finish_card(message, msg, out, "Track added", [
        ("Added", ("💬 Subtitle " if kind == "sub" else "🔊 Audio ") + os.path.splitext(track)[1].upper().lstrip(".")),
        ("Mode", "Lossless (stream copy)")])


async def merge_task(message, msg):
    """/merge – join 2-10 videos (lossless when they match, otherwise normalised to the first one)."""
    from . import ffcmd, scheduler
    from .encoding import probe, run_ffmpeg
    parts = scheduler.extra_of(message).get("parts") or []
    dl, enc = _dirs(message)
    paths = []
    for i, ref in enumerate(parts, 1):
        sub = os.path.join(dl, f"{i:02d}")
        os.makedirs(sub, exist_ok=True)
        p = await _download_ref(message, msg, ref, sub, f"Downloading {i}/{len(parts)}…")
        if await _cancelled(msg):
            return
        if p:
            paths.append(p)
    if len(paths) < 2:
        await msg.edit("❌ <b>Need at least 2 videos to merge</b> – some files were deleted.")
        return
    infos = [await probe(p) for p in paths]
    base = os.path.splitext(os.path.basename(paths[0]))[0]
    lossless = ffcmd.can_concat_copy(infos)
    if lossless:
        ext = ".mp4" if all(p.lower().endswith(".mp4") for p in paths) else ".mkv"
        inputs = paths
    else:
        w, h, fps = ffcmd.merge_target(infos)
        ext, inputs = ".mp4", []
        for i, (p, inf) in enumerate(zip(paths, infos), 1):
            await msg.edit(f"<b>🔧 Matching video {i}/{len(paths)}</b> to {w}×{h} @ {fps} fps…")
            norm = os.path.join(enc, f"norm_{i:02d}.mp4")
            code, err = await run_ffmpeg(ffcmd.normalize_command(p, norm, inf, w, h, fps), msg.id)
            if await _cancelled(msg):
                return
            if code != 0:
                await msg.edit(f"❌ <b>Video {i} couldn't be converted.</b>\n"
                               f"<blockquote expandable><code>{html.escape(err)[-280:]}</code></blockquote>")
                return
            inputs.append(norm)
    await msg.edit(f"<b>🔗 Joining {len(inputs)} videos…</b>")
    list_file = os.path.join(enc, "concat.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        f.write(ffcmd.concat_list(inputs))
    out = os.path.join(enc, base + ".merged" + ext)
    code, err = await run_ffmpeg(ffcmd.concat_command(list_file, out), msg.id)
    if await _cancelled(msg):
        return
    if code != 0 or not os.path.isfile(out) or not os.path.getsize(out):
        await msg.edit("❌ <b>Merge failed.</b>\n"
                       f"<blockquote expandable><code>{html.escape(err)[-280:]}</code></blockquote>")
        return
    total = sum(i.get("duration") or 0 for i in infos)
    from . import ffcmd as _f
    await _finish_card(message, msg, out, "Videos merged", [
        ("Parts", str(len(inputs))), ("Length", _f.fmt_ts(total)),
        ("Mode", "Lossless join" if lossless else "Normalised to the first video (H.264 / AAC)")])


async def convert_task(message, msg):
    """/convert mp3|m4a|opus|flac|wav|gif – audio extraction or a GIF clip."""
    from . import ffcmd, scheduler
    from .encoding import probe, run_ffmpeg
    extra = scheduler.extra_of(message)
    fmt = extra.get("fmt", "mp3")
    _dl, enc = _dirs(message)
    src = await _download(message, msg)
    if not src:
        return
    info = await probe(src)
    base = os.path.splitext(os.path.basename(src))[0]
    if fmt == "gif":
        if info.get("video") is None and info.get("ok"):
            await msg.edit("❌ <b>No video stream</b> – a GIF needs a video.")
            return
        start, length = ffcmd.gif_window(info.get("duration") or 0, extra.get("start"), extra.get("length"))
        await msg.edit(f"<b>🎞 Making a {length:g}s GIF</b> from {ffcmd.fmt_ts(start)}…")
        out = os.path.join(enc, f"{base}.gif")
        code, err = await run_ffmpeg(ffcmd.gif_command(src, out, start, length), msg.id, timeout=900)
        if await _cancelled(msg):
            return
        if code != 0 or not os.path.isfile(out):
            await msg.edit(f"❌ <b>GIF failed.</b>\n<code>{html.escape(err)[-200:]}</code>")
            return
        await message.reply_animation(out, caption=f"🎞 <code>{html.escape(base)[:60]}</code> · "
                                                   f"{ffcmd.fmt_ts(start)} +{length:g}s · {humanbytes(os.path.getsize(out))}")
        await msg.edit(f"✅ <b>GIF ready</b> – {humanbytes(os.path.getsize(out))}")
        from core.analytics import bump_later
        bump_later("tool")
        return
    if not info.get("audio") and info.get("ok"):
        await msg.edit("❌ <b>This file has no audio track.</b>")
        return
    ext, _args = ffcmd.CONVERT_FORMATS[fmt]
    out = os.path.join(enc, base + ext)
    await msg.edit(f"<b>🎧 Extracting audio → {fmt.upper()}…</b>")
    code, err = await run_ffmpeg(ffcmd.audio_extract_command(src, out, fmt), msg.id, timeout=3600)
    if await _cancelled(msg):
        return
    if code != 0 or not os.path.isfile(out) or not os.path.getsize(out):
        await msg.edit(f"❌ <b>Audio extraction failed.</b>\n<code>{html.escape(err)[-200:]}</code>")
        return
    dur = int((await probe(out)).get("duration") or info.get("duration") or 0)
    await message.reply_audio(out, caption=f"🎧 <code>{html.escape(base)[:60]}</code> · {fmt.upper()}",
                              duration=dur, title=base[:60], performer="Videl", progress=progress_for_pyrogram,
                              progress_args=("Uploading…", msg, time.time()))
    await msg.edit(f"✅ <b>Audio ready</b> – {fmt.upper()} · {humanbytes(os.path.getsize(out))}")
    from core.analytics import bump_later
    bump_later("tool")


async def leech_task(message, msg):
    """/leech <url> [| name] – download a Mega / Google Drive / direct link and upload it as-is."""
    import config
    from . import leech, scheduler
    from .uploads.telegram import upload_to_tg
    from .helper import _done_markup
    from core.style import hdr, row
    extra = scheduler.extra_of(message)
    url = extra.get("url") or ""
    dl, _enc = _dirs(message)
    pro = False
    try:
        from core.plans import is_encoder_pro
        pro = await is_encoder_pro(message.from_user.id)
    except Exception:
        pass
    limit = int((config.LEECH_PRO_GB if pro else config.LEECH_FREE_GB) * 1024 ** 3)
    await msg.edit(f"<b>🌐 Connecting…</b>\n<code>{html.escape(url[:80])}</code>")
    try:
        path = await leech.download(url, dl, name=extra.get("name") or None, key=msg.id, msg=msg, max_bytes=limit)
    except leech.Cancelled:
        await _cancelled(msg)
        return
    except leech.LeechError as e:
        note = "" if pro else "\n<i>🎬 Encoder Pro raises the limit – see /plans.</i>" if "limit" in str(e) else ""
        await msg.edit(f"❌ <b>Download failed:</b> {html.escape(str(e))}{note}")
        return
    except Exception as e:                       # network errors, timeouts, disk full …
        LOGGER.warning(f"leech {url[:80]}: {type(e).__name__}: {e}")
        await msg.edit(f"❌ <b>Download failed:</b> {html.escape(type(e).__name__)} – "
                       f"{html.escape(str(e))[:200] or 'the connection dropped'}.\n<i>Try again, or check the link.</i>")
        return
    if await _cancelled(msg):
        return
    size = os.path.getsize(path)
    await msg.edit(f"<b>📤 Downloaded {humanbytes(size)}</b> – uploading…")
    is_video = path.lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v"))
    link = await upload_to_tg(path, message, msg, as_doc=None if is_video else True)
    if link is None and await _cancelled(msg):
        return
    await msg.edit("\n".join([hdr("✅", "Link uploaded"), f"<code>{html.escape(os.path.basename(path))[:80]}</code>",
                              "", row("Size", humanbytes(size)), row("Source", leech.kind_of(url).replace(
                                  "gdrive", "Google Drive").replace("mega", "Mega").replace("direct", "Direct link"))]),
                   reply_markup=_done_markup(link), disable_web_page_preview=True)
    from core.analytics import bump_later
    bump_later("leech")


async def compress_task(message, msg):
    """/compress – download (or source-cache hit) → one fast encode with the chosen quality → upload."""
    from . import compress, scheduler
    from .encoding import LAST_ERROR, SIZE_WATCH, Encoded, encode, probe
    from .helper import _done_markup, _remove, _safe_edit
    from .uploads import upload_worker
    opts = compress.normalize(scheduler.extra_of(message))
    src = await _download(message, msg)
    if not src:
        return
    info = await probe(src)
    if info.get("video") is None and info.get("ok"):
        await _safe_edit(msg, "❌ <b>No video stream</b> – /compress needs a video.", _done_markup(None))
        _remove(src)
        return
    src_size = os.path.getsize(src)
    override, notes = compress.override(opts, info, src_size)
    tonemap = False
    hdr = compress.wants_tonemap(opts, info)
    if hdr:
        from .hw import has_filter
        tonemap = await asyncio.to_thread(has_filter, "zscale")
        notes.append(f"{hdr} → normal colours (SDR)" if tonemap else f"{hdr} kept – this ffmpeg can't tone-map")
    span = compress.cut_span(opts["cut"], info.get("duration") or 0) if opts["cut"] else None
    # what the result is measured against: the whole file, or the cut's share of it
    ref = int(src_size * span[1] / info["duration"]) if span and info.get("duration") else src_size
    await _safe_edit(msg, compress.working_text(opts, info, notes))
    result = await encode(src, message, msg, opts={"override": override, "info": info, "watch": ref,
                                                   "cut": opts["cut"] if span else None, "tonemap": tonemap})
    watched = SIZE_WATCH.pop(msg.id, None)
    if watched and not result:                        # stopped early: it was heading for a bigger file
        LAST_ERROR.pop(msg.id, None)
        await _safe_edit(msg, compress.bigger_text(opts, ref, watched[0] or ref, stopped_at=watched[1]),
                         _done_markup(None))
        _remove(src)
        return
    if not result:
        from . import jobs
        if not (jobs.is_cancelled(msg.id) or getattr(msg, "_videl_cancelled", False)):
            err = LAST_ERROR.pop(msg.id, "")
            text = "❌ <b>Compression failed.</b>"
            if err:
                text += f"\n<blockquote expandable><code>{html.escape(err)[-280:]}</code></blockquote>"
            await _safe_edit(msg, text + "\n<i>Try H.264 or another resolution.</i>", _done_markup(None))
        _remove(src)
        return
    new_size = os.path.getsize(result)
    # never send a "compressed" file that isn't smaller (a cut is always sent – it's a different video)
    if new_size >= ref * 0.98 and not span:
        await _safe_edit(msg, compress.bigger_text(opts, src_size, new_size), _done_markup(None))
        _remove(result, src)
        return
    stem = os.path.splitext(os.path.basename(src))[0]
    tag = compress.label(opts, int(info.get("height") or 0)).split()[0]
    if span:
        tag += " " + compress.fmt_cut([span[0], span[0] + span[1]]).replace(":", ".")
    final = os.path.join(os.path.dirname(result), f"{stem} [{tag}]{os.path.splitext(result)[1]}")
    try:
        os.replace(result, final)
        moved = Encoded(final)
        for k in ("info", "settings", "elapsed", "sample", "cut", "guard", "encoder", "where"):
            setattr(moved, k, getattr(result, k, None))
        result = moved
    except OSError:
        final = str(result)
    await _safe_edit(msg, "<b>📤 Compressed – uploading…</b>")
    try:
        link = await upload_worker(result, message, msg)
    except Exception as e:
        await _safe_edit(msg, f"❌ <b>Upload failed:</b> <code>{html.escape(str(e))[:300]}</code>", _done_markup(None))
        _remove(result, src)
        return
    if link is None and await _cancelled(msg):
        _remove(result, src)
        return
    shown = dict(info, duration=span[1]) if span else info
    await _safe_edit(msg, compress.done_text(os.path.basename(final), opts, shown, ref, new_size,
                                             result.elapsed or 0.0, notes), _done_markup(link))
    if opts["compare"]:
        await compress_compare(msg, src, str(result), opts, info, span, ref, new_size, tonemap)
    if shown.get("duration") and result.elapsed:
        await compress_learn(compress.speed_key(opts, int(info.get("height") or 0)),
                             float(shown["duration"]) / float(result.elapsed))
    try:
        await db.add_encode_stat(message.from_user.id, src_size, new_size, result.elapsed)
        from core.analytics import bump_later
        bump_later("encode")
    except Exception:
        pass
    _remove(result, src)


CMP_SPEED_ID = "cmp_speed"


async def compress_compare(msg, src: str, out: str, opts: dict, info: dict, span, old: int, new: int,
                           tonemap: bool = False):
    """🖼 The same frame before / after, side by side, as a reply to the result card. Best effort: a failure
    here never touches the finished task."""
    from . import compress, ffcmd
    from .encoding import run_ffmpeg
    from .helper import _remove
    try:
        length = span[1] if span else float(info.get("duration") or 0)
        at_out = length * 0.4 if length > 2 else 0.0
        at_src = (span[0] if span else 0.0) + at_out
        out_h = int(compress.out_class(compress.normalize(opts), {"height": info.get("height"),
                                                                  "width": info.get("width")}) or 720)
        dest = os.path.join(os.path.dirname(out), f"compare_{msg.id}.jpg")
        code, err = await run_ffmpeg(ffcmd.compare_command(src, out, at_src, at_out, out_h, dest, tonemap),
                                     timeout=90)
        if code != 0 or not os.path.isfile(dest):
            LOGGER.debug(f"compare frame failed ({code}): {err}")
            return
        try:
            await msg.reply_photo(dest, caption=compress.compare_caption(opts, info, old, new, at_src), quote=True)
        finally:
            _remove(dest)
    except Exception as e:
        LOGGER.debug(f"compare frame skipped: {e}")


async def compress_speeds() -> dict:
    """This server's measured /compress speed (× realtime) per codec + output size – for the panel's ETA."""
    try:
        from core.db import vdb
        doc = await vdb.db["runtime"].find_one({"_id": CMP_SPEED_ID}) or {}
        return dict(doc.get("speeds") or {})
    except Exception:
        return {}


async def compress_learn(key: str, realtime: float):
    try:
        from core.db import vdb
        from . import compress
        speeds = compress.learn(await compress_speeds(), key, realtime)
        await vdb.db["runtime"].update_one({"_id": CMP_SPEED_ID}, {"$set": {"speeds": speeds}}, upsert=True)
    except Exception as e:
        LOGGER.debug(f"compress speed not saved: {e}")

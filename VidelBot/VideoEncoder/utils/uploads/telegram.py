

import asyncio
import os
import time

from ... import LOGGER, app, download_dir, log
from ..database.access_db import db
from ..display_progress import progress_for_pyrogram
from ..encoding import get_duration, get_thumbnail, get_width_height


SPLIT_LIMIT = None      # bytes; None → config.SPLIT_SIZE_MB


def split_limit() -> int:
    if SPLIT_LIMIT:
        return SPLIT_LIMIT
    import config
    return max(50, min(1990, int(config.SPLIT_SIZE_MB))) * 1024 * 1024


async def upload_to_tg(new_file, message, msg, as_doc=None, caption=None):
    """Upload a file (video or document per the user's setting). Files above the Telegram limit are
    split into playable parts first. Returns the (first) link, or None when the user cancelled."""
    if os.path.isfile(new_file) and os.path.getsize(new_file) > split_limit():
        return await _upload_parts(new_file, message, msg, as_doc)
    return await _upload_one(new_file, message, msg, as_doc, caption)


async def _upload_parts(new_file, message, msg, as_doc=None):
    import shutil
    from ..encoding import split_for_upload
    try:
        await msg.edit(f"✂️ <b>File is over {split_limit() // 1048576} MB</b> – splitting it into parts…")
    except Exception:
        pass
    parts = await split_for_upload(new_file, split_limit(), key=getattr(msg, "id", None))
    if not parts:
        return None
    first = None
    name = os.path.basename(new_file)
    video_parts = all(p.lower().endswith((".mkv", ".mp4", ".webm", ".avi", ".mov")) for p in parts)
    try:
        for i, part in enumerate(parts, 1):
            try:
                await msg.edit(f"📤 <b>Uploading part {i}/{len(parts)}</b>\n<code>{name[:60]}</code>")
            except Exception:
                pass
            link = await _upload_one(part, message, msg, as_doc if video_parts else True,
                                     caption=f"{os.path.basename(part)}\n📦 Part {i} of {len(parts)} · {name}")
            if link is None:
                return first
            first = first or link
    finally:
        if parts[0] != new_file:
            shutil.rmtree(os.path.dirname(parts[0]), ignore_errors=True)
    try:
        await message.reply_text(f"📦 <b>{len(parts)} parts uploaded</b> – <code>{name[:80]}</code>\n"
                                 + ("<i>Every part plays on its own.</i>" if video_parts else
                                    "<i>Join them with 7-Zip (open .001) or <code>cat file.* &gt; file</code>.</i>"))
    except Exception:
        pass
    return first


async def _upload_one(new_file, message, msg, as_doc=None, caption=None):
    c_time = time.time()
    filename = os.path.basename(new_file)
    # ffprobe / ffmpeg are blocking – keep them off the event loop
    duration = await asyncio.to_thread(get_duration, new_file)

    custom_thumb = await db.get_thumbnail(message.from_user.id)
    thumb = None
    if custom_thumb:
        try:
            thumb = await app.download_media(custom_thumb,
                                             file_name=os.path.join(download_dir, str(time.time()) + ".jpg"))
        except Exception as e:
            LOGGER.warning(f"custom thumbnail download failed: {e}")
    if not thumb:
        thumb = await asyncio.to_thread(get_thumbnail, new_file, download_dir, (duration or 0) / 4)

    width, height = await asyncio.to_thread(get_width_height, new_file)
    if as_doc is None:
        as_doc = await db.get_upload_as_doc(message.from_user.id) is True
    try:
        if as_doc:
            link = await upload_doc(message, msg, c_time, caption or filename, new_file, thumb)
        else:
            link = await upload_video(message, msg, new_file, caption or filename, c_time, thumb, duration, width,
                                      height)
    finally:
        if thumb and os.path.isfile(thumb):
            try:
                os.remove(thumb)
            except OSError:
                pass
    return link


async def _log_copy(send, *args, **kwargs):
    """Copy to the log channel – a missing / misconfigured log chat must not fail the user's upload."""
    if not log:
        return
    try:
        await send(log, *args, **kwargs)
    except Exception as e:
        LOGGER.warning(f"encoder log copy failed: {e}")


async def upload_video(message, msg, new_file, filename, c_time, thumb, duration, width, height):
    resp = await message.reply_video(
        new_file,
        supports_streaming=True,
        parse_mode=None,
        caption=filename,
        thumb=thumb,
        duration=duration,
        width=width,
        height=height,
        progress=progress_for_pyrogram,
        progress_args=("Uploading ...", msg, c_time)
    )
    if not resp:                      # StopTransmission → cancelled
        return None
    media = resp.video or resp.document
    if media:
        await _log_copy(app.send_video if resp.video else app.send_document, media.file_id,
                        caption=filename, parse_mode=None)
    return resp.link


async def upload_doc(message, msg, c_time, filename, new_file, thumb=None):
    resp = await message.reply_document(
        new_file,
        caption=filename,
        parse_mode=None,
        thumb=thumb,
        progress=progress_for_pyrogram,
        progress_args=("Uploading ...", msg, c_time)
    )
    if not resp:
        return None
    if resp.document:
        await _log_copy(app.send_document, resp.document.file_id, caption=filename, parse_mode=None)
    return resp.link

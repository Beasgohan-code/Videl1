

import asyncio
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

async def on_task_complete():
    delete_downloads()
    if not data:
        return
    del data[0]
    if not len(data) > 0:
        return
    message = data[0]

    # Determine text content (message text or caption)
    text_content = message.text or message.caption

    if text_content:
        text = text_content.split(None, 1)
        command = text[0].lower()
        if '/ddl' in command:
            await handle_tasks(message, 'url')
        elif '/batch' in command:
            await handle_tasks(message, 'batch')
        elif '/sample' in command:
            await handle_tasks(message, 'sample')
        elif '/trim' in command:
            await handle_tasks(message, 'trim')
        elif '/screens' in command:
            await handle_tasks(message, 'screens')
        elif '/dl' in command:
            await handle_tasks(message, 'tg')
        elif '/af' in command:
            await handle_tasks(message, 'af')
        else:
             # If has text but not a known command, check if it's a file
            if message.document or message.video:
                 if message.document and not message.document.mime_type in video_mimetype:
                    await on_task_complete()
                    return
                 await handle_tasks(message, 'tg')
            else:
                 # Just text, maybe a link but without command? Or unhandled
                 pass
    else:
        # Fallback for any other file message if somehow added
        if message.document:
            if not message.document.mime_type in video_mimetype:
                await on_task_complete()
                return
        await handle_tasks(message, 'tg')


_MODE_TITLE = {'tg': "Encode", 'url': "Encode (link)", 'af': "Audio arrange", 'batch': "Batch encode",
               'sample': "Sample encode", 'trim': "Trim", 'screens': "Screenshots"}


async def handle_tasks(message, mode):
    msg = None
    try:
        from . import jobs
        from .encoding import cancel_markup
        msg = await message.reply_text(f"<b>📥 Downloading…</b>\n<i>{_MODE_TITLE.get(mode, 'Task')}</i>")
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
        await message.reply(text=f"Error! <code>{html.escape(str(e))[:300]}</code>")
    finally:
        if msg is not None:
            from . import jobs
            jobs.unregister(msg.id)
        await on_task_complete()


async def _cancelled(msg) -> bool:
    from . import jobs
    if jobs.is_cancelled(msg.id):
        try:
            await msg.edit("🚫 <b>Task cancelled.</b>")
        except Exception:
            pass
        return True
    return False


async def _download(message, msg):
    filepath = await handle_tg_down(message, msg)
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
    out = await trim(filepath, start, end, key=msg.id)
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
    shots = await screenshots(filepath, count, info["duration"], key=msg.id)
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
        filepath = await handle_download_url(message, msg, False)
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
    if message.reply_to_message:
        filepath = await handle_tg_down(message, msg, mode='reply')
    else:
        filepath = await handle_download_url(message, msg, True)
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


async def handle_download_url(message, msg, batch):
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

    path = os.path.join(download_dir, custom_file_name)
    filepath = path
    if 'drive.google.com' in url:
        await n.handle_drive(msg, url, custom_file_name, batch)
    else:
        await handle_url(url, filepath, msg)
    return filepath


async def handle_tg_down(message, msg, mode='no_reply'):
    c_time = time.time()

    # Determine what to download
    target_msg = message
    if message.reply_to_message and (message.reply_to_message.video or message.reply_to_message.document):
        target_msg = message.reply_to_message
    elif message.video or message.document:
        target_msg = message
    elif mode == 'reply' and message.reply_to_message:
        target_msg = message.reply_to_message
    else:
        # If command was just /dl without reply and without attachment, and mode is not explicit reply
        if not (message.reply_to_message and (message.reply_to_message.video or message.reply_to_message.document)):
             return None
        target_msg = message.reply_to_message

    path = await target_msg.download(
        file_name=os.path.join(download_dir, ""),
        progress=progress_for_pyrogram,
        progress_args=("Downloading...", msg, c_time))

    return path



import asyncio
import html
import os
import shutil

from pyrogram.errors.exceptions.bad_request_400 import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from pySmartDL import SmartDL

from .. import PUBLIC, all, everyone, owner, sudo_users, download_dir, encode_dir
from .database.access_db import db
from .display_progress import progress_for_url
from .encoding import encode, extract_subs
from .uploads import upload_worker

output = InlineKeyboardMarkup([
    [InlineKeyboardButton("⚙️ Settings", callback_data="OpenSettings"),
     InlineKeyboardButton("🏠 Home", callback_data="start_btn")]
])

start_but = InlineKeyboardMarkup([
    [InlineKeyboardButton("📊 Stats", callback_data="stats"), InlineKeyboardButton("⚙️ Settings", callback_data="OpenSettings")],
    [InlineKeyboardButton("🏠 Home", callback_data="start_btn")]])


def _id_set(raw) -> set:
    """DB stores auth/sudo lists as space separated strings – compare whole IDs only."""
    return {x for x in str(raw or "").replace(",", " ").split() if x}


async def check_chat(message, chat):
    ''' Authorize User! '''
    chat_id = message.chat.id
    if not message.from_user:
        return None
    user_id = message.from_user.id
    get_sudo = _id_set(await db.get_sudo())
    get_auth = _id_set(await db.get_chat())
    if user_id in owner:
        title = 'God'
    elif user_id in sudo_users or chat_id in sudo_users:
        title = 'Sudo'
    elif chat_id in everyone or user_id in everyone:
        title = 'Auth'
    elif str(user_id) in get_sudo or str(chat_id) in get_sudo:
        title = 'Sudo'
    elif str(chat_id) in get_auth or str(user_id) in get_auth:
        title = 'Auth'
    elif PUBLIC:
        title = 'Auth'
    else:
        title = None
    if title == 'God':
        return True
    if not chat == 'Owner':
        if title == 'Sudo':
            return True
        if chat == 'Both':
            if title == 'Auth':
                return True
    return None


async def handle_url(url, filepath, msg):
    # SmartDL's constructor probes the URL synchronously – keep it off the event loop
    downloader = await asyncio.to_thread(SmartDL, url, filepath, progress_bar=False, threads=10)
    await asyncio.to_thread(downloader.start, blocking=False)
    from . import jobs
    while not downloader.isFinished():
        if jobs.is_cancelled(getattr(msg, "id", None)):
            await asyncio.to_thread(downloader.stop)
            raise RuntimeError("Download cancelled")
        await progress_for_url(downloader, msg)
        await asyncio.sleep(6)              # edit at most every few seconds (FloodWait otherwise)
    if not downloader.isSuccessful():
        errors = "; ".join(str(e) for e in downloader.get_errors()[-2:]) or "unknown error"
        raise RuntimeError(f"Download failed: {errors}")


def _done_markup(link):
    rows = []
    if link and str(link).startswith(("http://", "https://")):
        rows.append([InlineKeyboardButton("📥 Open file", url=str(link))])
    rows.append([InlineKeyboardButton("⚙️ Settings", callback_data="OpenSettings"),
                 InlineKeyboardButton("🏠 Home", callback_data="start_btn")])
    return InlineKeyboardMarkup(rows)


def _remove(*paths):
    for p in paths:
        try:
            if p and os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


async def handle_encode(filepath, message, msg, audio_map=None, opts=None):
    """Encode → upload → before/after summary. Returns the uploaded file link (or None)."""
    from . import jobs
    from .encoding import LAST_ERROR, summary_text
    uid = message.from_user.id
    if await db.get_hardsub(uid):
        await _safe_edit(msg, "<b>📝 Extracting subtitles for hardsub…</b>")
        subs = await extract_subs(filepath, msg, uid)
        if not subs:
            await _safe_edit(msg, "❌ <b>Couldn't extract the subtitles.</b>\n<i>Picture subtitles (PGS/VobSub) can't be "
                                  "hard-subbed – turn Hardsub off in /settings → Extras.</i>", _done_markup(None))
            _remove(filepath)
            return None
    new_file = await encode(filepath, message, msg, audio_map=audio_map, opts=opts)
    link = None
    if new_file:
        await _safe_edit(msg, "<b>📤 Encoded – uploading…</b>")
        new_size = os.path.getsize(new_file) if os.path.isfile(new_file) else 0
        try:
            link = await upload_worker(new_file, message, msg)
        except Exception as e:
            await _safe_edit(msg, f"❌ <b>Upload failed:</b> <code>{html.escape(str(e))[:300]}</code>", _done_markup(None))
        else:
            try:
                text = summary_text(new_file, new_size, link,
                                    title="Sample ready" if getattr(new_file, "sample", None) else "Encode complete")
            except Exception as e:                       # the summary must never hide a finished upload
                text = f"✅ <b>Video encoded!</b>\n<i>{html.escape(str(e))[:100]}</i>"
            await _safe_edit(msg, text, _done_markup(link))
            if not getattr(new_file, "sample", None):
                try:
                    await db.add_encode_stat(uid, (new_file.info or {}).get("size", 0), new_size, new_file.elapsed)
                    from core.analytics import bump_later
                    bump_later("encode")
                except Exception:
                    pass
        _remove(new_file, filepath)
    elif jobs.is_cancelled(msg.id) or getattr(msg, "_videl_cancelled", False):
        _remove(filepath)
    else:
        err = LAST_ERROR.pop(msg.id, "")
        text = "❌ <b>Encoding failed.</b>"
        if err:
            text += f"\n<blockquote expandable><code>{html.escape(err)[-280:]}</code></blockquote>"
        text += "\n<i>Try another preset / codec in /settings, or /sample to test quickly.</i>"
        if not await _safe_edit(msg, text, _done_markup(None)):
            await message.reply(text)
        _remove(filepath)
    return link


async def _safe_edit(msg, text, markup=None) -> bool:
    try:
        await msg.edit(text, reply_markup=markup, disable_web_page_preview=True)
        return True
    except MessageNotModified:
        return True
    except Exception:
        return False


async def handle_extract(archieve):
    # get current directory
    path = os.getcwd()
    archieve = os.path.join(path, archieve)
    cmd = [f'./extract', archieve]
    rio = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await rio.communicate()
    os.remove(archieve)
    return path


async def get_zip_folder(orig_path: str):
    if orig_path.endswith(".tar.bz2"):
        return orig_path.rsplit(".tar.bz2", 1)[0]
    elif orig_path.endswith(".tar.gz"):
        return orig_path.rsplit(".tar.gz", 1)[0]
    elif orig_path.endswith(".bz2"):
        return orig_path.rsplit(".bz2", 1)[0]
    elif orig_path.endswith(".gz"):
        return orig_path.rsplit(".gz", 1)[0]
    elif orig_path.endswith(".tar.xz"):
        return orig_path.rsplit(".tar.xz", 1)[0]
    elif orig_path.endswith(".tar"):
        return orig_path.rsplit(".tar", 1)[0]
    elif orig_path.endswith(".tbz2"):
        return orig_path.rsplit("tbz2", 1)[0]
    elif orig_path.endswith(".tgz"):
        return orig_path.rsplit(".tgz", 1)[0]
    elif orig_path.endswith(".zip"):
        return orig_path.rsplit(".zip", 1)[0]
    elif orig_path.endswith(".7z"):
        return orig_path.rsplit(".7z", 1)[0]
    elif orig_path.endswith(".Z"):
        return orig_path.rsplit(".Z", 1)[0]
    elif orig_path.endswith(".rar"):
        return orig_path.rsplit(".rar", 1)[0]
    elif orig_path.endswith(".iso"):
        return orig_path.rsplit(".iso", 1)[0]
    elif orig_path.endswith(".wim"):
        return orig_path.rsplit(".wim", 1)[0]
    elif orig_path.endswith(".cab"):
        return orig_path.rsplit(".cab", 1)[0]
    elif orig_path.endswith(".apm"):
        return orig_path.rsplit(".apm", 1)[0]
    elif orig_path.endswith(".arj"):
        return orig_path.rsplit(".arj", 1)[0]
    elif orig_path.endswith(".chm"):
        return orig_path.rsplit(".chm", 1)[0]
    elif orig_path.endswith(".cpio"):
        return orig_path.rsplit(".cpio", 1)[0]
    elif orig_path.endswith(".cramfs"):
        return orig_path.rsplit(".cramfs", 1)[0]
    elif orig_path.endswith(".deb"):
        return orig_path.rsplit(".deb", 1)[0]
    elif orig_path.endswith(".dmg"):
        return orig_path.rsplit(".dmg", 1)[0]
    elif orig_path.endswith(".fat"):
        return orig_path.rsplit(".fat", 1)[0]
    elif orig_path.endswith(".hfs"):
        return orig_path.rsplit(".hfs", 1)[0]
    elif orig_path.endswith(".lzh"):
        return orig_path.rsplit(".lzh", 1)[0]
    elif orig_path.endswith(".lzma"):
        return orig_path.rsplit(".lzma", 1)[0]
    elif orig_path.endswith(".lzma2"):
        return orig_path.rsplit(".lzma2", 1)[0]
    elif orig_path.endswith(".mbr"):
        return orig_path.rsplit(".mbr", 1)[0]
    elif orig_path.endswith(".msi"):
        return orig_path.rsplit(".msi", 1)[0]
    elif orig_path.endswith(".mslz"):
        return orig_path.rsplit(".mslz", 1)[0]
    elif orig_path.endswith(".nsis"):
        return orig_path.rsplit(".nsis", 1)[0]
    elif orig_path.endswith(".ntfs"):
        return orig_path.rsplit(".ntfs", 1)[0]
    elif orig_path.endswith(".rpm"):
        return orig_path.rsplit(".rpm", 1)[0]
    elif orig_path.endswith(".squashfs"):
        return orig_path.rsplit(".squashfs", 1)[0]
    elif orig_path.endswith(".udf"):
        return orig_path.rsplit(".udf", 1)[0]
    elif orig_path.endswith(".vhd"):
        return orig_path.rsplit(".vhd", 1)[0]
    elif orig_path.endswith(".xar"):
        return orig_path.rsplit(".xar", 1)[0]
    else:
        raise IndexError("File format not supported for extraction!")


def delete_downloads():
    dir = encode_dir
    dir2 = download_dir
    for files in os.listdir(dir):
        path = os.path.join(dir, files)
        try:
            shutil.rmtree(path)
        except OSError:
            try:
                os.remove(path)
            except PermissionError:
                pass
    for files in os.listdir(dir2):
        path = os.path.join(dir2, files)
        try:
            shutil.rmtree(path)
        except OSError:
            try:
                os.remove(path)
            except PermissionError:
                pass

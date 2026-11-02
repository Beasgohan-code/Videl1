import asyncio
import html
import os
import time
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.errors.exceptions.bad_request_400 import MessageIdInvalid

from .. import download_dir, encode_dir
from ..utils.encoding import get_duration, get_thumbnail, get_width_height
from ..utils.helper import check_chat
from ..utils.uploads.drive import DriveNotConfiguredError
from ..utils.uploads.drive.upload import Uploader
from ..utils.uploads.telegram import upload_doc, upload_video

_FORBIDDEN_SUFFIXES = (".env", ".session", ".session-journal", ".key", ".pem", ".pickle")
_FORBIDDEN_NAMES = frozenset({"config.env", "token.json", "credentials.json", "rclone.conf"})
_FORBIDDEN_SYSTEM_PREFIXES = ("/etc/", "/proc/", "/sys/", "/root/.ssh/")


def _is_forbidden_upload_target(file_path: str) -> bool:
    resolved = str(Path(file_path).resolve()).replace("\\", "/")
    lower_name = os.path.basename(resolved).lower()
    if lower_name in _FORBIDDEN_NAMES or lower_name.endswith(_FORBIDDEN_SUFFIXES):
        return True
    if any(resolved.startswith(p) for p in _FORBIDDEN_SYSTEM_PREFIXES):
        return True
    return False


@Client.on_message(filters.command("dupload"))
async def docupload(client, message):
    c = await check_chat(message, chat="Sudo")
    if not c:
        return
    file = os.path.expanduser(" ".join(message.command[1:]).strip())
    if not file:
        await message.reply("you forgot to mention path!")
        return
    if not os.path.isfile(file):
        await message.reply("File not found.")
        return
    if _is_forbidden_upload_target(file):
        await message.reply("⚠️ Uploading credential or system files is blocked.")
        return
    c_time = time.time()
    filename = os.path.basename(file)
    reply = await message.reply(f"📤 Uploading <code>{html.escape(filename)}</code>…")
    try:
        await upload_doc(message, reply, c_time, filename, file)
    except Exception as e:
        await reply.edit(f"⚠️ Error while uploading: <code>{html.escape(str(e))[:250]}</code>")
    else:
        await reply.delete()


@Client.on_message(filters.command("vupload"))
async def videoupload(client, message):
    c = await check_chat(message, chat="Sudo")
    if c is None:
        return
    c_time = time.time()
    file = os.path.expanduser(" ".join(message.command[1:]).strip())
    if not file:
        await message.reply_text("you forgot to mention filepath!")
        return
    filename = os.path.basename(file)
    if not os.path.isfile(file):
        await message.reply_text("File not found.")
        return
    if _is_forbidden_upload_target(file):
        await message.reply_text("⚠️ Uploading credential or system files is blocked.")
        return
    # ffprobe / ffmpeg block – keep them off the event loop
    duration, (width, height) = await asyncio.gather(
        asyncio.to_thread(get_duration, file),
        asyncio.to_thread(get_width_height, file),
    )
    thumb = await asyncio.to_thread(get_thumbnail, file, download_dir, (duration or 0) / 4)
    reply = await message.reply_text(f"📤 Uploading <code>{html.escape(filename)}</code>…")
    try:
        await upload_video(message, reply, file, filename, c_time, thumb, duration, width, height)
    except Exception as e:
        await reply.edit(f"⚠️ Error while uploading: <code>{html.escape(str(e))[:250]}</code>")
    else:
        await reply.delete()
    finally:
        if thumb and os.path.isfile(thumb):
            try:
                os.remove(thumb)
            except OSError:
                pass


@Client.on_message(filters.command("gupload"))
async def driveupload(client, message):
    c = await check_chat(message, chat="Sudo")
    if c is None:
        return
    args = list(message.command[1:])
    backend = "gdrive"
    rclone_remote = None
    if args and args[0].lower() in ("gdrive", "drive", "rclone", "gofile"):
        first = args.pop(0).lower()
        backend = "gdrive" if first == "drive" else first
        if backend == "rclone" and len(args) >= 2 and ":" in args[0] and not os.path.isfile(os.path.expanduser(args[0])):
            rclone_remote = args.pop(0)
    new_file = os.path.expanduser(" ".join(args).strip())
    if not new_file:
        await message.reply_text("you forgot to mention path!")
        return
    if not os.path.isfile(new_file):
        await message.reply_text("File not found.")
        return
    if _is_forbidden_upload_target(new_file):
        await message.reply_text("⚠️ Uploading credential or system files is blocked.")
        return
    reply = await message.reply_text(
        f"☁️ Uploading <code>{html.escape(os.path.basename(new_file))}</code> ({html.escape(backend)})…"
    )
    try:
        if backend == "rclone":
            from ..utils.uploads.rclone import RcloneNotConfiguredError, upload_with_rclone
            res = await upload_with_rclone(new_file, message, reply, remote_spec=rclone_remote)
        elif backend == "gofile":
            from ..utils.uploads.gofile import upload_to_gofile
            res = await upload_to_gofile(new_file, message, reply)
        else:
            u = Uploader()
            res = await u.upload_to_drive(new_file, message, reply)
    except DriveNotConfiguredError as e:
        await reply.edit(f"ℹ️ <code>{html.escape(str(e))}</code>")
    except Exception as e:
        await reply.edit(f"⚠️ Error while uploading: <code>{html.escape(str(e))[:250]}</code>")
    else:
        if res:
            try:
                await reply.delete()
            except Exception:
                pass


@Client.on_message(filters.command("logs"))
async def logsup(client, message):
    c = await check_chat(message, chat="Sudo")
    if c is None:
        return
    file = "logs/videl.log"
    if not os.path.isfile(file):
        await message.reply("ℹ️ Log file not created yet.")
        return
    try:
        await message.reply_document(file, caption="#Logs")
    except MessageIdInvalid:
        await message.reply("Upload cancelled!")
    except Exception as e:
        await message.reply(f"⚠️ <code>{html.escape(str(e))[:250]}</code>")

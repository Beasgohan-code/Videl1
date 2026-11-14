"""
Resilient, cancellable Google Drive uploader with bounded chunk retry backoff,
safe event-loop polling, HTML-escaped output links, and live job telemetry.
"""
from __future__ import annotations

import asyncio
import html
import logging
import math
import os
import time
from mimetypes import MimeTypes
from urllib.parse import quote

import config
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from pyrogram.errors import FloodWait, MessageIdInvalid, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .... import app, drive_dir, index, log as log_channel
from ... import jobs
from . import TRANSIENT_HTTP_CODES, DriveAPI
from .download import TimeFormatter, humanbytes, submit_thread

logger = logging.getLogger("videl.drive.upload")
MAX_CHUNK_RETRIES = 5
CHUNK_SIZE = 50 * 1024 * 1024  # 50 MiB


def _sleep_interruptible(seconds: float, check_cancel) -> bool:
    """Sleep in short slices inside a worker thread; return True if cancelled."""
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        if check_cancel():
            return True
        time.sleep(min(0.25, max(0.01, end - time.monotonic())))
    return check_cancel()


class Uploader(DriveAPI):
    def __init__(self, *, service=None):
        super().__init__(service=service)
        self._output: str | None = None
        self._error: str | None = None
        self._cancelled: bool = False
        self._progress: str | None = None
        self._is_finished: bool = False

    def finish(self) -> None:
        self._is_finished = True

    def cancel(self) -> None:
        self._cancelled = True

    def _check_cancelled(self, job_key=None) -> bool:
        if self._cancelled:
            return True
        if job_key is not None and jobs.is_cancelled(job_key):
            self._cancelled = True
            return True
        return False

    def uploadFile(self, filePath: str, parentId: str | None = None, job_key=None) -> None:
        media = None
        try:
            if not os.path.isfile(filePath):
                raise FileNotFoundError(f"Upload file missing: {os.path.basename(filePath)}")
            file_name = os.path.basename(filePath)
            mimetype = MimeTypes().guess_type(file_name)[0] or "application/octet-stream"
            file_metadata: dict = {"name": file_name}
            target_parent = (parentId or "").strip()
            if target_parent:
                file_metadata["parents"] = [target_parent]

            total_disk_size = os.path.getsize(filePath)
            if total_disk_size == 0:
                media = MediaFileUpload(filePath, mimetype=mimetype, resumable=False)
                response = (
                    self.service.files()
                    .create(body=file_metadata, media_body=media, supportsAllDrives=True, fields="id, name")
                    .execute()
                )
                file_id = (response or {}).get("id")
                if not file_id:
                    raise RuntimeError("Google Drive API returned no file id.")
                self._output = self.get_drive_url(filePath, file_id)
                return

            media = MediaFileUpload(filePath, mimetype=mimetype, chunksize=CHUNK_SIZE, resumable=True)
            req = self.service.files().create(
                body=file_metadata, media_body=media, supportsAllDrives=True, fields="id, name"
            )
            c_time = time.monotonic()
            response = None
            retries = 0
            while response is None:
                if self._check_cancelled(job_key):
                    logger.info("Drive upload cancelled for %s", file_name)
                    return
                try:
                    status, response = req.next_chunk(num_retries=2)
                    retries = 0
                except HttpError as err:
                    code = int(getattr(getattr(err, "resp", None), "status", 0) or 0)
                    if code in TRANSIENT_HTTP_CODES and retries < MAX_CHUNK_RETRIES:
                        retries += 1
                        backoff = min(2 ** retries, 16)
                        logger.warning(
                            "Transient Drive upload HTTP %s (%d/%d) – retrying in %ss",
                            code, retries, MAX_CHUNK_RETRIES, backoff,
                        )
                        if _sleep_interruptible(backoff, lambda: self._check_cancelled(job_key)):
                            return
                        continue
                    raise
                except (TimeoutError, OSError) as err:
                    if retries < MAX_CHUNK_RETRIES:
                        retries += 1
                        backoff = min(2 ** retries, 16)
                        logger.warning(
                            "Transient Drive upload network error (%d/%d: %s) – retrying in %ss",
                            retries, MAX_CHUNK_RETRIES, err, backoff,
                        )
                        if _sleep_interruptible(backoff, lambda: self._check_cancelled(job_key)):
                            return
                        continue
                    raise

                if status:
                    f_size = max(int(status.total_size or total_disk_size or 1), 1)
                    uploaded = max(0, int(status.resumable_progress or 0))
                    diff = max(time.monotonic() - c_time, 1e-3)
                    percentage = min(100.0, max(0.0, uploaded * 100.0 / f_size))
                    speed = uploaded / diff
                    eta = round((f_size - uploaded) / speed) if speed > 0 else 0
                    filled = max(0, min(10, math.floor(percentage / 10)))
                    bar = "█" * filled + "░" * (10 - filled)
                    self._progress = (
                        f"<b>☁️ Uploading to Google Drive: {percentage:.1f}%</b>\n"
                        f"<code>[{bar}]</code>\n"
                        f" • Speed: {humanbytes(speed) or '0 B'}/s\n"
                        f" • Size: {humanbytes(uploaded) or '0 B'} / {humanbytes(f_size) or '0 B'}\n"
                        f" • ETA: {TimeFormatter(eta)}"
                    )
                    if job_key is not None:
                        jobs.update_progress(
                            job_key,
                            current=uploaded,
                            total=f_size,
                            pct=percentage,
                            speed=speed,
                            eta=eta,
                            stage="☁️ Drive Upload",
                            engine="Google Drive",
                            name=file_name,
                        )

            if self._check_cancelled(job_key):
                return
            file_id = (response or {}).get("id")
            if not file_id:
                raise RuntimeError("Google Drive API completed without returning a file id.")
            if job_key is not None:
                jobs.update_progress(
                    job_key,
                    current=total_disk_size,
                    total=total_disk_size,
                    pct=100.0,
                    eta=0,
                    stage="☁️ Drive Upload",
                    engine="Google Drive",
                )
            self._output = self.get_drive_url(filePath, file_id)
        except Exception as e:
            self._error = f"{type(e).__name__}: {e}"
            logger.error("Drive upload failed: %s", self._error)
        finally:
            try:
                stream = media.stream() if media is not None and hasattr(media, "stream") else None
                if stream is not None and hasattr(stream, "close"):
                    stream.close()
            except Exception:
                pass
            self.finish()

    def get_drive_url(self, filePath: str, file_id: str, *, index_base: str | None = None) -> str:
        filename = os.path.basename(filePath)
        safe_name = html.escape(filename)
        safe_id = quote(str(file_id), safe="-_")
        drive_url = f"https://drive.google.com/file/d/{safe_id}/view?usp=drivesdk"
        links = [f'<a href="{drive_url}">☁️ Drive</a>']

        idx = (index if index_base is None else index_base) or getattr(config, "INDEX_URL", "")
        idx = str(idx or "").strip().rstrip("/")
        if idx:
            encoded_name = quote(filename, safe="")
            index_url = f"{idx}/{encoded_name}"
            view_url = f"{index_url}?a=view"
            links.append(f'<a href="{html.escape(index_url, quote=True)}">⚡ Index</a>')
            links.append(f'<a href="{html.escape(view_url, quote=True)}">▶️ View</a>')

        return f"<code>{safe_name}</code>\n\n" + " | ".join(links)

    async def upload_to_drive(self, new_file: str, message, msg, *, poll_interval: float = 4.0) -> str | None:
        job_key = getattr(msg, "id", None)
        uid = getattr(getattr(message, "from_user", None), "id", 0)
        chat_id = getattr(getattr(message, "chat", None), "id", 0)
        owned_job = job_key is not None and jobs.get(job_key) is None
        if job_key is not None:
            jobs.register(
                job_key,
                uid,
                chat_id,
                name=os.path.basename(new_file),
                stage="☁️ Drive Upload",
                engine="Google Drive",
            )
        markup = (
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"enc_cancel:{job_key}")]])
            if job_key is not None
            else None
        )
        try:
            await msg.edit_text("<code>☁️ Preparing Google Drive upload…</code>", reply_markup=markup)
        except Exception:
            pass

        fut = submit_thread(self.uploadFile, new_file, drive_dir or getattr(config, "DRIVE_DIR", ""), job_key)
        last_text = None
        next_edit = 0.0
        step = min(0.25, max(0.02, poll_interval / 8.0))
        try:
            while not self._is_finished:
                if self._check_cancelled(job_key):
                    break
                now = time.monotonic()
                if self._progress and self._progress != last_text and now >= next_edit:
                    try:
                        await msg.edit(text=self._progress, reply_markup=markup)
                        last_text = self._progress
                        next_edit = time.monotonic() + poll_interval
                    except FloodWait as fw:
                        next_edit = time.monotonic() + int(getattr(fw, "value", 5) or 5) + 1
                    except MessageNotModified:
                        last_text = self._progress
                        next_edit = time.monotonic() + poll_interval
                    except MessageIdInvalid:
                        next_edit = time.monotonic() + poll_interval * 2
                    except Exception:
                        next_edit = time.monotonic() + poll_interval
                await asyncio.sleep(step)
            await asyncio.wrap_future(fut)
        finally:
            if owned_job and job_key is not None:
                jobs.unregister(job_key)

        if self._cancelled:
            try:
                await msg.edit_text("✖️ <b>Google Drive upload cancelled.</b>")
            except Exception:
                pass
            return None

        if not self._output:
            reason = html.escape(self._error or "Unknown Google Drive error")[:300]
            try:
                await msg.edit_text(f"⚠️ <b>Google Drive upload failed:</b>\n<code>{reason}</code>")
            except Exception:
                pass
            return None

        client = getattr(message, "_client", None) or app
        ms = await client.send_message(chat_id=message.chat.id, text=self._output, disable_web_page_preview=True)
        target_log = log_channel or getattr(config, "LOG_CHANNEL", 0)
        if target_log:
            try:
                await client.send_message(chat_id=target_log, text=self._output, disable_web_page_preview=True)
            except Exception as exc:
                logger.debug("Drive log-channel forward skipped: %s", exc)
        return getattr(ms, "link", None) or "Done"

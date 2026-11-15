"""
Resilient, cancellable Google Drive downloader with path-traversal protection,
bounded chunk retry backoff, non-blocking metadata fetches, and partial-file cleanup.
"""
from __future__ import annotations

import asyncio
import html
import io
import logging
import math
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from signal import SIG_DFL, signal
from typing import Any, Callable

try:
    from signal import SIGPIPE
except ImportError:
    SIGPIPE = None

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload
from pyrogram.errors import FloodWait, MessageIdInvalid, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core.sysinfo import effective_cpu_count
from .... import download_dir as _default_download_dir
from ... import jobs
from ...display_progress import TimeFormatter, humanbytes
from . import G_DRIVE_DIR_MIME_TYPE, TRANSIENT_HTTP_CODES, DriveAPI, _get_file_id

if SIGPIPE:
    signal(SIGPIPE, SIG_DFL)

logger = logging.getLogger("videl.drive.download")
MAX_CHUNK_RETRIES = 5
CHUNK_SIZE = 50 * 1024 * 1024  # 50 MiB


def _safe_filename(name: str, fallback: str = "drive_file") -> str:
    """Sanitize a remote Google Drive file/folder name against path traversal and illegal chars."""
    cleaned = os.path.basename((name or "").replace("\\", "/")).strip().lstrip(".")
    cleaned = "".join(ch for ch in cleaned if ch not in '<>:"|?*\x00' and ord(ch) >= 32)[:200].strip()
    return cleaned or fallback


def _sleep_interruptible(seconds: float, check_cancel) -> bool:
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        if check_cancel():
            return True
        time.sleep(min(0.25, max(0.01, end - time.monotonic())))
    return check_cancel()


class Downloader(DriveAPI):
    def __init__(self, *, service=None):
        super().__init__(service=service)
        self.DEFAULT_STORAGE_PATH = "drive_content"
        self._name = ""
        self._completed = 0
        self._list = 1
        self._output: Any = None
        self._cancelled = False
        self._progress: str | None = None
        self._is_finished = False

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

    def name(self, file_id: str) -> str:
        safe_id = _get_file_id(file_id) or file_id
        drive_file = (
            self.service.files()
            .get(fileId=safe_id, supportsAllDrives=True, fields="id, name, mimeType, size")
            .execute()
        )
        return _safe_filename(drive_file.get("name", ""))

    def _create_server_dir(self, current_path: str, folder_name: str) -> str:
        safe_folder = _safe_filename(folder_name, fallback="drive_folder")
        base_abs = os.path.abspath(current_path)
        path = os.path.abspath(os.path.join(base_abs, safe_folder))
        if os.path.commonpath([base_abs, path]) != base_abs:
            raise ValueError("Unsafe folder name rejected")
        os.makedirs(path, exist_ok=True)
        return path

    def _list_drive_dir(self, file_id: str) -> list[dict]:
        safe_id = _get_file_id(file_id)
        if not safe_id:
            raise ValueError("Invalid Google Drive folder ID")
        query = f"'{safe_id}' in parents and trashed = false"
        fields = "nextPageToken, files(id, name, mimeType, size)"
        page_token = None
        files: list[dict] = []
        while True:
            response = (
                self.service.files()
                .list(
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    q=query,
                    spaces="drive",
                    fields=fields,
                    pageToken=page_token,
                    pageSize=100,
                    corpora="allDrives",
                    orderBy="folder, name",
                )
                .execute()
            )
            files.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if page_token is None:
                break
        return files

    def download(self, file_id: str, custom_name: str = "", job_key=None, dest_dir: str | None = None) -> None:
        base_dir = dest_dir or _default_download_dir
        os.makedirs(base_dir, exist_ok=True)
        try:
            drive_file = (
                self.service.files()
                .get(fileId=file_id, fields="id, name, mimeType, size", supportsAllDrives=True)
                .execute()
            )
            if drive_file.get("mimeType") == G_DRIVE_DIR_MIME_TYPE:
                folder_target = custom_name or drive_file.get("name", "drive_folder")
                path = self._create_server_dir(base_dir, folder_target)
                self.downloadFolder(path, job_key=job_key, **drive_file)
            else:
                target_name = _safe_filename(custom_name or drive_file.get("name", ""))
                file_meta = {**drive_file, "name": target_name}
                self.downloadFile(base_dir, job_key=job_key, **file_meta)
        except Exception as e:
            self._output = e if isinstance(e, HttpError) else f"Download failed: {e}"
            logger.error("Drive download failed: %s", e)
        finally:
            self.finish()

    def downloadFolder(self, path: str, job_key=None, **kwargs) -> None:
        files = self._list_drive_dir(kwargs["id"])
        if not files:
            return
        self._list += len(files)
        for item in files:
            if self._check_cancelled(job_key):
                return
            if item.get("mimeType") == G_DRIVE_DIR_MIME_TYPE:
                sub_path = self._create_server_dir(path, item.get("name", "folder"))
                self.downloadFolder(sub_path, job_key=job_key, **item)
            else:
                self.downloadFile(path, job_key=job_key, **item)

    def downloadFile(self, path: str, name: str, job_key=None, **kwargs) -> None:
        safe_name = _safe_filename(name)
        base_abs = os.path.abspath(path)
        dest_file = os.path.abspath(os.path.join(base_abs, safe_name))
        if os.path.commonpath([base_abs, dest_file]) != base_abs:
            raise ValueError("Unsafe destination path rejected")

        request = self.service.files().get_media(fileId=kwargs["id"], supportsAllDrives=True)
        fh = io.FileIO(dest_file, mode="wb")
        succeeded = False
        try:
            downloader = MediaIoBaseDownload(fh, request, chunksize=CHUNK_SIZE)
            c_time = time.monotonic()
            done = False
            retries = 0
            known_size = int(kwargs.get("size") or 0)
            while not done:
                if self._check_cancelled(job_key):
                    logger.info("Drive download cancelled for %s", safe_name)
                    return
                try:
                    status, done = downloader.next_chunk(num_retries=2)
                    retries = 0
                except HttpError as err:
                    code = int(getattr(getattr(err, "resp", None), "status", 0) or 0)
                    if code in TRANSIENT_HTTP_CODES and retries < MAX_CHUNK_RETRIES:
                        retries += 1
                        backoff = min(2 ** retries, 16)
                        logger.warning(
                            "Transient Drive download HTTP %s (%d/%d) – retrying in %ss",
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
                            "Transient Drive download network error (%d/%d: %s) – retrying in %ss",
                            retries, MAX_CHUNK_RETRIES, err, backoff,
                        )
                        if _sleep_interruptible(backoff, lambda: self._check_cancelled(job_key)):
                            return
                        continue
                    raise

                if status:
                    downloaded = max(0, int(status.resumable_progress or 0))
                    f_size = max(int(status.total_size or known_size or 1), 1)
                    diff = max(time.monotonic() - c_time, 1e-3)
                    pct = min(100.0, max(0.0, downloaded * 100.0 / f_size))
                    speed = downloaded / diff
                    eta = round((f_size - downloaded) / speed) if speed > 0 else 0
                    filled = max(0, min(10, math.floor(pct / 10)))
                    bar = "█" * filled + "░" * (10 - filled)
                    self._progress = (
                        f"<b>⬇️ Downloading:</b> <code>{html.escape(self._name or safe_name)}</code>\n"
                        f"<code>[{bar}]</code> <b>{pct:.1f}%</b>\n"
                        f" • Completed: {self._completed}/{self._list} • ETA: {TimeFormatter(eta)}\n"
                        f" • Speed: {humanbytes(speed) or '0 B'}/s • Size: {humanbytes(f_size) or '0 B'}"
                    )
                    if job_key is not None:
                        jobs.update_progress(
                            job_key,
                            current=downloaded,
                            total=f_size,
                            pct=pct,
                            speed=speed,
                            eta=eta,
                            stage="⬇️ Drive Download",
                            engine="Google Drive",
                            name=safe_name,
                        )
            succeeded = not self._check_cancelled(job_key)
            if succeeded:
                self._completed += 1
        finally:
            try:
                fh.close()
            except Exception:
                pass
            if not succeeded and os.path.isfile(dest_file):
                try:
                    os.remove(dest_file)
                except OSError:
                    pass

    async def handle_drive(
        self,
        msg,
        url: str,
        custom_file_name: str,
        batch: bool,
        *,
        poll_interval: float = 4.0,
    ) -> str | None:
        file_id = _get_file_id(url)
        if not file_id:
            await msg.edit_text("⚠️ <b>Invalid Google Drive URL.</b>")
            return None

        drive_file = await asyncio.to_thread(
            lambda: self.service.files()
            .get(fileId=file_id, fields="id, name, mimeType, size", supportsAllDrives=True)
            .execute()
        )
        if drive_file.get("mimeType") == G_DRIVE_DIR_MIME_TYPE and not batch:
            await msg.edit_text("⚠️ Folder detected – use <code>/batch</code> instead.")
            raise IndexError("Folder requires /batch")

        self._name = _safe_filename(custom_file_name or drive_file.get("name", ""))
        job_key = getattr(msg, "id", None)
        chat_id = getattr(getattr(msg, "chat", None), "id", 0)
        owned_job = job_key is not None and jobs.get(job_key) is None
        if job_key is not None:
            jobs.register(
                job_key,
                0,
                chat_id,
                name=self._name,
                stage="⬇️ Drive Download",
                engine="Google Drive",
            )
        markup = (
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"enc_cancel:{job_key}")]])
            if job_key is not None
            else None
        )

        fut = submit_thread(self.download, file_id, self._name, job_key)
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
                await msg.edit_text("✖️ <b>Google Drive download cancelled.</b>")
            except Exception:
                pass
            return None

        if isinstance(self._output, HttpError):
            reason = html.escape(str(self._output._get_reason()))[:300]
            await msg.edit_text(f"⚠️ <b>Google Drive error:</b> <code>{reason}</code>")
            return None
        if self._output is not None:
            reason = html.escape(str(self._output))[:300]
            await msg.edit_text(f"⚠️ <b>{reason}</b>")
            return None
        return "Done"


_EXECUTOR = ThreadPoolExecutor(max_workers=min(8, max(2, effective_cpu_count() + 2)))


def submit_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Future:
    """Submit a blocking Google API transfer callable to the bounded thread pool."""
    return _EXECUTOR.submit(func, *args, **kwargs)

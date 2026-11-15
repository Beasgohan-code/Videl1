"""
Optional GoFile direct cloud upload backend (`VideoEncoder/utils/uploads/gofile.py`).

Features:
  • Streams files in 512 KiB chunks via `aiohttp.MultipartWriter` / async generator so multi-GB
    videos never buffer into container RAM.
  • Uses explicit `aiohttp.ClientTimeout` on every session (never unbounded HTTP requests).
  • Honors `jobs.is_cancelled(key)` and `await jobs.wait_if_paused(key)` on every chunk, feeding
    live speed/ETA/progress into `/status` and `/admin`.
  • Supports optional `GOFILE_TOKEN` + `GOFILE_FOLDER_ID` or zero-config guest upload server selection,
    with bounded exponential backoff on transient HTTP / network errors and zero token leakage.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from typing import AsyncIterator

import aiohttp

import config
from .. import jobs
from ..display_progress import humanbytes

log = logging.getLogger("videl.gofile")

GOFILE_API_BASE = "https://api.gofile.io"
CHUNK_SIZE = 512 * 1024
TRANSIENT_HTTP = frozenset({408, 429, 500, 502, 503, 504})


class GoFileUploadError(RuntimeError):
    """Raised on non-recoverable GoFile API failure."""


async def _pick_gofile_server(session: aiohttp.ClientSession) -> str:
    try:
        async with session.get(f"{GOFILE_API_BASE}/servers") as resp:
            if resp.status == 200:
                payload = await resp.json(content_type=None)
                servers = ((payload or {}).get("data") or {}).get("servers") or []
                if servers and isinstance(servers[0], dict) and servers[0].get("name"):
                    return str(servers[0]["name"])
    except Exception:
        pass
    return "upload"


async def _stream_file_chunks(
    file_path: str,
    total_bytes: int,
    *,
    job_key,
    status_msg,
    filename: str,
) -> AsyncIterator[bytes]:
    sent = 0
    started = time.time()
    last_edit = 0.0
    fh = await asyncio.to_thread(open, file_path, "rb")
    try:
        while True:
            if job_key is not None:
                if jobs.is_cancelled(job_key):
                    raise asyncio.CancelledError("GoFile upload cancelled")
                if jobs.is_paused(job_key):
                    ok = await jobs.wait_if_paused(job_key)
                    if not ok:
                        raise asyncio.CancelledError("GoFile upload cancelled while paused")
            chunk = await asyncio.to_thread(fh.read, CHUNK_SIZE)
            if not chunk:
                break
            sent += len(chunk)
            elapsed = max(0.001, time.time() - started)
            speed = sent / elapsed
            pct = min(100.0, (sent * 100.0 / total_bytes)) if total_bytes > 0 else 100.0
            eta = max(0.0, (total_bytes - sent) / speed) if speed > 0 and total_bytes > sent else 0.0
            if job_key is not None:
                jobs.update_progress(
                    job_key,
                    current=sent,
                    total=total_bytes,
                    pct=pct,
                    speed=speed,
                    eta=eta,
                    stage="Upload · GoFile",
                    engine="GoFile",
                )
            now = time.time()
            if status_msg is not None and now - last_edit >= 4.0:
                last_edit = now
                bar_fill = int(round(pct / 5.0))
                bar_txt = "█" * bar_fill + "░" * (20 - bar_fill)
                try:
                    await status_msg.edit_text(
                        f"🌐 <b>Uploading to GoFile…</b>\n"
                        f"📄 <code>{html.escape(filename)[:60]}</code>\n"
                        f"<code>[{bar_txt}]</code> <b>{pct:.1f}%</b>\n\n"
                        f"💾 {humanbytes(sent) or '0 B'} / {humanbytes(total_bytes) or '0 B'}\n"
                        f"⚡ {humanbytes(speed) or '0 B'}/s · ⏳ {int(eta)}s"
                    )
                except Exception:
                    pass
            yield chunk
    finally:
        await asyncio.to_thread(fh.close)


async def upload_to_gofile(
    file_path: str,
    message,
    status_msg,
    *,
    token: str | None = None,
    folder_id: str | None = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    http_timeout: float = 3600.0,
) -> str | None:
    """Stream `file_path` to GoFile with bounded retries, pause/cancel support, and secret-free output."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    filename = os.path.basename(file_path)
    total_bytes = os.path.getsize(file_path)
    api_token = (token if token is not None else getattr(config, "GOFILE_TOKEN", "")).strip()
    dest_folder = (folder_id if folder_id is not None else getattr(config, "GOFILE_FOLDER_ID", "")).strip()

    job_key = getattr(status_msg, "id", None)
    uid = getattr(getattr(message, "from_user", None), "id", 0)
    chat_id = getattr(getattr(message, "chat", None), "id", 0)
    own_job = False
    if job_key is not None and jobs.get(job_key) is None:
        jobs.register(job_key, uid, chat_id, name=filename, stage="Upload · GoFile", engine="GoFile")
        own_job = True
    elif job_key is not None:
        jobs.update_progress(job_key, name=filename, total=total_bytes, stage="Upload · GoFile", engine="GoFile")

    headers = {"Authorization": f"Bearer {api_token}"} if api_token else {}
    timeout = aiohttp.ClientTimeout(total=http_timeout, connect=30, sock_read=120)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for attempt in range(1, max_retries + 1):
                if job_key is not None and jobs.is_cancelled(job_key):
                    return None
                server = await _pick_gofile_server(session)
                upload_url = f"https://{server}.gofile.io/contents/uploadfile"
                form = aiohttp.FormData()
                if dest_folder and api_token:
                    form.add_field("folderId", dest_folder)
                form.add_field(
                    "file",
                    _stream_file_chunks(
                        file_path,
                        total_bytes,
                        job_key=job_key,
                        status_msg=status_msg,
                        filename=filename,
                    ),
                    filename=filename,
                    content_type="application/octet-stream",
                )
                try:
                    async with session.post(upload_url, data=form, headers=headers) as resp:
                        if resp.status in TRANSIENT_HTTP and attempt < max_retries:
                            await asyncio.sleep(min(20.0, float(retry_delay * (2 ** (attempt - 1)))))
                            continue
                        payload = await resp.json(content_type=None)
                        if resp.status != 200 or (payload or {}).get("status") != "ok":
                            reason = str((payload or {}).get("status") or f"HTTP {resp.status}")[:160]
                            raise GoFileUploadError(f"GoFile rejected upload ({reason})")
                        data = (payload or {}).get("data") or {}
                        dl_page = str(data.get("downloadPage") or "").strip()
                        if not dl_page:
                            raise GoFileUploadError("GoFile response did not include downloadPage")

                        safe_name = html.escape(filename)
                        safe_url = html.escape(dl_page, quote=True)
                        summary = (
                            f"✅ <b>Uploaded to GoFile</b>\n"
                            f"📄 <code>{safe_name}</code> ({humanbytes(total_bytes) or '0 B'})\n"
                            f"🔗 <a href='{safe_url}'>🌐 Download Page</a>"
                        )
                        client_obj = getattr(message, "_client", None)
                        if client_obj is not None:
                            await client_obj.send_message(
                                chat_id=chat_id,
                                text=summary,
                                reply_to_message_id=getattr(message, "id", None),
                            )
                        else:
                            await message.reply_text(summary)
                        return dl_page
                except asyncio.CancelledError:
                    if status_msg is not None:
                        try:
                            await status_msg.edit_text("🚫 <b>GoFile upload cancelled.</b>")
                        except Exception:
                            pass
                    return None
                except aiohttp.ClientError as net_err:
                    if attempt < max_retries:
                        await asyncio.sleep(min(20.0, float(retry_delay * (2 ** (attempt - 1)))))
                        continue
                    err_txt = html.escape(str(net_err)[:200])
                    if status_msg is not None:
                        try:
                            await status_msg.edit_text(f"❌ <b>GoFile network error:</b> <code>{err_txt}</code>")
                        except Exception:
                            pass
                    return None
                except GoFileUploadError as g_err:
                    if status_msg is not None:
                        try:
                            await status_msg.edit_text(f"❌ <b>{html.escape(str(g_err)[:220])}</b>")
                        except Exception:
                            pass
                    return None
        return None
    finally:
        if own_job and job_key is not None:
            jobs.unregister(job_key)

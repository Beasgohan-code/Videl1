"""
Pluggable Upload Destination Adapters & Sandboxed User File Manager
(`VidelBot/platform_core/destinations.py`).

Supported destinations:
  • `local`    — Per-user sandboxed workspace storage
  • `telegram` — Telegram chat delivery via active bot client
  • `gdrive`   — Google Drive API v3 (`VideoEncoder.utils.uploads.drive`)
  • `dropbox`  — Official Dropbox HTTP API v2 (`/2/files/upload` + chunked upload session)
  • `onedrive` — Official Microsoft Graph API v1.0 (`createUploadSession` / `:/content`)
  • `rclone`   — User-configured Rclone remote (`VideoEncoder.utils.uploads.rclone`)
  • `gofile`   — GoFile API (`VideoEncoder.utils.uploads.gofile`)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from typing import Any, Callable

import aiohttp

import config
from platform_core import models_db
from platform_core.providers import category_for_filename, sanitize_filename

DESTINATION_CATALOG: list[dict[str, Any]] = [
    {"id": "local", "name": "Workspace Storage", "requires_connection": False},
    {"id": "telegram", "name": "Telegram Chat / Saved Messages", "requires_connection": False},
    {"id": "gdrive", "name": "Google Drive", "requires_connection": True},
    {"id": "dropbox", "name": "Dropbox (API v2)", "requires_connection": True},
    {"id": "onedrive", "name": "Microsoft OneDrive (Graph API)", "requires_connection": True},
    {"id": "rclone", "name": "Rclone Cloud Remote", "requires_connection": True},
    {"id": "gofile", "name": "GoFile Cloud Share", "requires_connection": False},
]

VALID_DESTINATIONS = {d["id"] for d in DESTINATION_CATALOG}


class DestinationError(RuntimeError):
    """Raised when an upload destination is misconfigured, unauthorized, or fails."""


def workspace_root() -> str:
    base = os.path.abspath(getattr(config, "DOWNLOAD_DIR", "download") or "download")
    root = os.path.join(base, "platform_files")
    os.makedirs(root, exist_ok=True)
    return root


def user_workspace_dir(user_id: int) -> str:
    uid = int(user_id)
    # Demo principals use negative IDs, which are disjoint from Telegram users.
    # The integer conversion keeps the directory name free of path components.
    udir = os.path.realpath(os.path.join(workspace_root(), f"u_{uid}"))
    os.makedirs(udir, exist_ok=True)
    return udir


def resolve_user_path(user_id: int, rel_path: str, *, must_exist: bool = True) -> str:
    """Resolve `rel_path` strictly inside `user_workspace_dir(user_id)` (blocks `..` & symlink escapes)."""
    raw = (rel_path or "").strip().lstrip("/\\")
    if not raw or "\x00" in raw:
        raise PermissionError("Invalid file path.")
    parts = [p for p in raw.replace("\\", "/").split("/") if p]
    if not parts or any(p in (".", "..") for p in parts):
        raise PermissionError("Path traversal outside your workspace is forbidden.")
    base = user_workspace_dir(user_id)
    candidate = os.path.realpath(os.path.join(base, *parts))
    if candidate == base or not candidate.startswith(base + os.sep):
        raise PermissionError("Path traversal outside your workspace is forbidden.")
    if must_exist and not os.path.isfile(candidate):
        raise FileNotFoundError(f"File not found: {'/'.join(parts)}")
    return candidate


def list_user_files(user_id: int, *, search: str = "", category: str = "") -> dict[str, Any]:
    base = user_workspace_dir(user_id)
    q = (search or "").strip().lower()
    cat_filter = (category or "").strip().lower()
    items: list[dict[str, Any]] = []
    total_bytes = 0
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in sorted(dirs) if not d.startswith(".")]
        for fn in sorted(files):
            if fn.startswith(".") or fn.endswith(".part"):
                continue
            full = os.path.realpath(os.path.join(root, fn))
            if not full.startswith(base + os.sep):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, base).replace("\\", "/")
            cat = category_for_filename(fn)
            total_bytes += st.st_size
            if q and q not in fn.lower() and q not in rel.lower():
                continue
            if cat_filter and cat_filter != "all" and cat != cat_filter:
                continue
            items.append(
                {
                    "path": rel,
                    "name": fn,
                    "size_bytes": st.st_size,
                    "category": cat,
                    "modified_ts": round(st.st_mtime, 2),
                }
            )
    items.sort(key=lambda x: x["modified_ts"], reverse=True)
    return {
        "files": items,
        "count": len(items),
        "workspace_used_bytes": total_bytes,
    }


def rename_user_file(user_id: int, rel_path: str, new_name: str) -> dict[str, Any]:
    src = resolve_user_path(user_id, rel_path, must_exist=True)
    clean_new = sanitize_filename(new_name)
    dst_dir = os.path.dirname(src)
    dst = os.path.realpath(os.path.join(dst_dir, clean_new))
    base = user_workspace_dir(user_id)
    if not dst.startswith(base + os.sep):
        raise PermissionError("Destination outside workspace.")
    if os.path.exists(dst) and dst != src:
        raise FileExistsError(f"A file named '{clean_new}' already exists.")
    os.replace(src, dst)
    st = os.stat(dst)
    return {
        "path": os.path.relpath(dst, base).replace("\\", "/"),
        "name": clean_new,
        "size_bytes": st.st_size,
        "category": category_for_filename(clean_new),
    }


def delete_user_file(user_id: int, rel_path: str) -> bool:
    target = resolve_user_path(user_id, rel_path, must_exist=True)
    os.remove(target)
    return True


async def _upload_to_dropbox(
    local_path: str,
    user_id: int,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    token, cfg = await models_db.get_provider_token(user_id, "dropbox")
    if not token:
        raise DestinationError("Dropbox is not connected. Add a Dropbox token in Cloud Storage / Integrations.")
    folder = "/" + str((cfg or {}).get("folder") or "VidelDownloads").strip("/")
    fname = sanitize_filename(os.path.basename(local_path))
    remote_path = f"{folder}/{fname}"
    total = os.path.getsize(local_path)
    timeout = aiohttp.ClientTimeout(total=1800, connect=20)
    chunk_limit = 8 * 1024 * 1024

    async with aiohttp.ClientSession(timeout=timeout) as session:
        if total <= 140 * 1024 * 1024:
            with open(local_path, "rb") as fp:
                payload = fp.read()
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
                "Dropbox-API-Arg": json.dumps(
                    {"path": remote_path, "mode": "overwrite", "autorename": True, "mute": False}
                ),
            }
            async with session.post(
                "https://content.dropboxapi.com/2/files/upload", headers=headers, data=payload
            ) as resp:
                if resp.status >= 400:
                    err_txt = (await resp.text())[:240]
                    await models_db.record_provider_outcome(user_id, "dropbox", ok=False, error=f"HTTP {resp.status}")
                    raise DestinationError(f"Dropbox upload failed (HTTP {resp.status}): {err_txt}")
                meta = await resp.json(content_type=None)
            if progress_cb:
                progress_cb(total, total)
        else:
            # Chunked upload_session for large files (> 140 MiB)
            session_id = ""
            offset = 0
            with open(local_path, "rb") as fp:
                while True:
                    chunk = fp.read(chunk_limit)
                    if not chunk:
                        break
                    is_last = offset + len(chunk) >= total
                    if not session_id:
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/octet-stream",
                            "Dropbox-API-Arg": json.dumps({"close": False}),
                        }
                        async with session.post(
                            "https://content.dropboxapi.com/2/files/upload_session/start",
                            headers=headers,
                            data=chunk,
                        ) as r1:
                            if r1.status >= 400:
                                raise DestinationError(f"Dropbox session start failed (HTTP {r1.status}).")
                            session_id = (await r1.json(content_type=None))["session_id"]
                    elif not is_last:
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/octet-stream",
                            "Dropbox-API-Arg": json.dumps(
                                {"cursor": {"session_id": session_id, "offset": offset}, "close": False}
                            ),
                        }
                        async with session.post(
                            "https://content.dropboxapi.com/2/files/upload_session/append_v2",
                            headers=headers,
                            data=chunk,
                        ) as r2:
                            if r2.status >= 400:
                                raise DestinationError(f"Dropbox chunk append failed (HTTP {r2.status}).")
                    else:
                        headers = {
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/octet-stream",
                            "Dropbox-API-Arg": json.dumps(
                                {
                                    "cursor": {"session_id": session_id, "offset": offset},
                                    "commit": {"path": remote_path, "mode": "overwrite", "autorename": True},
                                }
                            ),
                        }
                        async with session.post(
                            "https://content.dropboxapi.com/2/files/upload_session/finish",
                            headers=headers,
                            data=chunk,
                        ) as r3:
                            if r3.status >= 400:
                                raise DestinationError(f"Dropbox session finish failed (HTTP {r3.status}).")
                            meta = await r3.json(content_type=None)
                    offset += len(chunk)
                    if progress_cb:
                        progress_cb(offset, total)

    await models_db.record_provider_outcome(user_id, "dropbox", ok=True)
    return {
        "destination": "dropbox",
        "remote_url": f"https://www.dropbox.com/home{meta.get('path_display', remote_path)}",
        "remote_path": meta.get("path_display", remote_path),
    }


async def _upload_to_onedrive(
    local_path: str,
    user_id: int,
    *,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    token, cfg = await models_db.get_provider_token(user_id, "onedrive")
    if not token:
        raise DestinationError("OneDrive is not connected. Add a Microsoft Graph token in Cloud Storage / Integrations.")
    folder = str((cfg or {}).get("folder") or "VidelDownloads").strip("/")
    fname = sanitize_filename(os.path.basename(local_path))
    total = os.path.getsize(local_path)
    timeout = aiohttp.ClientTimeout(total=1800, connect=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        if total <= 4 * 1024 * 1024:
            with open(local_path, "rb") as fp:
                payload = fp.read()
            put_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder}/{fname}:/content"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"}
            async with session.put(put_url, headers=headers, data=payload) as resp:
                if resp.status >= 400:
                    await models_db.record_provider_outcome(user_id, "onedrive", ok=False, error=f"HTTP {resp.status}")
                    raise DestinationError(f"OneDrive upload failed (HTTP {resp.status}).")
                meta = await resp.json(content_type=None)
            if progress_cb:
                progress_cb(total, total)
        else:
            sess_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{folder}/{fname}:/createUploadSession"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            body = {"item": {"@microsoft.graph.conflictBehavior": "rename", "name": fname}}
            async with session.post(sess_url, headers=headers, json=body) as sresp:
                if sresp.status >= 400:
                    await models_db.record_provider_outcome(user_id, "onedrive", ok=False, error=f"HTTP {sresp.status}")
                    raise DestinationError(f"OneDrive upload session failed (HTTP {sresp.status}).")
                upload_url = (await sresp.json(content_type=None))["uploadUrl"]
            chunk_sz = 5 * 320 * 1024  # 1.6 MiB (multiple of 320 KiB per Graph API spec)
            offset = 0
            meta = {}
            with open(local_path, "rb") as fp:
                while offset < total:
                    chunk = fp.read(chunk_sz)
                    if not chunk:
                        break
                    end_byte = offset + len(chunk) - 1
                    c_headers = {
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end_byte}/{total}",
                    }
                    async with session.put(upload_url, headers=c_headers, data=chunk) as cresp:
                        if cresp.status >= 400:
                            raise DestinationError(f"OneDrive chunk upload failed (HTTP {cresp.status}).")
                        if cresp.status in (200, 201):
                            meta = await cresp.json(content_type=None)
                    offset += len(chunk)
                    if progress_cb:
                        progress_cb(offset, total)

    await models_db.record_provider_outcome(user_id, "onedrive", ok=True)
    return {
        "destination": "onedrive",
        "remote_url": str(meta.get("webUrl") or ""),
        "remote_path": f"/{folder}/{fname}",
    }


async def deliver_file(
    local_path: str,
    *,
    user_id: int,
    destination: str = "local",
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Deliver `local_path` to the requested destination and log into `upload_history`."""
    dest = (destination or "local").strip().lower()
    if dest not in VALID_DESTINATIONS:
        raise DestinationError(f"Unsupported upload destination '{dest}'.")
    if not os.path.isfile(local_path):
        raise DestinationError("Source file does not exist on disk.")

    size_bytes = os.path.getsize(local_path)
    fname = sanitize_filename(os.path.basename(local_path))

    try:
        if dest == "local":
            udir = user_workspace_dir(user_id)
            target = os.path.realpath(os.path.join(udir, fname))
            if os.path.abspath(local_path) != target:
                if os.path.exists(target):
                    stem, ext = os.path.splitext(fname)
                    target = os.path.join(udir, f"{stem}_{int(time.time())}{ext}")
                shutil.copy2(local_path, target)
            if progress_cb:
                progress_cb(size_bytes, size_bytes)
            rel = os.path.relpath(target, udir).replace("\\", "/")
            res = {"destination": "local", "remote_url": "", "local_rel_path": rel}

        elif dest == "telegram":
            import client as bot_client

            tg_bot = getattr(bot_client, "bot", None)
            if tg_bot is None or not getattr(tg_bot, "is_connected", False):
                raise DestinationError(
                    "Telegram bot session is not currently connected (standalone web mode). Choose Workspace or Cloud."
                )
            msg = await tg_bot.send_document(
                chat_id=int(user_id),
                document=local_path,
                file_name=fname,
                caption=f"📦 <b>{fname}</b>",
            )
            if progress_cb:
                progress_cb(size_bytes, size_bytes)
            res = {"destination": "telegram", "remote_url": "", "message_id": getattr(msg, "id", 0)}

        elif dest == "gdrive":
            from VideoEncoder.utils.uploads.drive import is_drive_configured
            from VideoEncoder.utils.uploads.drive.upload import upload_file as gdrive_upload

            if not is_drive_configured():
                raise DestinationError("Google Drive credentials are not configured on this instance.")
            folder_id = getattr(config, "GDRIVE_FOLDER_ID", "") or "root"
            ok, link_or_err = await asyncio.to_thread(
                gdrive_upload, local_path, fname, "application/octet-stream", folder_id
            )
            if not ok:
                raise DestinationError(f"Google Drive upload failed: {link_or_err}")
            if progress_cb:
                progress_cb(size_bytes, size_bytes)
            res = {"destination": "gdrive", "remote_url": str(link_or_err)}

        elif dest == "dropbox":
            res = await _upload_to_dropbox(local_path, user_id, progress_cb=progress_cb)

        elif dest == "onedrive":
            res = await _upload_to_onedrive(local_path, user_id, progress_cb=progress_cb)

        elif dest == "rclone":
            from VideoEncoder.utils.uploads.rclone import rclone_upload
            from VideoEncoder.utils.uploads.storage import load_rclone_conf, user_settings

            conf_path = await load_rclone_conf(user_id)
            uset = await user_settings(user_id)
            remote = str(uset.get("rclone_remote") or "")
            if not conf_path or not remote:
                raise DestinationError("Rclone config/remote is not configured for your account.")
            link = await rclone_upload(
                local_path,
                conf_path,
                remote,
                base_dir=str(uset.get("rclone_path") or "Videl"),
                is_cancelled=cancel_check,
            )
            if progress_cb:
                progress_cb(size_bytes, size_bytes)
            res = {"destination": "rclone", "remote_url": str(link or remote)}

        elif dest == "gofile":
            from VideoEncoder.utils.uploads.gofile import gofile_upload

            token, gcfg = await models_db.get_provider_token(user_id, "gofile")
            link = await gofile_upload(
                local_path,
                token=token or None,
                folder_id=(gcfg or {}).get("folder_id") or None,
                is_cancelled=cancel_check,
            )
            if progress_cb:
                progress_cb(size_bytes, size_bytes)
            res = {"destination": "gofile", "remote_url": str(link)}
        else:
            raise DestinationError(f"Unsupported destination '{dest}'.")

        await models_db.record_upload_history(
            user_id=user_id,
            filename=fname,
            destination=dest,
            status="completed",
            size_bytes=size_bytes,
            remote_url=res.get("remote_url", ""),
        )
        return res

    except Exception as exc:
        await models_db.record_upload_history(
            user_id=user_id,
            filename=fname,
            destination=dest,
            status="failed",
            size_bytes=size_bytes,
            error=str(exc)[:300],
        )
        raise

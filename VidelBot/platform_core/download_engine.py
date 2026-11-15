"""
Streaming Download & Queue Engine (`VidelBot/platform_core/download_engine.py`).

Features:
  • Priority queue (`high`, `normal`, `low`) with global & per-user concurrency gates
  • Streaming chunked downloads (`64 KiB` chunks) with `.part` HTTP `Range` pause/resume
  • Per-user bandwidth rate limiting (`max_kbps` token pacing)
  • Real-time progress, transfer speed (`speed_bps`), and ETA (`eta_sec`) mirrored to `jobs`
  • Streaming SHA-256 calculation, optional checksum verification, and duplicate detection
  • Exponential backoff retry on transient HTTP / network errors
  • Pluggable post-download delivery (`local`, `telegram`, `gdrive`, `dropbox`, `onedrive`, `rclone`, `gofile`)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from typing import Any

import aiohttp

from core import bg
from platform_core import destinations, models_db, providers

log = logging.getLogger("videl.download_engine")


class _NoopJob:
    id = "standalone"


class _NoopJobs:
    @staticmethod
    def start(*_a, **_kw):
        return _NoopJob()

    @staticmethod
    def update(*_a, **_kw):
        return None

    @staticmethod
    def finish(*_a, **_kw):
        return None


def _jobs_mod():
    try:
        import importlib.util
        import sys
        if "VideoEncoder.utils.jobs" in sys.modules:
            return sys.modules["VideoEncoder.utils.jobs"]
        spec = importlib.util.spec_from_file_location(
            "VideoEncoder.utils.jobs",
            os.path.join(os.path.dirname(os.path.dirname(__file__)), "VideoEncoder", "utils", "jobs.py"),
        )
        if spec and spec.loader:
            mod = importlib.util.module_from_spec(spec)
            sys.modules["VideoEncoder.utils.jobs"] = mod
            spec.loader.exec_module(mod)
            return mod
    except Exception:
        pass
    return _NoopJobs()

CHUNK_SIZE = 65536
GLOBAL_MAX_CONCURRENT = 6
PRIORITY_RANK = {"high": 0, "normal": 1, "low": 2}


class DownloadEngineError(RuntimeError):
    """Raised on validation, quota, checksum, or transfer errors."""


class PlatformDownloadEngine:
    """In-process async priority queue backed by MongoDB `platform_tasks` + `download_history`."""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._active_handles: dict[str, asyncio.Task] = {}
        self._cancel_flags: dict[str, str] = {}  # task_id -> "paused" | "cancelled"
        self._wake = asyncio.Event()
        self._dispatcher_task: asyncio.Task | None = None
        self.retry_delay: float = 1.5

    def _ensure_dispatcher(self) -> None:
        if self._dispatcher_task is None or self._dispatcher_task.done():
            self._dispatcher_task = bg.spawn(self._dispatch_loop(), name="platform-dl-dispatcher")

    def _active_count(self, user_id: int | None = None) -> int:
        cnt = 0
        for tid, handle in list(self._active_handles.items()):
            if handle.done():
                self._active_handles.pop(tid, None)
                continue
            if user_id is None or int(self._tasks.get(tid, {}).get("user_id", 0)) == int(user_id):
                cnt += 1
        return cnt

    async def enqueue_item(
        self,
        *,
        user_id: int,
        item: providers.RemoteFileItem,
        source_url: str = "",
        destination: str = "local",
        priority: str = "normal",
        expected_sha256: str = "",
        allow_duplicate: bool = True,
    ) -> dict[str, Any]:
        uid = int(user_id)
        prio = priority if priority in PRIORITY_RANK else "normal"
        dest = (destination or "local").strip().lower()
        if dest not in destinations.VALID_DESTINATIONS:
            raise DownloadEngineError(f"Unsupported destination '{dest}'.")

        uset = await models_db.get_webapp_settings(uid)
        dl_cfg = uset.get("downloads") or {}
        max_mb = int(dl_cfg.get("max_file_mb") or 2048)
        if item.size_bytes and item.size_bytes > max_mb * 1024 * 1024:
            raise DownloadEngineError(
                f"File '{item.filename}' ({item.size_bytes // (1024 * 1024)} MiB) exceeds your {max_mb} MiB limit."
            )

        dup = None
        dup_action = str(dl_cfg.get("duplicate_action") or "warn")
        if dup_action != "allow":
            dup = await models_db.check_duplicate_download(uid, url=item.download_url)
            if dup and (dup_action == "skip" or not allow_duplicate):
                raise DownloadEngineError(
                    f"Duplicate download detected: '{dup.get('filename')}' was already completed."
                )

        tid = f"dl_{secrets.token_hex(6)}"
        now = time.time()
        record: dict[str, Any] = {
            "task_id": tid,
            "user_id": uid,
            "kind": "download",
            "title": providers.sanitize_filename(item.filename),
            "filename": providers.sanitize_filename(item.filename),
            "source_url": source_url or item.download_url,
            "download_url": item.download_url,
            "provider": item.provider,
            "category": item.category,
            "destination": dest,
            "priority": prio,
            "priority_rank": PRIORITY_RANK[prio],
            "status": "queued",
            "stage": "Queued",
            "total_bytes": int(item.size_bytes or 0),
            "done_bytes": 0,
            "percent": 0.0,
            "speed_bps": 0.0,
            "eta_sec": 0,
            "retries": 0,
            "max_retries": int((uset.get("performance") or {}).get("auto_retry_count") or 2),
            "supports_range": bool(item.supports_range),
            "expected_sha256": (expected_sha256 or "").strip().lower(),
            "sha256": "",
            "duplicate_of": dup.get("entry_id") if dup else "",
            "local_rel_path": "",
            "remote_url": "",
            "error": "",
            "extra": dict(item.extra or {}),
            "created_ts": now,
            "updated_ts": now,
        }
        self._tasks[tid] = record
        await models_db.upsert_platform_task(tid, record)
        self._ensure_dispatcher()
        self._wake.set()
        return self._public_task(record)

    async def enqueue_url(
        self,
        *,
        user_id: int,
        url: str,
        destination: str = "local",
        priority: str = "normal",
        expected_sha256: str = "",
        auto_select_all: bool = False,
    ) -> dict[str, Any]:
        """Inspect `url`; enqueue directly when single-file or return a selection session when multi-file."""
        manifest = await providers.inspect_source(url, user_id=user_id)
        uset = await models_db.get_webapp_settings(user_id)
        raw_excl = (uset.get("downloads") or {}).get("excluded_extensions") or []
        if isinstance(raw_excl, str):
            excl = [x.strip() for x in raw_excl.split(",") if x.strip()]
        else:
            excl = [str(x).strip() for x in raw_excl if str(x).strip()]
        if (manifest.requires_selection or len(manifest.files) > 1) and not auto_select_all:
            sess_view = providers.selection_store.create(
                user_id, manifest, destination=destination, excluded_extensions=excl
            )
            return {"mode": "selection_required", "selection": sess_view}

        tasks = []
        for f in manifest.files:
            ext = os.path.splitext(f.filename.lower())[1]
            if excl and ext in {e if e.startswith(".") else f".{e}" for e in excl}:
                continue
            t = await self.enqueue_item(
                user_id=user_id,
                item=f,
                source_url=url,
                destination=destination,
                priority=priority,
                expected_sha256=expected_sha256 if len(manifest.files) == 1 else "",
            )
            tasks.append(t)
        if not tasks:
            raise DownloadEngineError("All files in source matched your excluded extensions filter.")
        return {"mode": "enqueued", "tasks": tasks}

    async def enqueue_confirmed_selection(
        self,
        *,
        session_id: str,
        user_id: int,
        priority: str = "normal",
        destination: str = "",
        is_admin: bool = False,
    ) -> list[dict[str, Any]]:
        sess, chosen = providers.selection_store.pop_confirmed(session_id, user_id, is_admin=is_admin)
        dest = destination or sess.get("destination") or "local"
        owner_id = int(sess["user_id"])
        created = []
        for item in chosen:
            created.append(
                await self.enqueue_item(
                    user_id=owner_id,
                    item=item,
                    source_url=sess["source_url"],
                    destination=dest,
                    priority=priority,
                )
            )
        return created

    async def _dispatch_loop(self) -> None:
        while True:
            self._wake.clear()
            await self._try_schedule_queued()
            # Exit dispatcher cleanly when idle so unit tests never hang on background loops
            has_queued = any(t.get("status") == "queued" for t in self._tasks.values())
            if not has_queued and self._active_count() == 0:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=0.5)
            except asyncio.TimeoutError:
                pass

    async def _try_schedule_queued(self) -> None:
        if self._active_count() >= GLOBAL_MAX_CONCURRENT:
            return
        queued = [t for t in self._tasks.values() if t.get("status") == "queued"]
        queued.sort(key=lambda r: (int(r.get("priority_rank", 1)), float(r.get("created_ts", 0))))
        for rec in queued:
            if self._active_count() >= GLOBAL_MAX_CONCURRENT:
                break
            uid = int(rec["user_id"])
            uset = await models_db.get_webapp_settings(uid)
            user_cap = max(1, min(6, int((uset.get("downloads") or {}).get("max_concurrent") or 3)))
            if self._active_count(uid) >= user_cap:
                continue
            tid = rec["task_id"]
            rec["status"] = "downloading"
            rec["stage"] = "Starting"
            rec["updated_ts"] = time.time()
            self._cancel_flags.pop(tid, None)
            handle = bg.spawn(self._run_download(rec), name=f"dl-{tid}")
            self._active_handles[tid] = handle

    async def _run_download(self, rec: dict[str, Any]) -> None:
        tid = rec["task_id"]
        uid = int(rec["user_id"])
        udir = destinations.user_workspace_dir(uid)
        fname = providers.sanitize_filename(rec["filename"])
        final_path = os.path.join(udir, fname)
        part_path = f"{final_path}.{tid}.part"

        uset = await models_db.get_webapp_settings(uid)
        dl_s = uset.get("downloads") or {}
        max_kbps = int(dl_s.get("bandwidth_kbps") or dl_s.get("max_kbps") or 0)
        max_mb = int(dl_s.get("max_file_mb") or 2048)
        byte_cap = max_mb * 1024 * 1024

        jobs = _jobs_mod()
        job = jobs.start("download", uid, title=fname, total=int(rec.get("total_bytes") or 0))
        rec["job_id"] = job.id

        try:
            attempt = 0
            while True:
                if self._cancel_flags.get(tid):
                    raise asyncio.CancelledError(self._cancel_flags[tid])
                try:
                    await self._transfer_to_part(rec, part_path, byte_cap=byte_cap, max_kbps=max_kbps, job=job)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    attempt += 1
                    rec["retries"] = attempt
                    if attempt > int(rec.get("max_retries", 2)):
                        raise
                    rec["stage"] = f"Retrying ({attempt}/{rec.get('max_retries', 2)})"
                    rec["updated_ts"] = time.time()
                    await models_db.upsert_platform_task(tid, rec)
                    await asyncio.sleep(self.retry_delay * (2 ** (attempt - 1)))

            # Compute SHA-256 & verify expected hash if supplied
            rec["stage"] = "Verifying SHA-256"
            digest = await asyncio.to_thread(self._sha256_file, part_path)
            rec["sha256"] = digest
            if rec.get("expected_sha256") and digest.lower() != rec["expected_sha256"].lower():
                try:
                    os.remove(part_path)
                except OSError:
                    pass
                raise DownloadEngineError(
                    f"SHA-256 mismatch: expected {rec['expected_sha256'][:16]}…, got {digest[:16]}…"
                )

            if os.path.exists(final_path):
                stem, ext = os.path.splitext(fname)
                fname = f"{stem}_{int(time.time())}{ext}"
                final_path = os.path.join(udir, fname)
                rec["filename"] = fname
            os.replace(part_path, final_path)
            rec["done_bytes"] = os.path.getsize(final_path)
            rec["total_bytes"] = rec["done_bytes"]
            rec["percent"] = 100.0
            rec["local_rel_path"] = os.path.relpath(final_path, udir).replace("\\", "/")

            # Deliver to destination when destination != local
            if rec["destination"] != "local":
                rec["status"] = "uploading"
                rec["stage"] = f"Uploading -> {rec['destination']}"
                await models_db.upsert_platform_task(tid, rec)
                up_res = await destinations.deliver_file(
                    final_path,
                    user_id=uid,
                    destination=rec["destination"],
                    cancel_check=lambda: bool(self._cancel_flags.get(tid)),
                )
                rec["remote_url"] = str(up_res.get("remote_url") or "")

            rec["status"] = "completed"
            rec["stage"] = "Completed"
            rec["speed_bps"] = 0.0
            rec["eta_sec"] = 0
            rec["error"] = ""
            rec["updated_ts"] = time.time()
            jobs.finish(job.id, ok=True)
            await models_db.upsert_platform_task(tid, rec)
            await models_db.record_download_history(
                user_id=uid,
                url=rec["source_url"],
                provider=rec["provider"],
                filename=rec["filename"],
                size_bytes=rec["done_bytes"],
                sha256=digest,
                destination=rec["destination"],
                status="completed",
                remote_url=rec["remote_url"],
                local_path=rec["local_rel_path"],
            )

        except asyncio.CancelledError:
            reason = self._cancel_flags.get(tid) or "cancelled"
            rec["status"] = "paused" if reason == "paused" else "cancelled"
            rec["stage"] = "Paused" if reason == "paused" else "Cancelled"
            rec["speed_bps"] = 0.0
            rec["eta_sec"] = 0
            rec["updated_ts"] = time.time()
            if reason != "paused" and os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
            jobs.finish(job.id, ok=False, error=rec["stage"])
            await models_db.upsert_platform_task(tid, rec)

        except Exception as exc:
            err_msg = str(exc)[:300]
            rec["status"] = "failed"
            rec["stage"] = "Failed"
            rec["error"] = err_msg
            rec["speed_bps"] = 0.0
            rec["eta_sec"] = 0
            rec["updated_ts"] = time.time()
            jobs.finish(job.id, ok=False, error=err_msg)
            await models_db.upsert_platform_task(tid, rec)
            await models_db.record_download_history(
                user_id=uid,
                url=rec["source_url"],
                provider=rec["provider"],
                filename=rec["filename"],
                size_bytes=rec.get("done_bytes", 0),
                destination=rec["destination"],
                status="failed",
                error=err_msg,
            )
        finally:
            self._active_handles.pop(tid, None)
            self._wake.set()

    async def _transfer_to_part(
        self,
        rec: dict[str, Any],
        part_path: str,
        *,
        byte_cap: int,
        max_kbps: int,
        job: Any,
    ) -> None:
        tid = rec["task_id"]
        uid = int(rec["user_id"])
        prov = rec["provider"]
        jobs = _jobs_mod()

        if prov == "mega":
            from VideoEncoder.utils import leech

            def _prog(done: int, total: int, _st: str) -> None:
                rec["done_bytes"] = done
                rec["total_bytes"] = total
                rec["percent"] = round((done * 100.0 / total), 1) if total else 0.0
                jobs.update(job.id, done=done, total=total, stage="Downloading Mega")

            out_file, _ = await leech.download_mega(
                rec["download_url"],
                os.path.dirname(part_path),
                progress=_prog,
                cancelled=lambda: bool(self._cancel_flags.get(tid)),
            )
            os.replace(out_file, part_path)
            return

        if prov == "gdrive" and (rec.get("extra") or {}).get("drive_id"):
            from VideoEncoder.utils.uploads.drive import is_drive_configured
            from VideoEncoder.utils.uploads.drive.download import download_file as gdrive_dl

            if is_drive_configured():
                ok, res_path = await asyncio.to_thread(
                    gdrive_dl, rec["extra"]["drive_id"], os.path.dirname(part_path)
                )
                if ok and os.path.isfile(res_path):
                    os.replace(res_path, part_path)
                    return

        existing = os.path.getsize(part_path) if os.path.isfile(part_path) else 0
        headers: dict[str, str] = {}
        method = "GET"

        if prov == "dropbox" and (rec.get("extra") or {}).get("auth") == "token":
            token, _ = await models_db.get_provider_token(uid, "dropbox")
            if not token:
                raise DownloadEngineError("Dropbox token missing.")
            method = "POST"
            headers["Authorization"] = f"Bearer {token}"
            headers["Dropbox-API-Arg"] = json.dumps({"path": rec["extra"]["dropbox_path"]})
        elif prov == "onedrive" and (rec.get("extra") or {}).get("use_bearer"):
            token, _ = await models_db.get_provider_token(uid, "onedrive")
            if token:
                headers["Authorization"] = f"Bearer {token}"

        if existing > 0:
            headers["Range"] = f"bytes={existing}-"

        timeout = aiohttp.ClientTimeout(total=3600, sock_read=60, connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            resp, _ = await providers.open_ssrf_safe_response(
                session, method, rec["download_url"], headers=headers
            )
            try:
                if resp.status == 416 and existing > 0:
                    # Already fully downloaded in .part
                    return
                if resp.status >= 400:
                    raise DownloadEngineError(f"Remote server returned HTTP {resp.status}.")
                resumed = resp.status == 206 and existing > 0
                mode = "ab" if resumed else "wb"
                done = existing if resumed else 0

                cl = resp.headers.get("Content-Length", "")
                total = (done + int(cl)) if cl.isdigit() else int(rec.get("total_bytes") or 0)
                if total and total > byte_cap:
                    raise DownloadEngineError(f"Remote file ({total // (1024 * 1024)} MiB) exceeds quota.")
                rec["total_bytes"] = total
                rec["stage"] = "Downloading"

                t_start = time.monotonic()
                bytes_this_run = 0
                last_persist = 0.0

                with open(part_path, mode) as fp:
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        if self._cancel_flags.get(tid):
                            raise asyncio.CancelledError(self._cancel_flags[tid])
                        if not chunk:
                            continue
                        done += len(chunk)
                        bytes_this_run += len(chunk)
                        if done > byte_cap:
                            raise DownloadEngineError("Download exceeded maximum allowed file size.")
                        fp.write(chunk)

                        elapsed = max(0.05, time.monotonic() - t_start)
                        speed = bytes_this_run / elapsed
                        eta = int((total - done) / speed) if (total > done and speed > 1) else 0
                        pct = round((done * 100.0 / total), 1) if total > 0 else 0.0
                        rec["done_bytes"] = done
                        rec["speed_bps"] = round(speed, 1)
                        rec["eta_sec"] = eta
                        rec["percent"] = min(99.9, pct)

                        now_m = time.monotonic()
                        if now_m - last_persist >= 0.6:
                            last_persist = now_m
                            rec["updated_ts"] = time.time()
                            jobs.update(job.id, done=done, total=total, stage="Downloading")

                        if max_kbps > 0:
                            target_elapsed = bytes_this_run / (max_kbps * 1024.0)
                            if target_elapsed > elapsed:
                                await asyncio.sleep(min(0.5, target_elapsed - elapsed))
            finally:
                resp.release()

    @staticmethod
    def _sha256_file(path: str) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as fp:
            for block in iter(lambda: fp.read(262144), b""):
                h.update(block)
        return h.hexdigest()

    def _get_owned_record(self, task_id: str, user_id: int, *, is_admin: bool = False) -> dict[str, Any]:
        rec = self._tasks.get(task_id)
        if not rec:
            raise KeyError(f"Task '{task_id}' not found.")
        if not is_admin and int(rec["user_id"]) != int(user_id):
            raise PermissionError("You do not have permission to modify another user's task.")
        return rec

    async def control_task(
        self, task_id: str, action: str, *, user_id: int, is_admin: bool = False
    ) -> dict[str, Any]:
        act = (action or "").strip().lower()
        rec = self._get_owned_record(task_id, user_id, is_admin=is_admin)

        if act == "pause":
            if rec["status"] == "queued":
                rec["status"] = "paused"
                rec["stage"] = "Paused"
            elif rec["status"] in ("downloading", "uploading"):
                self._cancel_flags[task_id] = "paused"
                handle = self._active_handles.get(task_id)
                if handle and not handle.done():
                    handle.cancel()
        elif act == "resume":
            if rec["status"] == "paused":
                self._cancel_flags.pop(task_id, None)
                rec["status"] = "queued"
                rec["stage"] = "Queued (Resume)"
                self._ensure_dispatcher()
                self._wake.set()
        elif act == "cancel":
            if rec["status"] in ("queued", "paused"):
                rec["status"] = "cancelled"
                rec["stage"] = "Cancelled"
            elif rec["status"] in ("downloading", "uploading"):
                self._cancel_flags[task_id] = "cancelled"
                handle = self._active_handles.get(task_id)
                if handle and not handle.done():
                    handle.cancel()
        elif act == "retry":
            if rec["status"] in ("failed", "cancelled"):
                self._cancel_flags.pop(task_id, None)
                rec["status"] = "queued"
                rec["stage"] = "Queued (Retry)"
                rec["error"] = ""
                rec["retries"] = 0
                self._ensure_dispatcher()
                self._wake.set()
        elif act == "priority_high":
            rec["priority"] = "high"
            rec["priority_rank"] = 0
        elif act == "delete":
            if rec["status"] in ("downloading", "uploading"):
                self._cancel_flags[task_id] = "cancelled"
                handle = self._active_handles.get(task_id)
                if handle and not handle.done():
                    handle.cancel()
            self._tasks.pop(task_id, None)
            await models_db.delete_platform_task(task_id, user_id=None if is_admin else user_id)
            return {"task_id": task_id, "deleted": True}
        else:
            raise DownloadEngineError(f"Unsupported task action '{act}'.")

        rec["updated_ts"] = time.time()
        await models_db.upsert_platform_task(task_id, rec)
        return self._public_task(rec)

    async def clear_finished(self, *, user_id: int, is_admin: bool = False, all_users: bool = False) -> int:
        removed = 0
        terminal = {"completed", "failed", "cancelled"}
        for tid, rec in list(self._tasks.items()):
            if rec.get("status") not in terminal:
                continue
            if not (is_admin and all_users) and int(rec["user_id"]) != int(user_id):
                continue
            self._tasks.pop(tid, None)
            await models_db.delete_platform_task(tid)
            removed += 1
        return removed

    def list_tasks(
        self,
        *,
        user_id: int,
        is_admin: bool = False,
        all_users: bool = False,
        status: str = "",
        search: str = "",
    ) -> list[dict[str, Any]]:
        q = (search or "").strip().lower()
        st = (status or "").strip().lower()
        rows = []
        for rec in self._tasks.values():
            if not (is_admin and all_users) and int(rec["user_id"]) != int(user_id):
                continue
            if st and st != "all" and rec.get("status") != st:
                continue
            if q and q not in rec.get("filename", "").lower() and q not in rec.get("provider", "").lower():
                continue
            rows.append(self._public_task(rec))
        rows.sort(key=lambda r: float(r.get("created_ts", 0)), reverse=True)
        return rows

    @staticmethod
    def _public_task(rec: dict[str, Any]) -> dict[str, Any]:
        return {
            "task_id": rec["task_id"],
            "user_id": rec["user_id"],
            "kind": rec.get("kind", "download"),
            "title": rec.get("title") or rec.get("filename"),
            "filename": rec.get("filename", ""),
            "source_url": rec.get("source_url", ""),
            "provider": rec.get("provider", ""),
            "category": rec.get("category", "other"),
            "destination": rec.get("destination", "local"),
            "priority": rec.get("priority", "normal"),
            "status": rec.get("status", "queued"),
            "stage": rec.get("stage", ""),
            "total_bytes": int(rec.get("total_bytes") or 0),
            "done_bytes": int(rec.get("done_bytes") or 0),
            "percent": float(rec.get("percent") or 0.0),
            "speed_bps": float(rec.get("speed_bps") or 0.0),
            "eta_sec": int(rec.get("eta_sec") or 0),
            "retries": int(rec.get("retries") or 0),
            "sha256": rec.get("sha256", ""),
            "duplicate_of": rec.get("duplicate_of", ""),
            "local_rel_path": rec.get("local_rel_path", ""),
            "remote_url": rec.get("remote_url", ""),
            "error": rec.get("error", ""),
            "created_ts": rec.get("created_ts", 0),
            "updated_ts": rec.get("updated_ts", 0),
        }


engine = PlatformDownloadEngine()

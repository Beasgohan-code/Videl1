"""
MongoDB repositories, indexes, audit trail, history, storage connections, scheduled jobs
& 11-category webapp settings (`VidelBot/platform_core/models_db.py`).

Uses Videl1's shared `vdb.db` Motor database pool (`core.db.vdb`) without introducing a second
database engine or storing binary media inside MongoDB. All caches mutate strictly AFTER
successful database persistence.
"""
from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import Any

import config
from core.db import vdb
from filestore.utils import security

TASK_STATES = ("queued", "selecting", "downloading", "uploading", "paused", "completed", "failed", "cancelled")
TASK_PRIORITIES = {"low": 1, "normal": 2, "high": 3, "urgent": 4}

# All 11 requested Settings categories (Part 3D)
DEFAULT_WEBAPP_SETTINGS: dict[str, Any] = {
    "general": {"language": "en", "timezone": "UTC", "compact_tables": False},
    "downloads": {
        "default_destination": "local",
        "max_concurrent": 3,
        "bandwidth_kbps": 0,  # 0 = unlimited
        "max_file_mb": 2048,
        "duplicate_action": "warn",  # warn | allow | skip
        "verify_checksum": True,
        "preferred_extensions": [],
        "excluded_extensions": [".nfo", ".url"],
    },
    "uploads": {
        "auto_split_tg": True,
        "preserve_filename": True,
        "keep_local_copy": True,
    },
    "storage": {
        "default_provider": "local",
        "auto_cleanup_hours": 24,
        "quota_alert_pct": 85,
    },
    "media": {
        "video_preset": "veryfast",
        "video_crf": 24,
        "audio_format": "mp3",
        "audio_bitrate": "192k",
        "image_quality": 85,
    },
    "performance": {
        "auto_retry_count": 2,
        "chunk_kb": 64,
    },
    "notifications": {
        "notify_telegram_on_complete": False,
        "notify_on_failure": True,
    },
    "privacy": {
        "auto_purge_history_days": 30,
        "mask_urls_in_logs": True,
    },
    "security": {
        "session_timeout_hours": 12,
        "confirm_destructive_actions": True,
    },
    "appearance": {
        "theme": "dark",  # dark | light | system
        "file_view": "list",  # list | grid
    },
    "advanced": {
        "smart_categorize": True,
        "strict_mime_check": True,
    },
}

_settings_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_SETTINGS_TTL = 45.0


class _StandaloneCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def sort(self, key: str, direction: int = 1) -> "_StandaloneCursor":
        self._rows.sort(key=lambda r: r.get(key, 0), reverse=(direction < 0))
        return self

    def limit(self, n: int) -> "_StandaloneCursor":
        self._rows = self._rows[:n]
        return self

    def __aiter__(self):
        self._iter = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return dict(next(self._iter))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _StandaloneCollection:
    """Used ONLY when `MONGO_URI` is unconfigured in standalone preview mode."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    @staticmethod
    def _match(doc: dict[str, Any], q: dict[str, Any]) -> bool:
        for k, v in (q or {}).items():
            dv = doc.get(k)
            if isinstance(v, dict):
                if "$lte" in v and not (dv is not None and dv <= v["$lte"]):
                    return False
                if "$in" in v and dv not in v["$in"]:
                    return False
            elif dv != v:
                return False
        return True

    async def create_index(self, *_a, **_kw) -> str:
        return "standalone_idx"

    async def find_one(self, q: dict[str, Any] | None = None) -> dict[str, Any] | None:
        for d in self.docs:
            if self._match(d, q or {}):
                return dict(d)
        return None

    def find(self, q: dict[str, Any] | None = None) -> _StandaloneCursor:
        return _StandaloneCursor([dict(d) for d in self.docs if self._match(d, q or {})])

    async def insert_one(self, doc: dict[str, Any]):
        self.docs.append(dict(doc))
        return type("Res", (), {"inserted_id": doc.get("_id")})()

    async def update_one(self, q: dict[str, Any], upd: dict[str, Any], upsert: bool = False):
        for d in self.docs:
            if self._match(d, q):
                if "$set" in upd:
                    d.update(upd["$set"])
                if "$inc" in upd:
                    for ik, iv in upd["$inc"].items():
                        d[ik] = int(d.get(ik, 0)) + int(iv)
                return type("Res", (), {"modified_count": 1})()
        if upsert:
            base = {k: v for k, v in q.items() if not isinstance(v, dict)}
            if "$set" in upd:
                base.update(upd["$set"])
            self.docs.append(base)
            return type("Res", (), {"modified_count": 1})()
        return type("Res", (), {"modified_count": 0})()

    async def delete_one(self, q: dict[str, Any]):
        for i, d in enumerate(self.docs):
            if self._match(d, q):
                self.docs.pop(i)
                return type("Res", (), {"deleted_count": 1})()
        return type("Res", (), {"deleted_count": 0})()

    async def delete_many(self, q: dict[str, Any]):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._match(d, q)]
        return type("Res", (), {"deleted_count": before - len(self.docs)})()


_standalone_cols: dict[str, _StandaloneCollection] = {}


def _col(name: str):
    try:
        return vdb.db[name]
    except Exception:
        if getattr(config, "MONGO_URI", ""):
            raise
        if name not in _standalone_cols:
            _standalone_cols[name] = _StandaloneCollection()
        return _standalone_cols[name]


def is_mongodb_backend() -> bool:
    try:
        _ = vdb.db
        return True
    except Exception:
        return False


def clear_caches() -> None:
    _settings_cache.clear()
    _standalone_cols.clear()


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def tasks_col():
    return _col("platform_tasks")


def dl_history_col():
    return _col("download_history")


def ul_history_col():
    return _col("upload_history")


def schedules_col():
    return _col("scheduled_jobs")


def connections_col():
    return _col("storage_connections")


def audit_col():
    return _col("audit_logs")


def webapp_settings_col():
    return _col("webapp_settings")


async def ensure_platform_indexes() -> None:
    """Create indexes idempotently across all platform collections."""
    try:
        await tasks_col().create_index("task_id", unique=True)
        await tasks_col().create_index([("user_id", 1), ("created_ts", -1)])
        await tasks_col().create_index([("status", 1), ("priority_rank", 1), ("created_ts", 1)])

        await dl_history_col().create_index("entry_id", unique=True)
        await dl_history_col().create_index([("user_id", 1), ("created_ts", -1)])
        await dl_history_col().create_index("sha256", sparse=True)
        await dl_history_col().create_index([("user_id", 1), ("source_url", 1)])

        await ul_history_col().create_index("entry_id", unique=True)
        await ul_history_col().create_index([("user_id", 1), ("created_ts", -1)])

        await schedules_col().create_index("job_id", unique=True)
        await schedules_col().create_index([("enabled", 1), ("next_run_ts", 1)])
        await schedules_col().create_index("user_id")

        await connections_col().create_index([("user_id", 1), ("provider", 1)], unique=True)

        await audit_col().create_index([("created_ts", -1)])
        await audit_col().create_index("actor_id")
    except Exception:
        pass


# ─────────────────────────── Platform Tasks Persistence ───────────────────────────
async def upsert_platform_task(task_id: str, doc: dict[str, Any]) -> None:
    clean = {k: v for k, v in doc.items() if k != "_id"}
    clean["task_id"] = str(task_id)
    await tasks_col().update_one({"task_id": str(task_id)}, {"$set": clean}, upsert=True)


async def delete_platform_task(task_id: str, *, user_id: int | None = None) -> bool:
    q: dict[str, Any] = {"task_id": str(task_id)}
    if user_id is not None:
        q["user_id"] = int(user_id)
    res = await tasks_col().delete_one(q)
    return bool(getattr(res, "deleted_count", 0))


# ─────────────────────────── Audit Trail ───────────────────────────
async def log_audit(
    actor_id: int,
    action: str,
    *,
    actor_role: str = "admin",
    target: str = "",
    outcome: str = "ok",
    detail: str = "",
    surface: str = "webapp",
) -> dict[str, Any]:
    """Record an administrative or security-sensitive action (never include tokens/secrets in `detail`)."""
    now = time.time()
    doc = {
        "audit_id": f"aud_{int(now * 1000)}_{secrets.token_hex(3)}",
        "actor_id": int(actor_id),
        "actor_role": str(actor_role)[:24],
        "action": str(action)[:64],
        "target": str(target)[:160],
        "outcome": str(outcome)[:32],
        "detail": str(detail)[:300],
        "surface": str(surface)[:24],
        "created_at": utcnow_iso(),
        "created_ts": now,
    }
    await audit_col().insert_one(dict(doc))
    return doc


async def list_audit_logs(*, limit: int = 100, actor_id: int | None = None, action_filter: str = "") -> list[dict[str, Any]]:
    q: dict[str, Any] = {}
    if actor_id is not None:
        q["actor_id"] = int(actor_id)
    if action_filter:
        q["action"] = action_filter
    cur = audit_col().find(q).sort("created_ts", -1).limit(max(1, min(500, int(limit))))
    out = []
    async for row in cur:
        row.pop("_id", None)
        out.append(row)
    return out


# ─────────────────────────── Storage Credentials Vault ───────────────────────────
def _seal_secret(raw: str) -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    if security.is_encryption_enabled():
        return "enc:" + security.encrypt_token(val)
    return val


def _unseal_secret(stored: str) -> str:
    val = str(stored or "")
    if not val:
        return ""
    if val.startswith("enc:"):
        try:
            return security.decrypt_token(val[4:])
        except Exception:
            return ""
    return val


async def save_storage_connection(
    user_id: int,
    provider: str,
    *,
    access_token: str = "",
    root_folder: str = "",
    label: str = "",
) -> dict[str, Any]:
    """Save per-user cloud provider token (encrypted at rest when `ENCRYPTION_KEY` is configured)."""
    prov = provider.strip().lower()
    if prov not in ("dropbox", "onedrive", "gdrive", "rclone", "gofile"):
        raise ValueError(f"Unsupported storage provider '{provider}'.")
    now = time.time()
    sealed = _seal_secret(access_token)
    doc = {
        "user_id": int(user_id),
        "provider": prov,
        "token_sealed": sealed,
        "has_token": bool(sealed),
        "root_folder": str(root_folder or "").strip()[:200],
        "label": str(label or prov.title())[:64],
        "connected_at": utcnow_iso(),
        "updated_ts": now,
        "last_ok_at": None,
        "last_error": "",
    }
    await connections_col().update_one(
        {"user_id": int(user_id), "provider": prov},
        {"$set": doc},
        upsert=True,
    )
    return mask_connection_doc(doc)


async def remove_storage_connection(user_id: int, provider: str) -> bool:
    res = await connections_col().delete_one({"user_id": int(user_id), "provider": provider.strip().lower()})
    return bool(getattr(res, "deleted_count", 0))


async def get_provider_token(user_id: int, provider: str) -> tuple[str, dict[str, Any]]:
    """Return `(plaintext_token, config_dict)` for internal adapter calls ONLY (never serialize to client!)."""
    prov = provider.strip().lower()
    row = await connections_col().find_one({"user_id": int(user_id), "provider": prov})
    if row and row.get("token_sealed"):
        return _unseal_secret(row["token_sealed"]), {
            "folder": str(row.get("root_folder") or ""),
            "folder_id": str(row.get("root_folder") or ""),
            "label": str(row.get("label") or ""),
        }
    env_map = {
        "dropbox": getattr(config, "DROPBOX_ACCESS_TOKEN", "") or "",
        "onedrive": getattr(config, "ONEDRIVE_ACCESS_TOKEN", "") or "",
        "gofile": getattr(config, "GOFILE_TOKEN", "") or "",
    }
    return str(env_map.get(prov, "")).strip(), {}


async def record_provider_outcome(user_id: int, provider: str, *, ok: bool, error: str = "") -> None:
    prov = provider.strip().lower()
    upd: dict[str, Any] = {"updated_ts": time.time()}
    if ok:
        upd["last_ok_at"] = utcnow_iso()
        upd["last_error"] = ""
    else:
        upd["last_error"] = str(error or "")[:200]
    await connections_col().update_one({"user_id": int(user_id), "provider": prov}, {"$set": upd})


def mask_connection_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out = dict(doc)
    out.pop("_id", None)
    out.pop("token_sealed", None)
    out["authenticated"] = bool(doc.get("has_token") or doc.get("token_sealed"))
    return out


async def list_user_connections(user_id: int) -> dict[str, dict[str, Any]]:
    cur = connections_col().find({"user_id": int(user_id)})
    out: dict[str, dict[str, Any]] = {}
    async for row in cur:
        out[row["provider"]] = mask_connection_doc(row)
    return out


# ─────────────────────────── Download & Upload History ───────────────────────────
async def check_duplicate_download(
    user_id: int, *, url: str = "", source_url: str = "", sha256: str = ""
) -> dict[str, Any] | None:
    """Return most recent matching completed download entry for `user_id` if URL or SHA-256 matches."""
    uid = int(user_id)
    src = source_url or url
    if sha256:
        hit = await dl_history_col().find_one({"user_id": uid, "sha256": sha256, "state": "completed"})
        if hit:
            hit.pop("_id", None)
            return hit
    if src:
        hit = await dl_history_col().find_one({"user_id": uid, "source_url": src, "state": "completed"})
        if hit:
            hit.pop("_id", None)
            return hit
    return None


async def record_download_history(
    *,
    user_id: int,
    provider: str,
    filename: str,
    size_bytes: int,
    url: str = "",
    source_url: str = "",
    task_id: str = "",
    state: str = "",
    status: str = "",
    sha256: str = "",
    destination: str = "local",
    result_link: str = "",
    remote_url: str = "",
    local_path: str = "",
    error: str = "",
) -> dict[str, Any]:
    now = time.time()
    st = state or status or "completed"
    entry = {
        "entry_id": f"dlh_{int(now * 1000)}_{secrets.token_hex(3)}",
        "task_id": task_id,
        "user_id": int(user_id),
        "provider": provider,
        "source_url": str(source_url or url)[:1000],
        "filename": str(filename)[:240],
        "size_bytes": int(size_bytes or 0),
        "sha256": str(sha256 or ""),
        "destination": destination,
        "result_link": str(result_link or remote_url)[:500],
        "local_path": str(local_path)[:260],
        "state": st,
        "error": str(error or "")[:240],
        "created_at": utcnow_iso(),
        "created_ts": now,
    }
    await dl_history_col().insert_one(dict(entry))
    return entry


async def record_upload_history(
    *,
    user_id: int,
    destination: str,
    filename: str,
    size_bytes: int,
    task_id: str = "",
    state: str = "",
    status: str = "",
    result_link: str = "",
    remote_url: str = "",
    error: str = "",
) -> dict[str, Any]:
    now = time.time()
    st = state or status or "completed"
    entry = {
        "entry_id": f"ulh_{int(now * 1000)}_{secrets.token_hex(3)}",
        "task_id": task_id,
        "user_id": int(user_id),
        "destination": destination,
        "filename": str(filename)[:240],
        "size_bytes": int(size_bytes or 0),
        "result_link": str(result_link or remote_url)[:500],
        "state": st,
        "error": str(error or "")[:240],
        "created_at": utcnow_iso(),
        "created_ts": now,
    }
    await ul_history_col().insert_one(dict(entry))
    return entry


async def query_history(
    kind: str,
    *,
    user_id: int | None,
    search: str = "",
    state: str = "",
    provider: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    col = ul_history_col() if kind == "upload" else dl_history_col()
    q: dict[str, Any] = {}
    if user_id is not None:
        q["user_id"] = int(user_id)
    if state:
        q["state"] = state
    if provider:
        q["destination" if kind == "upload" else "provider"] = provider
    cur = col.find(q).sort("created_ts", -1)
    needle = (search or "").strip().lower()
    matched: list[dict[str, Any]] = []
    async for row in cur:
        row.pop("_id", None)
        if needle:
            hay = f"{row.get('filename', '')} {row.get('source_url', '')} {row.get('provider', '')} {row.get('destination', '')}".lower()
            if needle not in hay:
                continue
        matched.append(row)
    total = len(matched)
    lim = max(1, min(200, int(limit)))
    off = max(0, int(offset))
    return {"total": total, "items": matched[off : off + lim]}


async def delete_history_entry(kind: str, entry_id: str, *, user_id: int | None) -> bool:
    col = ul_history_col() if kind == "upload" else dl_history_col()
    q: dict[str, Any] = {"entry_id": str(entry_id)}
    if user_id is not None:
        q["user_id"] = int(user_id)
    res = await col.delete_one(q)
    return bool(getattr(res, "deleted_count", 0))


async def clear_completed_history(kind: str, *, user_id: int | None) -> int:
    col = ul_history_col() if kind == "upload" else dl_history_col()
    q: dict[str, Any] = {"state": "completed"}
    if user_id is not None:
        q["user_id"] = int(user_id)
    res = await col.delete_many(q)
    return int(getattr(res, "deleted_count", 0))


# ─────────────────────────── Scheduled Jobs (Downloads & Group Announcements) ───────────────────────────
async def create_scheduled_job(
    *,
    user_id: int,
    kind: str,
    target: str,
    run_at_ts: float,
    interval_minutes: int = 0,
    destination: str = "local",
    title: str = "",
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if kind not in ("download", "group_announcement"):
        raise ValueError("Unsupported scheduled job kind.")
    iv = max(0, min(10080, int(interval_minutes or 0)))
    now = time.time()
    doc = {
        "job_id": f"sch_{secrets.token_hex(6)}",
        "user_id": int(user_id),
        "kind": kind,
        "title": str(title or target)[:140],
        "target": str(target)[:1000],
        "destination": str(destination or "local")[:32],
        "interval_minutes": iv,
        "next_run_ts": float(run_at_ts),
        "enabled": True,
        "runs": 0,
        "last_run_ts": 0.0,
        "last_status": "scheduled",
        "options": dict(options or {}),
        "created_at": utcnow_iso(),
        "created_ts": now,
    }
    await schedules_col().insert_one(dict(doc))
    return doc


async def list_scheduled_jobs(*, user_id: int | None = None) -> list[dict[str, Any]]:
    q: dict[str, Any] = {}
    if user_id is not None:
        q["user_id"] = int(user_id)
    cur = schedules_col().find(q).sort("next_run_ts", 1).limit(200)
    out = []
    async for row in cur:
        row.pop("_id", None)
        out.append(row)
    return out


async def toggle_scheduled_job(job_id: str, enabled: bool, *, user_id: int | None = None) -> bool:
    q: dict[str, Any] = {"job_id": str(job_id)}
    if user_id is not None:
        q["user_id"] = int(user_id)
    res = await schedules_col().update_one(q, {"$set": {"enabled": bool(enabled)}})
    return bool(getattr(res, "modified_count", 0))


async def delete_scheduled_job(job_id: str, *, user_id: int | None = None) -> bool:
    q: dict[str, Any] = {"job_id": str(job_id)}
    if user_id is not None:
        q["user_id"] = int(user_id)
    res = await schedules_col().delete_one(q)
    return bool(getattr(res, "deleted_count", 0))


async def due_scheduled_jobs(now_ts: float) -> list[dict[str, Any]]:
    cur = schedules_col().find({"enabled": True, "next_run_ts": {"$lte": float(now_ts)}}).limit(50)
    out = []
    async for row in cur:
        row.pop("_id", None)
        out.append(row)
    return out


async def mark_scheduled_job_ran(
    job_id: str, *, status: str, interval_minutes: int, now: float
) -> None:
    if interval_minutes > 0:
        upd = {
            "$set": {
                "last_run_ts": float(now),
                "last_status": str(status)[:200],
                "next_run_ts": float(now) + interval_minutes * 60.0,
            },
            "$inc": {"runs": 1},
        }
    else:
        upd = {
            "$set": {
                "last_run_ts": float(now),
                "last_status": str(status)[:200],
                "enabled": False,
            },
            "$inc": {"runs": 1},
        }
    await schedules_col().update_one({"job_id": str(job_id)}, upd)


# ─────────────────────────── Webapp Settings (11 Sections) ───────────────────────────
def _deep_merge_defaults(saved: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for sec, defaults in DEFAULT_WEBAPP_SETTINGS.items():
        cur_sec = dict(defaults)
        raw_sec = saved.get(sec)
        if isinstance(raw_sec, dict):
            for k, dv in defaults.items():
                if k in raw_sec:
                    cur_sec[k] = raw_sec[k]
        merged[sec] = cur_sec
    return merged


def validate_webapp_settings_patch(patch: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate incoming settings patch against schema types and safe numeric bounds."""
    if not isinstance(patch, dict):
        raise ValueError("Settings payload must be a JSON object.")
    validated: dict[str, dict[str, Any]] = {}
    for sec, values in patch.items():
        if sec not in DEFAULT_WEBAPP_SETTINGS or not isinstance(values, dict):
            continue
        defaults = DEFAULT_WEBAPP_SETTINGS[sec]
        clean_sec: dict[str, Any] = {}
        for k, val in values.items():
            if k not in defaults:
                continue
            dv = defaults[k]
            if isinstance(dv, bool):
                if not isinstance(val, bool):
                    raise ValueError(f"{sec}.{k} must be a boolean.")
                clean_sec[k] = val
            elif isinstance(dv, int):
                if isinstance(val, bool) or not isinstance(val, (int, float)):
                    raise ValueError(f"{sec}.{k} must be an integer.")
                iv = int(val)
                if k == "max_concurrent" and not (1 <= iv <= 10):
                    raise ValueError("downloads.max_concurrent must be between 1 and 10.")
                if k == "bandwidth_kbps" and not (0 <= iv <= 1_000_000):
                    raise ValueError("downloads.bandwidth_kbps must be between 0 and 1000000.")
                if k == "max_file_mb" and not (1 <= iv <= 10240):
                    raise ValueError("downloads.max_file_mb must be between 1 and 10240.")
                if k == "video_crf" and not (12 <= iv <= 40):
                    raise ValueError("media.video_crf must be between 12 and 40.")
                if k == "image_quality" and not (10 <= iv <= 100):
                    raise ValueError("media.image_quality must be between 10 and 100.")
                if k == "quota_alert_pct" and not (10 <= iv <= 99):
                    raise ValueError("storage.quota_alert_pct must be between 10 and 99.")
                if k == "auto_retry_count" and not (0 <= iv <= 10):
                    raise ValueError("performance.auto_retry_count must be between 0 and 10.")
                if k == "session_timeout_hours" and not (1 <= iv <= 168):
                    raise ValueError("security.session_timeout_hours must be between 1 and 168.")
                clean_sec[k] = iv
            elif isinstance(dv, str):
                if not isinstance(val, str) or len(val) > 80:
                    raise ValueError(f"{sec}.{k} must be a short string.")
                if k == "theme" and val not in ("dark", "light", "system"):
                    raise ValueError("appearance.theme must be dark, light, or system.")
                if k == "file_view" and val not in ("list", "grid"):
                    raise ValueError("appearance.file_view must be list or grid.")
                if k == "duplicate_action" and val not in ("warn", "allow", "skip"):
                    raise ValueError("downloads.duplicate_action must be warn, allow, or skip.")
                if k in ("default_destination", "default_provider") and val not in (
                    "local", "telegram", "gdrive", "dropbox", "onedrive", "rclone", "gofile"
                ):
                    raise ValueError(f"{sec}.{k} specifies an unsupported destination.")
                clean_sec[k] = val
            elif isinstance(dv, list):
                if not isinstance(val, list) or len(val) > 40 or not all(isinstance(x, str) and len(x) <= 24 for x in val):
                    raise ValueError(f"{sec}.{k} must be a list of short extension strings.")
                clean_sec[k] = [x.strip().lower() for x in val if x.strip()]
        if clean_sec:
            validated[sec] = clean_sec
    return validated


async def get_webapp_settings(user_id: int, *, use_cache: bool = True) -> dict[str, Any]:
    uid = int(user_id)
    now = time.monotonic()
    hit = _settings_cache.get(uid)
    if use_cache and hit and (now - hit[0]) < _SETTINGS_TTL:
        return hit[1]
    raw = await webapp_settings_col().find_one({"_id": uid}) or {}
    merged = _deep_merge_defaults(raw)
    _settings_cache[uid] = (now, merged)
    return merged


async def update_webapp_settings(user_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    """Persist only supplied, validated fields; unrelated settings are not overwritten."""
    uid = int(user_id)
    validated = validate_webapp_settings_patch(patch)
    updates = {f"{section}.{key}": value for section, fields in validated.items() for key, value in fields.items()}
    updates["updated_at"] = utcnow_iso()
    collection = webapp_settings_col()
    try:
        await collection.update_one({"_id": uid}, {"$set": updates}, upsert=True)
    finally:
        # Even an interrupted or failed write can have an uncertain outcome.
        _settings_cache.pop(uid, None)
    # Read the stored result rather than reconstructing it from a possibly stale
    # cache. This does not fence concurrent same-field updates or other workers.
    raw = await collection.find_one({"_id": uid}) or {}
    return _deep_merge_defaults(raw)


async def reset_webapp_settings(user_id: int) -> dict[str, Any]:
    uid = int(user_id)
    fresh = _deep_merge_defaults({})
    await webapp_settings_col().update_one(
        {"_id": uid},
        {"$set": {**fresh, "updated_at": utcnow_iso()}},
        upsert=True,
    )
    _settings_cache[uid] = (time.monotonic(), fresh)
    return fresh

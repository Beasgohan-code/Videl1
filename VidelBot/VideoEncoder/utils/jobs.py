"""
Running encoder & transfer jobs – unified live telemetry and hard cancellation.

The original cancel only flipped a flag in status.json that nothing read, so ffmpeg kept
running until the end. Every active download, ffmpeg encode, split, Google Drive transfer,
and Telegram upload registers here:
  • live progress (`pct`, `processed`, `total`, `speed`, `speed_label`, `eta`, `engine`, `stage`)
    feeds `/status`, `/queue`, and the `/admin` web console without extra disk polling,
  • cancelling terminates child subprocesses (`SIGTERM` → `SIGKILL` after grace) and signals
    chunked upload/download loops via `jobs.is_cancelled(key)`.
"""
import asyncio
import time
from collections import deque

_JOBS: dict = {}              # status-message id → Job
_RECENT = deque(maxlen=200)   # keys cancelled lately (the job may already be unregistered)


class Job:
    __slots__ = (
        "key",
        "user_id",
        "chat_id",
        "proc",
        "started",
        "cancelled",
        "stage",
        "name",
        "engine",
        "mode",
        "progress",
        "processed",
        "total",
        "speed",
        "speed_label",
        "eta",
        "updated_at",
    )

    def __init__(self, key, user_id, chat_id, name="", stage="encode", engine="", mode=""):
        self.key = key
        self.user_id = user_id
        self.chat_id = chat_id
        self.name = name
        self.stage = stage
        self.engine = engine
        self.mode = mode
        self.proc = None
        self.started = time.time()
        self.cancelled = False
        self.progress = 0.0
        self.processed = 0
        self.total = 0
        self.speed = 0.0
        self.speed_label = ""
        self.eta = 0.0
        self.updated_at = self.started

    def to_dict(self) -> dict:
        now = time.time()
        return {
            "key": self.key,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "name": self.name or "",
            "stage": self.stage or "running",
            "engine": self.engine or "",
            "mode": self.mode or "",
            "progress": round(float(self.progress or 0.0), 1),
            "processed": int(self.processed or 0),
            "total": int(self.total or 0),
            "speed": float(self.speed or 0.0),
            "speed_label": self.speed_label or "",
            "eta": max(0, int(round(self.eta or 0))),
            "elapsed": max(0, int(now - self.started)),
            "cancelled": bool(self.cancelled),
        }


def register(key, user_id, chat_id, name="", stage="encode", *, engine="", mode="") -> Job:
    job = _JOBS.get(key)
    if job is None:
        job = _JOBS[key] = Job(key, user_id, chat_id, name, stage, engine=engine, mode=mode)
    else:
        job.stage = stage
        if name:
            job.name = name
        if engine:
            job.engine = engine
        if mode:
            job.mode = mode
        job.updated_at = time.time()
    return job


def update_progress(
    key,
    *,
    current: int | None = None,
    total: int | None = None,
    pct: float | None = None,
    speed: float | None = None,
    speed_label: str | None = None,
    eta: float | None = None,
    stage: str | None = None,
    engine: str | None = None,
    name: str | None = None,
):
    """Update live transfer/encode metrics on a registered job (no-op if unregistered)."""
    job = _JOBS.get(key)
    if job is None:
        return None
    if current is not None:
        job.processed = max(0, int(current))
    if total is not None:
        job.total = max(0, int(total))
    if pct is not None:
        job.progress = max(0.0, min(100.0, float(pct)))
    elif job.total > 0 and current is not None:
        job.progress = max(0.0, min(100.0, job.processed * 100.0 / job.total))
    if speed is not None:
        job.speed = max(0.0, float(speed))
        if speed_label is None:
            job.speed_label = ""
    if speed_label is not None:
        job.speed_label = str(speed_label)
    if eta is not None:
        job.eta = max(0.0, float(eta))
    if stage:
        job.stage = stage
    if engine:
        job.engine = engine
    if name:
        job.name = name
    job.updated_at = time.time()
    return job


def attach(key, proc):
    job = _JOBS.get(key)
    if job:
        job.proc = proc
        if job.cancelled:
            _terminate_now(proc)
    return job


def get(key):
    return _JOBS.get(key)


def is_cancelled(key) -> bool:
    job = _JOBS.get(key)
    return bool(job and job.cancelled) or key in _RECENT


def unregister(key):
    _JOBS.pop(key, None)


def active() -> list:
    return list(_JOBS.values())


def snapshot() -> dict:
    """Return `{key: job.to_dict()}` snapshot of all currently running jobs."""
    return {k: j.to_dict() for k, j in list(_JOBS.items())}


def latest():
    return next(reversed(_JOBS.values()), None) if _JOBS else None


def _terminate_now(proc):
    try:
        if proc is not None and proc.returncode is None:
            proc.terminate()
    except (ProcessLookupError, OSError):
        pass


async def cancel(key, grace: float = 5.0) -> bool:
    """Mark the job cancelled and stop its subprocess (`SIGTERM` → `SIGKILL`)."""
    job = _JOBS.get(key)
    if not job:
        return False
    job.cancelled = True
    _RECENT.append(key)
    proc = job.proc
    _terminate_now(proc)
    if proc is not None:
        try:
            await asyncio.wait_for(proc.wait(), grace)
        except (asyncio.TimeoutError, AttributeError):
            try:
                proc.kill()
            except (ProcessLookupError, OSError, AttributeError):
                pass
        except Exception:
            pass
    return True


# ─────────────────────────── restarts ───────────────────────────
def kill_all() -> int:
    """SIGKILL every running ffmpeg. Called right before the process re-execs itself (/restart, watchdog):
    os.exec* keeps child processes alive, so the old encodes kept eating the CPU while the restored queue
    started the very same encodes again – a duplicate *and* a slowdown."""
    n = 0
    for job in list(_JOBS.values()):
        proc = job.proc
        try:
            if proc is not None and proc.returncode is None:
                proc.kill()
                n += 1
        except (ProcessLookupError, OSError):
            pass
    return n


def reap_orphans(names=("ffmpeg", "ffprobe", "mkvextract"), proc_root: str = "/proc") -> int:
    """At startup: kill media tools that are still our children (left over from before an exec restart)."""
    import os
    import signal
    me, n = os.getpid(), 0
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return 0
    for pid in pids:
        try:
            with open(os.path.join(proc_root, pid, "stat")) as f:
                stat = f.read()
            comm = stat[stat.index("(") + 1:stat.rindex(")")]
            ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        if ppid == me and comm in names:
            try:
                os.kill(int(pid), signal.SIGKILL)
                n += 1
            except OSError:
                pass
    return n

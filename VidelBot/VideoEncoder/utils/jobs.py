"""
Running encoder & transfer jobs – unified live telemetry, pause/resume, and hard cancellation.

Every active VideoEncoder task, Google Drive transfer, Rclone/GoFile upload, Auto-Rename job,
and Content Saver batch registers here:
  • live progress (`pct`, `processed`, `total`, `speed`, `speed_label`, `eta`, `engine`, `stage`, `paused`)
    feeds `/status`, `/queue`, and the `/admin` web console without extra disk polling,
  • `pause(key)` / `resume(key)` suspends and resumes active POSIX child subprocesses (`SIGSTOP` / `SIGCONT`)
    and async chunked transfer loops (`wait_if_paused(key)`),
  • `cancel(key)` resumes any `SIGSTOP`ped subprocess before sending `SIGTERM` → `SIGKILL`, triggers optional
    `on_cancel` hooks for subsystem state flags, and marks the job cancelled in `_RECENT`.
"""
import asyncio
import os
import signal
import time
from collections import deque

_JOBS: dict = {}              # status-message id (or subsystem negative key) → Job
_RECENT = deque(maxlen=200)   # keys cancelled lately (the job may already be unregistered)


def _sig_if_supported(name: str):
    return getattr(signal, name, None)


_SIGSTOP = _sig_if_supported("SIGSTOP")
_SIGCONT = _sig_if_supported("SIGCONT")


class Job:
    __slots__ = (
        "key",
        "user_id",
        "chat_id",
        "proc",
        "started",
        "cancelled",
        "paused",
        "_pause_ev",
        "on_cancel",
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

    def __init__(self, key, user_id, chat_id, name="", stage="encode", engine="", mode="", on_cancel=None):
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
        self.paused = False
        self._pause_ev = asyncio.Event()
        self._pause_ev.set()
        self.on_cancel = on_cancel
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
            "paused": bool(self.paused),
        }


def register(key, user_id, chat_id, name="", stage="encode", *, engine="", mode="", on_cancel=None) -> Job:
    job = _JOBS.get(key)
    if job is None:
        job = _JOBS[key] = Job(key, user_id, chat_id, name, stage, engine=engine, mode=mode, on_cancel=on_cancel)
    else:
        job.stage = stage
        if name:
            job.name = name
        if engine:
            job.engine = engine
        if mode:
            job.mode = mode
        if on_cancel is not None:
            job.on_cancel = on_cancel
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
        elif job.paused and _SIGSTOP is not None:
            _signal_proc(proc, _SIGSTOP)
    return job


def get(key):
    return _JOBS.get(key)


def is_cancelled(key) -> bool:
    job = _JOBS.get(key)
    return bool(job and job.cancelled) or key in _RECENT


def is_paused(key) -> bool:
    job = _JOBS.get(key)
    return bool(job and job.paused and not job.cancelled)


def _signal_proc(proc, sig) -> bool:
    if proc is None or sig is None or getattr(proc, "returncode", None) is not None:
        return False
    try:
        if hasattr(proc, "send_signal"):
            proc.send_signal(sig)
            return True
        pid = getattr(proc, "pid", None)
        if pid:
            os.kill(int(pid), sig)
            return True
    except (ProcessLookupError, OSError, ValueError):
        pass
    return False


def pause(key) -> bool:
    """Pause a running job (`SIGSTOP` on attached subprocess + async pause gate)."""
    job = _JOBS.get(key)
    if not job or job.cancelled:
        return False
    if job.paused:
        return True
    job.paused = True
    job._pause_ev.clear()
    job.updated_at = time.time()
    if _SIGSTOP is not None:
        _signal_proc(job.proc, _SIGSTOP)
    return True


def resume(key) -> bool:
    """Resume a paused job (`SIGCONT` on attached subprocess + open async pause gate)."""
    job = _JOBS.get(key)
    if not job or job.cancelled:
        return False
    job.paused = False
    job._pause_ev.set()
    job.updated_at = time.time()
    if _SIGCONT is not None:
        _signal_proc(job.proc, _SIGCONT)
    return True


async def wait_if_paused(key, max_wait: float | None = None) -> bool:
    """Block while `key` is paused; returns `False` if the job was cancelled while paused."""
    job = _JOBS.get(key)
    if not job or not job.paused:
        return not is_cancelled(key)
    try:
        if max_wait is not None:
            await asyncio.wait_for(job._pause_ev.wait(), timeout=max_wait)
        else:
            await job._pause_ev.wait()
    except asyncio.TimeoutError:
        pass
    return not is_cancelled(key)


def unregister(key):
    job = _JOBS.pop(key, None)
    if job is not None:
        job.paused = False
        job._pause_ev.set()


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
            if _SIGCONT is not None:
                _signal_proc(proc, _SIGCONT)
            proc.terminate()
    except (ProcessLookupError, OSError):
        pass


async def cancel(key, grace: float = 5.0) -> bool:
    """Mark the job cancelled, wake any paused gate, run `on_cancel`, and stop its subprocess (`SIGTERM` → `SIGKILL`)."""
    job = _JOBS.get(key)
    if not job:
        return False
    job.cancelled = True
    job.paused = False
    job._pause_ev.set()
    _RECENT.append(key)
    if callable(job.on_cancel):
        try:
            res = job.on_cancel()
            if asyncio.iscoroutine(res):
                await res
        except Exception:
            pass
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
    """SIGKILL every running media/transfer subprocess before exec restart."""
    n = 0
    for job in list(_JOBS.values()):
        job.paused = False
        job._pause_ev.set()
        proc = job.proc
        try:
            if proc is not None and proc.returncode is None:
                if _SIGCONT is not None:
                    _signal_proc(proc, _SIGCONT)
                proc.kill()
                n += 1
        except (ProcessLookupError, OSError):
            pass
    return n


def reap_orphans(names=("ffmpeg", "ffprobe", "mkvextract", "rclone"), proc_root: str = "/proc") -> int:
    """At startup: kill media/transfer tools that are still our children (left over from before an exec restart)."""
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

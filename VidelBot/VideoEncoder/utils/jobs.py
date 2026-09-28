"""
Running encoder jobs – lets ❌ Cancel really stop ffmpeg.

The original cancel only flipped a flag in status.json that nothing read, so ffmpeg kept
running until the end. Every ffmpeg process is registered here; cancelling terminates it
(then kills it after a grace period) and the encode returns cleanly.
"""
import asyncio
import time
from collections import deque

_JOBS: dict = {}          # status-message id → Job
_RECENT = deque(maxlen=200)   # keys cancelled lately (the job may already be unregistered)


class Job:
    __slots__ = ("key", "user_id", "chat_id", "proc", "started", "cancelled", "stage", "name")

    def __init__(self, key, user_id, chat_id, name="", stage="encode"):
        self.key, self.user_id, self.chat_id, self.name, self.stage = key, user_id, chat_id, name, stage
        self.proc = None
        self.started = time.time()
        self.cancelled = False


def register(key, user_id, chat_id, name="", stage="encode") -> Job:
    job = _JOBS.get(key)
    if job is None:
        job = _JOBS[key] = Job(key, user_id, chat_id, name, stage)
    else:
        job.stage = stage
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


def latest():
    return next(reversed(_JOBS.values()), None) if _JOBS else None


def _terminate_now(proc):
    try:
        if proc is not None and proc.returncode is None:
            proc.terminate()
    except (ProcessLookupError, OSError):
        pass


async def cancel(key, grace: float = 5.0) -> bool:
    """Mark the job cancelled and stop its ffmpeg (SIGTERM → SIGKILL)."""
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

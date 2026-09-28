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

"""
Fire-and-forget tasks that can't vanish.

asyncio keeps only a *weak* reference to a task – a bare `asyncio.create_task(...)` whose result nobody
stores can be garbage-collected mid-run (Python docs, "Important" box under create_task), and an exception
inside it is only reported, if ever, as "Task exception was never retrieved" at GC time. spawn() holds a
strong reference until the task ends and logs the traceback of any crash with the task's name.
"""
import asyncio
import logging

log = logging.getLogger("videl.bg")
_TASKS: set = set()


def _done(task: asyncio.Task):
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error(f"background task {task.get_name()!r} crashed: {type(exc).__name__}: {exc}",
                  exc_info=(type(exc), exc, exc.__traceback__))


def track(task: asyncio.Task) -> asyncio.Task:
    """Hold + watch a task that was created elsewhere (e.g. on an explicit loop)."""
    _TASKS.add(task)
    task.add_done_callback(_done)
    return task


def spawn(coro, name: str | None = None) -> asyncio.Task:
    return track(asyncio.get_event_loop().create_task(coro, name=name))


def running() -> int:
    return len(_TASKS)


def trim(d: dict, limit: int, removable=None) -> int:
    """Keep a per-user in-memory dict from growing forever on a long-running server.

    When `d` holds more than `limit` keys, the oldest-inserted ones are dropped until it is back to half of
    `limit`. `removable(key, value)` can veto dropping an entry that is still in use. Returns how many were dropped.
    """
    if len(d) <= limit:
        return 0
    target, dropped = limit // 2, 0
    for k in list(d):
        if len(d) <= target:
            break
        if removable is None or removable(k, d[k]):
            d.pop(k, None)
            dropped += 1
    return dropped

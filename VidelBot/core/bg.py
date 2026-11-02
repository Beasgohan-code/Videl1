"""
Background-task helper and bounded-dict trimmer.

Python only keeps weak references to `asyncio.create_task(...)` – an unreferenced task
can be garbage-collected mid-flight, and an unhandled exception inside it only prints at
shutdown (if at all). `spawn()` holds a strong reference until the task finishes, logs
any crash with its name, and allows clean `cancel_all()` draining at shutdown.
"""
import asyncio
import logging

log = logging.getLogger("videl.bg")
_TASKS: set[asyncio.Task] = set()


def _done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("background task %r crashed: %s", task.get_name(), exc, exc_info=exc)


def spawn(coro, *, name: str = "bg") -> asyncio.Task:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    task = loop.create_task(coro)
    try:
        task.set_name(name)
    except AttributeError:
        pass
    _TASKS.add(task)
    task.add_done_callback(_done)
    return task


def running() -> int:
    return len(_TASKS)


def active_names() -> list[str]:
    return sorted(t.get_name() for t in _TASKS if not t.done())


async def cancel_all(timeout: float = 5.0) -> int:
    """Cancel and drain all tracked background tasks cleanly during shutdown."""
    pending = [t for t in list(_TASKS) if not t.done()]
    if not pending:
        _TASKS.clear()
        return 0
    for task in pending:
        task.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)
    except asyncio.TimeoutError:
        log.warning("timed out cancelling %d background task(s)", sum(1 for t in pending if not t.done()))
    finally:
        for task in pending:
            if task.done():
                _TASKS.discard(task)
    return len(pending)


def trim(mapping: dict, limit: int, droppable=None) -> int:
    """If `mapping` grew past `limit`, drop oldest entries down to `limit // 2`.

    `droppable(key, value) -> bool` can veto removal (e.g. a held lock or live client).
    """
    if len(mapping) <= limit:
        return 0
    target = max(1, limit // 2)
    dropped = 0
    for k in list(mapping):
        if len(mapping) <= target:
            break
        if droppable is not None and not droppable(k, mapping[k]):
            continue
        mapping.pop(k, None)
        dropped += 1
    return dropped

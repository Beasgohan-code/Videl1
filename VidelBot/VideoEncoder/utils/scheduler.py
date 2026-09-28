"""
Encoder scheduler: parallel workers, premium priority, per-user limits, restart-proof queue.

`VideoEncoder.data` stays THE queue (status / queue / watchdog / dashboard read it). What changed:

  • ENCODER_WORKERS tasks run at the same time (default 1). The running ones are tracked here,
    so a finished task removes *itself* – not blindly data[0].
  • 🎬 Encoder Pro / 💎 Premium / admins jump ahead of free users' waiting tasks (FIFO inside each group).
  • per-user cap: ENC_MAX_TASKS_FREE / ENC_MAX_TASKS_PRO queued + running tasks.
  • every task gets its own folders (downloads/<chat>_<msg>/, encodes/<chat>_<msg>/), so parallel
    tasks never delete each other's files.
  • the queue is mirrored to Mongo (collection `enc_queue`); after a restart restore_queue()
    fetches the original messages again and re-queues them in order.
"""
import asyncio
import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta

import config

from .. import data, download_dir, encode_dir

log = logging.getLogger("VideoEncoder.scheduler")

_running: list = []
MODES: dict = {}         # id(message) → mode
EXTRA: dict = {}         # id(message) → extra dict (mux track, merge parts, convert format …)
PRIO: set = set()        # id(message) of priority tasks
SRC: dict = {}           # id(message) → source signature (duplicate guard)
NOTES: dict = {}         # id(message) → its "Added to the queue" message, reused as the status card

COMMAND_MODES = {"/ddl": "url", "/batch": "batch", "/sample": "sample", "/trim": "trim", "/screens": "screens",
                 "/dl": "tg", "/af": "af", "/mux": "mux", "/merge": "merge", "/convert": "convert",
                 "/leech": "leech"}
MAX_RESTORE_AGE = timedelta(hours=48)


# ─────────────────────────── queue state ───────────────────────────
def _in_data(m) -> bool:
    return any(x is m for x in data)


def running() -> list:
    """Running tasks (stale entries – e.g. the queue was cleared – are dropped)."""
    _running[:] = [m for m in _running if _in_data(m)]
    return list(_running)


def is_running(m) -> bool:
    return any(x is m for x in running())


def waiting() -> list:
    run = running()
    return [m for m in data if not any(m is r for r in run)]


def slots_free() -> int:
    return config.ENCODER_WORKERS - len(running())


def next_waiting():
    w = waiting()
    return w[0] if w else None


def can_start(m) -> bool:
    return slots_free() > 0 and next_waiting() is m


def uid_of(m) -> int:
    return getattr(getattr(m, "from_user", None), "id", 0) or 0


def user_tasks(uid: int) -> int:
    return sum(1 for m in data if uid_of(m) == uid)


def signature(message, mode: str, extra: dict | None = None) -> str | None:
    """Same user + same task + same file / link + same arguments → same signature.

    File tasks are keyed by Telegram's file_unique_id (identical for every copy / forward of a file), so
    tapping /dl twice or re-sending the same video doesn't encode it twice. Arguments stay part of the key:
    /trim 0 10 and /trim 20 30 on one file are different tasks. A bare command with no file and no
    arguments has nothing to compare → None (only the exact same message counts as a repeat).
    """
    media = None
    for m in (getattr(message, "reply_to_message", None), message):
        for kind in ("video", "document", "audio"):
            media = getattr(m, kind, None) if m is not None else None
            if media is not None:
                break
        if media is not None:
            break
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    parts = text.split(None, 1) if text.startswith("/") else ["", text]
    args = " ".join(parts[1].split()) if len(parts) > 1 else ""
    unique = getattr(media, "file_unique_id", None) if media is not None else None
    if not isinstance(unique, str):
        unique = None
    if not unique and not args:
        return None
    try:
        ex = json.dumps(extra or {}, sort_keys=True, default=str)
    except Exception:
        ex = repr(extra)
    return f"{uid_of(message)}|{mode}|{unique or '-'}|{args}|{ex}"


def find_duplicate(message, mode: str, extra: dict | None = None) -> tuple:
    """→ (position, same_message). (0, False) when it's new.

    same_message: Telegram re-delivered this very update (it happens after a reconnect) – drop it quietly.
    """
    key, sig = task_key(message), signature(message, mode, extra)
    for i, m in enumerate(data):
        if m is message or (key is not None and task_key(m) == key):
            return i + 1, True
        if sig is not None and SRC.get(id(m)) == sig:
            return i + 1, False
    return 0, False


def add(message, mode: str, *, priority: bool = False, extra: dict | None = None) -> int:
    """Queue a task. Returns its 1-based position in the queue."""
    MODES[id(message)] = mode
    EXTRA[id(message)] = dict(extra or {})
    sig = signature(message, mode, extra)
    if sig is not None:
        SRC[id(message)] = sig
    if priority:
        PRIO.add(id(message))
        run = running()
        for i, m in enumerate(data):
            if not any(m is r for r in run) and id(m) not in PRIO:
                data.insert(i, message)
                return i + 1
    data.append(message)
    return len(data)


def position(message) -> int:
    for i, m in enumerate(data):
        if m is message:
            return i + 1
    return 0


def mark_running(message):
    if not any(x is message for x in _running):
        _running.append(message)


def finish(message):
    """Remove a finished / failed task from the queue."""
    data[:] = [m for m in data if m is not message]
    _running[:] = [m for m in _running if m is not message]
    MODES.pop(id(message), None)
    EXTRA.pop(id(message), None)
    SRC.pop(id(message), None)
    NOTES.pop(id(message), None)
    PRIO.discard(id(message))


def parse_mode(message) -> str | None:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    if text.startswith("/"):
        cmd = text.split(None, 1)[0].lower().split("@")[0]
        if cmd in COMMAND_MODES:
            return COMMAND_MODES[cmd]
    if getattr(message, "video", None) or getattr(message, "document", None):
        return "tg"
    return None


def mode_of(message) -> str | None:
    return MODES.get(id(message)) or parse_mode(message)


def extra_of(message) -> dict:
    return EXTRA.get(id(message)) or {}


# ─────────────────────────── per-task folders ───────────────────────────
def task_key(message) -> str | None:
    chat = getattr(getattr(message, "chat", None), "id", None)
    mid = getattr(message, "id", None)
    return f"{chat}_{mid}" if chat is not None and mid is not None else None


def task_dirs(message, create: bool = True) -> tuple:
    key = task_key(message)
    if not key:
        return download_dir, encode_dir
    dl, enc = os.path.join(download_dir, key), os.path.join(encode_dir, key)
    if create:
        os.makedirs(dl, exist_ok=True)
        os.makedirs(enc, exist_ok=True)
    return dl, enc


def cleanup_task(message):
    key = task_key(message)
    if not key:
        return
    for base in (download_dir, encode_dir):
        shutil.rmtree(os.path.join(base, key), ignore_errors=True)


# ─────────────────────────── persistence ───────────────────────────
def _col():
    from core.db import vdb
    return vdb.db["enc_queue"]


async def persist(message, mode: str, extra: dict | None = None, priority: bool = False):
    if not config.QUEUE_PERSIST:
        return
    key = task_key(message)
    if not key:
        return
    try:
        await _col().update_one({"_id": key}, {"$set": {
            "chat": message.chat.id, "msg": message.id, "user": uid_of(message), "mode": mode,
            "extra": extra or {}, "prio": bool(priority), "ts": datetime.utcnow()}}, upsert=True)
    except Exception as e:
        log.debug(f"queue persist failed: {e}")


async def forget(message):
    key = task_key(message)
    if not key or not config.QUEUE_PERSIST:
        return
    try:
        await _col().delete_one({"_id": key})
    except Exception as e:
        log.debug(f"queue forget failed: {e}")


async def restore_queue(app) -> int:
    """Re-queue the tasks that were waiting / running when the bot stopped. Returns how many."""
    if not config.QUEUE_PERSIST:
        return 0
    try:
        docs = [d async for d in _col().find({}).sort("ts", 1)]
    except Exception as e:
        log.warning(f"queue restore: {e}")
        return 0
    restored = 0
    cutoff = datetime.utcnow() - MAX_RESTORE_AGE
    for d in docs:
        await _col().delete_one({"_id": d["_id"]})
        ts = d.get("ts")
        if ts and getattr(ts, "tzinfo", None):
            ts = ts.replace(tzinfo=None)
        if ts and ts < cutoff:
            continue
        try:
            msg = await app.get_messages(d["chat"], d["msg"])
        except Exception:
            msg = None
        if not msg or getattr(msg, "empty", False):
            continue
        add(msg, d.get("mode") or parse_mode(msg) or "tg", priority=d.get("prio", False), extra=d.get("extra"))
        await persist(msg, d.get("mode"), d.get("extra"), d.get("prio", False))
        restored += 1
        try:
            NOTES[id(msg)] = await msg.reply_text(f"♻️ <b>The bot restarted</b> – your task is back in the queue "
                                                  f"(position <b>#{position(msg)}</b>).", quote=True)
        except Exception:
            pass
        await asyncio.sleep(0.3)
    if restored:
        log.info(f"♻️ restored {restored} encoder task(s)")
        from .tasks import dispatch
        asyncio.create_task(dispatch(spawn_all=True))
    return restored


def snapshot() -> dict:
    """Numbers for /status, the dashboard and the watchdog."""
    return {"workers": config.ENCODER_WORKERS, "running": len(running()), "waiting": len(waiting()),
            "priority_waiting": sum(1 for m in waiting() if id(m) in PRIO), "ts": int(time.time())}

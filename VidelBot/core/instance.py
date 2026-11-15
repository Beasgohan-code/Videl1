"""
One live Videl per bot token (fail-closed MongoDB lease).

With MTProto every running copy of the bot receives every update, so two copies = every /dl encoded
twice, every file uploaded twice, every reply doubled. That happens more than you'd think:
  • Railway / Render / Heroku start the new deploy *before* stopping the old one (zero-downtime),
  • the same token deployed on two hosts (an old Heroku app still running …).

A lease in MongoDB (`runtime.instance_lock`) fixes it:
  • start-up waits while another copy holds a live lease (the keep-alive server is already up, so the
    platform's health check passes and it goes on to stop the old deploy), then takes over when the
    lease is released or stops being renewed (TTL),
  • if MongoDB itself fails repeatedly or `SINGLE_INSTANCE_TIMEOUT` expires before acquisition,
    `acquire()` raises `InstanceLeaseError` (**fail closed**) instead of starting a duplicate bot,
  • a heartbeat renews it every BEAT seconds; if this copy finds the lease taken over OR fails to
    renew continuously past TTL, it exits instead of processing updates twice,
  • shutdown releases it, so the next deploy starts at once; /restart hands it to the re-exec'd process.

SINGLE_INSTANCE=False disables all of this.
"""
import asyncio
import logging
import os
import socket
import uuid
from datetime import datetime, timedelta

log = logging.getLogger("videl.instance")

LOCK_ID = "instance_lock"
TTL = 60                   # seconds a lease stays valid without renewal
BEAT = 15                  # renewal interval
POLL = 5                   # how often a waiting copy re-checks
MAX_DB_ERRORS = 5          # consecutive DB errors before acquire() fails closed
ENV = "VIDEL_INSTANCE_ID"  # survives os.exec* → a /restart keeps its own lease

ME = os.environ.get(ENV) or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
STATE = {"held": False, "waited": 0.0, "other": None, "last_error": None}
_task = None


class InstanceLeaseError(RuntimeError):
    """Raised when `SINGLE_INSTANCE=True` and the MongoDB lease cannot be safely acquired."""


def enabled() -> bool:
    try:
        import config
        return bool(getattr(config, "SINGLE_INSTANCE", True))
    except Exception:
        return True


def _acquire_timeout() -> float:
    try:
        import config
        return float(getattr(config, "SINGLE_INSTANCE_TIMEOUT", 0) or 0)
    except Exception:
        return 0.0


def _col():
    from core.db import vdb
    return vdb.db["runtime"]


def _now():
    return datetime.utcnow()


def _is_duplicate_key(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code == 11000:
        return True
    name = exc.__class__.__name__
    msg = str(exc)
    return name == "DuplicateKeyError" or "E11000" in msg or "duplicate key" in msg.lower()


async def try_acquire(col=None) -> bool:
    """Take the lease if it's free, expired or already ours. Never blocks.

    Propagates genuine MongoDB errors so callers never mistake an outage for normal contention
    or start without holding the lock.
    """
    col = col if col is not None else _col()
    now = _now()
    lease = {
        "owner": ME,
        "until": now + timedelta(seconds=TTL),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "beat": now,
    }
    res = await col.update_one(
        {"_id": LOCK_ID, "$or": [{"until": {"$lt": now}}, {"owner": ME}]},
        {"$set": lease},
    )
    if res.matched_count:
        STATE["held"] = True
        STATE["last_error"] = None
        return True
    try:                                        # no lease document yet
        await col.insert_one({"_id": LOCK_ID, **lease})
        STATE["held"] = True
        STATE["last_error"] = None
        return True
    except Exception as exc:
        if not _is_duplicate_key(exc):
            STATE["held"] = False
            STATE["last_error"] = str(exc)
            raise
        doc = await col.find_one({"_id": LOCK_ID}) or {}
        STATE["held"] = False
        STATE["other"] = {k: doc.get(k) for k in ("owner", "host", "until")}
        return False


async def acquire(
    col=None,
    poll: float = POLL,
    notify=None,
    *,
    max_db_errors: int = MAX_DB_ERRORS,
    timeout: float | None = None,
) -> float:
    """Wait until this copy holds the lease. Fails closed (`InstanceLeaseError`) on DB failure or timeout."""
    if not enabled():
        return 0.0
    loop = asyncio.get_running_loop()
    t0, last_log = loop.time(), -1e9
    effective_timeout = _acquire_timeout() if timeout is None else float(timeout)
    db_errors = 0
    while True:
        try:
            if await try_acquire(col):
                STATE["waited"] = loop.time() - t0
                if STATE["waited"] > 1:
                    log.info(f"🔒 took over the instance lease after {STATE['waited']:.0f}s")
                return STATE["waited"]
            db_errors = 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            db_errors += 1
            STATE["held"] = False
            STATE["last_error"] = str(e)
            if db_errors >= max(1, int(max_db_errors)):
                raise InstanceLeaseError(
                    f"Failed to acquire single-instance lease after {db_errors} MongoDB error(s): {e}"
                ) from e
            backoff = min(max(poll, 0.05) * db_errors, 15.0)
            log.warning(
                f"instance lock DB error ({db_errors}/{max_db_errors}: {e}) – retrying in {backoff:.2f}s"
            )
            await asyncio.sleep(backoff)
            continue

        elapsed = loop.time() - t0
        if effective_timeout > 0 and elapsed >= effective_timeout:
            other = STATE.get("other") or {}
            raise InstanceLeaseError(
                f"Timed out after {elapsed:.1f}s waiting for single-instance lease held by "
                f"{other.get('owner') or 'another instance'} (host {other.get('host') or '?'})"
            )

        if loop.time() - last_log > 60:
            other = STATE.get("other") or {}
            log.warning(
                f"⏳ another Videl is running with this bot token (host {other.get('host')}) – "
                "waiting for it to stop so updates aren't processed twice"
            )
            last_log = loop.time()
            if notify is not None:
                try:
                    await notify(other)
                except Exception:
                    pass
        await asyncio.sleep(poll)


async def renew(col=None) -> bool:
    """Extend our lease. False = someone else owns it now."""
    col = col if col is not None else _col()
    now = _now()
    res = await col.update_one(
        {"_id": LOCK_ID, "owner": ME},
        {"$set": {"until": now + timedelta(seconds=TTL), "beat": now}},
    )
    STATE["held"] = bool(res.matched_count)
    if STATE["held"]:
        STATE["last_error"] = None
    return STATE["held"]


async def release(col=None):
    if not enabled():
        return
    try:
        col = col if col is not None else _col()
        await col.delete_one({"_id": LOCK_ID, "owner": ME})
        STATE["held"] = False
    except Exception as e:
        log.debug(f"instance release: {e}")


async def handover():
    """Before os.exec*: the new process image reuses our id, so it gets the lease back immediately."""
    os.environ[ENV] = ME
    try:
        await renew()
    except Exception:
        pass


async def _heartbeat(on_lost, col=None, beat: float = BEAT, *, max_missed_beats: int | None = None):
    missed_limit = max_missed_beats if max_missed_beats is not None else max(3, int(TTL // max(beat, 1.0)))
    missed = 0
    while True:
        await asyncio.sleep(beat)
        try:
            if not await renew(col):
                log.error("🔒 the instance lease was taken over by another copy – stopping this one")
                await on_lost()
                return
            missed = 0
        except asyncio.CancelledError:
            raise
        except Exception as e:
            missed += 1
            STATE["last_error"] = str(e)
            log.warning(f"instance heartbeat error ({missed}/{missed_limit}): {e}")
            if missed >= missed_limit:
                STATE["held"] = False
                log.error(
                    f"🔒 lost MongoDB connectivity for {missed} consecutive heartbeats – "
                    "failing closed before lease TTL expires"
                )
                await on_lost()
                return


def _exit():
    os._exit(0)


async def _default_lost():
    _exit()


def start_heartbeat(on_lost=None, col=None, beat: float = BEAT):
    global _task
    if not enabled():
        return None
    from core.bg import track
    _task = track(asyncio.create_task(_heartbeat(on_lost or _default_lost, col, beat)), name="instance-heartbeat")
    return _task


def stop_heartbeat():
    global _task
    if _task is not None and not _task.done():
        _task.cancel()
    _task = None

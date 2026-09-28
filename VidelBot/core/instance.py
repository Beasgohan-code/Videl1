"""
One live Videl per bot token.

With MTProto every running copy of the bot receives every update, so two copies = every /dl encoded
twice, every file uploaded twice, every reply doubled. That happens more than you'd think:
  • Railway / Render / Heroku start the new deploy *before* stopping the old one (zero-downtime),
  • the same token deployed on two hosts (an old Heroku app still running …).

A lease in MongoDB (`runtime.instance_lock`) fixes it:
  • start-up waits while another copy holds a live lease (the keep-alive server is already up, so the
    platform's health check passes and it goes on to stop the old deploy), then takes over when the
    lease is released or stops being renewed (TTL),
  • a heartbeat renews it every BEAT seconds; if this copy finds the lease taken over (it was frozen /
    cut off for longer than the TTL) it exits instead of processing updates twice,
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
ENV = "VIDEL_INSTANCE_ID"  # survives os.exec* → a /restart keeps its own lease

ME = os.environ.get(ENV) or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
STATE = {"held": False, "waited": 0.0, "other": None}
_task = None


def enabled() -> bool:
    try:
        import config
        return bool(getattr(config, "SINGLE_INSTANCE", True))
    except Exception:
        return True


def _col():
    from core.db import vdb
    return vdb.db["runtime"]


def _now():
    return datetime.utcnow()


async def try_acquire(col=None) -> bool:
    """Take the lease if it's free, expired or already ours. Never blocks."""
    col = col if col is not None else _col()
    now = _now()
    lease = {"owner": ME, "until": now + timedelta(seconds=TTL), "host": socket.gethostname(),
             "pid": os.getpid(), "beat": now}
    res = await col.update_one({"_id": LOCK_ID, "$or": [{"until": {"$lt": now}}, {"owner": ME}]},
                               {"$set": lease})
    if res.matched_count:
        STATE["held"] = True
        return True
    try:                                        # no lease document yet
        await col.insert_one({"_id": LOCK_ID, **lease})
        STATE["held"] = True
        return True
    except Exception:                           # DuplicateKeyError → someone else holds a live lease
        doc = await col.find_one({"_id": LOCK_ID}) or {}
        STATE["other"] = {k: doc.get(k) for k in ("owner", "host", "until")}
        return False


async def acquire(col=None, poll: float = POLL, notify=None) -> float:
    """Wait until this copy holds the lease. Returns the seconds spent waiting."""
    if not enabled():
        return 0.0
    loop = asyncio.get_running_loop()
    t0, last_log = loop.time(), -1e9
    while True:
        try:
            if await try_acquire(col):
                STATE["waited"] = loop.time() - t0
                if STATE["waited"] > 1:
                    log.info(f"🔒 took over the instance lease after {STATE['waited']:.0f}s")
                return STATE["waited"]
        except Exception as e:                  # Mongo hiccup – don't block the boot on the lock itself
            log.warning(f"instance lock unavailable ({e}) – starting without it")
            return loop.time() - t0
        if loop.time() - last_log > 60:
            other = STATE.get("other") or {}
            log.warning(f"⏳ another Videl is running with this bot token (host {other.get('host')}) – "
                        "waiting for it to stop so updates aren't processed twice")
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
    res = await col.update_one({"_id": LOCK_ID, "owner": ME},
                               {"$set": {"until": now + timedelta(seconds=TTL), "beat": now}})
    STATE["held"] = bool(res.matched_count)
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


async def _heartbeat(on_lost, col=None, beat: float = BEAT):
    while True:
        await asyncio.sleep(beat)
        try:
            if not await renew(col):
                log.error("🔒 the instance lease was taken over by another copy – stopping this one")
                await on_lost()
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:                  # Mongo blip – try again next beat
            log.warning(f"instance heartbeat: {e}")


def _exit():
    os._exit(0)


async def _default_lost():
    _exit()


def start_heartbeat(on_lost=None, col=None, beat: float = BEAT):
    global _task
    if not enabled():
        return None
    _task = asyncio.create_task(_heartbeat(on_lost or _default_lost, col, beat))
    return _task


def stop_heartbeat():
    if _task is not None and not _task.done():
        _task.cancel()

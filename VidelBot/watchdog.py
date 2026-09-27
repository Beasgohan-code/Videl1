"""
Videl watchdog – self-healing + automatic cleanup, runs every WATCHDOG_INTERVAL seconds.

Each sweep:
  1. 🧹 Files   – removes temp downloads / encodes / progress files / tool leftovers
                  older than CLEANUP_AFTER_HOURS (never touches files still being written;
                  encoder dirs are skipped while the encode queue is busy).
  2. 💽 Disk    – if free space < MIN_FREE_DISK_GB → aggressive cleanup + owner alert.
  3. ⏳ States  – drops abandoned /login and clone-setup flows after STATE_TIMEOUT_MIN
                  (disconnects the half-logged-in client and tells the user).
  4. 🤖 Clones  – restarts crashed / disconnected clone bots, starts active ones that
                  failed at boot (with back-off).
  5. 📡 Link    – checks Telegram connectivity; after 3 failed checks in a row the
                  process restarts itself (AUTO_RESTART_ON_HANG).
  6. 🧠 Memory  – trims caches and runs the garbage collector.
"""
import asyncio
import gc
import logging
import os
import shutil
import sys
import time

import psutil

import config

log = logging.getLogger("videl.watchdog")

TEMP_DIRS = ["downloads"]                      # saver temp dirs + tools
ENCODER_DIRS = [config.DOWNLOAD_DIR, config.ENCODE_DIR]
ROOT_PATTERNS = ("downstatus.txt", "upstatus.txt")
dog = None  # the running Watchdog instance (set by start())


def _age_h(path: str) -> float:
    try:
        return (time.time() - os.path.getmtime(path)) / 3600
    except OSError:
        return 0.0


def _newest_mtime(path: str) -> float:
    """Most recent mtime inside a directory tree (so active downloads are never deleted)."""
    newest = 0.0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
            except OSError:
                pass
    return newest or os.path.getmtime(path)  # empty dir → its own mtime


def _size(path: str) -> int:
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _in_login(uid) -> bool:
    try:
        from saver import session
        return uid in session.LOGIN_STATE
    except Exception:
        return False


def _in_create(uid) -> bool:
    try:
        from filestore.main_bot.plugins.create_bot import _creation_state
        return uid in _creation_state
    except Exception:
        return False


def clean_dir(base: str, max_age_h: float, keep: set = frozenset()) -> tuple:
    """Delete entries in `base` whose newest file is older than max_age_h. Returns (count, bytes)."""
    count = freed = 0
    if not base or not os.path.isdir(base):
        return 0, 0
    for name in os.listdir(base):
        path = os.path.join(base, name)
        if name in keep or name.startswith("."):
            continue
        try:
            newest = _newest_mtime(path) if os.path.isdir(path) else os.path.getmtime(path)
            if (time.time() - newest) / 3600 < max_age_h:
                continue
            size = _size(path)
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
            count += 1
            freed += size
        except OSError as e:
            log.debug(f"cleanup skip {path}: {e}")
    return count, freed


class Watchdog:
    def __init__(self, app):
        self.app = app
        self.started = time.time()
        self.sweeps = 0
        self.total_freed = 0
        self.total_files = 0
        self.conn_failures = 0
        self.state_seen: dict = {}      # ("login"|"create", uid) -> (first seen ts, id(state))
        self.worker_failures: dict = {}  # bot_id -> consecutive start failures
        self.last: dict = {}
        self._last_disk_alert = 0.0
        self._task = None

    # ─────────────── public ───────────────
    def summary(self) -> dict:
        return {
            "sweeps": self.sweeps,
            "files_removed": self.total_files,
            "freed_mb": round(self.total_freed / 1048576, 1),
            "last": self.last,
        }

    async def run_forever(self):
        await asyncio.sleep(60)  # let the bot finish booting
        while True:
            try:
                await self.sweep()
            except Exception as e:
                log.exception(f"watchdog sweep failed: {e}")
            await asyncio.sleep(config.WATCHDOG_INTERVAL)

    async def sweep(self, aggressive: bool = False) -> dict:
        t0 = time.time()
        report = {}
        free_gb = shutil.disk_usage(".").free / 1073741824
        if free_gb < config.MIN_FREE_DISK_GB:
            aggressive = True
        report["files"], report["freed_mb"] = self.clean_files(aggressive)
        report["free_disk_gb"] = round(shutil.disk_usage(".").free / 1073741824, 2)
        if report["free_disk_gb"] < config.MIN_FREE_DISK_GB:
            await self._alert_disk(report["free_disk_gb"])
        report["states_dropped"] = await self.expire_states()
        report["clones_healed"] = await self.heal_workers()
        report["telegram_ok"] = await self.check_connection()
        report["ram_mb"] = self.trim_memory()
        report["took_s"] = round(time.time() - t0, 2)
        report["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        report["aggressive"] = aggressive
        self.sweeps += 1
        self.last = report
        if report["files"] or report["states_dropped"] or report["clones_healed"]:
            log.info(f"🐕 watchdog: {report}")
        return report

    # ─────────────── 1+2. files ───────────────
    def clean_files(self, aggressive: bool = False) -> tuple:
        age = 0.5 if aggressive else config.CLEANUP_AFTER_HOURS
        files = freed = 0
        for d in TEMP_DIRS:
            c, f = clean_dir(d, age)
            files += c
            freed += f
        # Encoder working dirs: only when nothing is queued (or in an emergency, very old files).
        try:
            from VideoEncoder import data as enc_queue
            busy = len(enc_queue) > 0
        except Exception:
            busy = False
        for d in ENCODER_DIRS:
            if busy and not aggressive:
                continue
            c, f = clean_dir(d, 24 if busy else age, keep={"process.txt"} if busy else set())
            files += c
            freed += f
        # Stray progress files the saver writes in the working directory.
        for name in os.listdir("."):
            if name.endswith(ROOT_PATTERNS) and _age_h(name) > 1:
                try:
                    freed += os.path.getsize(name)
                    os.remove(name)
                    files += 1
                except OSError:
                    pass
        self.total_files += files
        self.total_freed += freed
        return files, round(freed / 1048576, 1)

    async def _alert_disk(self, free_gb: float):
        if time.time() - self._last_disk_alert < 3 * 3600:
            return
        self._last_disk_alert = time.time()
        from core import botlog
        await botlog.event("LowDisk", f"⚠️ <b>Low disk space:</b> <code>{free_gb:.2f} GB</code> free "
                                      f"(threshold {config.MIN_FREE_DISK_GB} GB).\n🧹 Aggressive cleanup ran.",
                           client=self.app)

    # ─────────────── 3. abandoned flows ───────────────
    async def expire_states(self) -> int:
        timeout = config.STATE_TIMEOUT_MIN * 60
        now = time.time()
        dropped = 0
        live = set()

        try:
            from saver import session
            for uid in list(session.LOGIN_STATE):
                key = ("login", uid)
                live.add(key)
                if now - self._first_seen(key, session.LOGIN_STATE.get(uid), now) > timeout:
                    state = session.LOGIN_STATE.pop(uid, None) or {}
                    client = (state.get("data") or {}).get("client")
                    if client:
                        try:
                            await client.disconnect()
                        except Exception:
                            pass
                    dropped += 1
                    await self._notify(uid, "⌛ <b>Login timed out.</b> Send /login to start again.",
                                       remove_keyboard=True)
        except Exception as e:
            log.debug(f"login states: {e}")

        try:
            from filestore.main_bot.plugins.create_bot import _creation_state
            for uid in list(_creation_state):
                key = ("create", uid)
                live.add(key)
                if now - self._first_seen(key, _creation_state.get(uid), now) > timeout:
                    _creation_state.pop(uid, None)
                    dropped += 1
                    await self._notify(uid, "⌛ <b>Clone-bot setup timed out.</b> Open /clone to try again.")
        except Exception as e:
            log.debug(f"creation states: {e}")

        # forget flows that finished (or were just dropped)
        for key in [k for k in self.state_seen if k not in live]:
            self.state_seen.pop(key, None)
        for key in list(self.state_seen):
            uid = key[1]
            if (key[0] == "login" and not _in_login(uid)) or (key[0] == "create" and not _in_create(uid)):
                self.state_seen.pop(key, None)
        return dropped

    def _first_seen(self, key, state_obj, now) -> float:
        """When this exact flow (same state object) was first seen; a new flow resets the timer."""
        ts, ident = self.state_seen.get(key, (now, id(state_obj)))
        if ident != id(state_obj):
            ts = now
        self.state_seen[key] = (ts, id(state_obj))
        return ts

    async def _notify(self, uid: int, text: str, remove_keyboard: bool = False):
        try:
            from pyrogram.types import ReplyKeyboardRemove
            await self.app.send_message(uid, text, reply_markup=ReplyKeyboardRemove() if remove_keyboard else None)
        except Exception:
            pass

    # ─────────────── 4. clone bots ───────────────
    async def heal_workers(self) -> int:
        try:
            from filestore.database.main_db import MainDB
            from filestore.worker_bot.engine import worker_engine
        except Exception:
            return 0
        healed = 0
        main_db = MainDB()
        # a) running but disconnected
        for bot_id, client in list(worker_engine.workers.items()):
            if getattr(client, "is_connected", True):
                continue
            doc = await main_db.get_bot(bot_id)
            await worker_engine.stop_worker(bot_id)
            if doc and doc.get("is_active"):
                try:
                    await worker_engine.start_worker(doc)
                    healed += 1
                    log.info(f"🐕 restarted disconnected clone {bot_id}")
                    await self._clone_event("CloneHealed", doc, "🔌 was disconnected → restarted")
                except Exception as e:
                    log.warning(f"clone {bot_id} restart failed: {e}")
        # b) active in DB but not running (crashed at boot / after an error)
        try:
            active = await main_db.get_all_active_bots()
        except Exception:
            return healed
        for doc in active:
            bot_id = doc["_id"]
            if bot_id in worker_engine.workers or doc.get("is_deleted"):
                self.worker_failures.pop(bot_id, None)
                continue
            fails = self.worker_failures.get(bot_id, 0)
            # back-off: after 3 failures only retry every 6th sweep
            if fails >= 3 and (self.sweeps % 6):
                continue
            try:
                await worker_engine.start_worker(doc)
                self.worker_failures.pop(bot_id, None)
                healed += 1
                log.info(f"🐕 started missing clone {bot_id}")
                await self._clone_event("CloneHealed", doc, "💤 was not running → started")
            except Exception as e:
                self.worker_failures[bot_id] = fails + 1
                log.warning(f"clone {bot_id} start failed ({fails + 1}x): {e}")
                if fails + 1 == 5:
                    await self._clone_event("CloneFailed", doc, f"❌ failed to start 5× in a row: <code>{str(e)[:150]}</code>")
                    await self._notify(doc.get("owner_id"),
                                       f"⚠️ Your clone bot @{doc.get('bot_username', '?')} keeps failing to start. "
                                       "Was its token revoked? Check it in /clone → 📋 My Bots.")
        return healed

    async def _clone_event(self, tag, doc, what):
        try:
            from core import botlog
            await botlog.event(tag, f"<b>🤖 Clone bot:</b> @{doc.get('bot_username', '?')} (<code>{doc.get('_id')}</code>)\n"
                                    f"<b>👤 Owner:</b> <code>{doc.get('owner_id')}</code>\n<b>🐕 Watchdog:</b> {what}",
                               client=self.app)
        except Exception:
            pass

    # ─────────────── 5. connectivity ───────────────
    async def check_connection(self) -> bool:
        try:
            await asyncio.wait_for(self.app.get_me(), timeout=30)
            self.conn_failures = 0
            return True
        except Exception as e:
            self.conn_failures += 1
            log.warning(f"🐕 Telegram check failed ({self.conn_failures}x): {e}")
            if self.conn_failures >= 3 and config.AUTO_RESTART_ON_HANG:
                log.error("🐕 Telegram unreachable 3x in a row – restarting process")
                try:
                    from core import botlog
                    await botlog.event("AutoRestart", "🐕 Telegram was unreachable 3 checks in a row – "
                                                      "the process restarted itself.", client=self.app)
                    await botlog.flush(5)
                    botlog.set_restart_reason("🐕 auto-restart: Telegram was unreachable 3 checks in a row")
                except Exception:
                    pass
                try:
                    from filestore.worker_bot.engine import worker_engine
                    await asyncio.wait_for(worker_engine.stop_all_workers(), timeout=20)
                except Exception:
                    pass
                os.execl(sys.executable, sys.executable, "run.py")
            return False

    # ─────────────── 6. memory ───────────────
    def trim_memory(self) -> float:
        try:
            from core.db import vdb
            if len(vdb._known) > 200_000:
                vdb._known.clear()
        except Exception:
            pass
        try:
            from core import fsub
            cutoff = time.time() - 3600
            for d in (fsub._ok_cache, fsub._last_prompt):
                for k in [k for k, v in d.items() if v < cutoff]:
                    d.pop(k, None)
        except Exception:
            pass
        self._trim_module_caches()
        gc.collect()
        return round(psutil.Process().memory_info().rss / 1048576, 1)

    @staticmethod
    def _trim_module_caches():
        """Per-user dicts that only ever grew: drop idle entries (each block is independent)."""
        now = time.time()

        def drop_old(d: dict, max_age: float, key=lambda v: v):
            for k in [k for k, v in list(d.items()) if now - key(v) > max_age]:
                d.pop(k, None)

        try:
            from renamer import store
            drop_old(store._cache, store.CACHE_TTL, key=lambda v: v[0])
        except Exception:
            pass
        try:
            from renamer import engine as rn
            for uid in [u for u, lock in list(rn._locks.items()) if not lock.locked() and not rn._pending.get(u)]:
                rn._locks.pop(uid, None)
            drop_old(rn._cancel_before, 3600)
        except Exception:
            pass
        try:
            from core import botlog, errors, support
            drop_old(botlog._start_seen, 86400)
            drop_old(errors._last_sent, 86400)
            drop_old(support._last, 3600)
        except Exception:
            pass
        # saver batch_temp.IS_BATCH is NOT trimmed: a True entry may be a pending /cancel.


def start(app) -> Watchdog:
    global dog
    dog = Watchdog(app)
    dog._task = asyncio.get_event_loop().create_task(dog.run_forever())
    log.info(f"🐕 watchdog armed (every {config.WATCHDOG_INTERVAL}s, cleanup after {config.CLEANUP_AFTER_HOURS}h)")
    return dog

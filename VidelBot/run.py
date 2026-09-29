"""
Videl — entry point.

    python run.py

Boots one Pyrogram client with every module (core → saver → encoder →
clone-bot controller), then starts all user clone (worker) bots.
"""
import asyncio
import importlib
import logging
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import config  # noqa: E402,F401  (loads config.env + cleans env values – must be first)
import logger  # noqa: E402,F401  (configures logging)
import pyrogram.utils  # noqa: E402

pyrogram.utils.MIN_CHANNEL_ID = -1009147483647  # support newer channel IDs

log = logging.getLogger("videl")

# Loaded in this order – within a handler group the first match wins.
PLUGIN_ROOTS = [
    "core",
    "saver",
    "VideoEncoder/plugins",
    "filestore/main_bot/plugins",
    "renamer",
]
SKIP = {"__init__", "ui", "db", "texts", "botapi"}


def load_plugins(app) -> int:
    """Import every module under PLUGIN_ROOTS and register its decorated handlers."""
    count = 0
    for root in PLUGIN_ROOTS:
        for path in sorted(Path(root).rglob("*.py")):
            if path.stem in SKIP or "__pycache__" in path.parts:
                continue
            mod_name = ".".join(path.with_suffix("").parts)
            try:
                module = importlib.import_module(mod_name)
            except Exception:
                log.exception(f"❌ failed to import {mod_name}")
                continue
            for name in vars(module):
                obj = getattr(module, name)
                handlers = getattr(obj, "handlers", None)
                # only functions defined in this module (avoid double registration via imports)
                if not isinstance(handlers, list) or getattr(obj, "__module__", None) != mod_name:
                    continue
                for handler, group in handlers:
                    if isinstance(group, int):
                        app.add_handler(handler, group)
                        count += 1
    # last group-0 handler: the "what can I do with this file?" hint for files no plugin claimed
    try:
        from core import filehint
        count += filehint.register(app)
    except Exception:
        log.exception("❌ file hint not registered")
    return count


async def start_with_retry(app):
    """Start the client; survive FloodWait / transient network errors at login (from the SRC bot)."""
    from pyrogram.errors import FloodWait
    attempt = 0
    while True:
        attempt += 1
        try:
            await app.start()
            return
        except FloodWait as e:
            wait = int(e.value) + 10
            log.warning(f"FloodWait during login – sleeping {wait}s")
            await asyncio.sleep(wait)
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            wait = min(15 * attempt, 120)
            log.error(f"network error during start ({e}) – retrying in {wait}s")
            await asyncio.sleep(wait)


async def _start_encoder_extras(app):
    """GPU / AV1 detection (off the event loop), then re-queue encodes that were waiting before a restart."""
    try:
        from VideoEncoder.utils import hw, scheduler
        await asyncio.to_thread(hw.detect)
        log.info(f"🎛 {hw.summary()}")
        await scheduler.restore_queue(app)
    except Exception as e:
        log.error(f"encoder extras: {e}")
    for name in ("analytics", "backup"):
        try:
            mod = __import__(f"core.{name}", fromlist=["start"])
            mod.start(app)
        except ModuleNotFoundError:
            pass
        except Exception as e:
            log.error(f"{name}: {e}")


async def main():
    t0 = time.time()
    missing = config.missing_required()
    if missing:
        log.error(f"Missing required config: {', '.join(missing)} — see config.env.sample")
        sys.exit(1)

    import keep_alive
    await keep_alive.start_server()
    from core.bg import spawn
    spawn(keep_alive.self_ping_loop(), name="self-ping")

    from filestore.database.mongo import get_motor_client
    for attempt in range(1, 6):             # Atlas DNS / cold-start hiccups are common on free hosts
        try:
            await get_motor_client().admin.command("ping")
            log.info("✅ MongoDB connected")
            break
        except Exception as e:
            if attempt == 5:
                log.error(f"❌ MongoDB connection failed: {e} — check DB_URI and the Atlas IP allow-list (0.0.0.0/0)")
                sys.exit(1)
            log.warning(f"MongoDB not reachable yet ({e}) – retry {attempt}/5 in {attempt * 5}s")
            await asyncio.sleep(attempt * 5)

    from core.db import vdb
    await vdb.warm_up()

    # One live copy per bot token – a zero-downtime redeploy must not answer every update twice
    from core import instance
    await instance.acquire()
    instance.start_heartbeat()
    try:
        from VideoEncoder.utils import jobs
        n_orphans = jobs.reap_orphans()     # encodes left running by an exec restart
        if n_orphans:
            log.info(f"🧹 stopped {n_orphans} leftover ffmpeg process(es)")
    except Exception as e:
        log.debug(f"orphan sweep: {e}")
    try:
        from filestore.database.main_db import MainDB
        await MainDB().ensure_indexes()
        from core.db import ensure_module_indexes
        await ensure_module_indexes()
    except Exception as e:
        log.warning(f"clone index setup skipped: {e}")

    # DB admins (/add_admin) must be merged before the plugins build their filters
    from core.admins import load_db_admins
    await load_db_admins()

    from client import app
    n = load_plugins(app)
    log.info(f"🧩 registered {n} handlers")

    from pyrogram import idle

    from core import botlog
    from core.commands import register_commands, set_profile

    await start_with_retry(app)
    me = await app.get_me()
    botlog.bind(app)
    log.info(f"🤖 {config.BOT_NAME} started as @{me.username}")

    import filestore.utils.helpers as fs_helpers
    fs_helpers.main_bot_client = app

    await register_commands(app)
    await set_profile(app)

    from core.admin import announce_restart
    await announce_restart(app)

    from filestore.worker_bot.engine import worker_engine
    try:
        await worker_engine.start_all_workers()
        log.info(f"👷 {worker_engine.active_count} clone bots running")
    except Exception as e:
        log.error(f"starting clone bots failed: {e}")
    from filestore.main_bot.plugins.clone_lifecycle import lifecycle_task
    spawn(lifecycle_task(app, worker_engine), name="clone-lifecycle")     # clones unused for CLONE_INACTIVE_DAYS → off

    import watchdog
    watchdog.start(app)
    spawn(_start_encoder_extras(app), name="encoder-extras")
    from core.ui import fill_pic_pool
    spawn(fill_pic_pool(), name="pic-pool")      # random start pics ready before the first /start
    if config.DAILY_REPORT:
        spawn(botlog.daily_report_loop(app), name="daily-report")

    # #BotStarted → log channel + every owner's DM
    await botlog.boot_report(app, me, handlers=n, boot_seconds=time.time() - t0)

    await idle()

    log.info("stopping…")
    started = botlog.now() - timedelta(seconds=time.time() - t0)
    await botlog.event("BotStopped", (
        f"<b>❌ {botlog.esc(config.BOT_NAME)} is going offline</b>\n\n<blockquote>"
        f"<b>🤖 Bot:</b> @{me.username}\n<b>⏱ Was up since:</b> {started.strftime('%d %b %Y · %I:%M %p')}\n"
        f"<b>🤖 Clone bots stopped:</b> {worker_engine.active_count}</blockquote>"), client=app)
    await botlog.flush(10)
    await worker_engine.stop_all_workers()
    await keep_alive.stop_server()
    from core import botapi
    await botapi.close()
    await app.stop()
    instance.stop_heartbeat()
    await instance.release()                  # the next deploy can start right away


if __name__ == "__main__":
    asyncio.run(main())

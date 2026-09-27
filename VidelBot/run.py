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
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import logger  # noqa: E402,F401  (configures logging)
import pyrogram.utils  # noqa: E402

pyrogram.utils.MIN_CHANNEL_ID = -1009147483647  # support newer channel IDs

import config  # noqa: E402

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
    return count


async def hibernation_task(app, worker_engine):
    """Stop clone bots idle for more than HIBERNATION_HOURS to save RAM."""
    from filestore.database.main_db import MainDB

    main_db = MainDB()
    while True:
        await asyncio.sleep(3600)
        try:
            now = datetime.now(timezone.utc)
            for bot in await main_db.get_all_active_bots():
                last = bot.get("last_active") or bot.get("created_at") or now
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if now - last <= timedelta(hours=config.HIBERNATION_HOURS):
                    continue
                bot_id = bot["_id"]
                log.info(f"💤 hibernating idle clone {bot_id}")
                await worker_engine.stop_worker(bot_id)
                await main_db.set_bot_active(bot_id, False)
                from core import botlog
                await botlog.event("CloneHibernated", (
                    f"<b>🤖 Clone bot:</b> @{bot.get('bot_username', 'unknown')} (<code>{bot_id}</code>)\n"
                    f"<b>👤 Owner:</b> <code>{bot.get('owner_id')}</code>\n"
                    f"<b>💤 Idle for:</b> more than {config.HIBERNATION_HOURS}h"), client=app)
                try:
                    await app.send_message(
                        bot["owner_id"],
                        "<b>💤 Bot Hibernated</b>\n\n<blockquote>"
                        f"Your bot @{bot.get('bot_username', 'unknown')} was switched off after "
                        f"{config.HIBERNATION_HOURS}h of inactivity to save resources.\n\n"
                        "Wake it up anytime: <b>🤖 Clone Bots → 📋 My Bots → 🟢 Start Bot</b>.</blockquote>",
                    )
                except Exception:
                    pass
        except Exception as e:
            log.error(f"hibernation sweep failed: {e}")


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


async def main():
    t0 = time.time()
    missing = config.missing_required()
    if missing:
        log.error(f"Missing required config: {', '.join(missing)} — see config.env.sample")
        sys.exit(1)

    import keep_alive
    await keep_alive.start_server()
    asyncio.create_task(keep_alive.self_ping_loop())

    from filestore.database.mongo import get_motor_client
    try:
        await get_motor_client().admin.command("ping")
        log.info("✅ MongoDB connected")
    except Exception as e:
        log.error(f"❌ MongoDB connection failed: {e}")
        sys.exit(1)

    from core.db import vdb
    await vdb.warm_up()

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
    asyncio.create_task(hibernation_task(app, worker_engine))

    import watchdog
    watchdog.start(app)
    if config.DAILY_REPORT:
        asyncio.create_task(botlog.daily_report_loop(app))

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


if __name__ == "__main__":
    asyncio.run(main())

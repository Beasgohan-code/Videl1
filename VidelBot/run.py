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
]
SKIP = {"__init__", "ui", "db", "texts"}


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


BOT_COMMANDS = [
    ("start", "🏠 Home menu"),
    ("help", "❓ How to use every module"),
    ("settings", "⚙️ Saver / encoder / clone settings"),
    ("clone", "🤖 Create & manage your FileStore bots"),
    ("login", "🔐 Connect account for private channels"),
    ("logout", "🚪 Disconnect account"),
    ("myplan", "📊 Your plan & quota"),
    ("premium", "💎 Premium plans"),
    ("buy", "⭐ Buy Premium with Telegram Stars"),
    ("dl", "🎬 Encode a replied video"),
    ("ddl", "🔗 Encode from a direct link"),
    ("queue", "📋 Encoder queue"),
    ("mediainfo", "🔎 Media info of a replied file"),
    ("rename", "✏️ Rename a replied file"),
    ("upload", "☁️ Public link for a replied file"),
    ("short", "✂️ Shorten a URL"),
    ("qr", "🔳 Make a QR code"),
    ("id", "🆔 Get IDs"),
    ("info", "👤 User info"),
    ("ping", "🏓 Latency"),
    ("about", "ℹ️ About this bot"),
    ("cancel", "❌ Cancel current task"),
]

# Extra commands shown only to owners (BotCommandScopeChat).
OWNER_COMMANDS = [
    ("stats", "📊 Bot statistics"),
    ("broadcast", "📢 Broadcast to all users"),
    ("ban", "🚫 Ban a user"),
    ("unban", "✅ Unban a user"),
    ("add_premium", "💎 Give premium"),
    ("remove_premium", "➖ Remove premium"),
    ("premium_users", "👥 Premium users"),
    ("stars", "⭐ Stars payments"),
    ("refund", "↩️ Refund a Stars payment"),
    ("add_fsub", "🔒 Add force-sub channel"),
    ("del_fsub", "🔓 Remove force-sub channel"),
    ("fsub_list", "📋 Force-sub channels"),
    ("clonestats", "🤖 Clone bot statistics"),
    ("maintenance", "🛠 Toggle maintenance"),
    ("watchdog", "🐕 Watchdog status / sweep"),
    ("logs", "📜 Get log file"),
    ("restart", "♻️ Restart"),
]


async def setup_bot_profile(app):
    """Menu commands (public + owner scope), description and short about text."""
    from pyrogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

    public = [BotCommand(c, d) for c, d in BOT_COMMANDS]
    try:
        await app.set_bot_commands(public, scope=BotCommandScopeDefault())
    except Exception as e:
        log.warning(f"set_bot_commands failed: {e}")
    owner_cmds = public + [BotCommand(c, d) for c, d in OWNER_COMMANDS]
    for oid in config.OWNERS:
        try:
            await app.set_bot_commands(owner_cmds, scope=BotCommandScopeChat(oid))
        except Exception:
            pass  # owner hasn't started the bot yet
    try:
        await app.set_bot_info(
            lang_code="",
            description=(f"✨ {config.BOT_NAME} – all-in-one utility bot\n\n"
                         "📥 Save restricted posts & media\n🎬 Encode / compress videos\n"
                         "⚡ Create your own FileStore clone bots\n🧰 Rename · MediaInfo · Upload · QR · Short links\n\n"
                         "Tap START to begin!"),
            about=f"{config.BOT_NAME}: save restricted content, encode videos & clone FileStore bots.",
        )
    except Exception as e:
        log.debug(f"set_bot_info skipped: {e}")


async def main():
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

    from client import app
    n = load_plugins(app)
    log.info(f"🧩 registered {n} handlers")

    from pyrogram import idle

    await app.start()
    me = await app.get_me()
    log.info(f"🤖 {config.BOT_NAME} started as @{me.username}")

    import filestore.utils.helpers as fs_helpers
    fs_helpers.main_bot_client = app

    await setup_bot_profile(app)

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

    notify = config.LOG_CHANNEL or config.OWNER_ID
    try:
        await app.send_message(
            notify,
            f"<b>✅ {config.BOT_NAME} started</b>\n<blockquote>@{me.username}\n"
            f"Clone bots running: {worker_engine.active_count}</blockquote>",
        )
    except Exception as e:
        log.warning(f"startup notice failed (has the owner started the bot?): {e}")

    await idle()

    log.info("stopping…")
    await worker_engine.stop_all_workers()
    await keep_alive.stop_server()
    await app.stop()


if __name__ == "__main__":
    asyncio.run(main())

"""
Owner commands for the Videl clone (FileStore) platform
/fsstats - Clone platform overview
/bots   - List all bots
/sys    - System resource usage
"""

import time
import psutil
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from filestore.fs_config import OWNERS, LOGGER, HIBERNATION_HOURS, MAX_BOTS_PER_USER
from filestore.database.main_db import MainDB

log = LOGGER(__name__)
main_db = MainDB()


@Client.on_message(filters.command(["fsstats", "clonestats"]) & filters.private & filters.user(OWNERS))
async def platform_stats(client: Client, message: Message):
    """Full platform statistics."""
    try:
        total_users = await main_db.total_users()
        all_bots = await main_db.get_all_bots() if hasattr(main_db, "get_all_bots") else []
        
        # Fallback if method missing
        if not all_bots:
            try:
                active = await main_db.get_all_active_bots()
                all_bots = active
            except Exception:
                all_bots = []

        active_count = 0
        stopped_count = 0
        from filestore.worker_bot.engine import worker_engine
        
        for bot in all_bots:
            bid = bot.get("_id") or bot.get("bot_id")
            if worker_engine.get_worker(bid):
                active_count += 1
            else:
                stopped_count += 1

        text = (
            f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 𝗖𝗟𝗢𝗡𝗘 𝗣𝗟𝗔𝗧𝗙𝗢𝗥𝗠 𝗦𝗧𝗔𝗧𝗦\n"
            f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<blockquote>"
            f"◈ <b>Total Users:</b> <code>{total_users}</code>\n"
            f"◈ <b>Total Bots:</b> <code>{len(all_bots)}</code>\n"
            f"◈ <b>Running:</b> 🟢 <code>{active_count}</code>\n"
            f"◈ <b>Stopped:</b> 🔴 <code>{stopped_count}</code>\n"
            f"◈ <b>Max Bots/User:</b> <code>{MAX_BOTS_PER_USER}</code>\n"
            f"◈ <b>Hibernation:</b> <code>{HIBERNATION_HOURS}h</code>\n"
            f"</blockquote>\n"

        )
        await message.reply(text)
    except Exception as e:
        log.error(f"Stats error: {e}")
        await message.reply(f"<b>❌ Error fetching stats:</b> <code>{e}</code>")


@Client.on_message(filters.command("bots") & filters.private & filters.user(OWNERS))
async def list_all_bots(client: Client, message: Message):
    """Quick list of all registered bots."""
    try:
        bots = []
        try:
            bots = await main_db.get_all_active_bots()
        except Exception:
            pass

        if not bots:
            await message.reply("<b>❌ No bots found in database.</b>")
            return

        from filestore.worker_bot.engine import worker_engine
        lines = []
        for i, bot in enumerate(bots[:30], 1):  # Limit to 30
            username = bot.get("bot_username", "unknown")
            owner = bot.get("owner_id", "?")
            bid = bot.get("_id")
            live = "🟢" if worker_engine.get_worker(bid) else "🔴"
            lines.append(f"{i}. {live} @{username} | Owner: <code>{owner}</code>")

        text = (
            f"<b>🤖 All Bots ({len(bots)})</b>\n\n"
            f"<blockquote>" + "\n".join(lines) + "</blockquote>"
        )
        if len(bots) > 30:
            text += f"\n\n<i>...and {len(bots)-30} more</i>"
        await message.reply(text)
    except Exception as e:
        log.error(f"Bots list error: {e}")
        await message.reply(f"<b>❌ Error:</b> <code>{e}</code>")


@Client.on_message(filters.command(["sys", "system", "sysstats"]) & filters.private & filters.user(OWNERS))
async def system_stats(client: Client, message: Message):
    """Show server resource usage."""
    try:
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot

        from filestore.worker_bot.engine import worker_engine
        active_workers = worker_engine.active_count

        text = (
            f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
            f"🖥 𝗦𝗬𝗦𝗧𝗘𝗠 𝗦𝗧𝗔𝗧𝗦\n"
            f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<blockquote>"
            f"◈ <b>CPU:</b> <code>{cpu}%</code>\n"
            f"◈ <b>RAM:</b> <code>{mem.percent}%</code> "
            f"({mem.used // (1024**2)} / {mem.total // (1024**2)} MB)\n"
            f"◈ <b>Disk:</b> <code>{disk.percent}%</code>\n"
            f"◈ <b>Uptime:</b> <code>{str(uptime).split('.')[0]}</code>\n"
            f"◈ <b>Active Workers:</b> <code>{active_workers}</code>\n"
            f"</blockquote>"
        )
        await message.reply(text)
    except Exception as e:
        await message.reply(f"<b>❌ System stats error:</b> <code>{e}</code>")

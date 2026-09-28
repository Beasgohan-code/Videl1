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
            f"◈ <b>Auto-off:</b> <code>{str(HIBERNATION_HOURS // 24) + ' days unused' if HIBERNATION_HOURS else 'off'}</code>\n"
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


@Client.on_message(filters.command(["sys", "system", "sysstats", "systats"]) & filters.private & filters.user(OWNERS))
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


@Client.on_message(filters.command(["check", "checkbots"]) & filters.private & filters.user(OWNERS))
async def check_bots(client: Client, message: Message):
    """Health-check every clone bot: running / reachable / should-be-running. `/check fix` restarts the missing ones."""
    from core import stream
    async with stream.progress(message, "🩺 <i>Checking every clone bot…</i>") as status:
        await _check_bots(client, message, status)


async def _check_bots(client, message, status):
    import asyncio
    from filestore.worker_bot.engine import worker_engine
    bots = [b for b in await main_db.get_all_bots() if not b.get("is_deleted")]
    ok, dead, missing, off = [], [], [], []
    dead_ids = []

    async def probe(bot):
        bot_id = bot["_id"]
        worker = worker_engine.get_worker(bot_id)
        name = f"@{bot.get('bot_username', '?')} (<code>{bot_id}</code>)"
        if worker:
            try:
                await asyncio.wait_for(worker.get_me(), timeout=15)
                ok.append(name)
            except Exception as e:
                dead.append(f"{name} – {str(e)[:60]}")
                dead_ids.append(bot_id)
        elif bot.get("is_active"):
            missing.append(name)
        else:
            off.append(name)

    for i in range(0, len(bots), 20):
        await asyncio.gather(*(probe(b) for b in bots[i:i + 20]))

    fixed = ""
    if len(message.command) > 1 and message.command[1].lower() == "fix" and (missing or dead):
        import watchdog
        dog = watchdog.dog or watchdog.Watchdog(client)
        for bot_id in dead_ids:  # stop unresponsive ones so they are started fresh
            await worker_engine.stop_worker(bot_id)
        healed = await dog.heal_workers()
        fixed = f"\n\n🔧 <b>Fix:</b> {healed} clone(s) (re)started."

    def block(title, items):
        if not items:
            return ""
        shown = "\n".join(f"• {x}" for x in items[:25])
        more = f"\n<i>… and {len(items) - 25} more</i>" if len(items) > 25 else ""
        return f"\n\n<b>{title} ({len(items)})</b>\n<blockquote expandable>{shown}{more}</blockquote>"

    text = (
        f"<b>🩺 Clone bots health</b>\n\n"
        f"🟢 Running &amp; reachable: <code>{len(ok)}</code>\n"
        f"🔴 Running but not responding: <code>{len(dead)}</code>\n"
        f"⚠️ Active but not running: <code>{len(missing)}</code>\n"
        f"💤 Stopped / deactivated: <code>{len(off)}</code>"
        + block("🔴 Not responding", dead) + block("⚠️ Not running", missing)
        + (fixed or ("\n\n<i>Use /check fix to restart the broken ones.</i>" if (dead or missing) else ""))
    )
    await status.finish(text[:4096])

"""
Owner commands for the Videl clone (FileStore) platform
/fsstats - Clone platform overview
/bots   - List all bots
/sys    - System resource usage
"""

import asyncio
import html
import re
import time
import psutil
from datetime import datetime, timezone

from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from core import rich
from core.rich import Doc
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
        if not all_bots:
            try:
                all_bots = await main_db.get_all_active_bots()
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

        doc = Doc("📊", "Clone Platform Stats", "FileStore worker fleet").kv_table([
            ("👥 Total Users", f"{total_users:,}"),
            ("🤖 Total Bots", f"{len(all_bots):,}"),
            ("🟢 Running", str(active_count)),
            ("🔴 Stopped", str(stopped_count)),
            ("🔢 Max Bots/User", str(MAX_BOTS_PER_USER)),
            ("💤 Auto-Off", f"{HIBERNATION_HOURS // 24} days unused" if HIBERNATION_HOURS else "Off"),
        ], key_header="Metric", val_header="Value")
        await rich.reply(message, doc)
    except Exception as e:
        log.error(f"Stats error: {e}")
        await message.reply(f"<b>❌ Error fetching stats:</b> <code>{html.escape(str(e))}</code>")


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
            return await rich.reply(
                message,
                Doc("🤖", "Registered Clone Bots").status("empty", "No bots found", "No clone bots registered in database."),
            )

        from filestore.worker_bot.engine import worker_engine
        rows = []
        for bot in bots[:30]:
            username = bot.get("bot_username", "unknown")
            owner = bot.get("owner_id", "?")
            bid = bot.get("_id")
            live = "🟢 Live" if worker_engine.get_worker(bid) else "🔴 Idle"
            rows.append((f"@{username}", rich.code(owner), live))

        doc = Doc("🤖", f"All Clone Bots · {len(bots)}").table(
            rows, header=("Bot", "Owner ID", "Worker")
        )
        if len(bots) > 30:
            doc.footer(f"Showing 30 of {len(bots)} bots")
        await rich.reply(message, doc)
    except Exception as e:
        log.error(f"Bots list error: {e}")
        await message.reply(f"<b>❌ Error:</b> <code>{html.escape(str(e))}</code>")


@Client.on_message(filters.command(["sys", "system", "sysstats", "systats"]) & filters.private & filters.user(OWNERS))
async def system_stats(client: Client, message: Message):
    """Show server resource usage."""
    try:
        cpu = await asyncio.to_thread(psutil.cpu_percent, 0.2)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        boot = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot

        from filestore.worker_bot.engine import worker_engine
        active_workers = worker_engine.active_count

        doc = Doc("🖥", "System Resources", "host & worker telemetry")
        doc.progress(cpu, 100, label="CPU")
        doc.progress(mem.percent, 100, label="RAM", detail=f"{mem.used // (1024**2)} / {mem.total // (1024**2)} MB")
        doc.progress(disk.percent, 100, label="Disk")
        doc.kv_table([
            ("⏱ Host Uptime", str(uptime).split(".")[0]),
            ("👷 Active Clone Workers", str(active_workers)),
        ], key_header="Item", val_header="Value")
        await rich.reply(message, doc)
        # Widgets are optional; the existing owner-only response remains the fallback.
        try:
            from plugins.widget.details import encoder_queue_snapshot, send_detail, system_payload
            running, waiting = encoder_queue_snapshot()
            payload = system_payload(cpu, mem, disk, uptime, active_workers,
                                     encoder_running=running, encoder_waiting=waiting)
            await send_detail(client, message, payload, owner_only=True)
        except Exception as exc:
            log.debug("System detail widget preparation failed (%s)", type(exc).__name__)
    except Exception as e:
        await message.reply(f"<b>❌ System stats error:</b> <code>{html.escape(str(e))}</code>")


@Client.on_message(filters.command(["check", "checkbots"]) & filters.private & filters.user(OWNERS))
async def check_bots(client: Client, message: Message):
    """Inspect local clone clients; explicit `/check fix` also runs watchdog recovery."""
    from core import stream
    async with stream.progress(message, "🩺 <i>Checking every clone bot…</i>") as status:
        await _check_bots(client, message, status)


async def _check_bots(client, message, status):
    """Report local observations without exposing raw worker/DB errors or credentials."""
    from filestore.worker_bot.engine import worker_engine
    try:
        records = await main_db.get_all_bots()
    except Exception:
        return await status.finish('⚠️ Could not load clone records. No health result is available.')

    if not isinstance(records, list):
        return await status.finish('⚠️ Could not load clone records. No health result is available.')

    ok, dead, missing, off, unknown = [], [], [], [], []
    dead_workers = {}

    def label(bot_id, bot):
        # Do not echo arbitrary database fields: malformed entries can contain
        # credentials, HTML, or other private information.
        username = bot.get('bot_username')
        name = f'@{username}' if isinstance(username, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', username, flags=re.ASCII) else 'Username unavailable'
        return f'{name} (<code>{bot_id}</code>)'

    async def probe(bot):
        if not isinstance(bot, dict):
            unknown.append('Invalid record')
            return
        deleted = bot.get('is_deleted', False)
        if deleted is True:
            return
        bot_id = bot.get('_id')
        if type(bot_id) is not int or not 0 < bot_id < 2**63 or type(deleted) is not bool:
            unknown.append('Invalid record')
            return
        name = label(bot_id, bot)
        worker = worker_engine.get_worker(bot_id)
        if worker:
            try:
                identity = await asyncio.wait_for(worker.get_me(), timeout=15)
            except Exception:
                # Telegram exceptions can contain secrets; this handler does not echo or log them.
                if worker_engine.get_worker(bot_id) is worker:
                    dead.append(name)
                    dead_workers[bot_id] = worker
                else:
                    unknown.append(name)
                return
            if worker_engine.get_worker(bot_id) is not worker or type(getattr(identity, 'id', None)) is not int or identity.id != bot_id:
                unknown.append(name)
            else:
                ok.append(name)
        elif bot.get('is_active') is True:
            missing.append(name)
        elif bot.get('is_active') is False:
            off.append(name)
        else:
            unknown.append(name)

    for i in range(0, len(records), 20):
        await asyncio.gather(*(probe(bot) for bot in records[i:i + 20]))

    fixed = ''
    if len(message.command) > 1 and message.command[1].lower() == 'fix' and (missing or dead):
        try:
            import watchdog
            dog = watchdog.dog or watchdog.Watchdog(client)
            for bot_id, observed in dead_workers.items():
                # A replacement observed after the probe must not be stopped.
                # This is not an atomic lease; another writer can still race.
                if worker_engine.get_worker(bot_id) is not observed:
                    raise RuntimeError('Worker changed')
                if await worker_engine.stop_worker(bot_id) is not True:
                    raise RuntimeError('Stop unconfirmed')
            healed = await dog.heal_workers()
            fixed = f'\n\n🔧 Watchdog reported <code>{int(healed)}</code> starts/restarts. Results are not independently verified.'
        except Exception:
            fixed = ('\n\n⚠️ Repair was not confirmed and may be partial. Check current worker and registry state before retrying; '
                     'a stop or start may already have happened.')

    def block(title, items):
        if not items:
            return ''
        shown = '\n'.join(f'• {x}' for x in items[:12])
        more = f'\n<i>… and {len(items) - 12} more</i>' if len(items) > 12 else ''
        return f'\n\n<b>{title} ({len(items)})</b>\n<blockquote expandable>{shown}{more}</blockquote>'

    text = (
        '<b>🩺 Clone bots health</b>\n\n'
        f'🟢 Local client &amp; matching identity: <code>{len(ok)}</code>\n'
        f'⚠️ Local client, check unconfirmed: <code>{len(dead)}</code>\n'
        f'⚠️ Marked active, no local client: <code>{len(missing)}</code>\n'
        f'💤 Marked inactive, no local client: <code>{len(off)}</code>\n'
        f'❔ Unknown or changed: <code>{len(unknown)}</code>'
        + block('Unconfirmed', dead) + block('No local client', missing) + block('Unknown or changed', unknown)
        + '\n\n<i>Local process snapshot only, not proof of overall runtime or exclusive ownership. '
          'Checks can be stale or inconclusive.</i>'
        + (fixed or ('\n\n<i>/check fix may stop clients and run a fleet-wide watchdog repair; '
                     'inspect state and coordinate workers first.</i>' if (dead or missing) else ''))
    )
    await status.finish(text)

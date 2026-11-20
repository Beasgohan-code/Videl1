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
from filestore.utils.registry_ids import valid_registry_id

log = LOGGER(__name__)
main_db = MainDB()


_CLONE_PAGE_SIZE = 15
_CLONE_MAX_PAGE = 1000


def _registry_view(records):
    """Separate valid non-deleted IDs from malformed records without echoing them."""
    if not isinstance(records, list):
        raise ValueError('Invalid registry result')
    visible, invalid = [], 0
    for bot in records:
        if not isinstance(bot, dict):
            invalid += 1
            continue
        deleted = bot.get('is_deleted', False)
        if deleted is True:
            continue
        bot_id = bot.get('_id')
        if type(deleted) is not bool or not valid_registry_id(bot_id):
            invalid += 1
            continue
        visible.append(bot)
    visible.sort(key=lambda bot: bot['_id'])
    return visible, invalid


def _profile(bot):
    # Only the missing field implies the legacy File Store profile; malformed
    # and unsupported values must not be relabelled as File Store or working Music.
    mode = bot.get('clone_profile', 'filestore')
    return mode if isinstance(mode, str) and mode in ('filestore', 'group', 'music') else 'unknown'


def _profile_label(bot):
    return {'filestore': 'File Store', 'group': 'Group Management',
            'music': 'Music (unsupported)', 'unknown': 'Unknown / unsupported'}[_profile(bot)]


def _local_client(worker_engine, bot_id):
    try:
        return 'Present here' if worker_engine.get_worker(bot_id) is not None else 'Not found here'
    except Exception:
        return 'Unavailable'


def _username(bot):
    username = bot.get('bot_username')
    return f'@{username}' if isinstance(username, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', username, flags=re.ASCII) else 'Username unavailable'


@Client.on_message(filters.command(["fsstats", "clonestats"]) & filters.private & filters.user(OWNERS))
async def platform_stats(client: Client, message: Message):
    """Owner registry totals and process-local observations, without mutation."""
    try:
        bots, invalid = _registry_view(await main_db.get_clone_directory_records())
    except Exception:
        return await message.reply_text('⚠️ Clone registry is unavailable. No fleet totals are available.')
    try:
        users = await main_db.total_users()
        users = f'{users:,}' if type(users) is int and users >= 0 else 'Unavailable'
    except Exception:
        users = 'Unavailable'
    try:
        from filestore.worker_bot.engine import worker_engine
        observations = [_local_client(worker_engine, bot['_id']) for bot in bots]
        local_count = str(observations.count('Present here')) if 'Unavailable' not in observations else 'Unavailable'
    except Exception:
        local_count = 'Unavailable'
    profiles = {key: 0 for key in ('filestore', 'group', 'music', 'unknown')}
    for bot in bots:
        profiles[_profile(bot)] += 1
    doc = Doc('📊', 'Clone Platform Stats', 'Registry records · local process snapshot').kv_table([
        ('👥 Registered users', users),
        ('🤖 Non-deleted clone records', f'{len(bots):,}'),
        ('📁 File Store records', f"{profiles['filestore']:,}"),
        ('🛡 Group Management records', f"{profiles['group']:,}"),
        ('🎵 Music records (unsupported)', f"{profiles['music']:,}"),
        ('❔ Unknown profiles', f"{profiles['unknown']:,}"),
        ('⚠️ Invalid records excluded', f'{invalid:,}'),
        ('👷 Local clients found', local_count),
        ('🔢 Max bots per user', str(MAX_BOTS_PER_USER)),
        ('💤 Auto-off', f'{HIBERNATION_HOURS} hours' if HIBERNATION_HOURS else 'Off'),
    ], key_header='Metric', val_header='Value')
    doc.footer('Stored modes are not runtime health. Local clients do not prove availability or exclusive ownership. Music playback is unavailable.')
    await rich.reply(message, doc)


@Client.on_message(filters.command("bots") & filters.private & filters.user(OWNERS))
async def list_all_bots(client: Client, message: Message):
    """Paginated non-deleted registry records; state is process-local only."""
    command = getattr(message, 'command', None)
    if not isinstance(command, list) or len(command) > 2:
        return await message.reply_text('Usage: <code>/bots [page]</code>')
    page = 1
    if len(command) == 2:
        raw_page = command[1]
        if not isinstance(raw_page, str) or not re.fullmatch(r'[1-9][0-9]{0,3}', raw_page, flags=re.ASCII) or int(raw_page) > _CLONE_MAX_PAGE:
            return await message.reply_text('Use a page number from 1 to 1000: <code>/bots [page]</code>')
        page = int(raw_page)
    try:
        bots, invalid = _registry_view(await main_db.get_clone_directory_records())
    except Exception:
        return await message.reply_text('⚠️ Clone registry is unavailable. No bot list is available.')
    if not bots:
        doc = Doc('🤖', 'Clone bot records').status('empty', 'No valid non-deleted records',
                 f'{invalid} invalid record(s) excluded. No records were removed.')
        return await rich.reply(message, doc)
    pages = max(1, (len(bots) + _CLONE_PAGE_SIZE - 1) // _CLONE_PAGE_SIZE)
    requested = page
    page = min(page, pages)
    try:
        from filestore.worker_bot.engine import worker_engine
    except Exception:
        worker_engine = None
    rows = []
    for bot in bots[(page - 1) * _CLONE_PAGE_SIZE:page * _CLONE_PAGE_SIZE]:
        bot_id = bot['_id']
        owner = bot.get('owner_id')
        owner = str(owner) if valid_registry_id(owner) else 'unavailable'
        active = bot.get('is_active')
        saved_state = 'Marked active' if active is True else 'Marked inactive' if active is False else 'State unknown'
        local = _local_client(worker_engine, bot_id) if worker_engine is not None else 'Unavailable'
        rows.append((_username(bot), f'ID {bot_id} · Owner {owner} · {_profile_label(bot)} · {saved_state} · {local}'))
    doc = Doc('🤖', 'Clone bot records', f'Page {page} of {pages}').table(rows, header=('Bot', 'Registry and local client'))
    adjusted = f' Requested page {requested} was adjusted.' if requested != page else ''
    page_limit = ' Pages after 1000 are not available here.' if pages > _CLONE_MAX_PAGE else ''
    doc.footer(f'{len(bots)} valid non-deleted records; {invalid} invalid excluded.{adjusted}{page_limit} '
               'Use /bots [page]. Pages can shift; local client presence is not health. Music playback is unavailable.')
    await rich.reply(message, doc)


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
        if not valid_registry_id(bot_id) or type(deleted) is not bool:
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

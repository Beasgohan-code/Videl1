"""Bot-wide admin commands: stats, users, broadcast, bans, maintenance, restart, update."""
import asyncio
import json
import logging
import os
import shutil
import sys
import time
from datetime import datetime

import psutil
from pyrogram import Client, enums, filters
from pyrogram.errors import FloodWait, InputUserDeactivated, PeerIdInvalid, UserIsBlocked
from pyrogram.types import Message

from config import ADMINS, BOT_NAME, OWNERS
from core.db import vdb
from core.menus import BOOT_TIME
from core.ui import humanbytes, readable_time

log = logging.getLogger("videl.admin")
RESTART_FILE = ".restart_msg.json"

admin_filter = filters.user(ADMINS)
owner_filter = filters.user(OWNERS)


def _target_id(message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id, " ".join(message.command[1:])
    if len(message.command) > 1 and message.command[1].lstrip("-").isdigit():
        return int(message.command[1]), " ".join(message.command[2:])
    return None, ""


# ─────────────────────────── /stats ───────────────────────────
@Client.on_message(filters.command("stats") & admin_filter)
async def stats_cmd(client: Client, message: Message):
    msg = await message.reply_text("📊 <i>Collecting stats…</i>")
    videl_users = await vdb.total_users()
    banned = len(vdb._banned)

    saver_users = premium = 0
    try:
        from database.db import db as saver_db
        saver_users = await saver_db.total_users_count()
        premium = await saver_db.col.count_documents({"is_premium": True})
    except Exception as e:
        log.warning(f"saver stats: {e}")

    enc_users = 0
    queue = 0
    try:
        from VideoEncoder import data as enc_queue
        from VideoEncoder.utils.database.access_db import db as enc_db
        enc_users = await enc_db.total_users_count()
        queue = len(enc_queue)
    except Exception as e:
        log.warning(f"encoder stats: {e}")

    clones = active_clones = running = 0
    try:
        from filestore.database.main_db import MainDB
        from filestore.worker_bot.engine import worker_engine
        mdb = MainDB()
        clones = len(await mdb.get_all_bots())
        active_clones = len(await mdb.get_all_active_bots())
        running = worker_engine.active_count
    except Exception as e:
        log.warning(f"clone stats: {e}")

    du = shutil.disk_usage(".")
    mem = psutil.virtual_memory()
    text = (
        f"<b>📊 {BOT_NAME} Statistics</b>\n\n"
        "<b>👥 Users</b>\n"
        f"<blockquote>Total: <code>{videl_users}</code> · Banned: <code>{banned}</code>\n"
        f"Saver: <code>{saver_users}</code> (💎 <code>{premium}</code> premium)\n"
        f"Encoder: <code>{enc_users}</code> · Queue: <code>{queue}</code></blockquote>\n"
        "<b>🤖 Clone bots</b>\n"
        f"<blockquote>Registered: <code>{clones}</code> · Active: <code>{active_clones}</code> · "
        f"Running now: <code>{running}</code></blockquote>\n"
        "<b>🖥 Server</b>\n"
        f"<blockquote>CPU: <code>{psutil.cpu_percent(interval=0.5)}%</code> · "
        f"RAM: <code>{mem.percent}%</code> ({humanbytes(mem.used)} / {humanbytes(mem.total)})\n"
        f"Disk: <code>{humanbytes(du.used)}</code> / <code>{humanbytes(du.total)}</code> "
        f"(free {humanbytes(du.free)})\n"
        f"Uptime: <code>{readable_time(time.time() - BOOT_TIME)}</code></blockquote>"
    )
    await msg.edit_text(text)


# ─────────────────────────── /users ───────────────────────────
@Client.on_message(filters.command("users") & admin_filter)
async def users_cmd(client: Client, message: Message):
    total = await vdb.total_users()
    blocked = await vdb.users.count_documents({"blocked": True})
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    new_today = await vdb.users.count_documents({"joined": {"$gte": today}})
    await message.reply_text(
        f"<b>👥 Users</b>\n\n<blockquote>Total: <code>{total}</code>\n"
        f"New today: <code>{new_today}</code>\nBlocked the bot: <code>{blocked}</code>\n"
        f"Banned: <code>{len(vdb._banned)}</code></blockquote>"
    )


# ─────────────────────────── /broadcast ───────────────────────────
_broadcast_running = False


@Client.on_message(filters.command("broadcast") & admin_filter)
async def broadcast_cmd(client: Client, message: Message):
    global _broadcast_running
    if not message.reply_to_message:
        return await message.reply_text(
            "<b>Usage:</b> reply to any message with <code>/broadcast</code>\n"
            "Add <code>-pin</code> to pin it in every chat."
        )
    if _broadcast_running:
        return await message.reply_text("⏳ A broadcast is already running.")
    _broadcast_running = True
    pin = "-pin" in message.command
    src = message.reply_to_message
    status = await message.reply_text("📣 <i>Broadcast started…</i>")
    done = ok = blocked = failed = 0
    started = time.time()
    try:
        async for uid in vdb.active_user_ids():
            done += 1
            try:
                sent = await src.copy(uid)
                ok += 1
                if pin:
                    try:
                        await sent.pin(both_sides=True)
                    except Exception:
                        pass
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                try:
                    await src.copy(uid)
                    ok += 1
                except Exception:
                    failed += 1
            except (UserIsBlocked, InputUserDeactivated):
                blocked += 1
                await vdb.mark_blocked(uid)
            except PeerIdInvalid:
                failed += 1
            except Exception:
                failed += 1
            if done % 50 == 0:
                try:
                    await status.edit_text(
                        f"📣 <b>Broadcasting…</b>\n\nProcessed: <code>{done}</code>\n✅ <code>{ok}</code> · "
                        f"🚫 <code>{blocked}</code> · ❌ <code>{failed}</code>"
                    )
                except Exception:
                    pass
            await asyncio.sleep(0.05)
    finally:
        _broadcast_running = False
    from core import botlog
    await botlog.event("Broadcast", (
        f"<b>👮 By:</b> {botlog.esc(message.from_user.first_name)} (<code>{message.from_user.id}</code>)\n"
        f"<b>⏱ Took:</b> {readable_time(time.time() - started)}\n"
        f"<b>📊 Total:</b> {done} · ✅ {ok} · 🚫 {blocked} · ❌ {failed}"), client=client)
    await status.edit_text(
        f"<b>✅ Broadcast finished</b> in {readable_time(time.time() - started)}\n\n"
        f"<blockquote>Total: <code>{done}</code>\nSuccess: <code>{ok}</code>\n"
        f"Blocked / deleted: <code>{blocked}</code>\nFailed: <code>{failed}</code></blockquote>"
    )


# ─────────────────────────── bans ───────────────────────────
@Client.on_message(filters.command("ban") & admin_filter)
async def ban_cmd(client: Client, message: Message):
    uid, reason = _target_id(message)
    if not uid:
        return await message.reply_text("<b>Usage:</b> <code>/ban &lt;user_id&gt; [reason]</code> (or reply)")
    if uid in ADMINS:
        return await message.reply_text("❌ You can't ban an admin.")
    await vdb.ban(uid, reason)
    from core import botlog
    await botlog.event("Ban", f"<b>👤 User:</b> <code>{uid}</code>\n<b>📝 Reason:</b> {botlog.esc(reason) or '—'}\n"
                              f"<b>👮 By:</b> {botlog.esc(message.from_user.first_name)} (<code>{message.from_user.id}</code>)",
                       client=client)
    await message.reply_text(f"🚫 Banned <code>{uid}</code>" + (f"\n<b>Reason:</b> {reason}" if reason else ""))
    try:
        await client.send_message(uid, "🚫 <b>You have been banned from using this bot.</b>"
                                  + (f"\n<b>Reason:</b> {reason}" if reason else ""))
    except Exception:
        pass


@Client.on_message(filters.command("unban") & admin_filter)
async def unban_cmd(client: Client, message: Message):
    uid, _ = _target_id(message)
    if not uid:
        return await message.reply_text("<b>Usage:</b> <code>/unban &lt;user_id&gt;</code> (or reply)")
    await vdb.unban(uid)
    from core import botlog
    await botlog.event("Unban", f"<b>👤 User:</b> <code>{uid}</code>\n"
                                f"<b>👮 By:</b> {botlog.esc(message.from_user.first_name)} (<code>{message.from_user.id}</code>)",
                       client=client)
    await message.reply_text(f"✅ Unbanned <code>{uid}</code>")
    try:
        await client.send_message(uid, "✅ <b>You have been unbanned.</b> Send /start to continue.")
    except Exception:
        pass


@Client.on_message(filters.command("banned") & admin_filter)
async def banned_cmd(client: Client, message: Message):
    users = await vdb.banned_users()
    if not users:
        return await message.reply_text("✅ No banned users.")
    lines = [f"• <code>{u['id']}</code> {u.get('name', '')} — {u.get('ban_reason') or 'no reason'}" for u in users[:100]]
    await message.reply_text(f"<b>🚫 Banned users ({len(users)})</b>\n\n" + "\n".join(lines))


# ─────────────────────────── maintenance ───────────────────────────
@Client.on_message(filters.command("maintenance") & admin_filter)
async def maintenance_cmd(client: Client, message: Message):
    arg = message.command[1].lower() if len(message.command) > 1 else ""
    if arg in ("on", "off"):
        await vdb.set_setting("maintenance", arg == "on")
        from core import botlog
        await botlog.event("Maintenance", f"<b>🛠 Maintenance:</b> {'🟢 ON' if arg == 'on' else '🔴 OFF'}\n"
                                          f"<b>👮 By:</b> {botlog.esc(message.from_user.first_name)} "
                                          f"(<code>{message.from_user.id}</code>)", client=client)
    state = await vdb.get_setting("maintenance", False)
    await message.reply_text(
        f"🛠 <b>Maintenance mode:</b> {'🟢 ON' if state else '🔴 OFF'}\n"
        "<i>Use /maintenance on|off. Admins are never blocked.</i>"
    )


# ─────────────────────────── watchdog ───────────────────────────
@Client.on_message(filters.command("watchdog") & admin_filter)
async def watchdog_cmd(client: Client, message: Message):
    import watchdog
    if not watchdog.dog:
        return await message.reply_text("🐕 Watchdog is not running.")
    if len(message.command) > 1 and message.command[1].lower() in ("run", "now", "clean"):
        status = await message.reply_text("🐕 <i>Running a sweep…</i>")
        await watchdog.dog.sweep(aggressive=message.command[1].lower() == "clean")
    else:
        status = None
    s = watchdog.dog.summary()
    last = s["last"] or {}
    text = (
        "<b>🐕 Watchdog</b>\n\n<blockquote>"
        f"Sweeps: <code>{s['sweeps']}</code>\nFiles removed: <code>{s['files_removed']}</code>\n"
        f"Space freed: <code>{s['freed_mb']} MB</code></blockquote>\n"
        "<b>Last sweep</b>\n<blockquote>"
        f"At: <code>{last.get('at', '—')}</code> ({last.get('took_s', 0)}s)\n"
        f"Files: <code>{last.get('files', 0)}</code> · Freed: <code>{last.get('freed_mb', 0)} MB</code>\n"
        f"Free disk: <code>{last.get('free_disk_gb', '?')} GB</code> · RAM: <code>{last.get('ram_mb', '?')} MB</code>\n"
        f"Flows expired: <code>{last.get('states_dropped', 0)}</code> · Clones healed: <code>{last.get('clones_healed', 0)}</code>\n"
        f"Telegram: {'🟢' if last.get('telegram_ok', True) else '🔴'}</blockquote>\n"
        "<i>/watchdog run – sweep now · /watchdog clean – aggressive cleanup</i>"
    )
    if status:
        await status.edit_text(text)
    else:
        await message.reply_text(text)


# ─────────────────────────── restart / update ───────────────────────────
async def _restart(client: Client, status: Message, reason: str = "Restart"):
    from core import botlog
    by = status.reply_to_message.from_user if status.reply_to_message and status.reply_to_message.from_user else None
    await botlog.event(reason, f"<b>♻️ {reason} requested</b>" + (f" by {botlog.esc(by.first_name)} (<code>{by.id}</code>)"
                                                             if by else ""), client=client)
    await botlog.flush(8)
    botlog.set_restart_reason(reason + (f" by {by.first_name} ({by.id})" if by else ""))
    with open(RESTART_FILE, "w") as f:
        json.dump({"chat_id": status.chat.id, "message_id": status.id}, f)
    try:
        from filestore.worker_bot.engine import worker_engine
        await worker_engine.stop_all_workers()
    except Exception:
        pass
    try:
        from VideoEncoder.utils.helper import delete_downloads
        delete_downloads()
    except Exception:
        pass
    os.execl(sys.executable, sys.executable, "run.py")


@Client.on_message(filters.command("restart") & admin_filter)
async def restart_cmd(client: Client, message: Message):
    status = await message.reply_text("♻️ <i>Restarting…</i>")
    await _restart(client, status)


@Client.on_message(filters.command("update") & owner_filter)
async def update_cmd(client: Client, message: Message):
    status = await message.reply_text("📥 <i>Pulling latest code…</i>")
    proc = await asyncio.create_subprocess_shell(
        "git pull --ff-only", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
    )
    out, _ = await proc.communicate()
    out = out.decode(errors="ignore").strip()[-3000:] or "no output"
    if proc.returncode != 0:
        return await status.edit_text(f"❌ <b>git pull failed</b>\n<pre>{out}</pre>")
    if "Already up to date" in out:
        return await status.edit_text("✅ Already up to date.")
    await status.edit_text(f"<pre>{out}</pre>\n\n♻️ <i>Restarting…</i>")
    await _restart(client, status, reason="Update")


async def announce_restart(client: Client):
    """Called from run.py after boot – edits the '/restart' message."""
    if not os.path.exists(RESTART_FILE):
        return
    try:
        with open(RESTART_FILE) as f:
            d = json.load(f)
        await client.edit_message_text(d["chat_id"], d["message_id"], "✅ <b>Restarted successfully!</b>",
                                       parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        log.warning(f"restart announce failed: {e}")
    finally:
        try:
            os.remove(RESTART_FILE)
        except Exception:
            pass

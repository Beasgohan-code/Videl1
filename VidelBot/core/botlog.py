"""
Owner log channel – one place that reports everything the owner wants to know.

Every event goes to LOG_CHANNEL (tagged #Event so the channel is searchable).
Events listed in OWNER_DM_EVENTS are also sent to every owner in DM.
If no LOG_CHANNEL is configured, all events fall back to the owners' DMs.

Events: #BotStarted #BotStopped #NewUser #Start #CloneCreated #CloneDeleted #CloneStarted
#CloneStopped #CloneRestarted #CloneTransferred #CloneRestored #CloneSettingsCopied
#CloneHibernated #CloneFailed #CloneHealed #LinkGenerated #Login #Logout #PremiumAdded
#PremiumRemoved #StarsPayment #Refund #Ban #Unban #Broadcast #Maintenance #FsubAdded
#FsubRemoved #LowDisk #AutoRestart #Restart #Update #DailyReport #Error #Subscription
#Referral #Redeem #CodesCreated #Trial #Support #AdminAction #BotPhoto
"""
import asyncio
import html
import logging
import platform
import time
from datetime import datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import Message

import config

log = logging.getLogger("videl.botlog")

BLOCKING = False           # tests set True so sends complete before assertions
_client = None             # main bot client (bound by run.py)
_start_seen: dict = {}     # user_id -> last #Start log time
_tasks: set = set()

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(config.LOG_TZ)
except Exception:  # unknown tz / no tzdata
    TZ = timezone(timedelta(hours=5, minutes=30))


REASON_FILE = ".restart_reason"


def set_restart_reason(text: str):
    try:
        with open(REASON_FILE, "w", encoding="utf-8") as f:
            f.write(text[:300])
    except OSError:
        pass


def pop_restart_reason() -> str:
    import os
    try:
        with open(REASON_FILE, encoding="utf-8") as f:
            text = f.read().strip()
        os.remove(REASON_FILE)
        return text
    except OSError:
        return ""


def bind(client):
    global _client
    _client = client


def now() -> datetime:
    return datetime.now(TZ)


def stamp() -> str:
    return now().strftime("%d %b %Y · %I:%M:%S %p %Z")


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def user_block(user, extra: str = "") -> str:
    """Standard 'who' block for a Telegram user."""
    if not user:
        return "<b>👤 User:</b> unknown"
    name = esc(" ".join(x for x in (getattr(user, "first_name", ""), getattr(user, "last_name", "")) if x))
    uname = f"@{user.username}" if getattr(user, "username", None) else "—"
    lines = [
        f"<b>👤 User:</b> <a href=\"tg://user?id={user.id}\">{name or user.id}</a>",
        f"<b>🆔 ID:</b> <code>{user.id}</code>",
        f"<b>🔗 Username:</b> {uname}",
    ]
    if getattr(user, "language_code", None):
        lines.append(f"<b>🌐 Language:</b> {esc(user.language_code)}")
    if getattr(user, "is_premium", False):
        lines.append("<b>⭐ Telegram Premium:</b> yes")
    return "\n".join(lines) + (f"\n{extra}" if extra else "")


def _targets(tag: str) -> list:
    out = []
    if config.LOG_CHANNEL:
        out.append(config.LOG_CHANNEL)
    if tag in config.OWNER_DM_EVENTS or not config.LOG_CHANNEL:
        out.extend(o for o in config.OWNERS if o not in out)
    return out


async def _deliver(client, chat_id, text, reply_markup=None):
    for attempt in range(3):
        try:
            return await client.send_message(chat_id, text, reply_markup=reply_markup,
                                             disable_web_page_preview=True)
        except FloodWait as e:
            if e.value > 120:
                break
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            # Markup can be rejected (e.g. privacy-restricted user links) → retry plain once.
            if reply_markup is not None and attempt == 0:
                reply_markup = None
                continue
            log.debug(f"log to {chat_id} failed: {e}")
            break
    return None


async def event(tag: str, body: str, client=None, reply_markup=None, dm: bool = None):
    """Send `#tag` + body (+ timestamp) to the log channel / owners. Never raises."""
    client = client or _client
    if client is None:
        log.info(f"[{tag}] {body}")
        return
    text = f"<b>#{tag}</b>\n\n{body}\n\n<i>🕒 {stamp()}</i>"
    targets = _targets(tag)
    if dm is True:
        targets += [o for o in config.OWNERS if o not in targets]
    elif dm is False:
        targets = [t for t in targets if t not in config.OWNERS] or targets[:1]

    async def _send_all():
        for chat in targets:
            await _deliver(client, chat, text, reply_markup)

    if BLOCKING:
        await _send_all()
    else:
        task = asyncio.get_event_loop().create_task(_send_all())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def raw(text: str, client=None, tag: str = "Log"):
    """Forward an already formatted message (used by the clone-bot modules)."""
    client = client or _client
    if client is None:
        return
    body = text if text.lstrip().startswith("<b>#") else f"<b>#{tag}</b>\n\n{text}"
    for chat in _targets(tag):
        if BLOCKING:
            await _deliver(client, chat, f"{body}\n\n<i>🕒 {stamp()}</i>")
        else:
            t = asyncio.get_event_loop().create_task(_deliver(client, chat, f"{body}\n\n<i>🕒 {stamp()}</i>"))
            _tasks.add(t)
            t.add_done_callback(_tasks.discard)


async def flush(timeout: float = 10):
    """Wait for queued log sends (used before shutdown)."""
    if _tasks:
        await asyncio.wait(list(_tasks), timeout=timeout)


def should_log_start(user_id: int) -> bool:
    if not config.LOG_START_EVENTS:
        return False
    last = _start_seen.get(user_id, 0)
    if time.time() - last < config.START_LOG_COOLDOWN_MIN * 60:
        return False
    _start_seen[user_id] = time.time()
    if len(_start_seen) > 50_000:
        _start_seen.clear()
    return True


def host_name() -> str:
    import os
    for env, name in (("RENDER", "Render"), ("KOYEB_APP_NAME", "Koyeb"), ("RAILWAY_ENVIRONMENT", "Railway"),
                      ("DYNO", "Heroku"), ("REPL_ID", "Replit"), ("FLY_APP_NAME", "Fly.io")):
        if os.environ.get(env):
            return name
    if os.path.exists("/.dockerenv"):
        return "Docker"
    return f"VPS ({platform.node()[:20]})"


# ───────────────────────────── summaries ─────────────────────────────
async def collect_totals() -> dict:
    """Numbers shared by the boot message and the daily report."""
    out = {"users": 0, "banned": 0, "premium": 0, "clones": 0, "clones_active": 0, "clones_running": 0,
           "enc_queue": 0, "fsub": 0, "subs": 0}
    try:
        from core.db import vdb
        out["users"] = await vdb.total_users()
        out["banned"] = len(vdb._banned)
        out["subs"] = await vdb.db["subscriptions"].count_documents({"active": True, "canceled": {"$ne": True}})
    except Exception:
        pass
    try:
        from database.db import db as saver_db
        out["premium"] = await saver_db.col.count_documents({"is_premium": True})
    except Exception:
        pass
    try:
        from filestore.database.main_db import MainDB
        from filestore.worker_bot.engine import worker_engine
        m = MainDB()
        out["clones"] = await m.bots.count_documents({"is_deleted": {"$ne": True}})
        out["clones_active"] = await m.bots.count_documents({"is_active": True})
        out["clones_running"] = worker_engine.active_count
    except Exception:
        pass
    try:
        from VideoEncoder import data as q
        out["enc_queue"] = len(q)
    except Exception:
        pass
    try:
        from core.fsub import all_channels
        out["fsub"] = len(await all_channels())
    except Exception:
        pass
    return out


async def _botapi_line() -> str:
    try:
        from core import botapi
        return await botapi.status_line()
    except Exception as e:
        return f"⚠️ {esc(str(e))[:80]}"


async def boot_report(client, me, handlers: int, boot_seconds: float):
    import pyrogram
    t = await collect_totals()
    from core.db import vdb
    maint = "🛠 ON" if vdb.cached_setting("maintenance", False) else "off"
    reason = pop_restart_reason()
    body = (
        f"<b>✅ {esc(config.BOT_NAME)} is online!</b>\n"
        + (f"<b>♻️ After:</b> {esc(reason)}\n" if reason else "") + "\n"
        "<blockquote>"
        f"<b>🤖 Bot:</b> @{me.username} (<code>{me.id}</code>)\n"
        f"<b>🖥 Host:</b> {esc(host_name())}\n"
        f"<b>🐍 Python:</b> {platform.python_version()} · <b>Pyrofork:</b> {pyrogram.__version__}\n"
        f"<b>🧩 Handlers:</b> {handlers} · <b>⏱ Boot:</b> {boot_seconds:.1f}s\n"
        f"<b>🛰 Bot API:</b> {await _botapi_line()}</blockquote>\n"
        "<blockquote>"
        f"<b>👥 Users:</b> <code>{t['users']}</code> (🚫 {t['banned']} banned)\n"
        f"<b>💎 Premium:</b> <code>{t['premium']}</code> · <b>🔁 Subscriptions:</b> <code>{t['subs']}</code>\n"
        f"<b>🤖 Clone bots:</b> <code>{t['clones_running']}</code> running / {t['clones_active']} active / "
        f"{t['clones']} total\n"
        f"<b>🔒 Force-sub channels:</b> {t['fsub']} · <b>🛠 Maintenance:</b> {maint}</blockquote>"
    )
    from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"🚀 Open @{me.username}", url=f"https://t.me/{me.username}")]])
    await event("BotStarted", body, client=client, reply_markup=kb)


async def daily_report(client=None) -> str:
    """Last-24h activity + totals."""
    since = datetime.now(timezone.utc) - timedelta(days=1)
    new_users = clones_new = payments = stars = referrals = gifts = 0
    try:
        from core.db import vdb
        new_users = await vdb.users.count_documents({"joined": {"$gte": since}})
        agg = await vdb.db["payments"].aggregate([
            {"$match": {"date": {"$gte": since}, "refunded": False}},
            {"$group": {"_id": None, "n": {"$sum": 1}, "s": {"$sum": "$stars"}}},
        ]).to_list(1)
        if agg:
            payments, stars = agg[0]["n"], agg[0]["s"]
        referrals = await vdb.users.count_documents({"joined": {"$gte": since}, "referred_by": {"$exists": True}})
        gifts = await vdb.db["payments"].count_documents({"date": {"$gte": since}, "kind": "gift"})
    except Exception as e:
        log.debug(f"daily report users: {e}")
    try:
        from filestore.database.main_db import MainDB
        clones_new = await MainDB().bots.count_documents({"created_at": {"$gte": since.replace(tzinfo=None)}})
    except Exception:
        pass
    t = await collect_totals()
    body = (
        f"<b>📊 Daily report – {now().strftime('%d %b %Y')}</b>\n\n"
        "<b>Last 24 hours</b>\n<blockquote>"
        f"👤 New users: <code>{new_users}</code>\n"
        f"🤖 New clone bots: <code>{clones_new}</code>\n"
        f"⭐ Stars payments: <code>{payments}</code> (<code>{stars}</code> ⭐) · 🎁 gifts: <code>{gifts}</code>\n"
        f"🤝 Joined via referral: <code>{referrals}</code></blockquote>\n"
        "<b>Totals</b>\n<blockquote>"
        f"👥 Users: <code>{t['users']}</code> · 💎 Premium: <code>{t['premium']}</code> · 🔁 Subs: <code>{t['subs']}</code>\n"
        f"🤖 Clones: <code>{t['clones_running']}</code> running / {t['clones']} total\n"
        f"🎬 Encoder queue: <code>{t['enc_queue']}</code></blockquote>"
    )
    try:
        import watchdog
        if watchdog.dog and watchdog.dog.last:
            last = watchdog.dog.last
            body += (f"\n<b>Server</b>\n<blockquote>💽 Free disk: <code>{last.get('free_disk_gb')} GB</code> · "
                     f"🧠 RAM: <code>{last.get('ram_mb')} MB</code>\n"
                     f"🧹 Cleaned so far: <code>{watchdog.dog.total_files}</code> files</blockquote>")
    except Exception:
        pass
    await event("DailyReport", body, client=client)
    return body


async def daily_report_loop(client):
    while True:
        n = now()
        target = n.replace(hour=config.DAILY_REPORT_HOUR % 24, minute=0, second=0, microsecond=0)
        if target <= n:
            target += timedelta(days=1)
        await asyncio.sleep((target - n).total_seconds())
        try:
            await daily_report(client)
        except Exception as e:
            log.warning(f"daily report failed: {e}")
        await asyncio.sleep(60)


# ───────────────────────────── commands ─────────────────────────────
@Client.on_message(filters.command(["logtest", "botlog"]) & filters.user(config.ADMINS))
async def logtest_cmd(client: Client, message: Message):
    targets = _targets("BotStarted")
    await event("LogTest", f"🧪 Test log requested by {user_block(message.from_user)}", client=client)
    await message.reply_text(
        "<b>📝 Log channel</b>\n\n<blockquote>"
        f"Log channel: <code>{config.LOG_CHANNEL or 'not set → owner DMs'}</code>\n"
        f"Owner DM events: {', '.join(sorted(config.OWNER_DM_EVENTS)) or '—'}\n"
        f"Log /start: {'on' if config.LOG_START_EVENTS else 'off'} (every {config.START_LOG_COOLDOWN_MIN} min/user)\n"
        f"Log logins: {'on' if config.LOG_LOGINS else 'off'} · Log links: {'on' if config.LOG_LINKS else 'off'} · Daily report: "
        f"{'%02d:00 %s' % (config.DAILY_REPORT_HOUR, config.LOG_TZ) if config.DAILY_REPORT else 'off'}</blockquote>\n"
        f"✅ Test message sent to: {', '.join(f'<code>{t}</code>' for t in targets) or 'nobody'}"
    )


@Client.on_message(filters.command(["report", "dailyreport"]) & filters.user(config.ADMINS))
async def report_cmd(client: Client, message: Message):
    from core import stream
    async with stream.progress(message, "📝 <i>Building report…</i>") as p:
        await p.finish(await daily_report(client))

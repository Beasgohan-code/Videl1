"""
Clone lifecycle: a clone nobody uses for CLONE_INACTIVE_DAYS (default 7) is deactivated.

• activity = any message / button press inside the clone (worker middleware → main_db.update_last_active)
• a day before (¼ of the period for very short periods) the owner gets a warning with ✅ Keep it running
• on deactivation the worker is stopped, is_active=False, deactivated_reason="inactive",
  the owner gets 🟢 Reactivate and the log channel a #CloneDeactivated entry
• reactivating (here or with 🟢 Start in the dashboard) restarts the inactivity clock
"""
from datetime import datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

import config
from filestore.database.main_db import MainDB
from filestore.fs_config import LOGGER

log = LOGGER(__name__)
main_db = MainDB()
SWEEP_EVERY = 3600


def period() -> timedelta | None:
    days = int(getattr(config, "CLONE_INACTIVE_DAYS", 7) or 0)
    return timedelta(days=days) if days > 0 else None


def warn_after(limit: timedelta) -> timedelta:
    return limit - min(timedelta(days=1), limit / 4)


def last_activity(bot: dict, now: datetime) -> datetime:
    last = bot.get("last_active") or bot.get("created_at") or now
    return last if last.tzinfo else last.replace(tzinfo=timezone.utc)


def ago(delta: timedelta) -> str:
    s = max(0, int(delta.total_seconds()))
    if s < 3600:
        return f"{s // 60}m ago" if s >= 60 else "just now"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d {s % 86400 // 3600}h ago"


def status_of(bot: dict, is_live: bool) -> tuple[str, str]:
    """(emoji, label) for lists and the dashboard."""
    if is_live:
        return "🟢", "ʀᴜɴɴɪɴɢ"
    if bot.get("deactivated_reason") == "inactive":
        return "💤", "ᴅᴇᴀᴄᴛɪᴠᴀᴛᴇᴅ (ɪᴅʟᴇ)"
    return "🔴", "sᴛᴏᴘᴘᴇᴅ"


async def sweep(app, worker_engine, now: datetime = None) -> dict:
    """One pass over all active clones. Returns {"warned": [ids], "deactivated": [ids]}."""
    done = {"warned": [], "deactivated": []}
    base = period()
    if not base:
        return done
    now = now or datetime.now(timezone.utc)
    owner_days: dict = {}                    # owner → idle days allowed by their plan (0 = never)
    for bot in await main_db.get_all_active_bots():
        if bot.get("is_deleted"):
            continue
        bot_id, owner = bot["_id"], bot.get("owner_id")
        if owner not in owner_days:
            try:
                from core.plans import clone_idle_days
                owner_days[owner] = await clone_idle_days(owner)
            except Exception:
                owner_days[owner] = base.days
        if not owner_days[owner]:
            continue                         # 🚀 Clone Pro: never auto-off
        limit = base if owner_days[owner] == base.days else timedelta(days=owner_days[owner])
        days = limit.days or 1
        name = bot.get("bot_username", "unknown")
        idle = now - last_activity(bot, now)
        try:
            if idle >= limit:
                await worker_engine.stop_worker(bot_id)
                await main_db.deactivate_inactive(bot_id)
                done["deactivated"].append(bot_id)
                log.info(f"💤 clone {bot_id} deactivated after {idle}")
                from core import botlog
                await botlog.event("CloneDeactivated", (
                    f"<b>🤖 Clone bot:</b> @{name} (<code>{bot_id}</code>)\n"
                    f"<b>👤 Owner:</b> <code>{owner}</code>\n"
                    f"<b>💤 Unused for:</b> {ago(idle).replace(' ago', '')} (limit {days}d)"), client=app)
                try:
                    await app.send_message(
                        owner,
                        f"<b>💤 @{name} was deactivated</b>\n\n<blockquote>Nobody used it for {days} days, so it was "
                        "switched off to free resources. Users, files, links and settings are all kept.</blockquote>\n"
                        "<i>Turn it back on whenever you need it.</i>",
                        reply_markup=Kb([[Btn("🟢 ʀᴇᴀᴄᴛɪᴠᴀᴛᴇ", callback_data=f"cl_on_{bot_id}"),
                                          Btn("📋 ᴍʏ ʙᴏᴛs", callback_data="my_bots")]]))
                except Exception:
                    pass
            elif idle >= warn_after(limit) and not bot.get("inactive_warned"):
                await main_db.mark_inactive_warned(bot_id)
                done["warned"].append(bot_id)
                left = limit - idle
                try:
                    await app.send_message(
                        owner,
                        f"<b>⏳ @{name} will be deactivated in ~{max(1, int(left.total_seconds() // 3600))}h</b>\n\n"
                        f"<blockquote>It hasn't been used for {ago(idle).replace(' ago', '')}. Clones idle for "
                        f"{days} days are switched off automatically.</blockquote>",
                        reply_markup=Kb([[Btn("✅ ᴋᴇᴇᴘ ɪᴛ ʀᴜɴɴɪɴɢ", callback_data=f"cl_keep_{bot_id}")]]))
                except Exception:
                    pass
        except Exception as e:
            log.error(f"lifecycle sweep failed for clone {bot_id}: {e}")
    return done


async def lifecycle_task(app, worker_engine):
    import asyncio
    await asyncio.sleep(300)                  # let the clones boot first
    while True:
        try:
            await sweep(app, worker_engine)
        except Exception as e:
            log.error(f"lifecycle sweep failed: {e}")
        await asyncio.sleep(SWEEP_EVERY)


async def _owned(query: CallbackQuery, bot_id: int):
    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != query.from_user.id or bot.get("is_deleted"):
        await query.answer("❌ Bot not found or access denied!", show_alert=True)
        return None
    return bot


def _dash_kb(bot_id: int):
    return Kb([[Btn("⚙️ ᴅᴀsʜʙᴏᴀʀᴅ", callback_data=f"dashboard_{bot_id}")]])


@Client.on_callback_query(filters.regex(r"^cl_keep_(\d+)$"))
async def keep_running_cb(client: Client, query: CallbackQuery):
    bot = await _owned(query, int(query.data.rsplit("_", 1)[1]))
    if not bot:
        return
    if not bot.get("is_active"):
        return await query.answer("It was already deactivated – use 🟢 Reactivate.", show_alert=True)
    await main_db.keep_alive(bot["_id"])
    days = period().days if period() else 0
    await query.answer("✅ Kept running")
    from core.ui import smart_edit
    await smart_edit(query.message, f"<b>✅ @{bot.get('bot_username', 'unknown')} stays online.</b>\n"
                                    f"<i>The {days}-day timer was reset.</i>", _dash_kb(bot["_id"]))


@Client.on_callback_query(filters.regex(r"^cl_on_(\d+)$"))
async def reactivate_cb(client: Client, query: CallbackQuery):
    bot = await _owned(query, int(query.data.rsplit("_", 1)[1]))
    if not bot:
        return
    from filestore.worker_bot.engine import worker_engine
    from core.ui import smart_edit
    bot_id = bot["_id"]
    name = bot.get("bot_username", "unknown")
    if not worker_engine.get_worker(bot_id):
        await query.answer("🟢 Starting…")
        try:
            await worker_engine.start_worker(bot)
        except Exception as e:
            return await smart_edit(query.message, f"<b>❌ Couldn't start @{name}:</b> <code>{str(e)[:200]}</code>\n"
                                                   "<i>If you revoked the token in @BotFather, add the bot again.</i>",
                                    _dash_kb(bot_id))
        if not worker_engine.get_worker(bot_id):
            return await smart_edit(query.message, f"<b>❌ @{name} didn't come online.</b> Check its token in "
                                                   "@BotFather and try again.", _dash_kb(bot_id))
    else:
        await query.answer("Already running")
    await main_db.set_bot_active(bot_id, True)
    try:
        from filestore.main_bot.plugins.my_bots import _clone_log
        await _clone_log(client, "CloneReactivated", query.from_user, bot)
    except Exception:
        pass
    await smart_edit(query.message, f"<b>🟢 @{name} is back online.</b>\n<i>Users can open their links again.</i>",
                     _dash_kb(bot_id))

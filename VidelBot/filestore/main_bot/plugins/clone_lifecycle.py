"""
Clone lifecycle: a clone nobody uses for CLONE_INACTIVE_DAYS (default 7) is deactivated.

• activity = any message / button press inside the clone (worker middleware → main_db.update_last_active)
• a day before (¼ of the period for very short periods) the owner gets a warning with ✅ Keep it running
• on File Store/Group deactivation the local worker is stopped before the registry update;
  Music writes inactive desired state for its separate worker to reconcile on the next sync.
• the owner gets 🟢 Reactivate and the log channel a #CloneDeactivated entry
• reactivating (here or with 🟢 Start in the dashboard) restarts the inactivity clock
"""
from datetime import datetime, timedelta, timezone
import asyncio
import re

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

import config
from filestore.database.main_db import MainDB, music_runtime_report_is_fresh
from filestore.fs_config import LOGGER
from filestore.utils.registry_ids import valid_registry_id

log = LOGGER(__name__)
main_db = MainDB()
SWEEP_EVERY = 3600
# A failed stop or conditional write may have succeeded. Hold those IDs locally
# rather than repeating the same destructive operation on the next sweep.
_SWEEP_HOLD_LIMIT = 1024
_sweep_holds = set()
_sweep_claims = set()
_sweep_paused = False
_SWEEP_FIELDS = ("owner_id", "is_active", "is_deleted", "bot_token_encrypted",
                 "clone_profile", "last_active", "created_at")


def _hold_sweep(bot_id):
    global _sweep_paused
    if bot_id in _sweep_holds:
        return
    if len(_sweep_holds) >= _SWEEP_HOLD_LIMIT:
        _sweep_paused = True
        log.warning("Idle sweep paused: uncertain-operation hold capacity reached.")
        return
    _sweep_holds.add(bot_id)
    log.warning("Idle sweep held for clone %s; operation requires review.", bot_id)


def _same_snapshot(old, current):
    return (isinstance(current, dict) and current.get("_id") == old["_id"]
            and all((key in old) == (key in current) and old.get(key) == current.get(key)
                    for key in _SWEEP_FIELDS))


async def _deactivate_if_idle_unchanged(bot, worker_engine):
    """Deactivate a local clone after its confirmed stop, or request Music stop by desired state.

    The controller cannot observe/stop a separate Music worker's client. For
    Music, this only CAS-updates the registry's desired state; it never treats
    the worker's report as proof of a fleet-wide stop.
    """
    bot_id = bot["_id"]
    if (_sweep_paused or bot_id in _sweep_holds or bot_id in _sweep_claims
            or len(_sweep_claims) >= _SWEEP_HOLD_LIMIT):
        return False
    _sweep_claims.add(bot_id)
    attempted, committed = False, False
    try:
        if bot.get("clone_profile") == "music":
            # Music clients belong to the dedicated process. CAS the desired
            # inactive state after a fresh snapshot; the worker observes and
            # stops on its next poll. This is not a physical-stop confirmation.
            fresh = await main_db.get_bot(bot_id)
            if _sweep_paused or not _same_snapshot(bot, fresh):
                return False
            attempted = True
            if not await main_db.deactivate_inactive_if_unchanged(bot):
                attempted = False  # acknowledged CAS conflict: nothing was stopped or changed
                return False
            committed = True
            return True

        observed = worker_engine.get_worker(bot_id)
        if observed is None:
            return False  # a missing local client is not a confirmed fleet-wide stop
        fresh = await main_db.get_bot(bot_id)
        if (_sweep_paused or not _same_snapshot(bot, fresh)
                or worker_engine.get_worker(bot_id) is not observed):
            return False
        attempted = True
        if await asyncio.wait_for(worker_engine.stop_worker(bot_id), timeout=10) is not True:
            return False
        if worker_engine.get_worker(bot_id) is not None:
            return False
        if not await main_db.deactivate_inactive_if_unchanged(bot):
            return False
        committed = True
        return True
    finally:
        if attempted and not committed:
            _hold_sweep(bot_id)
        _sweep_claims.discard(bot_id)


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
    for bot in await main_db.get_active_clone_sweep_records():
        if (not isinstance(bot, dict) or bot.get("is_deleted", False) is not False
                or bot.get("is_active") is not True or not valid_registry_id(bot.get("_id"))
                or not valid_registry_id(bot.get("owner_id"))):
            continue
        bot_id, owner = bot["_id"], bot["owner_id"]
        if owner not in owner_days:
            try:
                from core.plans import clone_idle_days
                owner_days[owner] = await clone_idle_days(owner)
            except Exception:
                owner_days[owner] = None  # never replace an unknown plan with a shorter default
        if type(owner_days[owner]) is not int or not 0 <= owner_days[owner] <= 999999999:
            continue
        if owner_days[owner] == 0:
            continue                         # 🚀 Clone Pro: never auto-off
        limit = base if owner_days[owner] == base.days else timedelta(days=owner_days[owner])
        days = limit.days or 1
        name = bot.get("bot_username")
        name = (name if isinstance(name, str)
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name, flags=re.ASCII)
                else "unavailable")
        try:
            idle = now - last_activity(bot, now)
            if idle >= limit:
                if not await _deactivate_if_idle_unchanged(bot, worker_engine):
                    continue
                done["deactivated"].append(bot_id)
                if bot.get("clone_profile") == "music":
                    log.info("Music clone %s marked inactive in desired state; worker stop is pending sync.", bot_id)
                else:
                    log.info("Clone %s marked inactive after confirmed local stop.", bot_id)
                try:
                    from core import botlog
                    await botlog.event("CloneDeactivated", (
                        f"<b>🤖 Clone bot:</b> @{name} (<code>{bot_id}</code>)\n"
                        f"<b>👤 Owner:</b> <code>{owner}</code>\n"
                        f"<b>💤 Recorded inactivity:</b> {ago(idle).replace(' ago', '')} (limit {days}d)"), client=app)
                except Exception:
                    log.warning("Could not send clone inactivity log.")
                try:
                    detail = (
                        "The registry now requests Music stop; the dedicated worker will apply it on its next sync. "
                        "This request is not proof that a separate process has already stopped."
                        if bot.get("clone_profile") == "music" else
                        "The local client stop was confirmed and the registry was updated."
                    )
                    await app.send_message(
                        owner,
                        f"<b>💤 @{name} was marked inactive</b>\n\n<blockquote>After {days} days of recorded inactivity, "
                        f"{detail} Users, files, links and settings are kept.</blockquote>\n"
                        "<i>Runtime reports are local observations, not proof about other processes.</i>",
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
                        f"<b>⏳ @{name} may be marked inactive in ~{max(1, int(left.total_seconds() // 3600))}h</b>\n\n"
                        f"<blockquote>It has no recorded activity for {ago(idle).replace(' ago', '')}. "
                        f"The idle threshold is {days} days; actual state can change.</blockquote>",
                        reply_markup=Kb([[Btn("✅ ᴋᴇᴇᴘ ɪᴛ ʀᴜɴɴɪɴɢ", callback_data=f"cl_keep_{bot_id}")]]))
                except Exception:
                    pass
        except Exception:
            log.warning("Idle lifecycle sweep could not be confirmed for clone %s.", bot_id)
    return done


async def lifecycle_task(app, worker_engine):
    import asyncio
    await asyncio.sleep(300)                  # let the clones boot first
    while True:
        try:
            await sweep(app, worker_engine)
        except Exception:
            log.warning("Idle lifecycle sweep could not be completed.")
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
    """Reactivate an owned inactive clone; report ambiguous effects conservatively."""
    from pyrogram import enums
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    uid = getattr(user, "id", None)
    if (type(uid) is not int or not 0 < uid < 2**63
            or getattr(user, "is_bot", None) is not False
            or not chat or getattr(chat, "type", None) != enums.ChatType.PRIVATE
            or getattr(chat, "id", None) != uid):
        return await query.answer("❌ Bot not found or access denied!", show_alert=True)
    requested_id = int(query.data.rsplit("_", 1)[1])
    if not 0 < requested_id < 2**63:
        return await query.answer("❌ Bot not found or access denied!", show_alert=True)
    bot = await _owned(query, requested_id)
    if not bot:
        return
    from filestore.worker_bot.engine import worker_engine
    from core.ui import smart_edit
    bot_id = bot.get("_id")
    if bot.get("clone_profile") == "music":
        if (bot.get("is_active") is not False
                or not isinstance(bot.get("music_session_encrypted"), str)
                or not bot["music_session_encrypted"].startswith("enc:")):
            return await query.answer("Music clone isn't in a startable state; open its dashboard.", show_alert=True)
        if music_runtime_report_is_fresh(bot):
            return await query.answer("Music worker recently reported active. Use its dashboard controls.",
                                      show_alert=True)
        try:
            changed = await main_db.set_profile_active(bot_id, uid, "music", True)
        except Exception:
            return await smart_edit(query.message,
                                    "<b>⚠️ Music start request wasn't confirmed.</b>\n"
                                    "Reopen the Music dashboard before retrying.", _dash_kb(bot_id))
        if not changed:
            return await query.answer("Clone state changed. Reopen its dashboard.", show_alert=True)
        await query.answer("Music start requested; the Music clone worker will reconcile it.")
        stale_note = ("The last worker report was stale; this does not prove a separate process has stopped. "
                      if bot.get("music_runtime_state") in ("starting", "running", "stopping", "uncertain")
                      else "")
        return await smart_edit(
            query.message,
            "<b>🎵 Music start requested.</b>\n"
            f"{stale_note}The dedicated Music clone worker must be running to apply it; this is a desired-state update, not a live-client confirmation.",
            _dash_kb(bot_id))
    # Old buttons must not start or reset an already-active or malformed record.
    if (not valid_registry_id(bot_id) or bot_id != requested_id
            or not valid_registry_id(bot.get("owner_id")) or bot["owner_id"] != uid
            or bot.get("is_active") is not False
            or bot.get("music_setup_pending") is True
            or bot.get("is_deleted", False) is not False
            or not isinstance(bot.get("bot_token_encrypted"), str)
            or not bot["bot_token_encrypted"]
            or ("clone_profile" in bot and bot["clone_profile"] not in ("filestore", "group"))):
        return await query.answer("This clone is not in a supported inactive state. Open its dashboard.", show_alert=True)

    await query.answer("Starting clone…")
    try:
        # A local client on an inactive record is contradictory; do not adopt it.
        if worker_engine.get_worker(bot_id) is not None:
            raise RuntimeError("Client already present on inactive record")
        await worker_engine.start_worker(bot)
        if worker_engine.get_worker(bot_id) is None:
            raise RuntimeError("No local client found after start")
        if not await main_db.reactivate_inactive_if_unchanged(bot):
            raise RuntimeError("Registry changed during reactivation")
    except Exception:
        # A start or write may have succeeded even if its response was lost.
        # Do not disclose error text or automatically start, stop, or write again.
        log.warning("Clone reactivation could not be confirmed.")
        return await smart_edit(query.message,
                                "<b>⚠️ Reactivation could not be confirmed.</b>\n\n"
                                "The client or registry may have changed. Check the dashboard and "
                                "contact the operator before retrying.", _dash_kb(bot_id))

    try:
        from filestore.main_bot.plugins.my_bots import _clone_log
        import re
        name = bot.get("bot_username")
        safe_name = (name if isinstance(name, str)
                     and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name, flags=re.ASCII)
                     else "unavailable")
        await _clone_log(client, "CloneReactivated", query.from_user,
                         {"_id": bot_id, "bot_username": safe_name})
    except Exception:
        pass
    await smart_edit(query.message,
                     "<b>✅ Clone marked active; a local client was found.</b>\n"
                     "<i>This snapshot does not guarantee availability. Verify the bot in Telegram.</i>",
                     _dash_kb(bot_id))

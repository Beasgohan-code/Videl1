
import html
import asyncio
import re
from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from filestore.fs_config import LOGGER, BACKEND_API_SECRET, BACKEND_API_URL
from filestore.database.main_db import MainDB
from filestore.utils.security import mask_token, decrypt_token
from filestore.utils.registry_ids import valid_registry_id

log = LOGGER(__name__)


async def _clone_log(client, tag: str, user, bot: dict, extra: str = ""):
    """Report a clone-bot event to the owner log channel."""
    try:
        from core import botlog
        body = (f"{botlog.user_block(user)}\n\n"
                f"<b>🤖 Clone bot:</b> @{bot.get('bot_username', 'unknown')} (<code>{bot.get('_id')}</code>)")
        if extra:
            body += f"\n{extra}"
        await botlog.event(tag, body, client=client)
    except Exception:
        log.warning("Clone event logging failed.")
main_db = MainDB()


def _rotate_row(bot: dict):
    try:
        from filestore.main_bot.plugins.managed_bots import rotate_row
        return rotate_row(bot)
    except Exception:
        return []

def _navigation_blocked(user_id, *, allow_registration=False):
    """Do not discard another active wizard; only the list permits registration."""
    from filestore.main_bot.plugins.create_bot import _creation_state
    state = _creation_state.get(user_id)
    return state is not None and not (allow_registration and state.get("step") == "registering")


async def _busy_notice(query):
    from filestore.main_bot.plugins.create_bot import _creation_state
    if (_creation_state.get(query.from_user.id) or {}).get("step") == "registering":
        return await query.answer("Registration is in progress. Check My Bots and wait before opening its dashboard.", show_alert=True)
    return await query.answer(
        "A bot setup or setting is in progress. Continue it or use /cancel in private chat.", show_alert=True)


# =============================================================================
# CALLBACK: My Bots (list all bots)
# =============================================================================
@Client.on_callback_query(filters.regex(r"^my_bots$"))
async def my_bots_callback(client: Client, query: CallbackQuery):
    """List all bots owned by the user."""
    user_id = query.from_user.id

    if _navigation_blocked(user_id, allow_registration=True):
        return await _busy_notice(query)
    bots = await main_db.get_user_bots(user_id)
    if _navigation_blocked(user_id, allow_registration=True):
        return await _busy_notice(query)

    if not bots:
        from core import rich
        from core.ui import styled_button
        doc = (rich.Doc("📋", "My bots", "Your clone workspace")
               .status("empty", "No bots yet", "Create your first clone to get started"))
        markup = InlineKeyboardMarkup([
            [styled_button("⚡ Create a bot", style="success", callback_data="create_bot")],
            [styled_button("🔙 Back to menu", style="primary", callback_data="back_menu")],
        ])
        await rich.edit(query.message, doc, reply_markup=markup)
        await query.answer()
        return

    # Build buttons for each bot
    from filestore.worker_bot.engine import worker_engine
    from filestore.main_bot.plugins.clone_lifecycle import period, status_of
    from core.ui import styled_button
    buttons = []
    counts = {"🟢": 0, "💤": 0, "🔴": 0}
    for bot in bots:
        bot_id = bot["_id"]
        emoji, _label = status_of(bot, worker_engine.get_worker(bot_id) is not None)
        counts[emoji] += 1
        username = bot.get("bot_username")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username, flags=re.ASCII):
            username = "unknown"
        buttons.append([styled_button(f"{emoji} @{username}", style="primary",
                                      callback_data=f"dashboard_{bot_id}")])
    from core.plans import clone_limit
    max_bots = await clone_limit(query.from_user.id)
    if _navigation_blocked(user_id, allow_registration=True):
        return await _busy_notice(query)
    if len(bots) < max_bots:
        buttons.append([styled_button("⚡ Create another", style="success", callback_data="create_bot")])
    else:   # Disabled status chip; plans remain an explicit action.
        buttons.append([styled_button(f"🔒 Limit reached · {len(bots)}/{max_bots}", disabled=True)])
        buttons.append([styled_button("💳 More bots · plans", style="primary", callback_data="vx_home")])
    buttons.append([styled_button("🔙 Back to menu", style="primary", callback_data="back_menu")])

    summary = " · ".join(f"{k} {v}" for k, v in counts.items() if v) or "No running clones yet"
    limit = period()
    note = (f"Clones unused for {limit.days} days switch off automatically; open a clone to reactivate it."
            if limit else "")
    from core import rich
    doc = (rich.Doc("📋", "My bots", f"{len(bots)} / {max_bots if max_bots < 999 else '∞'} clones")
           .status("success" if counts["🟢"] else "info", "Live snapshot", summary)
           .text("Choose a bot to open its dashboard. Status icons show this process's observation, not a health guarantee."))
    if note:
        doc.footer(note)
    await rich.edit(query.message, doc, InlineKeyboardMarkup(buttons))
    await query.answer()


# =============================================================================
# CALLBACK: Bot Dashboard (main dashboard for a specific bot)
# =============================================================================

def _dashboard_identity(query):
    """Only direct human owners in their own private conversation may view it."""
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    uid = getattr(user, "id", None)
    return (type(uid) is int and 0 < uid < 2**63
            and getattr(user, "is_bot", None) is False
            and getattr(chat, "type", None) == enums.ChatType.PRIVATE
            and getattr(chat, "id", None) == uid)


async def _mode_dashboard(query, bot, mode):
    """Read-only group/unsupported view; existing controls enforce their own rules."""
    from core.ui import smart_edit
    bot_id = bot["_id"]
    name = bot.get("bot_username")
    name = (name if isinstance(name, str)
            and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name, flags=re.ASCII)
            else None)
    saved = bot.get("is_active")
    state = "Active" if saved is True else "Inactive" if saved is False else "Unknown"
    if mode == "group":
        try:
            from filestore.worker_bot.engine import worker_engine
            local = "Present" if worker_engine.get_worker(bot_id) is not None else "Not found"
        except Exception:
            local = "Unavailable"
        import config
        gate = ("Single-instance group profiles are enabled on this process."
                if config.CLONE_PROFILES_SINGLE_INSTANCE else
                "Starting group profiles is disabled until the operator verifies a single current worker.")
        text = ("<b>🛡 ɢʀᴏᴜᴘ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ</b>\n\n"
                f"<b>Bot:</b> {('@' + name) if name else 'Username unavailable'}\n"
                f"<b>Bot ID:</b> <code>{bot_id}</code>\n"
                f"<b>Saved state:</b> {state} · <b>Local client:</b> {local}\n\n"
                "<b>Get started</b>\n"
                "① Verify the bot identity, add it to your group and grant the rights it needs.\n"
                "② Use <code>/gcheck</code> in the group to inspect bot rights, "
                "then <code>/help</code> for the group guide.\n"
                "③ Moderation, rules, welcomes, notes and text filters depend on group permissions.\n"
                f"④ In this private chat, use <code>/clonegroupcheck {bot_id} &lt;group_id&gt;</code> "
                "for a read-only rights snapshot (you must be a group admin).\n\n"
                "File Store commands are not served in this mode; existing File Store data is retained. "
                "Music voice-chat playback is unavailable.\n\n"
                f"<i>{gate} Local client presence is not a health check or proof of exclusive ownership. "
                "This view is a snapshot; permissions and state can change.</i>")
        buttons = []
        if name:
            buttons.append([InlineKeyboardButton("➕ Add to group", url=f"https://t.me/{name}?startgroup"),
                            InlineKeyboardButton("🤖 Open bot", url=f"https://t.me/{name}")])
        buttons.append([InlineKeyboardButton("🧩 Bot profile", callback_data=f"clprofile_{bot_id}")])
        buttons.append([InlineKeyboardButton("📦 ᴛʀᴀɴsꜰᴇʀ", callback_data=f"transfer_{bot_id}"),
                        InlineKeyboardButton("🗑 ᴅᴇʟᴇᴛᴇ", callback_data=f"confirm_delete_{bot_id}")])
        buttons.extend(row for row in [_rotate_row(bot)] if row)
        if local == "Present" or (local == "Not found" and config.CLONE_PROFILES_SINGLE_INSTANCE is True
                                  and type(saved) is bool):
            buttons.append([InlineKeyboardButton("🔴 sᴛᴏᴘ" if local == "Present" else "🟢 sᴛᴀʀᴛ",
                                                 callback_data=f"toggle_bot_{bot_id}")])
    else:
        label = "Music" if mode == "music" else "Unknown / unsupported"
        if mode == "music":
            text = ("<b>🎵 ᴍᴜsɪᴄ</b>\n\n"
                    f"<b>Bot:</b> {('@' + name) if name else 'Username unavailable'}\n"
                    f"<b>Bot ID:</b> <code>{bot_id}</code>\n"
                    f"<b>Saved profile:</b> {label} · <b>Saved state:</b> {state}\n\n"
                    "<b>Get started</b>\n"
                    "① Set up the assistant account (paste a session string or create one here with a guided login).\n"
                    "② Add this bot to your group and start a voice chat.\n"
                    "③ Use <code>/play &lt;song&gt;</code> in the group; the assistant joins and plays.\n\n"
                    "Music clones run under the standalone music program, not this worker; restart that program after setup to load this clone. "
                    "Live Telegram voice-chat playback is not verified by this dashboard. "
                    "Your existing File Store data has not been removed.")
            buttons = [[InlineKeyboardButton("🎵 Set up assistant session", callback_data=f"clmusic_{bot_id}")],
                       [InlineKeyboardButton("🧩 Bot profile", callback_data=f"clprofile_{bot_id}")]]
        else:
            text = ("<b>🧩 ᴄʟᴏɴᴇ ᴘʀᴏꜰɪʟᴇ</b>\n\n"
                    f"<b>Bot:</b> {('@' + name) if name else 'Username unavailable'}\n"
                    f"<b>Bot ID:</b> <code>{bot_id}</code>\n"
                    f"<b>Saved profile:</b> {label} · <b>Saved state:</b> {state}\n\n"
                    "This profile cannot be started here. "
                    "Your existing File Store data has not been removed. "
                    "Contact the operator before attempting recovery; this view does not check other workers.")
            buttons = [[InlineKeyboardButton("🧩 Bot profile", callback_data=f"clprofile_{bot_id}")]]
    buttons.append([InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴍʏ ʙᴏᴛs", callback_data="my_bots")])
    await smart_edit(query.message, text, InlineKeyboardMarkup(buttons))
    try:
        await query.answer()
    except Exception:
        pass



@Client.on_callback_query(filters.regex(r"^dashboard_(\d+)$"))
async def dashboard_callback(client: Client, query: CallbackQuery):
    """Show the dashboard for a specific bot."""
    import re
    match = re.search(r"_(\d+)$", query.data)
    if not match:
        await query.answer("❌ Invalid bot!", show_alert=True)
        return

    bot_id = int(match.group(1))
    if not _dashboard_identity(query) or not 0 < bot_id < 2**63:
        return await query.answer("❌ Bot not found or access denied!", show_alert=True)
    user_id = query.from_user.id

    if _navigation_blocked(user_id):
        return await _busy_notice(query)

    # Verify ownership
    bot = await main_db.get_bot(bot_id)
    if _navigation_blocked(user_id):
        return await _busy_notice(query)
    if (not bot or not valid_registry_id(bot.get("_id")) or bot["_id"] != bot_id
            or not valid_registry_id(bot.get("owner_id")) or bot["owner_id"] != user_id
            or bot.get("is_deleted", False) is not False):
        await query.answer("❌ Bot not found or access denied!", show_alert=True)
        return

    if bot.get("music_setup_pending") is True:
        name = bot.get("bot_username")
        safe_name = (name if isinstance(name, str)
                     and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", name, flags=re.ASCII)
                     else None)
        text = ("<b>🎵 Music setup pending</b>\n\n"
                f"<b>Bot:</b> {('@' + safe_name) if safe_name else 'Username unavailable'}\n"
                f"<b>Bot ID:</b> <code>{bot_id}</code>\n\n"
                "This clone is safely paused until an assistant account session is saved. "
                "Its Music profile will only be selected after encrypted session storage succeeds. "
                "Then restart the standalone music program to load it.")
        buttons = [[InlineKeyboardButton("🎵 Set up assistant session", callback_data=f"clmusic_{bot_id}")],
                   [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴍʏ ʙᴏᴛs", callback_data="my_bots")]]
        from core.ui import smart_edit
        await smart_edit(query.message, text, InlineKeyboardMarkup(buttons))
        try:
            await query.answer()
        except Exception:
            pass
        return

    mode = bot.get("clone_profile", "filestore")
    if mode != "filestore":
        return await _mode_dashboard(query, bot, mode)

    from filestore.worker_bot.engine import worker_engine
    is_live = worker_engine.get_worker(bot_id) is not None
    from datetime import datetime, timezone
    from filestore.main_bot.plugins.clone_lifecycle import ago, last_activity, period, status_of
    emoji, label = status_of(bot, is_live)
    status = f"{emoji} {label}"
    now = datetime.now(timezone.utc)
    idle = now - last_activity(bot, now)
    limit = period()
    if is_live and limit:
        left = limit - idle
        auto_off = f"in {ago(left).replace(' ago', '')}" if left.total_seconds() > 0 else "soon"
        activity_line = f"◈ <b>ʟᴀsᴛ ᴜsᴇᴅ:</b> {ago(idle)} · <i>ᴀᴜᴛᴏ-ᴏꜰꜰ {auto_off}</i>\n"
    else:
        activity_line = f"◈ <b>ʟᴀsᴛ ᴜsᴇᴅ:</b> {ago(idle)}\n"
    username = bot.get("bot_username", "unknown")
    channel = bot.get("log_channel_id", "Not set")

    # Render registry details as structured, escaped Richgram values. Stored
    # settings are operator/user data: never interpolate them as raw HTML.
    settings = bot.get("settings")
    settings = settings if isinstance(settings, dict) else {}
    shortener = bot.get("shortener")
    shortener = shortener if isinstance(shortener, dict) else {}
    auto_del = settings.get("auto_delete_time", 0)
    auto_del_text = f"{auto_del}s" if isinstance(auto_del, (int, float)) and auto_del > 0 else "Disabled"
    shortener_status = "Enabled" if shortener.get("enabled") is True else "Disabled"
    protect_status = "On" if settings.get("protect_content") is True else "Off"
    perm_link_status = "On" if settings.get("permanent_link") is True else "Off"
    maint_status = "On" if settings.get("maintenance_mode") is True else "Off"

    from core import rich
    dashboard_doc = (rich.Doc("⚙️", "Bot dashboard", "File Store clone · owner controls")
                     .status("success" if is_live else "warning", "Runtime", status)
                     .text(activity_line.strip())
                     .kv_table([
                         ("Bot", rich.code("@" + str(username))),
                         ("Log channel", rich.code(channel)),
                         ("Auto-delete", auto_del_text),
                         ("Shortener", shortener_status),
                         ("Content protection", protect_status),
                         ("Permanent links", perm_link_status),
                         ("Maintenance", maint_status),
                     ])
                     .footer("Local client status is an observation, not a health check or guarantee."))

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 ʟᴏɢ ᴄʜᴀɴɴᴇʟ", callback_data=f"set_channel_{bot_id}"),
            InlineKeyboardButton("⏱ ᴀᴜᴛᴏ-ᴅᴇʟ", callback_data=f"auto_delete_{bot_id}"),
        ],
        [
            InlineKeyboardButton("🔗 ᴘᴇʀᴍᴀɴᴇɴᴛ ʟɪɴᴋ", callback_data=f"toggle_permanent_link_{bot_id}"),
        ],
        [
            InlineKeyboardButton("🔗 ꜰsᴜʙ", callback_data=f"fsub_{bot_id}"),
            InlineKeyboardButton("🔗 sʜᴏʀᴛᴇɴᴇʀ", callback_data=f"shortener_{bot_id}"),
        ],
        [
            InlineKeyboardButton("👥 ᴀᴅᴍɪɴs", callback_data=f"admins_{bot_id}"),
            InlineKeyboardButton("📩 sᴛᴀʀᴛ ᴄꜰɢ", callback_data=f"startcfg_{bot_id}"),
        ],
        [
            InlineKeyboardButton("📝 ᴄᴀᴘᴛɪᴏɴ", callback_data=f"captioncfg_{bot_id}"),
            InlineKeyboardButton("📊 sᴛᴀᴛs", callback_data=f"stats_{bot_id}"),
        ],
        [
            InlineKeyboardButton("🛡️ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ", callback_data=f"toggle_protect_{bot_id}"),
            InlineKeyboardButton("📦 ᴛʀᴀɴsꜰᴇʀ", callback_data=f"transfer_{bot_id}"),
        ],
        [
            InlineKeyboardButton("🔄 ʀᴇsᴛᴀʀᴛ", callback_data=f"restart_bot_{bot_id}"),
            InlineKeyboardButton("🚫 ʙᴀɴɴᴇᴅ", callback_data=f"banned_list_{bot_id}"),
        ],
        [
            InlineKeyboardButton("📋 ᴄʟᴏɴᴇ sᴇᴛᴛɪɴɢs", callback_data=f"clone_settings_{bot_id}"),
            InlineKeyboardButton("💾 ʙᴀᴄᴋᴜᴘ", callback_data=f"backup_bot_{bot_id}"),
        ],
        [
            InlineKeyboardButton("📥 ʀᴇsᴛᴏʀᴇ", callback_data=f"restore_bot_{bot_id}"),
            InlineKeyboardButton("🛠 ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ", callback_data=f"toggle_maint_{bot_id}"),
        ],
        [InlineKeyboardButton("🧩 Bot profile", callback_data=f"clprofile_{bot_id}")],
        [
            InlineKeyboardButton("✨ ᴇxᴛʀᴀs", callback_data=f"xtr_{bot_id}"),
            InlineKeyboardButton("📈 ᴀɴᴀʟʏᴛɪᴄs", callback_data=f"xan_{bot_id}"),
        ],
        *[row for row in [_rotate_row(bot)] if row],
        [
            InlineKeyboardButton("🗑 ᴅᴇʟᴇᴛᴇ", callback_data=f"confirm_delete_{bot_id}"),
        ],
        [
            InlineKeyboardButton(
                "🔴 sᴛᴏᴘ" if is_live else "🟢 sᴛᴀʀᴛ",
                callback_data=f"toggle_bot_{bot_id}",
            ),
        ],
        [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴍʏ ʙᴏᴛs", callback_data="my_bots")],
    ])

    await rich.edit(query.message, dashboard_doc, keyboard)

    try:
        await query.answer()
    except Exception:
        pass


# =============================================================================
# CALLBACK: Toggle Bot (start/stop)
# =============================================================================

@Client.on_callback_query(filters.regex(r"^toggle_bot_(\d+)$"))
async def toggle_bot_callback(client: Client, query: CallbackQuery):
    """Start or stop a bot."""
    import re
    match = re.match(r"^toggle_bot_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot["owner_id"] != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    from filestore.worker_bot.engine import worker_engine
    is_live = worker_engine.get_worker(bot_id) is not None

    if is_live:
        # Answer immediately before slow stop
        await query.answer("🔴 sᴛᴏᴘᴘɪɴɢ...", show_alert=False)
        stopped = await worker_engine.stop_worker(bot_id)
        if stopped is False:
            from core.ui import smart_edit
            return await smart_edit(query.message,
                                    "<b>⚠️ Stop could not be confirmed.</b>\n\n"
                                    "The bot may still be running. Profile changes are blocked; "
                                    "contact the operator before taking further action.",
                                    InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")]]))
        await main_db.set_bot_active(bot_id, False)
        await _clone_log(client, "CloneStopped", query.from_user, bot)
    else:
        # Answer immediately before slow start
        await query.answer("🟢 sᴛᴀʀᴛɪɴɢ...", show_alert=False)
        bot_doc = await main_db.get_bot(bot_id)
        try:
            await worker_engine.start_worker(bot_doc)
            await main_db.set_bot_active(bot_id, True)
            await _clone_log(client, "CloneStarted", query.from_user, bot)
        except Exception:
            # Client/Telegram errors may contain credentials; do not echo them.
            log.error("Failed to start clone %s.", bot_id)
            await _clone_log(client, "CloneFailed", query.from_user, bot, "<b>❌ Start could not be confirmed.</b>")
            from core.ui import smart_edit
            return await smart_edit(query.message,
                                    "<b>⚠️ Start could not be confirmed.</b>\n\n"
                                    "Check the dashboard and contact the operator before retrying.",
                                    InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")]]))

    # Refresh dashboard
    await dashboard_callback(client, query)


# =============================================================================
# CALLBACK: Toggle Protect Content
# =============================================================================

@Client.on_callback_query(filters.regex(r"^toggle_protect_(\d+)$"))
async def toggle_protect_callback(client: Client, query: CallbackQuery):
    """Toggle content protection for a bot."""
    import re
    match = re.match(r"^toggle_protect_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot["owner_id"] != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    settings = bot.get("settings", {})
    current = settings.get("protect_content", False)
    new_val = not current

    await main_db.update_setting(bot_id, "protect_content", new_val)
    
    # Restart worker to pick up changes
    from filestore.worker_bot.engine import worker_engine
    if worker_engine.get_worker(bot_id):
        await worker_engine.stop_worker(bot_id)
        bot_doc = await main_db.get_bot(bot_id)
        await worker_engine.start_worker(bot_doc)

    await query.answer(f"🛡️ Protection: {'Enabled' if new_val else 'Disabled'}")
    await dashboard_callback(client, query)


# =============================================================================
# CALLBACK: Toggle Permanent Link
# =============================================================================

@Client.on_callback_query(filters.regex(r"^toggle_permanent_link_(\d+)$"))
async def toggle_permanent_link_callback(client: Client, query: CallbackQuery):
    """Toggle permanent link feature for a bot."""
    import re
    match = re.match(r"^toggle_permanent_link_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot["owner_id"] != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    settings = bot.get("settings", {})
    current = settings.get("permanent_link", False)
    new_val = not current

    if new_val and not BACKEND_API_URL:
        await query.answer("🔗 Permanent links need the owner's BACKEND_API_URL (Cloudflare worker) – "
                           "it isn't configured on this Videl instance.", show_alert=True)
        return

    await main_db.update_setting(bot_id, "permanent_link", new_val)

    # Register the user with the permanent-link backend
    if new_val:
        import aiohttp
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                async with session.post(
                    f"{BACKEND_API_URL.rstrip('/')}/api/user",
                    json={"userId": str(user_id), "botUsername": bot.get("bot_username", "unknown")},
                    headers={"Authorization": f"Bearer {BACKEND_API_SECRET}"},
                ) as resp:
                    resp_data = await resp.json(content_type=None)
                    if not (resp_data or {}).get("success"):
                        LOGGER.warning(f"Permanent link backend refused user {user_id}: {resp_data}")
        except Exception as e:
            LOGGER.error(f"Permanent Link API Error: {e}")

    # Restart worker to pick up changes
    from filestore.worker_bot.engine import worker_engine
    if worker_engine.get_worker(bot_id):
        await worker_engine.stop_worker(bot_id)
        bot_doc = await main_db.get_bot(bot_id)
        await worker_engine.start_worker(bot_doc)

    await query.answer(f"🔗 Permanent Link: {'Enabled' if new_val else 'Disabled'}")
    await dashboard_callback(client, query)


# =============================================================================
# CALLBACK: Confirm Delete Bot
# =============================================================================

@Client.on_callback_query(filters.regex(r"^confirm_delete_(\d+)$"))
async def confirm_delete_callback(client: Client, query: CallbackQuery):
    """Show deletion confirmation."""
    import re
    match = re.match(r"^confirm_delete_(\d+)$", query.data)
    bot_id = int(match.group(1))

    bot = await main_db.get_bot(bot_id)
    if not bot or bot["owner_id"] != query.from_user.id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    username = html.escape(str(bot.get("bot_username", "unknown")))

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ 𝗗𝗘𝗟𝗘𝗧𝗘 @{username}?\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>This removes the clone from My Bots and attempts to stop its worker in this process. "
        f"It does <b>not</b> delete the Telegram bot or revoke its token. "
        f"The stored token, settings and associated data remain in the database for restoration or transfer. "
        f"Other workers may still need to be stopped separately.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ ʏᴇs, ᴅᴇʟᴇᴛᴇ", callback_data=f"delete_bot_{bot_id}"),
                InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"dashboard_{bot_id}"),
            ],
        ]),
    )
    await query.answer()


# =============================================================================
# CALLBACK: Delete Bot
# =============================================================================

@Client.on_callback_query(filters.regex(r"^delete_bot_(\d+)$"))
async def delete_bot_callback(client: Client, query: CallbackQuery):
    """Soft-delete a bot (keep data for transfer)."""
    import re
    match = re.match(r"^delete_bot_(\d+)$", query.data)
    bot_id = int(match.group(1))

    bot = await main_db.get_bot(bot_id)
    if not bot or bot["owner_id"] != query.from_user.id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    # Stop the worker
    from filestore.worker_bot.engine import worker_engine
    await worker_engine.stop_worker(bot_id)

    # Soft-delete (keeps collections for data transfer)
    await main_db.delete_bot(bot_id)

    username = html.escape(str(bot.get("bot_username", "unknown")))

    remaining = await main_db.bots.count_documents({"owner_id": query.from_user.id, "is_deleted": {"$ne": True}})
    await _clone_log(client, "CloneDeleted", query.from_user, bot, f"<b>📊 User's remaining clones:</b> {remaining}")

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗑 𝗕𝗢𝗧 𝗗𝗘𝗟𝗘𝗧𝗘𝗗\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>@{username} ʜᴀs ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ.\n\n"
        f"📦 <b>Stored data and token are retained.</b>\n"
        f"This did not revoke the Telegram token or delete the Telegram bot.\n"
        f"ʏᴏᴜ ᴄᴀɴ ᴛʀᴀɴsꜰᴇʀ ᴛʜᴇᴍ ᴛᴏ ᴀ ɴᴇᴡ ʙᴏᴛ\n"
        f"ᴠɪᴀ ᴛʜᴇ <b>📦 ᴛʀᴀɴsꜰᴇʀ ᴅᴀᴛᴀ</b> ʙᴜᴛᴛᴏɴ\n"
        f"ɪɴ ʏᴏᴜʀ ɴᴇᴡ ʙᴏᴛ's ᴅᴀsʜʙᴏᴀʀᴅ.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴍʏ ʙᴏᴛs", callback_data="my_bots")],
        ]),
    )
    await query.answer("✅ ʙᴏᴛ ᴅᴇʟᴇᴛᴇᴅ!", show_alert=True)


# =============================================================================
# CALLBACK: Transfer Data — Show list of old deleted bots to transfer from
# =============================================================================

@Client.on_callback_query(filters.regex(r"^transfer_(\d+)$"))
async def transfer_callback(client: Client, query: CallbackQuery):
    """Show deleted bots available for data transfer."""
    import re
    match = re.match(r"^transfer_(\d+)$", query.data)
    target_bot_id = int(match.group(1))
    user_id = query.from_user.id

    # Verify ownership of target bot
    target_bot = await main_db.get_bot(target_bot_id)
    if not target_bot or target_bot["owner_id"] != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    # Get deleted bots
    deleted_bots = await main_db.get_deleted_user_bots(user_id)
    
    # Filter out the target bot itself to prevent self-transfer
    deleted_bots = [b for b in deleted_bots if b["_id"] != target_bot_id]

    if not deleted_bots:
        await query.answer("📭 ɴᴏ ᴅᴇʟᴇᴛᴇᴅ ʙᴏᴛs ꜰᴏᴜɴᴅ ᴛᴏ ᴛʀᴀɴsꜰᴇʀ ꜰʀᴏᴍ.", show_alert=True)
        return

    buttons = []
    for bot in deleted_bots:
        old_id = bot["_id"]
        old_username = bot.get("bot_username", "unknown")
        buttons.append([
            InlineKeyboardButton(
                f"📦 @{old_username}",
                callback_data=f"dotransfer_{target_bot_id}_{old_id}",
            )
        ])

    buttons.append([InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"dashboard_{target_bot_id}")])

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 𝗧𝗥𝗔𝗡𝗦𝗙𝗘𝗥 𝗗𝗔𝗧𝗔\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>sᴇʟᴇᴄᴛ ᴀ ᴅᴇʟᴇᴛᴇᴅ ʙᴏᴛ ᴛᴏ ᴛʀᴀɴsꜰᴇʀ\n"
        f"ɪᴛs ᴅᴀᴛᴀ (ᴜsᴇʀs, ᴀᴅᴍɪɴs, ᴄʜᴀɴɴᴇʟs)\n"
        f"ɪɴᴛᴏ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ ʙᴏᴛ.</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.answer()


# =============================================================================
# CALLBACK: Execute Transfer
# =============================================================================

@Client.on_callback_query(filters.regex(r"^dotransfer_(\d+)_(\d+)$"))
async def do_transfer_callback(client: Client, query: CallbackQuery):
    """Execute data transfer from old bot to new bot."""
    import re
    match = re.match(r"^dotransfer_(\d+)_(\d+)$", query.data)
    target_bot_id = int(match.group(1))
    source_bot_id = int(match.group(2))
    user_id = query.from_user.id

    # Verify ownership of target
    target_bot = await main_db.get_bot(target_bot_id)
    if not target_bot or target_bot["owner_id"] != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    # Verify ownership of source
    source_bot = await main_db.get_bot(source_bot_id)
    if not source_bot or source_bot["owner_id"] != user_id:
        await query.answer("❌ Source bot not found!", show_alert=True)
        return

    await query.message.edit_text(
        "<b>⏳ ᴛʀᴀɴsꜰᴇʀʀɪɴɢ ᴅᴀᴛᴀ...</b>"
    )

    from filestore.database.worker_db import WorkerDB
    target_db = WorkerDB(target_bot_id)

    try:
        stats = await target_db.copy_data_from(source_bot_id)
    except Exception as e:
        await query.message.edit_text(
            f"<b>❌ ᴛʀᴀɴsꜰᴇʀ ꜰᴀɪʟᴇᴅ</b>\n\n"
            f"<blockquote>{html.escape(str(e))}</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"dashboard_{target_bot_id}")],
            ]),
        )
        return

    # Build stats text
    stats_lines = []
    for key, count in stats.items():
        stats_lines.append(f"◈ <b>{key}:</b> {count}")
    stats_text = "\n".join(stats_lines) if stats_lines else "ɴᴏ ᴅᴀᴛᴀ ꜰᴏᴜɴᴅ"

    src_username = source_bot.get("bot_username", "unknown")

    # Now purge old bot's data and registry permanently
    old_db = WorkerDB(source_bot_id)
    await old_db.drop_all_collections()
    await main_db.purge_bot(source_bot_id)
    await _clone_log(client, "CloneTransferred", query.from_user, target_bot,
                     f"<b>📤 From:</b> @{src_username} (<code>{source_bot_id}</code>)\n"
                     + "\n".join(f"<b>• {k}:</b> {v}" for k, v in stats.items()))

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ 𝗧𝗥𝗔𝗡𝗦𝗙𝗘𝗥 𝗖𝗢𝗠𝗣𝗟𝗘𝗧𝗘\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>ᴅᴀᴛᴀ ꜰʀᴏᴍ @{src_username} ʜᴀs ʙᴇᴇɴ\n"
        f"ᴛʀᴀɴsꜰᴇʀʀᴇᴅ sᴜᴄᴄᴇssꜰᴜʟʟʏ!\n\n"
        f"{stats_text}</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 ᴅᴀsʜʙᴏᴀʀᴅ", callback_data=f"dashboard_{target_bot_id}")],
        ]),
    )
    await query.answer("✅ ᴛʀᴀɴsꜰᴇʀ ᴅᴏɴᴇ!", show_alert=True)



# =============================================================================
# NEW FEATURE: Restart Worker
# =============================================================================

@Client.on_callback_query(filters.regex(r"^restart_bot_(\d+)$"))
async def restart_bot_callback(client: Client, query: CallbackQuery):
    """Force restart a worker bot."""
    import re
    match = re.match(r"^restart_bot_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    await query.answer("🔄 Restarting...", show_alert=False)
    
    from filestore.worker_bot.engine import worker_engine
    try:
        # Stop if running
        if worker_engine.get_worker(bot_id):
            await worker_engine.stop_worker(bot_id)
            await asyncio.sleep(1.5)
        
        # Start again
        await worker_engine.start_worker(bot)
        await _clone_log(client, "CloneRestarted", query.from_user, bot)
        await query.message.edit_text(
            f"<b>✅ Bot restarted successfully!</b>\n\n"
            f"<blockquote>@{bot.get('bot_username', 'unknown')} is now running.</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")],
            ]),
        )
    except Exception as e:
        await query.message.edit_text(
            f"<b>❌ Restart failed:</b>\n<code>{html.escape(str(e))}</code>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")],
            ]),
        )


# =============================================================================
# NEW FEATURE: View Banned Users
# =============================================================================

@Client.on_callback_query(filters.regex(r"^banned_list_(\d+)$"))
async def banned_list_callback(client: Client, query: CallbackQuery):
    """Show list of banned users for this bot."""
    import re
    match = re.match(r"^banned_list_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    from filestore.database.worker_db import WorkerDB
    worker_db = WorkerDB(bot_id)
    banned = await worker_db.get_ban_users()

    if not banned:
        text = (
            f"<b>🚫 Banned Users</b>\n\n"
            f"<blockquote>No users are currently banned.</blockquote>"
        )
    else:
        lines = [f"• <code>{uid}</code>" for uid in banned[:40]]
        text = (
            f"<b>🚫 Banned Users ({len(banned)})</b>\n\n"
            f"<blockquote>" + "\n".join(lines) + "</blockquote>"
        )
        if len(banned) > 40:
            text += f"\n\n<i>...and {len(banned)-40} more</i>"

    await query.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()


# =============================================================================
# NEW FEATURE: Toggle Maintenance Mode
# =============================================================================

@Client.on_callback_query(filters.regex(r"^toggle_maint_(\d+)$"))
async def toggle_maintenance_callback(client: Client, query: CallbackQuery):
    """Show maintenance mode menu (toggle + custom message)."""
    import re
    match = re.match(r"^toggle_maint_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    settings = bot.get("settings", {})
    current = settings.get("maintenance_mode", False)
    status = "🛠 ON" if current else "❌ OFF"
    msg_preview = settings.get("maintenance_msg", "Default message")
    if len(str(msg_preview)) > 80:
        msg_preview = str(msg_preview)[:80] + "..."

    toggle_label = "🔴 Turn OFF" if current else "🟢 Turn ON"

    await query.message.edit_text(
        f"<b>🛠 Maintenance Mode</b>\n\n"
        f"<blockquote>"
        f"◈ <b>Status:</b> {status}\n"
        f"◈ <b>Message:</b> {msg_preview}"
        f"</blockquote>\n\n"
        f"<i>When ON, normal users see the maintenance message. Admins still have full access.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle_label, callback_data=f"do_toggle_maint_{bot_id}")],
            [InlineKeyboardButton("✏️ Set Custom Message", callback_data=f"set_maint_msg_{bot_id}")],
            [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^do_toggle_maint_(\d+)$"))
async def do_toggle_maint_callback(client: Client, query: CallbackQuery):
    """Actually toggle maintenance mode."""
    import re
    match = re.match(r"^do_toggle_maint_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    settings = bot.get("settings", {})
    current = settings.get("maintenance_mode", False)
    new_val = not current
    await main_db.update_setting(bot_id, "maintenance_mode", new_val)

    status = "ENABLED 🛠" if new_val else "DISABLED"
    await query.answer(f"Maintenance {status}", show_alert=True)
    query.data = f"toggle_maint_{bot_id}"
    await toggle_maintenance_callback(client, query)


@Client.on_callback_query(filters.regex(r"^set_maint_msg_(\d+)$"))
async def set_maint_msg_callback(client: Client, query: CallbackQuery):
    """Prompt for custom maintenance message."""
    import re
    match = re.match(r"^set_maint_msg_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    # Store state for text input - use a simple module-level approach via bot_settings state if available
    try:
        from filestore.main_bot.plugins.bot_settings import _get_state
        state = _get_state()
        state[user_id] = {"step": "awaiting_maint_msg", "data": {"bot_id": bot_id}}
    except Exception:
        pass

    await query.message.edit_text(
        "<b>✏️ Custom Maintenance Message</b>\n\n"
        "<blockquote>Send the message users will see when maintenance is ON.\n"
        "HTML is supported.\n\n"
        "Send <code>/cancel</code> to cancel.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"toggle_maint_{bot_id}")],
        ]),
    )
    await query.answer()


# =============================================================================
# NEW FEATURE: Backup Bot Settings
# =============================================================================

@Client.on_callback_query(filters.regex(r"^backup_bot_(\d+)$"))
async def backup_bot_callback(client: Client, query: CallbackQuery):
    """Export bot settings as a readable backup."""
    import re
    import json
    from datetime import datetime
    match = re.match(r"^backup_bot_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    # Build safe backup (no tokens)
    backup = {
        "bot_id": bot_id,
        "bot_username": bot.get("bot_username"),
        "log_channel_id": bot.get("log_channel_id"),
        "settings": bot.get("settings", {}),
        "shortener": {
            "enabled": bot.get("shortener", {}).get("enabled", False),
            "domain": bot.get("shortener", {}).get("domain", ""),
            "provider": bot.get("shortener", {}).get("provider", "adlinkfly"),
            # api_key intentionally excluded for security
        },
        "backed_up_at": datetime.utcnow().isoformat() + "Z",
        "version": "Videl-FileStore-1.0",
    }

    # Also get admins & channels count
    from filestore.database.worker_db import WorkerDB
    wdb = WorkerDB(bot_id)
    try:
        backup["counts"] = {
            "admins": await wdb.total_admins(),
            "fsub_channels": await wdb.total_channels(),
            "users": await wdb.total_users(),
            "banned": await wdb.total_banned(),
        }
    except Exception:
        backup["counts"] = {}

    import html as _html
    dump = json.dumps(backup, indent=2, ensure_ascii=False, default=str)
    if len(dump) > 3000:
        # too long for one message – a cut-off JSON can't be restored, so send it as a file
        import io
        f = io.BytesIO(dump.encode())
        f.name = f"backup_{bot.get('bot_username', bot_id)}.json"
        try:
            await client.send_document(user_id, f, caption="<b>💾 Backup</b> – open it and paste the text into 📥 Restore.")
        except Exception as e:
            log.warning(f"backup file send failed: {e}")
        body = "<i>Sent as a .json file above (too long for a message).</i>"
    else:
        body = f"<pre>{_html.escape(dump)}</pre>"
    text = (
        f"<b>💾 Bot Backup</b>\n\n"
        f"<b>Bot:</b> @{bot.get('bot_username', 'unknown')}\n"
        f"<b>ID:</b> <code>{bot_id}</code>\n\n"
        f"{body}\n\n"
        f"<i>⚠️ API keys &amp; tokens are NOT included for security.</i>"
    )
    await query.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer("✅ Backup generated")


# =============================================================================
# NEW FEATURE: Clone Settings from another bot
# =============================================================================

@Client.on_callback_query(filters.regex(r"^clone_settings_(\d+)$"))
async def clone_settings_callback(client: Client, query: CallbackQuery):
    """Choose a source bot to clone settings from."""
    import re
    match = re.match(r"^clone_settings_(\d+)$", query.data)
    target_bot_id = int(match.group(1))
    user_id = query.from_user.id

    target = await main_db.get_bot(target_bot_id)
    if not target or target.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    user_bots = await main_db.get_user_bots(user_id)
    other_bots = [b for b in user_bots if b.get("_id") != target_bot_id]

    if not other_bots:
        await query.answer("❌ You need at least one other bot to clone from!", show_alert=True)
        return

    buttons = []
    for b in other_bots[:15]:
        uname = b.get("bot_username", "unknown")
        bid = b.get("_id")
        buttons.append([
            InlineKeyboardButton(
                f"📋 @{uname}",
                callback_data=f"doclone_{target_bot_id}_{bid}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"dashboard_{target_bot_id}")])

    await query.message.edit_text(
        f"<b>📋 Clone Settings</b>\n\n"
        f"<blockquote>Select the bot whose settings you want to copy "
        f"<b>into</b> @{target.get('bot_username', 'target')}.</blockquote>\n\n"
        f"<i>This copies: auto-delete, protection, permanent link, "
        f"maintenance, shortener config (without API key), start message &amp; caption.</i>",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^doclone_(\d+)_(\d+)$"))
async def do_clone_settings_callback(client: Client, query: CallbackQuery):
    """Actually clone settings from source to target."""
    import re
    match = re.match(r"^doclone_(\d+)_(\d+)$", query.data)
    target_bot_id = int(match.group(1))
    source_bot_id = int(match.group(2))
    user_id = query.from_user.id

    target = await main_db.get_bot(target_bot_id)
    source = await main_db.get_bot(source_bot_id)

    if not target or not source:
        await query.answer("❌ Bot not found!", show_alert=True)
        return
    if target.get("owner_id") != user_id or source.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    # Clone settings
    src_settings = source.get("settings", {})
    keys_to_copy = [
        "auto_delete_time", "protect_content", "permanent_link",
        "maintenance_mode", "start_msg", "start_pic", "caption",
    ]
    copied = []
    for key in keys_to_copy:
        if key in src_settings:
            await main_db.update_setting(target_bot_id, key, src_settings[key])
            copied.append(key)

    # Clone shortener domain + provider + enabled (not API key)
    src_short = source.get("shortener", {})
    if src_short.get("domain"):
        await main_db.update_shortener(target_bot_id, "domain", src_short["domain"])
        copied.append("shortener.domain")
    if src_short.get("provider"):
        await main_db.update_shortener(target_bot_id, "provider", src_short["provider"])
        copied.append("shortener.provider")
    if "enabled" in src_short:
        # Only enable if target already has an API key
        target_short = target.get("shortener", {})
        if target_short.get("api_key_encrypted") and src_short.get("enabled"):
            await main_db.update_shortener(target_bot_id, "enabled", True)
            copied.append("shortener.enabled")

    src_name = source.get("bot_username", "source")
    tgt_name = target.get("bot_username", "target")
    await _clone_log(client, "CloneSettingsCopied", query.from_user, target, f"<b>📋 Settings from:</b> @{src_name}")

    await query.message.edit_text(
        f"<b>✅ Settings Cloned</b>\n\n"
        f"<blockquote>From @{src_name} → @{tgt_name}\n\n"
        f"Copied: {', '.join(copied) if copied else 'nothing new'}</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{target_bot_id}")],
        ]),
    )
    await query.answer("✅ Clone complete!", show_alert=True)


# =============================================================================
# NEW FEATURE: Restore Settings from Backup JSON
# =============================================================================

@Client.on_callback_query(filters.regex(r"^restore_bot_(\d+)$"))
async def restore_bot_callback(client: Client, query: CallbackQuery):
    """Prompt user to paste a backup JSON."""
    import re
    match = re.match(r"^restore_bot_(\d+)$", query.data)
    bot_id = int(match.group(1))
    user_id = query.from_user.id

    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != user_id:
        await query.answer("❌ Access denied!", show_alert=True)
        return

    try:
        from filestore.main_bot.plugins.bot_settings import _get_state
        state = _get_state()
        state[user_id] = {"step": "awaiting_restore_json", "data": {"bot_id": bot_id}}
    except Exception:
        pass

    await query.message.edit_text(
        "<b>📥 Restore Settings</b>\n\n"
        "<blockquote>Paste the backup JSON you got from the <b>💾 Backup</b> button.\n\n"
        "Only safe fields are restored (no tokens/API keys).\n\n"
        "Send <code>/cancel</code> to cancel.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()

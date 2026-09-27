
import asyncio
from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from filestore.fs_config import LOGGER, BACKEND_API_SECRET, BACKEND_API_URL, MAX_BOTS_PER_USER
from filestore.database.main_db import MainDB
from filestore.utils.security import mask_token, decrypt_token

log = LOGGER(__name__)
main_db = MainDB()

# =============================================================================
# CALLBACK: My Bots (list all bots)
# =============================================================================
@Client.on_callback_query(filters.regex(r"^my_bots$"))
async def my_bots_callback(client: Client, query: CallbackQuery):
    """List all bots owned by the user."""
    user_id = query.from_user.id

    from filestore.main_bot.plugins.create_bot import _creation_state
    _creation_state.pop(user_id, None)

    bots = await main_db.get_user_bots(user_id)

    if not bots:
        await query.message.edit_text(
            "<b>━━━━━━━━━━━━━━━━━━━━━\n"
            "📋 𝗠𝗬 𝗕𝗢𝗧𝗦\n"
            "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            "<blockquote>ʏᴏᴜ ʜᴀᴠᴇɴ'ᴛ ᴄʀᴇᴀᴛᴇᴅ ᴀɴʏ ʙᴏᴛs ʏᴇᴛ.\n"
            "ᴛᴀᴘ <b>⚡ ᴄʀᴇᴀᴛᴇ ʙᴏᴛ</b> ᴛᴏ ɢᴇᴛ sᴛᴀʀᴛᴇᴅ!</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚡ ᴄʀᴇᴀᴛᴇ ʙᴏᴛ", callback_data="create_bot")],
                [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data="back_menu")],
            ]),
        )
        await query.answer()
        return

    # Build buttons for each bot
    from filestore.worker_bot.engine import worker_engine
    buttons = []
    for bot in bots:
        bot_id = bot["_id"]
        is_live = worker_engine.get_worker(bot_id) is not None
        status = "🟢" if is_live else "🔴"
        username = bot.get("bot_username", "unknown")
        bot_id = bot["_id"]
        buttons.append([
            InlineKeyboardButton(
                f"{status} @{username}",
                callback_data=f"dashboard_{bot_id}",
            )
        ])

    buttons.append([InlineKeyboardButton("🔙 ʙᴀᴄᴋ ᴛᴏ ᴍᴇɴᴜ", callback_data="back_menu")])

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"📋 𝗠𝗬 𝗕𝗢𝗧𝗦  [{len(bots)}/{MAX_BOTS_PER_USER}]\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>sᴇʟᴇᴄᴛ ᴀ ʙᴏᴛ ᴛᴏ ᴏᴘᴇɴ ɪᴛs ᴅᴀsʜʙᴏᴀʀᴅ:</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.answer()


# =============================================================================
# CALLBACK: Bot Dashboard (main dashboard for a specific bot)
# =============================================================================

@Client.on_callback_query(filters.regex(r"^dashboard_(\d+)$"))
async def dashboard_callback(client: Client, query: CallbackQuery):
    """Show the dashboard for a specific bot."""
    import re
    match = re.search(r"_(\d+)$", query.data)
    if not match:
        await query.answer("❌ Invalid bot!", show_alert=True)
        return

    bot_id = int(match.group(1))
    user_id = query.from_user.id

    from filestore.main_bot.plugins.create_bot import _creation_state
    _creation_state.pop(user_id, None)

    # Verify ownership
    bot = await main_db.get_bot(bot_id)
    if not bot or bot["owner_id"] != user_id:
        await query.answer("❌ Bot not found or access denied!", show_alert=True)
        return

    from filestore.worker_bot.engine import worker_engine
    is_live = worker_engine.get_worker(bot_id) is not None
    status = "🟢 ʀᴜɴɴɪɴɢ" if is_live else "🔴 sᴛᴏᴘᴘᴇᴅ"
    username = bot.get("bot_username", "unknown")
    channel = bot.get("log_channel_id", "Not set")

    # Get settings summary
    settings = bot.get("settings", {})
    shortener = bot.get("shortener", {})
    auto_del = settings.get("auto_delete_time", 0)
    auto_del_text = f"{auto_del}s" if auto_del > 0 else "ᴅɪsᴀʙʟᴇᴅ"
    shortener_status = "✅" if shortener.get("enabled") else "❌"
    protect_status = "✅ ᴏɴ" if settings.get("protect_content") else "❌ ᴏꜰꜰ"
    perm_link_status = "✅ ᴏɴ" if settings.get("permanent_link") else "❌ ᴏꜰꜰ"
    maint_status = "🛠 ᴏɴ" if settings.get("maintenance_mode") else "❌ ᴏꜰꜰ"

    text = (
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚙️ 𝗕𝗢𝗧 𝗗𝗔𝗦𝗛𝗕𝗢𝗔𝗥𝗗\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>"
        f"◈ <b>ʙᴏᴛ:</b> @{username}\n"
        f"◈ <b>sᴛᴀᴛᴜs:</b> {status}\n"
        f"◈ <b>ᴄʜᴀɴɴᴇʟ:</b> <code>{channel}</code>\n"
        f"◈ <b>ᴀᴜᴛᴏ-ᴅᴇʟ:</b> {auto_del_text}\n"
        f"◈ <b>sʜᴏʀᴛᴇɴᴇʀ:</b> {shortener_status}\n"
        f"◈ <b>ᴘʀᴏᴛᴇᴄᴛɪᴏɴ:</b> {protect_status}\n"
        f"◈ <b>ᴘᴇʀᴍ ʟɪɴᴋ:</b> {perm_link_status}\n"
        f"◈ <b>ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ:</b> {maint_status}"
        f"</blockquote>"
    )

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
        ],
        [
            InlineKeyboardButton("🛠 ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ", callback_data=f"toggle_maint_{bot_id}"),
        ],
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

    from pyrogram.errors import MessageNotModified
    try:
        await query.message.edit_text(text, reply_markup=keyboard)
    except MessageNotModified:
        pass
        
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
        await worker_engine.stop_worker(bot_id)
        await main_db.set_bot_active(bot_id, False)
    else:
        # Answer immediately before slow start
        await query.answer("🟢 sᴛᴀʀᴛɪɴɢ...", show_alert=False)
        bot_doc = await main_db.get_bot(bot_id)
        try:
            await worker_engine.start_worker(bot_doc)
            await main_db.set_bot_active(bot_id, True)
        except Exception as e:
            log.error(f"Failed to start bot {bot_id}: {e}")

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

    await main_db.update_setting(bot_id, "permanent_link", new_val)

    # Send user data to API if enabled
    if new_val:
        import aiohttp
        from filestore.fs_config import BACKEND_API_URL

        bot_username = bot.get("bot_username", "unknown")

        try:
            async with aiohttp.ClientSession() as session:
                # Assuming simple authentication with BACKEND_API_SECRET set in environment
                import os
                api_secret = BACKEND_API_SECRET
                headers = {"Authorization": f"Bearer {api_secret}"}

                async with session.post(
                    f"{BACKEND_API_URL}/api/user",
                    json={
                        "userId": str(user_id),
                        "botUsername": bot_username
                    },
                    headers=headers
                ) as resp:
                    resp_data = await resp.json()
                    if not resp_data.get("success"):
                        await query.answer("⚠️ API Error while syncing user data", show_alert=True)
        except Exception as e:
            await query.answer("⚠️ Connection Error to Backend API", show_alert=True)
            import logging
            logging.getLogger(__name__).error(f"Permanent Link API Error: {e}")

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

    username = bot.get("bot_username", "unknown")

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ 𝗗𝗘𝗟𝗘𝗧𝗘 @{username}?\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>ᴛʜɪs ᴡɪʟʟ ᴘᴇʀᴍᴀɴᴇɴᴛʟʏ ᴅᴇʟᴇᴛᴇ ᴛʜᴇ ʙᴏᴛ "
        f"ᴀɴᴅ ᴀʟʟ ɪᴛs ᴅᴀᴛᴀ (ᴜsᴇʀs, ᴄʜᴀɴɴᴇʟs, sᴇᴛᴛɪɴɢs).\n\n"
        f"<b>ᴛʜɪs ᴀᴄᴛɪᴏɴ ᴄᴀɴɴᴏᴛ ʙᴇ ᴜɴᴅᴏɴᴇ!</b></blockquote>",
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

    username = bot.get("bot_username", "unknown")

    from filestore.utils.helpers import send_main_log
    await send_main_log(
        client,
        f"<b>🗑 Bot Deleted</b>\n\n"
        f"<b>• User ID:</b> <code>{query.from_user.id}</code>\n"
        f"<b>• Bot ID:</b> <code>{bot_id}</code>\n"
        f"<b>• Username:</b> @{username}"
    )

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗑 𝗕𝗢𝗧 𝗗𝗘𝗟𝗘𝗧𝗘𝗗\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>@{username} ʜᴀs ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ.\n\n"
        f"📦 <b>ʏᴏᴜʀ ᴅᴀᴛᴀ ɪs sᴀꜰᴇ!</b>\n"
        f"ᴜsᴇʀs, ᴀᴅᴍɪɴs, ᴄʜᴀɴɴᴇʟs ᴀʀᴇ ᴘʀᴇsᴇʀᴠᴇᴅ.\n"
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
            f"<blockquote>{e}</blockquote>",
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
        await query.message.edit_text(
            f"<b>✅ Bot restarted successfully!</b>\n\n"
            f"<blockquote>@{bot.get('bot_username', 'unknown')} is now running.</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")],
            ]),
        )
    except Exception as e:
        await query.message.edit_text(
            f"<b>❌ Restart failed:</b>\n<code>{e}</code>",
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
        admins = await wdb.get_all_admins()
        channels = await wdb.show_channels()
        users = await wdb.total_users()
        banned = await wdb.get_ban_users()
        backup["counts"] = {
            "admins": len(admins),
            "fsub_channels": len(channels),
            "users": users,
            "banned": len(banned),
        }
    except Exception:
        backup["counts"] = {}

    text = (
        f"<b>💾 Bot Backup</b>\n\n"
        f"<b>Bot:</b> @{bot.get('bot_username', 'unknown')}\n"
        f"<b>ID:</b> <code>{bot_id}</code>\n\n"
        f"<pre>{json.dumps(backup, indent=2, ensure_ascii=False)[:3500]}</pre>\n\n"
        f"<i>⚠️ API keys & tokens are NOT included for security.</i>"
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
        f"maintenance, shortener config (without API key), start message & caption.</i>",
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

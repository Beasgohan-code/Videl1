
import re
from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from filestore.fs_config import API_ID, API_HASH, LOGGER
from filestore.database.main_db import MainDB
from filestore.database.worker_db import WorkerDB
from filestore.utils.helpers import upload_to_telegraph
from filestore.utils.security import encrypt_token, decrypt_token, mask_api_key

log = LOGGER(__name__)
main_db = MainDB()

# Helper to get creation state (shared with create_bot.py)
def _get_state():
    from filestore.main_bot.plugins.create_bot import _creation_state
    return _creation_state

# =============================================================================
# HELPER: Verify bot ownership
# =============================================================================

async def _verify_ownership(query: CallbackQuery, bot_id: int):
    """Verify that the user owns the given bot. Returns bot doc or None."""
    bot = await main_db.get_bot(bot_id)
    if not bot or bot["owner_id"] != query.from_user.id:
        await query.answer("❌ Access denied!", show_alert=True)
        return None
    return bot


def _extract_bot_id(pattern: str, data: str) -> int | None:
    """Extract bot_id from callback data using a regex pattern."""
    match = re.match(pattern, data)
    return int(match.group(1)) if match else None


# =============================================================================
# SET LOG CHANNEL
# =============================================================================

@Client.on_callback_query(filters.regex(r"^set_channel_(\d+)$"))
async def set_channel_callback(client: Client, query: CallbackQuery):
    """Prompt user to send new log channel ID."""
    bot_id = _extract_bot_id(r"^set_channel_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {
        "step": "awaiting_new_log_channel",
        "data": {"bot_id": bot_id},
    }

    await query.message.edit_text(
        "<b>📢 Set Log Channel</b>\n\n"
        f"<blockquote>Current channel: <code>{bot.get('log_channel_id', 'Not set')}</code>\n\n"
        "Send the new channel ID.\n"
        f"Make sure @{bot.get('bot_username', '')} is an admin in the channel.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()
    from filestore.main_bot.plugins.create_bot import offer_channel_picker
    await offer_channel_picker(client, query.from_user.id, state[query.from_user.id])


async def handle_log_channel_input(client: Client, message: Message, state: dict):
    """Process log channel ID input."""
    user_id = message.from_user.id
    bot_id = state["data"]["bot_id"]

    try:
        channel_id = int(message.text.strip())
    except ValueError:
        await message.reply("<b>❌ Invalid channel ID. Send a number like <code>-1001234567890</code></b>")
        return

    # Validate access
    bot = await main_db.get_bot(bot_id)
    token = decrypt_token(bot["bot_token_encrypted"])

    try:
        temp_client = Client(f"verify_{bot_id}", api_id=API_ID, api_hash=API_HASH, bot_token=token, in_memory=True)
        await temp_client.start()
        test = await temp_client.send_message(channel_id, "✅ Channel verified.")
        await test.delete()
        await temp_client.stop()
    except Exception as e:
        await message.reply(f"<b>❌ Cannot access channel!</b>\n<code>{str(e)[:100]}</code>")
        return

    await main_db.update_log_channel(bot_id, channel_id)

    # Clear state
    creation_state = _get_state()
    creation_state.pop(user_id, None)

    # Restart worker to pick up new channel
    try:
        from filestore.worker_bot.engine import worker_engine
        await worker_engine.stop_worker(bot_id)
        bot_doc = await main_db.get_bot(bot_id)
        await worker_engine.start_worker(bot_doc)
    except Exception as e:
        log.error(f"Failed to restart worker {bot_id}: {e}")

    await message.reply(
        f"<b>✅ Log channel updated to <code>{channel_id}</code></b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Dashboard", callback_data=f"dashboard_{bot_id}")],
        ]),
    )


# =============================================================================
# AUTO-DELETE TIMER
# =============================================================================

@Client.on_callback_query(filters.regex(r"^auto_delete_(\d+)$"))
async def auto_delete_callback(client: Client, query: CallbackQuery):
    """Show auto-delete settings."""
    bot_id = _extract_bot_id(r"^auto_delete_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    _get_state().pop(query.from_user.id, None)

    worker_db = WorkerDB(bot_id)
    current = await worker_db.get_del_timer()
    current_text = f"{current} seconds" if current > 0 else "Disabled"

    await query.message.edit_text(
        f"<b>⏱ Auto-Delete Settings</b>\n\n"
        f"<blockquote>Current: <b>{current_text}</b>\n\n"
        f"Files sent by the bot will be auto-deleted after this time.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("5 min", callback_data=f"setdel_{bot_id}_300"),
                InlineKeyboardButton("30 min", callback_data=f"setdel_{bot_id}_1800"),
                InlineKeyboardButton("1 hr", callback_data=f"setdel_{bot_id}_3600"),
            ],
            [
                InlineKeyboardButton("6 hr", callback_data=f"setdel_{bot_id}_21600"),
                InlineKeyboardButton("12 hr", callback_data=f"setdel_{bot_id}_43200"),
                InlineKeyboardButton("24 hr", callback_data=f"setdel_{bot_id}_86400"),
            ],
            [
                InlineKeyboardButton("❌ Disable", callback_data=f"setdel_{bot_id}_0"),
                InlineKeyboardButton("✏️ Custom", callback_data=f"customdel_{bot_id}"),
            ],
            [InlineKeyboardButton("🔙 Back", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^setdel_(\d+)_(\d+)$"))
async def set_del_timer_callback(client: Client, query: CallbackQuery):
    """Set a preset auto-delete time."""
    match = re.match(r"^setdel_(\d+)_(\d+)$", query.data)
    bot_id, seconds = int(match.group(1)), int(match.group(2))

    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    worker_db = WorkerDB(bot_id)
    await worker_db.set_del_timer(seconds)

    text = f"{seconds}s" if seconds > 0 else "disabled"
    await query.answer(f"✅ Auto-delete set to {text}", show_alert=True)
    await auto_delete_callback(client, query)


@Client.on_callback_query(filters.regex(r"^customdel_(\d+)$"))
async def custom_del_callback(client: Client, query: CallbackQuery):
    """Prompt for custom auto-delete time."""
    bot_id = _extract_bot_id(r"^customdel_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {
        "step": "awaiting_auto_delete_time",
        "data": {"bot_id": bot_id},
    }

    await query.message.edit_text(
        "<b>✏️ Custom Auto-Delete Time</b>\n\n"
        "<blockquote>Send the time in <b>seconds</b>.\n"
        "Example: <code>600</code> for 10 minutes.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"auto_delete_{bot_id}")],
        ]),
    )
    await query.answer()


async def handle_auto_delete_input(client: Client, message: Message, state: dict):
    """Process custom auto-delete time input."""
    user_id = message.from_user.id
    bot_id = state["data"]["bot_id"]

    try:
        seconds = int(message.text.strip())
        if seconds < 0:
            raise ValueError
    except ValueError:
        await message.reply("<b>❌ Send a valid number (0 to disable).</b>")
        return

    worker_db = WorkerDB(bot_id)
    await worker_db.set_del_timer(seconds)

    creation_state = _get_state()
    creation_state.pop(user_id, None)

    text = f"{seconds} seconds" if seconds > 0 else "disabled"
    await message.reply(
        f"<b>✅ Auto-delete set to {text}</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data=f"dashboard_{bot_id}")],
        ]),
    )


# =============================================================================
# FORCE SUBSCRIBE
# =============================================================================

@Client.on_callback_query(filters.regex(r"^fsub_(\d+)$"))
async def fsub_callback(client: Client, query: CallbackQuery):
    """Show force-subscribe settings for a bot."""
    bot_id = _extract_bot_id(r"^fsub_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    _get_state().pop(query.from_user.id, None)

    worker_db = WorkerDB(bot_id)
    channels = await worker_db.show_channels()

    if channels:
        channel_list = ""
        for i, ch_id in enumerate(channels, 1):
            mode = await worker_db.get_channel_mode(ch_id)
            mode_text = "📩 Request" if mode == "on" else "🔒 Force Join"
            channel_list += f"  {i}. <code>{ch_id}</code> — {mode_text}\n"
    else:
        channel_list = "  <i>No channels added</i>\n"

    await query.message.edit_text(
        f"<b>🔗 Force Subscribe Settings</b>\n\n"
        f"<blockquote><b>Current Channels:</b>\n{channel_list}</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Channel", callback_data=f"add_fsub_{bot_id}")],
            [InlineKeyboardButton("➖ Remove Channel", callback_data=f"rem_fsub_{bot_id}")],
            [InlineKeyboardButton("🔄 Toggle Mode", callback_data=f"toggle_fsub_{bot_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^add_fsub_(\d+)$"))
async def add_fsub_callback(client: Client, query: CallbackQuery):
    """Prompt to add a force-sub channel."""
    bot_id = _extract_bot_id(r"^add_fsub_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {
        "step": "awaiting_fsub_channel",
        "data": {"bot_id": bot_id, "action": "add"},
    }

    await query.message.edit_text(
        "<b>➕ Add Force Subscribe Channel</b>\n\n"
        f"<blockquote>Send the channel ID.\n"
        f"Make sure @{bot.get('bot_username', '')} is an admin in the channel.\n\n"
        f"Example: <code>-1001234567890</code></blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"fsub_{bot_id}")],
        ]),
    )
    from filestore.main_bot.plugins.create_bot import offer_channel_picker
    await offer_channel_picker(client, query.from_user.id, state[query.from_user.id])
    await query.answer()


@Client.on_callback_query(filters.regex(r"^rem_fsub_(\d+)$"))
async def rem_fsub_callback(client: Client, query: CallbackQuery):
    """Show channels to remove."""
    bot_id = _extract_bot_id(r"^rem_fsub_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    worker_db = WorkerDB(bot_id)
    channels = await worker_db.show_channels()

    if not channels:
        await query.answer("No channels to remove!", show_alert=True)
        return

    buttons = []
    for ch_id in channels:
        buttons.append([
            InlineKeyboardButton(
                f"🗑 {ch_id}",
                callback_data=f"do_rem_fsub_{bot_id}_{ch_id}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"fsub_{bot_id}")])

    await query.message.edit_text(
        "<b>➖ Remove Channel</b>\n\n"
        "<blockquote>Select a channel to remove:</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^do_rem_fsub_(\d+)_(-?\d+)$"))
async def do_rem_fsub_callback(client: Client, query: CallbackQuery):
    """Remove a force-sub channel."""
    match = re.match(r"^do_rem_fsub_(\d+)_(-?\d+)$", query.data)
    bot_id, channel_id = int(match.group(1)), int(match.group(2))

    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    worker_db = WorkerDB(bot_id)
    await worker_db.rem_channel(channel_id)

    await query.answer(f"✅ Channel {channel_id} removed!", show_alert=True)
    await fsub_callback(client, query)


@Client.on_callback_query(filters.regex(r"^toggle_fsub_(\d+)$"))
async def toggle_fsub_callback(client: Client, query: CallbackQuery):
    """Show channels to toggle mode."""
    bot_id = _extract_bot_id(r"^toggle_fsub_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    worker_db = WorkerDB(bot_id)
    channels = await worker_db.show_channels()

    if not channels:
        await query.answer("No channels added!", show_alert=True)
        return

    buttons = []
    for ch_id in channels:
        mode = await worker_db.get_channel_mode(ch_id)
        mode_text = "Request → Force Join" if mode == "on" else "Force Join → Request"
        buttons.append([
            InlineKeyboardButton(
                f"🔄 {ch_id} ({mode_text})",
                callback_data=f"do_toggle_{bot_id}_{ch_id}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"fsub_{bot_id}")])

    await query.message.edit_text(
        "<b>🔄 Toggle Channel Mode</b>\n\n"
        "<blockquote><b>Force Join:</b> User must join the channel.\n"
        "<b>Request:</b> User sends a join request.</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^do_toggle_(\d+)_(-?\d+)$"))
async def do_toggle_fsub_callback(client: Client, query: CallbackQuery):
    """Toggle a channel's force-sub mode."""
    match = re.match(r"^do_toggle_(\d+)_(-?\d+)$", query.data)
    bot_id, channel_id = int(match.group(1)), int(match.group(2))

    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    worker_db = WorkerDB(bot_id)
    current = await worker_db.get_channel_mode(channel_id)
    new_mode = "off" if current == "on" else "on"
    await worker_db.set_channel_mode(channel_id, new_mode)

    mode_name = "Request-based" if new_mode == "on" else "Force Join"
    await query.answer(f"✅ Channel set to {mode_name}!", show_alert=True)
    await fsub_callback(client, query)


async def handle_fsub_channel_input(client: Client, message: Message, state: dict):
    """Process force-sub channel ID input."""
    user_id = message.from_user.id
    bot_id = state["data"]["bot_id"]

    try:
        channel_id = int(message.text.strip())
    except ValueError:
        await message.reply("<b>❌ Invalid channel ID. Send a number like <code>-1001234567890</code></b>")
        return

    status_msg = await message.reply("<b>⏳ Verifying channel access...</b>")

    # Verify that the cloned bot has access and can create invite links
    bot = await main_db.get_bot(bot_id)
    token = decrypt_token(bot["bot_token_encrypted"])

    try:
        temp_client = Client(
            name=f"verify_fsub_{bot_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=token,
            in_memory=True
        )
        await temp_client.start()
        
        # 1. Check access
        chat = await temp_client.get_chat(channel_id)
        
        # 2. Check ability to create join request links (needed for request mode)
        invite = await temp_client.create_chat_invite_link(
            chat_id=channel_id,
            creates_join_request=True
        )
        await temp_client.revoke_chat_invite_link(channel_id, invite.invite_link)
        
        await temp_client.stop()
    except Exception as e:
        await status_msg.edit_text(
            f"<b>❌ Verification Failed!</b>\n\n"
            f"<blockquote>Make sure:\n"
            f"1. The channel ID is correct (<code>{channel_id}</code>)\n"
            f"2. The bot (@{bot.get('bot_username', 'unknown')}) is an admin in the channel\n"
            f"3. The bot has 'Invite Users via Link' admin rights\n\n"
            f"<b>Error:</b> <code>{str(e)[:100]}</code></blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"fsub_{bot_id}")],
            ])
        )
        return

    worker_db = WorkerDB(bot_id)
    await worker_db.add_channel(channel_id, mode="off")
    
    # Restart the worker bot to ensure it caches this new chat
    try:
        from filestore.worker_bot.engine import worker_engine
        await worker_engine.stop_worker(bot_id)
        await worker_engine.start_worker(bot)
    except Exception as e:
        log.error(f"Restarting worker failed after adding fsub: {e}")

    creation_state = _get_state()
    creation_state.pop(user_id, None)

    await status_msg.edit_text(
        f"<b>✅ Channel <code>{channel_id}</code> added with Force Join mode!</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data=f"fsub_{bot_id}")],
        ]),
    )


# =============================================================================
# URL SHORTENER
# =============================================================================

@Client.on_callback_query(filters.regex(r"^set_short_key_(\d+)$"))
async def set_shortener_key_callback(client: Client, query: CallbackQuery):
    """Prompt for shortener API key."""
    bot_id = _extract_bot_id(r"^set_short_key_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {
        "step": "awaiting_shortener_api_key",
        "data": {"bot_id": bot_id},
    }

    await query.message.edit_text(
        "<b>🔑 Set Shortener API Key</b>\n\n"
        "<blockquote>Send your shortener API key.\n"
        "This will be stored securely (encrypted).</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"shortener_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^set_short_domain_(\d+)$"))
async def set_shortener_domain_callback(client: Client, query: CallbackQuery):
    """Prompt for shortener domain."""
    bot_id = _extract_bot_id(r"^set_short_domain_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {
        "step": "awaiting_shortener_domain",
        "data": {"bot_id": bot_id},
    }

    await query.message.edit_text(
        "<b>🌐 Set Shortener Domain</b>\n\n"
        "<blockquote>Send your shortener domain.\n"
        "Example: <code>example.com</code></blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"shortener_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^toggle_short_(\d+)$"))
async def toggle_shortener_callback(client: Client, query: CallbackQuery):
    """Toggle the shortener on/off."""
    bot_id = _extract_bot_id(r"^toggle_short_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    shortener = bot.get("shortener", {})
    current = shortener.get("enabled", False)

    # Don't enable if no API key or domain
    if not current:
        if not shortener.get("api_key_encrypted") or not shortener.get("domain"):
            await query.answer(
                "❌ Set API key and domain first!", show_alert=True
            )
            return

    await main_db.update_shortener(bot_id, "enabled", not current)

    status = "enabled" if not current else "disabled"
    await query.answer(f"✅ Shortener {status}!", show_alert=True)
    await shortener_callback(client, query)


# =============================================================================
# ADMIN MANAGEMENT
# =============================================================================

@Client.on_callback_query(filters.regex(r"^admins_(\d+)$"))
async def admins_callback(client: Client, query: CallbackQuery):
    """Show admin management for a bot."""
    bot_id = _extract_bot_id(r"^admins_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    _get_state().pop(query.from_user.id, None)

    worker_db = WorkerDB(bot_id)
    admins = await worker_db.get_all_admins()

    if admins:
        admin_list = "\n".join([f"  • <code>{a}</code>" for a in admins])
    else:
        admin_list = "  <i>No admins added (owner has full access)</i>"

    await query.message.edit_text(
        f"<b>👥 Admin Management</b>\n\n"
        f"<blockquote><b>Bot Owner:</b> <code>{bot['owner_id']}</code> (always admin)\n\n"
        f"<b>Additional Admins:</b>\n{admin_list}</blockquote>\n"
        f"<i>🔒 Admins can make links, batches, bans and broadcasts in the bot – "
        f"only you can change its settings.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Admin", callback_data=f"add_admin_{bot_id}")],
            [InlineKeyboardButton("➖ Remove Admin", callback_data=f"rem_admin_{bot_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^add_admin_(\d+)$"))
async def add_admin_callback(client: Client, query: CallbackQuery):
    """Prompt to add an admin."""
    bot_id = _extract_bot_id(r"^add_admin_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {
        "step": "awaiting_admin_id",
        "data": {"bot_id": bot_id, "action": "add"},
    }

    await query.message.edit_text(
        "<b>➕ Add Admin</b>\n\n"
        "<blockquote>Send the user ID of the person to add as admin.\n\n"
        "Example: <code>123456789</code></blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data=f"admins_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^rem_admin_(\d+)$"))
async def rem_admin_callback(client: Client, query: CallbackQuery):
    """Show admins to remove."""
    bot_id = _extract_bot_id(r"^rem_admin_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    worker_db = WorkerDB(bot_id)
    admins = await worker_db.get_all_admins()

    if not admins:
        await query.answer("No admins to remove!", show_alert=True)
        return

    buttons = []
    for admin_id in admins:
        buttons.append([
            InlineKeyboardButton(
                f"🗑 {admin_id}",
                callback_data=f"do_rem_admin_{bot_id}_{admin_id}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"admins_{bot_id}")])

    await query.message.edit_text(
        "<b>➖ Remove Admin</b>\n\n"
        "<blockquote>Select an admin to remove:</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^do_rem_admin_(\d+)_(\d+)$"))
async def do_rem_admin_callback(client: Client, query: CallbackQuery):
    """Remove an admin."""
    match = re.match(r"^do_rem_admin_(\d+)_(\d+)$", query.data)
    bot_id, admin_id = int(match.group(1)), int(match.group(2))

    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    worker_db = WorkerDB(bot_id)
    await worker_db.del_admin(admin_id)

    await query.answer(f"✅ Admin {admin_id} removed!", show_alert=True)
    await admins_callback(client, query)


async def handle_admin_input(client: Client, message: Message, state: dict):
    """Process admin ID input."""
    user_id = message.from_user.id
    bot_id = state["data"]["bot_id"]

    try:
        admin_id = int(message.text.strip())
    except ValueError:
        await message.reply("<b>❌ Invalid user ID. Send a number.</b>")
        return

    worker_db = WorkerDB(bot_id)
    await worker_db.add_admin(admin_id)

    creation_state = _get_state()
    creation_state.pop(user_id, None)

    await message.reply(
        f"<b>✅ Admin <code>{admin_id}</code> added!</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data=f"admins_{bot_id}")],
        ]),
    )


# =============================================================================
# STATISTICS
# =============================================================================

@Client.on_callback_query(filters.regex(r"^stats_(\d+)$"))
async def stats_callback(client: Client, query: CallbackQuery):
    """Show bot statistics."""
    bot_id = _extract_bot_id(r"^stats_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    worker_db = WorkerDB(bot_id)
    total_users = await worker_db.total_users()
    total_admins = len(await worker_db.get_all_admins())
    total_channels = len(await worker_db.show_channels())
    total_banned = len(await worker_db.get_ban_users())

    created_at = bot.get("created_at", "Unknown")
    if hasattr(created_at, "strftime"):
        created_at = created_at.strftime("%Y-%m-%d %H:%M UTC")

    try:
        await query.message.edit_text(
            f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 𝗕𝗢𝗧 𝗦𝗧𝗔𝗧𝗜𝗦𝗧𝗜𝗖𝗦\n"
            f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            f"<blockquote>"
            f"◈ <b>ʙᴏᴛ:</b> @{bot.get('bot_username', 'unknown')}\n"
            f"◈ <b>ᴄʀᴇᴀᴛᴇᴅ:</b> {created_at}\n\n"
            f"👥 <b>ᴛᴏᴛᴀʟ ᴜsᴇʀs:</b> {total_users}\n"
            f"👨‍💼 <b>ᴀᴅᴍɪɴs:</b> {total_admins}\n"
            f"📢 <b>ꜰsᴜʙ ᴄʜᴀɴɴᴇʟs:</b> {total_channels}\n"
            f"🚫 <b>ʙᴀɴɴᴇᴅ:</b> {total_banned}"
            f"</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 ʀᴇꜰʀᴇsʜ", callback_data=f"stats_{bot_id}")],
                [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"dashboard_{bot_id}")],
            ]),
        )
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" in str(e):
            pass
        else:
            log.error(f"Error editing stats message: {e}")
            
    await query.answer()

# =============================================================================
# START CONFIGURATION
# =============================================================================

@Client.on_callback_query(filters.regex(r"^startcfg_(\d+)$"))
async def startcfg_callback(client: Client, query: CallbackQuery):
    """Show start configuration options."""
    bot_id = _extract_bot_id(r"^startcfg_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    _get_state().pop(query.from_user.id, None)

    settings = bot.get("settings", {})
    start_msg = settings.get("start_message", "ᴅᴇꜰᴀᴜʟᴛ")
    start_pic = settings.get("start_pic", "ɴᴏɴᴇ")

    if start_msg and len(start_msg) > 50:
        start_msg = start_msg[:47] + "..."
    if not start_msg:
        start_msg = "ᴅᴇꜰᴀᴜʟᴛ"
    if not start_pic:
        start_pic = "ɴᴏɴᴇ"

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"📩 𝗦𝗧𝗔𝗥𝗧 𝗖𝗢𝗡𝗙𝗜𝗚\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>◈ <b>ᴍᴇssᴀɢᴇ:</b>\n{start_msg}\n\n"
        f"◈ <b>ᴘʜᴏᴛᴏ:</b> {start_pic}</blockquote>\n\n"
        f"<i>sᴇʟᴇᴄᴛ ᴡʜᴀᴛ ᴛᴏ ᴄʜᴀɴɢᴇ:</i>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 ᴍᴇssᴀɢᴇ", callback_data=f"set_startmsg_{bot_id}"),
                InlineKeyboardButton("🖼 ᴘʜᴏᴛᴏ", callback_data=f"set_startpic_{bot_id}"),
            ],
            [InlineKeyboardButton("🤖 ʙᴏᴛ ᴘʀᴏꜰɪʟᴇ ᴘʜᴏᴛᴏ", callback_data=f"set_botphoto_{bot_id}")],
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()

@Client.on_callback_query(filters.regex(r"^set_startmsg_(\d+)$"))
async def set_startmsg_callback(client: Client, query: CallbackQuery):
    bot_id = _extract_bot_id(r"^set_startmsg_(\d+)$", query.data)
    user_id = query.from_user.id
    bot = await _verify_ownership(query, bot_id)
    if not bot: return

    state = _get_state()
    state[user_id] = {"step": "settings", "action": "set_startmsg", "data": {"bot_id": bot_id}}
    
    await query.message.edit_text(
        "<b>📝 Send the new Start Message (HTML supported):</b>\n\n"
        "<i>Or click Cancel to abort.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"startcfg_{bot_id}")]])
    )
    await query.answer()

@Client.on_callback_query(filters.regex(r"^set_startpic_(\d+)$"))
async def set_startpic_callback(client: Client, query: CallbackQuery):
    bot_id = _extract_bot_id(r"^set_startpic_(\d+)$", query.data)
    user_id = query.from_user.id
    bot = await _verify_ownership(query, bot_id)
    if not bot: return

    state = _get_state()
    state[user_id] = {"step": "settings", "action": "set_startpic", "data": {"bot_id": bot_id}}
    
    await query.message.edit_text(
        "<b>🖼 Send the new Start Photo (as an image or telegraph link text):</b>\n\n"
        "<i>Or click Cancel to abort.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"startcfg_{bot_id}")]])
    )
    await query.answer()

@Client.on_callback_query(filters.regex(r"^set_botphoto_(\d+)$"))
async def set_botphoto_callback(client: Client, query: CallbackQuery):
    """Bot API 9.4 setMyProfilePhoto – change the clone's own avatar from here."""
    bot_id = _extract_bot_id(r"^set_botphoto_(\d+)$", query.data)
    user_id = query.from_user.id
    bot = await _verify_ownership(query, bot_id)
    if not bot: return
    from filestore.worker_bot.engine import worker_engine
    from core import botapi
    if not worker_engine.get_worker(bot_id) and not botapi.enabled():
        return await query.answer("🔴 Start the bot first – the photo is set through the running clone.", show_alert=True)

    state = _get_state()
    state[user_id] = {"step": "settings", "action": "set_botphoto", "data": {"bot_id": bot_id}}

    await query.message.edit_text(
        f"<b>🤖 Bot profile photo · @{bot.get('bot_username', '')}</b>\n\n"
        "<blockquote>Send a <b>photo</b> to use it as your bot's avatar.\n"
        "Send <code>0</code> to remove the current avatar.</blockquote>\n\n"
        "<i>No @BotFather needed. Or click Cancel to abort.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"startcfg_{bot_id}")]])
    )
    await query.answer()


async def handle_botphoto_input(client: Client, message: Message, state: dict):
    import os
    user_id = message.from_user.id
    bot_id = state["data"]["bot_id"]
    back = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"startcfg_{bot_id}")]])
    from filestore.worker_bot.engine import worker_engine
    from core.profile import download_photo, remove_bot_photo, set_bot_photo
    from core import botapi
    worker = worker_engine.get_worker(bot_id)
    token = ""
    if botapi.enabled():  # Bot API with the clone's own token – works even while it's stopped
        try:
            from filestore.utils.security import decrypt_token
            doc = await main_db.get_bot(bot_id)
            token = decrypt_token(doc["bot_token_encrypted"]) if doc else ""
        except Exception:
            token = ""
    if not worker and not token:
        _get_state().pop(user_id, None)
        return await message.reply("<b>🔴 The bot is not running – start it and try again.</b>", reply_markup=back)
    if message.text and message.text.strip() == "0":
        try:
            await remove_bot_photo(worker, token=token)
        except Exception as e:
            return await message.reply(f"<b>❌ Couldn't remove the photo:</b> <code>{str(e)[:150]}</code>", reply_markup=back)
        _get_state().pop(user_id, None)
        return await message.reply("<b>🗑 Bot profile photo removed.</b>", reply_markup=back)
    if not (message.photo or (message.document and (message.document.mime_type or "").startswith("image/"))):
        return await message.reply("<b>❌ Please send a photo (or 0 to remove).</b>")
    status = await message.reply("<b>⏳ Updating your bot's profile photo…</b>")
    path = await download_photo(client, message)
    try:
        await set_bot_photo(worker, path, token=token)
    except Exception as e:
        return await status.edit_text(f"<b>❌ Telegram rejected the photo:</b>\n<code>{str(e)[:200]}</code>", reply_markup=back)
    finally:
        if path and os.path.exists(path):
            os.remove(path)
    _get_state().pop(user_id, None)
    await status.edit_text("<b>✅ Bot profile photo updated!</b>\n<i>It can take a moment to show everywhere.</i>",
                           reply_markup=back)


@Client.on_callback_query(filters.regex(r"^captioncfg_(\d+)$"))
async def captioncfg_callback(client: Client, query: CallbackQuery):
    bot_id = _extract_bot_id(r"^captioncfg_(\d+)$", query.data)
    user_id = query.from_user.id
    bot = await _verify_ownership(query, bot_id)
    if not bot: return

    state = _get_state()
    state[user_id] = {"step": "settings", "action": "set_custom_caption", "data": {"bot_id": bot_id}}
    
    await query.message.edit_text(
        "<b>📝 Set Custom Caption:</b>\n\n"
        "<blockquote>This text will be appended below the existing file caption.\n"
        "You can use placeholders like <code>{size}</code>, <code>{name}</code>, <code>{language}</code>, etc.\n\n"
        "Send <code>0</code> to remove the custom caption.</blockquote>\n\n"
        "<i>Or click Cancel to abort.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"dashboard_{bot_id}")]])
    )
    await query.answer()

async def handle_startcfg_input(client: Client, message: Message, state: dict):
    user_id = message.from_user.id
    bot_id = state["data"]["bot_id"]
    action = state["action"]

    if action == "set_botphoto":  # no worker restart needed
        return await handle_botphoto_input(client, message, state)

    if action == "set_startmsg":
        new_text = message.text
        if not new_text:
            await message.reply("<b>❌ Please send text.</b>")
            return
        
        if new_text.strip() == "0":
            new_text = ""
            
        await main_db.update_setting(bot_id, "start_message", new_text)
        await message.reply("<b>✅ Start message updated!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"startcfg_{bot_id}")]]))
        
    elif action == "set_startpic":
        pic_url = ""
        if message.photo or message.document:
            pic_url = await upload_to_telegraph(client, message)
            if not pic_url:
                return  # error already sent by helper
        elif message.text:
            if message.text.strip() == "0":
                pic_url = ""
            else:
                pic_url = message.text.strip()
        else:
            await message.reply("<b>❌ Please send a photo or a link.</b>")
            return
        
        await main_db.update_setting(bot_id, "start_pic", pic_url)
        await message.reply("<b>✅ Start photo updated!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"startcfg_{bot_id}")]]))

    elif action == "set_custom_caption":
        new_text = message.text.html if message.text else ""
        if not new_text:
            await message.reply("<b>❌ Please send text.</b>")
            return
        
        if new_text.strip() == "0":
            new_text = ""
            
        await main_db.update_setting(bot_id, "custom_caption", new_text)
        await message.reply("<b>✅ Custom caption updated!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"dashboard_{bot_id}")]]))

    # Stop and restart worker bot to apply changes
    try:
        from filestore.worker_bot.engine import worker_engine
        bot_doc = await main_db.get_bot(bot_id)
        await worker_engine.stop_worker(bot_id)
        import asyncio
        await asyncio.sleep(1) # Delay to allow Pyrogram connections to gracefully close
        await worker_engine.start_worker(bot_doc)
    except Exception as e:
        log.error(f"Failed to restart worker bot {bot_id} after cfg update: {e}")

    _get_state().pop(user_id, None)


# =============================================================================
# SHORTENER SETTINGS
# =============================================================================

@Client.on_callback_query(filters.regex(r"^shortener_(\d+)$"))
async def shortener_callback(client: Client, query: CallbackQuery):
    """Show shortener configuration."""
    bot_id = _extract_bot_id(r"^shortener_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    _get_state().pop(query.from_user.id, None)

    shortener = bot.get("shortener", {})
    enabled = shortener.get("enabled", False)
    domain = shortener.get("domain", "")
    api_key = shortener.get("api_key_encrypted", "")
    verify_expire = shortener.get("verify_expire", 86400)
    tutorial_link = shortener.get("tutorial_link", "")
    tutorial_enabled = shortener.get("tutorial_enabled", False)

    expire_hrs = verify_expire // 3600

    if api_key:
        try:
            masked_key = mask_api_key(decrypt_token(api_key))
        except Exception:  # legacy plain-text key
            masked_key = mask_api_key(api_key)
    else:
        masked_key = "ɴᴏᴛ sᴇᴛ"
    provider = shortener.get("provider", "adlinkfly")
    provider_label = {"adlinkfly": "AdLinkFly / Custom", "bitly": "Bitly", "tinyurl": "TinyURL",
                      "isgd": "is.gd", "vgd": "v.gd"}.get(provider, provider)

    status_icon = "✅" if enabled else "❌"
    tut_icon = "✅" if tutorial_enabled else "❌"

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔗 𝗦𝗛𝗢𝗥𝗧𝗘𝗡𝗘𝗥 𝗦𝗘𝗧𝗧𝗜𝗡𝗚𝗦\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>"
        f"◈ <b>sᴛᴀᴛᴜs:</b> {status_icon}\n"
        f"◈ <b>ᴘʀᴏᴠɪᴅᴇʀ:</b> {provider_label}\n"
        f"◈ <b>ᴅᴏᴍᴀɪɴ:</b> {domain or 'ɴᴏᴛ sᴇᴛ'}\n"
        f"◈ <b>ᴀᴘɪ ᴋᴇʏ:</b> {masked_key}\n"
        f"◈ <b>ᴠᴇʀɪꜰʏ ᴇxᴘɪʀʏ:</b> {expire_hrs}h\n"
        f"◈ <b>ᴛᴜᴛᴏʀɪᴀʟ:</b> {tut_icon}\n"
        f"◈ <b>ᴛᴜᴛ ʟɪɴᴋ:</b> {'sᴇᴛ' if tutorial_link else 'ɴᴏᴛ sᴇᴛ'}"
        f"</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    f"{'❌ ᴅɪsᴀʙʟᴇ' if enabled else '✅ ᴇɴᴀʙʟᴇ'}",
                    callback_data=f"short_toggle_{bot_id}",
                ),
            ],
            [InlineKeyboardButton("🏷 ᴘʀᴏᴠɪᴅᴇʀ", callback_data=f"short_provider_{bot_id}")],
            [
                InlineKeyboardButton("🌐 ᴅᴏᴍᴀɪɴ", callback_data=f"short_domain_{bot_id}"),
                InlineKeyboardButton("🔑 ᴀᴘɪ ᴋᴇʏ", callback_data=f"short_api_{bot_id}"),
            ],
            [
                InlineKeyboardButton("⏱ ᴇxᴘɪʀʏ", callback_data=f"short_expire_{bot_id}"),
                InlineKeyboardButton(
                    f"{'📹 ᴛᴜᴛ ᴏꜰꜰ' if tutorial_enabled else '📹 ᴛᴜᴛ ᴏɴ'}",
                    callback_data=f"short_tuttoggle_{bot_id}",
                ),
            ],
            [
                InlineKeyboardButton("📹 sᴇᴛ ᴛᴜᴛᴏʀɪᴀʟ", callback_data=f"short_tutorial_{bot_id}"),
            ],
            [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"dashboard_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^short_toggle_(\d+)$"))
async def short_toggle_callback(client: Client, query: CallbackQuery):
    """Toggle shortener on/off."""
    bot_id = _extract_bot_id(r"^short_toggle_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    short = bot.get("shortener", {})
    current = short.get("enabled", False)
    if not current and not (short.get("api_key_encrypted") and short.get("domain")):
        await query.answer("❌ Set the API key and domain first!", show_alert=True)
        return
    await main_db.update_shortener(bot_id, "enabled", not current)

    status = "ᴅɪsᴀʙʟᴇᴅ" if current else "ᴇɴᴀʙʟᴇᴅ"
    await query.answer(f"🔗 sʜᴏʀᴛᴇɴᴇʀ {status}!", show_alert=True)
    # Refresh
    await shortener_callback(client, query)


@Client.on_callback_query(filters.regex(r"^short_tuttoggle_(\d+)$"))
async def short_tuttoggle_callback(client: Client, query: CallbackQuery):
    """Toggle tutorial button on/off."""
    bot_id = _extract_bot_id(r"^short_tuttoggle_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    current = bot.get("shortener", {}).get("tutorial_enabled", False)
    await main_db.update_shortener(bot_id, "tutorial_enabled", not current)

    status = "ᴏꜰꜰ" if current else "ᴏɴ"
    await query.answer(f"📹 ᴛᴜᴛᴏʀɪᴀʟ {status}!", show_alert=True)
    await shortener_callback(client, query)


@Client.on_callback_query(filters.regex(r"^short_domain_(\d+)$"))
async def short_domain_callback(client: Client, query: CallbackQuery):
    """Prompt to set shortener domain."""
    bot_id = _extract_bot_id(r"^short_domain_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {"step": "settings", "action": "short_domain", "data": {"bot_id": bot_id}}

    await query.message.edit_text(
        "<b>━━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 𝗦𝗘𝗧 𝗗𝗢𝗠𝗔𝗜𝗡\n"
        "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<blockquote>sᴇɴᴅ ʏᴏᴜʀ sʜᴏʀᴛᴇɴᴇʀ ᴅᴏᴍᴀɪɴ.\n\n"
        "ᴇxᴀᴍᴘʟᴇ: <code>example.com</code></blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"shortener_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^short_api_(\d+)$"))
async def short_api_callback(client: Client, query: CallbackQuery):
    """Prompt to set shortener API key."""
    bot_id = _extract_bot_id(r"^short_api_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {"step": "settings", "action": "short_api", "data": {"bot_id": bot_id}}

    await query.message.edit_text(
        "<b>━━━━━━━━━━━━━━━━━━━━━\n"
        "🔑 𝗦𝗘𝗧 𝗔𝗣𝗜 𝗞𝗘𝗬\n"
        "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<blockquote>sᴇɴᴅ ʏᴏᴜʀ sʜᴏʀᴛᴇɴᴇʀ ᴀᴘɪ ᴋᴇʏ.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"shortener_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^short_expire_(\d+)$"))
async def short_expire_callback(client: Client, query: CallbackQuery):
    """Prompt to set verify expiry time."""
    bot_id = _extract_bot_id(r"^short_expire_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {"step": "settings", "action": "short_expire", "data": {"bot_id": bot_id}}

    current = bot.get("shortener", {}).get("verify_expire", 86400) // 3600

    await query.message.edit_text(
        f"<b>━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏱ 𝗦𝗘𝗧 𝗩𝗘𝗥𝗜𝗙𝗬 𝗘𝗫𝗣𝗜𝗥𝗬\n"
        f"━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>ᴄᴜʀʀᴇɴᴛ: <b>{current}h</b>\n\n"
        f"sᴇɴᴅ ɴᴇᴡ ᴇxᴘɪʀʏ ɪɴ ʜᴏᴜʀs.\n"
        f"ᴇxᴀᴍᴘʟᴇ: <code>24</code> ꜰᴏʀ 24 ʜᴏᴜʀs</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"shortener_{bot_id}")],
        ]),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^short_tutorial_(\d+)$"))
async def short_tutorial_callback(client: Client, query: CallbackQuery):
    """Prompt to set tutorial video link."""
    bot_id = _extract_bot_id(r"^short_tutorial_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    state = _get_state()
    state[query.from_user.id] = {"step": "settings", "action": "short_tutorial", "data": {"bot_id": bot_id}}

    await query.message.edit_text(
        "<b>━━━━━━━━━━━━━━━━━━━━━\n"
        "📹 𝗦𝗘𝗧 𝗧𝗨𝗧𝗢𝗥𝗜𝗔𝗟\n"
        "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        "<blockquote>sᴇɴᴅ ᴛʜᴇ ᴛᴜᴛᴏʀɪᴀʟ ᴠɪᴅᴇᴏ ʟɪɴᴋ.\n\n"
        "ᴛʜɪs ᴄᴀɴ ʙᴇ:\n"
        "◈ ᴀ ᴛᴇʟᴇɢʀᴀᴍ ᴍᴇssᴀɢᴇ ʟɪɴᴋ\n"
        "◈ ᴀ ʏᴏᴜᴛᴜʙᴇ ᴜʀʟ\n"
        "◈ ᴀɴʏ ᴠɪᴅᴇᴏ ʟɪɴᴋ\n\n"
        "sᴇɴᴅ <code>0</code> ᴛᴏ ʀᴇᴍᴏᴠᴇ.</blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"shortener_{bot_id}")],
        ]),
    )
    await query.answer()


async def handle_shortener_input(client: Client, message: Message, state: dict, field: str = None):
    """Process shortener setting inputs (dashboard actions + the older
    awaiting_shortener_api_key / awaiting_shortener_domain steps)."""
    user_id = message.from_user.id
    bot_id = state["data"]["bot_id"]
    action = {"api_key": "short_api", "domain": "short_domain"}.get(field) or state.get("action", "")
    text = message.text.strip() if message.text else ""

    if not text:
        await message.reply("<b>❌ ᴘʟᴇᴀsᴇ sᴇɴᴅ ᴛᴇxᴛ.</b>")
        return

    if action == "short_domain":
        text = text.replace("https://", "").replace("http://", "").strip("/")
        if "." not in text:
            await message.reply("<b>❌ Invalid domain. Example: <code>example.com</code></b>")
            return
        await main_db.update_shortener(bot_id, "domain", text)
        await message.reply(
            "<b>✅ ᴅᴏᴍᴀɪɴ ᴜᴘᴅᴀᴛᴇᴅ!</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"shortener_{bot_id}")],
            ]),
        )

    elif action == "short_api":
        if len(text) < 5:
            await message.reply("<b>❌ API key seems too short.</b>")
            return
        # stored encrypted – the worker decrypts it (it used to be saved in plain text)
        await main_db.update_shortener(bot_id, "api_key_encrypted", encrypt_token(text))
        await message.reply(
            "<b>✅ ᴀᴘɪ ᴋᴇʏ ᴜᴘᴅᴀᴛᴇᴅ!</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"shortener_{bot_id}")],
            ]),
        )

    elif action == "short_expire":
        try:
            hours = int(text)
            if hours < 1 or hours > 720:
                await message.reply("<b>❌ ᴍᴜsᴛ ʙᴇ 1-720 ʜᴏᴜʀs.</b>")
                return
            await main_db.update_shortener(bot_id, "verify_expire", hours * 3600)
            await message.reply(
                f"<b>✅ ᴠᴇʀɪꜰʏ ᴇxᴘɪʀʏ sᴇᴛ ᴛᴏ {hours}h!</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"shortener_{bot_id}")],
                ]),
            )
        except ValueError:
            await message.reply("<b>❌ sᴇɴᴅ ᴀ ɴᴜᴍʙᴇʀ.</b>")
            return

    elif action == "short_tutorial":
        if text == "0":
            text = ""
        await main_db.update_shortener(bot_id, "tutorial_link", text)
        await message.reply(
            "<b>✅ ᴛᴜᴛᴏʀɪᴀʟ ᴜᴘᴅᴀᴛᴇᴅ!</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 ʙᴀᴄᴋ", callback_data=f"shortener_{bot_id}")],
            ]),
        )

    # Restart worker to apply
    try:
        from filestore.worker_bot.engine import worker_engine
        bot_doc = await main_db.get_bot(bot_id)
        await worker_engine.stop_worker(bot_id)
        await worker_engine.start_worker(bot_doc)
    except Exception as e:
        log.error(f"Failed to restart worker {bot_id} after shortener update: {e}")

    _get_state().pop(user_id, None)


# =============================================================================
# NEW: Shortener Provider Selection
# =============================================================================

@Client.on_callback_query(filters.regex(r"^short_provider_(\d+)$"))
async def short_provider_callback(client: Client, query: CallbackQuery):
    """Choose shortener provider."""
    bot_id = _extract_bot_id(r"^short_provider_(\d+)$", query.data)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    providers = [
        ("adlinkfly", "AdLinkFly / Custom"),
        ("bitly", "Bitly"),
        ("tinyurl", "TinyURL"),
        ("isgd", "is.gd"),
        ("vgd", "v.gd"),
    ]
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"setprov_{bot_id}_{key}")]
        for key, name in providers
    ]
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"shortener_{bot_id}")])

    await query.message.edit_text(
        "<b>🏷 Select Shortener Provider</b>\n\n"
        "<blockquote>"
        "• <b>AdLinkFly / Custom</b> — Most Indian shortener sites\n"
        "• <b>Bitly</b> — Needs API token\n"
        "• <b>TinyURL</b> — Free, optional token\n"
        "• <b>is.gd / v.gd</b> — Free, no key needed"
        "</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^setprov_(\d+)_(\w+)$"))
async def set_provider_callback(client: Client, query: CallbackQuery):
    """Save selected provider."""
    import re
    match = re.match(r"^setprov_(\d+)_(\w+)$", query.data)
    bot_id = int(match.group(1))
    provider = match.group(2)
    bot = await _verify_ownership(query, bot_id)
    if not bot:
        return

    await main_db.update_shortener(bot_id, "provider", provider)
    await query.answer(f"✅ Provider set to {provider}", show_alert=True)
    # Refresh shortener menu
    query.data = f"shortener_{bot_id}"
    await shortener_callback(client, query)

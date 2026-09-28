
import html
import asyncio
from datetime import datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    RequestPeerTypeChannel,
)
from filestore.fs_config import BOT_CREATION_COOLDOWN, API_ID, API_HASH, LOGGER, CLONE_ENABLED, OWNERS
from filestore.database.main_db import MainDB
from filestore.utils.helpers import validate_bot_token, send_main_log
from filestore.utils.security import encrypt_token, mask_token

log = LOGGER(__name__)
main_db = MainDB()

# Track users currently in bot creation flow
_creation_state = {}  # user_id -> {"step": str, "data": dict}

# Native channel picker (KeyboardButtonRequestPeer) used wherever a channel ID
# is asked for. The shared channel arrives as a service message with
# ``chats_shared`` and is converted to its -100… ID, so typing the ID still works.
CHANNEL_PICKER_ID = 11
CHANNEL_STEPS = ("awaiting_channel", "awaiting_new_log_channel", "awaiting_fsub_channel")


def channel_picker_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("📢 Select channel",
                         request_chat=RequestPeerTypeChannel(button_id=CHANNEL_PICKER_ID))]],
        resize_keyboard=True, one_time_keyboard=True, placeholder="Pick a channel or type its ID",
    )


async def offer_channel_picker(client, user_id: int, state: dict = None):
    """Show the 📢 Select channel reply-keyboard (best-effort)."""
    try:
        await client.send_message(
            user_id, "👇 <i>Tap <b>📢 Select channel</b> to pick it from your list – or just type the ID.</i>",
            reply_markup=channel_picker_kb())
        if state is not None:
            state["picker"] = True
    except Exception as e:
        log.debug(f"channel picker failed: {e}")


async def remove_channel_picker(client, user_id: int, state: dict = None, text: str = None):
    """Hide the picker keyboard if it was shown for this flow."""
    if state is not None and not state.pop("picker", False):
        return
    try:
        await client.send_message(user_id, text or "⌨️", reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass


async def _has_shared_chat(_, __, m: Message) -> bool:
    return bool(getattr(m, "chats_shared", None))


shared_chat_filter = filters.create(_has_shared_chat)



async def accept_token(client: Client, user_id: int, token: str, status_msg, managed: bool = False) -> bool:
    """Validate a bot token and move the wizard to the channel step.
    Shared by the classic "paste your token" flow and the one-tap managed-bot flow."""
    state = _creation_state.setdefault(user_id, {"step": "awaiting_token", "data": {}})
    state.setdefault("data", {})
    if managed:
        state["data"]["managed"] = True
    bot_info = await validate_bot_token(token)
    if not bot_info:
        await status_msg.edit_text(
            "<b>❌ Invalid bot token!</b>\n\n"
            "The token could not be verified with Telegram.\n"
            "Please check and send again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
            ]),
        )
        return False

    # Check if bot is already registered
    bot_id = bot_info["id"]
    existing = await main_db.get_bot(bot_id)
    if existing:
        if existing.get("is_deleted") and existing.get("owner_id") == user_id:
            pass # Allow recreating a soft-deleted bot
        else:
            await status_msg.edit_text(
                "<b>❌ This bot is already registered!</b>\n\n"
                f"Bot @{bot_info.get('username', 'unknown')} is already in use.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
                ]),
            )
            _creation_state.pop(user_id, None)
            return False

    # Save token and move to next step
    state["data"]["token"] = token
    state["data"]["bot_info"] = bot_info
    state["step"] = "awaiting_channel"

    bot_name = bot_info.get("first_name", "Unknown")
    bot_username = bot_info.get("username", "unknown")

    await status_msg.edit_text(
        f"<b>✅ Token verified!</b>\n\n"
        f"<blockquote>Bot: <b>{html.escape(str(bot_name))}</b> (@{bot_username})\n\n"
        f"<b>Step 2/2:</b> Send me the <b>Log Channel ID</b>.\n\n"
        f"This is where your bot will store files.\n"
        f"Make sure the bot (@{bot_username}) is an admin in the channel.\n\n"
        f"Example: <code>-1001234567890</code></blockquote>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
        ]),
    )
    await offer_channel_picker(client, user_id, state)
    return True


# =============================================================================
# CALLBACK: Create Bot (entry point)
# =============================================================================

@Client.on_callback_query(filters.regex(r"^create_bot$"))
async def create_bot_callback(client: Client, query: CallbackQuery):
    """Start the bot creation flow."""
    user_id = query.from_user.id

    if not CLONE_ENABLED and user_id not in OWNERS:
        await query.answer("🚧 Clone bot creation is currently disabled.", show_alert=True)
        return

    # Check bot limit
    bot_count = await main_db.count_user_bots(user_id)
    from core.plans import clone_limit
    limit = await clone_limit(user_id)
    if bot_count >= limit:
        await query.answer(
            f"❌ You've reached the limit of {limit} bot{'s' if limit != 1 else ''}! "
            "Upgrade with /plans (🤖 Clone Plus / 🚀 Clone Pro) for more.",
            show_alert=True,
        )
        return

    # Check cooldown
    last_created = await main_db.get_cooldown(user_id)
    if last_created:
        if last_created.tzinfo is not None:  # stored aware, Mongo may return naive UTC
            last_created = last_created.replace(tzinfo=None) - (last_created.utcoffset() or timedelta(0))
        elapsed = (datetime.utcnow() - last_created).total_seconds()
        if elapsed < BOT_CREATION_COOLDOWN:
            remaining = int(BOT_CREATION_COOLDOWN - elapsed)
            await query.answer(
                f"⏳ Please wait {remaining}s before creating another bot.",
                show_alert=True,
            )
            return

    # Set user state to "awaiting token"
    _creation_state[user_id] = {"step": "awaiting_token", "data": {}}

    # Bot Management Mode → offer one-tap creation (no BotFather / token copy)
    from filestore.main_bot.plugins import managed_bots
    one_tap = await managed_bots.available()
    kb = [[InlineKeyboardButton("⚡ One-tap create (no token)", callback_data="managed_new")]] if one_tap else []
    kb.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")])
    extra = ("\n\n<b>⚡ New:</b> tap <b>One-tap create</b> and Telegram makes the bot for you – "
             "no BotFather, no token.") if one_tap else ""

    await query.message.edit_text(
        text=(
            "<b>🤖 Create a New Bot</b>\n\n"
            "<blockquote><b>Step 1/2:</b> Send me your bot token.\n\n"
            "You can get a bot token from @BotFather.\n"
            "Example: <code>123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw</code></blockquote>"
            + extra
        ),
        reply_markup=InlineKeyboardMarkup(kb),
    )
    await query.answer()


# =============================================================================
# CALLBACK: Cancel creation
# =============================================================================

@Client.on_callback_query(filters.regex(r"^cancel_creation$"))
async def cancel_creation_callback(client: Client, query: CallbackQuery):
    """Cancel the bot creation flow."""
    user_id = query.from_user.id
    old = _creation_state.pop(user_id, None)
    if old and old.get("picker"):
        await remove_channel_picker(client, user_id, old, "❌ Cancelled.")

    await query.message.edit_text(
        text="<b>❌ Bot creation cancelled.</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
        ]),
    )
    await query.answer()


# =============================================================================
# MESSAGE HANDLER: Process bot creation inputs
# =============================================================================

async def _in_creation_state(_, __, message: Message) -> bool:
    return bool(message.from_user) and message.from_user.id in _creation_state


creation_state_filter = filters.create(_in_creation_state)


# Only fires while the user is inside a clone-bot creation / settings flow,
# so it never swallows messages meant for the saver / encoder modules.
@Client.on_message(filters.private & creation_state_filter & (filters.text | filters.photo | filters.document | shared_chat_filter) & ~filters.regex(r"^/") & ~filters.bot)
async def handle_creation_input(client: Client, message: Message):
    """Handle text input during bot creation flow."""
    user_id = message.from_user.id

    if user_id not in _creation_state:
        return  # Not in creation flow, ignore

    state = _creation_state[user_id]
    step = state["step"]

    # 📢 Channel shared through the native picker → treat it as a typed ID
    if getattr(message, "chats_shared", None):
        from core.payments import shared_ids
        picked = shared_ids(message)
        if step not in CHANNEL_STEPS or not picked:
            await message.reply("<b>❌ Please send valid text.</b>")
            return
        channel_id, channel_name = picked[0]
        message.text = str(channel_id)
        from core.botlog import esc
        await remove_channel_picker(client, user_id, state,
                                    f"📢 Selected: <b>{esc(channel_name)}</b> (<code>{channel_id}</code>)")
    elif step in CHANNEL_STEPS and message.text:
        await remove_channel_picker(client, user_id, state, f"📢 Channel: <code>{message.text.strip()[:32]}</code>")

    # Global safeguard: enforce text everywhere except when setting a start_pic
    if not message.text:
        is_pic_step = step == "settings" and state.get("action") in ("set_startpic", "set_botphoto")
        if not is_pic_step:
            await message.reply("<b>❌ Please send valid text.</b>")
            return

    # -------------------------------------------------------------------------
    # STEP 1: Awaiting bot token
    # -------------------------------------------------------------------------
    if step == "awaiting_token":
        token = message.text.strip()

        if token.startswith("@"):
            from filestore.main_bot.plugins import managed_bots
            if await managed_bots.claim_username(client, message, user_id, token):
                return

        # Basic format check
        if ":" not in token or len(token) < 20:
            await message.reply(
                "<b>❌ Invalid token format.</b>\n\n"
                "A valid bot token looks like: <code>123456:AAH...</code>\n"
                "Please send a valid token or click Cancel.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                ]),
            )
            return

        # Validate with Telegram API
        status_msg = await message.reply("<b>⏳ Validating bot token...</b>")

        await accept_token(client, user_id, token, status_msg)

    # -------------------------------------------------------------------------
    # STEP 2: Awaiting log channel ID
    # -------------------------------------------------------------------------
    elif step == "awaiting_channel":
        channel_input = message.text.strip()

        # Validate channel ID format
        try:
            channel_id = int(channel_input)
        except ValueError:
            await message.reply(
                "<b>❌ Invalid channel ID.</b>\n\n"
                "Channel ID should be a number like <code>-1001234567890</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                ]),
            )
            return

        status_msg = await message.reply("<b>⏳ Validating channel access...</b>")

        # Validate that the worker bot can access the channel
        token = state["data"]["token"]
        bot_info = state["data"]["bot_info"]
        channel_title = str(channel_id)

        try:
            # Create a temporary Pyrogram client to verify channel access
            temp_client = Client(
                name=f"verify_{bot_info['id']}",
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=token,
                in_memory=True,
            )

            await temp_client.start()

            try:
                chat = await temp_client.get_chat(channel_id)
                channel_title = chat.title or str(channel_id)
                # Try sending a test message
                test_msg = await temp_client.send_message(
                    chat_id=channel_id, text="✅ Channel verified for FileStore bot."
                )
                await test_msg.delete()
            except Exception as e:
                await status_msg.edit_text(
                    f"<b>❌ Cannot access channel!</b>\n\n"
                    f"<blockquote>Make sure:\n"
                    f"1. The channel ID is correct\n"
                    f"2. The bot (@{bot_info.get('username', '')}) is an admin in the channel\n"
                    f"3. The bot has permission to send messages\n\n"
                    f"Error: <code>{str(e)[:100]}</code></blockquote>",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                    ]),
                )
                await temp_client.stop()
                return

            await temp_client.stop()

        except Exception as e:
            log.error(f"Channel validation error: {e}")
            await status_msg.edit_text(
                f"<b>❌ Verification failed!</b>\n\n"
                f"<blockquote>Error: <code>{str(e)[:150]}</code></blockquote>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
                ]),
            )
            return

        # Everything valid — save to database
        encrypted_token = encrypt_token(token)
        bot_id = bot_info["id"]
        bot_username = bot_info.get("username", "unknown")

        await main_db.add_bot(
            bot_id=bot_id,
            owner_id=user_id,
            bot_token_encrypted=encrypted_token,
            bot_username=bot_username,
            log_channel_id=channel_id,
        )
        if state["data"].get("managed"):
            # created through Bot Management Mode → Videl can rotate its token later
            await main_db.bots.update_one({"_id": bot_id}, {"$set": {"managed": True}})

        # Set cooldown
        await main_db.set_cooldown(user_id)

        # Clear creation state
        _creation_state.pop(user_id, None)

        # Start the worker bot
        try:
            from filestore.worker_bot.engine import worker_engine
            bot_doc = await main_db.get_bot(bot_id)
            await worker_engine.start_worker(bot_doc)
            worker_started = True
            
            # Log to the owner log channel (+ owner DM)
            try:
                from core import botlog
                user_bots = await main_db.bots.count_documents({"owner_id": user_id, "is_deleted": {"$ne": True}})
                total_bots = await main_db.bots.count_documents({"is_deleted": {"$ne": True}})
                title = channel_title
                await botlog.event("CloneCreated", (
                    f"{botlog.user_block(message.from_user)}\n\n"
                    f"<b>🤖 Clone bot:</b> {botlog.esc(bot_info.get('first_name', ''))} (@{bot_username})\n"
                    f"<b>🆔 Bot ID:</b> <code>{bot_id}</code>\n"
                    f"<b>📦 DB channel:</b> {botlog.esc(title)} (<code>{channel_id}</code>)\n"
                    f"<b>🔑 Token:</b> <code>{mask_token(token)}</code>\n"
                    f"<b>📊 Clones of this user:</b> {user_bots} · <b>Platform total:</b> {total_bots}"
                ), client=client)
            except Exception as e:
                log.warning(f"clone log failed: {e}")
            
        except Exception as e:
            log.error(f"Failed to start worker for bot {bot_id}: {e}")
            worker_started = False

        status_icon = "🟢" if worker_started else "🟡"
        status_text = "Running" if worker_started else "Pending restart"

        await status_msg.edit_text(
            f"<b>✅ Bot Created Successfully!</b>\n\n"
            f"<blockquote>"
            f"<b>Bot:</b> @{bot_username}\n"
            f"<b>Channel:</b> <code>{channel_id}</code>\n"
            f"<b>Status:</b> {status_icon} {status_text}\n\n"
            f"Your bot is ready! Use the dashboard to configure it."
            f"</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Bot Dashboard", callback_data=f"dashboard_{bot_id}")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
            ]),
        )

    # -------------------------------------------------------------------------
    # STEP: Awaiting shortener API key (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_shortener_api_key":
        from filestore.main_bot.plugins.bot_settings import handle_shortener_input
        await handle_shortener_input(client, message, state, "api_key")

    # -------------------------------------------------------------------------
    # STEP: Awaiting shortener domain (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_shortener_domain":
        from filestore.main_bot.plugins.bot_settings import handle_shortener_input
        await handle_shortener_input(client, message, state, "domain")

    # -------------------------------------------------------------------------
    # STEP: Awaiting force-sub channel (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_fsub_channel":
        from filestore.main_bot.plugins.bot_settings import handle_fsub_channel_input
        await handle_fsub_channel_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Awaiting admin ID (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_admin_id":
        from filestore.main_bot.plugins.bot_settings import handle_admin_input
        await handle_admin_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Awaiting log channel update (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_new_log_channel":
        from filestore.main_bot.plugins.bot_settings import handle_log_channel_input
        await handle_log_channel_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Awaiting auto-delete time (from bot_settings.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_auto_delete_time":
        from filestore.main_bot.plugins.bot_settings import handle_auto_delete_input
        await handle_auto_delete_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Custom maintenance message
    # -------------------------------------------------------------------------
    elif step == "awaiting_maint_msg":
        bot_id = state["data"]["bot_id"]
        text = (message.text or "").strip()
        if text.lower() in ("/cancel", "cancel"):
            from filestore.main_bot.plugins.bot_settings import _get_state
            _get_state().pop(message.from_user.id, None)
            await message.reply(
                "<b>❌ Cancelled.</b>",
                reply_markup=__import__("pyrogram").types.InlineKeyboardMarkup([
                    [__import__("pyrogram").types.InlineKeyboardButton("🔙 Back", callback_data=f"toggle_maint_{bot_id}")]
                ]),
            )
            return
        if len(text) < 3:
            await message.reply("<b>❌ Message too short.</b>")
            return
        await main_db.update_setting(bot_id, "maintenance_msg", text)
        from filestore.main_bot.plugins.bot_settings import _get_state
        _get_state().pop(message.from_user.id, None)
        await message.reply(
            "<b>✅ Maintenance message updated!</b>",
            reply_markup=__import__("pyrogram").types.InlineKeyboardMarkup([
                [__import__("pyrogram").types.InlineKeyboardButton("🔙 Maintenance", callback_data=f"toggle_maint_{bot_id}")]
            ]),
        )

    # -------------------------------------------------------------------------
    # STEP: New owner for 👑 ownership transfer (clone_extras_panel.py)
    # -------------------------------------------------------------------------
    elif step == "awaiting_new_owner":
        from filestore.main_bot.plugins.clone_extras_panel import handle_new_owner_input
        await handle_new_owner_input(client, message, state)

    # -------------------------------------------------------------------------
    # STEP: Restore settings from JSON backup
    # -------------------------------------------------------------------------
    elif step == "awaiting_restore_json":
        bot_id = state["data"]["bot_id"]
        raw = (message.text or "").strip()
        if raw.lower() in ("/cancel", "cancel"):
            from filestore.main_bot.plugins.bot_settings import _get_state
            _get_state().pop(message.from_user.id, None)
            await message.reply("<b>❌ Restore cancelled.</b>")
            return
        import json
        try:
            # Allow code block wrapping
            if "```" in raw:
                raw = raw.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw)
        except Exception as e:
            await message.reply(f"<b>❌ Invalid JSON:</b> <code>{html.escape(str(e))}</code>")
            return

        settings = data.get("settings", data)
        if not isinstance(settings, dict):
            await message.reply("<b>❌ That JSON has no settings.</b>")
            return
        from filestore.worker_bot.extras import TOGGLE_KEYS
        keys = ["auto_delete_time", "protect_content", "permanent_link", "maintenance_msg",
                "start_message", "start_pic", "force_pic", "custom_caption",
                "file_buttons", "help_text", "about_text", "premium_stars", "premium_days", *sorted(TOGGLE_KEYS)]
        aliases = {"start_msg": "start_message", "caption": "custom_caption"}   # names used by old backups
        applied = []
        for k, v in settings.items():
            k = aliases.get(k, k)
            if k in keys:
                await main_db.update_setting(bot_id, k, v)
                applied.append(k)

        short = data.get("shortener", {})
        if short.get("domain"):
            await main_db.update_shortener(bot_id, "domain", short["domain"])
            applied.append("shortener.domain")
        if short.get("provider"):
            await main_db.update_shortener(bot_id, "provider", short["provider"])
            applied.append("shortener.provider")

        from filestore.main_bot.plugins.bot_settings import _get_state
        _get_state().pop(message.from_user.id, None)
        try:
            from filestore.main_bot.plugins.my_bots import _clone_log
            await _clone_log(client, "CloneRestored", message.from_user, await main_db.get_bot(bot_id) or {"_id": bot_id},
                             f"<b>♻️ Applied:</b> {', '.join(applied) or 'nothing'}")
        except Exception:
            pass
        await message.reply(
            f"<b>✅ Restore complete</b>\n\n"
            f"<blockquote>Applied: {', '.join(applied) if applied else 'nothing'}</blockquote>",
            reply_markup=__import__("pyrogram").types.InlineKeyboardMarkup([
                [__import__("pyrogram").types.InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")]
            ]),
        )

    # -------------------------------------------------------------------------
    # STEP: Start Config (action set inside state)
    # -------------------------------------------------------------------------
    elif step == "settings":
        action = state.get("action", "")
        if action.startswith("short_"):
            from filestore.main_bot.plugins.bot_settings import handle_shortener_input
            await handle_shortener_input(client, message, state)
        else:
            from filestore.main_bot.plugins.bot_settings import handle_startcfg_input
            await handle_startcfg_input(client, message, state)

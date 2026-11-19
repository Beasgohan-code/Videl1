
import html
import asyncio
import re
import secrets
import time
from datetime import datetime, timedelta

import config
from pyrogram import Client, enums, filters
from pyrogram.types import (
    CallbackQuery,
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from filestore.fs_config import BOT_CREATION_COOLDOWN, API_ID, API_HASH, LOGGER, CLONE_ENABLED, OWNERS
from filestore.database.main_db import BotRegistrationConflict, MainDB
from filestore.utils.helpers import validate_bot_token
from filestore.utils.security import encrypt_token

log = LOGGER(__name__)
main_db = MainDB()

# Track users currently in bot creation flow
_creation_state = {}  # user_id -> {"step": str, "data": dict}
_verification_stops = {}  # Clients whose stop could not be confirmed in this process.
PROFILE_SELECTION_TIMEOUT = 300

# Native channel picker (KeyboardButtonRequestPeer) used wherever a channel ID
# is asked for. The shared channel arrives as a service message with
# ``chats_shared`` and is converted to its -100… ID, so typing the ID still works.
CHANNEL_PICKER_ID = 11
CHANNEL_STEPS = ("awaiting_channel", "awaiting_new_log_channel", "awaiting_fsub_channel")


# A nonce is scoped to both this in-process wizard and its displayed stage.
# Delayed or older screens must not cancel a newer stage or an unrelated flow.
_CREATION_STAGE_STEPS = {
    "token": ("awaiting_token", "verifying_token"),
    "channel": ("awaiting_channel", "verifying_channel"),
    "profile": ("awaiting_profile",),
}


def _stage_nonce(state, stage):
    if state.get("creation_control_stage") != stage:
        state["creation_control_stage"] = stage
        state["creation_control_nonce"] = secrets.token_urlsafe(8)
    return state["creation_control_nonce"]


def _creation_cancel_kb(state, stage):
    nonce = _stage_nonce(state, stage)
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"clone_setup_{nonce}_cancel")]])


def channel_picker_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton(
            "📢 Select channel",
            request_chat=KeyboardButtonRequestChat(
                button_id=CHANNEL_PICKER_ID,
                chat_is_channel=True,
                request_title=True,
                request_username=True,
            ),
        )]],
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
    except Exception:
        log.debug("Channel picker could not be displayed.")


async def remove_channel_picker(client, user_id: int, state: dict = None, text: str = None):
    """Hide the picker keyboard if it was shown for this flow."""
    if state is not None and not state.pop("picker", False):
        return
    try:
        await client.send_message(user_id, text or "⌨️", reply_markup=ReplyKeyboardRemove())
    except Exception:
        pass


async def _has_shared_chat(_, __, m: Message) -> bool:
    return bool(getattr(m, "chat_shared", None) or getattr(m, "chats_shared", None))


shared_chat_filter = filters.create(_has_shared_chat)



async def accept_token(client: Client, user_id: int, token: str, status_msg, managed: bool = False,
                       *, expected_state=None, expected_bot_id=None) -> bool:
    """Validate one token for the current in-process setup, never revive a cancelled flow."""
    if expected_state is None:
        state = _creation_state.setdefault(user_id, {"step": "awaiting_token", "data": {}})
    else:
        state = expected_state
    if _creation_state.get(user_id) is not state or state.get("step") != "awaiting_token":
        return False
    # Claim synchronously before network/database calls; duplicate submissions and
    # managed-bot callbacks must not race to choose a different credential.
    state["step"] = "verifying_token"
    state.setdefault("data", {})
    try:
        bot_info = await validate_bot_token(token)
        if _creation_state.get(user_id) is not state or state.get("step") != "verifying_token":
            return False
        if not bot_info:
            state["step"] = "awaiting_token"
            await status_msg.edit_text(
                "<b>❌ Invalid bot token!</b>\n\n"
                "The token could not be verified with Telegram.\n"
                "Please check and send again.",
                reply_markup=_creation_cancel_kb(state, "token"),
            )
            return False

        bot_id = bot_info["id"]
        if expected_bot_id is not None and bot_id != expected_bot_id:
            state["step"] = "awaiting_token"
            await status_msg.edit_text(
                "<b>⚠️ The verified bot did not match the Telegram update.</b> "
                "No clone was registered. You can paste the correct token instead.")
            return False
        existing = await main_db.get_bot(bot_id)
        if _creation_state.get(user_id) is not state or state.get("step") != "verifying_token":
            return False
        if existing and not (existing.get("is_deleted") is True and existing.get("owner_id") == user_id
                             and isinstance(existing.get("bot_token_encrypted"), str)):
            _creation_state.pop(user_id, None)
            await status_msg.edit_text(
                "<b>❌ This bot is already registered!</b>\n\n"
                f"Bot @{html.escape(str(bot_info.get('username', 'unknown')))} is already in use.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")],
                ]),
            )
            return False

        data = state["data"]
        for key in ("restore_deleted", "restore_expected_token", "restore_expected_profile", "restore_profile_present"):
            data.pop(key, None)
        data["managed"] = bool(managed)
        if existing:
            data["restore_deleted"] = True
            data["restore_expected_token"] = existing["bot_token_encrypted"]
            data["restore_profile_present"] = "clone_profile" in existing
            data["restore_expected_profile"] = existing.get("clone_profile")
        data["token"] = token
        data["bot_info"] = bot_info
        state["step"] = "awaiting_channel"
    except asyncio.CancelledError:
        if _creation_state.get(user_id) is state and state.get("step") == "verifying_token":
            state["step"] = "awaiting_token"
        raise
    except Exception:
        if _creation_state.get(user_id) is state and state.get("step") == "verifying_token":
            state["step"] = "awaiting_token"
            log.warning("Clone token verification could not be confirmed.")
            try:
                await status_msg.edit_text("<b>⚠️ Token verification could not be confirmed.</b> Please try again.")
            except Exception:
                pass
        return False

    bot_name = bot_info.get("first_name", "Unknown")
    bot_username = bot_info.get("username", "unknown")
    restore_notice = (
        "<b>Restoring a removed clone:</b> Continuing will update its token and channel "
        "and reactivate its saved settings, including any shortener configuration. "
        "Existing stored data is retained. Cancel if you do not want to restore it.\n\n"
        if state["data"].get("restore_deleted") else ""
    )

    await status_msg.edit_text(
        f"<b>✅ Token verified!</b>\n\n"
        f"<blockquote>Bot: <b>{html.escape(str(bot_name))}</b> (@{html.escape(str(bot_username))})\n\n"
        f"{restore_notice}"
        f"<b>Step 2/3:</b> Send me the <b>Log Channel ID</b>.\n\n"
        f"File Store uses it to store files; the other profiles still require this channel during setup.\n"
        f"Make sure the bot (@{html.escape(str(bot_username))}) is an admin in the channel.\n\n"
        f"Example: <code>-1001234567890</code></blockquote>",
        reply_markup=_creation_cancel_kb(state, "channel"),
    )
    if _creation_state.get(user_id) is state and state.get("step") == "awaiting_channel":
        await offer_channel_picker(client, user_id, state)
        return True
    return False


def _expire_profile_selection(user_id: int, nonce: str):
    state = _creation_state.get(user_id)
    if state and state.get("step") == "awaiting_profile" and state.get("profile_nonce") == nonce:
        _creation_state.pop(user_id, None)
        state.get("data", {}).pop("registration_token", None)
        state.get("data", {}).pop("restore_expected_token", None)


async def _show_creation_profiles(status_msg, user_id: int, state: dict):
    nonce = secrets.token_urlsafe(8)
    state["step"] = "awaiting_profile"
    state["profile_nonce"] = nonce
    state["profile_deadline"] = time.monotonic() + PROFILE_SELECTION_TIMEOUT
    state["profile_timer"] = asyncio.get_running_loop().call_later(PROFILE_SELECTION_TIMEOUT, _expire_profile_selection, user_id, nonce)
    restored = bool(state["data"].get("restore_deleted"))
    saved = state["data"].get("restore_expected_profile")
    present = state["data"].get("restore_profile_present", False)
    if restored:
        label = {"filestore": "File Store", "group": "Group Management"}.get(saved if present else "filestore", "Unsupported")
        intro = (f"Restoring a clone with saved mode: <b>{label}</b>. "
                 "Its mode and data will be kept. To change mode later, stop it in the dashboard.\n\n")
        buttons = [[InlineKeyboardButton(f"♻️ Keep saved mode · {label}", callback_data=f"clone_mode_{nonce}_keep")]]
    else:
        intro = "Choose one mode for this clone. You can change it later from its dashboard after stopping it.\n\n"
        buttons = [[InlineKeyboardButton("📁 File Store", callback_data=f"clone_mode_{nonce}_filestore")],
                   [InlineKeyboardButton("🛡 Group Management" + ("" if config.CLONE_PROFILES_SINGLE_INSTANCE else " · disabled"), callback_data=f"clone_mode_{nonce}_group")]]
    buttons += [[InlineKeyboardButton("🎵 Music · unavailable", callback_data=f"clone_mode_{nonce}_music")],
                *_creation_cancel_kb(state, "profile").inline_keyboard]
    await status_msg.edit_text(
        "<b>🧩 Step 3/3: Choose a bot mode</b>\n\n" + intro
        + "<b>File Store:</b> file links and delivery.\n"
          "<b>Group Management:</b> moderation, rules and welcomes; add the bot as a group admin.\n"
          "<b>Music:</b> voice-chat playback is not implemented.\n\n"
        + ("<i>Group mode is experimental and assumes one current worker.</i>" if config.CLONE_PROFILES_SINGLE_INSTANCE else
           "<i>Group mode is disabled until the operator verifies a single current worker.</i>")
        + "\n<i>This selection expires in 5 minutes.</i>",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


@Client.on_callback_query(filters.regex(r"^clone_mode_([A-Za-z0-9_-]{1,32})_(filestore|group|music|keep)$"))
async def creation_profile_callback(client: Client, query: CallbackQuery):
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    if (user is None or getattr(user, "is_bot", False) or chat is None
            or getattr(chat, "type", None) != enums.ChatType.PRIVATE or getattr(chat, "id", None) != user.id):
        return await query.answer("Setup is available only in your private chat.", show_alert=True)
    match = re.fullmatch(r"clone_mode_([A-Za-z0-9_-]{11})_(filestore|group|music|keep)", query.data or "")
    state = _creation_state.get(user.id)
    if not match or not state or state.get("step") != "awaiting_profile" or state.get("profile_nonce") != match.group(1):
        return await query.answer("This selection has expired. Start setup again.", show_alert=True)
    if time.monotonic() >= state.get("profile_deadline", 0):
        _expire_profile_selection(user.id, match.group(1))
        return await query.answer("This selection has expired. Start setup again.", show_alert=True)
    mode = match.group(2)
    if mode == "music":
        return await query.answer("Music voice-chat playback is not available yet.", show_alert=True)
    restored = bool(state["data"].get("restore_deleted"))
    if (mode == "keep") != restored:
        return await query.answer("This choice is not available for this setup.", show_alert=True)
    saved = state["data"].get("restore_expected_profile")
    present = state["data"].get("restore_profile_present", False)
    if restored:
        if present and saved not in ("filestore", "group"):
            return await query.answer("The saved mode is unsupported. Contact the operator.", show_alert=True)
        mode = saved if present else "filestore"
    if mode == "group" and not config.CLONE_PROFILES_SINGLE_INSTANCE:
        return await query.answer("Group mode is disabled until the operator verifies a single current worker.", show_alert=True)
    if not CLONE_ENABLED and user.id not in OWNERS:
        return await query.answer("Clone creation is currently disabled.", show_alert=True)
    # Claim the choice before awaiting: duplicate callbacks must not repeat a write.
    state["step"] = "registering"
    timer = state.pop("profile_timer", None)
    if timer:
        timer.cancel()
    try:
        try:
            await query.answer("⏳ Registering clone…")
        except Exception:
            pass  # An expired callback must not strand the registration state.
        await _finish_creation(client, query, state, mode)
    except asyncio.CancelledError:
        _clear_registration(user.id, state)
        log.warning("Clone registration was interrupted; its result may be uncertain.")
        raise


def _clear_registration(user_id, state):
    if _creation_state.get(user_id) is state:
        _creation_state.pop(user_id, None)
    state.get("data", {}).pop("registration_token", None)
    state.get("data", {}).pop("restore_expected_token", None)


async def _finish_creation(client, query, state, mode):
    user_id = query.from_user.id
    status_msg = query.message
    data = state["data"]
    bot_info = data["bot_info"]
    bot_id = bot_info["id"]
    bot_username = bot_info.get("username", "unknown")
    channel_id = data["channel_id"]
    channel_title = data["channel_title"]
    from filestore.worker_bot.engine import worker_engine
    try:
        # Coordinating with this process's worker lifecycle is not a distributed lease.
        async with worker_engine._operation_lock(bot_id):
            if _creation_state.get(user_id) is not state:
                _clear_registration(user_id, state)
                return await status_msg.edit_text("<b>Setup was closed.</b> No registration was attempted.")
            if mode == "group" and not config.CLONE_PROFILES_SINGLE_INSTANCE:
                _clear_registration(user_id, state)
                return await status_msg.edit_text("<b>Group mode is disabled.</b> No registration was attempted.")
            if (worker_engine.get_worker(bot_id) is not None or bot_id in worker_engine._uncertain_stops
                    or bot_id in _verification_stops):
                _clear_registration(user_id, state)
                return await status_msg.edit_text(
                    "<b>⚠️ Clone registration was not attempted.</b>\n\n"
                    "A running or uncertain worker was found in this process. Contact the operator before retrying.")
            kwargs = dict(bot_id=bot_id, owner_id=user_id, bot_token_encrypted=data["registration_token"],
                          bot_username=bot_username, log_channel_id=channel_id, managed=bool(data.get("managed")),
                          restore_deleted=bool(data.get("restore_deleted")), expected_old_token=data.get("restore_expected_token"))
            if data.get("restore_deleted"):
                kwargs["expected_old_profile"] = data.get("restore_expected_profile") if data.get("restore_profile_present") else None
            else:
                kwargs["initial_profile"] = mode
            await main_db.add_bot(**kwargs)
    except BotRegistrationConflict:
        _clear_registration(user_id, state)
        return await status_msg.edit_text(
            "<b>❌ Registration could not be completed.</b>\n\n"
            "This bot is already registered or its saved record changed. "
            "No existing record was replaced by this request. Check My Bots before trying again.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]))
    except Exception:
        _clear_registration(user_id, state)
        log.error("Clone registration could not be confirmed.")
        return await status_msg.edit_text(
            "<b>⚠️ Registration could not be confirmed.</b>\n\n"
            "The result may be uncertain. Check My Bots before retrying, or contact the operator.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]))
    _clear_registration(user_id, state)
    try:
        await main_db.set_cooldown(user_id)
    except Exception:
        log.error("Clone creation cooldown could not be recorded.")
        return await status_msg.edit_text(
            "<b>⚠️ Registration saved; this request did not attempt startup.</b>\n\n"
            "The cooldown could not be recorded. Check My Bots and contact the operator before retrying.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]))
    try:
        bot_doc = await main_db.get_bot(bot_id)
        await worker_engine.start_worker(bot_doc)
        worker_started = worker_engine.get_worker(bot_id) is not None
        try:
            from core import botlog
            user_bots = await main_db.bots.count_documents({"owner_id": user_id, "is_deleted": {"$ne": True}})
            total_bots = await main_db.bots.count_documents({"is_deleted": {"$ne": True}})
            await botlog.event("CloneCreated", (
                f"{botlog.user_block(query.from_user)}\n\n"
                f"<b>🤖 Clone bot:</b> {botlog.esc(bot_info.get('first_name', ''))} (@{botlog.esc(bot_username)})\n"
                f"<b>🆔 Bot ID:</b> <code>{bot_id}</code>\n"
                f"<b>📦 DB channel:</b> {botlog.esc(channel_title)} (<code>{channel_id}</code>)\n"
                f"<b>🧩 Mode:</b> {botlog.esc(mode)}\n"
                f"<b>📊 Clones of this user:</b> {user_bots} · <b>Platform total:</b> {total_bots}"
            ), client=client)
        except Exception:
            log.warning("Clone event logging failed.")
    except Exception:
        log.error("Clone worker start could not be confirmed.")
        worker_started = False
    icon = "🟢" if worker_started else "🟡"
    status = "Observed running in this process" if worker_started else "Not observed running in this process"
    title = "Clone restored" if data.get("restore_deleted") else "Clone registered"
    label = {"filestore": "File Store", "group": "Group Management"}[mode]
    await status_msg.edit_text(
        f"<b>✅ {title}</b>\n\n<blockquote>"
        f"<b>Bot:</b> @{html.escape(str(bot_username))}\n"
        f"<b>Channel:</b> <code>{channel_id}</code>\n"
        f"<b>Mode:</b> {label}\n<b>Status:</b> {icon} {status}\n\n"
        "The registration is saved. Use the dashboard to configure it; "
        "a saved record alone does not guarantee a running worker or confirm which token a running worker uses.</blockquote>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Bot Dashboard", callback_data=f"dashboard_{bot_id}")],
                                          [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]))


# =============================================================================
# CALLBACK: Create Bot (entry point)
# =============================================================================

@Client.on_callback_query(filters.regex(r"^create_bot$"))
async def create_bot_callback(client: Client, query: CallbackQuery):
    """Offer manual token setup and, when confirmed, Telegram creation."""
    return await _begin_creation(client, query, telegram=False)


@Client.on_callback_query(filters.regex(r"^clone_managed_start$"))
async def create_managed_callback(client: Client, query: CallbackQuery):
    """A hub shortcut; the same limits, cooldown and capability checks apply."""
    return await _begin_creation(client, query, telegram=True)


async def _begin_creation(client: Client, query: CallbackQuery, *, telegram: bool):
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    if (not user or getattr(user, "is_bot", False) or not chat
            or getattr(chat, "type", None) != enums.ChatType.PRIVATE or chat.id != user.id):
        return await query.answer("Open bot creation in your private chat.", show_alert=True)
    user_id = user.id
    old = _creation_state.get(user_id)
    if old and old.get("step") in ("starting", "registering", "verifying_token", "verifying_managed", "verifying_channel"):
        return await query.answer("Setup is already being processed. Check My Bots before starting again.", show_alert=True)
    if not CLONE_ENABLED and user_id not in OWNERS:
        return await query.answer("🚧 Clone bot creation is currently disabled.", show_alert=True)

    # Claim before database/network awaits; cancel or a new flow must not be
    # overwritten by a late response from this setup attempt.
    state = {"step": "starting", "data": {}}
    _creation_state[user_id] = state
    if old:
        timer = old.pop("profile_timer", None)
        if timer:
            timer.cancel()
        for key in ("token", "registration_token", "restore_expected_token"):
            old.get("data", {}).pop(key, None)

    async def current():
        if _creation_state.get(user_id) is state:
            return True
        await query.answer("Setup changed or was cancelled. Open /clone again.", show_alert=True)
        return False

    try:
        if old and old.get("picker"):
            await remove_channel_picker(client, user_id, old, "⌨️ Previous setup closed.")
            if not await current(): return
        bot_count = await main_db.count_user_bots(user_id)
        if not await current(): return
        from core.plans import clone_limit
        limit = await clone_limit(user_id)
        if not await current(): return
        if bot_count >= limit:
            _creation_state.pop(user_id, None)
            return await query.answer(
                f"❌ You've reached the limit of {limit} bot{'s' if limit != 1 else ''}! "
                "Upgrade with /plans (🤖 Clone Plus / 🚀 Clone Pro) for more.", show_alert=True)
        last_created = await main_db.get_cooldown(user_id)
        if not await current(): return
        if last_created:
            if last_created.tzinfo is not None:
                last_created = last_created.replace(tzinfo=None) - (last_created.utcoffset() or timedelta(0))
            elapsed = (datetime.utcnow() - last_created).total_seconds()
            if elapsed < BOT_CREATION_COOLDOWN:
                _creation_state.pop(user_id, None)
                return await query.answer(f"⏳ Please wait {int(BOT_CREATION_COOLDOWN - elapsed)}s before creating another bot.", show_alert=True)

        from filestore.main_bot.plugins import managed_bots
        enabled = await managed_bots.available()
        if not await current(): return
        state["step"] = "awaiting_token"
        if telegram and enabled:
            return await managed_bots.managed_new(client, query, capability_checked=True)
        nonce = _stage_nonce(state, "token")
        kb = [[InlineKeyboardButton("🤖 Create a bot with Telegram", callback_data=f"clone_setup_{nonce}_telegram")]] if enabled else []
        kb.extend(_creation_cancel_kb(state, "token").inline_keyboard)
        extra = ("\n\nOr choose <b>Create a bot with Telegram</b>. Telegram asks you to confirm creation; "
                 "you will still need to complete setup here.") if enabled else ""
        await query.message.edit_text(
            "<b>🤖 Connect a bot</b>\n\n"
            "<blockquote><b>Step 1/3:</b> Send your bot token in this private chat.\n\n"
            "Get a token from @BotFather. Keep it private and never share it with other users.</blockquote>" + extra,
            reply_markup=InlineKeyboardMarkup(kb))
        try:
            if telegram and not enabled:
                await query.answer("Telegram creation could not be confirmed. You can paste a token instead.", show_alert=True)
            else:
                await query.answer()
        except Exception:
            pass  # an expired callback must not invalidate the displayed token prompt
        return
    except asyncio.CancelledError:
        if _creation_state.get(user_id) is state and state.get("step") in ("starting", "awaiting_token"):
            _creation_state.pop(user_id, None)
        raise
    except Exception:
        if _creation_state.get(user_id) is state and state.get("step") in ("starting", "awaiting_token"):
            _creation_state.pop(user_id, None)
        log.warning("Clone creation setup could not be confirmed.")
        try:
            return await query.answer("Setup is temporarily unavailable. Open /clone and try again.", show_alert=True)
        except Exception:
            return


# =============================================================================
# CALLBACK: Cancel creation
# =============================================================================

@Client.on_callback_query(filters.regex(r"^clone_setup_[A-Za-z0-9_-]+_(?:cancel|telegram)$"))
async def creation_control_callback(client: Client, query: CallbackQuery):
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    if (not user or getattr(user, "is_bot", False) or not chat
            or getattr(chat, "type", None) != enums.ChatType.PRIVATE or chat.id != user.id):
        return await query.answer("Open bot creation in your private chat.", show_alert=True)
    payload = str(getattr(query, "data", ""))[len("clone_setup_"):]
    nonce, sep, action = payload.rpartition("_")
    state = _creation_state.get(user.id)
    stage = (state or {}).get("creation_control_stage")
    if (not sep or not nonce or action not in ("cancel", "telegram") or not state
            or state.get("creation_control_nonce") != nonce
            or state.get("step") not in _CREATION_STAGE_STEPS.get(stage, ())):
        return await query.answer("This setup screen has expired. Use the current screen or /clone.", show_alert=True)
    if action == "telegram":
        if stage != "token" or state.get("step") != "awaiting_token":
            return await query.answer("This setup step is already being processed.", show_alert=True)
        from filestore.main_bot.plugins import managed_bots
        return await managed_bots.managed_new(client, query)
    return await _cancel_current_creation(client, query, state)


@Client.on_callback_query(filters.regex(r"^cancel_creation$"))
async def cancel_creation_callback(client: Client, query: CallbackQuery):
    """Legacy buttons have no attempt identity; never cancel an unrelated flow."""
    return await query.answer("This old button has expired. Use /cancel in your private chat.", show_alert=True)


async def _cancel_current_creation(client: Client, query: CallbackQuery, expected_state):
    """Remove an already-authorized exact wizard before any network await."""
    user_id = query.from_user.id
    if _creation_state.get(user_id) is not expected_state or expected_state is None:
        return await query.answer("This setup has changed. Open /clone again.", show_alert=True)
    if expected_state.get("step") == "registering":
        return await query.answer("Registration is already being processed. Check My Bots before retrying.", show_alert=True)
    old = _creation_state.pop(user_id)
    if old.get("profile_timer"):
        old["profile_timer"].cancel()
    for key in ("token", "registration_token", "restore_expected_token"):
        old.get("data", {}).pop(key, None)
    if old.get("picker"):
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
    if step in ("verifying_token", "verifying_managed", "verifying_channel", "registering"):
        return await message.reply("⏳ This setup step is already being processed. Please wait.")
    if step == "awaiting_profile":
        return await message.reply("Choose a profile using the buttons above, or cancel setup.")

    # 📢 Channel shared through the native picker → treat it as a typed ID
    if getattr(message, "chat_shared", None) or getattr(message, "chats_shared", None):
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
                reply_markup=_creation_cancel_kb(state, "token"),
            )
            return

        # Validate with Telegram API
        status_msg = await message.reply("<b>⏳ Validating bot token...</b>")

        await accept_token(client, user_id, token, status_msg, expected_state=state)

    # -------------------------------------------------------------------------
    # STEP 2: Awaiting log channel ID
    # -------------------------------------------------------------------------
    elif step == "awaiting_channel":
        # Picker removal may have awaited while another input or cancellation
        # changed the state. Only the current still-pending flow can proceed.
        if _creation_state.get(user_id) is not state or state.get("step") != "awaiting_channel":
            return
        channel_input = message.text.strip()

        # Validate channel ID format
        try:
            channel_id = int(channel_input)
        except ValueError:
            await message.reply(
                "<b>❌ Invalid channel ID.</b>\n\n"
                "Channel ID should be a number like <code>-1001234567890</code>",
                reply_markup=_creation_cancel_kb(state, "channel"),
            )
            return

        bot_info = state["data"]["bot_info"]
        if bot_info["id"] in _verification_stops:
            return await message.reply("⚠️ A previous verification stop could not be confirmed. Contact the operator before retrying.")
        # Claim the step before awaiting; duplicate channel messages cannot start
        # competing verification clients in this process.
        state["step"] = "verifying_channel"
        try:
            status_msg = await message.reply("<b>⏳ Validating channel access...</b>")
        except asyncio.CancelledError:
            if _creation_state.get(user_id) is state:
                _creation_state.pop(user_id, None)
            state.get("data", {}).pop("token", None)
            state.get("data", {}).pop("restore_expected_token", None)
            raise
        except Exception:
            if _creation_state.get(user_id) is state:
                state["step"] = "awaiting_channel"
            return

        # The user may have cancelled while the status message was sent.
        if _creation_state.get(user_id) is not state or state.get("step") != "verifying_channel":
            return
        # Validate that the worker bot can access the channel
        token = state["data"]["token"]
        channel_title = str(channel_id)

        import html
        temp_client = None
        try:
            temp_client = Client(
                name=f"verify_{bot_info['id']}",
                api_id=API_ID,
                api_hash=API_HASH,
                bot_token=token,
                in_memory=True,
                no_updates=True,
            )
            await temp_client.start()

            try:
                chat = await temp_client.get_chat(channel_id)
                channel_title = chat.title or str(channel_id)
                test_msg = await temp_client.send_message(
                    chat_id=channel_id, text="✅ Channel verified for clone bot setup."
                )
                await test_msg.delete()
            except Exception:
                if _creation_state.get(user_id) is state:
                    state["step"] = "awaiting_channel"
                await status_msg.edit_text(
                    f"<b>❌ Cannot access channel!</b>\n\n"
                    f"<blockquote>Make sure:\n"
                    f"1. The channel ID is correct\n"
                    f"2. The bot (@{html.escape(str(bot_info.get('username', '')))}) is an admin in the channel\n"
                    f"3. The bot has permission to send messages.\n\n"
                    f"Telegram may also be temporarily unavailable.</blockquote>",
                    reply_markup=_creation_cancel_kb(state, "channel"),
                )
                return
        except asyncio.CancelledError:
            if _creation_state.get(user_id) is state:
                _creation_state.pop(user_id, None)
            state.get("data", {}).pop("token", None)
            state.get("data", {}).pop("restore_expected_token", None)
            raise
        except Exception:
            # Telegram/network exceptions may contain credentials or URLs.
            log.error("Clone channel verification failed.")
            if _creation_state.get(user_id) is state:
                state["step"] = "awaiting_channel"
            await status_msg.edit_text(
                "<b>❌ Verification failed!</b>\n\n"
                "<blockquote>Could not connect to Telegram. Please try again later.</blockquote>",
                reply_markup=_creation_cancel_kb(state, "channel"),
            )
            return
        finally:
            if (getattr(temp_client, "is_connected", False) is True
                    or getattr(temp_client, "is_initialized", False) is True):
                try:
                    await asyncio.wait_for(temp_client.stop(block=True), timeout=3.0)
                except asyncio.CancelledError:
                    _verification_stops[bot_info["id"]] = temp_client
                    if _creation_state.get(user_id) is state:
                        _creation_state.pop(user_id, None)
                    state.get("data", {}).pop("token", None)
                    state.get("data", {}).pop("restore_expected_token", None)
                    raise
                except Exception:
                    _verification_stops[bot_info["id"]] = temp_client
                if (getattr(temp_client, "is_connected", False) is True
                        or getattr(temp_client, "is_initialized", False) is True):
                    _verification_stops[bot_info["id"]] = temp_client
            if bot_info["id"] in _verification_stops and _creation_state.get(user_id) is state:
                _creation_state.pop(user_id, None)
                state.get("data", {}).pop("token", None)
                state.get("data", {}).pop("restore_expected_token", None)
                try:
                    await status_msg.edit_text(
                        "<b>⚠️ Verification stop could not be confirmed.</b>\n\n"
                        "No registration was attempted. Contact the operator before retrying.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]))
                except Exception:
                    log.warning("Could not display uncertain verification stop.")

        if _creation_state.get(user_id) is not state or state.get("step") != "verifying_channel":
            return  # cancelled or replaced while channel verification was in progress
        if bot_info["id"] in _verification_stops:
            _creation_state.pop(user_id, None)
            return await status_msg.edit_text(
                "<b>⚠️ Verification stop could not be confirmed.</b>\n\n"
                "No registration was attempted. Contact the operator before retrying.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_menu")]]),
            )
        try:
            # Existing credential storage supports legacy plaintext when no key
            # is configured; do not display the value in either case.
            state["data"]["registration_token"] = encrypt_token(token)
        except Exception:
            _creation_state.pop(user_id, None)
            log.error("Clone credential preparation failed.")
            return await status_msg.edit_text("<b>⚠️ Could not prepare the credential.</b> No registration was attempted.")
        state["data"].pop("token", None)
        state["data"]["channel_id"] = channel_id
        state["data"]["channel_title"] = channel_title
        await _show_creation_profiles(status_msg, user_id, state)

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

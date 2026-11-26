"""Music clone setup: collect the assistant session, then select Music.

A Music clone plays through an assistant *user* account in voice
chats (the classic VC-bot layout). When the owner picks Music for a
clone we ask for that account's Pyrogram session string, offering:

  • 📋 paste a session string they already have, or
  • 🤖 create one here — a guided phone → OTP (→ 2FA) login with the
    main bot, which exports the session string for them.

Sessions are full account credentials: they are only ever stored
Fernet-encrypted (`enc:`), never logged, and all session/login input
messages are deleted before processing. Failed deletion cancels the
flow; storage is refused without a valid ENCRYPTION_KEY. The clone
profile flips to `music` only after an owner-scoped session write and
stopped/profile CAS.
"""
import asyncio
import time
import weakref

import config
from pyrogram import Client, enums, filters
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

from core.ui import smart_edit
from filestore.database.main_db import MainDB
from filestore.fs_config import API_HASH, API_ID, LOGGER
from filestore.utils.security import encrypt_token, is_encryption_enabled

from .clone_profiles import _owned

log = LOGGER(__name__)
main_db = MainDB()

PENDING: dict[int, dict] = {}   # owner user_id -> flow state
FLOW_TTL = 600                  # seconds before a half-finished flow expires
MAX_PENDING_FLOWS = 100          # bound concurrent login clients held by this process
EXPIRED_FLOW_MARKERS: dict[int, tuple[str, float]] = {}
UNCERTAIN_LOGIN_CLIENTS: dict[int, object] = {}
_FLOW_LOCKS = weakref.WeakValueDictionary()


def _flow_lock(user_id: int) -> asyncio.Lock:
    lock = _FLOW_LOCKS.get(int(user_id))
    if lock is None:
        lock = asyncio.Lock()
        _FLOW_LOCKS[int(user_id)] = lock
    return lock


def mask_session(session: str) -> str:
    s = str(session or "")
    return f"{s[:6]}…{s[-4:]}" if len(s) > 12 else "…"


def looks_like_session(session: str) -> bool:
    s = (session or "").strip()
    return (50 <= len(s) <= 4096 and s.isascii()
            and all(33 <= ord(ch) <= 126 for ch in s))


async def _drop_login_client(state: dict) -> bool:
    state = state or {}
    client = state.get("login_client")
    if client is None:
        return True
    key = state.get("user_id")
    try:
        await asyncio.wait_for(client.disconnect(), timeout=5.0)
    except asyncio.CancelledError:
        if type(key) is int:
            UNCERTAIN_LOGIN_CLIENTS[key] = client
        raise
    except Exception:
        if type(key) is int:
            UNCERTAIN_LOGIN_CLIENTS[key] = client
        log.warning("Music login client cleanup could not be confirmed.")
        return False
    state.pop("login_client", None)
    if type(key) is int:
        UNCERTAIN_LOGIN_CLIENTS.pop(key, None)
    return True


def _mark_flow_expired(user_id: int, state: dict, *, now: float | None = None) -> None:
    now = time.time() if now is None else now
    EXPIRED_FLOW_MARKERS[int(user_id)] = (str(state.get("step") or ""), now + FLOW_TTL)
    while len(EXPIRED_FLOW_MARKERS) > MAX_PENDING_FLOWS * 4:
        oldest = min(EXPIRED_FLOW_MARKERS, key=lambda uid: EXPIRED_FLOW_MARKERS[uid][1])
        EXPIRED_FLOW_MARKERS.pop(oldest, None)


async def _state(user_id: int) -> dict | None:
    user_id = int(user_id)
    state = PENDING.get(user_id)
    if state and time.time() - state.get("ts", 0) > FLOW_TTL:
        PENDING.pop(user_id, None)
        _mark_flow_expired(user_id, state)
        await _drop_login_client(state)
        return None
    return state


async def _prune_expired_flows() -> None:
    now = time.time()
    for user_id, (step, expires_at) in list(EXPIRED_FLOW_MARKERS.items()):
        lock = _FLOW_LOCKS.get(user_id)
        if lock is not None and lock.locked():
            continue
        if expires_at <= now:
            EXPIRED_FLOW_MARKERS.pop(user_id, None)
    for user_id, state in list(PENDING.items()):
        lock = _FLOW_LOCKS.get(user_id)
        if lock is not None and lock.locked():
            continue
        if now - state.get("ts", 0) > FLOW_TTL:
            PENDING.pop(user_id, None)
            _mark_flow_expired(user_id, state, now=now)
            await _drop_login_client(state)
    for user_id, client in list(UNCERTAIN_LOGIN_CLIENTS.items()):
        lock = _FLOW_LOCKS.get(user_id)
        if lock is not None and lock.locked():
            continue
        try:
            await asyncio.wait_for(client.disconnect(), timeout=5.0)
        except asyncio.CancelledError:
            raise
        except Exception:
            continue
        UNCERTAIN_LOGIN_CLIENTS.pop(user_id, None)


async def select_music_profile(bot_id: int, owner_id: int) -> str:
    """CAS-select Music only after an assistant session exists; activate its desired state."""
    if not config.CLONE_PROFILES_SINGLE_INSTANCE:
        return "disabled"
    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != owner_id or bot.get("is_deleted"):
        return "denied"
    if not isinstance(bot.get("music_session_encrypted"), str) or not bot["music_session_encrypted"].startswith("enc:"):
        return "no_session"
    mode = bot.get("clone_profile", "filestore")
    if mode == "music" and bot.get("is_active") is True:
        return "unchanged"
    if bot.get("is_active") is not False:
        return "stop_first"
    if "clone_profile" in bot:
        if mode not in ("filestore", "group", "music"):
            return "unsupported"
        match = mode
    else:
        match = {"$exists": False}
    result = await main_db.bots.update_one(
        {"_id": bot_id, "owner_id": owner_id, "is_deleted": {"$ne": True},
         "is_active": False, "clone_profile": match},
        {"$set": {"clone_profile": "music", "is_active": True},
         "$unset": {"music_setup_pending": ""}},
    )
    return "saved" if result.matched_count else "changed"

async def save_session_and_select(bot_id: int, owner_id: int, session: str) -> str:
    """Encrypt + store the assistant session, then flip the profile."""
    if not looks_like_session(session):
        return "invalid_session"
    if not is_encryption_enabled():
        return "no_encryption"
    try:
        encrypted = encrypt_token(session.strip())
        saved = await main_db.set_music_session(bot_id, owner_id, encrypted)
    except Exception:
        log.warning("Music assistant session storage could not be confirmed.")
        return "save_uncertain"
    if not saved:
        return "denied"
    try:
        return await select_music_profile(bot_id, owner_id)
    except Exception:
        log.warning("Music profile selection could not be confirmed after session storage.")
        return "changed"


_RESULT_TEXT = {
    "saved": "✅ <b>Music mode is ready for this clone.</b>\n\nThe standalone Music worker "
             "will pick it up on its next sync (about 10 seconds) if that service is running. "
             "This controller does not run Music clones itself.",
    "unchanged": "✅ This clone is already active in Music mode; the session was updated. "
                 "The standalone Music worker will refresh it on its next sync.",
    "stop_first": "⏸ Session saved, but the clone is running. Stop it first, then pick Music again.",
    "disabled": "⚠️ Profile switching is disabled by the operator right now. The session was saved.",
    "denied": "❌ Bot not found or access denied!",
    "changed": "⚠️ The clone changed while we worked. Reopen the panel and review it.",
    "save_uncertain": "⚠️ Session storage could not be confirmed. Reopen the panel before retrying.",
    "unsupported": "⚠️ This clone's profile is unsupported. Ask the operator for help.",
    "no_session": "Set up and securely save an assistant session before selecting Music mode.",
    "invalid_session": "❌ The assistant session format was invalid; it was not saved. Start setup again.",
    "no_encryption": "🔒 <b>Can't store the session safely:</b> the operator hasn't configured a "
                     "valid ENCRYPTION_KEY, and sessions are never stored in plain text.",
}


def _prompt_markup(bot_id: int, has_saved: bool) -> Kb:
    rows = []
    if has_saved:
        rows.append([Btn("✅ Use saved session", callback_data=f"clmsess_keep_{bot_id}")])
    rows.append([Btn("📋 Paste session string", callback_data=f"clmsess_paste_{bot_id}"),
                 Btn("🤖 Create with main bot", callback_data=f"clmsess_create_{bot_id}")])
    rows.append([Btn("🔙 Profiles", callback_data=f"clprofile_{bot_id}")])
    return Kb(rows)


@Client.on_callback_query(filters.regex(r"^clmusicstate_(\d+)_(on|off)$"))
async def music_runtime_toggle(client, query):
    """Set desired Music state; the standalone Music worker reconciles it."""
    bot_id = int(query.matches[0].group(1))
    desired = query.matches[0].group(2) == "on"
    bot = await _owned(query, bot_id)
    if not bot:
        return
    if bot.get("clone_profile") != "music":
        return await query.answer("This clone is no longer in Music mode. Reopen its dashboard.", show_alert=True)
    if desired and (not isinstance(bot.get("music_session_encrypted"), str)
                    or not bot["music_session_encrypted"].startswith("enc:")):
        return await query.answer("Save an encrypted assistant session before starting Music.", show_alert=True)
    try:
        await main_db.set_profile_active(bot_id, query.from_user.id, "music", desired)
    except Exception:
        return await query.answer("Couldn't save the Music state. Reopen the dashboard before retrying.",
                                  show_alert=True)
    current = await main_db.get_bot(bot_id)
    if not current or current.get("clone_profile") != "music":
        return await query.answer("The clone profile changed. Reopen its dashboard.", show_alert=True)
    from filestore.main_bot.plugins.my_bots import _mode_dashboard
    await _mode_dashboard(query, current, "music")


@Client.on_callback_query(filters.regex(r"^clmusic_(\d+)$"))
async def music_prompt(client, query):
    bot_id = int(query.matches[0].group(1))
    bot = await _owned(query, bot_id)
    if not bot:
        return
    has_saved = bool(bot.get("music_session_encrypted"))
    saved_line = ""
    if has_saved:
        from filestore.utils.security import decrypt_token
        try:
            shown = mask_session(decrypt_token(bot["music_session_encrypted"]))
        except Exception:
            shown = "saved (unreadable — replace it)"
        saved_line = f"Saved session: <code>{shown}</code> — reuse or replace it.\n\n"
    text = (
        "<b>🎵 Music mode needs an assistant account</b>\n\n"
        "Music clones play through an assistant <b>user account</b> in voice chats "
        "(the bot itself only takes commands). I need that account's session string.\n\n"
        + saved_line
        + "<b>📋 Paste</b> — send a Pyrogram session string you already have.\n"
          "<b>🤖 Create with main bot</b> — log in here with phone + OTP and I'll make one.\n\n"
          "<i>🔒 Sessions are stored encrypted, never shared, and give full access to "
          "the assistant account — use a spare account you trust for this.</i>"
    )
    await smart_edit(query.message, text, _prompt_markup(bot_id, has_saved))
    await query.answer()


async def _music_session_action_impl(client, query):
    action, bot_id = query.matches[0].group(1), int(query.matches[0].group(2))
    bot = await _owned(query, bot_id)
    if not bot:
        return
    if action not in ("paste", "create", "keep"):
        return await query.answer("Unknown action.", show_alert=True)
    await _prune_expired_flows()
    user_id = query.from_user.id
    old = PENDING.pop(user_id, None)
    if old:
        await _drop_login_client(old)
    if user_id in UNCERTAIN_LOGIN_CLIENTS:
        return await query.answer("Previous assistant login cleanup is uncertain. Try again after reconnecting the bot.",
                                  show_alert=True)
    if action == "keep":
        if not bot.get("music_session_encrypted"):
            return await query.answer("No saved session yet.", show_alert=True)
        result = await select_music_profile(bot_id, user_id)
        await smart_edit(query.message, _RESULT_TEXT.get(result, _RESULT_TEXT["changed"]),
                         Kb([[Btn("🔙 Profiles", callback_data=f"clprofile_{bot_id}")]]))
        return await query.answer()
    if len(PENDING) + len(UNCERTAIN_LOGIN_CLIENTS) >= MAX_PENDING_FLOWS:
        return await query.answer("Too many assistant setup flows are open right now. Try again shortly.",
                                  show_alert=True)
    EXPIRED_FLOW_MARKERS.pop(user_id, None)
    if action == "paste":
        PENDING[user_id] = {"user_id": user_id, "bot_id": bot_id,
                            "step": "paste", "ts": time.time()}
        text = ("📋 <b>Send the session string now</b> as your next message.\n\n"
                "<i>I'll delete that message right away and store the session encrypted. "
                "Send /cancel to abort.</i>")
    else:
        PENDING[user_id] = {"user_id": user_id, "bot_id": bot_id,
                            "step": "phone", "ts": time.time()}
        text = ("🤖 <b>Let's create a session.</b>\n\n"
                "Send the assistant account's <b>phone number</b> with country code "
                "(e.g. <code>+919876543210</code>). Telegram will send a login code.\n\n"
                "<i>Use a spare account — it will join voice chats to play music. "
                "Send /cancel to abort.</i>")
    await smart_edit(query.message, text,
                     Kb([[Btn("❌ Cancel", callback_data=f"clmusic_{bot_id}")]]))
    await query.answer()


@Client.on_callback_query(filters.regex(r"^clmsess_(\w+)_(\d+)$"))
async def music_session_action(client, query):
    user = getattr(query, "from_user", None)
    user_id = getattr(user, "id", None)
    if type(user_id) is not int:
        return await _music_session_action_impl(client, query)
    async with _flow_lock(user_id):
        return await _music_session_action_impl(client, query)


def _make_login_client():
    return Client("music_session_login", api_id=API_ID, api_hash=API_HASH, in_memory=True)


async def _finish_flow(client, message, state: dict, session: str) -> None:
    user_id = message.from_user.id
    PENDING.pop(user_id, None)
    _mark_flow_expired(user_id, state)
    if not await _drop_login_client(state):
        return await message.reply_text(
            "⚠️ Login cleanup could not be confirmed, so the assistant session was not saved. "
            "Retry after reconnecting the main bot.")
    result = await save_session_and_select(state["bot_id"], user_id, session)
    await message.reply_text(_RESULT_TEXT.get(result, _RESULT_TEXT["changed"]))


async def _music_session_message_impl(client, message):
    user = getattr(message, "from_user", None)
    if not user or getattr(user, "is_bot", False):
        return
    state = await _state(user.id)
    if state is None:
        marker = EXPIRED_FLOW_MARKERS.get(user.id)
        if marker and marker[1] > time.time():
            expired_step = marker[0]
            text = (message.text or "").strip()
            looks_sensitive = (
                (expired_step == "paste" and looks_like_session(text))
                or (expired_step == "phone" and text.startswith("+") and text[1:].replace(" ", "").isdigit())
                or (expired_step == "code" and text.replace(" ", "").isdigit()
                    and 4 <= len(text.replace(" ", "")) <= 10)
                or (expired_step == "password" and bool(text) and not text.startswith("/"))
            )
            if looks_sensitive:
                try:
                    await message.delete()
                except Exception:
                    return await message.reply_text(
                        "🔒 This setup expired and the credential could not be deleted. "
                        "Please remove it manually; it was not processed or saved.")
                EXPIRED_FLOW_MARKERS.pop(user.id, None)
                return await message.reply_text(
                    "⏰ No assistant setup is active. That credential was deleted and not processed; start setup again if needed.")
        return  # not our flow — let other handlers have the message
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "cancel"):
        PENDING.pop(user.id, None)
        _mark_flow_expired(user.id, state)
        cleaned = await _drop_login_client(state)
        return await message.reply_text(
            "❌ Music setup cancelled." if cleaned else
            "⚠️ Music setup cancelled, but login-client cleanup could not be confirmed. Reconnect the main bot before retrying.")
    state["ts"] = time.time()
    step = state.get("step")
    if step in ("paste", "phone", "code", "password"):
        try:
            await message.delete()
        except Exception:
            # Never process or store credentials that remain visible in chat.
            PENDING.pop(user.id, None)
            cleaned = await _drop_login_client(state)
            cleanup_note = (" Login-client cleanup also needs operator review." if not cleaned else "")
            return await message.reply_text(
                "🔒 Couldn't securely remove that setup message, so the login was cancelled. "
                "Delete it manually before starting again." + cleanup_note)

    if step == "paste":
        if not looks_like_session(text):
            return await message.reply_text(
                "🤔 That doesn't look like a session string (too short or has spaces). "
                "Send the full string, or /cancel.")
        return await _finish_flow(client, message, state, text)

    if step == "phone":
        phone = text.replace(" ", "")
        if not phone.startswith("+") or not phone[1:].isdigit() or len(phone) < 8:
            return await message.reply_text("📱 Send the number with country code, e.g. +919876543210.")
        try:
            login = _make_login_client()
            await login.connect()
            sent = await login.send_code(phone)
        except Exception as exc:
            log.warning("music session login start failed: %s", type(exc).__name__)
            return await message.reply_text("❌ Couldn't send the code — check the number and try again, or /cancel.")
        state.update({"login_client": login, "phone": phone,
                      "phone_code_hash": sent.phone_code_hash, "step": "code"})
        return await message.reply_text("📩 <b>Code sent!</b> Send the login code you received (digits only).")

    if step in ("code", "password"):
        login = state.get("login_client")
        if login is None:
            PENDING.pop(user.id, None)
            return await message.reply_text("⏰ Login expired — start again from the Music button.")
        try:
            if step == "code":
                try:
                    await login.sign_in(state["phone"], state["phone_code_hash"], text.replace(" ", ""))
                except Exception as exc:
                    if exc.__class__.__name__ == "SessionPasswordNeeded":
                        state["step"] = "password"
                        return await message.reply_text("🔐 This account has 2FA. Send your Telegram password.")
                    raise
            else:
                await login.check_password(text)
            session = await login.export_session_string()
        except Exception as exc:
            name = exc.__class__.__name__
            friendly = {
                "PhoneCodeInvalid": "❌ That code doesn't match. Try again, or /cancel.",
                "PhoneCodeExpired": "⏰ Code expired. /cancel and start again.",
                "PasswordHashInvalid": "❌ Wrong password. Try again, or /cancel.",
                "FloodWait": "⏳ Telegram asks us to wait a bit. /cancel and retry later.",
            }.get(name, "❌ Login failed. Try again, or /cancel.")
            return await message.reply_text(friendly)
        return await _finish_flow(client, message, state, session)


@Client.on_message(filters.private & filters.text)
async def music_session_message(client, message):
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    if not user or getattr(user, "is_bot", False) or type(user_id) is not int:
        return
    async with _flow_lock(user_id):
        return await _music_session_message_impl(client, message)

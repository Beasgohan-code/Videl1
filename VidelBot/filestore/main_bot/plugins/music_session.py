"""Music clone setup: collect the assistant session, then select Music.

A Music clone plays through an assistant *user* account in voice
chats (the classic VC-bot layout). When the owner picks Music for a
clone we ask for that account's Pyrogram session string, offering:

  • 📋 paste a session string they already have, or
  • 🤖 create one here — a guided phone → OTP (→ 2FA) login with the
    main bot, which exports the session string for them.

Sessions are full account credentials: they are only ever stored
Fernet-encrypted (`enc:`), never logged, the owner's pasted message
is deleted, and storage is refused when no valid ENCRYPTION_KEY is
configured. The clone profile flips to `music` only after a session
is saved (same stopped/owner CAS rules as other profile switches).
"""
import time

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


def mask_session(session: str) -> str:
    s = str(session or "")
    return f"{s[:6]}…{s[-4:]}" if len(s) > 12 else "…"


def looks_like_session(session: str) -> bool:
    s = (session or "").strip()
    return len(s) >= 50 and not any(ch.isspace() for ch in s)


def _state(user_id: int) -> dict | None:
    state = PENDING.get(int(user_id))
    if state and time.time() - state.get("ts", 0) > FLOW_TTL:
        PENDING.pop(int(user_id), None)
        return None
    return state


async def _drop_login_client(state: dict) -> None:
    client = (state or {}).pop("login_client", None)
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            pass


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
    if not is_encryption_enabled():
        return "no_encryption"
    await main_db.set_music_session(bot_id, encrypt_token(session.strip()))
    return await select_music_profile(bot_id, owner_id)


_RESULT_TEXT = {
    "saved": "✅ <b>Music mode is ready for this clone.</b>\n\nRestart the standalone "
             "music program so it loads this clone; it will join voice chats as the assistant account.",
    "unchanged": "✅ This clone is already active in Music mode; the session was updated. "
                 "Restart the standalone music program if it is already running.",
    "stop_first": "⏸ Session saved, but the clone is running. Stop it first, then pick Music again.",
    "disabled": "⚠️ Profile switching is disabled by the operator right now. The session was saved.",
    "denied": "❌ Bot not found or access denied!",
    "changed": "⚠️ The clone changed while we worked. Reopen the panel and review it.",
    "unsupported": "⚠️ This clone's profile is unsupported. Ask the operator for help.",
    "no_session": "Set up and securely save an assistant session before selecting Music mode.",
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


@Client.on_callback_query(filters.regex(r"^clmsess_(\w+)_(\d+)$"))
async def music_session_action(client, query):
    action, bot_id = query.matches[0].group(1), int(query.matches[0].group(2))
    bot = await _owned(query, bot_id)
    if not bot:
        return
    if action not in ("paste", "create", "keep"):
        return await query.answer("Unknown action.", show_alert=True)
    user_id = query.from_user.id
    if action == "keep":
        if not bot.get("music_session_encrypted"):
            return await query.answer("No saved session yet.", show_alert=True)
        result = await select_music_profile(bot_id, user_id)
        await smart_edit(query.message, _RESULT_TEXT.get(result, _RESULT_TEXT["changed"]),
                         Kb([[Btn("🔙 Profiles", callback_data=f"clprofile_{bot_id}")]]))
        return await query.answer()
    old = PENDING.pop(user_id, None)
    if old:
        await _drop_login_client(old)
    if action == "paste":
        PENDING[user_id] = {"bot_id": bot_id, "step": "paste", "ts": time.time()}
        text = ("📋 <b>Send the session string now</b> as your next message.\n\n"
                "<i>I'll delete that message right away and store the session encrypted. "
                "Send /cancel to abort.</i>")
    else:
        PENDING[user_id] = {"bot_id": bot_id, "step": "phone", "ts": time.time()}
        text = ("🤖 <b>Let's create a session.</b>\n\n"
                "Send the assistant account's <b>phone number</b> with country code "
                "(e.g. <code>+919876543210</code>). Telegram will send a login code.\n\n"
                "<i>Use a spare account — it will join voice chats to play music. "
                "Send /cancel to abort.</i>")
    await smart_edit(query.message, text,
                     Kb([[Btn("❌ Cancel", callback_data=f"clmusic_{bot_id}")]]))
    await query.answer()


def _make_login_client():
    return Client("music_session_login", api_id=API_ID, api_hash=API_HASH, in_memory=True)


async def _finish_flow(client, message, state: dict, session: str) -> None:
    user_id = message.from_user.id
    PENDING.pop(user_id, None)
    await _drop_login_client(state)
    result = await save_session_and_select(state["bot_id"], user_id, session)
    await message.reply_text(_RESULT_TEXT.get(result, _RESULT_TEXT["changed"]))


@Client.on_message(filters.private & filters.text)
async def music_session_message(client, message):
    user = getattr(message, "from_user", None)
    if not user or getattr(user, "is_bot", False):
        return
    state = _state(user.id)
    if state is None:
        return  # not our flow — let other handlers have the message
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "cancel"):
        PENDING.pop(user.id, None)
        await _drop_login_client(state)
        return await message.reply_text("❌ Music setup cancelled.")
    state["ts"] = time.time()
    step = state.get("step")

    if step == "paste":
        try:
            await message.delete()
        except Exception:
            pass
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

"""
One-tap clone creation with Telegram *managed bots* (Bot API 9.6+, via aiogram).

When Videl's owner enables **Bot Management Mode** for Videl in @BotFather,
users no longer need to visit BotFather and copy a token:

  1. "⚡ One-tap create" → Videl builds a deep link
     ``https://t.me/newbot/<Videl>/<suggested_username>?name=<name>``;
     Telegram opens a pre-filled "create bot" sheet, the user confirms.
  2. Videl watches for the new bot's (secret, random) username, then asks the
     Bot API for its token (``getManagedBotToken``) and continues the normal
     wizard (log channel step) – the bot is owned by the user, Videl only
     manages it.
  3. If the user changed the username, they send ``@their_bot`` and prove it is
     theirs by pressing Start in it (checked through that bot's own getUpdates).

Managed clone token rotation requires a private, expiring confirmation, a valid
original encryption key and an operator-verified single current worker. It does
not automatically restart a worker. Without Bot Management Mode, the setup
wizard falls back to the classic "paste your token" flow.
"""
import html
import asyncio
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import quote

from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
from config import MANAGED_BOTS, MANAGED_PAIR_TIMEOUT
from core import botapi
from filestore.database.main_db import MainDB
from filestore.fs_config import LOGGER

log = LOGGER(__name__)
main_db = MainDB()
_tasks: set = set()          # keep watcher tasks referenced
POLL_START, POLL_MAX = 4, 10  # seconds between username checks


async def available() -> bool:
    """Require a fresh, matching getMe response; never reuse a stale positive."""
    if not MANAGED_BOTS or not botapi.enabled():
        return False
    try:
        bridge = botapi.bot()
        if bridge is None:
            return False
        me = await asyncio.wait_for(bridge.get_me(), timeout=5)
        bot_id = str(config.BOT_TOKEN or "").split(":", 1)[0]
        return (botapi.enabled() and bot_id.isdigit() and getattr(me, "id", None) == int(bot_id)
                and getattr(me, "is_bot", None) is True and getattr(me, "can_manage_bots", None) is True)
    except Exception:
        # A transient failure is not evidence that the capability is enabled.
        return False


def suggested_username(first_name: str) -> str:
    """Random, hard-to-guess username: ``<name>_<6 hex>_bot`` (5–32 chars)."""
    base = re.sub(r"[^a-z0-9]", "", (first_name or "").lower())[:12] or "videl"
    if not base[0].isalpha():
        base = "v" + base[:11]
    return f"{base}_{secrets.token_hex(3)}_bot"


def newbot_link(manager: str, username: str, name: str) -> str:
    return f"https://t.me/newbot/{manager}/{username}?name={quote(name[:64])}"


def _spawn(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)
    return t


def _pending(user_id: int, username: str) -> bool:
    from filestore.main_bot.plugins.create_bot import _creation_state
    st = _creation_state.get(user_id)
    return bool(st and st.get("step") == "awaiting_token"
                and st.get("data", {}).get("managed_username") == username)


async def _resolve_bot(client, username: str):
    """pyrofork lookup of a bot by username (None when not found / not a bot)."""
    try:
        u = await client.get_users(username)
    except FloodWait as e:
        await asyncio.sleep(min(int(getattr(e, "value", 5) or 5), 30))
        return None
    except Exception:
        return None
    if isinstance(u, list):
        u = u[0] if u else None
    return u if (u and getattr(u, "is_bot", False)) else None


async def _token_for(bot_id: int, client=None):
    if botapi.enabled():
        return await botapi.try_call(lambda b: b.get_managed_bot_token(user_id=bot_id))
    from core.ui import is_wzgram_client
    if is_wzgram_client(client) and hasattr(client, "get_managed_bot_token"):
        try:
            return await client.get_managed_bot_token(bot_id)
        except Exception:
            log.debug("Managed bot token retrieval failed.")
    return None


def _back_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Paste a token instead", callback_data="create_bot")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
    ])


# ─────────────────────────── one-tap flow ───────────────────────────
@Client.on_callback_query(filters.regex(r"^managed_new$"))
async def managed_new(client: Client, query: CallbackQuery, *, capability_checked=False):
    from filestore.main_bot.plugins.create_bot import _creation_state
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    if (not user or getattr(user, "is_bot", False) or not chat
            or getattr(chat, "type", None) != enums.ChatType.PRIVATE or chat.id != user.id):
        return await query.answer("Open bot creation in your private chat.", show_alert=True)
    st = _creation_state.get(user.id)
    if not st or st.get("step") != "awaiting_token":
        return await query.answer("Open /clone and start setup again.", show_alert=True)
    if st.get("managed_checking"):
        return await query.answer("Telegram creation is already being checked.", show_alert=True)
    st["managed_checking"] = True
    try:
        return await _open_managed_creation(client, query, user, st, capability_checked)
    finally:
        st.pop("managed_checking", None)


async def _open_managed_creation(client, query, user, st, capability_checked):
    from filestore.main_bot.plugins.create_bot import _creation_state
    if not capability_checked and not await available():
        return await query.answer("Telegram creation is unavailable right now. Paste a bot token instead.",
                                  show_alert=True)
    if _creation_state.get(user.id) is not st or st.get("step") != "awaiting_token":
        return await query.answer("Setup changed. Open /clone again.", show_alert=True)
    me = client.me or await client.get_me()
    if _creation_state.get(user.id) is not st or st.get("step") != "awaiting_token":
        return await query.answer("Setup changed. Open /clone again.", show_alert=True)
    expected_id = str(config.BOT_TOKEN or "").split(":", 1)[0]
    if (not expected_id.isdigit() or getattr(me, "id", None) != int(expected_id)
            or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", str(getattr(me, "username", "") or ""))):
        return await query.answer("The manager bot identity is unavailable. Paste a bot token instead.", show_alert=True)
    username = suggested_username(user.first_name)
    name = f"{(user.first_name or 'My')[:40]}'s Bot"
    st["data"]["managed_username"] = username
    st["data"]["managed_status_msg"] = query.message
    link = newbot_link(me.username, username, name)
    await query.message.edit_text(
        "<b>🤖 Create a bot with Telegram</b>\n\n"
        "<blockquote>1️⃣ Tap <b>Create my bot</b> below.\n"
        "2️⃣ Telegram opens a ready-made form – just press <b>Create</b>.\n"
        "3️⃣ Return here while Videl checks and connects it. Further setup is required.</blockquote>\n\n"
        f"Suggested username: <code>@{username}</code>\n"
        "<i>Changed the username? Send it here like <code>@my_new_bot</code>.</i>\n"
        f"<i>⌛ Waiting up to {MANAGED_PAIR_TIMEOUT // 60} min…</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 Create my bot", url=link)],
            [InlineKeyboardButton("🔑 Paste a token instead", callback_data="create_bot")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
        ]),
        disable_web_page_preview=True,
    )
    if (_creation_state.get(user.id) is not st or st.get("step") != "awaiting_token"
            or st.get("data", {}).get("managed_username") != username):
        return await query.answer("Setup changed. Open /clone again.", show_alert=True)
    _spawn(watch_username(client, user.id, username, query.message))
    try:
        await query.answer()
    except Exception:
        pass  # an expired callback must not prevent the watcher from starting


async def watch_username(client, user_id: int, username: str, status_msg, timeout: int = None):
    """Poll until the suggested bot exists, then fetch its token and continue the wizard."""
    from filestore.main_bot.plugins.create_bot import accept_token, _creation_state
    state = _creation_state.get(user_id)
    if state is None:
        return False
    deadline = time.time() + (timeout or MANAGED_PAIR_TIMEOUT)
    delay = POLL_START
    while time.time() < deadline:
        await asyncio.sleep(delay)
        delay = min(delay + 1, POLL_MAX)
        if _creation_state.get(user_id) is not state or not _pending(user_id, username):
            return False  # cancelled / token pasted / new attempt
        bot_user = await _resolve_bot(client, username)
        if not bot_user:
            continue
        token = await _token_for(bot_user.id, client=client)
        if _creation_state.get(user_id) is not state or not _pending(user_id, username):
            return False
        if not token:
            await _safe_edit(status_msg, f"<b>⚠️ @{username} exists but Videl can't manage it.</b>\n\n"
                                         "Paste its token from @BotFather instead.", _back_kb())
            return False
        await _safe_edit(status_msg, f"<b>⏳ Connecting @{username}…</b>")
        if _creation_state.get(user_id) is not state or not _pending(user_id, username):
            return False
        return await accept_token(client, user_id, token, status_msg, managed=True, expected_state=state)
    if _creation_state.get(user_id) is state and _pending(user_id, username):
        await _safe_edit(status_msg, "<b>⌛ Timed out waiting for the new bot.</b>\n\n"
                                     "Tap ⚡ Create Bot to try again, or paste a token.", _back_kb())
    return False


async def _safe_edit(msg, text, kb=None):
    try:
        await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass


# ─────────────────────────── renamed bot: claim with proof ───────────────────────────
async def claim_username(client, message, user_id: int, text: str) -> bool:
    """User sent ``@name`` while the one-tap flow was waiting. Returns True when handled."""
    from filestore.main_bot.plugins.create_bot import _creation_state
    st = _creation_state.get(user_id) or {}
    if not st.get("data", {}).get("managed_username") or not await available():
        return False
    username = text.strip().lstrip("@").split()[0]
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", username):
        await message.reply("<b>❌ That doesn't look like a bot username.</b>")
        return True
    status = await message.reply(f"<b>🔎 Looking up @{username}…</b>")
    bot_user = await _resolve_bot(client, username)
    token = await _token_for(bot_user.id, client=client) if bot_user else None
    if not token:
        await _safe_edit(status, f"<b>❌ @{username} wasn't created through Videl.</b>\n\n"
                                 "Use the one-tap button, or paste the bot token.", _back_kb())
        return True
    code = secrets.token_hex(4)
    if _creation_state.get(user_id) is not st or st.get("step") != "awaiting_token":
        return True
    st["data"]["managed_username"] = username   # stop the old watcher
    await _safe_edit(status, (
        f"<b>🔐 Prove @{username} is yours</b>\n\n"
        "<blockquote>Tap the button, press <b>Start</b> in your new bot, then come back.</blockquote>"),
        InlineKeyboardMarkup([
            [InlineKeyboardButton(f"▶️ Start @{username}", url=f"https://t.me/{username}?start=vd{code}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
        ]))
    _spawn(_await_proof(client, user_id, username, token, code, status, expected_state=st))
    return True


async def _await_proof(client, user_id, username, token, code, status, timeout: int = 180, *, expected_state=None):
    """Read the new bot's own updates (it isn't running anywhere yet) for ``/start vd<code>``."""
    from filestore.main_bot.plugins.create_bot import accept_token, _creation_state
    if expected_state is None:
        expected_state = _creation_state.get(user_id)
    if expected_state is None:
        return False
    wb = botapi.worker(token)
    offset, deadline = None, time.time() + timeout
    try:
        while time.time() < deadline and _creation_state.get(user_id) is expected_state and _pending(user_id, username):
            updates = await wb.get_updates(offset=offset, timeout=10, allowed_updates=["message"],
                                           request_timeout=25)
            if _creation_state.get(user_id) is not expected_state or not _pending(user_id, username):
                return False
            for u in updates or []:
                offset = u.update_id + 1
                m = u.message
                if m and m.from_user and m.from_user.id == user_id and (m.text or "").strip() == f"/start vd{code}":
                    if offset is not None:  # acknowledge so the worker doesn't see it again
                        await wb.get_updates(offset=offset, timeout=0, request_timeout=15)
                    await _safe_edit(status, f"<b>⏳ Connecting @{username}…</b>")
                    if _creation_state.get(user_id) is not expected_state or not _pending(user_id, username):
                        return False
                    return await accept_token(client, user_id, token, status, managed=True, expected_state=expected_state)
            if not updates:
                await asyncio.sleep(1)
    except Exception:
        log.warning("Managed bot ownership check failed.")
    if _creation_state.get(user_id) is expected_state and _pending(user_id, username):
        await _safe_edit(status, "<b>⌛ Verification timed out.</b>\n\nSend the @username again or paste the token.",
                         _back_kb())
    return False


# ─────────────────────────── WzGram Layer 232 push hook ───────────────────────────
@Client.on_managed_bot()
async def on_managed_bot_updated(client: Client, update):
    """Instant MTProto UpdateManagedBot hook — connects newly created managed bots with zero polling."""
    from filestore.main_bot.plugins.create_bot import _creation_state, accept_token
    user = getattr(update, "user", None)
    bot_user = getattr(update, "bot", None)
    if not (user and bot_user):
        return
    st = _creation_state.get(user.id)
    if not st or st.get("step") != "awaiting_token" or not st.get("data", {}).get("managed_username"):
        return
    expected_username = st["data"]["managed_username"]
    token = await _token_for(bot_user.id, client=client)
    if (not token or _creation_state.get(user.id) is not st or st.get("step") != "awaiting_token"
            or st.get("data", {}).get("managed_username") != expected_username):
        return
    # Clear managed_username so any background poll exits cleanly
    st["data"].pop("managed_username", None)
    status_msg = st["data"].pop("managed_status_msg", None)
    if status_msg is None:
        try:
            status_msg = await client.send_message(user.id, f"<b>⏳ Connecting @{bot_user.username or 'bot'}…</b>")
        except Exception:
            return
    else:
        await _safe_edit(status_msg, f"<b>⏳ Connecting @{bot_user.username or 'bot'}…</b>")
    await accept_token(client, user.id, token, status_msg, managed=True, expected_state=st)


# ─────────────────────────── token rotation ───────────────────────────
def rotate_row(bot: dict):
    """Dashboard row for managed clones (empty list otherwise)."""
    if bot.get("managed") and MANAGED_BOTS and botapi.enabled():
        return [InlineKeyboardButton("🔁 ʀᴏᴛᴀᴛᴇ ᴛᴏᴋᴇɴ", callback_data=f"rotate_tok_{bot['_id']}")]
    return []


# Rotation is irreversible at Telegram. Confirmations and the fast in-process
# fence are local; the Mongo reservation persists across restarts but does not
# coordinate old versions or unrelated lifecycle/ownership writers.
_rotation_pending = {}
_rotation_in_flight = set()
_rotation_uncertain = set()
_ROTATION_TTL = 300
_ROTATION_PENDING_LIMIT = 512
_ROTATION_MARKER = "managed_rotation_reservation"


def _rotation_collection():
    # Fail closed if the server cannot confirm majority+journalled writes.
    # No TTL: automatic expiry could allow a second irreversible request.
    from pymongo.write_concern import WriteConcern
    return main_db.bots.with_options(write_concern=WriteConcern(w="majority", j=True, wtimeout=10000))


def _valid_saved_token(value, cipher, bot_id):
    """Accept encrypted or legacy plaintext credentials only for the same bot."""
    try:
        token = cipher.decrypt(value[4:].encode()).decode() if value.startswith("enc:") else value
        return token.startswith(f"{bot_id}:") and len(token) > len(str(bot_id)) + 1
    except Exception:
        return False


@Client.on_callback_query(filters.regex(r"^rotate_tok_(?:do_)?\d+(?:_[A-Za-z0-9_-]{1,32})?$"))
async def rotate_token(client: Client, query: CallbackQuery):
    from filestore.utils import security
    from filestore.worker_bot.engine import worker_engine
    import config
    from pymongo.collation import Collation

    match = re.fullmatch(r"rotate_tok_(?:do_(\d+)(?:_([A-Za-z0-9_-]{11}))?|(\d+))", query.data or "")
    if not match:
        return await query.answer("This confirmation is invalid.", show_alert=True)
    bot_id = int(match.group(1) or match.group(3))
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    if (user is None or getattr(user, "is_bot", False) or chat is None
            or getattr(chat, "type", None) != enums.ChatType.PRIVATE or getattr(chat, "id", None) != user.id):
        return await query.answer("❌ Access denied!", show_alert=True)
    try:
        bot = await main_db.get_bot(bot_id)
    except Exception:
        return await query.answer("Could not verify this clone. Try again later.", show_alert=True)
    if (not bot or bot.get("owner_id") != user.id or bot.get("is_deleted")
            or bot.get("managed") is not True):
        return await query.answer("❌ Access denied!", show_alert=True)
    back = [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")]
    if not config.MANAGED_ROTATION_ENABLED:
        return await query.answer("Token rotation is disabled. Contact the operator.", show_alert=True)
    if not config.CLONE_PROFILES_SINGLE_INSTANCE:
        return await query.answer("Rotation requires an operator-verified single current worker. Contact the operator.", show_alert=True)
    if bot_id in _rotation_in_flight:
        return await query.answer("Rotation is already being processed. Do not retry.", show_alert=True)
    if bot_id in _rotation_uncertain or _ROTATION_MARKER in bot:
        return await query.answer("A rotation needs operator review. Do not retry.", show_alert=True)
    if match.group(3):
        old = bot.get("bot_token_encrypted")
        cipher = security._get_fernet()
        if not cipher or not isinstance(old, str) or not old:
            return await query.answer("A valid original encryption key and saved credential are required. Contact the operator.", show_alert=True)
        if not _valid_saved_token(old, cipher, bot_id):
            return await query.answer("The saved credential cannot be decrypted or does not match this bot. Contact the operator; do not replace the key.", show_alert=True)
        now = time.monotonic()
        for key, pending in list(_rotation_pending.items()):
            if pending["deadline"] <= now:
                _rotation_pending.pop(key, None)
        if len(_rotation_pending) >= _ROTATION_PENDING_LIMIT and bot_id not in _rotation_pending:
            return await query.answer("Too many pending confirmations. Try later.", show_alert=True)
        nonce = secrets.token_urlsafe(8)
        _rotation_pending[bot_id] = {"owner": user.id, "nonce": nonce, "old": old,
                                     "deadline": now + _ROTATION_TTL}
        name = html.escape(str(bot.get("bot_username") or "bot"))
        await query.message.edit_text(
            f"<b>🔁 Rotate @{name}?</b>\n\n"
            "<blockquote>Telegram invalidates the old token immediately. If a response or save is uncertain, "
            "contact the operator before retrying. The clone may need a manual stop and restart.</blockquote>\n"
            "<i>This confirmation expires in five minutes.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Rotate now", callback_data=f"rotate_tok_do_{bot_id}_{nonce}")], back]))
        return await query.answer()

    pending = _rotation_pending.get(bot_id)
    if (not pending or not match.group(2) or pending["nonce"] != match.group(2)
            or pending["owner"] != user.id or pending["deadline"] <= time.monotonic()):
        return await query.answer("Confirmation expired. Open the dashboard and review again.", show_alert=True)
    # Claim before the first await, so a double-click cannot rotate twice.
    _rotation_pending.pop(bot_id, None)
    _rotation_in_flight.add(bot_id)
    attempted = False
    claim_started = False
    try:
        try:
            await query.answer("🔁 Checking rotation…")
        except Exception:
            pass
        async with worker_engine._operation_lock(bot_id):
            current = await main_db.get_bot(bot_id)
            cipher = security._get_fernet()
            if (not config.MANAGED_ROTATION_ENABLED or not config.CLONE_PROFILES_SINGLE_INSTANCE or not current
                    or current.get("owner_id") != user.id or current.get("is_deleted")
                    or current.get("managed") is not True or current.get("bot_token_encrypted") != pending["old"]
                    or _ROTATION_MARKER in current or bot_id in worker_engine._uncertain_stops or not cipher):
                return await query.message.edit_text(
                    "<b>⚠️ Rotation was not attempted.</b> The clone, key or worker state changed. Review the dashboard or contact the operator.",
                    reply_markup=InlineKeyboardMarkup([back]))
            if not _valid_saved_token(pending["old"], cipher, bot_id):
                return await query.message.edit_text(
                    "<b>⚠️ Rotation was not attempted.</b> The saved credential cannot be decrypted or does not match this bot. Contact the operator.",
                    reply_markup=InlineKeyboardMarkup([back]))
            if not botapi.enabled() or (b := botapi.bot()) is None:
                return await query.message.edit_text(
                    "<b>⚠️ Rotation was not attempted.</b> Bot API is unavailable.",
                    reply_markup=InlineKeyboardMarkup([back]))

            collection = _rotation_collection()
            operation_id = secrets.token_hex(16)
            expected = {"_id": bot_id, "owner_id": user.id, "is_deleted": {"$ne": True},
                        "managed": True, "bot_token_encrypted": pending["old"]}
            # Set before Telegram, atomically and only if no prior reservation
            # exists. An ambiguous claim must never be automatically removed.
            claim_started = True
            claim = await collection.update_one(
                {**expected, _ROTATION_MARKER: {"$exists": False}},
                {"$set": {_ROTATION_MARKER: {"operation_id": operation_id,
                                               "started_at": datetime.now(timezone.utc)}}},
                collation=Collation("simple"))
            if not claim.acknowledged:
                raise RuntimeError("rotation reservation not acknowledged")
            if claim.matched_count != 1:
                claim_started = False  # no write was made by this operation
                return await query.message.edit_text(
                    "<b>⚠️ Rotation was not attempted.</b> The clone changed or another rotation needs operator review.",
                    reply_markup=InlineKeyboardMarkup([back]))
            if claim.modified_count != 1:
                raise RuntimeError("rotation reservation not confirmed")
            attempted = True
            # Do not use botapi.call: it retries some errors, unsafe for rotation.
            new_token = await b.replace_managed_bot_token(user_id=bot_id)
            if (not isinstance(new_token, str) or not new_token.startswith(f"{bot_id}:")
                    or len(new_token) <= len(str(bot_id)) + 1 or security._get_fernet() is not cipher):
                raise RuntimeError("rotation result unavailable")
            sealed = "enc:" + cipher.encrypt(new_token.encode()).decode()
            if security._get_fernet() is not cipher:
                raise RuntimeError("encryption key changed")
            # Save and clear the exact reservation together, never independently.
            result = await collection.update_one(
                {**expected, f"{_ROTATION_MARKER}.operation_id": operation_id},
                {"$set": {"bot_token_encrypted": sealed}, "$unset": {_ROTATION_MARKER: ""}},
                collation=Collation("simple"))
            if not result.acknowledged or result.matched_count != 1 or result.modified_count != 1:
                raise RuntimeError("rotation storage not confirmed")
        claim_started = False
        attempted = False  # token storage acknowledged; no automatic restart or repeat
        await query.message.edit_text(
            "<b>✅ New token saved.</b> Telegram has rotated the credential. "
            "If the clone was running, stop and start it from the dashboard to use the new token. "
            "Check its status; saving a token does not confirm a running worker.",
            reply_markup=InlineKeyboardMarkup([back]))
    except asyncio.CancelledError:
        if claim_started:
            _rotation_uncertain.add(bot_id)
        raise
    except Exception:
        if claim_started:
            _rotation_uncertain.add(bot_id)
            log.warning("Managed clone rotation reservation or result could not be confirmed for bot %s.", bot_id)
            try:
                detail = ("Telegram may have invalidated the old token." if attempted else
                          "The database reservation may have been written; Telegram was not contacted.")
                await query.message.edit_text(
                    "<b>⚠️ Rotation result is uncertain.</b> " + detail + " "
                    "Do not retry. Contact the operator and check the clone before taking further action.",
                    reply_markup=InlineKeyboardMarkup([back]))
            except Exception:
                pass
        else:
            log.warning("Managed clone rotation could not be prepared or its result displayed.")
            try:
                await query.message.edit_text("<b>⚠️ Rotation was not attempted or the result could not be displayed.</b> Check the dashboard before trying again.", reply_markup=InlineKeyboardMarkup([back]))
            except Exception:
                pass
    finally:
        _rotation_in_flight.discard(bot_id)


async def get_access_settings(bot_id: int, client=None):
    """Fetch Bot API 10.3 ``BotAccessSettings`` for a managed clone bot."""
    if botapi.enabled():
        return await botapi.get_managed_bot_access_settings(bot_id)
    from core.ui import is_wzgram_client
    if is_wzgram_client(client) and hasattr(client, "get_managed_bot_access_settings"):
        try:
            return await client.get_managed_bot_access_settings(bot_id)
        except Exception as e:
            log.debug(f"wzgram get_managed_bot_access_settings({bot_id}) failed: {e}")
    return None


async def set_access_settings(bot_id: int, restricted: bool,
                              added_user_ids: list[int] | None = None, client=None) -> bool:
    """Update Bot API 10.3 ``setManagedBotAccessSettings`` for a managed clone bot."""
    if botapi.enabled():
        return await botapi.set_managed_bot_access_settings(bot_id, restricted, added_user_ids=added_user_ids)
    from core.ui import is_wzgram_client
    if is_wzgram_client(client) and hasattr(client, "set_managed_bot_access_settings"):
        try:
            return bool(await client.set_managed_bot_access_settings(
                bot_id, restricted, added_user_ids=added_user_ids
            ))
        except Exception as e:
            log.debug(f"wzgram set_managed_bot_access_settings({bot_id}) failed: {e}")
    return False

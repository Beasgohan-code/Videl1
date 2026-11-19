"""
One-tap clone creation with Telegram *managed bots* (Bot API 9.6+, via aiogram).

When Videl's owner enables **Bot Management Mode** for Videl in @BotFather,
users no longer need to visit BotFather and copy a token:

  1. Videl builds a Telegram creation link and the user confirms in Telegram.
  2. A manager-bot update identifies a creator and bot. The user reviews the
     candidate in private before Videl retrieves its token.
  3. The bot token is verified, then channel and profile setup continues. If an
     update is missed, the user can paste a token instead.

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
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

import config
from config import MANAGED_BOTS, MANAGED_PAIR_TIMEOUT
from core import botapi
from filestore.database.main_db import MainDB
from filestore.fs_config import LOGGER

log = LOGGER(__name__)
main_db = MainDB()
_tasks: set = set()          # keep watcher tasks referenced
_CANDIDATE_TTL = 300  # seconds; confirmation and pairing state are process-local


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


async def _token_for(bot_id: int, client=None):
    if botapi.enabled():
        try:
            bridge = botapi.bot()
            return await bridge.get_managed_bot_token(user_id=bot_id) if bridge else None
        except Exception:
            log.warning("Managed bot token retrieval could not be confirmed.")
            return None
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
    st["data"].pop("managed_candidate", None)
    st["data"]["managed_username"] = username
    st["data"]["managed_pair_deadline"] = time.monotonic() + MANAGED_PAIR_TIMEOUT
    st["data"]["managed_status_msg"] = query.message
    link = newbot_link(me.username, username, name)
    await query.message.edit_text(
        "<b>🤖 Create a bot with Telegram</b>\n\n"
        "<blockquote>1️⃣ Tap <b>Create my bot</b> below.\n"
        "2️⃣ Telegram opens a ready-made form – just press <b>Create</b>.\n"
        "3️⃣ Return here and review the bot Telegram reports. Further setup is required.</blockquote>\n\n"
        f"Suggested username: <code>@{username}</code>\n"
        "<i>If Telegram's update does not arrive, you can paste a token instead.</i>\n"
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
    """Only a timeout notice: a suggested username is never ownership proof."""
    from filestore.main_bot.plugins.create_bot import _creation_state
    state = _creation_state.get(user_id)
    if state is None:
        return False
    await asyncio.sleep(MANAGED_PAIR_TIMEOUT if timeout is None else max(0, timeout))
    if (_creation_state.get(user_id) is state and _pending(user_id, username)
            and not state["data"].get("managed_candidate")):
        state["data"].pop("managed_username", None)
        state["data"].pop("managed_pair_deadline", None)
        state["data"].pop("managed_status_msg", None)
        await _safe_edit(status_msg, "<b>⌛ Telegram's creation update was not confirmed.</b>\n\n"
                                     "Try again or paste the token in this private chat.", _back_kb())
    return False


async def _safe_edit(msg, text, kb=None):
    try:
        await msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass


async def claim_username(client, message, user_id: int, text: str) -> bool:
    """A typed username is not ownership proof and never starts a child poller."""
    from filestore.main_bot.plugins.create_bot import _creation_state
    st = _creation_state.get(user_id)
    if not st or st.get("step") != "awaiting_token" or not st.get("data", {}).get("managed_username"):
        return False
    await message.reply(
        "<b>ℹ️ A username alone cannot confirm ownership.</b> "
        "Review Telegram's creation update when it arrives, or paste your bot token privately.")
    return True


@Client.on_managed_bot()
async def on_managed_bot_updated(client: Client, update):
    """Offer a candidate from Telegram's creator-bearing update, not a token."""
    from filestore.main_bot.plugins.create_bot import _creation_state
    user, bot_user = getattr(update, "user", None), getattr(update, "bot", None)
    uid, bot_id = getattr(user, "id", None), getattr(bot_user, "id", None)
    if (type(uid) is not int or uid <= 0 or getattr(user, "is_bot", None) not in (False, None)
            or type(bot_id) is not int or bot_id <= 0 or getattr(bot_user, "is_bot", None) is not True):
        return
    st = _creation_state.get(uid)
    if (not st or st.get("step") != "awaiting_token" or not st.get("data", {}).get("managed_username")
            or st["data"].get("managed_pair_deadline", 0) <= time.monotonic()):
        return
    data = st["data"]
    if data.get("managed_candidate") or data.get("managed_confirming"):
        return
    username = getattr(bot_user, "username", None)
    label = f"@{html.escape(username)}" if isinstance(username, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{3,31}", username) else "a bot without a confirmed username"
    nonce = secrets.token_urlsafe(8)
    candidate = {"bot_id": bot_id, "nonce": nonce, "deadline": time.monotonic() + _CANDIDATE_TTL}
    data["managed_candidate"] = candidate
    try:
        await client.send_message(uid,
            f"<b>🤖 Telegram reported {label}</b> (<code>{bot_id}</code>).\n\n"
            "Is this the bot you want to connect? Confirm only if you created or own it. "
            "This confirmation expires in five minutes. "
            "This does not start the worker; channel and mode setup still follow.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Yes, connect this bot", callback_data=f"managed_confirm_{nonce}")],
                [InlineKeyboardButton("🔑 Use a token instead", callback_data="create_bot")],
                [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
            ]))
    except Exception:
        if _creation_state.get(uid) is st and data.get("managed_candidate") is candidate:
            data.pop("managed_candidate", None)
        log.warning("Managed bot confirmation could not be delivered.")


@Client.on_callback_query(filters.regex(r"^managed_confirm_[A-Za-z0-9_-]+$"))
async def confirm_managed_bot(client: Client, query: CallbackQuery):
    from filestore.main_bot.plugins.create_bot import _creation_state, accept_token
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    if (not user or getattr(user, "is_bot", False) or not chat
            or getattr(chat, "type", None) != enums.ChatType.PRIVATE or chat.id != user.id):
        return await query.answer("Open bot creation in your private chat.", show_alert=True)
    st = _creation_state.get(user.id)
    candidate = (st or {}).get("data", {}).get("managed_candidate")
    nonce = str(getattr(query, "data", ""))[len("managed_confirm_"):]
    if (not st or st.get("step") != "awaiting_token" or not candidate
            or candidate.get("nonce") != nonce or candidate.get("deadline", 0) <= time.monotonic()):
        return await query.answer("Confirmation expired. Open /clone again or paste a token.", show_alert=True)
    data = st["data"]
    if data.get("managed_confirming"):
        return await query.answer("This confirmation is already being checked.", show_alert=True)
    # Claim before awaiting; no second token request for double-clicks.
    data["managed_confirming"] = True
    st["step"] = "verifying_managed"
    data.pop("managed_candidate", None)
    data.pop("managed_username", None)
    data.pop("managed_pair_deadline", None)
    data.pop("managed_status_msg", None)
    try:
        try:
            await query.answer("Checking the bot…")
        except Exception:
            pass
        try:
            token = await asyncio.wait_for(_token_for(candidate["bot_id"], client=client), timeout=20)
        except Exception:
            log.warning("Managed bot token retrieval could not be confirmed.")
            token = None
        if _creation_state.get(user.id) is not st or st.get("step") != "verifying_managed":
            return
        st["step"] = "awaiting_token"
        if not isinstance(token, str) or not token.startswith(f"{candidate['bot_id']}:") or not token.split(":", 1)[1]:
            return await _safe_edit(query.message, "<b>⚠️ The managed bot could not be confirmed.</b> Paste its token instead.", _back_kb())
        return await accept_token(client, user.id, token, query.message, managed=True,
                                  expected_state=st, expected_bot_id=candidate["bot_id"])
    finally:
        if _creation_state.get(user.id) is st and st.get("step") == "verifying_managed":
            st["step"] = "awaiting_token"
        data.pop("managed_confirming", None)


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

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

The dashboard of managed clones gets "🔁 Rotate token" (``replaceManagedBotToken``)
– the old token dies instantly, the new one is stored encrypted and the worker
restarts.  Without Bot Management Mode, everything silently falls back to the
classic "paste your token" flow.
"""
import html
import asyncio
import re
import secrets
import time
from urllib.parse import quote

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import MANAGED_BOTS, MANAGED_PAIR_TIMEOUT
from core import botapi
from filestore.database.main_db import MainDB
from filestore.fs_config import LOGGER

log = LOGGER(__name__)
main_db = MainDB()
_tasks: set = set()          # keep watcher tasks referenced
POLL_START, POLL_MAX = 4, 10  # seconds between username checks


async def available() -> bool:
    """True when Videl may create bots for users (Bot Management Mode on)."""
    return MANAGED_BOTS and botapi.enabled() and await botapi.can_manage_bots()


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


async def _token_for(bot_id: int):
    return await botapi.try_call(lambda b: b.get_managed_bot_token(user_id=bot_id))


def _back_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Paste a token instead", callback_data="create_bot")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
    ])


# ─────────────────────────── one-tap flow ───────────────────────────
@Client.on_callback_query(filters.regex(r"^managed_new$"))
async def managed_new(client: Client, query: CallbackQuery):
    from filestore.main_bot.plugins.create_bot import _creation_state
    user = query.from_user
    st = _creation_state.get(user.id)
    if not st or st.get("step") != "awaiting_token":
        return await query.answer("Tap ⚡ Create Bot again.", show_alert=True)
    if not await available():
        return await query.answer("One-tap creation is not available right now – paste a token instead.",
                                  show_alert=True)
    me = client.me or await client.get_me()
    username = suggested_username(user.first_name)
    name = f"{(user.first_name or 'My')[:40]}'s FileStore"
    st["data"]["managed_username"] = username
    link = newbot_link(me.username, username, name)
    await query.message.edit_text(
        "<b>⚡ One-tap bot creation</b>\n\n"
        "<blockquote>1️⃣ Tap <b>Create my bot</b> below.\n"
        "2️⃣ Telegram opens a ready-made form – just press <b>Create</b>.\n"
        "3️⃣ Come back here – I'll connect it automatically.</blockquote>\n\n"
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
    await query.answer()
    _spawn(watch_username(client, user.id, username, query.message))


async def watch_username(client, user_id: int, username: str, status_msg, timeout: int = None):
    """Poll until the suggested bot exists, then fetch its token and continue the wizard."""
    from filestore.main_bot.plugins.create_bot import accept_token
    deadline = time.time() + (timeout or MANAGED_PAIR_TIMEOUT)
    delay = POLL_START
    while time.time() < deadline:
        await asyncio.sleep(delay)
        delay = min(delay + 1, POLL_MAX)
        if not _pending(user_id, username):
            return False  # cancelled / token pasted / new attempt
        bot_user = await _resolve_bot(client, username)
        if not bot_user:
            continue
        token = await _token_for(bot_user.id)
        if not token:
            await _safe_edit(status_msg, f"<b>⚠️ @{username} exists but Videl can't manage it.</b>\n\n"
                                         "Paste its token from @BotFather instead.", _back_kb())
            return False
        await _safe_edit(status_msg, f"<b>⏳ Connecting @{username}…</b>")
        return await accept_token(client, user_id, token, status_msg, managed=True)
    if _pending(user_id, username):
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
    token = await _token_for(bot_user.id) if bot_user else None
    if not token:
        await _safe_edit(status, f"<b>❌ @{username} wasn't created through Videl.</b>\n\n"
                                 "Use the one-tap button, or paste the bot token.", _back_kb())
        return True
    code = secrets.token_hex(4)
    st["data"]["managed_username"] = username   # stop the old watcher
    await _safe_edit(status, (
        f"<b>🔐 Prove @{username} is yours</b>\n\n"
        "<blockquote>Tap the button, press <b>Start</b> in your new bot, then come back.</blockquote>"),
        InlineKeyboardMarkup([
            [InlineKeyboardButton(f"▶️ Start @{username}", url=f"https://t.me/{username}?start=vd{code}")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel_creation")],
        ]))
    _spawn(_await_proof(client, user_id, username, token, code, status))
    return True


async def _await_proof(client, user_id, username, token, code, status, timeout: int = 180):
    """Read the new bot's own updates (it isn't running anywhere yet) for ``/start vd<code>``."""
    from filestore.main_bot.plugins.create_bot import accept_token
    wb = botapi.worker(token)
    offset, deadline = None, time.time() + timeout
    try:
        while time.time() < deadline and _pending(user_id, username):
            updates = await wb.get_updates(offset=offset, timeout=10, allowed_updates=["message"],
                                           request_timeout=25)
            for u in updates or []:
                offset = u.update_id + 1
                m = u.message
                if m and m.from_user and m.from_user.id == user_id and (m.text or "").strip() == f"/start vd{code}":
                    if offset is not None:  # acknowledge so the worker doesn't see it again
                        await wb.get_updates(offset=offset, timeout=0, request_timeout=15)
                    await _safe_edit(status, f"<b>⏳ Connecting @{username}…</b>")
                    return await accept_token(client, user_id, token, status, managed=True)
            if not updates:
                await asyncio.sleep(1)
    except Exception as e:
        log.warning(f"ownership check for @{username} failed: {e}")
    if _pending(user_id, username):
        await _safe_edit(status, "<b>⌛ Verification timed out.</b>\n\nSend the @username again or paste the token.",
                         _back_kb())
    return False


# ─────────────────────────── token rotation ───────────────────────────
def rotate_row(bot: dict):
    """Dashboard row for managed clones (empty list otherwise)."""
    if bot.get("managed") and MANAGED_BOTS and botapi.enabled():
        return [InlineKeyboardButton("🔁 ʀᴏᴛᴀᴛᴇ ᴛᴏᴋᴇɴ", callback_data=f"rotate_tok_{bot['_id']}")]
    return []


@Client.on_callback_query(filters.regex(r"^rotate_tok_(do_)?(\d+)$"))
async def rotate_token(client: Client, query: CallbackQuery):
    m = re.match(r"^rotate_tok_(do_)?(\d+)$", query.data)
    confirmed, bot_id = bool(m.group(1)), int(m.group(2))
    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != query.from_user.id or not bot.get("managed"):
        return await query.answer("❌ Access denied!", show_alert=True)
    back = [InlineKeyboardButton("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")]
    if not confirmed:
        await query.message.edit_text(
            f"<b>🔁 Rotate the token of @{bot.get('bot_username')}?</b>\n\n"
            "<blockquote>The current token stops working immediately (use this if it leaked). "
            "Videl stores the new one encrypted and restarts the bot.</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Rotate now", callback_data=f"rotate_tok_do_{bot_id}")], back]))
        return await query.answer()
    await query.answer("🔁 Rotating…")
    try:
        new_token = await botapi.call(lambda b: b.replace_managed_bot_token(user_id=bot_id))
    except Exception as e:
        return await query.message.edit_text(f"<b>❌ Rotation failed:</b> <code>{html.escape(str(e))}</code>",
                                             reply_markup=InlineKeyboardMarkup([back]))
    await apply_new_token(bot, new_token)
    try:
        from core import botlog
        await botlog.event("CloneTokenRotated",
                           f"{botlog.user_block(query.from_user)}\n\n<b>🤖 Clone bot:</b> "
                           f"@{bot.get('bot_username')} (<code>{bot_id}</code>)", client=client)
    except Exception:
        pass
    await query.message.edit_text(
        f"<b>✅ Token rotated</b> for @{bot.get('bot_username')}.\n<i>The old token is dead; the bot restarted.</i>",
        reply_markup=InlineKeyboardMarkup([back]))


async def apply_new_token(bot: dict, new_token: str):
    """Store an encrypted token and restart the worker with it."""
    from filestore.utils.security import encrypt_token
    from filestore.worker_bot.engine import worker_engine
    enc = encrypt_token(new_token)
    await main_db.bots.update_one({"_id": bot["_id"]}, {"$set": {"bot_token_encrypted": enc}})
    bot = dict(bot, bot_token_encrypted=enc)
    try:
        if worker_engine.get_worker(bot["_id"]):
            await worker_engine.stop_worker(bot["_id"])
            await asyncio.sleep(1)
        if bot.get("is_active", True):
            await worker_engine.start_worker(bot)
    except Exception as e:
        log.warning(f"restart after token rotation failed: {e}")
    return enc

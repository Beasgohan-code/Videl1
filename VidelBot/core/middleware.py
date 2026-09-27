"""
Global middleware – runs before every module. Pyrogram runs one handler per
group, lowest group first, so each gate lives in its own group:

  group -4 → user tracking / new-user log   (never blocks)
  group -3 → ban + maintenance gate         (StopPropagation)
  group -2 → force-subscribe gate           (core/fsub.py)
"""
import logging
import time

from pyrogram import Client, StopPropagation, filters
from pyrogram.types import CallbackQuery, Message

from config import ADMINS
from core.db import vdb
from core.texts import BANNED_TEXT, MAINT_TEXT

log = logging.getLogger("videl.middleware")


def _gate_reason(user_id: int):
    if user_id in ADMINS:
        return None
    if vdb.is_banned(user_id):
        return BANNED_TEXT
    if vdb.cached_setting("maintenance", False):
        return MAINT_TEXT
    return None


@Client.on_message(filters.private & filters.incoming, group=-4)
async def track_users(client: Client, message: Message):
    user = message.from_user
    if not user or user.is_bot:
        return
    try:
        is_new = await vdb.track(user)
    except Exception as e:
        log.warning(f"user tracking failed: {e}")
        return
    if not is_new:
        return
    # Make sure the saver DB also knows the user (captions, words, limits …).
    try:
        from database.db import db as saver_db
        if not await saver_db.is_user_exist(user.id):
            await saver_db.add_user(user.id, user.first_name)
    except Exception as e:
        log.warning(f"saver add_user failed: {e}")
    # #NewUser → owner log channel (who, where from, total)
    try:
        from core import botlog
        botlog._start_seen[user.id] = time.time()   # don't double-log the same /start as #Start
        total = await vdb.total_users()
        text = message.text or message.caption or ""
        source = ""
        if text.startswith("/start") and len(text.split()) > 1:
            source = f"\n<b>🔗 Came from:</b> <code>{botlog.esc(text.split(maxsplit=1)[1][:64])}</code>"
        first = botlog.esc(text[:60]) if text and not text.startswith("/start") else "/start"
        await botlog.event("NewUser", botlog.user_block(user, (
            f"<b>💬 First message:</b> <code>{first}</code>{source}\n"
            f"<b>📊 Total users:</b> <code>{total}</code>")), client=client)
    except Exception as e:
        log.warning(f"new-user log failed: {e}")


@Client.on_message(filters.incoming & ~filters.channel & ~filters.successful_payment, group=-3)
async def gate_messages(client: Client, message: Message):
    if not message.from_user:
        return
    reason = _gate_reason(message.from_user.id)
    if not reason:
        return
    # Only answer in private chats and to real messages to avoid spam.
    if message.chat.type.value == "private" and (message.text or message.caption):
        try:
            await message.reply_text(reason)
        except Exception:
            pass
    raise StopPropagation


@Client.on_callback_query(group=-3)
async def gate_callbacks(client: Client, query: CallbackQuery):
    reason = _gate_reason(query.from_user.id)
    if not reason:
        return
    try:
        await query.answer(reason.replace("<b>", "").replace("</b>", "").split("\n")[0], show_alert=True)
    except Exception:
        pass
    raise StopPropagation

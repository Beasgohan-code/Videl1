"""
Support inbox — users talk to the owners through the bot, owners answer by
simply *replying* in their DM. Conversations can go back and forth: users
can reply to an answer to follow up.

  /support <text>              send a message to the owners
  /support (reply to a msg)    send any message (photo, video, file …)
  /support                     the next message you send is delivered

Every delivered message is mapped (chat, message_id → peer) in the
``support_map`` collection; the reply router only fires for replies to mapped
messages, so it never interferes with any other owner / user flow.
"""
import html
import logging
import time
from datetime import datetime, timezone

from pyrogram import Client, StopPropagation, filters
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message, CallbackQuery

from config import OWNERS, SUPPORT_ENABLED
from core.db import vdb

log = logging.getLogger("videl.support")
COOLDOWN = 20          # seconds between two support messages of one user
PENDING_TTL = 300      # "/support" alone waits 5 min for the next message
_last: dict[int, float] = {}
_pending: dict[int, float] = {}
TO_OWNERS = 0          # peer marker: deliver to every owner


def _col():
    return vdb.db["support_map"]


async def _remember(chat_id: int, msg_id: int, peer: int):
    await _col().insert_one({"chat": chat_id, "msg": msg_id, "peer": peer, "date": datetime.now(timezone.utc)})


async def _lookup(chat_id: int, msg_id: int):
    return await _col().find_one({"chat": chat_id, "msg": msg_id})


async def deliver_to_owners(client: Client, message: Message, text: str = None) -> int:
    """Send a user's message to every owner. Returns how many owners got it."""
    from core import botlog
    user = message.from_user
    header = (f"📨 <b>#Support</b>\n{botlog.user_block(user)}\n"
              + (f"\n<blockquote>{botlog.esc(text)}</blockquote>\n" if text else "")
              + "\n<i>↩️ Reply to this message to answer.</i>")
    kb = InlineKeyboardMarkup([[Btn("👤 Profile", callback_data=f"uadm:view:{user.id}")]])
    sent = 0
    for owner in OWNERS:
        try:
            h = await client.send_message(owner, header, reply_markup=kb, disable_web_page_preview=True)
            await _remember(owner, h.id, user.id)
            if text is None:
                c = await message.copy(owner, reply_to_message_id=h.id)
                await _remember(owner, c.id, user.id)
            sent += 1
        except Exception as e:
            log.debug(f"support → owner {owner} failed: {e}")
    await botlog.event("Support", botlog.user_block(user, (
        f"<b>💬 Message:</b> {botlog.esc((text or message.text or message.caption or '[media]')[:300])}")),
        client=client, dm=False)
    return sent


async def _submit(client: Client, user_msg: Message, content: Message, text: str = None):
    uid = user_msg.from_user.id
    left = COOLDOWN - (time.time() - _last.get(uid, 0))
    if left > 0:
        return await user_msg.reply_text(f"⏳ Please wait {int(left) + 1}s before sending another support message.")
    now = time.time()
    for k in [k for k, v in _last.items() if now - v > COOLDOWN]:
        _last.pop(k, None)
    _last[uid] = now
    n = await deliver_to_owners(client, content, text)
    if n:
        await user_msg.reply_text("✅ <b>Message sent to support!</b>\n<i>You'll get the answer right here.</i>")
    else:
        await user_msg.reply_text("❌ Support is unreachable right now – please try again later.")


@Client.on_message(filters.command(["support", "feedback"]) & filters.private)
async def support_cmd(client: Client, message: Message):
    if not SUPPORT_ENABLED or not OWNERS:
        return await message.reply_text("💬 The support inbox is disabled on this bot.")
    if message.reply_to_message:
        return await _submit(client, message, message.reply_to_message)
    if len(message.command) > 1:
        return await _submit(client, message, message, text=message.text.split(None, 1)[1][:3500])
    _pending[message.from_user.id] = time.time()
    await message.reply_text(
        "<b>💬 Support</b>\n\n<blockquote>Send your message now – text, photo, video or file. "
        "It goes straight to the bot owner and the answer arrives here.</blockquote>",
        reply_markup=InlineKeyboardMarkup([[Btn("❌ Cancel", callback_data="support_cancel")]]))


@Client.on_callback_query(filters.regex(r"^support_cancel$"))
async def support_cancel(client: Client, query: CallbackQuery):
    _pending.pop(query.from_user.id, None)
    await query.answer("Cancelled.")
    try:
        await query.message.edit_text("💬 Support message cancelled.")
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^support_btn$"))
async def support_btn(client: Client, query: CallbackQuery):
    if not SUPPORT_ENABLED or not OWNERS:
        return await query.answer("The support inbox is disabled.", show_alert=True)
    _pending[query.from_user.id] = time.time()
    await query.answer()
    await client.send_message(
        query.from_user.id,
        "<b>💬 Support</b>\n\n<blockquote>Send your message now – text, photo, video or file.</blockquote>",
        reply_markup=InlineKeyboardMarkup([[Btn("❌ Cancel", callback_data="support_cancel")]]))


# ─────────────────────────── routing filters ───────────────────────────
async def _is_pending(_, __, m: Message) -> bool:
    if not m.from_user or (m.text or "").startswith("/"):
        return False
    ts = _pending.get(m.from_user.id)
    return bool(ts) and time.time() - ts < PENDING_TTL


async def _is_mapped_reply(_, __, m: Message) -> bool:
    if not m.from_user or not getattr(m, "reply_to_message_id", None) or (m.text or "").startswith("/"):
        return False
    doc = await _lookup(m.chat.id, m.reply_to_message_id)
    if not doc:
        return False
    m.support_peer = doc["peer"]  # stash for the handler
    return True


pending_filter = filters.create(_is_pending, "SupportPending")
mapped_reply_filter = filters.create(_is_mapped_reply, "SupportReply")


@Client.on_message(filters.private & filters.incoming & pending_filter, group=-1)
async def support_pending_msg(client: Client, message: Message):
    _pending.pop(message.from_user.id, None)
    await _submit(client, message, message)
    raise StopPropagation


@Client.on_message(filters.private & filters.incoming & mapped_reply_filter, group=-1)
async def support_reply_router(client: Client, message: Message):
    peer = getattr(message, "support_peer", None)
    sender = message.from_user.id
    if sender in OWNERS and peer:  # owner → user
        try:
            h = await client.send_message(peer, "💬 <b>Reply from support:</b>\n<i>↩️ Reply to this message to follow up.</i>")
            c = await message.copy(peer, reply_to_message_id=h.id)
            await _remember(peer, h.id, TO_OWNERS)
            await _remember(peer, c.id, TO_OWNERS)
            await message.reply_text("✅ Reply delivered.", quote=True)
        except Exception as e:
            await message.reply_text(f"❌ Couldn't deliver: <code>{html.escape(str(e))}</code>", quote=True)
    elif peer == TO_OWNERS:        # user follow-up → owners
        await _submit(client, message, message)
    raise StopPropagation

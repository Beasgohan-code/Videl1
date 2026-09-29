"""
Global middleware – runs before every module. Pyrogram runs one handler per
group, lowest group first, so each gate lives in its own group:

  group -20 → re-delivered updates are dropped (Telegram replays some after a reconnect)
  group -5 → sender-less messages (channel posts, anonymous admins) stop here, except /id
  group -4 → user tracking / new-user log   (never blocks)
  group -3 → ban + maintenance gate         (StopPropagation)
  group -2 → force-subscribe gate           (core/fsub.py)
  group -1 → reply routers: support inbox, gift picker (core/support.py, core/payments.py)
"""
import logging
import time

from pyrogram import Client, StopPropagation, filters
from pyrogram.types import CallbackQuery, Message

from config import ADMINS
from core.db import vdb
from core.texts import BANNED_TEXT, MAINT_TEXT

log = logging.getLogger("videl.middleware")


# ─────────────────────────── replayed updates ───────────────────────────
class _Seen:
    """Keys of recently handled updates (bounded, time-limited)."""

    def __init__(self, ttl: float = 900, cap: int = 20000):
        from collections import OrderedDict
        self.ttl, self.cap, self._d = ttl, cap, OrderedDict()

    def check_and_add(self, key) -> bool:
        """True if `key` was already seen (→ drop the update)."""
        now = time.monotonic()
        while self._d:                                   # expire from the old end
            k, t = next(iter(self._d.items()))
            if now - t < self.ttl and len(self._d) < self.cap:
                break
            self._d.popitem(last=False)
        if key in self._d:
            return True
        self._d[key] = now
        return False

    def clear(self):
        self._d.clear()


SEEN = _Seen()
DROPPED = {"n": 0}


def _replayed(key) -> bool:
    if SEEN.check_and_add(key):
        DROPPED["n"] += 1
        log.info(f"dropped a re-delivered update {key}")
        return True
    return False


@Client.on_message(filters.incoming, group=-20)
async def drop_replayed_messages(client: Client, message: Message):
    chat = getattr(getattr(message, "chat", None), "id", None)
    if chat is not None and message.id and _replayed(("m", id(client), chat, message.id)):
        raise StopPropagation


@Client.on_callback_query(group=-20)
async def drop_replayed_callbacks(client: Client, query: CallbackQuery):
    if query.id and _replayed(("q", id(client), query.id)):
        raise StopPropagation


def _gate_reason(user_id: int):
    if user_id in ADMINS:
        return None
    if vdb.is_banned(user_id):
        return BANNED_TEXT
    if vdb.cached_setting("maintenance", False):
        return MAINT_TEXT
    return None


def _is_command(message: Message) -> bool:
    text = message.text or message.caption or ""
    return text.startswith("/")


@Client.on_message(filters.incoming & ~filters.service, group=-5)
async def senderless_gate(client: Client, message: Message):
    """Channel posts and anonymous-admin messages have no from_user; every module expects one.
    Let /id through (useful to read a channel's ID), give anonymous admins a hint, drop the rest."""
    if message.from_user:
        return
    text = message.text or message.caption or ""
    cmd = text.split()[0][1:].split("@")[0].lower() if text.startswith("/") and text.split() else ""
    if cmd == "id":
        return
    if _is_command(message) and message.chat and message.chat.type.value in ("group", "supergroup") \
            and message.sender_chat and message.sender_chat.id == message.chat.id:
        try:
            await message.reply_text("👤 You're posting as the group (anonymous admin). "
                                     "Turn off <b>Remain anonymous</b> or DM me to use commands.")
        except Exception:
            pass
    raise StopPropagation


@Client.on_message(filters.private & filters.incoming, group=-4)
async def track_users(client: Client, message: Message):
    user = message.from_user
    if not user or user.is_bot:
        return
    from core.analytics import touch
    await touch(user.id)
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
    # 🤝 referral credit (only genuinely new users count)
    try:
        from core.growth import on_new_user
        await on_new_user(client, user, message.text or "")
    except Exception as e:
        log.warning(f"referral hook failed: {e}")


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

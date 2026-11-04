"""Global error handler: logs unhandled exceptions and reports them to LOG_CHANNEL (rate-limited)."""
import html
import logging
import time
import traceback

from pyrogram import Client
from pyrogram.errors import (FloodWait, MessageIdInvalid, MessageNotModified, QueryIdInvalid,
                             UserIsBlocked)

from config import ERROR_MESSAGE, LOG_CHANNEL

log = logging.getLogger("videl.errors")
_IGNORED = (MessageNotModified, QueryIdInvalid, MessageIdInvalid, UserIsBlocked)
_last_sent: dict[str, float] = {}
REPORT_EVERY = 60  # seconds per distinct error


@Client.on_error()
async def on_error(client: Client, *args):
    # WzGram Dispatcher invokes: (client, exc, update_handler, update, users, chats)
    # Direct / legacy calls pass: (client, update, error)
    if len(args) >= 3 and isinstance(args[0], BaseException):
        error = args[0]
        update = args[2]
    elif len(args) >= 2:
        update, error = args[0], args[1]
    else:
        return
    if isinstance(error, _IGNORED):
        return
    if isinstance(error, FloodWait):
        log.warning(f"FloodWait {error.value}s while handling {type(update).__name__}")
        return
    tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    log.error(f"Unhandled error in {type(update).__name__}: {error}\n{tb}")
    if not (ERROR_MESSAGE and LOG_CHANNEL):
        return
    key = f"{type(error).__name__}:{str(error)[:80]}"
    now = time.time()
    if now - _last_sent.get(key, 0) < REPORT_EVERY:
        return
    _last_sent[key] = now
    from core.bg import trim
    trim(_last_sent, 2000)
    who = ""
    user = getattr(update, "from_user", None)
    uid = getattr(user, "id", None) or getattr(update, "user_id", None)
    if uid is None:
        raw_msg = getattr(update, "message", None)
        from_id = getattr(raw_msg, "from_id", None)
        uid = getattr(from_id, "user_id", None)
    if uid:
        who = f"\n<b>User:</b> <code>{uid}</code>"
    raw_msg = getattr(update, "message", None)
    what = (
        getattr(update, "text", None)
        or getattr(update, "data", None)
        or (getattr(raw_msg, "message", None) if not isinstance(raw_msg, str) else raw_msg)
        or ""
    )
    if isinstance(what, (bytes, bytearray)):
        what = what.decode("utf-8", "replace")
    try:
        await client.send_message(
            LOG_CHANNEL,
            f"#Error <b>{html.escape(type(error).__name__)}</b>{who}\n<b>Update:</b> "
            f"<code>{html.escape(str(what)[:100])}</code>\n<blockquote expandable><code>"
            f"{html.escape(tb[-3000:])}</code></blockquote>",
        )
    except Exception:
        pass

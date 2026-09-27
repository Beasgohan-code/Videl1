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
async def on_error(client: Client, update, error: Exception):
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
    who = ""
    user = getattr(update, "from_user", None)
    if user:
        who = f"\n<b>User:</b> <code>{user.id}</code>"
    what = getattr(update, "text", None) or getattr(update, "data", None) or ""
    try:
        await client.send_message(
            LOG_CHANNEL,
            f"#Error <b>{html.escape(type(error).__name__)}</b>{who}\n<b>Update:</b> "
            f"<code>{html.escape(str(what)[:100])}</code>\n<blockquote expandable><code>"
            f"{html.escape(tb[-3000:])}</code></blockquote>",
        )
    except Exception:
        pass

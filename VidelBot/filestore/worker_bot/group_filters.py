"""Opt-in plain-text group keyword replies scoped to a clone and a group.

Inspired by Miss Rose's filters; this intentionally supports a smaller syntax.
No message content or sender identity is persisted by the filter listener.
"""
import asyncio
import html
import logging
import re
import secrets
import time
from collections import OrderedDict

from pymongo.errors import DuplicateKeyError
from pyrogram import enums, filters

from filestore.database.mongo import get_db
from filestore.worker_bot.group_info import _can_configure, _scope
from filestore.worker_bot.group_notes import _name

log = logging.getLogger(__name__)
COLLECTION = "clone_group_filters"
MAX_FILTERS = 25
MAX_TEXT = 1000
MAX_ESCAPED = 3500
MAX_INPUT = 4096
MAX_ATTEMPTS = 6
CHECK_INTERVAL = 1.0
REPLY_INTERVAL = 10.0
MAX_TRACKED_GROUPS = 512
MAX_INFLIGHT = 32
MAX_CHECKS_PER_SECOND = 20.0
CHECK_BURST = 20.0
READ_TIMEOUT = 3.0
SEND_TIMEOUT = 8.0
_VERSION = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
# Process-local only. Each key is (bot ID, chat ID); no content or user ID.
_gates = OrderedDict()
_inflight = 0
_check_tokens = CHECK_BURST
_check_refill = None


class InvalidFilters(Exception):
    pass


def _validate(doc, scope):
    if doc is None:
        return {}
    if (not isinstance(doc, dict) or any(doc.get(k) != v for k, v in scope.items())
            or not isinstance(doc.get("version"), str) or not _VERSION.fullmatch(doc["version"])):
        raise InvalidFilters()
    items = doc.get("filters")
    if not isinstance(items, dict) or len(items) > MAX_FILTERS:
        raise InvalidFilters()
    for name, text in items.items():
        if (not isinstance(name, str) or _name(name) != name or not isinstance(text, str) or not text
                or len(text) > MAX_TEXT or len(html.escape(text)) > MAX_ESCAPED):
            raise InvalidFilters()
    return items


async def _load(scope):
    doc = await get_db()[COLLECTION].find_one({"_id": scope["_id"]})
    return doc, _validate(doc, scope)


async def _change(scope, name, text):
    coll = get_db()[COLLECTION]
    for _ in range(MAX_ATTEMPTS):
        doc, items = await _load(scope)
        items = dict(items)
        if text is None:
            if name not in items:
                return "missing"
            del items[name]
        else:
            if name not in items and len(items) >= MAX_FILTERS:
                return "full"
            items[name] = text
        version = secrets.token_hex(16)
        if doc is None:
            try:
                result = await coll.insert_one({**scope, "version": version, "filters": items})
                if not result.acknowledged:
                    raise RuntimeError("unacknowledged write")
                return "saved"
            except DuplicateKeyError:
                continue
        result = await coll.update_one({**scope, "version": doc["version"]},
                                       {"$set": {"version": version, "filters": items}})
        if not result.acknowledged:
            raise RuntimeError("unacknowledged write")
        if result.matched_count == 1:
            return "saved"
    return "conflict"


async def filter_command(client, message, action):
    if action not in ("filter", "filters", "stopfilter"):
        return
    scope = _scope(client, message)
    if scope is None:
        return
    if action != "filters" and not await _can_configure(client, message, scope):
        return await message.reply_text("🔒 A directly identified group admin with change-info permission is required.")
    args = (getattr(message, "command", None) or [])[1:]
    if action == "filters":
        if args:
            return await message.reply_text("Usage: /filters")
        try:
            _, items = await _load(scope)
        except Exception:
            return await message.reply_text("⚠️ Filters could not be loaded. Please try again later.")
        if not items:
            return await message.reply_text("No text filters have been set for this group.")
        listing = "\n".join(f"• <code>{html.escape(name)}</code>" for name in sorted(items))
        return await message.reply_text("<b>💬 Group filters</b>\n" + listing, parse_mode=enums.ParseMode.HTML)
    if action == "filter":
        raw = getattr(message, "text", None)
        parts = raw.split(maxsplit=2) if isinstance(raw, str) else []
        name = _name(parts[1]) if len(parts) == 3 else None
        text = parts[2].strip() if len(parts) == 3 else ""
        if not name or not text or len(text) > MAX_TEXT or len(html.escape(text)) > MAX_ESCAPED:
            return await message.reply_text("Usage: /filter WORD TEXT — use 1–32 letters, numbers or underscores for WORD and up to 1000 characters of plain text.")
    else:
        name = _name(args[0]) if len(args) == 1 else None
        if not name:
            return await message.reply_text("Usage: /stopfilter WORD")
        text = None
    try:
        outcome = await _change(scope, name, text)
    except Exception:
        log.warning("Clone group filter change could not be confirmed.")
        return await message.reply_text("⚠️ The change could not be confirmed. Review /filters before retrying.")
    if outcome == "missing":
        return await message.reply_text("That filter was not found in this group.")
    if outcome == "full":
        return await message.reply_text(f"⚠️ This group already has {MAX_FILTERS} filters. Remove one before adding another.")
    if outcome == "conflict":
        return await message.reply_text("⚠️ Filters changed concurrently. Review /filters and try again.")
    return await message.reply_text("✅ Text filter saved. Its reply is visible in the group." if action == "filter" else "✅ Text filter removed.")


def _acquire(scope, message_id):
    """Bound work before any await; do not queue when busy or saturated."""
    global _inflight, _check_tokens, _check_refill
    now = time.monotonic()
    key = (scope["bot_id"], scope["chat_id"])
    state = _gates.get(key)
    if state is None:
        # Evict only inactive, fully cooled entries; never evict active work.
        if len(_gates) >= MAX_TRACKED_GROUPS:
            for old_key, old in list(_gates.items()):
                if not old["busy"] and now - old["checked"] >= REPLY_INTERVAL and now - old["sent"] >= REPLY_INTERVAL:
                    del _gates[old_key]
                    break
            else:
                return None
        state = {"checked": float('-inf'), "sent": float('-inf'), "last_id": 0, "busy": False}
        _gates[key] = state
    _gates.move_to_end(key)
    if message_id <= state["last_id"]:
        return None
    # Record even skipped messages so late duplicate updates don't bypass throttling.
    state["last_id"] = message_id
    if state["busy"] or _inflight >= MAX_INFLIGHT or now - state["checked"] < CHECK_INTERVAL or now - state["sent"] < REPLY_INTERVAL:
        return None
    if _check_refill is not None:
        _check_tokens = min(CHECK_BURST, _check_tokens + max(0.0, now - _check_refill) * MAX_CHECKS_PER_SECOND)
    _check_refill = now
    if _check_tokens < 1.0:
        return None
    _check_tokens -= 1.0
    state["busy"] = True
    state["checked"] = now
    _inflight += 1
    return state


def _release(state):
    global _inflight
    state["busy"] = False
    _inflight -= 1


def _match(text, items):
    # Stable order makes overlapping triggers predictable. Unicode word
    # boundaries prevent matching inside a larger word; triggers are ASCII.
    for name in sorted(items, key=lambda key: (-len(key), key)):
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, flags=re.IGNORECASE):
            return items[name]
    return None


async def on_text(client, message):
    scope = _scope(client, message)
    text = getattr(message, "text", None)
    actor = getattr(message, "from_user", None)
    msg_id = getattr(message, "id", None)
    if (scope is None or not isinstance(text, str) or not text or len(text) > MAX_INPUT
            or text.lstrip().startswith(("/", ".", "!", "#"))
            or actor is None or getattr(actor, "is_bot", None) is not False
            or type(getattr(actor, "id", None)) is not int or not 0 < actor.id < 2**63
            or getattr(message, "sender_chat", None) is not None or getattr(message, "outgoing", False)
            or type(msg_id) is not int or not 0 < msg_id < 2**63):
        return
    state = _acquire(scope, msg_id)
    if state is None:
        return
    try:
        try:
            _, items = await asyncio.wait_for(_load(scope), timeout=READ_TIMEOUT)
        except Exception:
            log.debug("Clone group filter settings could not be loaded.")
            return
        response = _match(text, items)
        if response is None:
            return
        # Reserve before the await: a failed/ambiguous send is never retried.
        state["sent"] = time.monotonic()
        try:
            await asyncio.wait_for(message.reply_text(html.escape(response), parse_mode=enums.ParseMode.HTML, quote=True),
                                   timeout=SEND_TIMEOUT)
        except Exception:
            log.debug("Clone group filter reply could not be confirmed.")
    finally:
        _release(state)


def setup_group_filters(app):
    @app.on_message(filters.command(["filter", "filters", "stopfilter"], prefixes=["/", "."]) & filters.group)
    async def handle_filters(client, message):
        await filter_command(client, message, message.command[0].lower())

    # A separate handler group observes ordinary messages without swallowing
    # commands, joins, or other group-0 handlers.
    @app.on_message(filters.group & filters.text, group=1)
    async def handle_group_text(client, message):
        await on_text(client, message)

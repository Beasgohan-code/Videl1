"""Plain-text group notes, scoped to one clone and one chat.

Miss Rose's public notes documentation inspired the command names. This is an
independent, deliberately smaller implementation; notes are visible in-group.
"""
import html
import logging
import re
import secrets

from pymongo.errors import DuplicateKeyError
from pyrogram import enums, filters

from filestore.database.mongo import get_db
from filestore.worker_bot.group_info import _can_configure, _scope

log = logging.getLogger(__name__)
COLLECTION = "clone_group_notes"
MAX_NOTES = 50
MAX_TEXT = 1000
MAX_ESCAPED = 3500
MAX_ATTEMPTS = 6
_NAME = re.compile(r"[a-z0-9_]{1,32}\Z", re.ASCII)
_VERSION = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)


class InvalidNotes(Exception):
    pass


def _name(value):
    if not isinstance(value, str) or not value.isascii():
        return None
    name = value.lower()
    return name if _NAME.fullmatch(name) else None


def _validate(doc, scope):
    if doc is None:
        return {}
    if (not isinstance(doc, dict) or any(doc.get(k) != v for k, v in scope.items())
            or not isinstance(doc.get("version"), str) or not _VERSION.fullmatch(doc["version"])):
        raise InvalidNotes()
    notes = doc.get("notes")
    if not isinstance(notes, dict) or len(notes) > MAX_NOTES:
        raise InvalidNotes()
    for key, value in notes.items():
        if (not isinstance(key, str) or _name(key) != key or not isinstance(value, str) or not value
                or len(value) > MAX_TEXT or len(html.escape(value)) > MAX_ESCAPED):
            raise InvalidNotes()
    return notes


async def _load(scope):
    # Looking up by _id detects malformed rows instead of hiding a collision.
    doc = await get_db()[COLLECTION].find_one({"_id": scope["_id"]})
    return doc, _validate(doc, scope)


async def _change(scope, name, text):
    """Bounded optimistic single-document update; no retry on uncertain writes."""
    coll = get_db()[COLLECTION]
    for _ in range(MAX_ATTEMPTS):
        doc, notes = await _load(scope)
        notes = dict(notes)
        if text is None:
            if name not in notes:
                return "missing"
            del notes[name]
        else:
            if name not in notes and len(notes) >= MAX_NOTES:
                return "full"
            notes[name] = text
        version = secrets.token_hex(16)
        if doc is None:
            try:
                result = await coll.insert_one({**scope, "version": version, "notes": notes})
                if not result.acknowledged:
                    raise RuntimeError("unacknowledged write")
                return "saved"
            except DuplicateKeyError:
                # A concurrent creator won. Its acknowledged document can be read again.
                continue
        result = await coll.update_one({**scope, "version": doc["version"]},
                                       {"$set": {"version": version, "notes": notes}})
        if not result.acknowledged:
            raise RuntimeError("unacknowledged write")
        if result.matched_count == 1:
            return "saved"
    return "conflict"


async def notes_command(client, message, action):
    scope = _scope(client, message)
    if scope is None:
        return
    if action in ("save", "clear") and not await _can_configure(client, message, scope):
        return await message.reply_text("🔒 A directly identified group admin with change-info permission is required.")
    command = getattr(message, "command", None) or []
    args = command[1:]
    if action == "notes":
        if args:
            return await message.reply_text("Usage: /notes")
        try:
            _, notes = await _load(scope)
        except Exception:
            return await message.reply_text("⚠️ Notes could not be loaded. Please try again later.")
        if not notes:
            return await message.reply_text("No notes have been saved for this group.")
        listing = "\n".join(f"• <code>{html.escape(name)}</code>" for name in sorted(notes))
        return await message.reply_text("<b>📝 Group notes</b>\n" + listing, parse_mode=enums.ParseMode.HTML)
    if action == "save":
        raw = getattr(message, "text", None)
        parts = raw.split(maxsplit=2) if isinstance(raw, str) else []
        name = _name(parts[1]) if len(parts) == 3 else None
        text = parts[2].strip() if len(parts) == 3 else ""
        if not name or not text or len(text) > MAX_TEXT or len(html.escape(text)) > MAX_ESCAPED:
            return await message.reply_text("Usage: /save NAME TEXT — names use 1–32 letters, numbers or underscores; text is limited to 1000 characters.")
    else:
        name = _name(args[0]) if len(args) == 1 else None
        if not name:
            return await message.reply_text(f"Usage: /{action} NAME")
        text = None
    if action == "get":
        try:
            _, notes = await _load(scope)
        except Exception:
            return await message.reply_text("⚠️ Notes could not be loaded. Please try again later.")
        if name not in notes:
            return await message.reply_text("That note was not found in this group.")
        return await message.reply_text(html.escape(notes[name]), parse_mode=enums.ParseMode.HTML)
    try:
        outcome = await _change(scope, name, text)
    except Exception:
        log.warning("Clone group note update could not be confirmed.")
        return await message.reply_text("⚠️ The change could not be confirmed. Check the note before retrying.")
    if outcome == "missing":
        return await message.reply_text("That note was not found in this group.")
    if outcome == "full":
        return await message.reply_text(f"⚠️ This group already has {MAX_NOTES} notes. Clear one before adding another.")
    if outcome == "conflict":
        return await message.reply_text("⚠️ Notes changed concurrently. Review them and try again.")
    return await message.reply_text("✅ Note saved. Anyone in this group can read it." if action == "save" else "✅ Note cleared.")


def setup_group_notes(app):
    @app.on_message(filters.command(["save", "get", "notes", "clear"], prefixes=["/", "."]) & filters.group)
    async def handle_notes(client, message):
        await notes_command(client, message, message.command[0].lower())

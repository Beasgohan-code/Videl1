"""Manual, non-escalating group warnings, isolated by clone, chat and user.

Only counters are persisted; reasons are displayed once and are not stored.
Telegram message IDs provide an ordering watermark, not a durable update queue.
"""
import html
import logging
import re
import secrets

from pymongo.errors import DuplicateKeyError
from pyrogram import enums, filters

from filestore.database.mongo import get_db
from filestore.worker_bot.group_info import _scope
from filestore.worker_bot.group_profile import _has_restrict, _parse_action

log = logging.getLogger(__name__)
COLLECTION = "clone_group_warns"
MAX_WARNINGS = 100
MAX_ATTEMPTS = 6
_VERSION = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)


class InvalidWarnings(Exception):
    pass


def _valid(doc, scope):
    if doc is None:
        return 0
    if (not isinstance(doc, dict) or doc.get("_id") != scope["_id"]
            or any(not isinstance(doc.get(k), int) or isinstance(doc.get(k), bool) or doc[k] != scope[k]
                   for k in ("bot_id", "chat_id", "user_id"))
            or not isinstance(doc.get("count"), int) or isinstance(doc.get("count"), bool)
            or not 0 <= doc["count"] <= MAX_WARNINGS
            or not isinstance(doc.get("last_message_id"), int) or isinstance(doc.get("last_message_id"), bool)
            or not 0 < doc["last_message_id"] < 2**63
            or not isinstance(doc.get("version"), str) or not _VERSION.fullmatch(doc["version"])):
        raise InvalidWarnings()
    return doc["count"]


async def _load(scope):
    doc = await get_db()[COLLECTION].find_one({"_id": scope["_id"]})
    return doc, _valid(doc, scope)


async def _change(scope, message_id, action):
    """CAS count and watermark together; retry only confirmed conflicts."""
    if action not in ("warn", "rmwarn", "resetwarn") or type(message_id) is not int or not 0 < message_id < 2**63:
        raise ValueError("invalid warning operation")
    coll = get_db()[COLLECTION]
    for _ in range(MAX_ATTEMPTS):
        doc, count = await _load(scope)
        if doc is not None and message_id <= doc["last_message_id"]:
            return "stale", count
        if action == "warn" and count == MAX_WARNINGS:
            return "full", count
        new_count = count + 1 if action == "warn" else max(0, count - 1) if action == "rmwarn" else 0
        fields = {"count": new_count, "last_message_id": message_id, "version": secrets.token_hex(16)}
        if doc is None:
            try:
                result = await coll.insert_one({**scope, **fields})
                if not result.acknowledged:
                    raise RuntimeError("unacknowledged write")
                return "saved", new_count
            except DuplicateKeyError:
                continue
        result = await coll.update_one({**scope, "version": doc["version"]}, {"$set": fields})
        if not result.acknowledged:
            raise RuntimeError("unacknowledged write")
        if result.matched_count == 1:
            return "saved", new_count
    return "conflict", None


async def warning_command(client, message, action):
    if action not in ("warn", "warns", "rmwarn", "resetwarn"):
        return
    group = _scope(client, message)
    actor = getattr(message, "from_user", None)
    actor_id = getattr(actor, "id", None)
    bot_id = getattr(getattr(client, "me", None), "id", None)
    message_id = getattr(message, "id", None)
    if group is None:
        return
    if (actor is None or getattr(actor, "is_bot", False) or getattr(message, "sender_chat", None) is not None
            or type(actor_id) is not int or not 0 < actor_id < 2**63
            or type(bot_id) is not int or not 0 < bot_id < 2**63):
        return await message.reply_text("🔒 A directly identified user and bot are required.")
    if type(message_id) is not int or not 0 < message_id < 2**63:
        return await message.reply_text("⚠️ Message identity could not be verified. No change was attempted.")
    args = (getattr(message, "command", None) or [])[1:]
    reply = getattr(message, "reply_to_message", None)
    self_view = action == "warns" and not args and reply is None
    # Check the actor before resolving usernames or accessing another user's counter.
    if not self_view:
        try:
            actor_member = await client.get_chat_member(group["chat_id"], actor_id)
            authorized = _has_restrict(actor_member)
        except Exception:
            authorized = False
        if not authorized:
            return await message.reply_text("🔒 A directly identified group admin with restrict-members permission is required.")
    if self_view:
        target, reason = actor_id, ""
    else:
        parsed = _parse_action(message, action)
        if parsed is None or (action != "warn" and parsed[2]):
            return await message.reply_text("Usage: /warn USER [reason], or reply with /warn [reason]. Use /warns, /rmwarn or /resetwarn with a target or reply.")
        token, _, reason, _ = parsed
        try:
            target = getattr(await client.get_users(token), "id", None) if isinstance(token, str) else token
        except Exception:
            return await message.reply_text("⚠️ Could not resolve that user. No change was attempted.")
        if type(target) is not int or not 0 < target < 2**63:
            return await message.reply_text("⚠️ Could not resolve that user. No change was attempted.")
    scope = {"_id": f"{group['bot_id']}:{group['chat_id']}:{target}",
             "bot_id": group["bot_id"], "chat_id": group["chat_id"], "user_id": target}
    if action == "warns":
        try:
            _, count = await _load(scope)
        except Exception:
            return await message.reply_text("⚠️ Warnings could not be loaded. Please try again later.")
        return await message.reply_text(f"📝 User <code>{target}</code> has <b>{count}</b> recorded warning(s). No automatic punishment is configured.", parse_mode=enums.ParseMode.HTML)
    if action == "warn" and target in (actor_id, bot_id):
        return await message.reply_text("🔒 Cannot warn yourself or this bot.")
    if action == "warn":
        try:
            member = await client.get_chat_member(group["chat_id"], target)
        except Exception:
            return await message.reply_text("⚠️ Could not verify the target in this group. No change was attempted.")
        if getattr(member, "status", None) not in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED):
            return await message.reply_text("🔒 Target is not an eligible group member; admins and owners are protected.")
    try:
        outcome, count = await _change(scope, message_id, action)
    except Exception:
        log.warning("Clone group warning update could not be confirmed.")
        return await message.reply_text("⚠️ The change could not be confirmed. Check /warns before retrying.")
    if outcome == "stale":
        return await message.reply_text("⚠️ This command was already processed or arrived out of order. Check /warns before issuing a new command.")
    if outcome == "full":
        return await message.reply_text(f"⚠️ This user already has {MAX_WARNINGS} warnings. No warning was added.")
    if outcome == "conflict":
        return await message.reply_text("⚠️ Warnings changed concurrently. Check /warns and try again.")
    verb = {"warn": "Warning recorded", "rmwarn": "One warning removed if present", "resetwarn": "Warnings reset"}[action]
    text = f"✅ {verb} for user <code>{target}</code>. Current count: <b>{count}</b>."
    if action == "warn" and reason:
        text += f"\nReason: {html.escape(reason)}"
    text += "\nNo automatic punishment was applied."
    return await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


def setup_group_warns(app):
    @app.on_message(filters.command(["warn", "warns", "rmwarn", "resetwarn"], prefixes=["/", "."]) & filters.group)
    async def handle_warns(client, message):
        await warning_command(client, message, message.command[0].lower())

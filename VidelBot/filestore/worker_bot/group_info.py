"""Clone/group-scoped rules and text greetings, independent of main-bot state."""
import html
import logging
import re
from types import SimpleNamespace

from pyrogram import enums, filters

from filestore.database.mongo import get_db

log = logging.getLogger(__name__)
COLLECTION = "clone_group_info"
MAX_TEXT = 1000
MAX_RENDERED = 3500
MAX_JOINS = 10
DEFAULT_WELCOME = "Welcome {first} to {chatname}!"
PLACEHOLDERS = re.compile(r"\{(first|id|chatname)\}")


def _scope(client, message):
    """Take both identity components from the live client and incoming group."""
    bot_id = getattr(getattr(client, "me", None), "id", None)
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if (type(bot_id) is not int or not 0 < bot_id < 2**63
            or chat is None or getattr(chat, "type", None) not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
            or type(chat_id) is not int or not -(2**63) < chat_id < 0):
        return None
    return {"_id": f"{bot_id}:{chat_id}", "bot_id": bot_id, "chat_id": chat_id}


def _admin_can_change(member):
    status = getattr(member, "status", None)
    if status == enums.ChatMemberStatus.OWNER:
        return True
    privileges = getattr(member, "privileges", None)
    return (status == enums.ChatMemberStatus.ADMINISTRATOR and privileges is not None
            and getattr(privileges, "can_change_info", None) is True)


async def _can_configure(client, message, scope):
    actor = getattr(message, "from_user", None)
    if (actor is None or getattr(actor, "is_bot", False) or getattr(message, "sender_chat", None) is not None
            or type(getattr(actor, "id", None)) is not int or actor.id <= 0):
        return False
    try:
        member = await client.get_chat_member(scope["chat_id"], actor.id)
        return _admin_can_change(member)
    except Exception:
        return False


def _render_welcome(template, user, chat):
    name = str(getattr(user, "first_name", None) or "Member")[:128]
    title = str(getattr(chat, "title", None) or "Group")[:128]
    uid = getattr(user, "id", None)
    if type(uid) is not int or not 0 < uid < 2**63:
        return None
    values = {"first": html.escape(name), "chatname": html.escape(title), "id": str(uid)}
    # Substitute once: placeholders inside a user's name are never interpreted.
    text = PLACEHOLDERS.sub(lambda match: values[match.group(1)], html.escape(template))
    return text if len(text) <= MAX_RENDERED else None


def _content(message):
    raw = getattr(message, "text", None)
    if not isinstance(raw, str):
        return None
    parts = raw.split(maxsplit=1)
    text = parts[1].strip() if len(parts) == 2 else ""
    return text if text and len(text) <= MAX_TEXT else None


def _welcome_valid(text):
    # Reject templates that could exceed Telegram's limit after safe escaping
    # and placeholder expansion, even for long names and titles.
    extreme = SimpleNamespace(id=2**63 - 1, first_name='"' * 128)
    chat = SimpleNamespace(title='"' * 128)
    return _render_welcome(text, extreme, chat) is not None


async def _read(scope):
    return await get_db()[COLLECTION].find_one(scope) or {}


async def _write(scope, update):
    # Unique _id is bot + group. Matching the components as well fails closed
    # if a malformed/corrupted row ever occupies this identifier.
    result = await get_db()[COLLECTION].update_one(scope, update, upsert=True)
    if not result.acknowledged:
        raise RuntimeError("Unacknowledged write")


async def settings_command(client, message, action):
    scope = _scope(client, message)
    if scope is None:
        return
    if action == "rules":
        if len(getattr(message, "command", None) or []) != 1:
            return await message.reply_text("Usage: /rules")
        try:
            settings = await _read(scope)
        except Exception:
            return await message.reply_text("⚠️ Rules could not be loaded. Please try again later.")
        text = settings.get("rules")
        if text is None or text == "":
            return await message.reply_text("No rules have been set for this group.")
        if not isinstance(text, str) or len(text) > MAX_TEXT or len(html.escape(text)) > MAX_RENDERED:
            return await message.reply_text("⚠️ Rules could not be displayed; an admin can review or reset them.")
        return await message.reply_text("<b>📜 Group rules</b>\n\n" + html.escape(text), parse_mode=enums.ParseMode.HTML)

    if not await _can_configure(client, message, scope):
        return await message.reply_text("🔒 A directly identified group admin with change-info permission is required.")
    args = (getattr(message, "command", None) or [])[1:]
    if action == "welcome" and not args:
        try:
            settings = await _read(scope)
        except Exception:
            return await message.reply_text("⚠️ Welcome settings could not be loaded. Please try again later.")
        template = settings.get("welcome_text", DEFAULT_WELCOME)
        if not isinstance(template, str) or len(template) > MAX_TEXT:
            return await message.reply_text("⚠️ Welcome template is invalid; set a new text or reset it.")
        preview = _render_welcome(template, message.from_user, message.chat)
        if preview is None:
            return await message.reply_text("⚠️ Welcome template is too long; set a shorter one.")
        state = "on" if settings.get("welcome_enabled") is True else "off"
        return await message.reply_text(f"<b>👋 Welcome: {state}</b>\n\n{preview}", parse_mode=enums.ParseMode.HTML)
    if action in ("setrules", "setwelcome"):
        text = _content(message)
        if text is None or (action == "setrules" and len(html.escape(text)) > MAX_RENDERED) or (action == "setwelcome" and not _welcome_valid(text)):
            return await message.reply_text(f"⚠️ Provide plain text up to {MAX_TEXT} characters; reduce repeated placeholders or special characters if needed.")
        update = {"$set": {"rules" if action == "setrules" else "welcome_text": text}}
        result = "✅ Rules saved." if action == "setrules" else "✅ Welcome text saved. Use /welcome on to enable welcomes."
    elif action == "welcome":
        if len(args) != 1 or args[0].lower() not in ("on", "off", "yes", "no", "true", "false"):
            return await message.reply_text("Usage: /welcome on or /welcome off")
        enabled = args[0].lower() in ("on", "yes", "true")
        update = {"$set": {"welcome_enabled": enabled}}
        result = "✅ Welcomes enabled." if enabled else "✅ Welcomes disabled; the saved text is retained."
    elif action in ("resetrules", "resetwelcome"):
        if args:
            return await message.reply_text("⚠️ This command does not take arguments.")
        update = {"$unset": {"rules" if action == "resetrules" else "welcome_text": ""}}
        result = "✅ Rules removed." if action == "resetrules" else "✅ Welcome text reset to the default; the on/off setting is unchanged."
    else:
        return
    try:
        await _write(scope, update)
    except Exception:
        log.warning("Clone group setting change could not be confirmed.")
        return await message.reply_text("⚠️ The change could not be confirmed. Review the current setting before retrying.")
    return await message.reply_text(result)


async def welcome_members(client, message):
    scope = _scope(client, message)
    if scope is None:
        return
    users = getattr(message, "new_chat_members", None)
    if not isinstance(users, (list, tuple)):
        return
    recipients = [user for user in users[:MAX_JOINS]
                  if not getattr(user, "is_bot", False) and getattr(user, "id", None) != scope["bot_id"]]
    if not recipients:
        return
    try:
        settings = await _read(scope)
    except Exception:
        log.warning("Clone group welcome settings could not be loaded.")
        return
    if settings.get("welcome_enabled") is not True:
        return
    template = settings.get("welcome_text", DEFAULT_WELCOME)
    if not isinstance(template, str) or not template or len(template) > MAX_TEXT:
        return
    for user in recipients:
        text = _render_welcome(template, user, message.chat)
        if text is None:
            continue
        try:
            await client.send_message(scope["chat_id"], text, parse_mode=enums.ParseMode.HTML)
        except Exception:
            # Delivery may have happened despite an error: never retry automatically.
            log.warning("Clone group welcome delivery could not be confirmed.")


async def _has_new_members(_, __, message):
    return bool(getattr(message, "new_chat_members", None))


def setup_group_info(app):
    @app.on_message(filters.command(["rules", "setrules", "resetrules", "welcome", "setwelcome", "resetwelcome"], prefixes=["/", "."]) & filters.group)
    async def handle_settings(client, message):
        await settings_command(client, message, message.command[0].lower())

    @app.on_message(filters.create(_has_new_members) & filters.group)
    async def handle_join(client, message):
        await welcome_members(client, message)

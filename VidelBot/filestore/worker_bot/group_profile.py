"""Independent clone group moderation; no main-bot group settings or caches.

The command family is inspired by the public Miss Rose documentation. Telegram
remains the authority for every action; this module has no shared state.
"""
import html
import logging
import re
from datetime import datetime, timedelta, timezone

from pyrogram import enums, filters
from pyrogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault, ChatPermissions

log = logging.getLogger(__name__)
GROUP_COMMANDS = [
    ("start", "About this group bot"),
    ("help", "Group moderation commands"),
    ("groupid", "Show this group ID"),
    ("ban", "Ban a member by reply, ID or username"),
    ("tban", "Temporarily ban a member"),
    ("dban", "Ban and delete the replied message"),
    ("sban", "Silently ban a member"),
    ("unban", "Unban a user"),
    ("mute", "Mute a member"),
    ("tmute", "Temporarily mute a member"),
    ("dmute", "Mute and delete the replied message"),
    ("smute", "Silently mute a member"),
    ("unmute", "Restore group default permissions"),
    ("kick", "Remove a member who may rejoin"),
    ("dkick", "Kick and delete the replied message"),
    ("skick", "Silently kick a member"),
    ("del", "Delete a replied message"),
    ("pin", "Pin a replied message"),
    ("unpin", "Unpin a message"),
]
HELP = ("<b>🛡 Group Management</b>\n\n"
        "<b>Ban:</b> <code>/ban USER_ID [reason]</code> or reply with <code>/ban [reason]</code>. "
        "Usernames such as <code>@name</code> are also supported when Telegram can resolve them.\n"
        "<b>Timed:</b> <code>/tban USER_ID 3d [reason]</code> or reply with <code>/tban 3d</code>. "
        "Use minutes (m), hours (h), days (d) or weeks (w); range: 5 minutes–365 days.\n"
        "<b>Delete + ban:</b> reply with <code>/dban [reason]</code>. "
        "<b>Silent:</b> <code>/sban USER_ID [reason]</code> or reply; deletes the command "
        "and replied message after a confirmed ban. <code>/unban USER_ID</code> reverses a ban.\n\n"
        "<b>Mute:</b> /mute, /tmute, /dmute, /smute and /unmute use the same syntax. "
        "Unmute replaces a member's restrictions with the group defaults. Moderation requires a supergroup.\n"
        "<b>Kick:</b> /kick, /dkick and /skick remove a member but allow rejoining; "
        "a failed second step can leave the member banned.\n\n"
        "<b>Messages:</b> reply with /del to delete, /pin to pin silently or /pin loud "
        "to notify; /unpin removes the replied or latest pin.\n\n"
        "All commands also accept a dot prefix, for example <code>.ban</code>. "
        "The actor and bot need restrict-members rights; delete variants also need delete-messages rights. "
        "Reasons are displayed with the result but are not stored or sent to a log channel.\n\n"
        "File Store commands and links are not served in this mode. Voice-chat music is not available yet.")


def _has_right(member, right):
    status = getattr(member, "status", None)
    if status == enums.ChatMemberStatus.OWNER:
        return True
    privileges = getattr(member, "privileges", None)
    return (status == enums.ChatMemberStatus.ADMINISTRATOR and privileges is not None
            and getattr(privileges, right, None) is True)


def _has_restrict(member):
    return _has_right(member, "can_restrict_members")


def _duration(value):
    match = re.fullmatch(r"([1-9][0-9]{0,5})([mhdw])", value.lower(), flags=re.ASCII)
    if not match:
        return None
    seconds = int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]
    return seconds if 300 <= seconds <= 365 * 86400 else None


def _parse_action(message, action):
    """Return a target token, duration, reason and reply, or a usage error."""
    args = list((getattr(message, "command", None) or [])[1:])
    reply = getattr(message, "reply_to_message", None)
    if action in ("dban", "dmute", "dkick") and reply is None:
        return None
    if reply is not None:
        user = getattr(reply, "from_user", None)
        chat = getattr(reply, "chat", None)
        if (user is None or getattr(reply, "sender_chat", None) is not None
                or chat is None or getattr(chat, "id", None) != message.chat.id):
            return None
        token = getattr(user, "id", None)
        if type(token) is not int or not 0 < token < 2**63:
            return None
    else:
        if not args:
            return None
        token = args.pop(0)
        if re.fullmatch(r"[0-9]{1,19}", token, flags=re.ASCII):
            token = int(token)
            if not 0 < token < 2**63:
                return None
        elif not re.fullmatch(r"@[A-Za-z][A-Za-z0-9_]{3,31}", token, flags=re.ASCII):
            return None
    seconds = None
    if action in ("tban", "tmute"):
        if not args or not (seconds := _duration(args.pop(0))):
            return None
    if action in ("unban", "gunban", "unmute") and args:
        return None
    reason = " ".join(args)
    if len(reason) > 300:
        return None
    return token, seconds, reason, reply


async def _moderate(client, message, *, unban=False, action=None):
    action = action or ("unban" if unban else "ban")
    chat = getattr(message, "chat", None)
    actor = getattr(message, "from_user", None)
    if (chat is None or getattr(chat, "type", None) not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
            or type(getattr(chat, "id", None)) is not int or chat.id >= 0
            or actor is None or getattr(actor, "is_bot", False)
            or getattr(message, "sender_chat", None) is not None
            or type(getattr(actor, "id", None)) is not int or actor.id <= 0):
        return await message.reply_text("🔒 A directly identified group admin is required.")
    parsed = _parse_action(message, action)
    if parsed is None:
        return await message.reply_text("⚠️ Invalid target or arguments. Use /help for the command syntax.")
    token, seconds, reason, reply = parsed
    bot_id = getattr(getattr(client, "me", None), "id", None)
    if type(bot_id) is not int or bot_id <= 0:
        return await message.reply_text("🔒 Could not verify the bot's identity. No action was attempted.")
    try:
        actor_member = await client.get_chat_member(chat.id, actor.id)
        bot_member = await client.get_chat_member(chat.id, bot_id)
    except Exception:
        return await message.reply_text("⚠️ Could not verify group permissions. No action was attempted.")
    if not _has_restrict(actor_member) or not _has_restrict(bot_member):
        return await message.reply_text("🔒 Both you and the bot need the group restrict-members permission.")
    if action in ("dban", "sban", "dmute", "smute", "dkick", "skick") and (not _has_right(actor_member, "can_delete_messages")
                                      or not _has_right(bot_member, "can_delete_messages")):
        return await message.reply_text("🔒 Both you and the bot need delete-messages permission for this command.")
    try:
        if isinstance(token, str):
            user = await client.get_users(token)
            target = getattr(user, "id", None)
        else:
            target = token
        if type(target) is not int or not 0 < target < 2**63:
            raise ValueError("invalid target")
        if target in (actor.id, bot_id):
            return await message.reply_text("🔒 Cannot moderate yourself or this bot.")
        target_member = await client.get_chat_member(chat.id, target)
    except Exception:
        return await message.reply_text("⚠️ Could not verify the target in this group. No action was attempted.")
    undo = action in ("unban", "gunban")
    mute = action in ("mute", "tmute", "dmute", "smute", "unmute")
    kick = action in ("kick", "dkick", "skick")
    if chat.type != enums.ChatType.SUPERGROUP:
        return await message.reply_text("⚠️ This moderation command requires a supergroup. No action was attempted.")
    status = getattr(target_member, "status", None)
    if undo:
        allowed = (enums.ChatMemberStatus.BANNED,)
    elif action == "unmute":
        allowed = (enums.ChatMemberStatus.RESTRICTED,)
    else:
        allowed = (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED)
    if status not in allowed:
        return await message.reply_text("🔒 Target status is not eligible for this action. Admins and owners are protected.")
    permissions = None
    if action == "unmute":
        try:
            permissions = getattr(await client.get_chat(chat.id), "permissions", None)
            if not isinstance(permissions, ChatPermissions):
                raise ValueError("default permissions unavailable")
        except Exception:
            return await message.reply_text("⚠️ Could not verify group default permissions. No action was attempted.")
    try:
        if undo:
            result = await client.unban_chat_member(chat.id, target)
        elif mute:
            kw = {"until_date": datetime.now(timezone.utc) + timedelta(seconds=seconds)} if seconds is not None else {}
            result = await client.restrict_chat_member(chat.id, target, permissions if action == "unmute" else ChatPermissions(), **kw)
        elif seconds is not None:
            result = await client.ban_chat_member(chat.id, target, until_date=datetime.now(timezone.utc) + timedelta(seconds=seconds))
        else:
            result = await client.ban_chat_member(chat.id, target)
        if result is False:
            raise RuntimeError("unconfirmed action")
    except Exception:
        log.warning("Group moderation result could not be confirmed.")
        return await message.reply_text("⚠️ Telegram did not confirm the result. Check the member's status before retrying; no deletion was attempted.")
    if kick:
        try:
            if await client.unban_chat_member(chat.id, target) is False:
                raise RuntimeError("unconfirmed unban")
        except Exception:
            log.warning("Group kick unban result could not be confirmed.")
            return await message.reply_text("⚠️ The removal was confirmed, but unban could not be confirmed; the user may remain banned. Check Telegram before retrying. No deletion was attempted.")
    silent = action in ("sban", "smute", "skick")
    if action in ("dban", "sban", "dmute", "smute", "dkick", "skick"):
        incomplete = False
        for item in ([reply] if reply is not None else []) + ([message] if silent else []):
            try:
                if await item.delete() is False:
                    incomplete = True
            except Exception:
                incomplete = True
        if incomplete:
            # The command itself may already be deleted, so do not reply to it.
            try:
                await client.send_message(chat.id, "⚠️ Moderation was confirmed, but some message deletions could not be confirmed. Check before retrying.")
            except Exception:
                log.warning("Could not deliver the partial moderation result.")
            return
        if silent:
            return
    label = ("✅ Unbanned" if undo else "🔊 Restored group defaults for" if action == "unmute"
             else "🔇 Muted" if mute else "👢 Kicked" if kick else "🚫 Banned")
    text = f"{label} user <code>{target}</code>."
    if seconds is not None:
        text += f" Duration: <code>{seconds // 60} minutes</code>."
    if reason:
        text += f"\nReason: {html.escape(reason)}"
    return await message.reply_text(text)


async def _message_action(client, message, action):
    chat, actor = getattr(message, "chat", None), getattr(message, "from_user", None)
    if (chat is None or getattr(chat, "type", None) not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
            or type(getattr(chat, "id", None)) is not int or chat.id >= 0
            or actor is None or getattr(actor, "is_bot", False)
            or getattr(message, "sender_chat", None) is not None
            or type(getattr(actor, "id", None)) is not int or actor.id <= 0):
        return await message.reply_text("🔒 A directly identified group admin is required.")
    args = (getattr(message, "command", None) or [])[1:]
    reply = getattr(message, "reply_to_message", None)
    reply_chat = getattr(reply, "chat", None)
    reply_id = getattr(reply, "id", None)
    if ((reply is not None and (type(reply_id) is not int or reply_id <= 0
                               or reply_chat is None or getattr(reply_chat, "id", None) != chat.id))
            or (action in ("del", "pin") and reply is None)
            or (args and (action != "pin" or args != ["loud"]))):
        return await message.reply_text("⚠️ Reply to a group message. Use /pin loud to notify members.")
    bot_id = getattr(getattr(client, "me", None), "id", None)
    if type(bot_id) is not int or bot_id <= 0:
        return await message.reply_text("🔒 Could not verify the bot's identity. No action was attempted.")
    right = "can_delete_messages" if action == "del" else "can_pin_messages"
    try:
        actor_member = await client.get_chat_member(chat.id, actor.id)
        bot_member = await client.get_chat_member(chat.id, bot_id)
    except Exception:
        return await message.reply_text("⚠️ Could not verify group permissions. No action was attempted.")
    if not _has_right(actor_member, right) or not _has_right(bot_member, right):
        return await message.reply_text("🔒 Both you and the bot need the relevant Telegram admin permission.")
    try:
        if action == "del":
            result = await reply.delete()
        elif action == "pin":
            result = await client.pin_chat_message(chat.id, reply_id, disable_notification=not args)
        else:
            result = await client.unpin_chat_message(chat.id, reply_id if reply else 0)
        if result is False:
            raise RuntimeError("unconfirmed action")
    except Exception:
        log.warning("Group message action could not be confirmed.")
        return await message.reply_text("⚠️ Telegram did not confirm the result. Check the group before retrying.")
    if action == "del":
        try:
            if await message.delete() is False:
                raise RuntimeError("unconfirmed deletion")
        except Exception:
            try:
                await client.send_message(chat.id, "⚠️ The replied message was deleted, but command deletion could not be confirmed.")
            except Exception:
                log.warning("Could not deliver the partial delete result.")
            return
        return
    return await message.reply_text("✅ Message pinned." if action == "pin" else "✅ Message unpinned.")


def setup_group_profile(app):
    @app.on_message(filters.command("start") & filters.private)
    async def start(client, message):
        await message.reply_text(HELP)

    @app.on_message(filters.command(["help", "ghelp"], prefixes=["/", "."]) & filters.group)
    async def help_group(client, message):
        await message.reply_text(HELP)

    @app.on_message(filters.command("groupid", prefixes=["/", "."]) & filters.group)
    async def group_id(client, message):
        title = html.escape(str(getattr(message.chat, "title", None) or "Group"))
        await message.reply_text(f"<b>{title}</b>\nGroup ID: <code>{message.chat.id}</code>")

    @app.on_message(filters.command(["ban", "gban", "tban", "dban", "sban", "unban", "gunban", "mute", "tmute", "dmute", "smute", "unmute", "kick", "dkick", "skick"], prefixes=["/", "."]) & filters.group)
    async def moderation(client, message):
        await _moderate(client, message, action=message.command[0].lower())

    @app.on_message(filters.command(["del", "pin", "unpin"], prefixes=["/", "."]) & filters.group)
    async def message_actions(client, message):
        await _message_action(client, message, message.command[0].lower())


async def set_group_commands(app, owner_id, worker_db):
    """Best-effort menu sync; unknown old per-chat scopes may still exist."""
    commands = [BotCommand(name, description) for name, description in GROUP_COMMANDS]
    try:
        await app.set_bot_commands(commands, scope=BotCommandScopeDefault())
        admins = {owner_id, *(await worker_db.get_all_admins())}
        for uid in list(admins)[:30]:
            try:
                await app.set_bot_commands(commands, scope=BotCommandScopeChat(uid))
            except Exception:
                log.warning("Group profile command menu update could not be confirmed.")
    except Exception:
        log.warning("Group profile command menu update could not be confirmed.")

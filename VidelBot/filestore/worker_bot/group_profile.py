"""Small, stateless group-moderation profile for one clone client.

This deliberately does not import the main bot's chat-only group settings,
filters, warning records or caches. All moderation uses Telegram's live rights.
"""
import html
import logging

from pyrogram import enums, filters
from pyrogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

log = logging.getLogger(__name__)
GROUP_COMMANDS = [
    ("start", "About this group bot"),
    ("ghelp", "Group moderation commands"),
    ("groupid", "Show this group ID"),
    ("gban", "Ban a member: reply or user ID"),
    ("gunban", "Unban a user by ID"),
]
HELP = ("<b>🛡 Group Management</b>\n\n"
        "Add this bot to your group and grant it the restrict members permission. "
        "Group admins with that permission can reply to a member or use "
        "<code>/gban USER_ID</code>; use <code>/gunban USER_ID</code> to unban.\n\n"
        "<code>/groupid</code> shows the group ID. File Store commands and links "
        "are not served in this mode. Voice-chat music is not available yet.")


def _has_restrict(member):
    status = getattr(member, "status", None)
    if status == enums.ChatMemberStatus.OWNER:
        return True
    privileges = getattr(member, "privileges", None)
    return (status == enums.ChatMemberStatus.ADMINISTRATOR and privileges is not None
            and getattr(privileges, "can_restrict_members", None) is True)


def _target_id(message):
    reply = getattr(message, "reply_to_message", None)
    if reply is not None:
        user = getattr(reply, "from_user", None)
        if getattr(reply, "sender_chat", None) is not None or user is None:
            return None
        target = getattr(user, "id", None)
    else:
        command = getattr(message, "command", None) or []
        if len(command) != 2 or not command[1].isascii() or not command[1].isdigit() or len(command[1]) > 20:
            return None
        target = int(command[1])
    return target if type(target) is int and 0 < target < 2**63 else None


async def _moderate(client, message, *, unban=False):
    chat = getattr(message, "chat", None)
    actor = getattr(message, "from_user", None)
    if (chat is None or getattr(chat, "type", None) not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
            or type(getattr(chat, "id", None)) is not int or chat.id >= 0
            or actor is None or getattr(actor, "is_bot", False)
            or getattr(message, "sender_chat", None) is not None
            or type(getattr(actor, "id", None)) is not int or actor.id <= 0):
        return await message.reply_text("🔒 A directly identified group admin is required.")
    target = _target_id(message)
    if target is None:
        command = "/gunban" if unban else "/gban"
        return await message.reply_text(f"Usage: <code>{command} USER_ID</code> or reply to a user.")
    bot_id = getattr(getattr(client, "me", None), "id", None)
    if type(bot_id) is not int or bot_id <= 0 or target in (actor.id, bot_id):
        return await message.reply_text("🔒 Cannot verify identity or moderate yourself or this bot.")
    try:
        actor_member = await client.get_chat_member(chat.id, actor.id)
        bot_member = await client.get_chat_member(chat.id, bot_id)
        target_member = await client.get_chat_member(chat.id, target)
    except Exception:
        return await message.reply_text("⚠️ Could not verify group permissions or target status. No action was attempted.")
    if not _has_restrict(actor_member) or not _has_restrict(bot_member):
        return await message.reply_text("🔒 Both you and the bot need the group restrict-members permission.")
    status = getattr(target_member, "status", None)
    allowed = (enums.ChatMemberStatus.BANNED,) if unban else (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED)
    if status not in allowed:
        return await message.reply_text("🔒 Target status is not eligible for this action. Admins and owners are protected.")
    try:
        if unban:
            await client.unban_chat_member(chat.id, target)
        else:
            await client.ban_chat_member(chat.id, target)
    except Exception:
        log.warning("Group moderation result could not be confirmed.")
        return await message.reply_text("⚠️ Telegram did not confirm the result. Check the member's status before retrying.")
    return await message.reply_text(f"{'✅ Unbanned' if unban else '🚫 Banned'} user <code>{target}</code>.")


def setup_group_profile(app):
    @app.on_message(filters.command("start") & filters.private)
    async def start(client, message):
        await message.reply_text(HELP)

    @app.on_message(filters.command("ghelp") & filters.group)
    async def help_group(client, message):
        await message.reply_text(HELP)

    @app.on_message(filters.command("groupid") & filters.group)
    async def group_id(client, message):
        title = html.escape(str(getattr(message.chat, "title", None) or "Group"))
        await message.reply_text(f"<b>{title}</b>\nGroup ID: <code>{message.chat.id}</code>")

    @app.on_message(filters.command("gban") & filters.group)
    async def ban(client, message):
        await _moderate(client, message)

    @app.on_message(filters.command("gunban") & filters.group)
    async def unban(client, message):
        await _moderate(client, message, unban=True)


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

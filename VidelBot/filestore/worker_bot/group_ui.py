"""Public, read-only group-clone guide and bot-permission check.

The guide contains no owner settings or actions. Callback data chooses only a
static page; moderation always goes through its existing permission checks.
"""
import asyncio
import logging
import re

from pyrogram import enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

log = logging.getLogger(__name__)
PAGES = {
    "home": (
        "<b>🛡 Group Management</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "A practical toolkit for your group. Choose a section below for commands and limits.\n\n"
        "<b>Get started</b>\n"
        "① Add this bot to your group and grant the rights you need.\n"
        "② Run <code>/gcheck</code> in the group to inspect its current bot permissions.\n"
        "③ Use <code>/help</code> in the group to return to this guide.\n\n"
        "File Store commands and links are not served in this mode. Music voice-chat playback is unavailable."
    ),
    "moderate": (
        "<b>🛡 Moderation</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Reply or use a numeric ID / resolvable username:\n"
        "<code>/ban USER [reason]</code> · <code>/unban USER</code>\n"
        "<code>/mute USER [reason]</code> · <code>/unmute USER</code>\n"
        "<code>/kick USER [reason]</code> (also <code>/gban</code> and <code>/gunban</code> aliases).\n\n"
        "Timed: <code>/tban USER 3d</code> or <code>/tmute USER 3d</code>; "
        "5 minutes–365 days, using m, h, d or w. Reply forms omit USER.\n"
        "Reply with <code>/dban</code>, <code>/dmute</code> or <code>/dkick</code> to delete the replied message after the action. "
        "<code>/sban</code>, <code>/smute</code> and <code>/skick</code> also attempt to delete the command.\n\n"
        "Supergroup required. Actor and bot need restrict-members rights; delete variants also need delete rights. "
        "Admins and owners are protected. A failed kick's unban may leave the user banned. "
        "Muting overwrites existing restrictions; unmute applies group defaults, not previous custom restrictions. "
        "Reasons appear in non-silent results but are not stored or sent to a log channel.\n\n"
        "<b>Warnings:</b> <code>/warn USER [reason]</code>, <code>/warns [USER]</code>, "
        "<code>/rmwarn USER</code>, <code>/resetwarn USER</code> (or reply). "
        "Members can view their own count; checking someone else and all changes require a group admin with restrict-members rights. "
        "Warnings are counters only; no automatic punishment is applied."
    ),
    "messages": (
        "<b>📌 Messages &amp; pins</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Reply with <code>/del</code> to delete a message.\n"
        "Reply with <code>/pin</code> for a silent pin or <code>/pin loud</code> to notify members.\n"
        "Use <code>/unpin</code> on a reply or without one for the latest pin.\n\n"
        "Both the actor and bot need the appropriate delete or pin permission. "
        "If Telegram cannot confirm an action, check the group before retrying."
    ),
    "welcome": (
        "<b>👋 Rules &amp; welcomes</b>\n━━━━━━━━━━━━━━━━━━\n"
        "<code>/rules</code> shows the rules in the group.\n"
        "Admins: <code>/setrules TEXT</code> · <code>/resetrules</code>\n\n"
        "<code>/welcome</code> previews settings for admins.\n"
        "<code>/setwelcome TEXT</code> · <code>/welcome on</code> · <code>/welcome off</code> · <code>/resetwelcome</code>\n"
        "Welcomes start off. Templates are plain text with <code>{first}</code>, <code>{id}</code> and <code>{chatname}</code>.\n\n"
        "Changing settings requires a directly identified admin with change-info permission."
    ),
    "notes": (
        "<b>📝 Group notes</b>\n━━━━━━━━━━━━━━━━━━\n"
        "<code>/save guide Some useful text</code>\n"
        "<code>/get guide</code> · <code>/notes</code> · <code>/clear guide</code>\n\n"
        "Notes are plain text, visible to everyone in the group. Never store secrets. "
        "Admins with change-info permission can save or clear them; members can read them. "
        "There are at most 50 notes per group, with 1,000 characters per note. "
        "Names use letters, digits or underscores; attachments and private notes are not supported."
    ),
    "setup": (
        "<b>🔎 Setup &amp; troubleshooting</b>\n━━━━━━━━━━━━━━━━━━\n"
        "Run <code>/gcheck</code> in the group for a read-only check of this bot's reported role and rights. "
        "Run <code>/groupid</code> for the group ID.\n\n"
        "Telegram is the final authority: actor rights, target status, bot privacy, update delivery "
        "and permissions can change or prevent an action. A check cannot guarantee success.\n\n"
        "Commands also accept a dot prefix, for example <code>.ban</code>. "
        "Music, automatic warning punishments, filters and antiflood are not available in this worker yet."
    ),
}
BUTTONS = (("home", "🏠 Home"), ("moderate", "🛡 Moderation"), ("messages", "📌 Messages"),
           ("welcome", "👋 Welcome"), ("notes", "📝 Notes"), ("setup", "🔎 Setup"))
_CALLBACK = re.compile(r"cgh:(home|moderate|messages|welcome|notes|setup)\Z", re.ASCII)


def keyboard(active):
    buttons = [InlineKeyboardButton(("• " if key == active else "") + label, callback_data="cgh:" + key)
               for key, label in BUTTONS]
    return InlineKeyboardMarkup([buttons[:2], buttons[2:4], buttons[4:]])


async def show_guide(message):
    return await message.reply_text(PAGES["home"], parse_mode=enums.ParseMode.HTML, reply_markup=keyboard("home"))


async def guide_callback(client, query):
    data = getattr(query, "data", None)
    match = _CALLBACK.fullmatch(data) if isinstance(data, str) else None
    msg = getattr(query, "message", None)
    chat = getattr(msg, "chat", None)
    actor = getattr(query, "from_user", None)
    bot_id = getattr(getattr(client, "me", None), "id", None)
    author = getattr(getattr(msg, "from_user", None), "id", None)
    chat_id = getattr(chat, "id", None)
    actor_id = getattr(actor, "id", None)
    kind = getattr(chat, "type", None)
    allowed_chat = ((kind == enums.ChatType.PRIVATE and type(chat_id) is int and chat_id > 0 and chat_id == actor_id)
                    or (kind in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP) and type(chat_id) is int and chat_id < 0))
    if (not match or not allowed_chat or type(bot_id) is not int or bot_id <= 0
            or type(author) is not int or author != bot_id
            or type(actor_id) is not int or actor_id <= 0 or getattr(actor, "is_bot", False)):
        return await query.answer("This guide is unavailable here.", show_alert=True)
    page = match.group(1)
    try:
        await msg.edit_text(PAGES[page], parse_mode=enums.ParseMode.HTML, reply_markup=keyboard(page))
    except MessageNotModified:
        pass
    except Exception:
        log.warning("Group guide edit could not be confirmed.")
        return await query.answer("Could not update the guide. Try /help again.", show_alert=True)
    return await query.answer()


_RIGHTS = (("Restrict members", "can_restrict_members"), ("Delete messages", "can_delete_messages"),
           ("Pin messages", "can_pin_messages"), ("Change group info", "can_change_info"))
_ROLES = {enums.ChatMemberStatus.OWNER: "Owner", enums.ChatMemberStatus.ADMINISTRATOR: "Administrator",
          enums.ChatMemberStatus.MEMBER: "Member", enums.ChatMemberStatus.RESTRICTED: "Restricted",
          enums.ChatMemberStatus.LEFT: "Left", enums.ChatMemberStatus.BANNED: "Banned"}


def _right(member, right):
    role = getattr(member, "status", None)
    if role == enums.ChatMemberStatus.OWNER:
        return "✅ Granted"
    if role == enums.ChatMemberStatus.ADMINISTRATOR:
        value = getattr(getattr(member, "privileges", None), right, None)
        return "✅ Granted" if value is True else "❌ Missing" if value is False else "❔ Unknown"
    if role in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED,
                enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
        return "❌ Missing"
    return "❔ Unknown"


async def group_check(client, message):
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if (getattr(chat, "type", None) not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)
            or type(chat_id) is not int or chat_id >= 0):
        return
    if len(getattr(message, "command", None) or []) != 1:
        return await message.reply_text("Usage: /gcheck")
    bot_id = getattr(getattr(client, "me", None), "id", None)
    if type(bot_id) is not int or bot_id <= 0:
        return await message.reply_text("⚠️ Bot identity could not be verified. No settings were changed.")
    try:
        member = await asyncio.wait_for(client.get_chat_member(chat_id, bot_id), timeout=8)
    except Exception:
        return await message.reply_text("⚠️ Telegram could not confirm the bot's group permissions. No settings were changed.")
    role = next((name for status, name in _ROLES.items() if status == getattr(member, "status", None)), "Unknown")
    rows = "\n".join(f"{label}: {_right(member, key)}" for label, key in _RIGHTS)
    group_type = "Supergroup" if chat.type == enums.ChatType.SUPERGROUP else "Basic group"
    restriction = ("Supergroup type supports moderation; rights are still required." if chat.type == enums.ChatType.SUPERGROUP
                   else "⚠️ Ban, mute and kick commands require a supergroup.")
    text = (f"<b>🔎 Bot setup check</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"Group: <b>{group_type}</b>\nBot role: <b>{role}</b>\n\n{rows}\n\n{restriction}\n\n"
            "Read-only snapshot: actor and target rights, bot privacy and update delivery were not checked. "
            "Rights can change; Telegram decides each action. No settings were changed.")
    return await message.reply_text(text, parse_mode=enums.ParseMode.HTML)


def setup_group_ui(app):
    @app.on_callback_query(filters.regex(r"^cgh:(home|moderate|messages|welcome|notes|setup)$"))
    async def on_guide(client, query):
        await guide_callback(client, query)

    @app.on_message(filters.command("gcheck", prefixes=["/", "."]) & filters.group)
    async def on_check(client, message):
        await group_check(client, message)

import html
import os
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from database.db import db
from config import FREE_LIMIT_DAILY
from saver.strings import COMMANDS_TXT
from core.ui import smart_edit
# ======================================================
# /settings - Enhanced Professional Settings Menu
# ======================================================
def saver_settings_view(user_id: int, is_premium) -> tuple:
    """Unified Settings Dashboard (original saver layout + encoder & clone-bot entries)."""
    from core.texts import SETTINGS_HUB
    badge = "💎 Premium Member" if is_premium else "👤 Standard User"
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 Command List", callback_data="cmd_list_btn")],
        [InlineKeyboardButton("📊 Usage Stats", callback_data="user_stats_btn")],
        [InlineKeyboardButton("🗑 Dump Chat Settings", callback_data="dump_chat_btn")],
        [InlineKeyboardButton("🖼 Manage Thumbnail", callback_data="thumb_btn"),
         InlineKeyboardButton("📝 Edit Caption", callback_data="caption_btn")],
        [InlineKeyboardButton("🎬 Encoder Settings", callback_data="hub_enc"),
         InlineKeyboardButton("⚡ My Clone Bots", callback_data="my_bots")],
        [InlineKeyboardButton("⬅️ Return to Home", callback_data="start_btn"),
         InlineKeyboardButton("❌ Close", callback_data="close_btn")],
    ])
    return SETTINGS_HUB.format(badge=badge, user_id=user_id), buttons


# ======================================================
# /commands - Direct Access to Commands List
# ======================================================
@Client.on_message(filters.command(["commands", "cmd"]) & filters.private)
async def direct_commands(client: Client, message: Message):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Open Settings", callback_data="settings_back_btn"), InlineKeyboardButton("❌ Close", callback_data="close_btn")]
    ])
    from core import rich
    await rich.reply(message, commands_doc(), reply_markup=buttons)


def commands_doc():
    """Every private-chat command, one table per section (rich) / ◈ rows (classic)."""
    import re as _re
    from core.commands import USER_COMMANDS, user_sections
    from core.rich import Doc
    doc = Doc("📜", "All commands", f"{len(USER_COMMANDS)} commands · tap one to use it")
    for emoji, title, cmds in user_sections():
        doc.h(emoji, title)
        # descriptions start with their own emoji – the section heading already has one
        doc.table([(f"/{c}", _re.sub(r"^\W+\s*", "", d) or d) for c, d in cmds], header=("Command", "What it does"))
    doc.footer("Admins get their extra commands in the bot menu.")
    return doc
# ======================================================
# /setchat - Set or Clear Dump Chat
# ======================================================
@Client.on_message(filters.command("setchat") & filters.private)
async def set_dump_chat(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>🗑 Set Dump Chat</b>\n\n"
            "<b>Usage:</b>\n"
            "<code>/setchat &lt;chat_id&gt;</code> → Set forward destination\n"
            "<code>/setchat clear</code> → Remove dump chat\n\n"
            "<i>Example: /setchat -1001234567890</i>",
            parse_mode=enums.ParseMode.HTML
        )
    arg = message.command[1].strip().lower()
    if arg == "clear":
        await db.set_dump_chat(user_id, None)
        return await message.reply_text("✅ <b>Dump Chat Cleared Successfully</b>", parse_mode=enums.ParseMode.HTML)
    try:
        chat_id = int(arg)
        try:
            chat = await client.get_chat(chat_id)
            chat_title = chat.title or "Private Chat"
        except Exception:
            chat_title = "Unknown Chat"
        await db.set_dump_chat(user_id, chat_id)
        await message.reply_text(
            f"✅ <b>Dump Chat Set Successfully</b>\n\n"
            f"<b>Forward To:</b> <code>{chat_id}</code>\n"
            f"<b>Title:</b> {chat_title}",
            parse_mode=enums.ParseMode.HTML
        )
    except ValueError:
        await message.reply_text("❌ <b>Invalid Chat ID</b>\n\n<i>Must be a number (e.g., -1001234567890)</i>", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"❌ <b>Unable to Access Chat</b>\n<i>{html.escape(str(e))}</i>", parse_mode=enums.ParseMode.HTML)
# ======================================================
# Callbacks - Full Settings Navigation
# ======================================================
@Client.on_callback_query(filters.regex("^(cmd_list_btn|dump_chat_btn|thumb_btn|caption_btn|user_stats_btn|settings_back_btn|close_btn)$"))
async def settings_callbacks(client: Client, callback_query: CallbackQuery):
    data = callback_query.data
    user_id = callback_query.from_user.id
   
    # Common back/close buttons
    back_close = [[InlineKeyboardButton("⬅️ Back", callback_data="settings_back_btn"), InlineKeyboardButton("❌ Close", callback_data="close_btn")]]
    if data == "cmd_list_btn":
        await smart_edit(callback_query.message, COMMANDS_TXT, InlineKeyboardMarkup(back_close))
    elif data == "dump_chat_btn":
        current = await db.get_dump_chat(user_id)
        if current:
            try:
                chat = await client.get_chat(current)
                title = chat.title or "Private Chat"
            except Exception:
                title = "Unknown (Inaccessible)"
            text = (
                f"<b>🗑 Current Dump Chat</b>\n\n"
                f"<b>Chat ID:</b> <code>{current}</code>\n"
                f"<b>Title:</b> {html.escape(title)}\n\n"
                "<i>All saved files are forwarded here.</i>\n"
                "<i>Use /setchat to change or clear.</i>"
            )
        else:
            text = (
                "<b>🗑 No Dump Chat Set</b>\n\n"
                "<i>Saved files appear only in this chat.</i>\n"
                "<i>Use /setchat &lt;chat_id&gt; to enable forwarding.</i>"
            )
        await smart_edit(callback_query.message, text, InlineKeyboardMarkup(back_close))
    elif data == "thumb_btn":
        thumb = await db.get_thumbnail(user_id)
        if thumb:
            await callback_query.message.reply_photo(
                thumb,
                caption="<b>🖼 Your Current Custom Thumbnail</b>\n\n<i>Send a new photo to update • /del_thumb to remove</i>",
                parse_mode=enums.ParseMode.HTML
            )
            await callback_query.answer("Thumbnail preview sent below 👇")
        else:
            await smart_edit(callback_query.message, "<b>🖼 No Custom Thumbnail Set</b>\n\n"
                "<i>Reply to a photo with /set_thumb to set a default thumbnail.</i>", InlineKeyboardMarkup(back_close))
    elif data == "caption_btn":
        caption = await db.get_caption(user_id)
        if caption:
            try:
                preview = caption.format(filename="Video_File_2024.mp4", size="1.2 GB")
            except (KeyError, IndexError, ValueError):
                preview = caption
            text = (
                f"<b>📝 Current Custom Caption</b>\n\n"
                f"<code>{html.escape(caption)}</code>\n\n"
                f"<b>Preview:</b>\n{preview}\n\n"
                "<i>Placeholders: {filename}, {size}</i>\n"
                "<i>/set_caption &lt;text&gt; to change • /del_caption to remove</i>"
            )
        else:
            text = (
                "<b>📝 No Custom Caption Set</b>\n\n"
                "<i>Use /set_caption &lt;text&gt; to set one.</i>\n"
                "<i>Supports {filename} and {size} placeholders.</i>"
            )
        await smart_edit(callback_query.message, text, InlineKeyboardMarkup(back_close))
    elif data == "user_stats_btn":
        # Fetch real stats from DB
        is_premium = await db.check_premium(user_id)
        user_data = await db.col.find_one({'id': int(user_id)}) or {}
       
        if is_premium:
            limit_text = "♾️ Unlimited"
            usage_text = "Ignored (Premium)"
        else:
            # Free user logic
            daily_limit = FREE_LIMIT_DAILY
            used = user_data.get('daily_usage', 0)
            limit_text = f"{daily_limit} Files / 24h"
            usage_text = f"{used} / {daily_limit}"
        text = (
            f"<b>📊 My Usage Statistics</b>\n\n"
            f"<b>Plan:</b> {'💎 Premium' if is_premium else '👤 Free'}\n"
            f"<b>Daily Limit:</b> <code>{limit_text}</code>\n"
            f"<b>Today's Usage:</b> <code>{usage_text}</code>\n\n"
            f"<i>Upgrade to Premium for unlimited downloads!</i>"
        )
        await smart_edit(callback_query.message, text, InlineKeyboardMarkup(back_close))
    elif data == "settings_back_btn":
        is_premium = await db.check_premium(user_id)
        text, buttons = saver_settings_view(user_id, is_premium)
        await smart_edit(callback_query.message, text, buttons)
    elif data == "close_btn":
        await callback_query.message.delete()
    await callback_query.answer()

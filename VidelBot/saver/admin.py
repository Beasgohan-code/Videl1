
from pyrogram import Client, filters
from pyrogram.types import Message
from database.db import db
from config import ADMINS

@Client.on_message(filters.command("set_dump") & filters.user(ADMINS))
async def set_dump(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text("**Usage:** `/set_dump user_id chat_id`")
    try:
        user_id = int(message.command[1])
        chat_id = int(message.command[2])
        await db.set_dump_chat(user_id, chat_id)
        await message.reply_text(f"**Dump chat set for user {user_id}.**")
    except Exception:
        await message.reply_text("Error setting dump chat.")

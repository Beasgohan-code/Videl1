"""
Purge, Delete & Auto-Delete (Media / Messages) module for AoiMusic.
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
"""

from __future__ import annotations

import asyncio
from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import get_chat_settings, set_chat_setting
from AoiMusic.utils.manage_helpers import (
    check_admin_and_bot,
    esc,
    is_user_admin,
    mgmt_reply,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "Purge"
__HELP__ = f"""
{pe('TRASH_DELETE')} <b>ᴘᴜʀɢᴇ &amp; ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ</b>

<code>/del</code> — Delete the replied message
<code>/purge</code> — Delete all messages from replied message to current

<b>• ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ᴍᴇᴅɪᴀ</b>
<code>/mediadelete</code> &lt;on|off&gt; — Auto-delete media in group
<code>/setmediadelay</code> &lt;seconds&gt; — Set delay (5–3600s, default 60s)
<code>/mediastatus</code> — Check media auto-delete settings

<b>• ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ᴍᴇssᴀɢᴇs</b>
<code>/msgdelete</code> &lt;on|off&gt; — Auto-delete all non-admin messages
<code>/setmsgdelay</code> &lt;seconds&gt; — Set delay (5–3600s, default 60s)
<code>/msgstatus</code> — Check message auto-delete settings
"""


async def _delayed_delete(client, chat_id: int, msg_id: int, delay: int) -> None:
    try:
        await asyncio.sleep(max(5, int(delay)))
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass


@app.on_message(filters.command("del") & filters.group & ~BANNED_USERS)
async def del_message_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if not message.reply_to_message:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ᴅᴇʟᴇᴛᴇ ɪᴛ.</blockquote>",
        )
    try:
        await message.reply_to_message.delete()
        await message.delete()
    except Exception as exc:
        await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴅᴇʟᴇᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )


@app.on_message(filters.command("purge") & filters.group & ~BANNED_USERS)
async def purge_messages_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if not message.reply_to_message:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴛʜᴇ sᴛᴀʀᴛ ᴍᴇssᴀɢᴇ ᴛᴏ ᴘᴜʀɢᴇ ғʀᴏᴍ.</blockquote>",
        )
    start_id = message.reply_to_message.id
    end_id = message.id
    ids = list(range(start_id, end_id + 1))
    deleted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i : i + 100]
        try:
            await client.delete_messages(chat_id, chunk)
            deleted += len(chunk)
        except Exception:
            pass
    notice = await mgmt_reply(
        message,
        f"<blockquote>{pe('TRASH_DELETE')} <b>ᴘᴜʀɢᴇ ᴄᴏᴍᴘʟᴇᴛᴇᴅ</b> — <code>{deleted}</code> ᴍᴇssᴀɢᴇs ᴄʟᴇᴀʀᴇᴅ.</blockquote>",
    )
    if notice:
        asyncio.create_task(_delayed_delete(client, chat_id, notice.id, 5))


@app.on_message(filters.command("mediadelete") & filters.group & ~BANNED_USERS)
async def mediadelete_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/mediadelete on</code> ᴏʀ <code>off</code></blockquote>",
        )
    state = message.command[1].lower() == "on"
    await set_chat_setting(chat_id, "mediadelete", state)
    status = "ᴇɴᴀʙʟᴇᴅ" if state else "ᴅɪsᴀʙʟᴇᴅ"
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴍᴇᴅɪᴀ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ {status}.</b></blockquote>",
    )


@app.on_message(filters.command("setmediadelay") & filters.group & ~BANNED_USERS)
async def setmediadelay_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2 or not message.command[1].isdigit():
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/setmediadelay 60</code> (5–3600s)</blockquote>",
        )
    sec = max(5, min(3600, int(message.command[1])))
    await set_chat_setting(chat_id, "mediadelay", sec)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('TIMER_COOLDOWN')} <b>ᴍᴇᴅɪᴀ ᴅᴇʟᴇᴛᴇ ᴅᴇʟᴀʏ sᴇᴛ ᴛᴏ:</b> <code>{sec}s</code></blockquote>",
    )


@app.on_message(filters.command("mediastatus") & filters.group & ~BANNED_USERS)
async def mediastatus_cmd(client, message: Message):
    s = await get_chat_settings(message.chat.id)
    on = "ᴏɴ" if s.get("mediadelete") else "ᴏғғ"
    delay = int(s.get("mediadelay", 60))
    await mgmt_reply(
        message,
        f"<blockquote>{pe('IMAGE_PHOTO')} <b>ᴍᴇᴅɪᴀ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ sᴛᴀᴛᴜs</b>\n"
        f"• <b>sᴛᴀᴛᴇ:</b> <code>{on}</code>\n"
        f"• <b>ᴅᴇʟᴀʏ:</b> <code>{delay}s</code></blockquote>",
    )


@app.on_message(filters.command("msgdelete") & filters.group & ~BANNED_USERS)
async def msgdelete_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/msgdelete on</code> ᴏʀ <code>off</code></blockquote>",
        )
    state = message.command[1].lower() == "on"
    await set_chat_setting(chat_id, "msgdelete", state)
    status = "ᴇɴᴀʙʟᴇᴅ" if state else "ᴅɪsᴀʙʟᴇᴅ"
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴍᴇssᴀɢᴇ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ {status}.</b></blockquote>",
    )


@app.on_message(filters.command("setmsgdelay") & filters.group & ~BANNED_USERS)
async def setmsgdelay_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2 or not message.command[1].isdigit():
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/setmsgdelay 60</code> (5–3600s)</blockquote>",
        )
    sec = max(5, min(3600, int(message.command[1])))
    await set_chat_setting(chat_id, "msgdelay", sec)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('TIMER_COOLDOWN')} <b>ᴍᴇssᴀɢᴇ ᴅᴇʟᴇᴛᴇ ᴅᴇʟᴀʏ sᴇᴛ ᴛᴏ:</b> <code>{sec}s</code></blockquote>",
    )


@app.on_message(filters.command("msgstatus") & filters.group & ~BANNED_USERS)
async def msgstatus_cmd(client, message: Message):
    s = await get_chat_settings(message.chat.id)
    on = "ᴏɴ" if s.get("msgdelete") else "ᴏғғ"
    delay = int(s.get("msgdelay", 60))
    await mgmt_reply(
        message,
        f"<blockquote>{pe('TRASH_DELETE')} <b>ᴍᴇssᴀɢᴇ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ sᴛᴀᴛᴜs</b>\n"
        f"• <b>sᴛᴀᴛᴇ:</b> <code>{on}</code>\n"
        f"• <b>ᴅᴇʟᴀʏ:</b> <code>{delay}s</code></blockquote>",
    )


@app.on_message(filters.group & ~filters.service, group=16)
async def auto_delete_watcher(client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    s = await get_chat_settings(chat_id)
    media_on = bool(s.get("mediadelete"))
    msg_on = bool(s.get("msgdelete"))
    if not media_on and not msg_on:
        return
    if await is_user_admin(client, chat_id, message.from_user.id):
        return
    is_media = any(
        [message.photo, message.video, message.animation, message.sticker, message.document, message.video_note]
    )
    if media_on and is_media:
        asyncio.create_task(_delayed_delete(client, chat_id, message.id, int(s.get("mediadelay", 60))))
    elif msg_on:
        asyncio.create_task(_delayed_delete(client, chat_id, message.id, int(s.get("msgdelay", 60))))

"""
AFK (Away From Keyboard) module for AoiMusic.
Supports `/afk [reason]` and `brb [reason]`, auto-clears on return, and
notifies users who mention or reply to an AFK user.
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
"""

from __future__ import annotations

import time
from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import clear_afk, get_afk, set_afk
from AoiMusic.utils.manage_helpers import (
    esc,
    format_duration,
    mgmt_reply,
    user_mention,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "AFK"
__HELP__ = f"""
{pe('CLOCK_DURATION')} <b>ᴀғᴋ sᴛᴀᴛᴜs</b>

<code>/afk</code> [reason] — Mark yourself as AFK
<code>brb</code> [reason] — Shortcut to go AFK without slash

<i>When anyone replies to you or mentions you, the bot will inform them how long you've been away. Sending any message in a group automatically ends your AFK.</i>
"""


@app.on_message(
    (filters.command("afk") | filters.regex(r"(?i)^brb(?:\s+|$)"))
    & ~BANNED_USERS,
    group=3,
)
async def set_afk_cmd(client, message: Message):
    if not message.from_user:
        return
    parts = (message.text or "").split(None, 1)
    reason = parts[1].strip() if len(parts) > 1 else ""
    await set_afk(message.from_user.id, reason)
    reason_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CLOCK_DURATION')} <b>{user_mention(message.from_user)} ɪs ɴᴏᴡ ᴀғᴋ!</b>{reason_line}</blockquote>",
    )


@app.on_message(filters.group & ~filters.service & ~BANNED_USERS, group=22)
async def afk_watcher(client, message: Message):
    if not message.from_user:
        return
    text = (message.text or "").strip()
    # Skip if this message itself is setting AFK
    if text.lower().startswith(("/afk", "brb")):
        return

    # 1. Check if sender was AFK -> welcome back!
    sender_afk = await get_afk(message.from_user.id)
    if sender_afk:
        await clear_afk(message.from_user.id)
        elapsed = format_duration(int(time.time()) - int(sender_afk.get("since", time.time())))
        await mgmt_reply(
            message,
            f"<blockquote>{pe('SPARKLES_AI')} <b>ᴡᴇʟᴄᴏᴍᴇ ʙᴀᴄᴋ, {user_mention(message.from_user)}!</b>\n"
            f"{pe('TIMER_COOLDOWN')} ʏᴏᴜ ᴡᴇʀᴇ ᴀғᴋ ғᴏʀ <code>{elapsed}</code>.</blockquote>",
        )

    # 2. Check if replying to an AFK user
    notified: set[int] = set()
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if target.id != message.from_user.id:
            data = await get_afk(target.id)
            if data:
                notified.add(target.id)
                elapsed = format_duration(int(time.time()) - int(data.get("since", time.time())))
                reason = data.get("reason")
                r_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""
                await mgmt_reply(
                    message,
                    f"<blockquote>{pe('CLOCK_DURATION')} <b>{user_mention(target)} ɪs ᴀғᴋ</b> (<code>{elapsed}</code> ᴀɢᴏ){r_line}</blockquote>",
                )

    # 3. Check @mentions / text_mentions
    for ent in message.entities or []:
        target = None
        if getattr(ent, "user", None):
            target = ent.user
        elif str(ent.type).endswith("MENTION") and message.text:
            uname = message.text[ent.offset : ent.offset + ent.length]
            try:
                target = await client.get_users(uname)
            except Exception:
                target = None
        if target and target.id != message.from_user.id and target.id not in notified:
            data = await get_afk(target.id)
            if data:
                notified.add(target.id)
                elapsed = format_duration(int(time.time()) - int(data.get("since", time.time())))
                reason = data.get("reason")
                r_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""
                await mgmt_reply(
                    message,
                    f"<blockquote>{pe('CLOCK_DURATION')} <b>{user_mention(target)} ɪs ᴀғᴋ</b> (<code>{elapsed}</code> ᴀɢᴏ){r_line}</blockquote>",
                )

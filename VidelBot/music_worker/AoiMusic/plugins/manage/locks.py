"""
Chat Locks & Nightmode management module for AoiMusic.
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
"""

from __future__ import annotations

import re
from pyrogram import filters
from pyrogram.types import ChatPermissions, Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import (
    get_chat_settings,
    get_locks,
    is_approved,
    set_chat_setting,
    set_lock,
)
from AoiMusic.utils.manage_helpers import (
    check_admin_and_bot,
    esc,
    is_user_admin,
    mgmt_reply,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "Locks"
__HELP__ = f"""
{pe('LOCK')} <b>ʟᴏᴄᴋs &amp; ɴɪɢʜᴛᴍᴏᴅᴇ</b>

<code>/lock</code> &lt;type&gt; — Lock a message/media type
<code>/unlock</code> &lt;type&gt; — Unlock a type
<code>/locks</code> — Show active locks in this group
<code>/locktypes</code> — List all supported lock types
<code>/nightmode</code> — Lock group messaging (night mode ON)
<code>/rmnight</code> — Restore group messaging (night mode OFF)
"""

LOCK_TYPES = {
    "all",
    "text",
    "media",
    "photo",
    "video",
    "audio",
    "voice",
    "document",
    "sticker",
    "gif",
    "url",
    "forward",
    "poll",
    "inline",
    "contact",
    "location",
    "command",
}

_URL_RE = re.compile(r"(https?://\S+|t\.me/\S+|www\.\S+)", re.IGNORECASE)


@app.on_message(filters.command("locktypes") & ~BANNED_USERS)
async def locktypes_cmd(client, message: Message):
    items = ", ".join(f"<code>{t}</code>" for t in sorted(LOCK_TYPES))
    await mgmt_reply(
        message,
        f"<blockquote>{pe('LOCK')} <b>ᴀᴠᴀɪʟᴀʙʟᴇ ʟᴏᴄᴋ ᴛʏᴘᴇs</b>\n\n{items}</blockquote>",
    )


@app.on_message(filters.command(["lock", "unlock"]) & filters.group & ~BANNED_USERS)
async def toggle_lock_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/{message.command[0]} &lt;type&gt;</code>\n"
            f"ᴜsᴇ <code>/locktypes</code> ᴛᴏ sᴇᴇ ᴀʟʟ ᴛʏᴘᴇs.</blockquote>",
        )
    state = message.command[0].lower() == "lock"
    targets = [t.lower() for t in message.command[1:] if t.lower() in LOCK_TYPES]
    if not targets:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ɪɴᴠᴀʟɪᴅ ʟᴏᴄᴋ ᴛʏᴘᴇ. ᴜsᴇ <code>/locktypes</code>.</blockquote>",
        )
    for t in targets:
        await set_lock(chat_id, t, state)
    icon = pe("LOCK") if state else pe("UNLOCK")
    word = "ʟᴏᴄᴋᴇᴅ" if state else "ᴜɴʟᴏᴄᴋᴇᴅ"
    joined = ", ".join(f"<code>{t}</code>" for t in targets)
    await mgmt_reply(
        message,
        f"<blockquote>{icon} <b>{word}:</b> {joined}</blockquote>",
    )


@app.on_message(filters.command("locks") & filters.group & ~BANNED_USERS)
async def list_locks_cmd(client, message: Message):
    locks = await get_locks(message.chat.id)
    active = [k for k, v in sorted(locks.items()) if v]
    if not active:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('UNLOCK')} ɴᴏ ʟᴏᴄᴋs ᴀʀᴇ ᴄᴜʀʀᴇɴᴛʟʏ ᴀᴄᴛɪᴠᴇ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</blockquote>",
        )
    body = "\n".join(f"• <code>{k}</code>" for k in active)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('LOCK')} <b>ᴀᴄᴛɪᴠᴇ ʟᴏᴄᴋs</b>\n\n{body}</blockquote>",
    )


@app.on_message(filters.command(["nightmode", "rmnight"]) & filters.group & ~BANNED_USERS)
async def nightmode_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    enable = message.command[0].lower() == "nightmode"
    try:
        if enable:
            await client.set_chat_permissions(chat_id, ChatPermissions())
            await set_chat_setting(chat_id, "nightmode", True)
            await mgmt_reply(
                message,
                f"<blockquote>{pe('LOCK')} <b>ɴɪɢʜᴛᴍᴏᴅᴇ ᴇɴᴀʙʟᴇᴅ</b>\n"
                f"ɢʀᴏᴜᴘ ᴍᴇssᴀɢɪɴɢ ɪs ʟᴏᴄᴋᴇᴅ ᴜɴᴛɪʟ <code>/rmnight</code>.</blockquote>",
            )
        else:
            await client.set_chat_permissions(
                chat_id,
                ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_polls=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                    can_invite_users=True,
                ),
            )
            await set_chat_setting(chat_id, "nightmode", False)
            await mgmt_reply(
                message,
                f"<blockquote>{pe('UNLOCK')} <b>ɴɪɢʜᴛᴍᴏᴅᴇ ᴅɪsᴀʙʟᴇᴅ</b>\n"
                f"ɢʀᴏᴜᴘ ᴍᴇssᴀɢɪɴɢ ʜᴀs ʙᴇᴇɴ ʀᴇsᴛᴏʀᴇᴅ.</blockquote>",
            )
    except Exception as exc:
        await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )


def _matches_lock(message: Message, locks: dict) -> bool:
    if locks.get("all"):
        return True
    if locks.get("sticker") and message.sticker:
        return True
    if locks.get("photo") and message.photo:
        return True
    if locks.get("video") and (message.video or message.video_note):
        return True
    if locks.get("audio") and message.audio:
        return True
    if locks.get("voice") and message.voice:
        return True
    if locks.get("document") and message.document:
        return True
    if locks.get("gif") and message.animation:
        return True
    if locks.get("poll") and message.poll:
        return True
    if locks.get("contact") and message.contact:
        return True
    if locks.get("location") and (message.location or message.venue):
        return True
    if locks.get("inline") and message.via_bot:
        return True
    if locks.get("forward") and (message.forward_date or message.forward_from or message.forward_from_chat):
        return True
    if locks.get("media") and any(
        [message.photo, message.video, message.audio, message.voice, message.document, message.animation, message.sticker, message.video_note]
    ):
        return True
    text = message.text or message.caption or ""
    if locks.get("url") and _URL_RE.search(text):
        return True
    if locks.get("command") and text.startswith("/"):
        return True
    if locks.get("text") and message.text:
        return True
    return False


@app.on_message(filters.group & ~filters.service, group=15)
async def enforce_locks_watcher(client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    locks = await get_locks(chat_id)
    if not any(locks.values()):
        return
    if not _matches_lock(message, locks):
        return
    if await is_user_admin(client, chat_id, message.from_user.id):
        return
    if await is_approved(chat_id, message.from_user.id):
        return
    try:
        await message.delete()
    except Exception:
        pass

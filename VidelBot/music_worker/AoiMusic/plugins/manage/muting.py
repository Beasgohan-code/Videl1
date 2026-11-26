"""
Group User Muting module for AoiMusic (coexists cleanly with VC /mute & /unmute).
When /mute or /unmute is invoked WITH a reply or user argument, this module mutes/unmutes
the user in group chat. When invoked with no args & no reply, vcmute.py handles VC audio.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import ChatPermissions, Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_helpers import (
    check_admin_and_bot,
    esc,
    extract_user_and_reason,
    is_user_admin,
    log_mgmt_action,
    mgmt_reply,
    parse_time,
    user_mention,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "Muting"
__HELP__ = f"""
{pe('VOLUME_MUTE')} <b>ᴜsᴇʀ ᴍᴜᴛɪɴɢ</b>

<code>/mute</code> &lt;user&gt; [reason] — Mute a user in group chat
<code>/tmute</code> / <code>/tempmute</code> &lt;user&gt; &lt;time&gt; [reason] — Temporarily mute (e.g. <code>30m</code>, <code>2h</code>, <code>1d</code>)
<code>/unmute</code> &lt;user&gt; — Unmute a muted user

<i>ɴᴏᴛᴇ: <code>/mute</code> &amp; <code>/unmute</code> ᴡɪᴛʜᴏᴜᴛ ʀᴇᴘʟʏ/ᴜsᴇʀ ᴄᴏɴᴛʀᴏʟ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ ᴀᴜᴅɪᴏ.</i>
"""

UNMUTE_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)


@app.on_message(filters.command("mute") & filters.group & ~BANNED_USERS, group=2)
async def mute_user_cmd(client, message: Message):
    # Let vcmute.py handle bare /mute with no reply & no arguments
    if len(message.command) == 1 and not message.reply_to_message:
        return
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, reason = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ ᴛᴏ ᴍᴜᴛᴇ.</blockquote>",
        )
    if await is_user_admin(client, chat_id, user.id):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('SHIELD_ADMIN')} ɪ ᴄᴀɴɴᴏᴛ ᴍᴜᴛᴇ ᴀɴ ᴀᴅᴍɪɴ ᴏʀ sᴜᴅᴏ ᴜsᴇʀ.</blockquote>",
        )
    try:
        await client.restrict_chat_member(chat_id, user.id, ChatPermissions())
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴍᴜᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    actor = user_mention(message.from_user) if message.from_user else "Admin"
    reason_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""
    text = (
        f"<blockquote>{pe('VOLUME_MUTE')} <b>ᴜsᴇʀ ᴍᴜᴛᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}{reason_line}</blockquote>"
    )
    await mgmt_reply(message, text)
    await log_mgmt_action(client, chat_id, text)


@app.on_message(filters.command(["tmute", "tempmute"]) & filters.group & ~BANNED_USERS)
async def temp_mute_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, rest = await extract_user_and_reason(client, message)
    if not user or not rest:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/tmute @user 30m [reason]</code></blockquote>",
        )
    if await is_user_admin(client, chat_id, user.id):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('SHIELD_ADMIN')} ɪ ᴄᴀɴɴᴏᴛ ᴍᴜᴛᴇ ᴀɴ ᴀᴅᴍɪɴ ᴏʀ sᴜᴅᴏ ᴜsᴇʀ.</blockquote>",
        )
    parts = rest.split(None, 1)
    until, _, label = parse_time(parts[0])
    if not until:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ɪɴᴠᴀʟɪᴅ ᴛɪᴍᴇ. ᴇxᴀᴍᴘʟᴇs: <code>15m</code>, <code>2h</code>, <code>1d</code></blockquote>",
        )
    reason = parts[1].strip() if len(parts) > 1 else ""
    try:
        await client.restrict_chat_member(chat_id, user.id, ChatPermissions(), until_date=until)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴛᴇᴍᴘ-ᴍᴜᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    actor = user_mention(message.from_user) if message.from_user else "Admin"
    reason_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""
    text = (
        f"<blockquote>{pe('TIMER_COOLDOWN')} <b>ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ᴍᴜᴛᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('CLOCK_DURATION')} <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> <code>{label}</code>\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}{reason_line}</blockquote>"
    )
    await mgmt_reply(message, text)
    await log_mgmt_action(client, chat_id, text)


@app.on_message(filters.command("unmute") & filters.group & ~BANNED_USERS, group=2)
async def unmute_user_cmd(client, message: Message):
    # Let vcmute.py handle bare /unmute with no reply & no arguments
    if len(message.command) == 1 and not message.reply_to_message:
        return
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, _ = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ ᴛᴏ ᴜɴᴍᴜᴛᴇ.</blockquote>",
        )
    try:
        await client.restrict_chat_member(chat_id, user.id, UNMUTE_PERMS)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴜɴᴍᴜᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    actor = user_mention(message.from_user) if message.from_user else "Admin"
    text = (
        f"<blockquote>{pe('UNLOCK')} <b>ᴜsᴇʀ ᴜɴᴍᴜᴛᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}</blockquote>"
    )
    await mgmt_reply(message, text)
    await log_mgmt_action(client, chat_id, text)

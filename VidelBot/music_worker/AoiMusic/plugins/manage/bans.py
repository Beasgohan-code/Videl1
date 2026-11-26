"""
Bans, Kicks, Temp-Bans & Remote Sudo Moderation module for AoiMusic.
Uses Kurigram 2.2.27 + Musiciconis premium emojis.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import ChatPermissions, Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.misc import SUDOERS
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

__MODULE__ = "Bans"
__HELP__ = f"""
{pe('BAN_BLOCK')} <b>ʙᴀɴs &amp; ᴋɪᴄᴋs</b>

<code>/ban</code> &lt;user&gt; [reason] — Permanently ban a user
<code>/sban</code> &lt;user&gt; — Silently ban a user (deletes command)
<code>/tban</code> &lt;user&gt; &lt;time&gt; [reason] — Temporarily ban (e.g. <code>2h</code>, <code>3d</code>)
<code>/unban</code> &lt;user&gt; — Unban a user
<code>/kick</code> &lt;user&gt; [reason] — Kick a user (can rejoin)
<code>/kickme</code> — Kick yourself from the group

<b>• ʀᴇᴍᴏᴛᴇ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ (sᴜᴅᴏ)</b>
<code>/rban</code>, <code>/runban</code>, <code>/rkick</code>, <code>/rmute</code>, <code>/runmute</code> &lt;chat_id&gt; &lt;user&gt;
"""


@app.on_message(filters.command(["ban", "sban"]) & filters.group & ~BANNED_USERS)
async def ban_user(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, reason = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ ᴛᴏ ʙᴀɴ.</blockquote>",
        )
    if await is_user_admin(client, chat_id, user.id):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('SHIELD_ADMIN')} ɪ ᴄᴀɴɴᴏᴛ ʙᴀɴ ᴀɴ ᴀᴅᴍɪɴ ᴏʀ sᴜᴅᴏ ᴜsᴇʀ.</blockquote>",
        )
    silent = message.command[0].lower() == "sban"
    try:
        await client.ban_chat_member(chat_id, user.id)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ʙᴀɴ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )

    actor = user_mention(message.from_user) if message.from_user else "Admin"
    reason_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""
    log_text = (
        f"<blockquote>{pe('BAN_BLOCK')} <b>ᴜsᴇʀ ʙᴀɴɴᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}{reason_line}</blockquote>"
    )
    if silent:
        try:
            await message.delete()
        except Exception:
            pass
        if message.reply_to_message:
            try:
                await message.reply_to_message.delete()
            except Exception:
                pass
    else:
        await mgmt_reply(message, log_text)
    await log_mgmt_action(client, chat_id, log_text)


@app.on_message(filters.command("tban") & filters.group & ~BANNED_USERS)
async def temp_ban_user(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, rest = await extract_user_and_reason(client, message)
    if not user or not rest:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/tban @user 2h [reason]</code></blockquote>",
        )
    if await is_user_admin(client, chat_id, user.id):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('SHIELD_ADMIN')} ɪ ᴄᴀɴɴᴏᴛ ʙᴀɴ ᴀɴ ᴀᴅᴍɪɴ ᴏʀ sᴜᴅᴏ ᴜsᴇʀ.</blockquote>",
        )
    parts = rest.split(None, 1)
    until, _, label = parse_time(parts[0])
    if not until:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ɪɴᴠᴀʟɪᴅ ᴛɪᴍᴇ ғᴏʀᴍᴀᴛ. ᴇxᴀᴍᴘʟᴇs: <code>15m</code>, <code>2h</code>, <code>3d</code>, <code>1w</code></blockquote>",
        )
    reason = parts[1].strip() if len(parts) > 1 else ""
    try:
        await client.ban_chat_member(chat_id, user.id, until_date=until)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴛᴇᴍᴘ-ʙᴀɴ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    actor = user_mention(message.from_user) if message.from_user else "Admin"
    reason_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""
    text = (
        f"<blockquote>{pe('TIMER_COOLDOWN')} <b>ᴛᴇᴍᴘᴏʀᴀʀɪʟʏ ʙᴀɴɴᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('CLOCK_DURATION')} <b>ᴅᴜʀᴀᴛɪᴏɴ:</b> <code>{label}</code>\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}{reason_line}</blockquote>"
    )
    await mgmt_reply(message, text)
    await log_mgmt_action(client, chat_id, text)


@app.on_message(filters.command("unban") & filters.group & ~BANNED_USERS)
async def unban_user(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, _ = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ ᴛᴏ ᴜɴʙᴀɴ.</blockquote>",
        )
    try:
        await client.unban_chat_member(chat_id, user.id)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴜɴʙᴀɴ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    actor = user_mention(message.from_user) if message.from_user else "Admin"
    text = (
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴜsᴇʀ ᴜɴʙᴀɴɴᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}</blockquote>"
    )
    await mgmt_reply(message, text)
    await log_mgmt_action(client, chat_id, text)


@app.on_message(filters.command("kick") & filters.group & ~BANNED_USERS)
async def kick_user(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, reason = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ ᴛᴏ ᴋɪᴄᴋ.</blockquote>",
        )
    if await is_user_admin(client, chat_id, user.id):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('SHIELD_ADMIN')} ɪ ᴄᴀɴɴᴏᴛ ᴋɪᴄᴋ ᴀɴ ᴀᴅᴍɪɴ ᴏʀ sᴜᴅᴏ ᴜsᴇʀ.</blockquote>",
        )
    try:
        await client.ban_chat_member(chat_id, user.id)
        await client.unban_chat_member(chat_id, user.id)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴋɪᴄᴋ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    actor = user_mention(message.from_user) if message.from_user else "Admin"
    reason_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""
    text = (
        f"<blockquote>{pe('LOGOUT_EXIT')} <b>ᴜsᴇʀ ᴋɪᴄᴋᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}{reason_line}</blockquote>"
    )
    await mgmt_reply(message, text)
    await log_mgmt_action(client, chat_id, text)


@app.on_message(filters.command("kickme") & filters.group & ~BANNED_USERS)
async def kick_self(client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    if await is_user_admin(client, chat_id, message.from_user.id):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('SHIELD_ADMIN')} ᴀᴅᴍɪɴs ᴄᴀɴɴᴏᴛ ᴜsᴇ <code>/kickme</code>.</blockquote>",
        )
    try:
        await client.ban_chat_member(chat_id, message.from_user.id)
        await client.unban_chat_member(chat_id, message.from_user.id)
        await mgmt_reply(
            message,
            f"<blockquote>{pe('LOGOUT_EXIT')} {user_mention(message.from_user)} ᴋɪᴄᴋᴇᴅ ᴛʜᴇᴍsᴇʟᴠᴇs ᴏᴜᴛ.</blockquote>",
        )
    except Exception as exc:
        await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )


@app.on_message(
    filters.command(["rban", "runban", "rkick", "rmute", "runmute"]) & SUDOERS
)
async def remote_mod_cmd(client, message: Message):
    if len(message.command) < 3:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('TERMINAL_DEV')} <b>ᴜsᴀɢᴇ:</b> <code>/{message.command[0]} &lt;chat_id&gt; &lt;user_id/@user&gt;</code></blockquote>",
        )
    cmd = message.command[0].lower()
    try:
        target_chat = int(message.command[1])
        target_user = await client.get_users(
            int(message.command[2]) if message.command[2].isdigit() else message.command[2]
        )
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ɪɴᴠᴀʟɪᴅ ᴄʜᴀᴛ/ᴜsᴇʀ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    try:
        if cmd == "rban":
            await client.ban_chat_member(target_chat, target_user.id)
            action = "ʀᴇᴍᴏᴛᴇ ʙᴀɴɴᴇᴅ"
        elif cmd == "runban":
            await client.unban_chat_member(target_chat, target_user.id)
            action = "ʀᴇᴍᴏᴛᴇ ᴜɴʙᴀɴɴᴇᴅ"
        elif cmd == "rkick":
            await client.ban_chat_member(target_chat, target_user.id)
            await client.unban_chat_member(target_chat, target_user.id)
            action = "ʀᴇᴍᴏᴛᴇ ᴋɪᴄᴋᴇᴅ"
        elif cmd == "rmute":
            await client.restrict_chat_member(target_chat, target_user.id, ChatPermissions())
            action = "ʀᴇᴍᴏᴛᴇ ᴍᴜᴛᴇᴅ"
        else:
            await client.restrict_chat_member(
                target_chat,
                target_user.id,
                ChatPermissions(
                    can_send_messages=True,
                    can_send_media_messages=True,
                    can_send_other_messages=True,
                    can_add_web_page_previews=True,
                ),
            )
            action = "ʀᴇᴍᴏᴛᴇ ᴜɴᴍᴜᴛᴇᴅ"
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ʀᴇᴍᴏᴛᴇ ᴀᴄᴛɪᴏɴ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>{action}</b>\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(target_user)} (<code>{target_user.id}</code>)\n"
        f"{pe('USERS_GROUP')} <b>ᴄʜᴀᴛ:</b> <code>{target_chat}</code></blockquote>",
    )

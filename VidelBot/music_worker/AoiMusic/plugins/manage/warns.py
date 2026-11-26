"""
Warnings management module for AoiMusic.
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.types import CallbackQuery, ChatPermissions, InlineKeyboardMarkup, Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import (
    add_warn,
    get_chat_settings,
    get_warns,
    remove_last_warn,
    reset_warns,
    set_chat_setting,
)
from AoiMusic.utils.manage_helpers import (
    check_admin_and_bot,
    esc,
    extract_user_and_reason,
    has_admin_perm,
    is_user_admin,
    log_mgmt_action,
    mgmt_reply,
    user_mention,
)
from AoiMusic.utils.premium_emoji import pbtn, pe

__MODULE__ = "Warns"
__HELP__ = f"""
{pe('WARNING')} <b>ᴡᴀʀɴɪɴɢs</b>

<code>/warn</code> &lt;user&gt; [reason] — Issue a warning to a user
<code>/dwarn</code> &lt;user&gt; [reason] — Delete replied message &amp; warn user
<code>/warns</code> [user] — View active warnings of a user
<code>/resetwarn</code> / <code>/resetwarns</code> &lt;user&gt; — Reset all warnings
<code>/warnlimit</code> &lt;num&gt; — Set max warns before punishment (default: 3)
<code>/strongwarn</code> &lt;ban|kick|mute&gt; — Set punishment when limit is reached
"""


async def _apply_warn_punishment(client, chat_id: int, user_id: int, mode: str) -> str:
    if mode == "kick":
        await client.ban_chat_member(chat_id, user_id)
        await client.unban_chat_member(chat_id, user_id)
        return "ᴋɪᴄᴋᴇᴅ"
    if mode == "mute":
        await client.restrict_chat_member(chat_id, user_id, ChatPermissions())
        return "ᴍᴜᴛᴇᴅ"
    await client.ban_chat_member(chat_id, user_id)
    return "ʙᴀɴɴᴇᴅ"


@app.on_message(filters.command(["warn", "dwarn"]) & filters.group & ~BANNED_USERS)
async def warn_user_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, reason = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ ᴛᴏ ᴡᴀʀɴ.</blockquote>",
        )
    if await is_user_admin(client, chat_id, user.id):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('SHIELD_ADMIN')} ɪ ᴄᴀɴɴᴏᴛ ᴡᴀʀɴ ᴀɴ ᴀᴅᴍɪɴ ᴏʀ sᴜᴅᴏ ᴜsᴇʀ.</blockquote>",
        )
    if message.command[0].lower() == "dwarn" and message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except Exception:
            pass

    settings = await get_chat_settings(chat_id)
    limit = int(settings.get("warn_limit", 3))
    mode = str(settings.get("warn_mode", "ban")).lower()
    count = await add_warn(chat_id, user.id, reason)
    actor = user_mention(message.from_user) if message.from_user else "Admin"
    reason_line = f"\n{pe('DOCUMENT_RULES')} <b>ʀᴇᴀsᴏɴ:</b> {esc(reason)}" if reason else ""

    if count >= limit:
        await reset_warns(chat_id, user.id)
        try:
            action = await _apply_warn_punishment(client, chat_id, user.id, mode)
        except Exception as exc:
            return await mgmt_reply(
                message,
                f"<blockquote>{pe('WARNING')} ᴡᴀʀɴ ʟɪᴍɪᴛ ʀᴇᴀᴄʜᴇᴅ ʙᴜᴛ ᴀᴄᴛɪᴏɴ ғᴀɪʟᴇᴅ: <code>{esc(exc)}</code></blockquote>",
            )
        text = (
            f"<blockquote>{pe('BAN_BLOCK')} <b>ᴡᴀʀɴ ʟɪᴍɪᴛ ʀᴇᴀᴄʜᴇᴅ — {action}</b>\n\n"
            f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
            f"{pe('WARNING')} <b>ᴡᴀʀɴs:</b> <code>{count}/{limit}</code>\n"
            f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}{reason_line}</blockquote>"
        )
        await mgmt_reply(message, text)
        return await log_mgmt_action(client, chat_id, text)

    kb = InlineKeyboardMarkup(
        [
            [
                pbtn(
                    "ʀᴇᴍᴏᴠᴇ ᴡᴀʀɴ",
                    emoji="TRASH_DELETE",
                    style="DANGER",
                    callback_data=f"mgmt:rmwarn:{user.id}",
                )
            ]
        ]
    )
    text = (
        f"<blockquote>{pe('WARNING')} <b>ᴜsᴇʀ ᴡᴀʀɴᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('CHART_STATS')} <b>ᴡᴀʀɴs:</b> <code>{count}/{limit}</code>\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}{reason_line}</blockquote>"
    )
    await mgmt_reply(message, text, reply_markup=kb)
    await log_mgmt_action(client, chat_id, text)


@app.on_callback_query(filters.regex(r"^mgmt:rmwarn:(\d+)$"))
async def rm_warn_callback(client, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await has_admin_perm(client, chat_id, query.from_user.id, "can_restrict_members"):
        return await query.answer("Only admins can remove warnings.", show_alert=True)
    target_id = int(query.matches[0].group(1))
    remaining = await remove_last_warn(chat_id, target_id)
    await query.answer("Warning removed.")
    try:
        await query.message.edit_text(
            f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴡᴀʀɴ ʀᴇᴍᴏᴠᴇᴅ</b>\n"
            f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {user_mention(query.from_user)}\n"
            f"{pe('CHART_STATS')} <b>ʀᴇᴍᴀɪɴɪɴɢ ᴡᴀʀɴs:</b> <code>{remaining}</code></blockquote>"
        )
    except Exception:
        pass


@app.on_message(filters.command("warns") & filters.group & ~BANNED_USERS)
async def check_warns_cmd(client, message: Message):
    chat_id = message.chat.id
    user, _ = await extract_user_and_reason(client, message)
    user = user or message.from_user
    if not user:
        return
    data = await get_warns(chat_id, user.id)
    settings = await get_chat_settings(chat_id)
    limit = int(settings.get("warn_limit", 3))
    if data["count"] == 0:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('CHECK_CIRCLE')} {user_mention(user)} ʜᴀs <code>0/{limit}</code> ᴡᴀʀɴɪɴɢs.</blockquote>",
        )
    reasons = "\n".join(f"• {esc(r)}" for r in data["reasons"][-10:])
    await mgmt_reply(
        message,
        f"<blockquote>{pe('WARNING')} <b>ᴡᴀʀɴɪɴɢs ғᴏʀ {user_mention(user)}</b> (<code>{data['count']}/{limit}</code>)\n\n"
        f"{reasons}</blockquote>",
    )


@app.on_message(filters.command(["resetwarn", "resetwarns"]) & filters.group & ~BANNED_USERS)
async def reset_warns_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, _ = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.</blockquote>",
        )
    await reset_warns(chat_id, user.id)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴡᴀʀɴs ʀᴇsᴇᴛ</b> ғᴏʀ {user_mention(user)}.</blockquote>",
    )


@app.on_message(filters.command("warnlimit") & filters.group & ~BANNED_USERS)
async def set_warn_limit_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    if len(message.command) < 2 or not message.command[1].isdigit():
        settings = await get_chat_settings(chat_id)
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴄᴜʀʀᴇɴᴛ ᴡᴀʀɴ ʟɪᴍɪᴛ:</b> <code>{settings.get('warn_limit', 3)}</code>\n"
            f"<b>ᴜsᴀɢᴇ:</b> <code>/warnlimit 3</code></blockquote>",
        )
    val = max(1, min(20, int(message.command[1])))
    await set_chat_setting(chat_id, "warn_limit", val)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴡᴀʀɴ ʟɪᴍɪᴛ sᴇᴛ ᴛᴏ:</b> <code>{val}</code></blockquote>",
    )


@app.on_message(filters.command("strongwarn") & filters.group & ~BANNED_USERS)
async def set_warn_mode_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    if len(message.command) < 2:
        settings = await get_chat_settings(chat_id)
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴄᴜʀʀᴇɴᴛ ᴡᴀʀɴ ᴍᴏᴅᴇ:</b> <code>{settings.get('warn_mode', 'ban')}</code>\n"
            f"<b>ᴏᴘᴛɪᴏɴs:</b> <code>ban</code>, <code>kick</code>, <code>mute</code></blockquote>",
        )
    arg = message.command[1].lower()
    if arg in ("on", "yes", "ban"):
        mode = "ban"
    elif arg in ("off", "no", "kick"):
        mode = "kick"
    elif arg == "mute":
        mode = "mute"
    else:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ᴄʜᴏᴏsᴇ ᴏɴᴇ: <code>ban</code>, <code>kick</code>, ᴏʀ <code>mute</code>.</blockquote>",
        )
    await set_chat_setting(chat_id, "warn_mode", mode)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴡᴀʀɴ ᴘᴜɴɪsʜᴍᴇɴᴛ sᴇᴛ ᴛᴏ:</b> <code>{mode}</code></blockquote>",
    )

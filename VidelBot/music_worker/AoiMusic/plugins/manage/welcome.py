"""
Welcome, Goodbye, Welcome-Mute & Join Requests management module for AoiMusic.
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
"""

from __future__ import annotations

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    CallbackQuery,
    ChatJoinRequest,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardMarkup,
    Message,
)

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import (
    get_chat_settings,
    get_welcome,
    set_chat_setting,
    update_welcome,
)
from AoiMusic.utils.manage_helpers import (
    NO_PREVIEW,
    check_admin_and_bot,
    esc,
    has_admin_perm,
    mgmt_reply,
    user_mention,
)
from AoiMusic.utils.premium_emoji import pbtn, pe

__MODULE__ = "Welcome"
__HELP__ = f"""
{pe('USERS_GROUP')} <b>ᴡᴇʟᴄᴏᴍᴇ, ɢᴏᴏᴅʙʏᴇ &amp; ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛs</b>

<code>/welcome</code> &lt;on|off&gt; — Toggle welcome messages
<code>/goodbye</code> &lt;on|off&gt; — Toggle goodbye messages
<code>/setwelcome</code> &lt;text&gt; — Custom welcome (vars: <code>{{first}}</code>, <code>{{mention}}</code>, <code>{{chatname}}</code>, <code>{{id}}</code>)
<code>/setgoodbye</code> &lt;text&gt; — Custom goodbye text
<code>/resetwelcome</code> / <code>/resetgoodbye</code> — Reset to default
<code>/cleanwelcome</code> &lt;on|off&gt; — Delete previous welcome message
<code>/cleanservice</code> &lt;on|off&gt; — Delete join/leave service messages
<code>/welcomemute</code> &lt;on|off&gt; — Mute new members until they tap Verify
<code>/joinrequest</code> &lt;on|off&gt; — Send Approve/Dismiss buttons for join requests
"""

DEFAULT_WELCOME = (
    f"<blockquote>{pe('SPARKLES_AI')} <b>ᴡᴇʟᴄᴏᴍᴇ {{mention}}!</b>\n"
    f"{pe('USERS_GROUP')} <b>ɢʀᴏᴜᴘ:</b> {{chatname}}\n"
    f"{pe('INFO')} <b>ɪᴅ:</b> <code>{{id}}</code></blockquote>"
)
DEFAULT_GOODBYE = f"<blockquote>{pe('LOGOUT_EXIT')} <b>{{first}}</b> ʟᴇғᴛ <b>{{chatname}}</b>.</blockquote>"

UNMUTE_PERMS = ChatPermissions(
    can_send_messages=True,
    can_send_media_messages=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
    can_invite_users=True,
)


def _format_tpl(tpl: str, user, chat_title: str) -> str:
    return (
        tpl.replace("{first}", esc(user.first_name or "User"))
        .replace("{last}", esc(user.last_name or ""))
        .replace("{fullname}", esc(f"{user.first_name or ''} {user.last_name or ''}".strip()))
        .replace("{username}", f"@{esc(user.username)}" if user.username else user_mention(user))
        .replace("{mention}", user_mention(user))
        .replace("{chatname}", esc(chat_title or "this group"))
        .replace("{id}", str(user.id))
    )


async def _toggle_welcome_flag(client, message: Message, key: str, label: str):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        w = await get_welcome(chat_id)
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>{label}:</b> <code>{'ᴏɴ' if w.get(key) else 'ᴏғғ'}</code>\n"
            f"<b>ᴜsᴀɢᴇ:</b> <code>/{message.command[0]} on</code> ᴏʀ <code>off</code></blockquote>",
        )
    state = message.command[1].lower() == "on"
    await update_welcome(chat_id, {key: state})
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>{label} {'ᴇɴᴀʙʟᴇᴅ' if state else 'ᴅɪsᴀʙʟᴇᴅ'}.</b></blockquote>",
    )


@app.on_message(filters.command("welcome") & filters.group & ~BANNED_USERS)
async def welcome_toggle(client, message: Message):
    await _toggle_welcome_flag(client, message, "welcome_on", "ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇs")


@app.on_message(filters.command("goodbye") & filters.group & ~BANNED_USERS)
async def goodbye_toggle(client, message: Message):
    await _toggle_welcome_flag(client, message, "goodbye_on", "ɢᴏᴏᴅʙʏᴇ ᴍᴇssᴀɢᴇs")


@app.on_message(filters.command("cleanwelcome") & filters.group & ~BANNED_USERS)
async def cleanwelcome_toggle(client, message: Message):
    await _toggle_welcome_flag(client, message, "clean_welcome", "ᴄʟᴇᴀɴ ᴡᴇʟᴄᴏᴍᴇ")


@app.on_message(filters.command("cleanservice") & filters.group & ~BANNED_USERS)
async def cleanservice_toggle(client, message: Message):
    await _toggle_welcome_flag(client, message, "clean_service", "ᴄʟᴇᴀɴ sᴇʀᴠɪᴄᴇ")


@app.on_message(filters.command("welcomemute") & filters.group & ~BANNED_USERS)
async def welcomemute_toggle(client, message: Message):
    await _toggle_welcome_flag(client, message, "welcome_mute", "ᴡᴇʟᴄᴏᴍᴇ ᴍᴜᴛᴇ ᴠᴇʀɪғʏ")


@app.on_message(filters.command("setwelcome") & filters.group & ~BANNED_USERS)
async def set_welcome_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    text = (
        message.text.split(None, 1)[1]
        if len(message.command) > 1
        else (message.reply_to_message.text if message.reply_to_message and message.reply_to_message.text else "")
    )
    if not text:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/setwelcome Welcome {{mention}} to {{chatname}}!</code></blockquote>",
        )
    await update_welcome(chat_id, {"welcome_text": text, "welcome_on": True})
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴄᴜsᴛᴏᴍ ᴡᴇʟᴄᴏᴍᴇ sᴀᴠᴇᴅ &amp; ᴇɴᴀʙʟᴇᴅ.</b></blockquote>",
    )


@app.on_message(filters.command("setgoodbye") & filters.group & ~BANNED_USERS)
async def set_goodbye_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    text = (
        message.text.split(None, 1)[1]
        if len(message.command) > 1
        else (message.reply_to_message.text if message.reply_to_message and message.reply_to_message.text else "")
    )
    if not text:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/setgoodbye Goodbye {{first}}!</code></blockquote>",
        )
    await update_welcome(chat_id, {"goodbye_text": text, "goodbye_on": True})
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴄᴜsᴛᴏᴍ ɢᴏᴏᴅʙʏᴇ sᴀᴠᴇᴅ &amp; ᴇɴᴀʙʟᴇᴅ.</b></blockquote>",
    )


@app.on_message(filters.command("resetwelcome") & filters.group & ~BANNED_USERS)
async def reset_welcome_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    await update_welcome(chat_id, {"welcome_text": None})
    await mgmt_reply(message, f"<blockquote>{pe('REFRESH')} <b>ᴡᴇʟᴄᴏᴍᴇ ᴍᴇssᴀɢᴇ ʀᴇsᴇᴛ ᴛᴏ ᴅᴇғᴀᴜʟᴛ.</b></blockquote>")


@app.on_message(filters.command("resetgoodbye") & filters.group & ~BANNED_USERS)
async def reset_goodbye_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    await update_welcome(chat_id, {"goodbye_text": None})
    await mgmt_reply(message, f"<blockquote>{pe('REFRESH')} <b>ɢᴏᴏᴅʙʏᴇ ᴍᴇssᴀɢᴇ ʀᴇsᴇᴛ ᴛᴏ ᴅᴇғᴀᴜʟᴛ.</b></blockquote>")


@app.on_message(filters.command("joinrequest") & filters.group & ~BANNED_USERS)
async def joinrequest_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_invite_users"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        s = await get_chat_settings(chat_id)
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ᴘᴀɴᴇʟ:</b> <code>{'ᴏɴ' if s.get('joinrequest') else 'ᴏғғ'}</code>\n"
            f"<b>ᴜsᴀɢᴇ:</b> <code>/joinrequest on</code> ᴏʀ <code>off</code></blockquote>",
        )
    state = message.command[1].lower() == "on"
    await set_chat_setting(chat_id, "joinrequest", state)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ᴀʟᴇʀᴛs {'ᴇɴᴀʙʟᴇᴅ' if state else 'ᴅɪsᴀʙʟᴇᴅ'}.</b></blockquote>",
    )


@app.on_message(filters.group & (filters.new_chat_members | filters.left_chat_member), group=20)
async def clean_service_watcher(client, message: Message):
    w = await get_welcome(message.chat.id)
    if w.get("clean_service"):
        try:
            await message.delete()
        except Exception:
            pass


@app.on_chat_member_updated(filters.group, group=20)
async def member_join_leave_handler(client, update: ChatMemberUpdated):
    chat = update.chat
    old = update.old_chat_member
    new = update.new_chat_member
    w = await get_welcome(chat.id)

    # Joined
    if new and (not old or str(old.status).endswith(("LEFT", "BANNED"))) and str(new.status).endswith("MEMBER"):
        user = new.user
        if not user or user.is_bot or not w.get("welcome_on"):
            return
        if w.get("clean_welcome") and w.get("last_welcome_id"):
            try:
                await client.delete_messages(chat.id, int(w["last_welcome_id"]))
            except Exception:
                pass
        tpl = w.get("welcome_text") or DEFAULT_WELCOME
        text = _format_tpl(tpl, user, chat.title)
        kb = None
        if w.get("welcome_mute"):
            try:
                await client.restrict_chat_member(chat.id, user.id, ChatPermissions())
                kb = InlineKeyboardMarkup(
                    [
                        [
                            pbtn(
                                "ɪ ᴀᴍ ʜᴜᴍᴀɴ",
                                emoji="VERIFIED_BADGE",
                                style="SUCCESS",
                                callback_data=f"mgmt:wverify:{user.id}",
                            )
                        ]
                    ]
                )
            except Exception:
                pass
        try:
            sent = await client.send_message(
                chat.id,
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
                link_preview_options=NO_PREVIEW,
            )
            await update_welcome(chat.id, {"last_welcome_id": sent.id})
        except Exception:
            pass

    # Left
    elif old and (not new or str(new.status).endswith("LEFT")):
        user = old.user
        if not user or user.is_bot or not w.get("goodbye_on"):
            return
        tpl = w.get("goodbye_text") or DEFAULT_GOODBYE
        text = _format_tpl(tpl, user, chat.title)
        try:
            await client.send_message(
                chat.id,
                text,
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
        except Exception:
            pass


@app.on_callback_query(filters.regex(r"^mgmt:wverify:(\d+)$"))
async def welcome_verify_cb(client, query: CallbackQuery):
    uid = int(query.matches[0].group(1))
    if query.from_user.id != uid:
        return await query.answer("This verification button is not for you.", show_alert=True)
    try:
        await client.restrict_chat_member(query.message.chat.id, uid, UNMUTE_PERMS)
        await query.answer("Verified! You can chat now.")
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        await query.answer("Could not unmute automatically.", show_alert=True)


@app.on_chat_join_request(filters.group)
async def join_request_handler(client, req: ChatJoinRequest):
    s = await get_chat_settings(req.chat.id)
    if not s.get("joinrequest"):
        return
    user = req.from_user
    kb = InlineKeyboardMarkup(
        [
            [
                pbtn("ᴀᴘᴘʀᴏᴠᴇ", emoji="CHECK_CIRCLE", style="SUCCESS", callback_data=f"mgmt:jr:ok:{user.id}"),
                pbtn("ᴅɪsᴍɪss", emoji="CLOSE", style="DANGER", callback_data=f"mgmt:jr:no:{user.id}"),
            ]
        ]
    )
    try:
        await client.send_message(
            req.chat.id,
            f"<blockquote>{pe('BELL_NOTIFY')} <b>ɴᴇᴡ ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ</b>\n\n"
            f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)</blockquote>",
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
            link_preview_options=NO_PREVIEW,
        )
    except Exception:
        pass


@app.on_callback_query(filters.regex(r"^mgmt:jr:(ok|no):(\d+)$"))
async def join_request_cb(client, query: CallbackQuery):
    chat_id = query.message.chat.id
    if not await has_admin_perm(client, chat_id, query.from_user.id, "can_invite_users"):
        return await query.answer("Only admins with invite rights can do this.", show_alert=True)
    action, uid = query.matches[0].group(1), int(query.matches[0].group(2))
    try:
        if action == "ok":
            await client.approve_chat_join_request(chat_id, uid)
            status = f"{pe('CHECK_CIRCLE')} <b>ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ᴀᴘᴘʀᴏᴠᴇᴅ</b>"
        else:
            await client.decline_chat_join_request(chat_id, uid)
            status = f"{pe('CLOSE')} <b>ᴊᴏɪɴ ʀᴇǫᴜᴇsᴛ ᴅɪsᴍɪssᴇᴅ</b>"
        await query.answer("Done!")
        await query.message.edit_text(
            f"<blockquote>{status}\n{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {user_mention(query.from_user)}</blockquote>",
            parse_mode=ParseMode.HTML,
        )
    except Exception as exc:
        await query.answer(f"Failed: {exc}", show_alert=True)

"""
Group Admin & Chat Settings management module for AoiMusic.
Uses Kurigram 2.2.27 + Musiciconis premium emojis.
"""

from __future__ import annotations

import os
from pyrogram import filters
from pyrogram.enums import ChatMembersFilter, ChatType
from pyrogram.types import ChatPrivileges, Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_helpers import (
    check_admin_and_bot,
    esc,
    extract_user_and_reason,
    is_user_admin,
    log_mgmt_action,
    mgmt_reply,
    resolve_chat_id,
    user_mention,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "Admins"
__HELP__ = f"""
{pe('SHIELD_ADMIN')} <b>ᴀᴅᴍɪɴ &amp; ɢʀᴏᴜᴘ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ</b>

<b>• ᴘʀᴏᴍᴏᴛᴇ / ᴅᴇᴍᴏᴛᴇ</b>
<code>/promote</code>, <code>/fullpromote</code>, <code>/lowpromote</code> — Promote a user
<code>/demote</code> — Demote an admin
<code>/title</code> / <code>/settitle</code> — Set admin custom title

<b>• ɢʀᴏᴜᴘ ɪɴғᴏ &amp; ᴘɪɴs</b>
<code>/admins</code> / <code>/staff</code> — List group admins
<code>/bots</code> — List bots in the group
<code>/pin</code> [loud] — Pin replied message
<code>/unpin</code> [all] — Unpin message(s)
<code>/pinned</code> — Get current pinned message
<code>/invitelink</code> — Generate/fetch group invite link

<b>• ᴄʜᴀᴛ sᴇᴛᴛɪɴɢs</b>
<code>/setgtitle</code> &lt;title&gt; — Change group title
<code>/setdesc</code> &lt;text&gt; — Change group description
<code>/setgpic</code> — Reply to photo to set group photo
<code>/delgpic</code> — Remove group photo
<code>/setsticker</code> — Reply to sticker to set group sticker set
"""


@app.on_message(
    filters.command(["promote", "fullpromote", "lowpromote"])
    & filters.group
    & ~BANNED_USERS
)
async def promote_user(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_promote_members"):
        return
    user, title = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ ᴛᴏ ᴘʀᴏᴍᴏᴛᴇ.</blockquote>",
        )
    cmd = message.command[0].lower()
    if cmd == "fullpromote":
        privs = ChatPrivileges(
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_promote_members=True,
            can_change_info=True,
            can_invite_users=True,
            can_pin_messages=True,
        )
        tier = "ꜰᴜʟʟ ᴀᴅᴍɪɴ"
    elif cmd == "lowpromote":
        privs = ChatPrivileges(
            can_manage_chat=True,
            can_delete_messages=True,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_video_chats=True,
        )
        tier = "ʟᴏᴡ ᴀᴅᴍɪɴ"
    else:
        privs = ChatPrivileges(
            can_manage_chat=True,
            can_delete_messages=True,
            can_manage_video_chats=True,
            can_restrict_members=True,
            can_change_info=True,
            can_invite_users=True,
            can_pin_messages=True,
        )
        tier = "ᴀᴅᴍɪɴ"

    try:
        await client.promote_chat_member(chat_id, user.id, privileges=privs)
        if title:
            try:
                await client.set_administrator_title(chat_id, user.id, title[:16])
            except Exception:
                pass
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴘʀᴏᴍᴏᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )

    actor = user_mention(message.from_user) if message.from_user else "Admin"
    title_line = f"\n{pe('EDIT_PENCIL')} <b>ᴛɪᴛʟᴇ:</b> <code>{esc(title[:16])}</code>" if title else ""
    text = (
        f"<blockquote>{pe('CROWN_OWNER')} <b>ᴘʀᴏᴍᴏᴛᴇᴅ ({tier})</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}{title_line}</blockquote>"
    )
    await mgmt_reply(message, text)
    await log_mgmt_action(client, chat_id, text)


@app.on_message(filters.command("demote") & filters.group & ~BANNED_USERS)
async def demote_user(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_promote_members"):
        return
    user, _ = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀɴ ᴀᴅᴍɪɴ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ ᴛᴏ ᴅᴇᴍᴏᴛᴇ.</blockquote>",
        )
    try:
        await client.promote_chat_member(
            chat_id,
            user.id,
            privileges=ChatPrivileges(
                can_manage_chat=False,
                can_delete_messages=False,
                can_manage_video_chats=False,
                can_restrict_members=False,
                can_promote_members=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False,
            ),
        )
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴅᴇᴍᴏᴛᴇ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    actor = user_mention(message.from_user) if message.from_user else "Admin"
    text = (
        f"<blockquote>{pe('DOWN_ARROW')} <b>ᴅᴇᴍᴏᴛᴇᴅ</b>\n\n"
        f"{pe('USER')} <b>ᴜsᴇʀ:</b> {user_mention(user)} (<code>{user.id}</code>)\n"
        f"{pe('SHIELD_ADMIN')} <b>ʙʏ:</b> {actor}</blockquote>"
    )
    await mgmt_reply(message, text)
    await log_mgmt_action(client, chat_id, text)


@app.on_message(
    filters.command(["title", "settitle", "admintag", "settag"])
    & filters.group
    & ~BANNED_USERS
)
async def set_admin_title_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_promote_members"):
        return
    user, title = await extract_user_and_reason(client, message)
    if not user or not title:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/title @user &lt;custom title&gt;</code></blockquote>",
        )
    try:
        await client.set_administrator_title(chat_id, user.id, title[:16])
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴛɪᴛʟᴇ ᴜᴘᴅᴀᴛᴇᴅ</b>\n"
        f"{pe('USER')} {user_mention(user)} → <code>{esc(title[:16])}</code></blockquote>",
    )


@app.on_message(filters.command(["admins", "staff"]) & filters.group & ~BANNED_USERS)
async def list_admins(client, message: Message):
    chat_id = message.chat.id
    owners, admins = [], []
    async for m in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
        if m.user.is_bot or m.user.is_deleted:
            continue
        title = f" <i>({esc(m.custom_title)})</i>" if m.custom_title else ""
        entry = f"• {user_mention(m.user)}{title}"
        if str(m.status).endswith("OWNER"):
            owners.append(entry)
        else:
            admins.append(entry)
    lines = [f"{pe('SHIELD_ADMIN')} <b>sᴛᴀғғ ɪɴ {esc(message.chat.title)}</b>\n"]
    if owners:
        lines.append(f"{pe('CROWN_OWNER')} <b>ᴏᴡɴᴇʀ:</b>")
        lines.extend(owners)
        lines.append("")
    if admins:
        lines.append(f"{pe('STAR')} <b>ᴀᴅᴍɪɴs ({len(admins)}):</b>")
        lines.extend(admins)
    body = "\n".join(lines)
    await mgmt_reply(message, f"<blockquote>{body}</blockquote>")


@app.on_message(filters.command("bots") & filters.group & ~BANNED_USERS)
async def list_bots(client, message: Message):
    bots = []
    async for m in client.get_chat_members(message.chat.id, filter=ChatMembersFilter.BOTS):
        uname = f"@{m.user.username}" if m.user.username else esc(m.user.first_name)
        bots.append(f"• {uname} (<code>{m.user.id}</code>)")
    body = "\n".join(bots) if bots else "ɴᴏ ʙᴏᴛs ғᴏᴜɴᴅ."
    await mgmt_reply(
        message,
        f"<blockquote>{pe('BOT')} <b>ʙᴏᴛs ɪɴ {esc(message.chat.title)} ({len(bots)})</b>\n\n{body}</blockquote>",
    )


@app.on_message(filters.command("pin") & filters.group & ~BANNED_USERS)
async def pin_msg(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_pin_messages"):
        return
    if not message.reply_to_message:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ᴘɪɴ ɪᴛ.</blockquote>",
        )
    loud = len(message.command) > 1 and message.command[1].lower() in ("loud", "notify", "violent")
    try:
        await client.pin_chat_message(
            chat_id,
            message.reply_to_message.id,
            disable_notification=not loud,
        )
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴘɪɴ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    mode = "ᴡɪᴛʜ ɴᴏᴛɪғɪᴄᴀᴛɪᴏɴ" if loud else "sɪʟᴇɴᴛʟʏ"
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴍᴇssᴀɢᴇ ᴘɪɴɴᴇᴅ</b> ({mode}).</blockquote>",
    )


@app.on_message(filters.command("unpin") & filters.group & ~BANNED_USERS)
async def unpin_msg(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_pin_messages"):
        return
    try:
        if len(message.command) > 1 and message.command[1].lower() == "all":
            await client.unpin_all_chat_messages(chat_id)
            return await mgmt_reply(
                message,
                f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴜɴᴘɪɴɴᴇᴅ ᴀʟʟ ᴍᴇssᴀɢᴇs.</b></blockquote>",
            )
        if message.reply_to_message:
            await client.unpin_chat_message(chat_id, message.reply_to_message.id)
        else:
            await client.unpin_chat_message(chat_id)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ᴜɴᴘɪɴ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴍᴇssᴀɢᴇ ᴜɴᴘɪɴɴᴇᴅ.</b></blockquote>",
    )


@app.on_message(filters.command("pinned") & filters.group & ~BANNED_USERS)
async def get_pinned_msg(client, message: Message):
    chat = await client.get_chat(message.chat.id)
    if not chat.pinned_message:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} ɴᴏ ᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</blockquote>",
        )
    link = chat.pinned_message.link
    await mgmt_reply(
        message,
        f"<blockquote>{pe('LINK')} <b>ᴘɪɴɴᴇᴅ ᴍᴇssᴀɢᴇ:</b> <a href='{link}'>ᴊᴜᴍᴘ ᴛᴏ ᴍᴇssᴀɢᴇ</a></blockquote>",
    )


@app.on_message(filters.command("invitelink") & filters.group & ~BANNED_USERS)
async def invite_link_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_invite_users"):
        return
    try:
        link = await client.export_chat_invite_link(chat_id)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    await mgmt_reply(
        message,
        f"<blockquote>{pe('LINK')} <b>ɪɴᴠɪᴛᴇ ʟɪɴᴋ:</b>\n<code>{esc(link)}</code></blockquote>",
    )


@app.on_message(filters.command("setgtitle") & filters.group & ~BANNED_USERS)
async def set_group_title(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/setgtitle &lt;new title&gt;</code></blockquote>",
        )
    title = message.text.split(None, 1)[1].strip()
    try:
        await client.set_chat_title(chat_id, title)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ɢʀᴏᴜᴘ ᴛɪᴛʟᴇ ᴜᴘᴅᴀᴛᴇᴅ:</b> <code>{esc(title)}</code></blockquote>",
    )


@app.on_message(filters.command(["setdesc", "setdescription"]) & filters.group & ~BANNED_USERS)
async def set_group_desc(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    desc = message.text.split(None, 1)[1].strip() if len(message.command) > 1 else ""
    try:
        await client.set_chat_description(chat_id, desc[:255])
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ɢʀᴏᴜᴘ ᴅᴇsᴄʀɪᴘᴛɪᴏɴ ᴜᴘᴅᴀᴛᴇᴅ.</b></blockquote>",
    )


@app.on_message(filters.command("setgpic") & filters.group & ~BANNED_USERS)
async def set_group_pic(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    reply = message.reply_to_message
    if not reply or not (reply.photo or reply.document):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('IMAGE_PHOTO')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴘʜᴏᴛᴏ ᴛᴏ sᴇᴛ ɪᴛ ᴀs ɢʀᴏᴜᴘ ᴘɪᴄ.</blockquote>",
        )
    path = None
    try:
        path = await reply.download()
        await client.set_chat_photo(chat_id, photo=path)
        await mgmt_reply(
            message,
            f"<blockquote>{pe('CHECK_CIRCLE')} <b>ɢʀᴏᴜᴘ ᴘʜᴏᴛᴏ ᴜᴘᴅᴀᴛᴇᴅ.</b></blockquote>",
        )
    except Exception as exc:
        await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@app.on_message(filters.command("delgpic") & filters.group & ~BANNED_USERS)
async def del_group_pic(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    try:
        await client.delete_chat_photo(chat_id)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    await mgmt_reply(
        message,
        f"<blockquote>{pe('TRASH_DELETE')} <b>ɢʀᴏᴜᴘ ᴘʜᴏᴛᴏ ʀᴇᴍᴏᴠᴇᴅ.</b></blockquote>",
    )


@app.on_message(filters.command("setsticker") & filters.group & ~BANNED_USERS)
async def set_group_sticker(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    reply = message.reply_to_message
    if not reply or not reply.sticker or not reply.sticker.set_name:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ sᴛɪᴄᴋᴇʀ ғʀᴏᴍ ᴀ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋ.</blockquote>",
        )
    try:
        await client.set_chat_sticker_set(chat_id, reply.sticker.set_name)
    except Exception as exc:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} <b>ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>",
        )
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ɢʀᴏᴜᴘ sᴛɪᴄᴋᴇʀ sᴇᴛ ᴜᴘᴅᴀᴛᴇᴅ:</b> <code>{esc(reply.sticker.set_name)}</code></blockquote>",
    )

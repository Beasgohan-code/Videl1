"""
Group Utilities for AoiMusic:
Approve Users, Reports (@admin), TagAll, Log Channel, Backup Export/Import,
Remote Connect, Command Disable, and User/Chat Info (/id, /info).
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
"""

from __future__ import annotations

import asyncio
import io
import json
from pyrogram import filters
from pyrogram.enums import ChatMembersFilter, ParseMode
from pyrogram.types import Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import (
    add_to_setting_set,
    approve_user,
    clear_connection,
    get_approved_users,
    get_blacklists,
    get_chat_settings,
    get_connection,
    get_locks,
    get_rules,
    get_welcome,
    is_approved,
    list_notes,
    remove_from_setting_set,
    set_chat_setting,
    set_connection,
    set_rules,
    unapprove_all,
    unapprove_user,
    update_welcome,
)
from AoiMusic.utils.manage_helpers import (
    NO_PREVIEW,
    check_admin_and_bot,
    esc,
    extract_user_and_reason,
    is_user_admin,
    mgmt_reply,
    user_mention,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "Group Tools"
__HELP__ = f"""
{pe('SETTINGS')} <b>ɢʀᴏᴜᴘ ᴛᴏᴏʟs &amp; ᴜᴛɪʟɪᴛɪᴇs</b>

<b>• ᴀᴘᴘʀᴏᴠᴀʟ &amp; ʀᴇᴘᴏʀᴛs</b>
<code>/approve</code>, <code>/unapprove</code>, <code>/approved</code>, <code>/unapproveall</code> — Exempt trusted users from locks/filters
<code>/report</code> or <code>@admin</code> — Report a message to group admins
<code>/reports</code> &lt;on|off&gt; — Toggle reporting

<b>• ᴛᴀɢ ᴀʟʟ</b>
<code>/tagall</code> / <code>/all</code> &lt;msg&gt; — Mention group members in batches
<code>/canceltag</code> / <code>/tagoff</code> — Stop an active mention session

<b>• ʟᴏɢs, ᴄᴏɴɴᴇᴄᴛ, ʙᴀᴄᴋᴜᴘ &amp; ɪɴғᴏ</b>
<code>/setlog</code> &lt;channel_id&gt;, <code>/unsetlog</code>, <code>/logchannel</code> — Moderation log channel
<code>/connect</code> &lt;chat_id&gt;, <code>/disconnect</code>, <code>/connection</code> — Manage group from PM
<code>/export</code> / <code>/import</code> — Backup or restore group settings JSON
<code>/id</code>, <code>/info</code> [user] — Get user/chat ID &amp; profile info
"""

_ACTIVE_TAGALL: set[int] = set()


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------
@app.on_message(filters.command("approve") & filters.group & ~BANNED_USERS)
async def approve_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, _ = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.</blockquote>")
    await approve_user(chat_id, user.id)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('VERIFIED_BADGE')} {user_mention(user)} ʜᴀs ʙᴇᴇɴ <b>ᴀᴘᴘʀᴏᴠᴇᴅ</b> ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</blockquote>",
    )


@app.on_message(filters.command("unapprove") & filters.group & ~BANNED_USERS)
async def unapprove_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    user, _ = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.</blockquote>")
    await unapprove_user(chat_id, user.id)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CLOSE')} {user_mention(user)} ɪs ɴᴏ ʟᴏɴɢᴇʀ ᴀᴘᴘʀᴏᴠᴇᴅ.</blockquote>",
    )


@app.on_message(filters.command("approved") & filters.group & ~BANNED_USERS)
async def approved_list_cmd(client, message: Message):
    users = await get_approved_users(message.chat.id)
    if not users:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} ɴᴏ ᴀᴘᴘʀᴏᴠᴇᴅ ᴜsᴇʀs ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</blockquote>")
    body = "\n".join(f"• <code>{u}</code>" for u in sorted(users))
    await mgmt_reply(message, f"<blockquote>{pe('VERIFIED_BADGE')} <b>ᴀᴘᴘʀᴏᴠᴇᴅ ᴜsᴇʀs ({len(users)})</b>\n\n{body}</blockquote>")


@app.on_message(filters.command("approval") & filters.group & ~BANNED_USERS)
async def check_approval_cmd(client, message: Message):
    user, _ = await extract_user_and_reason(client, message)
    user = user or message.from_user
    if not user:
        return
    ok = await is_approved(message.chat.id, user.id)
    icon = pe("VERIFIED_BADGE") if ok else pe("INFO")
    await mgmt_reply(
        message,
        f"<blockquote>{icon} {user_mention(user)} ɪs <b>{'ᴀᴘᴘʀᴏᴠᴇᴅ' if ok else 'ɴᴏᴛ ᴀᴘᴘʀᴏᴠᴇᴅ'}</b>.</blockquote>",
    )


@app.on_message(filters.command("unapproveall") & filters.group & ~BANNED_USERS)
async def unapprove_all_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    await unapprove_all(chat_id)
    await mgmt_reply(message, f"<blockquote>{pe('TRASH_DELETE')} ᴀʟʟ ᴀᴘᴘʀᴏᴠᴇᴅ ᴜsᴇʀs ʜᴀᴠᴇ ʙᴇᴇɴ ʀᴇᴍᴏᴠᴇᴅ.</blockquote>")


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
@app.on_message(filters.command("reports") & filters.group & ~BANNED_USERS)
async def reports_toggle_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        s = await get_chat_settings(chat_id)
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ʀᴇᴘᴏʀᴛs:</b> <code>{'ᴏɴ' if s.get('reports', True) else 'ᴏғғ'}</code></blockquote>",
        )
    state = message.command[1].lower() == "on"
    await set_chat_setting(chat_id, "reports", state)
    await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} <b>ʀᴇᴘᴏʀᴛs {'ᴇɴᴀʙʟᴇᴅ' if state else 'ᴅɪsᴀʙʟᴇᴅ'}.</b></blockquote>")


@app.on_message(
    (filters.command("report") | filters.regex(r"(?i)^@admins?\b"))
    & filters.group
    & ~BANNED_USERS
)
async def report_user_cmd(client, message: Message):
    chat_id = message.chat.id
    s = await get_chat_settings(chat_id)
    if not s.get("reports", True):
        return
    if not message.reply_to_message or not message.reply_to_message.from_user:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ ᴛᴏ ʀᴇᴘᴏʀᴛ ɪᴛ ᴛᴏ ᴀᴅᴍɪɴs.</blockquote>")
    reported = message.reply_to_message.from_user
    if await is_user_admin(client, chat_id, reported.id):
        return await mgmt_reply(message, f"<blockquote>{pe('SHIELD_ADMIN')} ʏᴏᴜ ᴄᴀɴɴᴏᴛ ʀᴇᴘᴏʀᴛ ᴀɴ ᴀᴅᴍɪɴ.</blockquote>")
    pings = []
    async for m in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
        if not m.user.is_bot and not m.user.is_deleted:
            pings.append(f'<a href="tg://user?id={m.user.id}">\u2060</a>')
    reporter = user_mention(message.from_user) if message.from_user else "Someone"
    await mgmt_reply(
        message,
        f"<blockquote>{pe('BELL_NOTIFY')} <b>ʀᴇᴘᴏʀᴛᴇᴅ ᴛᴏ ᴀᴅᴍɪɴs</b>{''.join(pings)}\n\n"
        f"{pe('USER')} <b>ʀᴇᴘᴏʀᴛᴇᴅ:</b> {user_mention(reported)} (<code>{reported.id}</code>)\n"
        f"{pe('INFO')} <b>ʙʏ:</b> {reporter}</blockquote>",
    )


# ---------------------------------------------------------------------------
# TagAll
# ---------------------------------------------------------------------------
@app.on_message(
    (filters.command(["tagall", "all", "mentionall"]) | filters.regex(r"(?i)^@all(?:\s|$)"))
    & filters.group
    & ~BANNED_USERS
)
async def tagall_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if chat_id in _ACTIVE_TAGALL:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ᴀ ᴛᴀɢᴀʟʟ sᴇssɪᴏɴ ɪs ᴀʟʀᴇᴀᴅʏ ʀᴜɴɴɪɴɢ. ᴜsᴇ <code>/canceltag</code>.</blockquote>")
    parts = (message.text or "").split(None, 1)
    header = esc(parts[1]) if len(parts) > 1 else "ᴀᴛᴛᴇɴᴛɪᴏɴ ᴇᴠᴇʀʏᴏɴᴇ!"
    _ACTIVE_TAGALL.add(chat_id)
    try:
        batch = []
        async for m in client.get_chat_members(chat_id):
            if chat_id not in _ACTIVE_TAGALL:
                break
            if m.user.is_bot or m.user.is_deleted:
                continue
            batch.append(user_mention(m.user))
            if len(batch) >= 5:
                await client.send_message(
                    chat_id,
                    f"{pe('MEGAPHONE')} <b>{header}</b>\n\n" + " • ".join(batch),
                    parse_mode=ParseMode.HTML,
                    link_preview_options=NO_PREVIEW,
                )
                batch.clear()
                await asyncio.sleep(2.5)
        if batch and chat_id in _ACTIVE_TAGALL:
            await client.send_message(
                chat_id,
                f"{pe('MEGAPHONE')} <b>{header}</b>\n\n" + " • ".join(batch),
                parse_mode=ParseMode.HTML,
                link_preview_options=NO_PREVIEW,
            )
    finally:
        _ACTIVE_TAGALL.discard(chat_id)


@app.on_message(filters.command(["canceltag", "tagoff"]) & filters.group & ~BANNED_USERS)
async def cancel_tagall_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if chat_id in _ACTIVE_TAGALL:
        _ACTIVE_TAGALL.discard(chat_id)
        await mgmt_reply(message, f"<blockquote>{pe('STOP')} <b>ᴛᴀɢᴀʟʟ sᴛᴏᴘᴘᴇᴅ.</b></blockquote>")
    else:
        await mgmt_reply(message, f"<blockquote>{pe('INFO')} ɴᴏ ᴀᴄᴛɪᴠᴇ ᴛᴀɢᴀʟʟ sᴇssɪᴏɴ.</blockquote>")


# ---------------------------------------------------------------------------
# Log Channel
# ---------------------------------------------------------------------------
@app.on_message(filters.command("setlog") & filters.group & ~BANNED_USERS)
async def setlog_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/setlog -100xxxxxxxxxx</code></blockquote>")
    try:
        target = int(message.command[1])
    except ValueError:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ᴘʀᴏᴠɪᴅᴇ ᴀ ᴠᴀʟɪᴅ ɴᴜᴍᴇʀɪᴄ ᴄʜᴀɴɴᴇʟ ɪᴅ.</blockquote>")
    await set_chat_setting(chat_id, "log_channel", target)
    await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} <b>ʟᴏɢ ᴄʜᴀɴɴᴇʟ sᴇᴛ:</b> <code>{target}</code></blockquote>")


@app.on_message(filters.command("unsetlog") & filters.group & ~BANNED_USERS)
async def unsetlog_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    await set_chat_setting(chat_id, "log_channel", None)
    await mgmt_reply(message, f"<blockquote>{pe('TRASH_DELETE')} <b>ʟᴏɢ ᴄʜᴀɴɴᴇʟ ᴜɴsᴇᴛ.</b></blockquote>")


@app.on_message(filters.command("logchannel") & filters.group & ~BANNED_USERS)
async def getlog_cmd(client, message: Message):
    s = await get_chat_settings(message.chat.id)
    lc = s.get("log_channel")
    await mgmt_reply(
        message,
        f"<blockquote>{pe('DOCUMENT_RULES')} <b>ʟᴏɢ ᴄʜᴀɴɴᴇʟ:</b> <code>{lc or 'ɴᴏᴛ sᴇᴛ'}</code></blockquote>",
    )


# ---------------------------------------------------------------------------
# Connect / Disconnect
# ---------------------------------------------------------------------------
@app.on_message(filters.command("connect") & ~BANNED_USERS)
async def connect_cmd(client, message: Message):
    if not message.from_user:
        return
    if message.chat.type.name in ("GROUP", "SUPERGROUP"):
        target = message.chat.id
    elif len(message.command) > 1 and message.command[1].lstrip("-").isdigit():
        target = int(message.command[1])
    else:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/connect &lt;group_id&gt;</code></blockquote>")
    if not await is_user_admin(client, target, message.from_user.id):
        return await mgmt_reply(message, f"<blockquote>{pe('LOCK')} ʏᴏᴜ ᴍᴜsᴛ ʙᴇ ᴀɴ ᴀᴅᴍɪɴ ɪɴ <code>{target}</code> ᴛᴏ ᴄᴏɴɴᴇᴄᴛ.</blockquote>")
    await set_connection(message.from_user.id, target)
    await mgmt_reply(message, f"<blockquote>{pe('LINK')} <b>ᴄᴏɴɴᴇᴄᴛᴇᴅ ᴛᴏ ɢʀᴏᴜᴘ:</b> <code>{target}</code></blockquote>")


@app.on_message(filters.command("disconnect") & ~BANNED_USERS)
async def disconnect_cmd(client, message: Message):
    if not message.from_user:
        return
    await clear_connection(message.from_user.id)
    await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴅɪsᴄᴏɴɴᴇᴄᴛᴇᴅ ғʀᴏᴍ ɢʀᴏᴜᴘ.</b></blockquote>")


@app.on_message(filters.command("connection") & ~BANNED_USERS)
async def connection_cmd(client, message: Message):
    if not message.from_user:
        return
    cid = await get_connection(message.from_user.id)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('LINK')} <b>ᴄᴜʀʀᴇɴᴛ ᴄᴏɴɴᴇᴄᴛɪᴏɴ:</b> <code>{cid or 'ɴᴏɴᴇ'}</code></blockquote>",
    )


# ---------------------------------------------------------------------------
# Backup Export / Import
# ---------------------------------------------------------------------------
@app.on_message(filters.command("export") & filters.group & ~BANNED_USERS)
async def export_backup_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    payload = {
        "chat_id": chat_id,
        "rules": await get_rules(chat_id),
        "locks": await get_locks(chat_id),
        "welcome": {k: v for k, v in (await get_welcome(chat_id)).items() if k != "_id"},
        "blacklists": {k: v for k, v in (await get_blacklists(chat_id)).items() if k != "_id"},
        "notes_list": await list_notes(chat_id),
    }
    buf = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
    buf.name = f"aoimusic_backup_{chat_id}.json"
    await message.reply_document(
        buf,
        caption=f"<blockquote>{pe('DOWNLOAD')} <b>ɢʀᴏᴜᴘ ʙᴀᴄᴋᴜᴘ ᴇxᴘᴏʀᴛᴇᴅ</b></blockquote>",
        parse_mode=ParseMode.HTML,
    )


@app.on_message(filters.command("import") & filters.group & ~BANNED_USERS)
async def import_backup_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        return await mgmt_reply(message, f"<blockquote>{pe('UPLOAD')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ʙᴀᴄᴋᴜᴘ <code>.json</code> ғɪʟᴇ ᴛᴏ ɪᴍᴘᴏʀᴛ.</blockquote>")
    raw = await message.reply_to_message.download(in_memory=True)
    try:
        data = json.loads(raw.getvalue().decode("utf-8"))
        if data.get("rules"):
            await set_rules(chat_id, data["rules"])
        if isinstance(data.get("welcome"), dict):
            w = {k: v for k, v in data["welcome"].items() if k not in ("_id", "chat_id")}
            if w:
                await update_welcome(chat_id, w)
        await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} <b>ɢʀᴏᴜᴘ ʙᴀᴄᴋᴜᴘ ɪᴍᴘᴏʀᴛᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ.</b></blockquote>")
    except Exception as exc:
        await mgmt_reply(message, f"<blockquote>{pe('WARNING')} <b>ɪᴍᴘᴏʀᴛ ғᴀɪʟᴇᴅ:</b> <code>{esc(exc)}</code></blockquote>")


# ---------------------------------------------------------------------------
# Disable / Enable Commands
# ---------------------------------------------------------------------------
@app.on_message(filters.command(["disable", "enable"]) & filters.group & ~BANNED_USERS)
async def disable_enable_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/{message.command[0]} &lt;cmd&gt;</code></blockquote>")
    target = message.command[1].lstrip("/").lower()
    if message.command[0].lower() == "disable":
        await add_to_setting_set(chat_id, "disabled_cmds", target)
        await mgmt_reply(message, f"<blockquote>{pe('BAN_BLOCK')} ᴅɪsᴀʙʟᴇᴅ <code>/{esc(target)}</code>.</blockquote>")
    else:
        await remove_from_setting_set(chat_id, "disabled_cmds", target)
        await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} ᴇɴᴀʙʟᴇᴅ <code>/{esc(target)}</code>.</blockquote>")


@app.on_message(filters.command("disabled") & filters.group & ~BANNED_USERS)
async def disabled_list_cmd(client, message: Message):
    s = await get_chat_settings(message.chat.id)
    cmds = s.get("disabled_cmds") or []
    body = ", ".join(f"<code>/{esc(c)}</code>" for c in cmds) if cmds else "ɴᴏɴᴇ"
    await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴅɪsᴀʙʟᴇᴅ ᴄᴏᴍᴍᴀɴᴅs:</b> {body}</blockquote>")


# ---------------------------------------------------------------------------
# ID & Info
# ---------------------------------------------------------------------------
@app.on_message(filters.command("id") & ~BANNED_USERS)
async def id_cmd(client, message: Message):
    lines = [f"{pe('INFO')} <b>ɪᴅ ɪɴғᴏʀᴍᴀᴛɪᴏɴ</b>\n"]
    lines.append(f"{pe('USERS_GROUP')} <b>ᴄʜᴀᴛ ɪᴅ:</b> <code>{message.chat.id}</code>")
    if message.from_user:
        lines.append(f"{pe('USER')} <b>ʏᴏᴜʀ ɪᴅ:</b> <code>{message.from_user.id}</code>")
    if message.reply_to_message:
        r = message.reply_to_message
        if r.from_user:
            lines.append(f"{pe('USER')} <b>ʀᴇᴘʟɪᴇᴅ ᴜsᴇʀ:</b> <code>{r.from_user.id}</code>")
        if r.sticker:
            lines.append(f"{pe('STAR')} <b>sᴛɪᴄᴋᴇʀ ɪᴅ:</b> <code>{r.sticker.file_id}</code>")
    body = "\n".join(lines)
    await mgmt_reply(message, f"<blockquote>{body}</blockquote>")


@app.on_message(filters.command("info") & ~BANNED_USERS)
async def info_cmd(client, message: Message):
    user, _ = await extract_user_and_reason(client, message)
    user = user or message.from_user
    if not user:
        return
    uname = f"@{esc(user.username)}" if user.username else "ɴᴏɴᴇ"
    dc = getattr(user, "dc_id", "N/A")
    await mgmt_reply(
        message,
        f"<blockquote>{pe('USER')} <b>ᴜsᴇʀ ɪɴғᴏ</b>\n\n"
        f"• <b>ɴᴀᴍᴇ:</b> {user_mention(user)}\n"
        f"• <b>ɪᴅ:</b> <code>{user.id}</code>\n"
        f"• <b>ᴜsᴇʀɴᴀᴍᴇ:</b> {uname}\n"
        f"• <b>ᴅᴄ ɪᴅ:</b> <code>{dc}</code>\n"
        f"• <b>ʙᴏᴛ:</b> <code>{'ʏᴇs' if user.is_bot else 'ɴᴏ'}</code></blockquote>",
    )

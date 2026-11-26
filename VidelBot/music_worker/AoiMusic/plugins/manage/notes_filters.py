"""
Notes, Custom Keyword Filters & Group Rules module for AoiMusic.
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
Note: Uses /stopfilter & /delfilter so music's /stop command stays conflict-free.
"""

from __future__ import annotations

import re
from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import (
    clear_all_filters,
    clear_all_notes,
    clear_rules,
    delete_filter,
    delete_note,
    get_all_filters,
    get_note,
    get_rules,
    list_notes,
    save_filter,
    save_note,
    set_rules,
)
from AoiMusic.utils.manage_helpers import (
    NO_PREVIEW,
    check_admin_and_bot,
    esc,
    mgmt_reply,
    resolve_chat_id,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "Notes & Filters"
__HELP__ = f"""
{pe('DOCUMENT_RULES')} <b>ɴᴏᴛᴇs, ғɪʟᴛᴇʀs &amp; ʀᴜʟᴇs</b>

<b>• ɴᴏᴛᴇs</b>
<code>/save</code> &lt;name&gt; &lt;text / reply&gt; — Save a note
<code>/get</code> &lt;name&gt; or <code>#name</code> — Fetch a saved note
<code>/notes</code> / <code>/saved</code> — List all group notes
<code>/clear</code> &lt;name&gt; — Delete a note
<code>/removeallnotes</code> — Clear all notes

<b>• ғɪʟᴛᴇʀs</b>
<code>/filter</code> &lt;keyword&gt; &lt;reply&gt; — Auto-reply to a keyword
<code>/filters</code> — List active filters
<code>/stopfilter</code> / <code>/delfilter</code> &lt;keyword&gt; — Remove a filter
<code>/removeallfilters</code> — Clear all filters

<b>• ʀᴜʟᴇs</b>
<code>/rules</code> — View group rules
<code>/setrules</code> &lt;text&gt; — Set group rules
<code>/clearrules</code> — Remove group rules
"""


def _extract_media(msg: Message):
    if not msg:
        return None, None
    if msg.photo:
        return msg.photo.file_id, "photo"
    if msg.video:
        return msg.video.file_id, "video"
    if msg.document:
        return msg.document.file_id, "document"
    if msg.sticker:
        return msg.sticker.file_id, "sticker"
    if msg.animation:
        return msg.animation.file_id, "animation"
    if msg.audio:
        return msg.audio.file_id, "audio"
    if msg.voice:
        return msg.voice.file_id, "voice"
    return None, None


async def _send_saved_item(message: Message, item: dict):
    text = item.get("text") or ""
    fid = item.get("file_id")
    mtype = item.get("media_type")
    try:
        if fid and mtype == "photo":
            return await message.reply_photo(fid, caption=text or None, parse_mode=ParseMode.HTML)
        if fid and mtype == "video":
            return await message.reply_video(fid, caption=text or None, parse_mode=ParseMode.HTML)
        if fid and mtype == "document":
            return await message.reply_document(fid, caption=text or None, parse_mode=ParseMode.HTML)
        if fid and mtype == "animation":
            return await message.reply_animation(fid, caption=text or None, parse_mode=ParseMode.HTML)
        if fid and mtype == "sticker":
            return await message.reply_sticker(fid)
        if fid and mtype == "audio":
            return await message.reply_audio(fid, caption=text or None, parse_mode=ParseMode.HTML)
        if fid and mtype == "voice":
            return await message.reply_voice(fid, caption=text or None, parse_mode=ParseMode.HTML)
        if text:
            return await message.reply_text(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
@app.on_message(filters.command("rules") & ~BANNED_USERS)
async def get_rules_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ᴜsᴇ ᴛʜɪs ɪɴ ᴀ ɢʀᴏᴜᴘ ᴏʀ <code>/connect</code> ᴏɴᴇ ғɪʀsᴛ.</blockquote>")
    rules = await get_rules(chat_id)
    if not rules:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} ɴᴏ ʀᴜʟᴇs ʜᴀᴠᴇ ʙᴇᴇɴ sᴇᴛ ғᴏʀ ᴛʜɪs ɢʀᴏᴜᴘ.</blockquote>")
    await mgmt_reply(
        message,
        f"<blockquote>{pe('DOCUMENT_RULES')} <b>ɢʀᴏᴜᴘ ʀᴜʟᴇs</b>\n\n{rules}</blockquote>",
    )


@app.on_message(filters.command("setrules") & ~BANNED_USERS)
async def set_rules_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    text = (
        message.text.split(None, 1)[1]
        if len(message.command) > 1
        else (message.reply_to_message.text if message.reply_to_message and message.reply_to_message.text else "")
    )
    if not text:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/setrules &lt;rules text&gt;</code></blockquote>")
    await set_rules(chat_id, text)
    await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} <b>ɢʀᴏᴜᴘ ʀᴜʟᴇs ᴜᴘᴅᴀᴛᴇᴅ.</b></blockquote>")


@app.on_message(filters.command("clearrules") & ~BANNED_USERS)
async def clear_rules_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    await clear_rules(chat_id)
    await mgmt_reply(message, f"<blockquote>{pe('TRASH_DELETE')} <b>ɢʀᴏᴜᴘ ʀᴜʟᴇs ᴄʟᴇᴀʀᴇᴅ.</b></blockquote>")


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
@app.on_message(filters.command("save") & ~BANNED_USERS)
async def save_note_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/save &lt;name&gt; &lt;text or reply&gt;</code></blockquote>")
    name = message.command[1].lstrip("#").lower()
    text = message.text.split(None, 2)[2] if len(message.command) > 2 else ""
    fid, mtype = None, None
    if message.reply_to_message:
        fid, mtype = _extract_media(message.reply_to_message)
        if not text:
            text = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not text and not fid:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ᴘʀᴏᴠɪᴅᴇ ᴛᴇxᴛ ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ/ᴍᴇᴅɪᴀ.</blockquote>")
    await save_note(chat_id, name, text=text, file_id=fid, media_type=mtype)
    await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} <b>ɴᴏᴛᴇ sᴀᴠᴇᴅ:</b> <code>#{esc(name)}</code></blockquote>")


@app.on_message(filters.command("get") & ~BANNED_USERS)
async def get_note_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or len(message.command) < 2:
        return
    name = message.command[1].lstrip("#").lower()
    note = await get_note(chat_id, name)
    if not note:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ɴᴏᴛᴇ <code>#{esc(name)}</code> ɴᴏᴛ ғᴏᴜɴᴅ.</blockquote>")
    await _send_saved_item(message, note)


@app.on_message(filters.command(["notes", "saved"]) & ~BANNED_USERS)
async def list_notes_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id:
        return
    names = await list_notes(chat_id)
    if not names:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} ɴᴏ ɴᴏᴛᴇs sᴀᴠᴇᴅ ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</blockquote>")
    body = "\n".join(f"• <code>#{esc(n)}</code>" for n in names)
    await mgmt_reply(message, f"<blockquote>{pe('FOLDER')} <b>sᴀᴠᴇᴅ ɴᴏᴛᴇs ({len(names)})</b>\n\n{body}</blockquote>")


@app.on_message(filters.command("clear") & ~BANNED_USERS)
async def clear_note_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/clear &lt;name&gt;</code></blockquote>")
    name = message.command[1].lstrip("#").lower()
    ok = await delete_note(chat_id, name)
    if ok:
        await mgmt_reply(message, f"<blockquote>{pe('TRASH_DELETE')} ᴅᴇʟᴇᴛᴇᴅ ɴᴏᴛᴇ <code>#{esc(name)}</code>.</blockquote>")
    else:
        await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ɴᴏᴛᴇ <code>#{esc(name)}</code> ɴᴏᴛ ғᴏᴜɴᴅ.</blockquote>")


@app.on_message(filters.command("removeallnotes") & ~BANNED_USERS)
async def remove_all_notes_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    cnt = await clear_all_notes(chat_id)
    await mgmt_reply(message, f"<blockquote>{pe('TRASH_DELETE')} ᴄʟᴇᴀʀᴇᴅ <code>{cnt}</code> ɴᴏᴛᴇ(s).</blockquote>")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
@app.on_message(filters.command("filter") & ~BANNED_USERS)
async def save_filter_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/filter &lt;keyword&gt; &lt;reply or reply-to-msg&gt;</code></blockquote>")
    keyword = message.command[1].lower()
    text = message.text.split(None, 2)[2] if len(message.command) > 2 else ""
    fid, mtype = None, None
    if message.reply_to_message:
        fid, mtype = _extract_media(message.reply_to_message)
        if not text:
            text = message.reply_to_message.text or message.reply_to_message.caption or ""
    if not text and not fid:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ᴘʀᴏᴠɪᴅᴇ ʀᴇᴘʟʏ ᴛᴇxᴛ ᴏʀ ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴍᴇssᴀɢᴇ/ᴍᴇᴅɪᴀ.</blockquote>")
    await save_filter(chat_id, keyword, text=text, file_id=fid, media_type=mtype)
    await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} <b>ғɪʟᴛᴇʀ ᴀᴅᴅᴇᴅ:</b> <code>{esc(keyword)}</code></blockquote>")


@app.on_message(filters.command("filters") & ~BANNED_USERS)
async def list_filters_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id:
        return
    items = await get_all_filters(chat_id)
    if not items:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} ɴᴏ ᴀᴄᴛɪᴠᴇ ғɪʟᴛᴇʀs ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ.</blockquote>")
    body = "\n".join(f"• <code>{esc(d['keyword'])}</code>" for d in sorted(items, key=lambda x: x["keyword"]))
    await mgmt_reply(message, f"<blockquote>{pe('QUEUE_LIST')} <b>ᴀᴄᴛɪᴠᴇ ғɪʟᴛᴇʀs ({len(items)})</b>\n\n{body}</blockquote>")


@app.on_message(filters.command(["stopfilter", "delfilter"]) & ~BANNED_USERS)
async def stop_filter_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/stopfilter &lt;keyword&gt;</code></blockquote>")
    kw = message.command[1].lower()
    ok = await delete_filter(chat_id, kw)
    if ok:
        await mgmt_reply(message, f"<blockquote>{pe('TRASH_DELETE')} ʀᴇᴍᴏᴠᴇᴅ ғɪʟᴛᴇʀ <code>{esc(kw)}</code>.</blockquote>")
    else:
        await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ғɪʟᴛᴇʀ <code>{esc(kw)}</code> ɴᴏᴛ ғᴏᴜɴᴅ.</blockquote>")


@app.on_message(filters.command("removeallfilters") & ~BANNED_USERS)
async def clear_all_filters_cmd(client, message: Message):
    chat_id = await resolve_chat_id(message)
    if not chat_id or not await check_admin_and_bot(client, message, chat_id, "can_change_info"):
        return
    cnt = await clear_all_filters(chat_id)
    await mgmt_reply(message, f"<blockquote>{pe('TRASH_DELETE')} ᴄʟᴇᴀʀᴇᴅ <code>{cnt}</code> ғɪʟᴛᴇʀ(s).</blockquote>")


# ---------------------------------------------------------------------------
# Live #note & keyword filter watcher
# ---------------------------------------------------------------------------
@app.on_message(filters.group & filters.text & ~filters.service, group=21)
async def notes_and_filters_watcher(client, message: Message):
    text = (message.text or "").strip()
    if not text:
        return
    chat_id = message.chat.id

    # #notename trigger
    if text.startswith("#") and len(text) > 1:
        key = text.split()[0][1:].lower()
        note = await get_note(chat_id, key)
        if note:
            return await _send_saved_item(message, note)

    if text.startswith("/"):
        return

    items = await get_all_filters(chat_id)
    if not items:
        return
    low = text.lower()
    for item in items:
        kw = item.get("keyword", "")
        if kw and re.search(rf"(?i)(?:^|\W){re.escape(kw)}(?:$|\W)", low):
            await _send_saved_item(message, item)
            break

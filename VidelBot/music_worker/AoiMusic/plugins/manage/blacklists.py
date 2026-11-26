"""
Word & Sticker Blacklist management module for AoiMusic.
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
"""

from __future__ import annotations

import re
from pyrogram import filters
from pyrogram.types import ChatPermissions, Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import (
    add_blacklist_words,
    add_blsticker,
    get_blacklists,
    get_blstickers,
    is_approved,
    remove_blacklist_words,
    remove_blsticker,
    set_blacklist_mode,
    set_blsticker_mode,
)
from AoiMusic.utils.manage_helpers import (
    check_admin_and_bot,
    esc,
    is_user_admin,
    mgmt_reply,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "Blacklists"
__HELP__ = f"""
{pe('BAN_BLOCK')} <b>ᴡᴏʀᴅ &amp; sᴛɪᴄᴋᴇʀ ʙʟᴀᴄᴋʟɪsᴛs</b>

<b>• ᴡᴏʀᴅ ʙʟᴀᴄᴋʟɪsᴛ</b>
<code>/blacklist</code> — List blacklisted words
<code>/addblacklist</code> &lt;word1&gt; [word2...] — Add words to blacklist
<code>/unblacklist</code> &lt;word1&gt; [word2...] — Remove words from blacklist
<code>/blacklistmode</code> &lt;delete|mute|kick|ban&gt; — Set punishment

<b>• sᴛɪᴄᴋᴇʀ ʙʟᴀᴄᴋʟɪsᴛ</b>
<code>/blsticker</code> — List blacklisted sticker packs
<code>/addblsticker</code> — Reply to a sticker to blacklist its pack
<code>/unblsticker</code> / <code>/rmblsticker</code> — Remove replied sticker pack
<code>/blstickermode</code> &lt;delete|mute|kick|ban&gt; — Set punishment
"""

VALID_MODES = {"delete", "mute", "kick", "ban"}


async def _punish_blacklist(client, message: Message, mode: str) -> None:
    try:
        await message.delete()
    except Exception:
        pass
    if not message.from_user or mode == "delete":
        return
    chat_id = message.chat.id
    uid = message.from_user.id
    try:
        if mode == "mute":
            await client.restrict_chat_member(chat_id, uid, ChatPermissions())
        elif mode == "kick":
            await client.ban_chat_member(chat_id, uid)
            await client.unban_chat_member(chat_id, uid)
        elif mode == "ban":
            await client.ban_chat_member(chat_id, uid)
    except Exception:
        pass


@app.on_message(filters.command("blacklist") & filters.group & ~BANNED_USERS)
async def list_blacklist_cmd(client, message: Message):
    data = await get_blacklists(message.chat.id)
    words = data.get("words") or []
    mode = data.get("mode", "delete")
    if not words:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} ɴᴏ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴡᴏʀᴅs ɪɴ ᴛʜɪs ɢʀᴏᴜᴘ. (ᴍᴏᴅᴇ: <code>{mode}</code>)</blockquote>",
        )
    body = ", ".join(f"<code>{esc(w)}</code>" for w in sorted(words))
    await mgmt_reply(
        message,
        f"<blockquote>{pe('BAN_BLOCK')} <b>ʙʟᴀᴄᴋʟɪsᴛᴇᴅ ᴡᴏʀᴅs ({len(words)})</b> — ᴍᴏᴅᴇ: <code>{mode}</code>\n\n{body}</blockquote>",
    )


@app.on_message(filters.command("addblacklist") & filters.group & ~BANNED_USERS)
async def add_blacklist_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/addblacklist word1 word2</code></blockquote>",
        )
    words = message.command[1:]
    await add_blacklist_words(chat_id, words)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} ᴀᴅᴅᴇᴅ <code>{len(words)}</code> ᴡᴏʀᴅ(s) ᴛᴏ ʙʟᴀᴄᴋʟɪsᴛ.</blockquote>",
    )


@app.on_message(filters.command("unblacklist") & filters.group & ~BANNED_USERS)
async def rm_blacklist_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/unblacklist word1</code></blockquote>",
        )
    words = message.command[1:]
    await remove_blacklist_words(chat_id, words)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} ʀᴇᴍᴏᴠᴇᴅ <code>{len(words)}</code> ᴡᴏʀᴅ(s) ғʀᴏᴍ ʙʟᴀᴄᴋʟɪsᴛ.</blockquote>",
    )


@app.on_message(filters.command("blacklistmode") & filters.group & ~BANNED_USERS)
async def bl_mode_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in VALID_MODES:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴏᴘᴛɪᴏɴs:</b> <code>delete</code>, <code>mute</code>, <code>kick</code>, <code>ban</code></blockquote>",
        )
    mode = message.command[1].lower()
    await set_blacklist_mode(chat_id, mode)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ʙʟᴀᴄᴋʟɪsᴛ ᴍᴏᴅᴇ sᴇᴛ ᴛᴏ:</b> <code>{mode}</code></blockquote>",
    )


@app.on_message(filters.command("blsticker") & filters.group & ~BANNED_USERS)
async def list_blsticker_cmd(client, message: Message):
    data = await get_blstickers(message.chat.id)
    sets = data.get("sets") or []
    mode = data.get("mode", "delete")
    if not sets:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} ɴᴏ ʙʟᴀᴄᴋʟɪsᴛᴇᴅ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋs. (ᴍᴏᴅᴇ: <code>{mode}</code>)</blockquote>",
        )
    body = "\n".join(f"• <code>{esc(s)}</code>" for s in sorted(sets))
    await mgmt_reply(
        message,
        f"<blockquote>{pe('BAN_BLOCK')} <b>ʙʟᴀᴄᴋʟɪsᴛᴇᴅ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋs</b> — ᴍᴏᴅᴇ: <code>{mode}</code>\n\n{body}</blockquote>",
    )


@app.on_message(filters.command("addblsticker") & filters.group & ~BANNED_USERS)
async def add_blsticker_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    reply = message.reply_to_message
    set_name = None
    if reply and reply.sticker and reply.sticker.set_name:
        set_name = reply.sticker.set_name
    elif len(message.command) > 1:
        set_name = message.command[1].strip()
    if not set_name:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ sᴛɪᴄᴋᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴀ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋ ɴᴀᴍᴇ.</blockquote>",
        )
    await add_blsticker(chat_id, set_name)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} ʙʟᴀᴄᴋʟɪsᴛᴇᴅ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋ: <code>{esc(set_name)}</code></blockquote>",
    )


@app.on_message(filters.command(["unblsticker", "rmblsticker"]) & filters.group & ~BANNED_USERS)
async def rm_blsticker_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    reply = message.reply_to_message
    set_name = None
    if reply and reply.sticker and reply.sticker.set_name:
        set_name = reply.sticker.set_name
    elif len(message.command) > 1:
        set_name = message.command[1].strip()
    if not set_name:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ sᴛɪᴄᴋᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴀ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋ ɴᴀᴍᴇ.</blockquote>",
        )
    await remove_blsticker(chat_id, set_name)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} ʀᴇᴍᴏᴠᴇᴅ sᴛɪᴄᴋᴇʀ ᴘᴀᴄᴋ <code>{esc(set_name)}</code> ғʀᴏᴍ ʙʟᴀᴄᴋʟɪsᴛ.</blockquote>",
    )


@app.on_message(filters.command("blstickermode") & filters.group & ~BANNED_USERS)
async def blsticker_mode_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in VALID_MODES:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴏᴘᴛɪᴏɴs:</b> <code>delete</code>, <code>mute</code>, <code>kick</code>, <code>ban</code></blockquote>",
        )
    mode = message.command[1].lower()
    await set_blsticker_mode(chat_id, mode)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>sᴛɪᴄᴋᴇʀ ʙʟᴀᴄᴋʟɪsᴛ ᴍᴏᴅᴇ:</b> <code>{mode}</code></blockquote>",
    )


@app.on_message(filters.group & ~filters.service, group=17)
async def blacklist_watcher(client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    uid = message.from_user.id

    # Sticker blacklist check
    if message.sticker and message.sticker.set_name:
        sdata = await get_blstickers(chat_id)
        if message.sticker.set_name.lower() in (sdata.get("sets") or []):
            if not await is_user_admin(client, chat_id, uid) and not await is_approved(chat_id, uid):
                return await _punish_blacklist(client, message, sdata.get("mode", "delete"))

    # Word blacklist check
    text = (message.text or message.caption or "").lower()
    if not text:
        return
    wdata = await get_blacklists(chat_id)
    words = wdata.get("words") or []
    if not words:
        return
    for w in words:
        if re.search(rf"(?i)(?:^|\W){re.escape(w)}(?:$|\W)", text):
            if not await is_user_admin(client, chat_id, uid) and not await is_approved(chat_id, uid):
                return await _punish_blacklist(client, message, wdata.get("mode", "delete"))
            break

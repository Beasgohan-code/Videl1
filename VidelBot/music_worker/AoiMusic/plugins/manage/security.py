"""
Group Security Suite for AoiMusic:
Anti-Flood, Anti-Edit, Bio-Link Guard, Anti-Abuse, NSFW Guard,
Auto-Demote, Anti-Banall Lockdown Trap, and Bluetext Cleaner.
Uses Kurigram 2.2.27 + Motor MongoDB + Musiciconis premium emojis.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from pyrogram import filters
from pyrogram.enums import ChatMembersFilter
from pyrogram.types import ChatPermissions, ChatPrivileges, Message

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.utils.manage_db import (
    add_to_setting_set,
    get_chat_settings,
    get_flood_config,
    is_approved,
    remove_from_setting_set,
    set_chat_setting,
    set_flood_limit,
    set_flood_mode,
)
from AoiMusic.utils.manage_helpers import (
    check_admin_and_bot,
    esc,
    extract_user_and_reason,
    is_user_admin,
    is_user_owner,
    log_mgmt_action,
    mgmt_reply,
    user_mention,
)
from AoiMusic.utils.premium_emoji import pe

__MODULE__ = "Security"
__HELP__ = f"""
{pe('SHIELD_CHECK')} <b>ɢʀᴏᴜᴘ sᴇᴄᴜʀɪᴛʏ &amp; ᴘʀᴏᴛᴇᴄᴛɪᴏɴ</b>

<b>• ᴀɴᴛɪ-ғʟᴏᴏᴅ</b>
<code>/setflood</code> &lt;count|off&gt; — Max consecutive messages (e.g. <code>5</code>)
<code>/setfloodmode</code> &lt;mute|kick|ban&gt; — Punishment for flooders
<code>/flood</code> — View current flood settings

<b>• ʙɪᴏ-ʟɪɴᴋ, ᴀɴᴛɪ-ᴇᴅɪᴛ &amp; ᴄʟᴇᴀɴᴇʀ</b>
<code>/biolink</code> &lt;on|off&gt; — Block users with links in bio
<code>/biofree</code>, <code>/biounfree</code>, <code>/biofreelist</code> — Exempt users from bio-link check
<code>/antiedit</code> &lt;on|off&gt; — Auto-delete edited messages by non-admins
<code>/cleanblue</code> &lt;on|off&gt; — Delete bot <code>/commands</code> after use
<code>/ignoreblue</code>, <code>/unignoreblue</code>, <code>/listblue</code> — Ignore commands from cleaner

<b>• ᴀɴᴛɪ-ᴀʙᴜsᴇ, ɴsғᴡ &amp; ʟᴏᴄᴋᴅᴏᴡɴ</b>
<code>/abuse</code> &lt;on|off&gt; — Auto-delete abusive words
<code>/nsfw</code> &lt;on|off&gt; — Toggle NSFW keyword/media guard
<code>/autodemote</code> &lt;on|off&gt; — Toggle auto-demote protection
<code>/lockdown</code> &lt;on|off&gt; — Emergency chat lockdown (owner/sudo)
<i>ᴛʀɪɢɢᴇʀs:</i> <code>banall</code> (trap) &amp; <code>gcfree</code> (lift lockdown)
"""

# In-memory flood tracker: chat_id -> (last_user_id, count, last_ts)
_FLOOD_STATE: dict[int, list] = {}
# Bio cache: user_id -> (has_link, checked_at)
_BIO_CACHE: dict[int, tuple[bool, float]] = {}

_LINK_RE = re.compile(r"(https?://\S+|t\.me/\S+|@\w{4,}|www\.\S+)", re.IGNORECASE)
_ABUSE_WORDS = {
    "madarchod", "bhenchod", "bhosdike", "bsdk", "chutiya", "gandu", "randi",
    "mc", "bc", "harami", "kutta", "kamina", "fuck", "fucker", "motherfucker",
    "bitch", "asshole", "bastard", "slut", "whore",
}
_NSFW_WORDS = {"porn", "xxx", "hentai", "nudes", "sexvideo", "xnxx", "xvideos", "pornhub"}
_ABUSE_STATS: dict[int, int] = defaultdict(int)


# ---------------------------------------------------------------------------
# Anti-Flood
# ---------------------------------------------------------------------------
@app.on_message(filters.command("setflood") & filters.group & ~BANNED_USERS)
async def set_flood_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/setflood 5</code> ᴏʀ <code>/setflood off</code></blockquote>",
        )
    arg = message.command[1].lower()
    if arg in ("off", "no", "0"):
        await set_flood_limit(chat_id, 0)
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('CHECK_CIRCLE')} <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ ᴅɪsᴀʙʟᴇᴅ.</b></blockquote>",
        )
    if not arg.isdigit() or int(arg) < 2:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ᴘʀᴏᴠɪᴅᴇ ᴀ ɴᴜᴍʙᴇʀ ≥ 2 ᴏʀ <code>off</code>.</blockquote>",
        )
    val = min(50, int(arg))
    await set_flood_limit(chat_id, val)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('SHIELD_CHECK')} <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ sᴇᴛ ᴛᴏ:</b> <code>{val}</code> ᴄᴏɴsᴇᴄᴜᴛɪᴠᴇ ᴍᴇssᴀɢᴇs.</blockquote>",
    )


@app.on_message(filters.command("setfloodmode") & filters.group & ~BANNED_USERS)
async def set_flood_mode_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_restrict_members"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in ("mute", "kick", "ban"):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>ᴏᴘᴛɪᴏɴs:</b> <code>mute</code>, <code>kick</code>, <code>ban</code></blockquote>",
        )
    mode = message.command[1].lower()
    await set_flood_mode(chat_id, mode)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHECK_CIRCLE')} <b>ғʟᴏᴏᴅ ᴍᴏᴅᴇ sᴇᴛ ᴛᴏ:</b> <code>{mode}</code></blockquote>",
    )


@app.on_message(filters.command("flood") & filters.group & ~BANNED_USERS)
async def flood_status_cmd(client, message: Message):
    cfg = await get_flood_config(message.chat.id)
    lim = int(cfg.get("limit", 0))
    mode = cfg.get("mode", "mute")
    status = f"<code>{lim}</code> ᴍsɢs (ᴍᴏᴅᴇ: <code>{mode}</code>)" if lim else "<code>ᴏғғ</code>"
    await mgmt_reply(
        message,
        f"<blockquote>{pe('SHIELD_CHECK')} <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ:</b> {status}</blockquote>",
    )


# ---------------------------------------------------------------------------
# Simple On/Off Security Toggles
# ---------------------------------------------------------------------------
async def _toggle_bool_setting(client, message: Message, key: str, label: str):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        s = await get_chat_settings(chat_id)
        cur = "ᴏɴ" if s.get(key) else "ᴏғғ"
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('INFO')} <b>{label}:</b> <code>{cur}</code>\n"
            f"<b>ᴜsᴀɢᴇ:</b> <code>/{message.command[0]} on</code> ᴏʀ <code>off</code></blockquote>",
        )
    state = message.command[1].lower() == "on"
    await set_chat_setting(chat_id, key, state)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('SHIELD_CHECK')} <b>{label} {'ᴇɴᴀʙʟᴇᴅ' if state else 'ᴅɪsᴀʙʟᴇᴅ'}.</b></blockquote>",
    )


@app.on_message(filters.command("biolink") & filters.group & ~BANNED_USERS)
async def biolink_cmd(client, message: Message):
    await _toggle_bool_setting(client, message, "biolink", "ʙɪᴏ-ʟɪɴᴋ ɢᴜᴀʀᴅ")


@app.on_message(filters.command(["biofree", "biounfree"]) & filters.group & ~BANNED_USERS)
async def biofree_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    user, _ = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.</blockquote>",
        )
    if message.command[0].lower() == "biofree":
        await add_to_setting_set(chat_id, "biofree", user.id)
        await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} {user_mention(user)} ᴇxᴇᴍᴘᴛᴇᴅ ғʀᴏᴍ ʙɪᴏ-ʟɪɴᴋ ᴄʜᴇᴄᴋ.</blockquote>")
    else:
        await remove_from_setting_set(chat_id, "biofree", user.id)
        await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} {user_mention(user)} ʀᴇᴍᴏᴠᴇᴅ ғʀᴏᴍ ʙɪᴏ-ғʀᴇᴇ ʟɪsᴛ.</blockquote>")


@app.on_message(filters.command("biofreelist") & filters.group & ~BANNED_USERS)
async def biofreelist_cmd(client, message: Message):
    s = await get_chat_settings(message.chat.id)
    ids = s.get("biofree") or []
    body = "\n".join(f"• <code>{u}</code>" for u in ids) if ids else "ɴᴏ ᴇxᴇᴍᴘᴛ ᴜsᴇʀs."
    await mgmt_reply(message, f"<blockquote>{pe('SHIELD_CHECK')} <b>ʙɪᴏ-ғʀᴇᴇ ᴜsᴇʀs ({len(ids)})</b>\n\n{body}</blockquote>")


@app.on_message(filters.command("antiedit") & filters.group & ~BANNED_USERS)
async def antiedit_cmd(client, message: Message):
    await _toggle_bool_setting(client, message, "antiedit", "ᴀɴᴛɪ-ᴇᴅɪᴛ")


@app.on_message(filters.command("abuse") & filters.group & ~BANNED_USERS)
async def abuse_cmd(client, message: Message):
    await _toggle_bool_setting(client, message, "abuse", "ᴀɴᴛɪ-ᴀʙᴜsᴇ ғɪʟᴛᴇʀ")


@app.on_message(filters.command("abusestats") & filters.group & ~BANNED_USERS)
async def abusestats_cmd(client, message: Message):
    cnt = _ABUSE_STATS.get(message.chat.id, 0)
    await mgmt_reply(
        message,
        f"<blockquote>{pe('CHART_STATS')} <b>ᴀʙᴜsᴇ ᴍᴇssᴀɢᴇs ʙʟᴏᴄᴋᴇᴅ (ᴛʜɪs sᴇssɪᴏɴ):</b> <code>{cnt}</code></blockquote>",
    )


@app.on_message(filters.command("nsfw") & filters.group & ~BANNED_USERS)
async def nsfw_cmd(client, message: Message):
    await _toggle_bool_setting(client, message, "nsfw", "ɴsғᴡ ɢᴜᴀʀᴅ")


@app.on_message(filters.command(["nsfwfree", "nsfwunfree"]) & filters.group & ~BANNED_USERS)
async def nsfwfree_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    user, _ = await extract_user_and_reason(client, message)
    if not user:
        return await mgmt_reply(message, f"<blockquote>{pe('WARNING')} ʀᴇᴘʟʏ ᴛᴏ ᴀ ᴜsᴇʀ ᴏʀ ᴘʀᴏᴠɪᴅᴇ ᴜsᴇʀɴᴀᴍᴇ/ɪᴅ.</blockquote>")
    if message.command[0].lower() == "nsfwfree":
        await add_to_setting_set(chat_id, "nsfwfree", user.id)
        await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} {user_mention(user)} ᴀᴅᴅᴇᴅ ᴛᴏ ɴsғᴡ-ғʀᴇᴇ ʟɪsᴛ.</blockquote>")
    else:
        await remove_from_setting_set(chat_id, "nsfwfree", user.id)
        await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} {user_mention(user)} ʀᴇᴍᴏᴠᴇᴅ ғʀᴏᴍ ɴsғᴡ-ғʀᴇᴇ ʟɪsᴛ.</blockquote>")


@app.on_message(filters.command("nsfwlist") & filters.group & ~BANNED_USERS)
async def nsfwlist_cmd(client, message: Message):
    s = await get_chat_settings(message.chat.id)
    ids = s.get("nsfwfree") or []
    body = "\n".join(f"• <code>{u}</code>" for u in ids) if ids else "ɴᴏ ᴇxᴇᴍᴘᴛ ᴜsᴇʀs."
    await mgmt_reply(message, f"<blockquote>{pe('SHIELD_CHECK')} <b>ɴsғᴡ-ғʀᴇᴇ ᴜsᴇʀs ({len(ids)})</b>\n\n{body}</blockquote>")


@app.on_message(filters.command(["autodemote", "setautodemote"]) & filters.group & ~BANNED_USERS)
async def autodemote_cmd(client, message: Message):
    await _toggle_bool_setting(client, message, "autodemote", "ᴀᴜᴛᴏ-ᴅᴇᴍᴏᴛᴇ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ")


@app.on_message(filters.command("cleanblue") & filters.group & ~BANNED_USERS)
async def cleanblue_cmd(client, message: Message):
    await _toggle_bool_setting(client, message, "cleanblue", "ʙʟᴜᴇᴛᴇxᴛ ᴄʟᴇᴀɴᴇʀ")


@app.on_message(filters.command(["ignoreblue", "unignoreblue"]) & filters.group & ~BANNED_USERS)
async def ignoreblue_cmd(client, message: Message):
    chat_id = message.chat.id
    if not await check_admin_and_bot(client, message, chat_id, "can_delete_messages"):
        return
    if len(message.command) < 2:
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/{message.command[0]} play</code></blockquote>")
    cmd_word = message.command[1].lstrip("/").lower()
    if message.command[0].lower() == "ignoreblue":
        await add_to_setting_set(chat_id, "ignoreblue", cmd_word)
        await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} ɪɢɴᴏʀɪɴɢ <code>/{esc(cmd_word)}</code> ɪɴ ʙʟᴜᴇᴛᴇxᴛ ᴄʟᴇᴀɴᴇʀ.</blockquote>")
    else:
        await remove_from_setting_set(chat_id, "ignoreblue", cmd_word)
        await mgmt_reply(message, f"<blockquote>{pe('CHECK_CIRCLE')} sᴛᴏᴘᴘᴇᴅ ɪɢɴᴏʀɪɴɢ <code>/{esc(cmd_word)}</code>.</blockquote>")


@app.on_message(filters.command("listblue") & filters.group & ~BANNED_USERS)
async def listblue_cmd(client, message: Message):
    s = await get_chat_settings(message.chat.id)
    cmds = s.get("ignoreblue") or []
    body = ", ".join(f"<code>/{esc(c)}</code>" for c in cmds) if cmds else "ɴᴏɴᴇ"
    await mgmt_reply(
        message,
        f"<blockquote>{pe('INFO')} <b>ʙʟᴜᴇᴛᴇxᴛ ᴄʟᴇᴀɴᴇʀ:</b> <code>{'ᴏɴ' if s.get('cleanblue') else 'ᴏғғ'}</code>\n"
        f"<b>ɪɢɴᴏʀᴇᴅ:</b> {body}</blockquote>",
    )


# ---------------------------------------------------------------------------
# Lockdown & Anti-Banall Trap
# ---------------------------------------------------------------------------
async def _set_lockdown(client, chat_id: int, state: bool) -> None:
    await set_chat_setting(chat_id, "lockdown", state)
    perms = (
        ChatPermissions()
        if state
        else ChatPermissions(
            can_send_messages=True,
            can_send_media_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True,
            can_invite_users=True,
        )
    )
    try:
        await client.set_chat_permissions(chat_id, perms)
    except Exception:
        pass


@app.on_message(filters.command("lockdown") & filters.group & ~BANNED_USERS)
async def lockdown_cmd(client, message: Message):
    chat_id = message.chat.id
    if not message.from_user or not await is_user_owner(client, chat_id, message.from_user.id):
        return await mgmt_reply(
            message,
            f"<blockquote>{pe('LOCK')} ᴏɴʟʏ ᴛʜᴇ ɢʀᴏᴜᴘ ᴏᴡɴᴇʀ ᴏʀ ʙᴏᴛ sᴜᴅᴏ ᴄᴀɴ ᴛᴏɢɢʟᴇ ʟᴏᴄᴋᴅᴏᴡɴ.</blockquote>",
        )
    if len(message.command) < 2 or message.command[1].lower() not in ("on", "off"):
        return await mgmt_reply(message, f"<blockquote>{pe('INFO')} <b>ᴜsᴀɢᴇ:</b> <code>/lockdown on</code> ᴏʀ <code>off</code></blockquote>")
    state = message.command[1].lower() == "on"
    await _set_lockdown(client, chat_id, state)
    icon = pe("LOCK") if state else pe("UNLOCK")
    await mgmt_reply(
        message,
        f"<blockquote>{icon} <b>ɢʀᴏᴜᴘ ʟᴏᴄᴋᴅᴏᴡɴ {'ᴀᴄᴛɪᴠᴀᴛᴇᴅ' if state else 'ʟɪғᴛᴇᴅ'}.</b></blockquote>",
    )


# ---------------------------------------------------------------------------
# Unified Live Security Watchers
# ---------------------------------------------------------------------------
@app.on_edited_message(filters.group & ~filters.service, group=18)
async def antiedit_watcher(client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    s = await get_chat_settings(chat_id)
    if not s.get("antiedit"):
        return
    if await is_user_admin(client, chat_id, message.from_user.id):
        return
    if await is_approved(chat_id, message.from_user.id):
        return
    try:
        await message.delete()
    except Exception:
        pass


@app.on_message(filters.group & ~filters.service, group=19)
async def security_watcher(client, message: Message):
    if not message.from_user:
        return
    chat_id = message.chat.id
    uid = message.from_user.id
    text = (message.text or message.caption or "").strip()
    s = await get_chat_settings(chat_id)

    # 1. Anti-Banall Trap & gcfree
    if text:
        if re.search(r"(?i)\bgc\s?free\b", text):
            if await is_user_owner(client, chat_id, uid):
                await _set_lockdown(client, chat_id, False)
                await mgmt_reply(
                    message,
                    f"<blockquote>{pe('UNLOCK')} <b>ʟᴏᴄᴋᴅᴏᴡɴ ʟɪғᴛᴇᴅ</b> — ᴍᴇssᴀɢɪɴɢ ʀᴇsᴛᴏʀᴇᴅ.</blockquote>",
                )
            return
        if re.search(r"(?i)\bban\s?all\b", text):
            if not await is_user_owner(client, chat_id, uid):
                try:
                    await message.delete()
                except Exception:
                    pass
                await _set_lockdown(client, chat_id, True)
                # Demote non-owner admins if bot has promote rights
                try:
                    me = await client.get_me()
                    async for m in client.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
                        if m.user.id == me.id or await is_user_owner(client, chat_id, m.user.id):
                            continue
                        try:
                            await client.promote_chat_member(chat_id, m.user.id, privileges=ChatPrivileges())
                        except Exception:
                            pass
                except Exception:
                    pass
                alert = (
                    f"<blockquote>{pe('WARNING')} <b>ᴀɴᴛɪ-ʙᴀɴᴀʟʟ ʟᴏᴄᴋᴅᴏᴡɴ ᴛʀɪɢɢᴇʀᴇᴅ</b>\n"
                    f"{pe('USER')} <b>ᴛʀɪɢɢᴇʀᴇᴅ ʙʏ:</b> {user_mention(message.from_user)}\n"
                    f"ᴜsᴇ <code>gcfree</code> (ᴏᴡɴᴇʀ/sᴜᴅᴏ) ᴛᴏ ʀᴇsᴛᴏʀᴇ ᴛʜᴇ ɢʀᴏᴜᴘ.</blockquote>"
                )
                await mgmt_reply(message, alert)
                await log_mgmt_action(client, chat_id, alert)
                return

    # Skip further checks for admins & approved users
    if await is_user_admin(client, chat_id, uid) or await is_approved(chat_id, uid):
        return

    # 2. Active Lockdown Watcher
    if s.get("lockdown"):
        try:
            await message.delete()
        except Exception:
            pass
        return

    # 3. Bluetext Cleaner
    if s.get("cleanblue") and text.startswith("/"):
        cmd_word = text.split()[0].lstrip("/").split("@")[0].lower()
        ignored = s.get("ignoreblue") or []
        if cmd_word not in ignored:
            try:
                await message.delete()
            except Exception:
                pass

    # 4. Anti-Abuse & NSFW keyword check
    if text:
        words_set = set(re.findall(r"[a-zA-Z0-9]+", text.lower()))
        if s.get("abuse") and (words_set & _ABUSE_WORDS):
            try:
                await message.delete()
                _ABUSE_STATS[chat_id] += 1
            except Exception:
                pass
            return
        if s.get("nsfw") and uid not in (s.get("nsfwfree") or []) and (words_set & _NSFW_WORDS):
            try:
                await message.delete()
            except Exception:
                pass
            return

    # 5. Bio-Link Guard (cached 10 min per user)
    if s.get("biolink") and uid not in (s.get("biofree") or []):
        now = time.time()
        cached = _BIO_CACHE.get(uid)
        if cached and now - cached[1] < 600:
            has_link = cached[0]
        else:
            has_link = False
            try:
                full_chat = await client.get_chat(uid)
                bio = getattr(full_chat, "bio", "") or ""
                has_link = bool(_LINK_RE.search(bio))
            except Exception:
                has_link = False
            _BIO_CACHE[uid] = (has_link, now)
        if has_link:
            try:
                await message.delete()
            except Exception:
                pass
            return

    # 6. Anti-Flood
    fcfg = await get_flood_config(chat_id)
    limit = int(fcfg.get("limit", 0))
    if limit >= 2:
        now = time.time()
        st = _FLOOD_STATE.get(chat_id)
        if st and st[0] == uid and now - st[2] < 10:
            st[1] += 1
            st[2] = now
        else:
            st = [uid, 1, now]
            _FLOOD_STATE[chat_id] = st
        if st[1] >= limit:
            _FLOOD_STATE.pop(chat_id, None)
            mode = fcfg.get("mode", "mute")
            try:
                if mode == "ban":
                    await client.ban_chat_member(chat_id, uid)
                    act = "ʙᴀɴɴᴇᴅ"
                elif mode == "kick":
                    await client.ban_chat_member(chat_id, uid)
                    await client.unban_chat_member(chat_id, uid)
                    act = "ᴋɪᴄᴋᴇᴅ"
                else:
                    await client.restrict_chat_member(chat_id, uid, ChatPermissions())
                    act = "ᴍᴜᴛᴇᴅ"
                await mgmt_reply(
                    message,
                    f"<blockquote>{pe('WARNING')} <b>ᴀɴᴛɪ-ғʟᴏᴏᴅ</b> — {user_mention(message.from_user)} ᴡᴀs <b>{act}</b> ғᴏʀ ғʟᴏᴏᴅɪɴɢ.</blockquote>",
                )
            except Exception:
                pass

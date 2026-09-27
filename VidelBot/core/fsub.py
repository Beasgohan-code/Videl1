"""
Main-bot Force Subscribe (from the Son-Goku controller's FSUB_CHANNEL + FORCE_MSG/FORCE_PIC,
and the saver's /add_unsubscribe · /del_unsubscribe stubs – now fully implemented).

Channels = FSUB_CHANNELS env + channels added at runtime with /add_fsub.
Supports normal join and join-request mode (a pending request counts as joined).
"""
import logging
import time

from pyrogram import Client, StopPropagation, enums, filters
from pyrogram.errors import UserNotParticipant
from pyrogram.types import (CallbackQuery, ChatJoinRequest, InlineKeyboardButton as Btn,
                            InlineKeyboardMarkup, Message)

from config import ADMINS, FORCE_PIC, FSUB_CHANNELS, FSUB_REQUEST_MODE
from core.db import vdb
from core.texts import FORCE_MSG
from core.ui import send_with_preview, smart_edit

log = logging.getLogger("videl.fsub")

_ok_cache: dict[int, float] = {}          # user_id -> time of last successful check
_chat_cache: dict = {}                    # chat -> (title, invite_link, ts)
_last_prompt: dict[int, float] = {}       # user_id -> last time FORCE_MSG was sent (anti-spam)
OK_TTL = 600
CHAT_TTL = 3600
_ALLOWED = {enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED}


async def all_channels() -> list:
    extra = await vdb.get_setting("fsub_channels", []) or []
    out = []
    for c in list(FSUB_CHANNELS) + list(extra):
        if c not in out:
            out.append(c)
    return out


async def _chat_info(client: Client, chat):
    hit = _chat_cache.get(chat)
    if hit and time.time() - hit[2] < CHAT_TTL:
        return hit[0], hit[1]
    title, link = str(chat), None
    try:
        c = await client.get_chat(chat)
        title = c.title or title
        if c.username and not FSUB_REQUEST_MODE:
            link = f"https://t.me/{c.username}"
        else:
            inv = await client.create_chat_invite_link(c.id, creates_join_request=FSUB_REQUEST_MODE or None,
                                                       name="Videl force-sub")
            link = inv.invite_link
    except Exception as e:
        log.warning(f"fsub chat {chat} unavailable (is the bot admin there?): {e}")
    _chat_cache[chat] = (title, link, time.time())
    return title, link


async def missing_channels(client: Client, user_id: int) -> list:
    """Channels the user still has to join (empty list → allowed)."""
    if user_id in ADMINS:
        return []
    ts = _ok_cache.get(user_id)
    if ts and time.time() - ts < OK_TTL:
        return []
    channels = await all_channels()
    if not channels:
        return []
    missing = []
    for chat in channels:
        try:
            member = await client.get_chat_member(chat, user_id)
            if member.status in _ALLOWED and (member.status != enums.ChatMemberStatus.RESTRICTED or member.is_member):
                continue
        except UserNotParticipant:
            pass
        except Exception as e:
            # Misconfigured channel → don't lock everybody out.
            log.warning(f"fsub check failed for {chat}: {e}")
            continue
        if FSUB_REQUEST_MODE and await vdb.db["fsub_requests"].find_one({"chat": str(chat), "user": user_id}):
            continue
        missing.append(chat)
    if not missing:
        _ok_cache[user_id] = time.time()
    return missing


async def force_markup(client: Client, missing: list) -> InlineKeyboardMarkup:
    kb = []
    for chat in missing:
        title, link = await _chat_info(client, chat)
        if link:
            kb.append([Btn(f"📢 Join {title}"[:60], url=link)])
    kb.append([Btn("♻️ Reload", callback_data="fsub_reload")])
    return InlineKeyboardMarkup(kb)


# ─────────────────────────── gates (group -2) ───────────────────────────
@Client.on_message(filters.private & filters.incoming & ~filters.successful_payment, group=-2)
async def fsub_gate_messages(client: Client, message: Message):
    if not message.from_user:
        return
    missing = await missing_channels(client, message.from_user.id)
    if not missing:
        return
    uid = message.from_user.id
    if time.time() - _last_prompt.get(uid, 0) < 20 and not (message.text or "").startswith("/start"):
        raise StopPropagation
    _last_prompt[uid] = time.time()
    await send_with_preview(client, message.chat.id, FORCE_MSG.format(mention=message.from_user.mention),
                            await force_markup(client, missing), pic=FORCE_PIC, reply_to=message.id)
    raise StopPropagation


@Client.on_callback_query(~filters.regex(r"^fsub_reload$"), group=-2)
async def fsub_gate_callbacks(client: Client, query: CallbackQuery):
    if not query.message or query.message.chat.type != enums.ChatType.PRIVATE:
        return
    if not await missing_channels(client, query.from_user.id):
        return
    await query.answer("🔒 Join the required channel(s) first, then tap ♻️ Reload.", show_alert=True)
    raise StopPropagation


@Client.on_callback_query(filters.regex(r"^fsub_reload$"))
async def fsub_reload(client: Client, query: CallbackQuery):
    _ok_cache.pop(query.from_user.id, None)
    missing = await missing_channels(client, query.from_user.id)
    if missing:
        await query.answer("❌ You haven't joined all channels yet!", show_alert=True)
        try:
            await query.message.edit_reply_markup(await force_markup(client, missing))
        except Exception:
            pass
        return
    await query.answer("✅ Thanks for joining!")
    from core.menus import render_home
    await render_home(client, query)


@Client.on_chat_join_request()
async def record_join_request(client: Client, request: ChatJoinRequest):
    """Join-request mode: remember pending requests so they count as joined."""
    channels = [str(c) for c in await all_channels()]
    chat_keys = {str(request.chat.id)}
    if request.chat.username:
        chat_keys.add(request.chat.username)
    for key in chat_keys & set(channels):
        await vdb.db["fsub_requests"].update_one(
            {"chat": key, "user": request.from_user.id},
            {"$set": {"chat": key, "user": request.from_user.id, "ts": time.time()}}, upsert=True)
    _ok_cache.pop(request.from_user.id, None)


# ─────────────────────────── admin commands ───────────────────────────
def _parse_chat(arg: str):
    arg = arg.strip()
    return int(arg) if arg.lstrip("-").isdigit() else arg.lstrip("@").replace("https://t.me/", "")


@Client.on_message(filters.command(["add_fsub", "add_unsubscribe"]) & filters.user(ADMINS))
async def add_fsub(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("<b>Usage:</b> <code>/add_fsub -100xxxxxxxxxx</code> or <code>/add_fsub @channel</code>\n"
                                        "<i>The bot must be admin in that channel.</i>")
    chat = _parse_chat(message.command[1])
    try:
        c = await client.get_chat(chat)
        me = await client.get_chat_member(c.id, "me")
        if me.status not in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER):
            return await message.reply_text("❌ Make me an admin in that channel first.")
    except Exception as e:
        return await message.reply_text(f"❌ Can't access that chat: <code>{e}</code>")
    extra = await vdb.get_setting("fsub_channels", []) or []
    if c.id in extra or c.id in FSUB_CHANNELS:
        return await message.reply_text("ℹ️ Already in the force-sub list.")
    extra.append(c.id)
    await vdb.set_setting("fsub_channels", extra)
    _ok_cache.clear()
    from core import botlog
    await botlog.event("FsubAdded", f"<b>📢 Channel:</b> {botlog.esc(c.title)} (<code>{c.id}</code>)\n"
                                    f"<b>👮 By:</b> <code>{message.from_user.id}</code>", client=client)
    await message.reply_text(f"✅ Added <b>{c.title}</b> (<code>{c.id}</code>) to force-subscribe.")


@Client.on_message(filters.command(["del_fsub", "del_unsubscribe", "rem_fsub"]) & filters.user(ADMINS))
async def del_fsub(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("<b>Usage:</b> <code>/del_fsub -100xxxxxxxxxx</code>")
    chat = _parse_chat(message.command[1])
    extra = await vdb.get_setting("fsub_channels", []) or []
    if chat in extra:
        extra.remove(chat)
        await vdb.set_setting("fsub_channels", extra)
        _chat_cache.pop(chat, None)
        from core import botlog
        await botlog.event("FsubRemoved", f"<b>📢 Channel:</b> <code>{chat}</code>\n"
                                          f"<b>👮 By:</b> <code>{message.from_user.id}</code>", client=client)
        return await message.reply_text(f"✅ Removed <code>{chat}</code> from force-subscribe.")
    if chat in FSUB_CHANNELS:
        return await message.reply_text("ℹ️ That channel comes from the FSUB_CHANNELS env var – remove it there.")
    await message.reply_text("❌ Not in the list. See /fsub_list")


@Client.on_message(filters.command(["fsub_list", "fsub"]) & filters.user(ADMINS))
async def fsub_list(client: Client, message: Message):
    channels = await all_channels()
    if not channels:
        return await message.reply_text("🔓 Force-subscribe is <b>off</b>. Add a channel with /add_fsub.")
    lines = []
    for c in channels:
        title, link = await _chat_info(client, c)
        src = "env" if c in FSUB_CHANNELS else "db"
        lines.append(f"• <b>{title}</b> — <code>{c}</code> ({src})" + (f"\n  {link}" if link else " ⚠️ no access"))
    mode = "join-request" if FSUB_REQUEST_MODE else "join"
    await message.reply_text(f"<b>🔒 Force-subscribe ({mode} mode)</b>\n\n" + "\n".join(lines),
                             disable_web_page_preview=True)

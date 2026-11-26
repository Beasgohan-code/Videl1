"""
Shared Kurigram 2.2.27 helpers for AoiMusic Group Management plugins.
All responses use ParseMode.HTML + Musiciconis premium custom emojis (`pe()`, `pbtn()`).
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus, ChatType, ParseMode
from pyrogram.types import (
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    User,
)

import config
from AoiMusic.misc import SUDOERS
from AoiMusic.utils.manage_db import get_chat_settings, get_connection
from AoiMusic.utils.premium_emoji import pbtn, pe

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


def esc(text: object) -> str:
    return html.escape(str(text or ""))


def user_mention(user: User) -> str:
    name = esc(user.first_name or "User")
    return f'<a href="tg://user?id={user.id}">{name}</a>'


async def mgmt_reply(
    message: Message,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Optional[Message]:
    """Send a compact HTML card with Musiciconis premium emojis and disabled link preview."""
    try:
        return await message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            link_preview_options=NO_PREVIEW,
        )
    except Exception:
        return None


async def resolve_chat_id(message: Message) -> Optional[int]:
    """Return group chat_id, or connected group chat_id when invoked in private chat."""
    if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        return message.chat.id
    if message.from_user:
        return await get_connection(message.from_user.id)
    return None


async def is_user_admin(client: Client, chat_id: int, user_id: int) -> bool:
    if user_id in config.OWNER_ID or user_id in SUDOERS:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.OWNER, ChatMemberStatus.ADMINISTRATOR)
    except Exception:
        return False


async def is_user_owner(client: Client, chat_id: int, user_id: int) -> bool:
    if user_id in config.OWNER_ID or user_id in SUDOERS:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        return member.status == ChatMemberStatus.OWNER
    except Exception:
        return False


async def has_admin_perm(
    client: Client,
    chat_id: int,
    user_id: int,
    perm: Optional[str] = None,
) -> bool:
    if user_id in config.OWNER_ID or user_id in SUDOERS:
        return True
    try:
        member = await client.get_chat_member(chat_id, user_id)
        if member.status == ChatMemberStatus.OWNER:
            return True
        if member.status != ChatMemberStatus.ADMINISTRATOR:
            return False
        if not perm:
            return True
        return bool(getattr(member.privileges, perm, False))
    except Exception:
        return False


async def check_admin_and_bot(
    client: Client,
    message: Message,
    chat_id: int,
    perm: Optional[str] = None,
) -> bool:
    """Verify both caller and bot have the required admin permission."""
    # Anonymous admin in same group
    caller_ok = False
    if message.sender_chat and message.sender_chat.id == chat_id:
        caller_ok = True
    elif message.from_user:
        caller_ok = await has_admin_perm(client, chat_id, message.from_user.id, perm)

    if not caller_ok:
        await mgmt_reply(
            message,
            f"<blockquote>{pe('LOCK')} <b>ᴀᴄᴄᴇss ᴅᴇɴɪᴇᴅ</b>\n"
            f"ʏᴏᴜ ɴᴇᴇᴅ <code>{perm or 'admin'}</code> ᴘᴇʀᴍɪssɪᴏɴ ᴛᴏ ᴜsᴇ ᴛʜɪs ᴄᴏᴍᴍᴀɴᴅ.</blockquote>",
        )
        return False

    try:
        me = await client.get_me()
        bot_member = await client.get_chat_member(chat_id, me.id)
        if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
            await mgmt_reply(
                message,
                f"<blockquote>{pe('WARNING')} <b>ʙᴏᴛ ɪs ɴᴏᴛ ᴀᴅᴍɪɴ</b>\n"
                f"ᴘʀᴏᴍᴏᴛᴇ ᴍᴇ ᴀs ᴀᴅᴍɪɴ ᴡɪᴛʜ <code>{perm or 'admin'}</code> ʀɪɢʜᴛs ғɪʀsᴛ.</blockquote>",
            )
            return False
        if perm and not getattr(bot_member.privileges, perm, False):
            await mgmt_reply(
                message,
                f"<blockquote>{pe('WARNING')} <b>ᴍɪssɪɴɢ ʙᴏᴛ ᴘᴇʀᴍɪssɪᴏɴ</b>\n"
                f"ɪ ɴᴇᴇᴅ <code>{perm}</code> ᴘᴇʀᴍɪssɪᴏɴ ᴛᴏ ᴅᴏ ᴛʜᴀᴛ.</blockquote>",
            )
            return False
    except Exception:
        return False

    return True


async def extract_user_and_reason(
    client: Client,
    message: Message,
) -> Tuple[Optional[User], str]:
    """Extract target User and optional reason from reply, @username, or numeric user_id."""
    args = message.command[1:] if message.command and len(message.command) > 1 else []
    target: Optional[User] = None
    reason = ""

    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        reason = " ".join(args).strip()
        return target, reason

    if not args:
        return None, ""

    first = args[0]
    reason = " ".join(args[1:]).strip()
    try:
        if first.isdigit():
            target = await client.get_users(int(first))
        elif first.startswith("@"):
            target = await client.get_users(first)
        else:
            # Check text_mention entity
            for ent in message.entities or []:
                if getattr(ent, "user", None):
                    target = ent.user
                    break
    except Exception:
        target = None

    return target, reason


_TIME_RE = re.compile(r"^(\d+)([smhdw])$", re.IGNORECASE)


def parse_time(time_str: str) -> Tuple[Optional[datetime], int, str]:
    """Parse duration like '30m', '2h', '7d', '1w'. Returns (until_dt, seconds, human_label)."""
    if not time_str:
        return None, 0, ""
    m = _TIME_RE.match(time_str.strip())
    if not m:
        return None, 0, ""
    val = int(m.group(1))
    unit = m.group(2).lower()
    mult = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    names = {"s": "sec", "m": "min", "h": "hr", "d": "day", "w": "week"}
    secs = val * mult
    if secs < 35:
        secs = 35
    until = datetime.now(timezone.utc) + timedelta(seconds=secs)
    return until, secs, f"{val} {names[unit]}{'s' if val != 1 else ''}"


def format_duration(seconds: int) -> str:
    seconds = max(0, int(seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


async def is_cmd_disabled(chat_id: int, cmd_name: str) -> bool:
    settings = await get_chat_settings(chat_id)
    disabled = settings.get("disabled_cmds") or []
    return cmd_name.lower() in disabled


async def log_mgmt_action(client: Client, chat_id: int, html_text: str) -> None:
    """Send moderation log to the chat's configured /setlog channel if set."""
    try:
        settings = await get_chat_settings(chat_id)
        log_chat = settings.get("log_channel")
        if not log_chat:
            return
        await client.send_message(
            int(log_chat),
            html_text,
            parse_mode=ParseMode.HTML,
            link_preview_options=NO_PREVIEW,
        )
    except Exception:
        pass

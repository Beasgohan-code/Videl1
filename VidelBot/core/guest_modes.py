"""Narrow WzGram guest and guardian support for the primary bot only.

No guest commands, context echo, moderation decisions, new receiver or persistence.
"""
import asyncio
import logging
import re
import time
from collections import OrderedDict

import config
from pyrogram import Client, ContinuePropagation, StopPropagation, enums, filters
from pyrogram.types import (InlineKeyboardButton, InlineKeyboardMarkup,
                            InlineQueryResultArticle, InputTextMessageContent)

log = logging.getLogger(__name__)
_seen_guest = OrderedDict()
_guest_users = OrderedDict()
_seen_guard = OrderedDict()
_guest_starts = []
_guard_starts = []
_inflight_guest = 0
_inflight_guard = 0
_GUEST_REPLY = ("Hi, I'm Videl! I offer file tools, media utilities and bot management. "
                "For setup and private actions, open a private chat with me and verify the bot identity. "
                "This guest reply is a public introduction; it doesn't run commands.")


def _primary(client):
    prefix = str(config.BOT_TOKEN or '').split(':', 1)[0]
    return prefix.isdigit() and getattr(getattr(client, 'me', None), 'id', None) == int(prefix)


def _guest_markup(client):
    """Link only to this primary bot's validated Telegram username, if known."""
    username = getattr(getattr(client, 'me', None), 'username', None)
    if not isinstance(username, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', username, flags=re.ASCII):
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton('Open private chat', url=f'https://t.me/{username}')]])


def _query_id(value):
    # WzGram converts these IDs to an MTProto signed long; no query ID is logged.
    if not isinstance(value, str) or not value or len(value) > 20:
        return False
    digits = value[1:] if value.startswith("-") else value
    if not digits or not digits.isascii() or not digits.isdigit():
        return False
    number = int(value)
    return -(2**63) <= number < 2**63 and number != 0


def _remember(store, key, now, ttl, cap):
    while store and next(iter(store.values())) <= now - ttl:
        store.popitem(last=False)
    if key in store:
        return False
    if len(store) >= cap:
        return False  # do not evict a live replay guard to accept new work
    store[key] = now
    return True


@Client.on_guest_message()
async def guest_introduction(client, message):
    global _inflight_guest, _guest_starts
    if not config.GUEST_REPLIES_ENABLED or not _primary(client):
        return
    query_id = getattr(message, 'guest_query_id', None)
    caller = getattr(message, 'guest_bot_caller_user', None)
    uid = getattr(caller, 'id', None)
    if (not _query_id(query_id) or type(uid) is not int or uid <= 0
            or getattr(caller, 'is_bot', None) is not False):
        return
    now = time.monotonic()
    _guest_starts = [t for t in _guest_starts if t > now - 1]
    while _guest_users and next(iter(_guest_users.values())) <= now - 5:
        _guest_users.popitem(last=False)
    if uid in _guest_users or len(_guest_users) >= 1024 or len(_guest_starts) >= 20 or _inflight_guest >= 20:
        return
    if not _remember(_seen_guest, str(int(query_id)), now, 120, 4096):
        return
    _guest_users[uid] = now
    _guest_starts.append(now)
    _inflight_guest += 1
    try:
        result = InlineQueryResultArticle('About Videl', InputTextMessageContent(_GUEST_REPLY, parse_mode=enums.ParseMode.DISABLED),
                                          reply_markup=_guest_markup(client))
        await asyncio.wait_for(client.answer_guest_query(query_id, result), timeout=8)
    except Exception:
        # A lost response may mean Telegram sent it: never retry automatically.
        log.warning('Guest reply could not be confirmed.')
    finally:
        _inflight_guest -= 1


@Client.on_chat_join_request(group=-10)
async def guardian_manual_queue(client, request):
    global _inflight_guard, _guard_starts
    query_id = getattr(request, 'query_id', None)
    if query_id is None:
        raise ContinuePropagation  # ordinary join requests keep their old handlers
    # Never let a guardian query reach ordinary join automation or force-sub
    # accounting, even if it is malformed, disabled or the response is uncertain.
    if config.GUARD_QUEUE_ENABLED and _primary(client) and _query_id(query_id):
        now = time.monotonic()
        _guard_starts = [t for t in _guard_starts if t > now - 1]
        if (_inflight_guard < 20 and len(_guard_starts) < 20
                and _remember(_seen_guard, str(int(query_id)), now, 120, 4096)):
            _guard_starts.append(now)
            _inflight_guard += 1
            try:
                result = await asyncio.wait_for(
                    client.answer_chat_join_request_query(query_id, enums.ChatJoinRequestQueryResult.QUEUE), timeout=5)
                if result is not True:
                    log.warning('Guardian manual-review queue result could not be confirmed.')
            except Exception:
                log.warning('Guardian manual-review queue result could not be confirmed.')
            finally:
                _inflight_guard -= 1
    raise StopPropagation


@Client.on_message(filters.command("guardcheck") & filters.private)
async def guardian_check(client, message):
    """Read-only assignment check for a directly identified chat administrator."""
    user = getattr(message, 'from_user', None)
    chat = getattr(message, 'chat', None)
    uid = getattr(user, 'id', None)
    if (not _primary(client) or type(uid) is not int or uid <= 0
            or getattr(user, 'is_bot', None) is not False
            or getattr(message, 'sender_chat', None) is not None
            or getattr(chat, 'type', None) != enums.ChatType.PRIVATE
            or getattr(chat, 'id', None) != uid):
        return
    command = getattr(message, 'command', None)
    if not isinstance(command, list) or len(command) != 2:
        return await message.reply_text('Usage: <code>/guardcheck &lt;group/channel ID or @username&gt;</code>')
    arg = command[1]
    if not isinstance(arg, str) or len(arg) > 32:
        return await message.reply_text('Use a group/channel numeric ID or @username.')
    if re.fullmatch(r'-[1-9][0-9]{0,19}', arg, flags=re.ASCII):
        target = int(arg)
        if target < -(2**63):
            return await message.reply_text('Use a group/channel numeric ID or @username.')
    elif re.fullmatch(r'@[A-Za-z][A-Za-z0-9_]{4,31}', arg, flags=re.ASCII):
        target = arg
    else:
        return await message.reply_text('Use a group/channel numeric ID or @username.')
    try:
        found = await asyncio.wait_for(client.get_chat(target), timeout=5)
        found_id = getattr(found, 'id', None)
        if (getattr(found, 'type', None) not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL)
                or type(found_id) is not int or found_id >= 0):
            raise ValueError('Unsupported chat')
        member = await asyncio.wait_for(client.get_chat_member(found_id, uid), timeout=5)
        if getattr(member, 'status', None) not in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR):
            raise ValueError('Administrator not verified')
    except Exception:
        return await message.reply_text('Chat unavailable or administrator access could not be verified. Check the ID and bot access.')
    guard = getattr(found, 'guard_bot', None)
    guard_id = getattr(guard, 'id', None)
    if type(guard_id) is int and guard_id > 0:
        assignment = 'This bot is reported as the guard bot.' if guard_id == client.me.id else 'A different bot is reported as the guard bot.'
    else:
        assignment = 'Guard assignment is unavailable; no assignment can be confirmed.'
    mode = 'enabled' if config.GUARD_QUEUE_ENABLED else 'disabled'
    await message.reply_text(
        f'<b>🛡 Guard check</b>\n\n{assignment}\n'
        f'This process: manual-review queue answers are <b>{mode}</b>.\n'
        'This snapshot does not prove update delivery, permissions, successful queueing or protection. '
        'Only this primary bot is checked; confirm with a disposable group.')

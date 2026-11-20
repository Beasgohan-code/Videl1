"""Read-only group clone setup inspection via the existing local client.

This controller command does not install or copy any group moderation plugin.
"""
import asyncio
import re
import time
from collections import OrderedDict

import config
from pyrogram import Client, enums, filters

from filestore.database.main_db import MainDB
from filestore.utils.registry_ids import valid_registry_id

main_db = MainDB()
_recent = OrderedDict()
_active = set()
_starts = []


def _primary(client):
    prefix = str(config.BOT_TOKEN or '').split(':', 1)[0]
    if not re.fullmatch(r'[1-9][0-9]{0,18}', prefix, flags=re.ASCII):
        return False
    bot_id = getattr(getattr(client, 'me', None), 'id', None)
    return type(bot_id) is int and 0 < bot_id < 2**63 and bot_id == int(prefix)


def _arguments(command):
    if not isinstance(command, list) or len(command) != 3:
        return None
    bot, chat = command[1:]
    if (not isinstance(bot, str) or not isinstance(chat, str)
            or not re.fullmatch(r'[1-9][0-9]{0,18}', bot, flags=re.ASCII)
            or not re.fullmatch(r'-[1-9][0-9]{0,18}', chat, flags=re.ASCII)):
        return None
    bot, chat = int(bot), int(chat)
    return (bot, chat) if bot < 2**63 and -(2**63) < chat < 0 else None


def _owned_group(bot, bot_id, uid):
    return (isinstance(bot, dict) and valid_registry_id(bot.get('_id')) and bot['_id'] == bot_id
            and valid_registry_id(bot.get('owner_id')) and bot['owner_id'] == uid
            and bot.get('is_deleted', False) is False and bot.get('clone_profile') == 'group')


def _is_admin(member):
    return getattr(member, 'status', None) in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR)


def _right(member, key):
    status = getattr(member, 'status', None)
    if status == enums.ChatMemberStatus.OWNER:
        return 'Yes'
    if status == enums.ChatMemberStatus.ADMINISTRATOR:
        flag = getattr(getattr(member, 'privileges', None), key, None)
        return 'Yes' if flag is True else 'No' if flag is False else 'Unknown'
    if status in (enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.RESTRICTED,
                  enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED):
        return 'No'
    return 'Unknown'


async def _inspect(bot_id, chat_id, uid):
    """Authorize and read observations, then recheck owner and admin before reply."""
    from filestore.worker_bot.engine import worker_engine
    before = await asyncio.wait_for(main_db.get_bot(bot_id), timeout=4)
    if not _owned_group(before, bot_id, uid):
        raise ValueError('Not authorized')
    worker = worker_engine.get_worker(bot_id)
    if worker is None:
        raise ValueError('No local client')
    identity = await asyncio.wait_for(worker.get_me(), timeout=4)
    if type(getattr(identity, 'id', None)) is not int or identity.id != bot_id:
        raise ValueError('Wrong client')
    chat = await asyncio.wait_for(worker.get_chat(chat_id), timeout=4)
    if (type(getattr(chat, 'id', None)) is not int or chat.id != chat_id
            or getattr(chat, 'type', None) not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)):
        raise ValueError('Wrong chat')
    actor = await asyncio.wait_for(worker.get_chat_member(chat_id, uid), timeout=4)
    if not _is_admin(actor):
        raise ValueError('Group administrator not verified')
    bot_member = await asyncio.wait_for(worker.get_chat_member(chat_id, bot_id), timeout=4)
    # A fresh observation is still not a lock. Avoid disclosing a stale result
    # after an observed owner, credential, mode, client, or administrator change.
    after = await asyncio.wait_for(main_db.get_bot(bot_id), timeout=4)
    if (not _owned_group(after, bot_id, uid)
            or before.get('bot_token_encrypted') != after.get('bot_token_encrypted')
            or worker_engine.get_worker(bot_id) is not worker):
        raise ValueError('Clone changed')
    actor = await asyncio.wait_for(worker.get_chat_member(chat_id, uid), timeout=4)
    if not _is_admin(actor) or worker_engine.get_worker(bot_id) is not worker:
        raise ValueError('Administrator or client changed')
    rights = [('Restrict members', 'can_restrict_members'), ('Delete messages', 'can_delete_messages'),
              ('Pin messages', 'can_pin_messages'), ('Change group info', 'can_change_info')]
    rows = '\n'.join(f'{label}: You {_right(actor, key)} · Bot {_right(bot_member, key)}' for label, key in rights)
    group_type = 'Supergroup' if chat.type == enums.ChatType.SUPERGROUP else 'Basic group'
    limitation = ('Ban, mute and kick require a supergroup.\n' if chat.type == enums.ChatType.GROUP else '')
    return (f'<b>🛡 Group clone setup check</b>\n\n'
            f'Clone: <code>{bot_id}</code> · Group: <code>{chat_id}</code>\n'
            f'Type: <b>{group_type}</b>\n\n{rows}\n\n{limitation}'
            'Read-only snapshot from this process. Rights, ownership and client state can change; '
            'privacy mode, update delivery, target status and actual moderation were not checked. '
            'Telegram decides each action. No settings were changed.')


@Client.on_message(filters.command('clonegroupcheck') & filters.private)
async def clone_group_check(client, message):
    """Inspect a group only for the clone owner who is also its group admin."""
    global _starts
    user = getattr(message, 'from_user', None)
    uid = getattr(user, 'id', None)
    chat = getattr(message, 'chat', None)
    if (not _primary(client) or type(uid) is not int or not 0 < uid < 2**63
            or getattr(user, 'is_bot', None) is not False
            or getattr(message, 'sender_chat', None) is not None
            or getattr(chat, 'type', None) != enums.ChatType.PRIVATE
            or getattr(chat, 'id', None) != uid):
        return
    args = _arguments(getattr(message, 'command', None))
    if args is None:
        return await message.reply_text('Usage: <code>/clonegroupcheck BOT_ID GROUP_ID</code> (use a negative numeric group ID).')
    now = time.monotonic()
    _starts = [stamp for stamp in _starts if stamp > now - 1]
    while _recent and next(iter(_recent.values())) <= now - 5:
        _recent.popitem(last=False)
    if uid in _active or len(_active) >= 10 or len(_starts) >= 10 or uid in _recent or len(_recent) >= 1024:
        return await message.reply_text('Checks are busy or were requested too recently. Please wait and try again.')
    _recent[uid] = now
    _active.add(uid)
    _starts.append(now)
    try:
        try:
            result = await asyncio.wait_for(_inspect(*args, uid), timeout=18)
        except Exception:
            return await message.reply_text('Check unavailable or access could not be verified. Confirm clone ownership, local client and group admin access.')
        return await message.reply_text(result)
    finally:
        _active.discard(uid)

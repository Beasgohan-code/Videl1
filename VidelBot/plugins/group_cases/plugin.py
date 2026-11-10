"""Manual private casebook; it never enforces a Telegram moderation action."""
from __future__ import annotations

import re
import secrets
import time
from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError
from pyrogram import Client, enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from core import rich
from core.db import vdb
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from core.rich import Doc
from core.ui import styled_button
from plugins.group_mod import plugin as gmod

PLG_FILTER = plugin_filter('group_cases')
_COLLECTION = 'group_moderation_cases'
_CASE_ID = re.compile(r'^[0-9a-f]{12}$')
_MAX_CASES = 100
_MAX_NOTES = 20
_MAX_SUMMARY = 200
_MAX_NOTE = 400
_LIST_SIZE = 8
_CREATE_RETRIES = 5


class Plugin(PluginBase):
    pass


def _private_actor(client, message, actor):
    bot = getattr(getattr(client, 'me', None), 'id', None)
    user_id = getattr(actor, 'id', None)
    chat = getattr(message, 'chat', None)
    if (type(bot) is not int or bot <= 0 or type(user_id) is not int or user_id <= 0
            or getattr(chat, 'type', None) != enums.ChatType.PRIVATE or getattr(chat, 'id', None) != user_id):
        return None
    return bot, user_id


def _group(value):
    if not re.fullmatch(r'-[1-9][0-9]{0,18}', value or ''):
        return None
    number = int(value)
    return number if -(2**63) <= number < 0 else None


def _positive(value):
    if not value or not value.isascii() or not value.isdigit() or len(value) > 19:
        return None
    number = int(value)
    return number if 0 < number <= 2**63 - 1 else None


def _revision(value):
    return _positive(value)


def _doc_key(scope):
    return f'{scope[0]}:{scope[1]}'


async def _authorize(client, group, actor_id):
    # Both checks are live and fail closed. In particular, a former bot member
    # must not keep serving this group's stored notes after removal.
    ok, _ = await gmod.verify_mod_permissions(client, group, actor_id, privilege='can_restrict_members', check_bot=False)
    if not ok:
        return False
    bot_id = getattr(getattr(client, 'me', None), 'id', None)
    if type(bot_id) is not int or bot_id <= 0:
        return False
    try:
        member = await client.get_chat_member(group, bot_id)
    except Exception:
        return False
    status = getattr(member, 'status', None)
    return (status in (enums.ChatMemberStatus.OWNER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.MEMBER)
            or (status == enums.ChatMemberStatus.RESTRICTED and getattr(member, 'is_member', None) is True))


async def _load(scope):
    return await vdb.db[_COLLECTION].find_one({'_id': _doc_key(scope)})


async def _create(scope, actor, target, summary):
    col = vdb.db[_COLLECTION]
    key = _doc_key(scope)
    case_id = secrets.token_hex(6)
    for _ in range(_CREATE_RETRIES):
        doc = await col.find_one({'_id': key})
        cases = dict((doc or {}).get('cases') or {})
        if len(cases) >= _MAX_CASES:
            return 'full', None
        if case_id in cases:
            case_id = secrets.token_hex(6)
            continue
        now = int(time.time())
        cases[case_id] = {'target_id': target, 'summary': summary, 'status': 'open',
                          'created_by': actor, 'created_at': now, 'updated_by': actor,
                          'updated_at': now, 'notes': []}
        if doc is None:
            try:
                await col.insert_one({'_id': key, 'bot_id': scope[0], 'chat_id': scope[1], 'version': 1, 'cases': cases})
                return 'created', case_id
            except DuplicateKeyError:
                continue
        result = await col.update_one({'_id': key, 'version': doc['version']},
                                      {'$set': {'cases': cases}, '$inc': {'version': 1}})
        if result.matched_count:
            return 'created', case_id
    return 'conflict', None


async def _change(scope, case_id, version, actor, action, note=None):
    """Apply one reviewed-revision change without discarding concurrent changes."""
    col = vdb.db[_COLLECTION]
    doc = await col.find_one({'_id': _doc_key(scope)})
    cases = dict((doc or {}).get('cases') or {})
    if case_id not in cases:
        return 'missing'
    if doc.get('version') != version:
        return 'stale'
    case = dict(cases[case_id])
    if action == 'purge':
        del cases[case_id]
    else:
        if action == 'note':
            notes = list(case.get('notes') or [])
            if len(notes) >= _MAX_NOTES:
                return 'full'
            notes.append({'author_id': actor, 'at': int(time.time()), 'text': note})
            case['notes'] = notes
        else:
            new_status = 'closed' if action == 'close' else 'open'
            if case['status'] == new_status:
                return 'unchanged'
            case['status'] = new_status
        case['updated_by'] = actor
        case['updated_at'] = int(time.time())
        cases[case_id] = case
    result = await col.update_one({'_id': doc['_id'], 'version': version},
                                  {'$set': {'cases': cases}, '$inc': {'version': 1}})
    return ('purged' if action == 'purge' else 'updated') if result.matched_count else 'stale'


def _list_doc(doc, group, status, page, target=None):
    cases = (doc or {}).get('cases') or {}
    entries = sorted(((k, c) for k, c in cases.items() if (status == 'all' or c.get('status') == status) and (target is None or c.get('target_id') == target)),
                     key=lambda pair: (-pair[1].get('created_at', 0), pair[0]))
    start = (page - 1) * _LIST_SIZE
    current = entries[start:start + _LIST_SIZE]
    screen = Doc('🗂', 'Moderation Cases', f'Group {group} · ' + (f'User {target}' if target is not None else status) + f' · Page {page}')
    if current:
        screen.table([(cid, str(c.get('target_id')), c.get('status', 'unknown')) for cid, c in current],
                     header=('Case', 'User ID', 'Status'))
    else:
        screen.status('empty', 'No cases on this page', 'Change the filter or create a case in this private chat.')
    screen.status('info', 'Private workflow', 'Manual notes only; no moderation action is performed. Group-admin access is checked live.')
    buttons = [[styled_button(cid, callback_data=f'plg_case:show:{group}:{cid}:1') for cid, _ in current[i:i + 2]]
               for i in range(0, len(current), 2)]
    nav = []
    prefix = f'plg_case:find:{group}:{target}' if target is not None else f'plg_case:list:{group}:{status}'
    if page > 1:
        nav.append(styled_button('← Previous', callback_data=f'{prefix}:{page - 1}'))
    if start + _LIST_SIZE < len(entries):
        nav.append(styled_button('Next →', callback_data=f'{prefix}:{page + 1}'))
    if nav:
        buttons.append(nav)
    return screen, InlineKeyboardMarkup(buttons) if buttons else None


def _utc_timestamp(value):
    try:
        return datetime.fromtimestamp(value, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    except (ValueError, TypeError, OSError, OverflowError):
        return 'Unavailable'


def _case_doc(doc, group, case_id, page):
    cases = (doc or {}).get('cases') or {}
    case = cases.get(case_id)
    back = [styled_button('← Open cases', callback_data=f'plg_case:list:{group}:open:1')]
    if not case:
        return Doc('🗂', 'Moderation Case').status('empty', 'Case not found', 'It may have been purged.'), InlineKeyboardMarkup([back])
    screen = Doc('🗂', f'Case · {case_id}', f"Group {group} · {case.get('status', 'unknown')}")
    screen.kv_table([('User ID', str(case['target_id'])), ('Created by', str(case['created_by'])),
                     ('Created', _utc_timestamp(case.get('created_at'))), ('Last updated by', str(case['updated_by'])),
                     ('Updated', _utc_timestamp(case.get('updated_at'))), ('Group revision', str(doc['version']))])
    screen.h('📝', 'Summary').text(rich.esc(case['summary']))
    notes = case.get('notes') or []
    if notes and page <= len(notes):
        item = notes[page - 1]
        screen.h('📌', f'Note {page} of {len(notes)} · admin {item["author_id"]}').text(rich.esc(item['text']))
        screen.status('info', 'Noted at', _utc_timestamp(item.get('at')))
    elif notes:
        screen.status('empty', 'No note on this page', 'Use the previous button or reopen the case.')
    else:
        screen.status('empty', 'No notes yet', 'Use /modcase <group-id> note <case-id> <revision> <text>.')
    screen.status('info', 'Record', 'User ID is unverified. Notes are not proof of a Telegram action or a complete audit log.')
    nav = []
    if notes and page > 1:
        nav.append(styled_button('← Note', callback_data=f'plg_case:show:{group}:{case_id}:{page - 1}'))
    if page < len(notes):
        nav.append(styled_button('Next note →', callback_data=f'plg_case:show:{group}:{case_id}:{page + 1}'))
    return screen, InlineKeyboardMarkup(([nav] if nav else []) + [back])


async def _send(message, screen, markup=None):
    return await message.reply_text(screen.classic(), reply_markup=markup, parse_mode=enums.ParseMode.HTML)


def _usage():
    return (Doc('🗂', 'Moderation Cases').status('info', 'Private group-admin casebook',
        'In a private chat use /modcase <group-id> list, find <user-id>, open <user-id> <summary>, show <case-id>, note <case-id> <revision> <text>, close/reopen <case-id> <revision> confirm, or purge <case-id> to review deletion. No action is enforced.')
        .status('warning', 'Sensitive records', 'Group admins with restrict-members permission can access records. Notes are stored until an authorized admin explicitly purges them; avoid secrets.'))


@Client.on_message(filters.private & filters.command('modcase') & PLG_FILTER)
async def modcase_cmd(client: Client, message: Message):
    identity = _private_actor(client, message, getattr(message, 'from_user', None))
    if not identity:
        return  # Never send case data to a group or another user's private chat.
    raw = getattr(message, 'text', None) or ''
    parts = raw.split(maxsplit=3)
    group = _group(parts[1]) if len(parts) >= 2 else None
    if group is None or len(parts) < 3:
        return await _send(message, _usage())
    bot, actor = identity
    try:
        if not await _authorize(client, group, actor):
            return await _send(message, Doc('🔒', 'Moderation Cases').status('error', 'Access unavailable', 'Could not verify your group administrator permission and the bot’s membership.'))
        scope = (bot, group)
        action, rest = parts[2].lower(), parts[3] if len(parts) > 3 else ''
        if action == 'open':
            args = rest.split(maxsplit=1)
            target = _positive(args[0]) if args else None
            summary = args[1].strip() if len(args) == 2 else ''
            if target is None or not 1 <= len(summary) <= _MAX_SUMMARY:
                return await _send(message, Doc('🗂', 'Moderation Cases').status('info', 'Usage', '/modcase <group-id> open <user-id> <summary up to 200 characters>.'))
            result, case_id = await _create(scope, actor, target, summary)
            detail = {'created': f'Case {case_id} saved until explicitly purged; notes are not encrypted by this plugin. Use /modcase {group} show {case_id} to review.',
                      'full': 'This group reached the 100-case limit; review records before explicit purge.',
                      'conflict': 'Another edit was in progress. Try again.'}[result]
            return await _send(message, Doc('🗂', 'Moderation Cases').status('success' if result == 'created' else 'warning', result.title(), detail))
        if action == 'list':
            args = rest.split()
            status = args[0].lower() if args else 'open'
            raw_page = args[1] if len(args) == 2 else '1'
            if len(args) > 2 or status not in ('open', 'closed', 'all') or not raw_page.isascii() or not raw_page.isdigit() or len(raw_page) > 2 or not 1 <= int(raw_page) <= 13:
                return await _send(message, _usage())
            screen, markup = _list_doc(await _load(scope), group, status, int(raw_page))
            return await _send(message, screen, markup)
        if action == 'find':
            args = rest.split()
            target = _positive(args[0]) if args else None
            page = _positive(args[1]) if len(args) == 2 else (1 if len(args) == 1 else None)
            if target is None or page is None or page > 13:
                return await _send(message, _usage())
            screen, markup = _list_doc(await _load(scope), group, 'all', page, target=target)
            return await _send(message, screen, markup)
        args = rest.split(maxsplit=2)
        case_id = args[0] if args else ''
        if not _CASE_ID.fullmatch(case_id):
            return await _send(message, _usage())
        if action == 'show':
            page = _positive(args[1]) if len(args) == 2 else (1 if len(args) == 1 else None)
            if page is None or page > _MAX_NOTES:
                return await _send(message, _usage())
            screen, markup = _case_doc(await _load(scope), group, case_id, page)
            return await _send(message, screen, markup)
        if action == 'purge' and len(args) == 1:
            doc = await _load(scope)
            if not doc or case_id not in (doc.get('cases') or {}):
                return await _send(message, Doc('🗂', 'Moderation Cases').status('empty', 'Case not found', 'No record was removed.'))
            return await _send(message, Doc('⚠️', 'Purge case').status('warning', 'Confirm irreversible deletion', f"To permanently remove case {case_id} and its notes, use /modcase {group} purge {case_id} {doc['version']} confirm. Sent messages and backups may remain."))
        if action not in ('note', 'close', 'reopen', 'purge') or len(args) != 3 or _revision(args[1]) is None:
            return await _send(message, _usage())
        version = _revision(args[1])
        if action == 'note':
            note = args[2].strip()
            if not 1 <= len(note) <= _MAX_NOTE:
                return await _send(message, Doc('🗂', 'Moderation Cases').status('warning', 'Invalid note', 'Use 1–400 characters.'))
        else:
            note = None
            if args[2].lower() != 'confirm':
                return await _send(message, _usage())
        result = await _change(scope, case_id, version, actor, action, note)
        detail = {'updated': 'Saved. Reopen the case to see its new revision.',
                  'purged': 'Removed from the casebook; messages and backups may remain.',
                  'missing': 'Case not found.', 'stale': 'The group casebook changed. Reopen the case and review its revision.',
                  'unchanged': 'The case already has that status.', 'full': 'This case has reached its 20-note limit.'}[result]
        return await _send(message, Doc('🗂', 'Moderation Cases').status('success' if result in ('updated', 'purged') else 'warning', result.title(), detail))
    except Exception:
        return await _send(message, Doc('⚠️', 'Moderation Cases').status('warning', 'Unavailable', 'Could not read or save cases. Try again later.'))


@Client.on_callback_query(filters.regex(r'^plg_case:[a-zA-Z0-9_:-]+$'))
async def modcase_callback(client: Client, query: CallbackQuery):
    if not plugin_enabled('group_cases'):
        return await query.answer('Casebook disabled.', show_alert=True)
    message = query.message
    identity = _private_actor(client, message, query.from_user) if message else None
    parts = (query.data or '').split(':')
    group = _group(parts[2]) if len(parts) == 5 and parts[0] == 'plg_case' else None
    if not identity or group is None:
        return await query.answer('Open the casebook in your private chat.', show_alert=True)
    bot, actor = identity
    try:
        if not await _authorize(client, group, actor):
            return await query.answer('Could not verify group-admin access and bot membership.', show_alert=True)
        action, item, raw_page = parts[1], parts[3], parts[4]
        page = _positive(raw_page)
        if action == 'list' and item in ('open', 'closed', 'all') and page is not None and page <= 13:
            screen, markup = _list_doc(await _load((bot, group)), group, item, page)
        elif action == 'find' and _positive(item) is not None and page is not None and page <= 13:
            screen, markup = _list_doc(await _load((bot, group)), group, 'all', page, target=_positive(item))
        elif action == 'show' and _CASE_ID.fullmatch(item) and page is not None and page <= _MAX_NOTES:
            screen, markup = _case_doc(await _load((bot, group)), group, item, page)
        else:
            return await query.answer('Invalid casebook action.', show_alert=True)
        await message.edit_text(screen.classic(), reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        return await query.answer()
    except Exception:
        return await query.answer('Casebook unavailable. Try again later.', show_alert=True)
    return await query.answer()

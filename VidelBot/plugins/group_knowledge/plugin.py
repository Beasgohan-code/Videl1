"""A small, explicitly managed, group-visible knowledge base, scoped by bot and group."""
from __future__ import annotations

import re

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

PLG_FILTER = plugin_filter('group_knowledge')
_COLLECTION = 'group_knowledge'
_KEY = re.compile(r'^[a-z0-9][a-z0-9_-]{0,31}$')
_MAX_ENTRIES = 50
_MAX_BODY = 700
_PAGE_SIZE = 8
_MAX_RETRIES = 5
_RESERVED = frozenset({'list', 'search', 'save', 'replace', 'rename', 'delete'})


class Plugin(PluginBase):
    pass


def _scope(client, message):
    bot = getattr(getattr(client, 'me', None), 'id', None)
    chat = getattr(message, 'chat', None)
    chat_id = getattr(chat, 'id', None)
    if (type(bot) is not int or bot <= 0 or type(chat_id) is not int or chat_id >= 0
            or getattr(chat, 'type', None) not in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP)):
        return None
    return bot, chat_id


def _key(bot, chat):
    return f'{bot}:{chat}'


def _valid_key(value):
    if not value.isascii():
        return None
    value = value.lower()
    return value if _KEY.fullmatch(value) else None


async def _load(scope):
    return await vdb.db[_COLLECTION].find_one({'_id': _key(*scope)})


async def _create(scope, name, body):
    """Create only; CAS keeps the entry limit and avoids overwriting other admins."""
    col = vdb.db[_COLLECTION]
    doc_id = _key(*scope)
    for _ in range(_MAX_RETRIES):
        doc = await col.find_one({'_id': doc_id})
        entries = dict((doc or {}).get('entries') or {})
        if name in entries:
            return 'exists'
        if len(entries) >= _MAX_ENTRIES:
            return 'full'
        entries[name] = body
        if doc is None:
            try:
                await col.insert_one({'_id': doc_id, 'bot_id': scope[0], 'chat_id': scope[1], 'version': 1, 'entries': entries})
                return 'saved'
            except DuplicateKeyError:
                continue
        result = await col.update_one({'_id': doc_id, 'version': doc['version']},
                                      {'$set': {'entries': entries}, '$inc': {'version': 1}})
        if result.matched_count:
            return 'saved'
    return 'conflict'


async def _change(scope, name, version, body=None):
    """Replace or delete only the exact revision the admin reviewed."""
    col = vdb.db[_COLLECTION]
    doc = await col.find_one({'_id': _key(*scope)})
    if not doc or name not in (doc.get('entries') or {}):
        return 'missing'
    if doc.get('version') != version:
        return 'stale'
    entries = dict(doc['entries'])
    if body is None:
        del entries[name]
    else:
        entries[name] = body
    result = await col.update_one({'_id': doc['_id'], 'version': version},
                                  {'$set': {'entries': entries}, '$inc': {'version': 1}})
    return ('deleted' if body is None else 'updated') if result.matched_count else 'stale'


async def _rename(scope, old, new, version):
    """Rename without replacing another key, against a reviewed group revision."""
    col = vdb.db[_COLLECTION]
    doc = await col.find_one({'_id': _key(*scope)})
    entries = dict((doc or {}).get('entries') or {})
    if not doc or old not in entries:
        return 'missing'
    if doc.get('version') != version:
        return 'stale'
    if new in entries:
        return 'exists'
    entries[new] = entries.pop(old)
    result = await col.update_one({'_id': doc['_id'], 'version': version},
                                  {'$set': {'entries': entries}, '$inc': {'version': 1}})
    return 'renamed' if result.matched_count else 'stale'


def _index_doc(doc, page, search=None):
    entries = (doc or {}).get('entries') or {}
    names = sorted(k for k in entries if not search or search in k)
    start = (page - 1) * _PAGE_SIZE
    shown = names[start:start + _PAGE_SIZE]
    subtitle = 'This group · searchable by key' if not search else f'Keys matching “{search}”'
    screen = Doc('📚', 'Group FAQ', subtitle)
    if shown:
        screen.items([f'#{name}' for name in shown])
    else:
        screen.status('empty', 'No matching entries', 'Try /faq list or ask an admin to add one.')
    screen.status('info', 'Visibility', 'Entries are visible to everyone in this group. Search checks keys, not contents.')
    buttons = [[styled_button(name, callback_data=f'plg_faq:open:{name}') for name in shown[i:i + 2]]
               for i in range(0, len(shown), 2)]
    nav = []
    prefix = f'plg_faq:search:{search}' if search else 'plg_faq:list'
    if page > 1:
        nav.append(styled_button('← Previous', callback_data=f'{prefix}:{page - 1}'))
    if start + _PAGE_SIZE < len(names):
        nav.append(styled_button('Next →', callback_data=f'{prefix}:{page + 1}'))
    if nav:
        buttons.append(nav)
    return screen, InlineKeyboardMarkup(buttons) if buttons else None


def _entry_doc(doc, name):
    entries = (doc or {}).get('entries') or {}
    if name not in entries:
        return (Doc('📚', 'Group FAQ').status('empty', 'Entry not found', 'Use /faq list to browse available keys.'),
                InlineKeyboardMarkup([[styled_button('← All entries', callback_data='plg_faq:list:1')]]))
    screen = Doc('📚', f'FAQ · {name}', 'Visible to everyone in this group')
    screen.text(rich.esc(entries[name]))
    screen.status('info', 'Revision', f"Group revision {doc['version']}. Admins can use /faq replace, /faq rename or /faq delete.")
    return screen, InlineKeyboardMarkup([[styled_button('← All entries', callback_data='plg_faq:list:1')]])


async def _send(message, screen, markup=None):
    # Use an ordinary editable group message; callbacks must not depend on rich/ephemeral editing.
    return await message.reply_text(screen.classic(), reply_markup=markup, parse_mode=enums.ParseMode.HTML)


def _body(message, parts, start):
    if len(parts) > start:
        value = parts[start]
    else:
        reply = getattr(message, 'reply_to_message', None)
        value = getattr(reply, 'text', None) or getattr(reply, 'caption', None) or ''
    value = value.strip()
    return value if 1 <= len(value) <= _MAX_BODY else None


async def _is_admin(client, message):
    uid = getattr(getattr(message, 'from_user', None), 'id', None)
    return await gmod.verify_mod_permissions(client, message.chat.id, uid, privilege='can_change_info',
                                             is_anon_admin=gmod._is_anon_group_admin(message), check_bot=False)


@Client.on_message(filters.group & filters.command('faq') & PLG_FILTER)
async def faq_cmd(client: Client, message: Message):
    scope = _scope(client, message)
    if not scope:
        return await _send(message, Doc('⚠️', 'Group FAQ').status('warning', 'Unavailable', 'Use this command in a group.'))
    raw = getattr(message, 'text', None) or getattr(message, 'caption', None) or ''
    parts = raw.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else 'list'
    rest = parts[2] if len(parts) > 2 else ''
    try:
        if action in ('save', 'replace', 'rename', 'delete'):
            ok, err = await _is_admin(client, message)
            if not ok:
                return await _send(message, Doc('🔒', 'Group FAQ').status('error', 'Permission check failed', err))
            args = rest.split(maxsplit=1 if action == 'save' else 2)
            name = _valid_key(args[0]) if args else None
            if not name or name in _RESERVED:
                return await _send(message, Doc('📚', 'Group FAQ').status('info', 'Invalid key', 'Use 1–32 letters, numbers, underscores or hyphens; reserved command words are not keys.'))
            if action == 'rename':
                new = _valid_key(args[1]) if len(args) == 3 else None
                revision = args[2] if len(args) == 3 else ''
                if not new or new in _RESERVED or not revision.isascii() or not revision.isdigit() or len(revision) > 18:
                    return await _send(message, Doc('📚', 'Group FAQ').status('info', 'Usage', '/faq rename <old-key> <new-key> <revision>. Open the old entry to review its revision.'))
                result = await _rename(scope, name, new, int(revision))
                detail = {'renamed': 'Renamed for everyone in this group. Previously sent copies remain.',
                          'missing': 'Entry not found.', 'exists': 'The destination key already exists. Nothing was overwritten.',
                          'stale': 'The group FAQ changed. Open the entry and review it again.'}[result]
                return await _send(message, Doc('📚', 'Group FAQ').status('success' if result == 'renamed' else 'warning', result.title(), detail))
            if action == 'save':
                body = _body(message, args, 1)
                if not body:
                    return await _send(message, Doc('📚', 'Group FAQ').status('info', 'Usage', '/faq save <key> <text>, or reply to text with /faq save <key>. Limit: 700 characters.'))
                result = await _create(scope, name, body)
                detail = {'saved': 'Saved for everyone in this group.', 'exists': 'This key already exists. Open it with /faq <key> and use /faq replace <key> <revision> <text>.',
                          'full': 'This group has reached the 50-entry limit.', 'conflict': 'Another edit was in progress. Try again.'}[result]
                return await _send(message, Doc('📚', 'Group FAQ').status('success' if result == 'saved' else 'warning', result.title(), detail))
            if action == 'delete' and len(args) == 1:
                doc = await _load(scope)
                if not doc or name not in (doc.get('entries') or {}):
                    return await _send(message, Doc('📚', 'Group FAQ').status('empty', 'Entry not found', 'Use /faq list to browse available keys.'))
                return await _send(message, Doc('⚠️', 'Delete FAQ').status('warning', 'Confirm deletion', f"This removes #{name} from this bot and group. To confirm, use /faq delete {name} confirm {doc['version']}. Sent messages and copies remain."))
            if action == 'replace':
                # The third part contains replacement text; reply is accepted if omitted.
                if len(args) < 2 or not args[1].isascii() or not args[1].isdigit() or len(args[1]) > 18:
                    return await _send(message, Doc('📚', 'Group FAQ').status('info', 'Usage', '/faq replace <key> <revision> <text>, or reply to text. Open /faq <key> to see the revision.'))
                body = _body(message, args, 2)
                if not body:
                    return await _send(message, Doc('📚', 'Group FAQ').status('warning', 'Invalid text', 'Provide 1–700 characters.'))
                version = int(args[1])
            else:
                if len(args) != 3 or args[1].lower() != 'confirm' or not args[2].isascii() or not args[2].isdigit() or len(args[2]) > 18:
                    return await _send(message, Doc('📚', 'Group FAQ').status('info', 'Usage', '/faq delete <key> to review, then confirm using the displayed revision.'))
                version, body = int(args[2]), None
            result = await _change(scope, name, version, body)
            detail = {'updated': 'Updated for everyone in this group.', 'deleted': 'Removed from this bot and group. Previously sent copies remain.',
                      'missing': 'Entry not found.', 'stale': 'The group FAQ changed. Open the entry or review deletion again before retrying.'}[result]
            return await _send(message, Doc('📚', 'Group FAQ').status('success' if result in ('updated', 'deleted') else 'warning', result.title(), detail))
        doc = await _load(scope)
        if action == 'list':
            page = int(rest) if rest.isascii() and rest.isdigit() and len(rest) <= 2 else (1 if not rest else 0)
            if not 1 <= page <= 7:
                return await _send(message, Doc('📚', 'Group FAQ').status('info', 'Usage', '/faq list [page 1–7].'))
            screen, markup = _index_doc(doc, page)
        elif action == 'search':
            search_args = rest.lower().split()
            term = search_args[0] if search_args else ''
            page_token = search_args[1] if len(search_args) == 2 else '1'
            if (len(search_args) not in (1, 2) or not rest.isascii() or not re.fullmatch(r'[a-z0-9_-]{1,32}', term)
                    or not page_token.isascii() or not page_token.isdigit() or len(page_token) > 2
                    or not 1 <= int(page_token) <= 7):
                return await _send(message, Doc('📚', 'Group FAQ').status('info', 'Usage', '/faq search <key fragment> [page 1–7]. Search covers keys only.'))
            screen, markup = _index_doc(doc, int(page_token), term)
        else:
            name = _valid_key(action) if not rest else None
            if not name:
                return await _send(message, Doc('📚', 'Group FAQ').status('info', 'Usage', '/faq, /faq <key>, /faq search <fragment>, or /faq list [page].'))
            screen, markup = _entry_doc(doc, name)
        return await _send(message, screen, markup)
    except Exception:
        return await _send(message, Doc('⚠️', 'Group FAQ').status('warning', 'Unavailable', 'Could not read or save entries. Try again later.'))


@Client.on_callback_query(filters.regex(r'^plg_faq:[a-zA-Z0-9_:-]+$'))
async def faq_callback(client: Client, query: CallbackQuery):
    if not plugin_enabled('group_knowledge'):
        return await query.answer('Group FAQ is disabled.', show_alert=True)
    message = query.message
    if not message or not query.from_user or not _scope(client, message):
        return await query.answer('Open this FAQ in its original group.', show_alert=True)
    parts = (query.data or '').split(':')
    if len(parts) not in (3, 4) or parts[0] != 'plg_faq':
        return await query.answer('Invalid FAQ action.', show_alert=True)
    try:
        doc = await _load(_scope(client, message))
        if len(parts) == 3 and parts[1] == 'open' and _valid_key(parts[2]):
            screen, markup = _entry_doc(doc, _valid_key(parts[2]))
        elif len(parts) == 3 and parts[1] == 'list' and parts[2].isascii() and parts[2].isdigit() and len(parts[2]) <= 2 and 1 <= int(parts[2]) <= 7:
            screen, markup = _index_doc(doc, int(parts[2]))
        elif (len(parts) == 4 and parts[1] == 'search' and re.fullmatch(r'[a-z0-9_-]{1,32}', parts[2])
              and parts[3].isascii() and parts[3].isdigit() and len(parts[3]) <= 2 and 1 <= int(parts[3]) <= 7):
            screen, markup = _index_doc(doc, int(parts[3]), parts[2])
        else:
            return await query.answer('Invalid FAQ action.', show_alert=True)
        await message.edit_text(screen.classic(), reply_markup=markup, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        return await query.answer()
    except Exception:
        return await query.answer('FAQ unavailable. Try again later.', show_alert=True)
    return await query.answer()

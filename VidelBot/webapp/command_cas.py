"""Conditional edits and deletes for workspace command metadata."""
from __future__ import annotations

import time
from typing import Any
from pymongo import ReturnDocument
from platform_core import models_db
from webapp import command_create, db_models, services_bots

_FIELDS = frozenset({'description', 'category', 'permission', 'visibility', 'enabled', 'custom_reply_text', 'handler_bound'})
_EDITABLE = frozenset({'description', 'category', 'permission', 'visibility', 'enabled'})
_TEXT_LIMITS = {'description': 256, 'category': 64, 'permission': 64, 'visibility': 64, 'custom_reply_text': 1000}


def _input(body: dict[str, Any], *, deleting: bool) -> tuple[str, dict[str, Any], dict[str, Any]]:
    if set(body) != ({'command', 'expected'} if deleting else {'command', 'expected', 'changes'}):
        raise services_bots.SaaSValidationError('Invalid conditional command request.')
    cmd, expected = body['command'], body['expected']
    if not isinstance(cmd, str) or not services_bots.CMD_NAME_RE.fullmatch(cmd):
        raise services_bots.SaaSValidationError('Invalid command name.')
    if not isinstance(expected, dict) or set(expected) != _FIELDS:
        raise services_bots.SaaSValidationError('Provide the complete expected command metadata.')
    for key, limit in _TEXT_LIMITS.items():
        if not isinstance(expected[key], str) or len(expected[key]) > limit:
            raise services_bots.SaaSValidationError('Invalid expected command metadata.')
    if any(not isinstance(expected[key], bool) for key in ('enabled', 'handler_bound')):
        raise services_bots.SaaSValidationError('Invalid expected command metadata.')
    if deleting:
        return cmd, expected, {}
    changes = body['changes']
    if not isinstance(changes, dict) or not changes or not set(changes).issubset(_EDITABLE):
        raise services_bots.SaaSValidationError('Provide changed command metadata only.')
    changes = dict(changes)
    if 'description' in changes:
        desc = changes['description']
        if not isinstance(desc, str) or not 2 <= len(desc.strip()) <= 256:
            raise services_bots.SaaSValidationError('Invalid command description.')
        changes['description'] = desc.strip()
    if 'category' in changes and (not isinstance(changes['category'], str) or changes['category'] not in services_bots.COMMAND_CATEGORIES):
        raise services_bots.SaaSValidationError('Invalid command category.')
    if ('permission' in changes) != ('visibility' in changes):
        raise services_bots.SaaSValidationError('Permission and visibility must be updated together.')
    if 'permission' in changes:
        perm, visible = changes['permission'], changes['visibility']
        if (not isinstance(perm, str) or perm not in services_bots.COMMAND_PERMISSIONS or
                not isinstance(visible, str) or visible != ('public' if perm == 'everyone' else 'scoped')):
            raise services_bots.SaaSValidationError('Invalid command permission metadata.')
    if 'enabled' in changes and not isinstance(changes['enabled'], bool):
        raise services_bots.SaaSValidationError('Invalid command enabled state.')
    return cmd, expected, changes


async def mutate(workspace_id: str, bot_id: str, actor_id: int, body: dict[str, Any], *, deleting: bool) -> dict[str, Any] | None:
    cmd, expected, changes = _input(body, deleting=deleting)
    await services_bots.get_owned_workspace_bot(workspace_id, bot_id)
    await command_create._require_unique_index(db_models.bots_col(), [('bot_id', 1)])
    col = db_models.bot_commands_col()
    await command_create._require_unique_index(col, [('bot_id', 1), ('command', 1)])
    comparisons = [{'$eq': [{'$ifNull': [f'${field}', False if field in ('enabled', 'handler_bound') else '']}, {'$literal': value}]}
                   for field, value in expected.items()]
    query = {'bot_id': str(bot_id), 'command': cmd, '$expr': {'$and': comparisons}}
    if deleting:
        doc = await col.find_one_and_delete(query, collation={'locale': 'simple'})
    else:
        doc = await col.find_one_and_update(query, {'$set': {**changes, 'updated_at': db_models.utcnow_iso(), 'updated_ts': time.time()}}, return_document=ReturnDocument.AFTER, collation={'locale': 'simple'})
    if doc is None:
        return None
    doc.pop('_id', None)
    await models_db.log_audit(actor_id, 'bot_command_conditional_delete' if deleting else 'bot_command_conditional_update', target=f'{workspace_id}:{bot_id}:/{cmd}')
    return doc

"""Offline tests for group-scoped FAQ handlers, permissions and revision safety."""
import asyncio
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run
from tests.test_group_mod_and_security import GroupFakeClient, _full_privs, _grp_msg

load_all()


@pytest.fixture(autouse=True)
def setup():
    from core.plugin_manager import plugin_manager
    run(reset_db())
    no_botapi()
    plugin_manager._enabled.clear()
    plugin_manager.discover()
    yield
    plugin_manager._enabled.clear()
    no_botapi()


def client(bot=999):
    c = GroupFakeClient({10: (enums.ChatMemberStatus.OWNER, None),
                         20: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_change_info=False)),
                         30: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
                         bot: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())})
    c.me.id = bot
    return c


def message(text, uid=10, chat=-100888, c=None):
    return _grp_msg(text, uid=uid, chat_id=chat, client=c)


def test_save_read_replace_delete_and_stale_confirmation():
    from core.db import vdb
    from plugins.group_knowledge import plugin as faq
    c = client()
    save = message('/faq save rules Be kind & welcome <everyone>', c=c)
    run(faq.faq_cmd(c, save))
    doc = run(vdb.db[faq._COLLECTION].find_one({'_id': '999:-100888'}))
    assert doc['entries'] == {'rules': 'Be kind & welcome <everyone>'} and doc['version'] == 1
    assert set(doc) == {'_id', 'bot_id', 'chat_id', 'version', 'entries'}
    get = message('/faq rules', uid=77, c=c)
    run(faq.faq_cmd(c, get))
    assert 'Be kind &amp; welcome &lt;everyone&gt;' in get.replies[-1] and '<everyone>' not in get.replies[-1]
    duplicate = message('/faq save rules overwrite?', c=c)
    run(faq.faq_cmd(c, duplicate))
    assert 'already exists' in duplicate.replies[-1].lower()
    run(faq.faq_cmd(c, message('/faq save other separate entry', c=c)))
    stale = message('/faq replace rules 1 changed', c=c)
    run(faq.faq_cmd(c, stale))
    assert 'stale' in stale.replies[-1].lower()
    run(faq.faq_cmd(c, message('/faq replace rules 2 new text with spaces', c=c)))
    doc = run(vdb.db[faq._COLLECTION].find_one({'_id': '999:-100888'}))
    assert doc['version'] == 3 and doc['entries']['rules'] == 'new text with spaces'
    preview = message('/faq delete rules', c=c)
    run(faq.faq_cmd(c, preview))
    assert '/faq delete rules confirm 3' in preview.replies[-1]
    run(faq.faq_cmd(c, message('/faq save another revision change', c=c)))
    bad = message('/faq delete rules confirm 3', c=c)
    run(faq.faq_cmd(c, bad))
    assert 'stale' in bad.replies[-1].lower()
    run(faq.faq_cmd(c, message('/faq delete rules confirm 4', c=c)))
    doc = run(vdb.db[faq._COLLECTION].find_one({'_id': '999:-100888'}))
    assert doc['version'] == 5 and 'rules' not in doc['entries']
    # Re-created keys still cannot be deleted by a stale previous confirmation.
    run(faq.faq_cmd(c, message('/faq save rules fresh', c=c)))
    run(faq.faq_cmd(c, bad))
    assert 'stale' in bad.replies[-1].lower()
    assert run(vdb.db[faq._COLLECTION].find_one({'_id': '999:-100888'}))['entries']['rules'] == 'fresh'


def test_permissions_scope_isolation_reply_text_and_invalid_input():
    from core.db import vdb
    from core.middleware import senderless_gate
    from plugins.group_knowledge import plugin as faq
    c, other = client(), client(1000)
    for uid in (20, 77):
        denied = message('/faq save key should not save', uid=uid, c=c)
        run(faq.faq_cmd(c, denied))
        assert 'permission check failed' in denied.replies[-1].lower()
    assert run(vdb.db[faq._COLLECTION].count_documents({})) == 0
    anon = message('/faq save hello anonymous admin', uid=None, c=c)
    anon.sender_chat = SimpleNamespace(id=anon.chat.id)
    run(senderless_gate(c, anon))
    run(faq.faq_cmd(c, anon))
    source = message('/faq save replykey', uid=30, c=c)
    source.reply_to_message = message('Body from a reply\nsecond line', uid=77, c=c)
    run(faq.faq_cmd(c, source))
    assert run(vdb.db[faq._COLLECTION].find_one({'_id': '999:-100888'}))['entries']['replykey'] == 'Body from a reply\nsecond line'
    for bot, chat in ((c, -100999), (other, -100888)):
        view = message('/faq hello', uid=77, chat=chat, c=bot)
        run(faq.faq_cmd(bot, view))
        assert 'entry not found' in view.replies[-1].lower() and 'anonymous admin' not in view.replies[-1]
    for command in ('/faq save bad.key hello', '/faq save search test', '/faq save xx ' + 'a' * 701):
        invalid = message(command, c=c)
        run(faq.faq_cmd(c, invalid))
        assert 'invalid' in invalid.replies[-1].lower() or 'usage' in invalid.replies[-1].lower()
    assert run(vdb.db[faq._COLLECTION].count_documents({})) == 1
    private = FakeMsg('/faq hello', uid=77, client=c)
    assert not run(faq.faq_cmd.handlers[0][0].filters(c, private))


def test_search_pagination_buttons_and_disabled_callback():
    from core.plugin_manager import plugin_manager
    from plugins.group_knowledge import plugin as faq
    c = client()
    for i in range(12):
        run(faq.faq_cmd(c, message(f'/faq save help{i:02} message {i}', c=c)))
    doc = run(faq._load((999, -100888)))
    screen, markup = faq._index_doc(doc, 1, 'help')
    values = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert 'plg_faq:search:help:2' in values and 'plg_faq:open:help00' in values
    assert all(len(value.encode()) <= 64 for value in values)
    board = message('board', uid=10, c=c)
    q = FakeQuery('plg_faq:search:help:2', uid=77, message=board)
    run(faq.faq_callback(c, q))
    assert q.answers[-1] == (None, False) and '#help08' in board.edits[-1] and '#help00' not in board.edits[-1]
    q = FakeQuery('plg_faq:open:help10', uid=77, message=board)
    run(faq.faq_callback(c, q))
    assert 'message 10' in board.edits[-1]
    board.chat.type = enums.ChatType.PRIVATE
    q = FakeQuery('plg_faq:open:help10', uid=77, message=board)
    run(faq.faq_callback(c, q))
    assert q.answers[-1][1] and 'group' in q.answers[-1][0].lower()
    plugin_manager._enabled['group_knowledge'] = False
    q = FakeQuery('plg_faq:list:1', uid=77, message=message('board', c=c))
    run(faq.faq_callback(c, q))
    assert 'disabled' in q.answers[-1][0].lower()


def test_capacity_and_concurrent_create_and_revision_guard():
    from plugins.group_knowledge import plugin as faq
    from core.db import vdb
    col = vdb.db[faq._COLLECTION]
    scope = (999, -100888)
    entries = {f'key{i:02}': str(i) for i in range(49)}
    run(col.insert_one({'_id': '999:-100888', 'bot_id': 999, 'chat_id': -100888, 'version': 1, 'entries': entries}))
    async def create_two():
        return await asyncio.gather(faq._create(scope, 'alpha', 'A'), faq._create(scope, 'beta', 'B'))
    results = run(create_two())
    assert sorted(results) == ['full', 'saved']
    doc = run(col.find_one({'_id': '999:-100888'}))
    assert len(doc['entries']) == 50 and doc['version'] == 2
    async def edit_two():
        return await asyncio.gather(faq._change(scope, 'key00', 2, 'first'), faq._change(scope, 'key01', 2, 'second'))
    results = run(edit_two())
    assert sorted(results) == ['stale', 'updated']
    assert run(col.find_one({'_id': '999:-100888'}))['version'] == 3


def test_plugin_discovery_menu_and_loader():
    import run as bot_run
    from core.commands import GROUP_COMMANDS, _cmds
    from core.plugin_manager import plugin_manager
    from plugins.group_knowledge import plugin as faq
    manifest = plugin_manager.discover()['group_knowledge']
    assert {x['name'] for x in manifest.commands} == {'faq'}
    assert not plugin_manager.command_conflicts(manifest)
    assert 'faq' in {x.command for x in _cmds(GROUP_COMMANDS)} and len(_cmds(GROUP_COMMANDS)) <= 100
    class Registry:
        def __init__(self):
            self.handlers = []
        def add_handler(self, handler, group):
            self.handlers.append((handler, group))
    registry = Registry()
    bot_run.load_plugins(registry)
    callbacks = [getattr(handler, 'callback', None) for handler, _ in registry.handlers]
    assert faq.faq_cmd in callbacks and faq.faq_callback in callbacks
    assert not bot_run.boot_import_errors.get('plugins.group_knowledge.plugin')


def test_forced_simultaneous_saves_cannot_exceed_capacity():
    from core.db import vdb
    from plugins.group_knowledge import plugin as faq
    scope = (999, -100888)
    real = vdb.db[faq._COLLECTION]
    entries = {f'key{i:02}': str(i) for i in range(49)}
    run(real.insert_one({'_id': '999:-100888', 'bot_id': 999, 'chat_id': -100888, 'version': 1, 'entries': entries}))
    class BarrierCollection:
        def __init__(self):
            self.reads = 0
            self.both_read = asyncio.Event()
        async def find_one(self, *args, **kwargs):
            doc = await real.find_one(*args, **kwargs)
            self.reads += 1
            if self.reads <= 2:
                if self.reads == 2:
                    self.both_read.set()
                await self.both_read.wait()
            return doc
        async def update_one(self, *args, **kwargs):
            return await real.update_one(*args, **kwargs)
        async def insert_one(self, *args, **kwargs):
            return await real.insert_one(*args, **kwargs)
    barrier = BarrierCollection()
    original = faq.vdb
    class DB:
        def __getitem__(self, name):
            return barrier if name == faq._COLLECTION else original.db[name]
    faq.vdb = SimpleNamespace(db=DB())
    async def exercise():
        return await asyncio.gather(faq._create(scope, 'one', 'one'), faq._create(scope, 'two', 'two'))
    try:
        results = run(exercise())
    finally:
        faq.vdb = original
    assert sorted(results) == ['full', 'saved']
    assert len(run(real.find_one({'_id': '999:-100888'}))['entries']) == 50


def test_unicode_key_rejected_and_confirmation_needs_live_admin():
    from core.db import vdb
    from plugins.group_knowledge import plugin as faq
    c = client()
    unicode_key = message('/faq save K secret', c=c)
    run(faq.faq_cmd(c, unicode_key))
    assert 'invalid key' in unicode_key.replies[-1].lower()
    run(faq.faq_cmd(c, message('/faq save entry public text', c=c)))
    denied = message('/faq delete entry confirm 1', uid=77, c=c)
    run(faq.faq_cmd(c, denied))
    assert 'permission check failed' in denied.replies[-1].lower()
    assert run(vdb.db[faq._COLLECTION].find_one({'_id': '999:-100888'}))['entries']['entry'] == 'public text'


def test_revision_checked_rename_preserves_content_and_rejects_collisions():
    from core.db import vdb
    from plugins.group_knowledge import plugin as faq
    c = client()
    run(faq.faq_cmd(c, message('/faq save old hello & world', c=c)))
    run(faq.faq_cmd(c, message('/faq save taken other', c=c)))
    denied = message('/faq rename old new 2', uid=77, c=c)
    run(faq.faq_cmd(c, denied))
    assert 'permission check failed' in denied.replies[-1].lower()
    stale = message('/faq rename old new 1', c=c)
    run(faq.faq_cmd(c, stale)); assert 'stale' in stale.replies[-1].lower()
    collision = message('/faq rename old taken 2', c=c)
    run(faq.faq_cmd(c, collision)); assert 'already exists' in collision.replies[-1].lower()
    for text in ('/faq rename old rename 2', '/faq rename old bad.key 2', '/faq rename old new ２', '/faq rename old new 2 extra'):
        invalid = message(text, c=c); run(faq.faq_cmd(c, invalid))
        assert 'usage' in invalid.replies[-1].lower()
    changed = message('/faq rename old New 2', c=c)
    run(faq.faq_cmd(c, changed)); assert 'renamed' in changed.replies[-1].lower()
    doc = run(vdb.db[faq._COLLECTION].find_one({'_id': '999:-100888'}))
    assert doc['version'] == 3 and doc['entries'] == {'new': 'hello & world', 'taken': 'other'}
    retry = message('/faq rename old another 2', c=c)
    run(faq.faq_cmd(c, retry)); assert 'not found' in retry.replies[-1].lower()
    reserved = message('/faq save rename forbidden', c=c)
    run(faq.faq_cmd(c, reserved)); assert 'invalid key' in reserved.replies[-1].lower()


def test_rename_compare_and_swap_does_not_lose_concurrent_changes():
    from plugins.group_knowledge import plugin as faq
    from core.db import vdb
    scope = (999, -100888)
    col = vdb.db[faq._COLLECTION]
    run(col.insert_one({'_id': '999:-100888', 'bot_id': 999, 'chat_id': -100888,
                        'version': 1, 'entries': {'first': 'one', 'second': 'two'}}))
    class Interleaved:
        async def find_one(self, *args, **kwargs):
            doc = await col.find_one(*args, **kwargs)
            await col.update_one({'_id': doc['_id'], 'version': 1},
                                 {'$set': {'entries': {'first': 'one', 'second': 'new'}}, '$inc': {'version': 1}})
            return doc
        async def update_one(self, *args, **kwargs):
            return await col.update_one(*args, **kwargs)
    original = faq.vdb
    faq.vdb = SimpleNamespace(db={faq._COLLECTION: Interleaved()})
    try:
        assert run(faq._rename(scope, 'first', 'renamed', 1)) == 'stale'
    finally:
        faq.vdb = original
    doc = run(col.find_one({'_id': '999:-100888'}))
    assert doc['version'] == 2 and doc['entries'] == {'first': 'one', 'second': 'new'}


def test_failed_permission_lookup_and_database_error_fail_closed():
    from plugins.group_knowledge import plugin as faq
    from core.db import vdb
    c = client()
    async def unavailable_member(*args):
        raise RuntimeError('permission service unavailable')
    c.get_chat_member = unavailable_member
    denied = message('/faq save sensitive data', c=c)
    run(faq.faq_cmd(c, denied))
    assert 'permission check failed' in denied.replies[-1].lower()
    assert run(vdb.db[faq._COLLECTION].count_documents({})) == 0
    class Broken:
        def __getitem__(self, name):
            raise RuntimeError('sensitive database details')
    old = faq.vdb
    faq.vdb = SimpleNamespace(db=Broken())
    try:
        view = message('/faq', uid=77, c=c)
        run(faq.faq_cmd(c, view))
        assert 'unavailable' in view.replies[-1].lower()
        assert 'sensitive database details' not in view.replies[-1]
    finally:
        faq.vdb = old


def test_rename_is_scoped_by_both_bot_and_group():
    from plugins.group_knowledge import plugin as faq
    from core.db import vdb
    owner, other_bot = client(), client(1000)
    for c, chat, value in ((owner, -100888, 'one'), (owner, -100999, 'two'), (other_bot, -100888, 'three')):
        run(faq.faq_cmd(c, message('/faq save intro ' + value, chat=chat, c=c)))
    run(faq.faq_cmd(owner, message('/faq rename intro welcome 1', c=owner)))
    docs = [run(vdb.db[faq._COLLECTION].find_one({'_id': key})) for key in ('999:-100888', '999:-100999', '1000:-100888')]
    assert docs[0]['entries'] == {'welcome': 'one'}
    assert docs[1]['entries'] == {'intro': 'two'} and docs[2]['entries'] == {'intro': 'three'}

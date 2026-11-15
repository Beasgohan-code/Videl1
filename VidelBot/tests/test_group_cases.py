"""Offline tests for private group-admin casebook isolation and revision safety."""
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
                         20: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_restrict_members=False)),
                         30: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
                         bot: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())})
    c.me.id = bot
    return c


def dm(text, uid=10, c=None):
    return FakeMsg(text, uid=uid, chat_type=enums.ChatType.PRIVATE, chat_id=uid, client=c)


def record(c, summary='Summary'):
    from core.db import vdb
    from plugins.group_cases import plugin as cases
    m = dm(f'/modcase -100888 open 77 {summary}', c=c)
    run(cases.modcase_cmd(c, m))
    doc = run(vdb.db[cases._COLLECTION].find_one({'_id': f'{c.me.id}:-100888'}))
    assert doc and len(doc['cases']) == 1
    return next(iter(doc['cases']))


def test_private_case_lifecycle_revision_and_no_automatic_enforcement():
    from core.db import vdb
    from plugins.group_cases import plugin as cases
    c = client()
    case_id = record(c, 'Read <message> & decide')
    show = dm(f'/modcase -100888 show {case_id}', c=c)
    run(cases.modcase_cmd(c, show))
    assert 'Read &lt;message&gt; &amp; decide' in show.replies[-1] and '<message>' not in show.replies[-1]
    assert 'complete audit log' in show.replies[-1] and 'unverified' in show.replies[-1]
    run(cases.modcase_cmd(c, dm(f'/modcase -100888 note {case_id} 1 Review <evidence> & context', c=c)))
    doc = run(vdb.db[cases._COLLECTION].find_one({'_id': '999:-100888'}))
    assert doc['version'] == 2 and doc['cases'][case_id]['notes'][0]['text'] == 'Review <evidence> & context'
    run(cases.modcase_cmd(c, show))
    assert 'Review &lt;evidence&gt; &amp; context' in show.replies[-1] and 'Noted at' in show.replies[-1] and 'UTC' in show.replies[-1]
    stale = dm(f'/modcase -100888 close {case_id} 1 confirm', c=c)
    run(cases.modcase_cmd(c, stale))
    assert 'stale' in stale.replies[-1].lower()
    run(cases.modcase_cmd(c, dm(f'/modcase -100888 close {case_id} 2 confirm', c=c)))
    doc = run(vdb.db[cases._COLLECTION].find_one({'_id': '999:-100888'}))
    assert doc['version'] == 3 and doc['cases'][case_id]['status'] == 'closed'
    run(cases.modcase_cmd(c, dm(f'/modcase -100888 reopen {case_id} 3 confirm', c=c)))
    preview = dm(f'/modcase -100888 purge {case_id}', c=c)
    run(cases.modcase_cmd(c, preview))
    assert f'/modcase -100888 purge {case_id} 4 confirm' in preview.replies[-1]
    run(cases.modcase_cmd(c, dm(f'/modcase -100888 note {case_id} 4 newer update', c=c)))
    stale_purge = dm(f'/modcase -100888 purge {case_id} 4 confirm', c=c)
    run(cases.modcase_cmd(c, stale_purge))
    assert 'stale' in stale_purge.replies[-1].lower()
    run(cases.modcase_cmd(c, dm(f'/modcase -100888 purge {case_id} 5 confirm', c=c)))
    doc = run(vdb.db[cases._COLLECTION].find_one({'_id': '999:-100888'}))
    assert doc['version'] == 6 and doc['cases'] == {}
    assert not c.called('ban_chat_member') and not c.called('restrict_chat_member') and not c.called('delete_messages')


def test_access_requires_live_group_admin_and_strict_private_identity(monkeypatch):
    from core.db import vdb
    from plugins.group_cases import plugin as cases
    c = client()
    case_id = record(c, 'PRIVATE CASE CONTENT')
    for uid in (20, 77):
        denied = dm(f'/modcase -100888 show {case_id}', uid=uid, c=c)
        run(cases.modcase_cmd(c, denied))
        assert 'access unavailable' in denied.replies[-1].lower() and 'PRIVATE CASE CONTENT' not in denied.replies[-1]
    group = _grp_msg(f'/modcase -100888 show {case_id}', uid=10, client=c)
    assert not run(cases.modcase_cmd.handlers[0][0].filters(c, group))
    run(cases.modcase_cmd(c, group))
    assert not group.replies
    forged = dm(f'/modcase -100888 show {case_id}', uid=10, c=c)
    forged.chat.id = 77
    run(cases.modcase_cmd(c, forged))
    assert not forged.replies
    # A verifier error is fail-closed, before the case collection is read.
    async def broken(*args, **kwargs):
        raise RuntimeError('Telegram unavailable')
    c.get_chat_member = broken
    async def never_load(*args, **kwargs):
        raise AssertionError('database read before authorization')
    monkeypatch.setattr(cases, '_load', never_load)
    unavailable = dm(f'/modcase -100888 show {case_id}', c=c)
    run(cases.modcase_cmd(c, unavailable))
    assert 'access unavailable' in unavailable.replies[-1].lower() and 'PRIVATE CASE CONTENT' not in unavailable.replies[-1]
    assert run(vdb.db[cases._COLLECTION].count_documents({})) == 1


def test_cross_group_and_bot_isolation_and_revoked_callback():
    from core.plugin_manager import plugin_manager
    from plugins.group_cases import plugin as cases
    c, other = client(), client(1000)
    case_id = record(c, 'GroupOneSecret')
    for bot, group in ((c, -100999), (other, -100888)):
        request = dm(f'/modcase {group} show {case_id}', c=bot)
        run(cases.modcase_cmd(bot, request))
        assert 'case not found' in request.replies[-1].lower() and 'GroupOneSecret' not in request.replies[-1]
    board = dm('board', uid=10, c=c)
    q = FakeQuery(f'plg_case:show:-100888:{case_id}:1', uid=10, message=board)
    run(cases.modcase_callback(c, q))
    assert q.answers[-1] == (None, False) and 'GroupOneSecret' in board.edits[-1]
    before = len(board.edits)
    c._members[10] = (enums.ChatMemberStatus.MEMBER, None)
    run(cases.modcase_callback(c, q))
    assert len(board.edits) == before and 'verify' in q.answers[-1][0].lower()
    mismatch = FakeQuery(f'plg_case:show:-100888:{case_id}:1', uid=30, message=board)
    run(cases.modcase_callback(c, mismatch))
    assert len(board.edits) == before and mismatch.answers[-1][1]
    plugin_manager._enabled['group_cases'] = False
    run(cases.modcase_callback(c, q))
    assert 'disabled' in q.answers[-1][0].lower()


def test_note_limits_long_html_and_safe_pagination():
    from core.db import vdb
    from plugins.group_cases import plugin as cases
    c = client()
    cid = record(c, '<' * 200)
    long = dm(f'/modcase -100888 note {cid} 1 ' + '&' * 401, c=c)
    run(cases.modcase_cmd(c, long))
    assert 'invalid note' in long.replies[-1].lower()
    run(cases.modcase_cmd(c, dm(f'/modcase -100888 note {cid} 1 ' + '&' * 400, c=c)))
    show = dm(f'/modcase -100888 show {cid}', c=c)
    run(cases.modcase_cmd(c, show))
    assert len(show.replies[-1]) < 4096 and show.replies[-1].count('&amp;') == 400
    doc = run(vdb.db[cases._COLLECTION].find_one({'_id': '999:-100888'}))
    doc['cases'][cid]['notes'] = [{'author_id': 10, 'at': 0, 'text': str(i)} for i in range(20)]
    run(vdb.db[cases._COLLECTION].replace_one({'_id': doc['_id']}, doc))
    full = dm(f'/modcase -100888 note {cid} 2 more', c=c)
    run(cases.modcase_cmd(c, full))
    assert '20-note limit' in full.replies[-1]
    _, markup = cases._case_doc(doc, -100888, cid, 1)
    values = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert f'plg_case:show:-100888:{cid}:2' in values and all(len(x.encode()) <= 64 for x in values)


def test_simultaneous_edits_do_not_overwrite_and_creation_cap():
    from core.db import vdb
    from plugins.group_cases import plugin as cases
    c = client()
    cid = record(c)
    scope = (999, -100888)
    real = vdb.db[cases._COLLECTION]
    class BarrierCollection:
        def __init__(self):
            self.reads = 0
            self.both = asyncio.Event()
        async def find_one(self, *args, **kwargs):
            doc = await real.find_one(*args, **kwargs)
            self.reads += 1
            if self.reads <= 2:
                if self.reads == 2:
                    self.both.set()
                await self.both.wait()
            return doc
        async def update_one(self, *args, **kwargs):
            return await real.update_one(*args, **kwargs)
        async def insert_one(self, *args, **kwargs):
            return await real.insert_one(*args, **kwargs)
    barrier = BarrierCollection()
    original = cases.vdb
    class DB:
        def __getitem__(self, name):
            return barrier if name == cases._COLLECTION else original.db[name]
    cases.vdb = SimpleNamespace(db=DB())
    async def exercise():
        return await asyncio.gather(cases._change(scope, cid, 1, 10, 'note', 'first'),
                                    cases._change(scope, cid, 1, 30, 'note', 'second'))
    try:
        results = run(exercise())
    finally:
        cases.vdb = original
    assert sorted(results) == ['stale', 'updated']
    doc = run(real.find_one({'_id': '999:-100888'}))
    assert len(doc['cases'][cid]['notes']) == 1 and doc['version'] == 2
    doc['cases'] = {f'{i:012x}': doc['cases'][cid] for i in range(100)}
    run(real.replace_one({'_id': doc['_id']}, doc))
    assert run(cases._create(scope, 10, 77, 'new')) == ('full', None)


def test_discovery_filters_and_input_validation():
    import run as bot_run
    from core.commands import USER_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS, _cmds
    from core.plugin_manager import plugin_manager
    from plugins.group_cases import plugin as cases
    manifest = plugin_manager.discover()['group_cases']
    assert {x['name'] for x in manifest.commands} == {'modcase'} and not plugin_manager.command_conflicts(manifest)
    assert len(_cmds(USER_COMMANDS + ADMIN_COMMANDS + OWNER_COMMANDS)) <= 100
    class Registry:
        def __init__(self):
            self.handlers = []
        def add_handler(self, handler, group):
            self.handlers.append((handler, group))
    registry = Registry()
    bot_run.load_plugins(registry)
    callbacks = [getattr(handler, 'callback', None) for handler, _ in registry.handlers]
    assert cases.modcase_cmd in callbacks and cases.modcase_callback in callbacks
    c = client()
    for command in ('/modcase 123 open 77 x', '/modcase -100888 open -77 x', '/modcase -100888 open 77 ' + 'a' * 201,
                    '/modcase -100888 show ../../etc'):
        request = dm(command, c=c)
        run(cases.modcase_cmd(c, request))
        assert request.replies and ('usage' in request.replies[-1].lower() or 'private group-admin' in request.replies[-1].lower())


def test_group_membership_is_verified_for_requested_group_before_database_read(monkeypatch):
    from plugins.group_cases import plugin as cases
    c = client()
    cid = record(c, 'No cross-group disclosure')
    original = c.get_chat_member
    async def group_permissions(group, uid):
        if group != -100888:
            return SimpleNamespace(status=enums.ChatMemberStatus.MEMBER, privileges=None)
        return await original(group, uid)
    c.get_chat_member = group_permissions
    async def never_load(*args, **kwargs):
        raise AssertionError('should not read data for unverified group')
    monkeypatch.setattr(cases, '_load', never_load)
    request = dm(f'/modcase -100999 show {cid}', c=c)
    run(cases.modcase_cmd(c, request))
    assert 'access unavailable' in request.replies[-1].lower() and 'No cross-group disclosure' not in request.replies[-1]
    board = dm('board', c=c)
    q = FakeQuery(f'plg_case:show:-100999:{cid}:1', uid=10, message=board)
    run(cases.modcase_callback(c, q))
    assert 'verify' in q.answers[-1][0].lower() and not board.edits


def test_forced_simultaneous_creations_respect_hard_limit():
    from core.db import vdb
    from plugins.group_cases import plugin as cases
    scope = (999, -100888)
    real = vdb.db[cases._COLLECTION]
    template = {'target_id': 77, 'summary': 's', 'status': 'open', 'created_by': 10,
                'created_at': 1, 'updated_by': 10, 'updated_at': 1, 'notes': []}
    existing = {f'{i:012x}': template for i in range(99)}
    run(real.insert_one({'_id': '999:-100888', 'bot_id': 999, 'chat_id': -100888, 'version': 1, 'cases': existing}))
    class BarrierCollection:
        def __init__(self):
            self.reads = 0
            self.both = asyncio.Event()
        async def find_one(self, *args, **kwargs):
            doc = await real.find_one(*args, **kwargs)
            self.reads += 1
            if self.reads <= 2:
                if self.reads == 2:
                    self.both.set()
                await self.both.wait()
            return doc
        async def update_one(self, *args, **kwargs):
            return await real.update_one(*args, **kwargs)
        async def insert_one(self, *args, **kwargs):
            return await real.insert_one(*args, **kwargs)
    barrier = BarrierCollection()
    original = cases.vdb
    class DB:
        def __getitem__(self, name):
            return barrier if name == cases._COLLECTION else original.db[name]
    cases.vdb = SimpleNamespace(db=DB())
    async def exercise():
        return await asyncio.gather(cases._create(scope, 10, 77, 'first'), cases._create(scope, 30, 88, 'second'))
    try:
        results = run(exercise())
    finally:
        cases.vdb = original
    assert sorted(result[0] for result in results) == ['created', 'full']
    assert len(run(real.find_one({'_id': '999:-100888'}))['cases']) == 100


def test_bot_membership_is_required_before_case_data_reads_or_writes(monkeypatch):
    from core.db import vdb
    from plugins.group_cases import plugin as cases
    c = client(); cid = record(c, 'sensitive record')
    original = c.get_chat_member
    async def never_load(*args, **kwargs):
        raise AssertionError('case data read before authorization')
    monkeypatch.setattr(cases, '_load', never_load)
    for status, is_member in ((enums.ChatMemberStatus.LEFT, False), (enums.ChatMemberStatus.BANNED, False),
                              (enums.ChatMemberStatus.RESTRICTED, False), (None, None)):
        async def check(group, uid):
            if uid == c.me.id:
                return SimpleNamespace(status=status, is_member=is_member)
            return await original(group, uid)
        c.get_chat_member = check
        request = dm(f'/modcase -100888 show {cid}', c=c)
        run(cases.modcase_cmd(c, request))
        assert 'access unavailable' in request.replies[-1].lower() and 'sensitive record' not in request.replies[-1]
        write = dm('/modcase -100888 open 88 must not save', c=c)
        run(cases.modcase_cmd(c, write)); assert 'access unavailable' in write.replies[-1].lower()
        q = FakeQuery(f'plg_case:show:-100888:{cid}:1', uid=10, message=dm('board', c=c))
        run(cases.modcase_callback(c, q)); assert q.answers[-1][1] and not q.message.edits
    async def error(group, uid):
        if uid == c.me.id:
            raise RuntimeError('unknown membership')
        return await original(group, uid)
    c.get_chat_member = error
    assert not run(cases._authorize(c, -100888, 10))
    assert run(vdb.db[cases._COLLECTION].find_one({'_id': '999:-100888'}))['version'] == 1
    async def restricted_member(group, uid):
        return SimpleNamespace(status=enums.ChatMemberStatus.RESTRICTED, is_member=True) if uid == c.me.id else await original(group, uid)
    c.get_chat_member = restricted_member
    assert run(cases._authorize(c, -100888, 10))


def test_find_cases_by_user_pagination_and_permissions():
    from core.db import vdb
    from plugins.group_cases import plugin as cases
    c = client(); scope = (999, -100888)
    col = vdb.db[cases._COLLECTION]
    records = {}
    for i in range(12):
        records[f'{i:012x}'] = {'target_id': 77 if i < 10 else 88, 'status': 'closed' if i % 2 else 'open',
                                'created_at': i, 'created_by': 10, 'updated_by': 10, 'updated_at': i,
                                'summary': 'private', 'notes': []}
    run(col.insert_one({'_id': '999:-100888', 'bot_id': 999, 'chat_id': -100888, 'version': 1, 'cases': records}))
    first = dm('/modcase -100888 find 77', c=c); run(cases.modcase_cmd(c, first))
    assert '77' in first.replies[-1] and '000000000009' in first.replies[-1] and '00000000000b' not in first.replies[-1]
    _, markup = cases._list_doc(run(col.find_one({'_id': '999:-100888'})), -100888, 'all', 1, target=77)
    buttons = [button.callback_data for row in markup.inline_keyboard for button in row]
    assert 'plg_case:find:-100888:77:2' in buttons and all(len(x.encode()) <= 64 for x in buttons)
    board = dm('board', c=c)
    q = FakeQuery('plg_case:find:-100888:77:2', uid=10, message=board)
    run(cases.modcase_callback(c, q))
    assert q.answers[-1] == (None, False) and '000000000001' in board.edits[-1] and '000000000009' not in board.edits[-1]
    c._members[10] = (enums.ChatMemberStatus.MEMBER, None)
    before = len(board.edits); run(cases.modcase_callback(c, q))
    assert len(board.edits) == before and q.answers[-1][1]
    for raw in ('0', '-1', '７７', '77 14', '77 1 extra'):
        bad = dm('/modcase -100888 find ' + raw, uid=30, c=c)
        run(cases.modcase_cmd(c, bad)); assert 'private group-admin' in bad.replies[-1].lower()
    assert len('plg_case:find:-9223372036854775808:9223372036854775807:13'.encode()) <= 64

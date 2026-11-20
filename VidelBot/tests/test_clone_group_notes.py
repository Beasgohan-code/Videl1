"""Rose-inspired plain-text notes; fake Telegram and in-memory Mongo only."""
import asyncio
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import reset_db, run
from tests.test_audit import _dispatch
from tests.test_clone_group_info import GroupClient, message
from tests.test_clone_group_moderation import application, status
from filestore.database.mongo import get_db
from filestore.worker_bot import group_notes


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())


async def dispatch(app, client, text, chat_id=-100, user_id=5):
    msg = message(text, chat_id, user_id)
    await _dispatch(app, client, msg)
    return msg


def test_dispatch_isolation_plain_text_and_case(application):
    async def go():
        a, b = GroupClient(4242), GroupClient(5151)
        assert 'saved' in (await dispatch(application, a, '/save Guide First line\n<b>safe</b> & more')).replies[-1]
        await dispatch(application, a, '.save guide Other chat', -200)
        await dispatch(application, b, '/save guide Other bot')
        assert (await dispatch(application, a, '/get GUIDE', user_id=9)).replies[-1] == 'First line\n&lt;b&gt;safe&lt;/b&gt; &amp; more'
        assert (await dispatch(application, a, '.get guide', -200, 9)).replies[-1] == 'Other chat'
        assert (await dispatch(application, b, '/get guide', user_id=9)).replies[-1] == 'Other bot'
        listing = await dispatch(application, a, '/notes', user_id=9)
        assert '<code>guide</code>' in listing.replies[-1]
        missing = await dispatch(application, a, '/get missing', user_id=9)
        assert 'not found' in missing.replies[-1]
        docs = await get_db()[group_notes.COLLECTION].find({}).to_list(None)
        assert {(d['bot_id'], d['chat_id']) for d in docs} == {(4242, -100), (4242, -200), (5151, -100)}
    run(go())


@pytest.mark.parametrize('text', ['/save x test', '.clear x'])
def test_mutations_require_direct_admin_and_change_info(application, text):
    async def go():
        for kind in ('member', 'no_right', 'anonymous', 'bot', 'failure'):
            c = GroupClient(); m = message(text)
            if kind == 'member': c.members[5] = status(enums.ChatMemberStatus.MEMBER)
            if kind == 'no_right': c.members[5] = SimpleNamespace(status=enums.ChatMemberStatus.ADMINISTRATOR, privileges=SimpleNamespace(can_change_info=False))
            if kind == 'anonymous': m.from_user = None; m.sender_chat = SimpleNamespace(id=-100)
            if kind == 'bot': m.from_user.is_bot = True
            if kind == 'failure': c.members[5] = RuntimeError('secret')
            await _dispatch(application, c, m)
            assert 'admin' in m.replies[-1].lower()
            assert await get_db()[group_notes.COLLECTION].count_documents({}) == 0
    run(go())


def test_update_clear_and_limits(application):
    async def go():
        c = GroupClient()
        for text in ['/save a.b text', '/save ü text', '/save bad', '/save a ' + 'x'*1001, '/save a ' + '"'*1000]:
            m = await dispatch(application, c, text)
            assert 'Usage:' in m.replies[-1]
        for i in range(group_notes.MAX_NOTES):
            assert 'saved' in (await dispatch(application, c, f'/save n{i} value')).replies[-1]
        assert 'already has' in (await dispatch(application, c, '/save extra more')).replies[-1]
        assert 'saved' in (await dispatch(application, c, '/save n1 replacement')).replies[-1]
        assert 'cleared' in (await dispatch(application, c, '/clear n1')).replies[-1]
        assert 'not found' in (await dispatch(application, c, '/clear n1')).replies[-1]
        assert 'saved' in (await dispatch(application, c, '/save extra more')).replies[-1]
        assert 'Usage:' in (await dispatch(application, c, '/notes extra')).replies[-1]
        assert 'Usage:' in (await dispatch(application, c, '/get extra unexpected')).replies[-1]
    run(go())


def test_corrupt_or_unavailable_storage_fails_closed_without_leaking(application, monkeypatch):
    async def go():
        c = GroupClient(); coll = get_db()[group_notes.COLLECTION]
        await coll.insert_one({'_id':'4242:-100', 'bot_id':5151, 'chat_id':-100, 'version':'0'*32, 'notes':{'a':'secret'}})
        for text in ('/save a b', '/get a', '/notes', '/clear a'):
            m = await dispatch(application, c, text)
            assert '⚠️' in m.replies[-1] and 'secret' not in m.replies[-1]
        assert (await coll.find_one({'_id':'4242:-100'}))['notes'] == {'a':'secret'}
        async def fail(*args): raise RuntimeError('secret db uri')
        monkeypatch.setattr(group_notes, '_load', fail)
        assert 'secret' not in (await dispatch(application, c, '/get a')).replies[-1]
    run(go())


def test_cas_conflict_reloads_and_retries_but_uncertain_write_does_not(monkeypatch):
    async def go():
        scope={'_id':'4242:-100','bot_id':4242,'chat_id':-100}
        coll = get_db()[group_notes.COLLECTION]
        await coll.insert_one({**scope, 'version':'0'*32, 'notes':{'a':'old'}})
        real = coll.update_one
        calls = []
        async def racing(query, update):
            calls.append(query)
            if len(calls) == 1:
                await real(query, {'$set': {'version':'1'*32, 'notes':{'a':'old', 'other':'concurrent'}}})
            return await real(query, update)
        monkeypatch.setattr(coll, 'update_one', racing)
        monkeypatch.setattr(group_notes, 'get_db', lambda: {group_notes.COLLECTION: coll})
        assert await group_notes._change(scope, 'a', 'new') == 'saved'
        doc = await coll.find_one({'_id':scope['_id']})
        assert doc['notes'] == {'a':'new', 'other':'concurrent'} and len(calls) == 2
        async def uncertain(*args, **kwargs):
            calls.append('uncertain')
            raise RuntimeError('unknown result')
        monkeypatch.setattr(coll, 'update_one', uncertain)
        with pytest.raises(RuntimeError): await group_notes._change(scope, 'a', 'again')
        assert calls.count('uncertain') == 1
    run(go())


def test_scope_and_addressed_bot_filters(application):
    async def go():
        c = GroupClient(); m = await dispatch(application, c, '/save@OtherBot a b')
        assert not m.replies
        m = message('/save a b'); m.chat.type = enums.ChatType.PRIVATE
        await _dispatch(application, c, m)
        assert not m.replies and await get_db()[group_notes.COLLECTION].count_documents({}) == 0
        m = await dispatch(application, c, '/save@CloneBot a b')
        assert 'saved' in m.replies[-1]
    run(go())


def test_concurrent_final_slot_cannot_exceed_limit(monkeypatch):
    async def go():
        scope = {'_id':'4242:-100', 'bot_id':4242, 'chat_id':-100}
        coll = get_db()[group_notes.COLLECTION]
        await coll.insert_one({**scope, 'version':'0'*32,
                               'notes':{f'n{i}':'text' for i in range(group_notes.MAX_NOTES - 1)}})
        original = group_notes._load
        count = 0
        both_read = asyncio.Event()
        async def gated_load(s):
            nonlocal count
            result = await original(s)
            count += 1
            if count <= 2:
                if count == 2:
                    both_read.set()
                await both_read.wait()
            return result
        monkeypatch.setattr(group_notes, '_load', gated_load)
        outcomes = await asyncio.gather(group_notes._change(scope, 'newa', 'A'), group_notes._change(scope, 'newb', 'B'))
        assert sorted(outcomes) == ['full', 'saved']
        doc = await coll.find_one({'_id':scope['_id']})
        assert len(doc['notes']) == group_notes.MAX_NOTES
        assert ('newa' in doc['notes']) != ('newb' in doc['notes'])
    run(go())


def test_duplicate_initial_insert_reloads_and_does_not_erase_winner(monkeypatch):
    from pymongo.errors import DuplicateKeyError
    async def go():
        scope={'_id':'4242:-100','bot_id':4242,'chat_id':-100}
        coll=get_db()[group_notes.COLLECTION]
        original=coll.insert_one
        calls=[]
        async def first_insert(doc):
            calls.append(doc)
            if len(calls) == 1:
                await original({**scope, 'version':'1'*32, 'notes':{'other':'winner'}})
                raise DuplicateKeyError('duplicate')
            return await original(doc)
        monkeypatch.setattr(coll, 'insert_one', first_insert)
        monkeypatch.setattr(group_notes, 'get_db', lambda: {group_notes.COLLECTION: coll})
        assert await group_notes._change(scope, 'mine', 'value') == 'saved'
        assert (await coll.find_one({'_id':scope['_id']}))['notes'] == {'other':'winner', 'mine':'value'}
        assert len(calls) == 1
    run(go())


def test_bounded_cas_conflicts_and_unacknowledged_writes(monkeypatch):
    async def go():
        scope={'_id':'4242:-100','bot_id':4242,'chat_id':-100}
        coll = get_db()[group_notes.COLLECTION]
        await coll.insert_one({**scope, 'version':'0'*32, 'notes':{'a':'old'}})
        attempts = []
        async def conflict(*args, **kwargs):
            attempts.append(1)
            return SimpleNamespace(acknowledged=True, matched_count=0)
        monkeypatch.setattr(coll, 'update_one', conflict)
        monkeypatch.setattr(group_notes, 'get_db', lambda: {group_notes.COLLECTION: coll})
        assert await group_notes._change(scope, 'a', 'new') == 'conflict'
        assert len(attempts) == group_notes.MAX_ATTEMPTS
        async def unacknowledged(*args, **kwargs):
            attempts.append(1)
            return SimpleNamespace(acknowledged=False)
        monkeypatch.setattr(coll, 'update_one', unacknowledged)
        before = len(attempts)
        with pytest.raises(RuntimeError):
            await group_notes._change(scope, 'a', 'new')
        assert len(attempts) == before + 1
        assert (await coll.find_one({'_id':scope['_id']}))['notes'] == {'a':'old'}
    run(go())

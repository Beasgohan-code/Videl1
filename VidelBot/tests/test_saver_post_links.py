"""Offline Content Saver link parsing and routing; no live Telegram calls."""
import pytest

from tests.harness import FakeClient, FakeMsg, load_all, no_botapi, reset_db, run

load_all()
from saver.links import PostLink, parse_post_link
from saver import start
from database.db import db


@pytest.fixture(autouse=True)
def clean():
    run(reset_db())
    no_botapi()
    start.batch_temp.IS_BATCH.clear()
    start.batch_temp.ACTIVE.clear()
    yield
    start.batch_temp.IS_BATCH.clear()
    start.batch_temp.ACTIVE.clear()


@pytest.mark.parametrize('text,expected', [
    ('https://t.me/example/123', PostLink('public', 'example', 123, 123)),
    ('See HTTPS://T.ME/Example_2/10-12?single please', PostLink('public', 'Example_2', 10, 12)),
    ('https://t.me/s/example/10-12/', PostLink('public', 'example', 10, 12)),
    ('https://t.me/c/123456789/7?single=1', PostLink('private', -100123456789, 7, 7)),
    ('https://t.me/c/123456789/7-9', PostLink('private', -100123456789, 7, 9)),
    ('http://t.me/b/SomeBot/42-43', PostLink('bot', 'SomeBot', 42, 43)),
    ('https://t.me/example/2147483647', PostLink('public', 'example', 2147483647, 2147483647)),
])
def test_supported_post_links(text, expected):
    assert parse_post_link(text) == expected


@pytest.mark.parametrize('text', [
    '', 'https://example.com/a/12', 'https://evil.test/https://t.me/name/12',
    'https://t.me/name', 'https://t.me/+invite/12', 'https://t.me/joinchat/abc',
    'https://t.me/name/s/12', 'https://t.me/name/1/2', 'https://t.me/c/123/1/2',
    'https://t.me/name/12?comment=13', 'https://t.me/name/12?thread=13',
    'https://t.me/name/12#fragment', 'https://t.me/name/12-13-14',
    'https://t.me/name/12-no', 'https://t.me/name/0', 'https://t.me/name/9-8',
    'https://t.me/name/2147483648', 'https://t.me/name/'+'9'*5000,
    'https://t.me/c/0/12', 'https://t.me/c/01/12', 'https://t.me/c/notanint/12',
    'https://t.me/b/invalid-name/12', 'https://t.me/name/%31%32',
    'https://t.me/name/12)']
)
def test_reject_ambiguous_or_malformed_links(text):
    with pytest.raises(ValueError):
        parse_post_link(text)


def test_public_preview_and_ranges_route_to_existing_bot_copy(monkeypatch):
    async def fast_sleep(_): pass
    monkeypatch.setattr(start.asyncio, 'sleep', fast_sleep)
    uid = 73501
    run(db.add_user(uid, 'Saver tester'))
    client = FakeClient()
    message = FakeMsg('https://t.me/s/example/4-6?single', uid=uid, client=client)
    run(start.save(client, message))
    copied = [call[2] for call in client.called('copy_message')]
    assert [(c['from_chat_id'],c['message_id']) for c in copied] == [('example',4),('example',5),('example',6)]
    assert start.batch_temp.IS_BATCH[uid] is True


def test_private_and_bot_ranges_use_existing_user_session(monkeypatch):
    async def fast_sleep(_): pass
    calls = []
    class Session:
        def __init__(self, *args, **kwargs):
            assert kwargs['no_updates'] is True
            assert kwargs['in_memory'] is True
        async def connect(self): calls.append(('connect',))
        async def disconnect(self): calls.append(('disconnect',))
    async def handle(_client, _session, _message, chat, msg): calls.append((chat,msg))
    monkeypatch.setattr(start.asyncio, 'sleep', fast_sleep)
    monkeypatch.setattr(start, 'Client', Session)
    monkeypatch.setattr(start, 'handle_restricted_content', handle)
    uid = 73502
    run(db.add_user(uid, 'Saver tester'))
    run(db.set_session(uid, 'test-session'))
    client = FakeClient()
    for text, expected in [('https://t.me/c/123456789/7-8', [(-100123456789,7),(-100123456789,8)]),
                           ('https://t.me/b/SomeBot/3-4', [('SomeBot',3),('SomeBot',4)])]:
        calls.clear()
        run(start.save(client, FakeMsg(text, uid=uid, client=client)))
        assert calls == [('connect',), *expected, ('disconnect',)]
        assert start.batch_temp.IS_BATCH[uid] is True
    assert not client.called('copy_message')


def test_ambiguous_link_rejected_without_telegram_or_session_access(monkeypatch):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError('must not read a session')
    monkeypatch.setattr(db, 'get_session', forbidden)
    uid = 73503
    run(db.add_user(uid, 'Saver tester'))
    client = FakeClient()
    for text in ['https://t.me/name/12?comment=45', 'https://t.me/name/s/45', 'https://t.me/c/123/1/2']:
        message = FakeMsg(text, uid=uid, client=client)
        run(start.save(client, message))
        assert any('unsupported post link' in reply for reply in message.replies)
        assert not client.called('copy_message')


def test_cancelled_task_stays_claimed_until_cleanup(monkeypatch):
    import asyncio
    uid = 73504
    run(db.add_user(uid, 'Saver tester'))
    async def fast_sleep(_): pass
    monkeypatch.setattr(start.asyncio, 'sleep', fast_sleep)
    async def scenario():
        client = FakeClient()
        entered, release = asyncio.Event(), asyncio.Event()
        original = client.copy_message
        async def blocked_copy(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(client, 'copy_message', blocked_copy, raising=False)
        first = asyncio.create_task(start.save(client, FakeMsg('https://t.me/example/1-2', uid=uid, client=client)))
        await entered.wait()
        # Cancellation requests stop work but must not release the task claim yet.
        start.batch_temp.IS_BATCH[uid] = True
        second = FakeMsg('https://t.me/example/3', uid=uid, client=client)
        await start.save(client, second)
        assert any('cleaning up' in text for text in second.replies)
        assert not client.called('copy_message')
        release.set()
        await first
        assert [c[2]['message_id'] for c in client.called('copy_message')] == [1]
        assert uid not in start.batch_temp.ACTIVE
        monkeypatch.setattr(client, 'copy_message', original, raising=False)
        await start.save(client, FakeMsg('https://t.me/example/3', uid=uid, client=client))
        assert [c[2]['message_id'] for c in client.called('copy_message')] == [1,3]
    run(scenario())


def test_claim_taken_before_database_wait_and_released_on_failure(monkeypatch):
    import asyncio
    uid = 73505
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def failing_lookup(_):
            entered.set()
            await release.wait()
            raise RuntimeError('synthetic database outage')
        monkeypatch.setattr(db, 'is_user_exist', failing_lookup)
        client = FakeClient()
        first = asyncio.create_task(start.save(client, FakeMsg('https://t.me/example/1', uid=uid, client=client)))
        await entered.wait()
        second = FakeMsg('https://t.me/example/2', uid=uid, client=client)
        await start.save(client, second)
        assert any('cleaning up' in text for text in second.replies)
        release.set()
        with pytest.raises(RuntimeError, match='synthetic database outage'):
            await first
        assert uid not in start.batch_temp.ACTIVE
    run(scenario())


def test_one_users_running_transfer_does_not_block_another(monkeypatch):
    import asyncio
    first_uid, other_uid = 73506, 73507
    run(db.add_user(first_uid, 'First'))
    run(db.add_user(other_uid, 'Second'))
    async def fast_sleep(_): pass
    monkeypatch.setattr(start.asyncio, 'sleep', fast_sleep)
    async def scenario():
        first_client, other_client = FakeClient(), FakeClient()
        entered, release = asyncio.Event(), asyncio.Event()
        original = first_client.copy_message
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)
        monkeypatch.setattr(first_client, 'copy_message', blocked, raising=False)
        first = asyncio.create_task(start.save(first_client, FakeMsg('https://t.me/example/1', uid=first_uid, client=first_client)))
        await entered.wait()
        await start.save(other_client, FakeMsg('https://t.me/example/2', uid=other_uid, client=other_client))
        assert [c[2]['message_id'] for c in other_client.called('copy_message')] == [2]
        release.set()
        await first
        assert not start.batch_temp.ACTIVE
    run(scenario())

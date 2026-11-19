"""Synthetic WzGram guest/guardian paths; no Telegram service is contacted."""
import asyncio
from types import SimpleNamespace

import pytest
from pyrogram import ContinuePropagation, StopPropagation, enums
from pyrogram.handlers import GuestMessageHandler, ChatJoinRequestHandler
from tests.harness import FakeClient, load_all, run
import config
from core import guest_modes as modes


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    modes._seen_guest.clear(); modes._guest_users.clear(); modes._seen_guard.clear()
    modes._guest_starts.clear(); modes._guard_starts.clear()
    modes._inflight_guest = 0; modes._inflight_guard = 0
    monkeypatch.setattr(config, 'GUEST_REPLIES_ENABLED', True)
    monkeypatch.setattr(config, 'GUARD_QUEUE_ENABLED', True)


class Client:
    def __init__(self):
        self.me = SimpleNamespace(id=int(config.BOT_TOKEN.split(':')[0]))
        self.guests = []; self.guards = []; self.fail = False
    async def answer_guest_query(self, qid, result):
        self.guests.append((qid, result))
        if self.fail: raise RuntimeError('private query secret')
        return object()
    async def answer_chat_join_request_query(self, qid, result):
        self.guards.append((qid, result))
        if self.fail: raise RuntimeError('private query secret')
        return True


def guest(qid='1234', uid=7, is_bot=False):
    return SimpleNamespace(guest_query_id=qid, guest_bot_caller_user=SimpleNamespace(id=uid, is_bot=is_bot), text='private input')


def test_registered_with_existing_wzgram_dispatcher():
    modules = load_all()
    assert modes in modules
    assert any(isinstance(h, GuestMessageHandler) for h, g in modes.guest_introduction.handlers)
    assert any(isinstance(h, ChatJoinRequestHandler) and g == -10 for h, g in modes.guardian_manual_queue.handlers)


def test_guest_single_public_fixed_reply_and_replay_blocked():
    c = Client(); m = guest()
    run(modes.guest_introduction(c, m)); run(modes.guest_introduction(c, m))
    assert len(c.guests) == 1
    result = c.guests[0][1]
    assert result.input_message_content.message_text == modes._GUEST_REPLY
    assert 'private input' not in result.input_message_content.message_text
    assert result.input_message_content.parse_mode == enums.ParseMode.DISABLED


def test_guest_wrong_bot_missing_caller_and_bot_caller_ignored(monkeypatch):
    c = Client(); c.me.id += 1; run(modes.guest_introduction(c, guest()))
    c.me.id -= 1
    run(modes.guest_introduction(c, guest(is_bot=True)))
    run(modes.guest_introduction(c, SimpleNamespace(guest_query_id='123', guest_bot_caller_user=None)))
    run(modes.guest_introduction(c, guest(qid='9'*5000)))
    monkeypatch.setattr(config, 'GUEST_REPLIES_ENABLED', False)
    run(modes.guest_introduction(c, guest()))
    assert c.guests == []


def test_guest_failure_does_not_retry_and_logs_no_input(caplog):
    c = Client(); c.fail = True
    run(modes.guest_introduction(c, guest())); run(modes.guest_introduction(c, guest()))
    assert len(c.guests) == 1 and 'private input' not in caplog.text and 'private query secret' not in caplog.text


def test_guardian_queues_only_and_stops_legacy_handlers():
    c = Client(); r = SimpleNamespace(query_id='1234')
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, r))
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, r))
    assert c.guards == [('1234', enums.ChatJoinRequestQueryResult.QUEUE)]
    with pytest.raises(ContinuePropagation): run(modes.guardian_manual_queue(c, SimpleNamespace(query_id=None)))


def test_guardian_disabled_invalid_or_failure_never_reaches_legacy(monkeypatch, caplog):
    c = Client()
    monkeypatch.setattr(config, 'GUARD_QUEUE_ENABLED', False)
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, SimpleNamespace(query_id='123')))
    monkeypatch.setattr(config, 'GUARD_QUEUE_ENABLED', True)
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, SimpleNamespace(query_id='9'*5000)))
    c.fail = True
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, SimpleNamespace(query_id='456')))
    assert len(c.guards) == 1 and 'private query secret' not in caplog.text


def test_guardian_cancellation_is_not_swallowed():
    async def go():
        class Slow(Client):
            async def answer_chat_join_request_query(self, *args):
                await asyncio.Event().wait()
        task = asyncio.create_task(modes.guardian_manual_queue(Slow(), SimpleNamespace(query_id='1234')))
        await asyncio.sleep(0); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    run(go())
    assert modes._inflight_guard == 0


def test_guest_article_serializes_using_installed_wzgram():
    from pyrogram import raw
    c = Client(); run(modes.guest_introduction(c, guest()))
    serializer = FakeClient(); serializer.link_preview_options = None
    article = run(c.guests[0][1].write(serializer))
    assert isinstance(article, raw.types.InputBotInlineResult)
    assert article.send_message.message == modes._GUEST_REPLY
    assert article.send_message.reply_markup is None


def test_signed_mtproto_query_ids_and_bounds():
    assert modes._query_id('-123') and modes._query_id(str(-(2**63)))
    assert not modes._query_id('0') and not modes._query_id(str(2**63))
    assert not modes._query_id(str(-(2**63)-1)) and not modes._query_id('-')
    c = Client()
    with pytest.raises(StopPropagation):
        run(modes.guardian_manual_queue(c, SimpleNamespace(query_id='-123')))
    assert c.guards == [('-123', enums.ChatJoinRequestQueryResult.QUEUE)]


def test_guard_rate_limit_and_cancellation_release_inflight():
    c = Client()
    for i in range(21):
        with pytest.raises(StopPropagation):
            run(modes.guardian_manual_queue(c, SimpleNamespace(query_id=str(i + 1))))
    assert len(c.guards) == 20
    assert modes._inflight_guard == 0


def test_real_plugin_loader_registers_priority_handlers():
    import run as app_run
    from core import fsub
    from plugins.group_mod import plugin as group_mod
    class Registry:
        def __init__(self): self.added = []
        def add_handler(self, handler, group): self.added.append((handler, group))
    registry = Registry()
    app_run.load_plugins(registry)
    by_callback = {h.callback: group for h, group in registry.added}
    assert by_callback[modes.guest_introduction] == 0
    assert by_callback[modes.guardian_manual_queue] == -10
    assert by_callback[fsub.record_join_request] == 0
    assert by_callback[group_mod.group_join_request_handler] == 4
    assert not app_run.boot_import_errors

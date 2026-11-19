"""Synthetic /clone entry points, capability and cancellation boundaries."""
import asyncio
from types import SimpleNamespace

import pytest
from aiogram import types as at
from pyrogram import enums
from tests.harness import FakeClient, FakeMsg, FakeQuery, no_botapi, reset_db, run, use_botapi
from core import botapi, menus
from filestore.main_bot.plugins import create_bot, managed_bots
import config


@pytest.fixture(autouse=True)
def clean():
    run(reset_db()); no_botapi(); create_bot._creation_state.clear()
    yield
    no_botapi(); create_bot._creation_state.clear()


def buttons(kb):
    return {b.callback_data: b.text for row in kb.inline_keyboard for b in row if b.callback_data}


def user_result(value=True, uid=123456):
    return at.User(id=uid, is_bot=True, first_name='Videl', username='VidelBot', can_manage_bots=value)


def test_hub_shows_fresh_confirmed_capability_and_always_manual(monkeypatch):
    captured = []
    async def send(client, chat_id, text, markup, **kw): captured.append((text, markup))
    async def picture(): return None
    monkeypatch.setattr(menus, 'send_with_preview', send)
    monkeypatch.setattr(menus, 'random_start_pic', picture)
    session = use_botapi(responses={'GetMe': user_result(True)})
    run(menus.clone_cmd(FakeClient(), FakeMsg('/clone', uid=5)))
    text, kb = captured[-1]; options = buttons(kb)
    assert options['clone_managed_start'] == '🤖 Create a bot with Telegram'
    assert options['create_bot'] == '🔑 Use an existing bot token'
    assert 'confirm creation' in text and 'Music' in text
    assert session.names().count('GetMe') == 1
    session.responses['GetMe'] = user_result(False)
    run(menus.clone_cmd(FakeClient(), FakeMsg('/clone', uid=5)))
    assert 'clone_managed_start' not in buttons(captured[-1][1])
    assert 'create_bot' in buttons(captured[-1][1])


@pytest.mark.parametrize('result', [user_result(False), user_result(None), user_result(True, uid=444)])
def test_capability_requires_true_and_matching_bot(result):
    use_botapi(responses={'GetMe': result})
    assert run(managed_bots.available()) is False


def test_getme_failure_cannot_reuse_cached_success(monkeypatch):
    session = use_botapi(responses={'GetMe': user_result(True)})
    assert run(botapi.me()).can_manage_bots is True
    session.fail['GetMe'] = RuntimeError('sensitive failure')
    assert not run(managed_bots.available())
    monkeypatch.setattr(managed_bots, 'MANAGED_BOTS', False)
    assert not run(managed_bots.available())


def test_direct_telegram_flow_and_manual_choice(monkeypatch):
    def stop_poll(coro): coro.close()
    monkeypatch.setattr(managed_bots, '_spawn', stop_poll)
    session = use_botapi(responses={'GetMe': user_result(True)})
    q = FakeQuery('clone_managed_start', uid=5)
    seen = {}
    async def edit(text=None, reply_markup=None, **kwargs): seen.update(text=text, kb=reply_markup)
    q.message.edit_text = edit
    run(create_bot.create_managed_callback(FakeClient(), q))
    assert 'Create a bot with Telegram' in seen['text']
    assert q.answers[-1] == (None, False)
    assert 'further setup is required' in seen['text'].lower()
    url_button = seen['kb'].inline_keyboard[0][0]
    assert url_button.url.startswith('https://t.me/newbot/VidelBot/')
    choices = buttons(seen['kb'])
    manual_data = next(key for key in choices if key.startswith('managed_action_') and key.endswith('_manual'))
    assert choices[manual_data] == '🔑 Paste a token instead'
    assert create_bot._creation_state[5]['step'] == 'awaiting_token'
    assert session.names().count('GetMe') == 1
    manual = FakeQuery(manual_data, uid=5)
    run(managed_bots.managed_setup_action(FakeClient(), manual))
    assert create_bot._creation_state[5]['step'] == 'awaiting_token'
    assert 'Send your bot token' in manual.message.text


def test_expired_shortcut_falls_back_to_manual_setup():
    use_botapi(responses={'GetMe': user_result(False)})
    q = FakeQuery('clone_managed_start', uid=5)
    run(create_bot.create_managed_callback(FakeClient(), q))
    assert 'Send your bot token' in q.message.text
    assert 'could not be confirmed' in q.answers[-1][0]
    assert create_bot._creation_state[5]['step'] == 'awaiting_token'


@pytest.mark.parametrize('handler,data', [(create_bot.create_bot_callback, 'create_bot'),
                                           (create_bot.create_managed_callback, 'clone_managed_start'),
                                           (managed_bots.managed_new, 'managed_new')])
def test_buttons_refuse_group_or_mismatched_private_chat(handler, data):
    use_botapi()
    for chat_type, chat_id in [(enums.ChatType.SUPERGROUP, -1001), (enums.ChatType.PRIVATE, 6)]:
        q = FakeQuery(data, uid=5); q.message.chat.type = chat_type; q.message.chat.id = chat_id
        run(handler(FakeClient(), q))
        assert 'private chat' in q.answers[-1][0] and 5 not in create_bot._creation_state


def test_clone_disabled_limit_and_cooldown_apply_to_shortcut(monkeypatch):
    use_botapi()
    monkeypatch.setattr(create_bot, 'CLONE_ENABLED', False)
    q = FakeQuery('clone_managed_start', uid=5); run(create_bot.create_managed_callback(FakeClient(), q))
    assert 'disabled' in q.answers[-1][0] and 5 not in create_bot._creation_state
    monkeypatch.setattr(create_bot, 'CLONE_ENABLED', True)
    async def limit(uid): return 0
    monkeypatch.setattr('core.plans.clone_limit', limit)
    q = FakeQuery('clone_managed_start', uid=5); run(create_bot.create_managed_callback(FakeClient(), q))
    assert 'limit' in q.answers[-1][0] and 5 not in create_bot._creation_state


def test_double_click_and_cancel_during_quota_check_do_not_revive(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def count(uid): entered.set(); await release.wait(); return 0
        monkeypatch.setattr(create_bot.main_db, 'count_user_bots', count)
        q = FakeQuery('clone_managed_start', uid=5)
        first = asyncio.create_task(create_bot.create_managed_callback(FakeClient(), q)); await entered.wait()
        duplicate = FakeQuery('create_bot', uid=5)
        await create_bot.create_bot_callback(FakeClient(), duplicate)
        assert 'already being processed' in duplicate.answers[-1][0]
        await create_bot.cancel_creation_callback(FakeClient(), FakeQuery('cancel_creation', uid=5))
        release.set(); await first
        assert 5 not in create_bot._creation_state and 'cancelled' in q.answers[-1][0]
    run(go())


def test_cancel_or_clone_during_registration_preserves_inflight_state(monkeypatch):
    state = {'step': 'registering', 'data': {'registration_token': 'secret'}}
    create_bot._creation_state[5] = state
    run(menus.cancel_cmd(FakeClient(), FakeMsg('/cancel', uid=5)))
    assert create_bot._creation_state[5] is state
    assert menus._clear_flows(5) is None and create_bot._creation_state[5] is state


def test_cooldown_applies_to_telegram_shortcut(monkeypatch):
    from datetime import datetime
    async def recent(uid): return datetime.utcnow()
    monkeypatch.setattr(create_bot.main_db, 'get_cooldown', recent)
    monkeypatch.setattr(create_bot, 'BOT_CREATION_COOLDOWN', 60)
    use_botapi()
    q = FakeQuery('clone_managed_start', uid=5)
    run(create_bot.create_managed_callback(FakeClient(), q))
    assert 'wait' in q.answers[-1][0] and 5 not in create_bot._creation_state


def test_capability_disabled_or_bridge_off_keeps_manual_available(monkeypatch):
    no_botapi()
    assert 'create_bot' in buttons(run(menus.clone_hub_keyboard(5)))
    assert 'clone_managed_start' not in buttons(run(menus.clone_hub_keyboard(5)))
    use_botapi(); monkeypatch.setattr(menus, 'CLONE_ENABLED', False)
    assert 'clone_managed_start' not in buttons(run(menus.clone_hub_keyboard(5)))
    assert 'create_bot' in buttons(run(menus.clone_hub_keyboard(5)))


def test_cancel_or_duplicate_while_capability_check_waits_does_not_open_link(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_available(): entered.set(); await release.wait(); return True
        monkeypatch.setattr(managed_bots, 'available', slow_available)
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[5] = state
        first = FakeQuery('managed_new', uid=5)
        task = asyncio.create_task(managed_bots.managed_new(FakeClient(), first)); await entered.wait()
        duplicate = FakeQuery('managed_new', uid=5)
        await managed_bots.managed_new(FakeClient(), duplicate)
        assert 'already being checked' in duplicate.answers[-1][0]
        create_bot._creation_state.pop(5)
        release.set(); await task
        assert 'Setup changed' in first.answers[-1][0] and first.message.edits == []
    run(go())




def test_expired_callback_does_not_discard_displayed_manual_prompt():
    use_botapi(responses={'GetMe': user_result(False)})
    q = FakeQuery('clone_managed_start', uid=5)
    async def expired(*args, **kwargs): raise RuntimeError('expired callback')
    q.answer = expired
    run(create_bot.create_managed_callback(FakeClient(), q))
    assert 'Send your bot token' in q.message.text
    assert create_bot._creation_state[5]['step'] == 'awaiting_token'


def test_late_prompt_failure_does_not_destroy_advanced_setup(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked_edit(*args, **kwargs): entered.set(); await release.wait(); raise RuntimeError('failed')
        no_botapi(); q = FakeQuery('create_bot', uid=5); q.message.edit_text = blocked_edit
        task = asyncio.create_task(create_bot.create_bot_callback(FakeClient(), q)); await entered.wait()
        state = create_bot._creation_state[5]
        state['step'] = 'verifying_token'
        release.set(); await task
        assert create_bot._creation_state[5] is state and state['step'] == 'verifying_token'
    run(go())

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


def test_old_clone_card_has_small_caps_and_original_buttons_without_capability_check(monkeypatch):
    captured = []
    async def send(client, chat_id, text, markup, **kw): captured.append((text, markup))
    async def picture(): return None
    monkeypatch.setattr(menus, 'send_with_preview', send)
    monkeypatch.setattr(menus, 'random_start_pic', picture)
    session = use_botapi(responses={'GetMe': user_result(True)})
    run(menus.clone_cmd(FakeClient(), FakeMsg('/clone', uid=5)))
    text, kb = captured[-1]; options = buttons(kb)
    assert options == {'create_bot': '⚡ ᴄʀᴇᴀᴛᴇ ʙᴏᴛ', 'my_bots': '📋 ᴍʏ ʙᴏᴛs',
                       'clone_help': '📖 ʜᴇʟᴘ', 'clone_about': 'ℹ️ ᴀʙᴏᴜᴛ', 'start_btn': '⬅️ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ'}
    assert '𝗩𝗜𝗗𝗘𝗟 𝗙𝗜𝗟𝗘𝗦𝗧𝗢𝗥𝗘' in text and 'ᴡᴇʟᴄᴏᴍᴇ' in text
    assert session.names().count('GetMe') == 0


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


def test_create_button_offers_telegram_or_manual_and_opens_telegram(monkeypatch):
    monkeypatch.setattr(managed_bots, '_spawn', lambda coro: coro.close())
    session = use_botapi(responses={'GetMe': user_result(True)})
    q = FakeQuery('create_bot', uid=5); seen = {}
    async def edit(text=None, reply_markup=None, **kwargs): seen.update(text=text, kb=reply_markup)
    q.message.edit_text = edit
    run(create_bot.create_bot_callback(FakeClient(), q))
    options = buttons(seen['kb'])
    telegram = next(key for key in options if key.endswith('_telegram'))
    manual = next(key for key in options if key.endswith('_manual'))
    assert options[telegram] == '🤖 Create a bot with Telegram' and options[manual] == '🔑 Paste a token'
    assert session.names().count('GetMe') == 1
    run(create_bot.creation_control_callback(FakeClient(), FakeQuery(telegram, uid=5, message=q.message)))
    assert 'Create a bot with Telegram' in seen['text'] and 'further setup is required' in seen['text'].lower()
    assert seen['kb'].inline_keyboard[0][0].url.startswith('https://t.me/newbot/VidelBot/')
    assert session.names().count('GetMe') == 2  # fresh recheck before opening
    manual_data = next(key for key in buttons(seen['kb']) if key.startswith('managed_action_') and key.endswith('_manual'))
    manual_query = FakeQuery(manual_data, uid=5)
    run(managed_bots.managed_setup_action(FakeClient(), manual_query))
    assert 'Send your bot token' in manual_query.message.text
    assert create_bot._creation_state[5]['step'] == 'awaiting_token'


def test_unavailable_capability_still_offers_manual_token():
    use_botapi(responses={'GetMe': user_result(False)})
    q = FakeQuery('create_bot', uid=5); seen = {}
    async def edit(text=None, reply_markup=None, **kwargs): seen.update(text=text, kb=reply_markup)
    q.message.edit_text = edit
    run(create_bot.create_bot_callback(FakeClient(), q))
    options = buttons(seen['kb'])
    assert not any(key.endswith('_telegram') for key in options)
    manual = next(key for key in options if key.endswith('_manual'))
    assert 'unavailable' in seen['text']
    run(create_bot.creation_control_callback(FakeClient(), FakeQuery(manual, uid=5, message=q.message)))
    assert 'Send your bot token' in seen['text'] and create_bot._creation_state[5]['step'] == 'awaiting_token'


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
        await create_bot._cancel_current_creation(FakeClient(), FakeQuery('cancel_creation', uid=5), create_bot._creation_state.get(5))
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
        nonce = create_bot._stage_nonce(state, 'token')
        first = FakeQuery(f'clone_setup_{nonce}_telegram', uid=5)
        task = asyncio.create_task(managed_bots.managed_new(FakeClient(), first)); await entered.wait()
        duplicate = FakeQuery(f'clone_setup_{nonce}_telegram', uid=5)
        await managed_bots.managed_new(FakeClient(), duplicate)
        assert 'already being checked' in duplicate.answers[-1][0]
        create_bot._creation_state.pop(5)
        release.set(); await task
        assert 'Setup changed' in first.answers[-1][0] and first.message.edits == []
    run(go())




def test_expired_callback_does_not_discard_displayed_choice():
    use_botapi(responses={'GetMe': user_result(False)})
    q = FakeQuery('create_bot', uid=5)
    async def expired(*args, **kwargs): raise RuntimeError('expired callback')
    q.answer = expired
    run(create_bot.create_bot_callback(FakeClient(), q))
    assert 'Choose how to connect' in q.message.text
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


def test_manual_choice_wins_over_pending_telegram_capability_check(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(): entered.set(); await release.wait(); return True
        monkeypatch.setattr(managed_bots, 'available', slow)
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[5] = state
        nonce = create_bot._stage_nonce(state, 'choice')
        telegram = FakeQuery(f'clone_setup_{nonce}_telegram', uid=5)
        task = asyncio.create_task(create_bot.creation_control_callback(FakeClient(), telegram)); await entered.wait()
        manual = FakeQuery(f'clone_setup_{nonce}_manual', uid=5)
        await create_bot.creation_control_callback(FakeClient(), manual)
        assert 'Send your bot token' in manual.message.text and state['creation_control_stage'] == 'token'
        release.set(); await task
        assert 'Setup changed' in telegram.answers[-1][0]
        assert 'managed_username' not in state['data'] and create_bot._creation_state[5] is state
    run(go())


def test_disabled_or_stale_telegram_option_retains_manual_path(monkeypatch):
    async def go():
        session = use_botapi(responses={'GetMe': user_result(True)})
        q = FakeQuery('create_bot', uid=5); seen = {}
        async def edit(text=None, reply_markup=None, **kwargs): seen.update(text=text, kb=reply_markup)
        q.message.edit_text = edit
        await create_bot.create_bot_callback(FakeClient(), q)
        options = buttons(seen['kb']); telegram = next(k for k in options if k.endswith('_telegram'))
        manual = next(k for k in options if k.endswith('_manual'))
        session.responses['GetMe'] = user_result(False)
        tg = FakeQuery(telegram, uid=5, message=q.message)
        await create_bot.creation_control_callback(FakeClient(), tg)
        assert 'unavailable' in tg.answers[-1][0]
        await create_bot.creation_control_callback(FakeClient(), FakeQuery(manual, uid=5, message=q.message))
        assert 'Send your bot token' in seen['text']
    run(go())


def test_manual_choice_invalidates_old_buttons_and_keeps_private_input(monkeypatch):
    async def go():
        use_botapi(responses={'GetMe': user_result(True)})
        first = FakeQuery('create_bot', uid=5); seen = {}
        async def edit(text=None, reply_markup=None, **kwargs): seen.update(text=text, kb=reply_markup)
        first.message.edit_text = edit
        await create_bot.create_bot_callback(FakeClient(), first)
        options = buttons(seen['kb']); manual = next(k for k in options if k.endswith('_manual'))
        old_telegram = next(k for k in options if k.endswith('_telegram'))
        old_cancel = next(k for k in options if k.endswith('_cancel'))
        await create_bot.creation_control_callback(FakeClient(), FakeQuery(manual, uid=5, message=first.message))
        assert 'Send your bot token' in seen['text']
        state = create_bot._creation_state[5]
        for data in (old_telegram, old_cancel):
            q = FakeQuery(data, uid=5)
            await create_bot.creation_control_callback(FakeClient(), q)
            assert q.answers[-1][1] and create_bot._creation_state[5] is state
        current_cancel = next(k for k in buttons(seen['kb']) if k.endswith('_cancel'))
        await create_bot.creation_control_callback(FakeClient(), FakeQuery(current_cancel, uid=5))
        assert 5 not in create_bot._creation_state
    run(go())


def test_late_choice_edit_failure_does_not_discard_manual_choice(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        no_botapi()
        async def blocked_edit(*args, **kwargs): entered.set(); await release.wait(); raise RuntimeError('failed')
        first = FakeQuery('create_bot', uid=5); first.message.edit_text = blocked_edit
        task = asyncio.create_task(create_bot.create_bot_callback(FakeClient(), first)); await entered.wait()
        state = create_bot._creation_state[5]; nonce = state['creation_control_nonce']
        manual = FakeQuery(f'clone_setup_{nonce}_manual', uid=5)
        await create_bot.creation_control_callback(FakeClient(), manual)
        release.set(); await task
        assert create_bot._creation_state[5] is state and state['creation_control_stage'] == 'token'
        assert 'Send your bot token' in manual.message.text
    run(go())

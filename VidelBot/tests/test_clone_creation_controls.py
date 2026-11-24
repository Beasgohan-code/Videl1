"""Offline tests for stage- and attempt-bound clone setup controls."""
import asyncio

import pytest
from aiogram import types as at
from pyrogram import enums
from tests.harness import FakeClient, FakeMsg, FakeQuery, no_botapi, reset_db, run, use_botapi
from filestore.main_bot.plugins import create_bot, managed_bots
from tests.test_clone_creation_profiles import setup_mode


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    from core import rich
    async def edit_rich(message, doc, reply_markup=None):
        return await message.edit_text(doc.classic(), reply_markup=reply_markup)
    monkeypatch.setattr(rich, "edit", edit_rich)
    run(reset_db()); no_botapi(); create_bot._creation_state.clear()
    yield
    for state in create_bot._creation_state.values():
        if state.get('profile_timer'): state['profile_timer'].cancel()
    no_botapi(); create_bot._creation_state.clear()


def control(state, action='cancel', uid=5, message=None):
    return FakeQuery(f"clone_setup_{state['creation_control_nonce']}_{action}", uid=uid, message=message)


def callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def test_manual_prompt_has_bound_telegram_and_cancel_controls():
    async def go():
        use_botapi(responses={'GetMe': at.User(id=123456, is_bot=True, first_name='Videl', can_manage_bots=True)})
        q = FakeQuery('create_bot'); captured = []
        async def edit(text, reply_markup=None, **kwargs): captured.append(reply_markup)
        q.message.edit_text = edit
        await create_bot.create_bot_callback(FakeClient(), q)
        state = create_bot._creation_state[5]; values = callbacks(captured[-1])
        assert control(state).data in values and control(state, 'telegram').data in values
        assert 'cancel_creation' not in values and 'managed_new' not in values
    run(go())


def test_stale_token_and_channel_controls_cannot_cancel_profile_or_new_wizard():
    async def go():
        c = FakeClient(); state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[5] = state
        token = create_bot._creation_cancel_kb(state, 'token').inline_keyboard[0][0].callback_data
        state['step'] = 'awaiting_channel'
        channel = create_bot._creation_cancel_kb(state, 'channel').inline_keyboard[0][0].callback_data
        state['step'] = 'awaiting_profile'
        profile = create_bot._creation_cancel_kb(state, 'profile').inline_keyboard[0][0].callback_data
        for data in (token, channel):
            q = FakeQuery(data); await create_bot.creation_control_callback(c, q)
            assert q.answers[-1][1] and create_bot._creation_state[5] is state
        replacement = {'step': 'awaiting_profile', 'data': {'registration_token': 'keep'}}
        create_bot._creation_state[5] = replacement
        create_bot._creation_cancel_kb(replacement, 'profile')
        q = FakeQuery(profile); await create_bot.creation_control_callback(c, q)
        assert q.answers[-1][1] and replacement['data']['registration_token'] == 'keep'
        q = control(replacement); await create_bot.creation_control_callback(c, q)
        assert 5 not in create_bot._creation_state and 'registration_token' not in replacement['data']
    run(go())


def test_real_mode_screen_cancel_clears_timer_and_secret(monkeypatch):
    # setup_mode uses the shared synthetic verification client.
    from tests.test_clone_registration_safety import FakeChannelClient
    monkeypatch.setattr(create_bot, 'Client', FakeChannelClient)
    msg, state = setup_mode()
    timer = state['profile_timer']
    q = control(state, uid=10, message=msg.sent[-1])
    run(create_bot.creation_control_callback(FakeClient(), q))
    assert timer.cancelled() and 10 not in create_bot._creation_state
    assert 'registration_token' not in state['data']


def test_old_cancel_and_telegram_buttons_fail_closed():
    async def go():
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[5] = state; create_bot._creation_cancel_kb(state, 'token')
        for fn, data in ((create_bot.cancel_creation_callback, 'cancel_creation'), (managed_bots.managed_new, 'managed_new')):
            q = FakeQuery(data); await fn(FakeClient(), q)
            assert q.answers[-1][1] and 'expired' in q.answers[-1][0]
            assert create_bot._creation_state[5] is state
    run(go())


def test_controls_require_correct_private_user_and_block_registration():
    async def go():
        state = {'step': 'awaiting_profile', 'data': {}}
        create_bot._creation_state[5] = state; create_bot._creation_cancel_kb(state, 'profile')
        for q in (control(state, uid=6), control(state, message=FakeMsg(uid=5, chat_id=6)),
                  control(state, message=FakeMsg(uid=5, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100))):
            await create_bot.creation_control_callback(FakeClient(), q)
            assert q.answers[-1][1] and create_bot._creation_state[5] is state
        state['step'] = 'registering'; q = control(state)
        await create_bot.creation_control_callback(FakeClient(), q)
        assert q.answers[-1][1] and create_bot._creation_state[5] is state
    run(go())


def test_old_token_prompt_cannot_control_managed_attempt(monkeypatch):
    async def go():
        import config
        monkeypatch.setattr(config, 'BOT_TOKEN', '123456:token')
        monkeypatch.setattr(managed_bots, '_spawn', lambda coro: coro.close())
        async def yes(): return True
        monkeypatch.setattr(managed_bots, 'available', yes)
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[5] = state; create_bot._creation_cancel_kb(state, 'token')
        old_cancel, start = control(state), control(state, 'telegram')
        await create_bot.creation_control_callback(FakeClient(), start)
        assert 'managed_controls_nonce' in state['data']
        await create_bot.creation_control_callback(FakeClient(), old_cancel)
        assert old_cancel.answers[-1][1] and create_bot._creation_state[5] is state
        q = FakeQuery('managed_action_' + state['data']['managed_controls_nonce'] + '_cancel')
        await managed_bots.managed_setup_action(FakeClient(), q)
        assert 5 not in create_bot._creation_state
    run(go())


def test_stale_capability_response_does_not_open_managed_attempt(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(): entered.set(); await release.wait(); return True
        monkeypatch.setattr(managed_bots, 'available', slow)
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[5] = state; create_bot._creation_cancel_kb(state, 'token')
        start = control(state, 'telegram')
        task = asyncio.create_task(create_bot.creation_control_callback(FakeClient(), start)); await entered.wait()
        await create_bot.creation_control_callback(FakeClient(), control(state))
        release.set(); await task
        assert 5 not in create_bot._creation_state and 'managed_username' not in state['data']
    run(go())

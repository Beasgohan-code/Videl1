"""Offline tests for creator-bearing managed updates and one-use confirmation."""
import asyncio
import time
from types import SimpleNamespace

import pytest
from pyrogram import enums, raw, types
from tests.harness import FakeClient, FakeMsg, FakeQuery, no_botapi, reset_db, run, use_botapi
from filestore.main_bot.plugins import create_bot, managed_bots


@pytest.fixture(autouse=True)
def reset():
    run(reset_db()); no_botapi(); create_bot._creation_state.clear()
    yield
    no_botapi(); create_bot._creation_state.clear()


def pending(uid=5):
    st = {'step': 'awaiting_token', 'data': {'managed_username': 'suggested_bot',
          'managed_pair_deadline': time.monotonic() + 60, 'managed_controls_nonce': 'controls123'}}
    create_bot._creation_state[uid] = st
    return st


def update(uid=5, bot_id=424242, username='renamed_bot'):
    return SimpleNamespace(user=SimpleNamespace(id=uid, is_bot=None),
                           bot=SimpleNamespace(id=bot_id, is_bot=True, username=username))


def confirm(st, uid=5, message=None):
    return FakeQuery('managed_confirm_' + st['data']['managed_candidate']['nonce'], uid=uid, message=message)


def test_raw_wzgram_nonbot_flag_and_confirmation_before_token(monkeypatch):
    async def go():
        # WzGram forwards absent MTProto bot flags as None, not False.
        person = types.User._parse(None, raw.types.User(id=5, first_name='User', usernames=[], restriction_reason=[]))
        bot = types.User._parse(None, raw.types.User(id=424242, first_name='Bot', bot=True, username='renamed_bot', usernames=[], restriction_reason=[]))
        st, c = pending(), FakeClient()
        session = use_botapi(responses={'GetManagedBotToken': '424242:secret-long-enough'})
        async def validate(tok): return {'id': 424242, 'username': 'renamed_bot', 'first_name': 'Bot'}
        monkeypatch.setattr(create_bot, 'validate_bot_token', validate)
        await managed_bots.on_managed_bot_updated(c, types.ManagedBotUpdated(user=person, bot=bot))
        assert session.names().count('GetManagedBotToken') == 0
        assert st['step'] == 'awaiting_token' and st['data']['managed_candidate']['bot_id'] == 424242
        q = confirm(st)
        await managed_bots.confirm_managed_bot(c, q)
        assert session.names().count('GetManagedBotToken') == 1
        assert st['step'] == 'awaiting_channel' and st['data']['managed']
        await managed_bots.confirm_managed_bot(c, q)
        assert session.names().count('GetManagedBotToken') == 1
    run(go())


def test_username_and_timeout_never_retrieve_token_or_poll(monkeypatch):
    async def fail(*args, **kwargs): raise AssertionError('username is not proof')
    monkeypatch.setattr(managed_bots, '_token_for', fail)
    st = pending(); msg = FakeMsg('@someone_else_bot')
    assert run(managed_bots.claim_username(FakeClient(), msg, 5, msg.text))
    assert 'username alone' in msg.replies[0]
    assert run(managed_bots.watch_username(FakeClient(), 5, 'suggested_bot', FakeMsg(), timeout=0)) is False
    assert 'managed_username' not in st['data'] and st['step'] == 'awaiting_token'


def test_reject_wrong_creator_invalid_expired_and_duplicate_updates():
    async def go():
        st, c = pending(), FakeClient()
        for upd in (update(uid=6), update(bot_id=0), SimpleNamespace(user=SimpleNamespace(id=5, is_bot=True), bot=update().bot)):
            await managed_bots.on_managed_bot_updated(c, upd)
        assert 'managed_candidate' not in st['data']
        await managed_bots.on_managed_bot_updated(c, update())
        candidate = st['data']['managed_candidate']
        await managed_bots.on_managed_bot_updated(c, update(bot_id=999999))
        assert st['data']['managed_candidate'] is candidate
        q = confirm(st); candidate['deadline'] = time.monotonic() - 1
        await managed_bots.confirm_managed_bot(c, q)
        assert 'expired' in q.answers[-1][0] and st['step'] == 'awaiting_token'
        st = pending(); st['data']['managed_pair_deadline'] = time.monotonic() - 1
        await managed_bots.on_managed_bot_updated(c, update())
        assert 'managed_candidate' not in st['data']
    run(go())


def test_private_user_confirmation_required(monkeypatch):
    async def fail(*a, **kw): raise AssertionError('wrong user must not fetch')
    monkeypatch.setattr(managed_bots, '_token_for', fail)
    async def go():
        st = pending(); await managed_bots.on_managed_bot_updated(FakeClient(), update())
        nonce = st['data']['managed_candidate']['nonce']
        for q in (FakeQuery('managed_confirm_'+nonce, uid=6),
                  FakeQuery('managed_confirm_'+nonce, uid=5, message=FakeMsg(uid=5, chat_type=enums.ChatType.SUPERGROUP, chat_id=-10)),
                  FakeQuery('managed_confirm_wrong', uid=5)):
            await managed_bots.confirm_managed_bot(FakeClient(), q)
            assert q.answers[-1][1]
        assert st['data']['managed_candidate']['nonce'] == nonce
    run(go())


def test_cancel_and_double_click_during_token_fetch(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event(); calls = []
        async def token(*a, **kw): calls.append(1); entered.set(); await release.wait(); return '424242:secret-long-enough'
        monkeypatch.setattr(managed_bots, '_token_for', token)
        st = pending(); c = FakeClient(); await managed_bots.on_managed_bot_updated(c, update()); q = confirm(st)
        task = asyncio.create_task(managed_bots.confirm_managed_bot(c, q)); await entered.wait()
        await managed_bots.confirm_managed_bot(c, q)
        assert len(calls) == 1 and st['step'] == 'verifying_managed'
        await managed_bots.managed_setup_action(c, action(st))
        newer = pending(); release.set(); await task
        assert create_bot._creation_state[5] is newer and newer['step'] == 'awaiting_token' and 'token' not in newer['data']
    run(go())


def test_incorrect_token_identity_never_accepted(monkeypatch):
    async def go():
        st = pending(); c = FakeClient(); await managed_bots.on_managed_bot_updated(c, update())
        async def token(*a, **kw): return '123456:some-secret-token'
        monkeypatch.setattr(managed_bots, '_token_for', token)
        q = confirm(st); await managed_bots.confirm_managed_bot(c, q)
        assert st['step'] == 'awaiting_token' and 'token' not in st['data']
        assert 'could not be confirmed' in q.message.text
        async def validate(tok): return {'id': 777, 'username': 'wrong_bot', 'first_name': 'Wrong'}
        monkeypatch.setattr(create_bot, 'validate_bot_token', validate)
        assert not await create_bot.accept_token(c, 5, '424242:some-secret-token', q.message, managed=True, expected_state=st, expected_bot_id=424242)
        assert st['step'] == 'awaiting_token' and 'token' not in st['data']
    run(go())


def test_replacement_or_failed_delivery_invalidates_confirmation(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        c = FakeClient(); st = pending()
        async def delayed(*args, **kwargs): entered.set(); await release.wait()
        c.send_message = delayed
        task = asyncio.create_task(managed_bots.on_managed_bot_updated(c, update()))
        await entered.wait(); q = confirm(st)
        newer = pending(); release.set(); await task
        await managed_bots.confirm_managed_bot(c, q)
        assert q.answers[-1][1] and create_bot._creation_state[5] is newer
        async def fail(*args, **kwargs): raise RuntimeError('private delivery failed')
        c.send_message = fail
        await managed_bots.on_managed_bot_updated(c, update())
        assert 'managed_candidate' not in newer['data']
    run(go())


def test_new_attempt_invalidates_old_candidate(monkeypatch):
    async def go():
        import config
        st, c = pending(), FakeClient()
        await managed_bots.on_managed_bot_updated(c, update()); old = confirm(st)
        monkeypatch.setattr(config, 'BOT_TOKEN', '123456:test')
        def discard(coro): coro.close()
        monkeypatch.setattr(managed_bots, '_spawn', discard)
        await managed_bots.managed_new(c, FakeQuery('managed_new'), capability_checked=True)
        await managed_bots.confirm_managed_bot(c, old)
        assert old.answers[-1][1] and 'managed_candidate' not in st['data']
        assert st['step'] == 'awaiting_token'
    run(go())


def test_managed_retrieval_error_is_generic_and_releases_claim(monkeypatch):
    async def go():
        async def fail(*args, **kwargs): raise RuntimeError('secret synthetic value')
        monkeypatch.setattr(managed_bots, '_token_for', fail)
        st, c = pending(), FakeClient(); await managed_bots.on_managed_bot_updated(c, update()); q = confirm(st)
        await managed_bots.confirm_managed_bot(c, q)
        assert st['step'] == 'awaiting_token' and 'secret' not in q.message.text
        assert 'managed_confirming' not in st['data']
    run(go())


def test_manual_input_is_blocked_while_managed_fetch_in_flight(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args, **kw): entered.set(); await release.wait(); return None
        async def validate(*args): raise AssertionError('manual token must not race managed retrieval')
        monkeypatch.setattr(managed_bots, '_token_for', delayed)
        monkeypatch.setattr(create_bot, 'validate_bot_token', validate)
        st, c = pending(), FakeClient(); await managed_bots.on_managed_bot_updated(c, update())
        task = asyncio.create_task(managed_bots.confirm_managed_bot(c, confirm(st))); await entered.wait()
        msg = FakeMsg('123456:manual-token-long-enough')
        await create_bot.handle_creation_input(c, msg)
        assert 'already being processed' in msg.replies[0]
        release.set(); await task
        assert st['step'] == 'awaiting_token'
    run(go())


def action(st, choice='cancel', uid=5, message=None):
    return FakeQuery(f"managed_action_{st['data']['managed_controls_nonce']}_{choice}", uid=uid, message=message)


def test_old_controls_do_not_cancel_or_replace_new_attempt():
    async def go():
        old = pending(); cancel = action(old); manual = action(old, 'manual')
        newer = pending(); newer['data']['managed_controls_nonce'] = 'new_controls'
        for q in (cancel, manual):
            await managed_bots.managed_setup_action(FakeClient(), q)
            assert q.answers[-1][1] and 'changed' in q.answers[-1][0]
            assert create_bot._creation_state[5] is newer and newer['step'] == 'awaiting_token'
        # Old controls cannot cancel an already verified setup either.
        newer['step'] = 'awaiting_channel'
        q = action(newer); await managed_bots.managed_setup_action(FakeClient(), q)
        assert q.answers[-1][1] and create_bot._creation_state[5] is newer
    run(go())


def test_managed_controls_reject_other_user_or_nonprivate_chat():
    async def go():
        st = pending()
        for q in (action(st, uid=6), action(st, message=FakeMsg(uid=5, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)),
                  action(st, message=FakeMsg(uid=5, chat_id=6))):
            await managed_bots.managed_setup_action(FakeClient(), q)
            assert q.answers[-1][1] and create_bot._creation_state[5] is st
    run(go())


def test_cancel_during_callback_ack_prevents_token_request(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def token(*args, **kwargs): raise AssertionError('cancelled before token request')
        monkeypatch.setattr(managed_bots, '_token_for', token)
        st, client = pending(), FakeClient()
        await managed_bots.on_managed_bot_updated(client, update())
        q = confirm(st)
        async def slow_answer(*args, **kwargs): entered.set(); await release.wait()
        q.answer = slow_answer
        task = asyncio.create_task(managed_bots.confirm_managed_bot(client, q)); await entered.wait()
        assert st['step'] == 'verifying_managed'
        await managed_bots.managed_setup_action(client, action(st))
        assert 5 not in create_bot._creation_state
        release.set(); await task
        assert 5 not in create_bot._creation_state
    run(go())


def test_cancel_during_token_validation_prevents_wizard_advancement(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def token(*args, **kwargs): return '424242:secret-long-enough'
        async def validate(*args): entered.set(); await release.wait(); return {'id': 424242, 'username': 'test_bot', 'first_name': 'Test'}
        monkeypatch.setattr(managed_bots, '_token_for', token)
        monkeypatch.setattr(create_bot, 'validate_bot_token', validate)
        st, c = pending(), FakeClient(); await managed_bots.on_managed_bot_updated(c, update())
        task = asyncio.create_task(managed_bots.confirm_managed_bot(c, confirm(st)))
        await entered.wait(); assert st['step'] == 'verifying_token'
        await managed_bots.managed_setup_action(c, action(st)); release.set(); await task
        assert 5 not in create_bot._creation_state and 'token' not in st['data']
    run(go())


def test_manual_action_preserves_eligibility_checks(monkeypatch):
    st = pending()
    monkeypatch.setattr(create_bot, 'CLONE_ENABLED', False)
    q = action(st, 'manual')
    run(managed_bots.managed_setup_action(FakeClient(), q))
    assert 'disabled' in q.answers[-1][0] and create_bot._creation_state[5] is st


def test_scoped_controls_present_on_candidate_timeout_and_failure(monkeypatch):
    async def go():
        st, c = pending(), FakeClient(); sent = []
        async def capture(*args, **kwargs): sent.append(kwargs['reply_markup'])
        c.send_message = capture
        await managed_bots.on_managed_bot_updated(c, update())
        buttons = [b.callback_data for row in sent[0].inline_keyboard for b in row]
        assert action(st, 'manual').data in buttons and action(st).data in buttons
        async def fail(*args, **kwargs): return None
        monkeypatch.setattr(managed_bots, '_token_for', fail)
        q = confirm(st); edits = []
        async def edit(text, **kwargs): edits.append(kwargs.get('reply_markup'))
        q.message.edit_text = edit
        await managed_bots.confirm_managed_bot(c, q)
        buttons = [b.callback_data for row in edits[-1].inline_keyboard for b in row]
        assert action(st, 'manual').data in buttons and action(st).data in buttons
        st = pending(); edits.clear()
        status = FakeMsg(); status.edit_text = edit
        await managed_bots.watch_username(c, 5, 'suggested_bot', status, timeout=0)
        buttons = [b.callback_data for row in edits[-1].inline_keyboard for b in row]
        assert action(st, 'manual').data in buttons and action(st).data in buttons
    run(go())


def test_cancel_before_scheduled_token_task_runs(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        original_wait = asyncio.wait_for
        async def delayed_wait(coro, timeout):
            entered.set(); await release.wait()
            return await original_wait(coro, timeout)
        async def token(*args, **kwargs): raise AssertionError('cancelled before scheduled request')
        monkeypatch.setattr(asyncio, 'wait_for', delayed_wait)
        monkeypatch.setattr(managed_bots, '_token_for', token)
        st, c = pending(), FakeClient(); await managed_bots.on_managed_bot_updated(c, update())
        task = asyncio.create_task(managed_bots.confirm_managed_bot(c, confirm(st)))
        await entered.wait(); await managed_bots.managed_setup_action(c, action(st))
        release.set(); await task
        assert 5 not in create_bot._creation_state
    run(go())

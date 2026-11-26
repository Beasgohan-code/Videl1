"""Synthetic navigation checks for active clone setup/settings sessions."""
import asyncio

import pytest
from tests.harness import FakeClient, FakeMsg, FakeQuery, no_botapi, reset_db, run
from core import menus
from filestore.main_bot.plugins import create_bot, my_bots


@pytest.fixture(autouse=True)
def clean():
    run(reset_db()); no_botapi(); create_bot._creation_state.clear()
    yield
    for state in create_bot._creation_state.values():
        timer = state.get('profile_timer')
        if timer: timer.cancel()
    create_bot._creation_state.clear(); no_botapi()


@pytest.mark.parametrize('data', ['start_btn', 'back_menu', 'my_bots', 'dashboard_42', 'create_bot', 'clone_managed_start'])
@pytest.mark.parametrize('step', ['awaiting_token', 'awaiting_channel', 'awaiting_profile', 'awaiting_shortener_domain'])
def test_old_navigation_does_not_discard_active_flow(data, step):
    state = {'step': step, 'data': {'token': 'do-not-show', 'registration_token': 'retained'}}
    create_bot._creation_state[5] = state
    q = FakeQuery(data, uid=5)
    handlers = {'start_btn': menus.menu_callbacks, 'back_menu': menus.menu_callbacks,
                'my_bots': my_bots.my_bots_callback, 'dashboard_42': my_bots.dashboard_callback,
                'create_bot': create_bot.create_bot_callback, 'clone_managed_start': create_bot.create_managed_callback}
    run(handlers[data](FakeClient(), q))
    assert create_bot._creation_state[5] is state and state['data']['registration_token'] == 'retained'
    assert q.answers[-1][1] and 'in progress' in q.answers[-1][0] and 'do-not-show' not in str(q.answers)
    assert not q.message.edits


def test_cancel_then_navigation_works():
    create_bot._creation_state[5] = {'step': 'awaiting_token', 'data': {}}
    msg = FakeMsg('/cancel', uid=5)
    run(menus.cancel_cmd(FakeClient(), msg))
    assert 5 not in create_bot._creation_state and 'Cancelled' in msg.replies[-1]
    q = FakeQuery('my_bots', uid=5)
    run(my_bots.my_bots_callback(FakeClient(), q))
    assert q.message.edits and "No bots yet" in q.message.text


def test_my_bots_is_available_but_dashboard_is_blocked_during_registration():
    async def go():
        state = {'step': 'registering', 'data': {'registration_token': 'keep-private'}}
        create_bot._creation_state[5] = state
        await my_bots.main_db.add_bot(42, 5, 'enc', 'owned_bot', -100)
        myq = FakeQuery('my_bots', uid=5)
        await my_bots.my_bots_callback(FakeClient(), myq)
        dash = FakeQuery('dashboard_42', uid=5)
        await my_bots.dashboard_callback(FakeClient(), dash)
        assert myq.message.edits and not dash.message.edits
        assert 'Registration is in progress' in dash.answers[-1][0]
        assert create_bot._creation_state[5] is state and state['data']['registration_token'] == 'keep-private'
        assert 'keep-private' not in str(myq.message.edits) + str(dash.answers)
        denied = FakeQuery('dashboard_42', uid=6)
        await my_bots.dashboard_callback(FakeClient(), denied)
        assert 'access denied' in denied.answers[-1][0]
    run(go())


@pytest.mark.parametrize('target', ['my_bots', 'dashboard_42'])
def test_setup_started_while_navigation_lookup_waits_is_not_overwritten(monkeypatch, target):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args):
            entered.set(); await release.wait()
            return [] if target == 'my_bots' else {'_id': 42, 'owner_id': 5}
        if target == 'my_bots':
            monkeypatch.setattr(my_bots.main_db, 'get_user_bots', delayed)
            handler = my_bots.my_bots_callback
        else:
            monkeypatch.setattr(my_bots.main_db, 'get_bot', delayed)
            handler = my_bots.dashboard_callback
        q = FakeQuery(target, uid=5)
        task = asyncio.create_task(handler(FakeClient(), q)); await entered.wait()
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[5] = state
        release.set(); await task
        assert create_bot._creation_state[5] is state and not q.message.edits
        assert q.answers[-1][1] and 'in progress' in q.answers[-1][0]
    run(go())


def test_manual_managed_fallback_requires_current_attempt():
    async def go():
        prior = {'step': 'awaiting_token', 'data': {}}
        newer = {'step': 'awaiting_token', 'data': {'marker': True}}
        create_bot._creation_state[5] = newer
        q = FakeQuery('create_bot', uid=5)
        await create_bot._begin_creation(FakeClient(), q, manual=True, expected_state=prior)
        assert create_bot._creation_state[5] is newer and newer['data'] == {'marker': True}
        assert q.answers[-1][1] and 'changed' in q.answers[-1][0]
    run(go())


def test_old_my_bots_button_does_not_interrupt_inflight_registration(monkeypatch):
    from tests.test_clone_registration_safety import FakeChannelClient, wizard_state
    from tests.test_clone_creation_profiles import FakeEngine
    import filestore.worker_bot.engine as engine_module

    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(create_bot, 'Client', FakeChannelClient)
        worker = FakeEngine(); monkeypatch.setattr(engine_module, 'worker_engine', worker)
        wizard_state(uid=10, bot_id=42090)
        message = FakeMsg('-100001', uid=10)
        await create_bot.handle_creation_input(FakeClient(), message)
        state = create_bot._creation_state[10]
        original = create_bot.main_db.add_bot
        async def delayed(*args, **kwargs):
            entered.set(); await release.wait(); return await original(*args, **kwargs)
        monkeypatch.setattr(create_bot.main_db, 'add_bot', delayed)
        select = FakeQuery(f"clone_mode_{state['profile_nonce']}_filestore", uid=10, message=message.sent[-1])
        task = asyncio.create_task(create_bot.creation_profile_callback(FakeClient(), select))
        await entered.wait(); assert state['step'] == 'registering'
        old = FakeQuery('my_bots', uid=10)
        await my_bots.my_bots_callback(FakeClient(), old)
        assert old.message.edits and create_bot._creation_state[10] is state
        release.set(); await task
        row = await create_bot.main_db.get_bot(42090)
        assert row['owner_id'] == 10 and len(worker.started) == 1 and 10 not in create_bot._creation_state
    run(go())

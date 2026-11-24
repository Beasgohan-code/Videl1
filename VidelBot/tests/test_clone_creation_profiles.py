"""Synthetic channel-then-mode clone onboarding and failure boundaries."""
import asyncio
import re
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import FakeClient, FakeMsg, FakeQuery, reset_db, run
from tests.test_clone_registration_safety import FakeChannelClient, wizard_state
from filestore.database.main_db import BotRegistrationConflict, MainDB
from filestore.main_bot.plugins import create_bot
import filestore.worker_bot.engine as engine_module
import config


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    run(reset_db())
    create_bot._creation_state.clear()
    create_bot._verification_stops.clear()
    monkeypatch.setattr(create_bot, 'Client', FakeChannelClient)
    monkeypatch.setattr(config, 'CLONE_PROFILES_SINGLE_INSTANCE', False)
    yield
    for state in create_bot._creation_state.values():
        if state.get('profile_timer'): state['profile_timer'].cancel()
    create_bot._creation_state.clear()
    create_bot._verification_stops.clear()


class FakeEngine(engine_module.WorkerEngine):
    def __init__(self):
        super().__init__()
        self.started=[]
    async def start_worker(self, doc):
        self.started.append(dict(doc))
        self.workers[doc['_id']]=object()


def setup_mode(bot_id=42020, uid=10):
    wizard_state(uid=uid, bot_id=bot_id)
    msg=FakeMsg('-100001',uid=uid)
    run(create_bot.handle_creation_input(FakeClient(),msg))
    return msg, create_bot._creation_state[uid]


def query_for(msg, state, mode, uid=10):
    return FakeQuery(f"clone_mode_{state['profile_nonce']}_{mode}",uid=uid,message=msg.sent[-1])


def test_channel_verification_asks_for_mode_without_registration_or_start(monkeypatch):
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    msg,state=setup_mode()
    assert state['step']=='awaiting_profile' and 'token' not in state['data']
    assert 'selection expires in 5 minutes' in msg.sent[-1].text
    assert 'File links, storage and delivery' in msg.sent[-1].text and 'assistant account' in msg.sent[-1].text
    assert 'encrypted session' in msg.sent[-1].text
    assert run(MainDB().get_bot(42020)) is None and worker.started==[]
    assert 'synthetic:token-secret' not in msg.sent[-1].text


@pytest.mark.parametrize('mode', ['filestore','group'])
def test_selected_mode_is_atomically_saved_before_worker_start(monkeypatch, mode):
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    monkeypatch.setattr(config,'CLONE_PROFILES_SINGLE_INSTANCE',True)
    msg,state=setup_mode();q=query_for(msg,state,mode)
    run(create_bot.creation_profile_callback(FakeClient(),q))
    row=run(MainDB().get_bot(42020))
    assert row['clone_profile']==mode and worker.started[0]['clone_profile']==mode
    assert row['owner_id']==10 and 10 not in create_bot._creation_state
    assert ('File Store' if mode=='filestore' else 'Group Management') in msg.sent[-1].text
    assert 'synthetic:token-secret' not in msg.sent[-1].text
    run(create_bot.creation_profile_callback(FakeClient(),q))
    assert len(worker.started)==1 and 'expired' in q.answers[-1][0]


def test_disabled_group_does_not_register_or_change_the_choice(monkeypatch):
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    msg,state=setup_mode()
    q=query_for(msg,state,'group')
    run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 'disabled' in q.answers[-1][0] and state['step']=='awaiting_profile'
    assert run(MainDB().get_bot(42020)) is None and not worker.started
    q=query_for(msg,state,'filestore');run(create_bot.creation_profile_callback(FakeClient(),q))
    assert run(MainDB().get_bot(42020))['clone_profile']=='filestore'


def test_music_selection_registers_paused_pending_clone_without_starting_filestore_worker(monkeypatch):
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    captured=[]
    original=FakeMsg.edit_text
    async def capture_markup(self,text,*args,**kwargs):
        captured.append(kwargs.get('reply_markup'))
        return await original(self,text,*args,**kwargs)
    monkeypatch.setattr(FakeMsg,'edit_text',capture_markup)
    msg,state=setup_mode();q=query_for(msg,state,'music')
    run(create_bot.creation_profile_callback(FakeClient(),q))
    row=run(MainDB().get_bot(42020))
    assert row['clone_profile']=='filestore' and row['is_active'] is False
    assert row['music_setup_pending'] is True
    assert worker.started==[] and worker.get_worker(42020) is None
    assert 'Music' in msg.sent[-1].text and 'assistant session' in msg.sent[-1].text
    assert any(button.callback_data=='clmusic_42020'
               for markup in captured if markup
               for line in markup.inline_keyboard for button in line)


def test_callbacks_require_matching_private_owner_and_unique_nonce(monkeypatch):
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    msg,state=setup_mode()
    q=query_for(msg,state,'filestore',uid=20);q.message.chat.id=20
    run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 'expired' in q.answers[-1][0] and state['step']=='awaiting_profile'
    q=query_for(msg,state,'filestore');q.message.chat.id=-100;q.message.chat.type=enums.ChatType.SUPERGROUP
    run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 'private chat' in q.answers[-1][0]
    msg.sent[-1].chat.id=10;msg.sent[-1].chat.type=enums.ChatType.PRIVATE
    stale=FakeQuery('clone_mode_abcdefghijk_filestore',uid=10,message=msg.sent[-1])
    run(create_bot.creation_profile_callback(FakeClient(),stale))
    assert 'expired' in stale.answers[-1][0] and run(MainDB().get_bot(42020)) is None


def test_expired_selection_destroys_saved_credential_and_rejects_old_button(monkeypatch):
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    msg,state=setup_mode();q=query_for(msg,state,'filestore')
    state['profile_deadline']=0
    run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 'expired' in q.answers[-1][0] and 10 not in create_bot._creation_state
    assert 'registration_token' not in state['data'] and not worker.started


def test_cancel_clears_credential_and_stale_button_is_rejected(monkeypatch):
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    msg,state=setup_mode();q=query_for(msg,state,'filestore')
    cancel=FakeQuery('cancel_creation',uid=10,message=msg.sent[-1])
    run(create_bot._cancel_current_creation(FakeClient(),cancel,create_bot._creation_state.get(10)))
    assert 10 not in create_bot._creation_state and 'registration_token' not in state['data']
    run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 'expired' in q.answers[-1][0] and run(MainDB().get_bot(42020)) is None


def test_different_flow_rejects_old_nonce_and_old_expiry_does_not_clear_new(monkeypatch):
    msg,old=setup_mode();q=query_for(msg,old,'filestore');old_nonce=old['profile_nonce']
    msg2,new=setup_mode(bot_id=42021)
    assert old_nonce != new['profile_nonce']
    create_bot._expire_profile_selection(10,old_nonce)
    run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 10 in create_bot._creation_state and create_bot._creation_state[10] is new
    assert 'expired' in q.answers[-1][0]
    old['profile_timer'].cancel()


def test_duplicate_clicks_are_serialized_before_registry_write(monkeypatch):
    async def go():
        worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
        wizard_state(bot_id=42022);msg=FakeMsg('-100001',uid=10)
        await create_bot.handle_creation_input(FakeClient(),msg)
        state=create_bot._creation_state[10]
        q1=query_for(msg,state,'filestore');q2=query_for(msg,state,'filestore')
        entered=asyncio.Event();release=asyncio.Event();original=create_bot.main_db.add_bot;calls=[]
        async def slow(*args,**kwargs):
            calls.append(1);entered.set();await release.wait();return await original(*args,**kwargs)
        monkeypatch.setattr(create_bot.main_db,'add_bot',slow)
        first=asyncio.create_task(create_bot.creation_profile_callback(FakeClient(),q1))
        await entered.wait()
        await create_bot.creation_profile_callback(FakeClient(),q2)
        assert 'expired' in q2.answers[-1][0] and calls==[1]
        release.set();await first
        assert len(worker.started)==1 and (await MainDB().get_bot(42022))['clone_profile']=='filestore'
    run(go())


def test_restore_keeps_mode_settings_and_detects_stale_profile(monkeypatch):
    async def verified(_):return {'id':42023,'username':'sample_bot','first_name':'Sample'}
    monkeypatch.setattr(create_bot,'validate_bot_token',verified)
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    monkeypatch.setattr(config,'CLONE_PROFILES_SINGLE_INSTANCE',True)
    async def setup():
        db=MainDB();await db.add_bot(42023,10,'old','sample_bot',-100,initial_profile='group')
        await db.bots.update_one({'_id':42023},{'$set':{'is_deleted':True,'is_active':False,'settings.custom_caption':'retained'}})
        await create_bot.accept_token(FakeClient(),10,'synthetic:token-secret',FakeMsg('status'))
        msg=FakeMsg('-100001',uid=10);await create_bot.handle_creation_input(FakeClient(),msg)
        return msg,create_bot._creation_state[10]
    msg,state=run(setup());assert 'Restoring saved profile' in msg.sent[-1].text and 'ɢʀᴏᴜᴘ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ' in msg.sent[-1].text
    invalid=query_for(msg,state,'filestore');run(create_bot.creation_profile_callback(FakeClient(),invalid))
    assert 'not available' in invalid.answers[-1][0]
    q=query_for(msg,state,'keep');run(create_bot.creation_profile_callback(FakeClient(),q))
    row=run(MainDB().get_bot(42023))
    assert row['clone_profile']=='group' and row['settings']['custom_caption']=='retained'
    assert row['is_deleted'] is False and worker.started[0]['clone_profile']=='group'
    # A profile changed after the owner saw the restore prompt must not be overwritten.
    run(MainDB().bots.update_one({'_id':42023},{'$set':{'is_deleted':True,'is_active':False}}))
    worker.workers.clear();run(create_bot.accept_token(FakeClient(),10,'synthetic:token-secret',FakeMsg('status')))
    m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m));state=create_bot._creation_state[10]
    run(MainDB().bots.update_one({'_id':42023},{'$set':{'clone_profile':'filestore'}}))
    q=query_for(m,state,'keep');run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 'saved record changed' in m.sent[-1].text
    row=run(MainDB().get_bot(42023));assert row['is_deleted'] and row['clone_profile']=='filestore'
    assert row['settings']['custom_caption']=='retained'


def test_restore_group_requires_single_instance_gate(monkeypatch):
    async def verified(_):return {'id':42024,'username':'sample_bot','first_name':'Sample'}
    monkeypatch.setattr(create_bot,'validate_bot_token',verified)
    db=MainDB();run(db.add_bot(42024,10,'old','sample_bot',-100,initial_profile='group'))
    run(db.bots.update_one({'_id':42024},{'$set':{'is_deleted':True,'is_active':False}}))
    run(create_bot.accept_token(FakeClient(),10,'synthetic:token-secret',FakeMsg('status')))
    msg=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),msg));state=create_bot._creation_state[10]
    q=query_for(msg,state,'keep');run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 'disabled' in q.answers[-1][0] and run(db.get_bot(42024))['is_deleted'] is True


def test_running_or_uncertain_worker_refuses_registration(monkeypatch):
    worker=FakeEngine();worker.workers[42020]=object();monkeypatch.setattr(engine_module,'worker_engine',worker)
    msg,state=setup_mode();q=query_for(msg,state,'filestore')
    run(create_bot.creation_profile_callback(FakeClient(),q))
    assert 'not attempted' in msg.sent[-1].text and run(MainDB().get_bot(42020)) is None


def test_uncertain_verification_stop_does_not_advance_or_register(monkeypatch):
    class BadStop(FakeChannelClient):
        async def stop(self,**kwargs):raise RuntimeError('secret')
    monkeypatch.setattr(create_bot,'Client',BadStop)
    wizard_state(bot_id=42025);m=FakeMsg('-100001',uid=10)
    run(create_bot.handle_creation_input(FakeClient(),m))
    assert 'stop could not be confirmed' in m.sent[-1].text and 'secret' not in m.sent[-1].text
    assert 10 not in create_bot._creation_state and 42025 in create_bot._verification_stops
    assert run(MainDB().get_bot(42025)) is None
    wizard_state(bot_id=42025);m2=FakeMsg('-100001',uid=10)
    run(create_bot.handle_creation_input(FakeClient(),m2))
    assert 'stop could not be confirmed' in m2.replies[-1]


def test_cancel_while_verifying_does_not_show_profile_or_register(monkeypatch):
    async def go():
        entered=asyncio.Event();release=asyncio.Event()
        class Slow(FakeChannelClient):
            async def get_chat(self,_):entered.set();await release.wait();return SimpleNamespace(title='Channel')
        monkeypatch.setattr(create_bot,'Client',Slow)
        wizard_state(bot_id=42026);m=FakeMsg('-100001',uid=10)
        task=asyncio.create_task(create_bot.handle_creation_input(FakeClient(),m))
        await entered.wait()
        q=FakeQuery('cancel_creation',uid=10);await create_bot._cancel_current_creation(FakeClient(),q,create_bot._creation_state.get(10))
        release.set();await task
        assert 10 not in create_bot._creation_state and 'Choose a bot mode' not in m.sent[-1].text
        assert await MainDB().get_bot(42026) is None
    run(go())


def test_add_bot_validates_pending_music_setup_and_restore_mode():
    async def go():
        db=MainDB()
        with pytest.raises(ValueError):await db.add_bot(42027,10,'enc','bot',-100,initial_profile='music')
        with pytest.raises(ValueError):await db.add_bot(42027,10,'enc','bot',-100,
                                                           music_setup_pending=True,initial_active=True)
        with pytest.raises(ValueError):await db.add_bot(42027,10,'enc','bot',-100,
                                                           restore_deleted=True,music_setup_pending=True)
        await db.add_bot(42027,10,'enc','bot',-100,initial_profile='filestore',
                         initial_active=False,music_setup_pending=True)
        row=await db.get_bot(42027)
        assert row['music_setup_pending'] is True and row['is_active'] is False
        assert row['clone_profile']=='filestore'
        with pytest.raises(ValueError):await db.add_bot(42028,10,'enc','bot',-100,
                                                           restore_deleted=True,initial_profile='group')
    run(go())


def test_duplicate_channel_inputs_cannot_create_competing_verifiers(monkeypatch):
    async def go():
        remove_entered=asyncio.Event();allow_remove=asyncio.Event()
        verify_entered=asyncio.Event();allow_verify=asyncio.Event();created=[]
        async def slow_remove(*args,**kwargs):
            remove_entered.set();await allow_remove.wait()
        class Recording(FakeChannelClient):
            def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);created.append(self)
            async def get_chat(self,_):verify_entered.set();await allow_verify.wait();return SimpleNamespace(title='Channel')
        monkeypatch.setattr(create_bot,'remove_channel_picker',slow_remove)
        monkeypatch.setattr(create_bot,'Client',Recording)
        wizard_state(bot_id=42028);m1=FakeMsg('-100001',uid=10);m2=FakeMsg('-100001',uid=10)
        one=asyncio.create_task(create_bot.handle_creation_input(FakeClient(),m1))
        await remove_entered.wait()
        two=asyncio.create_task(create_bot.handle_creation_input(FakeClient(),m2))
        allow_remove.set();await verify_entered.wait();await asyncio.sleep(0)
        assert len(created)==1
        allow_verify.set();await asyncio.gather(one,two)
        assert len(created)==1 and create_bot._creation_state[10]['step']=='awaiting_profile'
    run(go())


def test_unacknowledged_registry_insert_is_not_reported_as_confirmed(monkeypatch):
    class FakeCollection:
        async def insert_one(self,doc):return SimpleNamespace(acknowledged=False)
    async def go():
        db=MainDB();monkeypatch.setattr(db,'bots',FakeCollection())
        with pytest.raises(RuntimeError,match='not acknowledged'):
            await db.add_bot(42029,10,'enc','bot',-100,initial_profile='filestore')
    run(go())


def test_cancelled_registration_clears_token_without_retry_or_start(monkeypatch):
    async def go():
        worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
        wizard_state(bot_id=42030);msg=FakeMsg('-100001',uid=10)
        await create_bot.handle_creation_input(FakeClient(),msg);state=create_bot._creation_state[10]
        entered=asyncio.Event();calls=[];original=create_bot.main_db.add_bot
        async def committed_then_wait(*args,**kwargs):
            calls.append(1);await original(*args,**kwargs);entered.set();await asyncio.Event().wait()
        monkeypatch.setattr(create_bot.main_db,'add_bot',committed_then_wait)
        q=query_for(msg,state,'filestore')
        task=asyncio.create_task(create_bot.creation_profile_callback(FakeClient(),q))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert 10 not in create_bot._creation_state and 'registration_token' not in state['data']
        assert calls==[1] and not worker.started
        assert (await MainDB().get_bot(42030))['clone_profile']=='filestore'
    run(go())


def test_cancelled_channel_validation_stops_client_and_clears_credentials(monkeypatch):
    async def go():
        entered=asyncio.Event();clients=[]
        class Slow(FakeChannelClient):
            def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);clients.append(self)
            async def get_chat(self,_):entered.set();await asyncio.Event().wait()
        monkeypatch.setattr(create_bot,'Client',Slow)
        wizard_state(bot_id=42031);state=create_bot._creation_state[10];m=FakeMsg('-100001',uid=10)
        task=asyncio.create_task(create_bot.handle_creation_input(FakeClient(),m))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert 10 not in create_bot._creation_state and 'token' not in state['data']
        assert not clients[0].is_connected and 42031 not in create_bot._verification_stops
        assert await MainDB().get_bot(42031) is None
    run(go())


def test_mode_keyboard_exposes_choices_without_credentials_and_with_unique_nonce(monkeypatch):
    from core import rich
    captured={}
    async def capture(message,doc,reply_markup=None):captured.update(doc=doc,markup=reply_markup)
    monkeypatch.setattr(rich,'edit',capture)
    async def go():
        state={'step':'verifying_channel','data':{'registration_token':'synthetic:token-secret'}}
        create_bot._creation_state[10]=state;msg=FakeMsg(uid=10)
        await create_bot._show_creation_profiles(msg,10,state)
        rows=captured['markup'].inline_keyboard
        buttons=[button for row in rows for button in row]
        assert captured['doc'].title=='Choose a bot mode'
        assert [b.text for b in buttons]==['📁 File Store','🛡 Group Management · disabled','🎵 Music','❌ Cancel setup']
        assert all('synthetic:token-secret' not in (b.callback_data or '') for b in buttons)
        assert all(len((b.callback_data or '').encode())<=64 for b in buttons)
        assert all(state['profile_nonce'] in b.callback_data for b in buttons[:3])
        assert rich.markup_ok(captured['markup'])
    run(go())

def test_native_channel_picker_also_leads_to_mode_choice(monkeypatch):
    worker=FakeEngine();monkeypatch.setattr(engine_module,'worker_engine',worker)
    wizard_state(bot_id=42032)
    msg=FakeMsg(text=None,uid=10)
    msg.chat_shared=SimpleNamespace(chat=SimpleNamespace(id=-100111,title='Chosen channel'))
    run(create_bot.handle_creation_input(FakeClient(),msg))
    assert create_bot._creation_state[10]['step']=='awaiting_profile'
    assert create_bot._creation_state[10]['data']['channel_id']==-100111
    assert 'selection expires in 5 minutes' in msg.sent[-1].text and run(MainDB().get_bot(42032)) is None


def test_cancel_while_status_message_sends_does_not_start_client(monkeypatch):
    async def go():
        entered=asyncio.Event();release=asyncio.Event();clients=[]
        class Recording(FakeChannelClient):
            def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);clients.append(self)
        class SlowMessage(FakeMsg):
            async def reply(self,*args,**kwargs):
                entered.set();await release.wait();return await super().reply(*args,**kwargs)
        monkeypatch.setattr(create_bot,'Client',Recording)
        wizard_state(bot_id=42033);state=create_bot._creation_state[10];msg=SlowMessage('-100001',uid=10)
        task=asyncio.create_task(create_bot.handle_creation_input(FakeClient(),msg))
        await entered.wait();q=FakeQuery('cancel_creation',uid=10)
        await create_bot._cancel_current_creation(FakeClient(),q,create_bot._creation_state.get(10))
        release.set();await task
        assert not clients and 10 not in create_bot._creation_state and 'token' not in state['data']
        assert await MainDB().get_bot(42033) is None
    run(go())


def test_cancel_during_token_validation_does_not_revive_flow(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def validate(_):
            entered.set(); await release.wait()
            return {'id': 42040, 'username': 'sample_bot', 'first_name': 'Sample'}
        monkeypatch.setattr(create_bot, 'validate_bot_token', validate)
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[10] = state
        status = FakeMsg('status')
        task = asyncio.create_task(create_bot.accept_token(FakeClient(), 10, 'synthetic:secret', status, expected_state=state))
        await entered.wait()
        assert state['step'] == 'verifying_token'
        await create_bot._cancel_current_creation(FakeClient(), FakeQuery('cancel_creation', uid=10), create_bot._creation_state.get(10))
        release.set()
        assert not await task
        assert 10 not in create_bot._creation_state and 'token' not in state['data']
        assert 'secret' not in status.text
    run(go())


def test_duplicate_token_submissions_and_replaced_setup_are_isolated(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def validate(token):
            calls.append(token); entered.set(); await release.wait()
            return {'id': 42041, 'username': 'sample_bot', 'first_name': 'Sample'}
        monkeypatch.setattr(create_bot, 'validate_bot_token', validate)
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[10] = state
        first = asyncio.create_task(create_bot.accept_token(FakeClient(), 10, 'first:secret', FakeMsg('status'), expected_state=state))
        await entered.wait()
        assert not await create_bot.accept_token(FakeClient(), 10, 'second:secret', FakeMsg('status'), expected_state=state)
        new_state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[10] = new_state
        release.set()
        assert not await first and calls == ['first:secret']
        assert create_bot._creation_state[10] is new_state and new_state['data'] == {} and state['data'] == {}
    run(go())


def test_cancel_during_registry_lookup_does_not_store_token(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def validate(_): return {'id': 42042, 'username': 'sample_bot', 'first_name': 'Sample'}
        async def lookup(_): entered.set(); await release.wait(); return None
        monkeypatch.setattr(create_bot, 'validate_bot_token', validate)
        monkeypatch.setattr(create_bot.main_db, 'get_bot', lookup)
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[10] = state
        task = asyncio.create_task(create_bot.accept_token(FakeClient(), 10, 'synthetic:secret', FakeMsg('status'), expected_state=state))
        await entered.wait()
        await create_bot._cancel_current_creation(FakeClient(), FakeQuery('cancel_creation', uid=10), create_bot._creation_state.get(10))
        release.set()
        assert not await task and 'token' not in state['data'] and 10 not in create_bot._creation_state
    run(go())


def test_token_verification_error_is_sanitized_and_retryable(monkeypatch):
    async def fail(_): raise RuntimeError('synthetic:secret')
    monkeypatch.setattr(create_bot, 'validate_bot_token', fail)
    state = {'step': 'awaiting_token', 'data': {}}
    create_bot._creation_state[10] = state
    status = FakeMsg('status')
    assert not run(create_bot.accept_token(FakeClient(), 10, 'synthetic:secret', status, expected_state=state))
    assert state['step'] == 'awaiting_token' and not state['data']
    assert 'could not be confirmed' in status.text and 'secret' not in status.text




def test_cancel_while_token_status_reply_is_pending_does_not_recreate_flow(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        async def validate(_): raise AssertionError('cancelled setup must not validate')
        monkeypatch.setattr(create_bot, 'validate_bot_token', validate)
        state = {'step': 'awaiting_token', 'data': {}}
        create_bot._creation_state[10] = state
        message = FakeMsg('123456:synthetic-token-long-enough', uid=10)
        async def reply(*args, **kwargs):
            entered.set(); await release.wait(); return FakeMsg('status')
        message.reply = reply
        task = asyncio.create_task(create_bot.handle_creation_input(FakeClient(), message))
        await entered.wait()
        await create_bot._cancel_current_creation(FakeClient(), FakeQuery('cancel_creation', uid=10), create_bot._creation_state.get(10))
        release.set(); await task
        assert 10 not in create_bot._creation_state and not state['data']
    run(go())

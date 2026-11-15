"""Synthetic Telegram handlers and mock-Mongo clone registration safety."""
import asyncio
from types import SimpleNamespace
import pytest
from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run
load_all()
from filestore.database.main_db import BotRegistrationConflict, MainDB
from filestore.main_bot.plugins import create_bot, my_bots
import filestore.worker_bot.engine as engine_module

@pytest.fixture(autouse=True)
def clean():
    run(reset_db()); no_botapi(); create_bot._creation_state.clear()
    yield
    create_bot._creation_state.clear()


def test_insert_only_collisions_and_ownership():
    async def go():
        db=MainDB()
        async def attempt(owner):
            try:
                return await db.add_bot(42000,owner,f'secret-{owner}',f'bot{owner}',-100)
            except BotRegistrationConflict:
                return None
        one,two=await asyncio.gather(attempt(10),attempt(20))
        assert (one is None)!=(two is None)
        row=await db.get_bot(42000); assert row['owner_id'] in (10,20)
        before=dict(row)
        with pytest.raises(BotRegistrationConflict):
            await db.add_bot(42000,30,'secret-30','attacker',-101)
        assert await db.get_bot(42000)==before
        assert await db.bots.count_documents({'_id':42000})==1
    run(go())


def test_restore_is_explicit_atomic_and_preserves_legacy_fields():
    async def go():
        db=MainDB(); await db.add_bot(42001,10,'original','old_bot',-100,managed=True)
        await db.bots.update_one({'_id':42001},{'$set':{'is_deleted':True,'is_active':False,'settings.custom_caption':'retained','shortener.api_key_encrypted':'retained-secret','legacy_field':{'keep':True}}})
        before=await db.get_bot(42001)
        with pytest.raises(BotRegistrationConflict): await db.add_bot(42001,20,'new','other_bot',-101,restore_deleted=True,expected_old_token='original')
        with pytest.raises(BotRegistrationConflict): await db.add_bot(42001,10,'new','other_bot',-101)
        assert await db.get_bot(42001)==before
        row=await db.add_bot(42001,10,'new-token','same_bot',-101,restore_deleted=True,expected_old_token='original')
        assert row['is_deleted'] is False and row['is_active'] is True and row['owner_id']==10
        assert row['settings']['custom_caption']=='retained' and row['shortener']['api_key_encrypted']=='retained-secret'
        assert row['legacy_field']=={'keep':True} and row['created_at']==before['created_at']
        assert row['managed'] is False and row['bot_token_encrypted']=='new-token' and row['log_channel_id']==-101
        with pytest.raises(BotRegistrationConflict): await db.add_bot(42001,10,'replacement','same_bot',-102,restore_deleted=True,expected_old_token='original')
        assert await db.get_bot(42001)==row
    run(go())


def test_restore_notice_and_managed_flag_are_consistent(monkeypatch):
    db=MainDB(); run(db.add_bot(42002,10,'token','sample_bot',-100))
    run(db.bots.update_one({'_id':42002},{'$set':{'is_deleted':True}}))
    async def verified(_): return {'id':42002,'username':'sample_bot','first_name':'Sample'}
    monkeypatch.setattr(create_bot,'validate_bot_token',verified)
    status=FakeMsg('status'); assert run(create_bot.accept_token(FakeClient(),10,'synthetic',status))
    assert create_bot._creation_state[10]['data']['restore_deleted'] is True
    assert 'Restoring a removed clone' in status.text and 'shortener' in status.text
    assert 'synthetic' not in status.text
    create_bot._creation_state[10]['data']['managed']=True
    assert run(create_bot.accept_token(FakeClient(),10,'synthetic',FakeMsg('status')))
    assert create_bot._creation_state[10]['data']['managed'] is False
    denied=FakeMsg('status'); assert not run(create_bot.accept_token(FakeClient(),20,'synthetic',denied))
    assert 'already registered' in denied.text and 20 not in create_bot._creation_state


class FakeChannelClient:
    def __init__(self,*args,**kwargs): self.is_connected=False
    async def start(self): self.is_connected=True
    async def stop(self,**kwargs): self.is_connected=False
    async def get_chat(self,_): return SimpleNamespace(title='Synthetic channel')
    async def send_message(self,**kwargs): return FakeMsg('test')


def wizard_state(uid=10, bot_id=42003):
    create_bot._creation_state[uid]={'step':'awaiting_channel','data':{'token':'synthetic:token-secret','bot_info':{'id':bot_id,'username':'sample_bot','first_name':'Sample'}}}


def test_wizard_collision_and_worker_status_are_honest(monkeypatch):
    monkeypatch.setattr(create_bot,'Client',FakeChannelClient)
    class Worker:
        def __init__(self): self.started=[]
        async def start_worker(self,doc): self.started.append(doc['_id'])  # returns without running
        def get_worker(self,_): return None
    worker=Worker(); monkeypatch.setattr(engine_module,'worker_engine',worker)
    db=MainDB();run(db.add_bot(42003,20,'original','existing',-100))
    wizard_state();m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m))
    assert 'already registered' in m.sent[-1].text and 10 not in create_bot._creation_state
    assert run(db.get_bot(42003))['owner_id']==20 and worker.started==[]
    wizard_state(bot_id=42004);m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m))
    assert 'Not observed running in this process' in m.sent[-1].text
    assert 'does not guarantee a running worker' in m.sent[-1].text and 10 not in create_bot._creation_state
    assert worker.started==[42004]


def test_wizard_cooldown_failure_reports_saved_without_starting(monkeypatch):
    monkeypatch.setattr(create_bot,'Client',FakeChannelClient)
    async def fail(_): raise RuntimeError('private database secret')
    monkeypatch.setattr(create_bot.main_db,'set_cooldown',fail)
    class Worker:
        async def start_worker(self,_): raise AssertionError('must not start')
    monkeypatch.setattr(engine_module,'worker_engine',Worker())
    wizard_state(bot_id=42005);m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m))
    assert 'Registration saved; this request did not attempt startup' in m.sent[-1].text
    assert 'private database secret' not in m.sent[-1].text and 10 not in create_bot._creation_state
    assert run(MainDB().get_bot(42005))['owner_id']==10


def test_channel_failure_does_not_show_token_or_exception(monkeypatch):
    class FailClient(FakeChannelClient):
        async def start(self): raise RuntimeError('https://api.telegram.org/botsynthetic:token-secret/getMe')
    monkeypatch.setattr(create_bot,'Client',FailClient)
    wizard_state(bot_id=42006);m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m))
    assert 'Verification failed' in m.sent[-1].text and 'synthetic:token-secret' not in m.sent[-1].text
    assert run(MainDB().get_bot(42006)) is None


def test_delete_confirmation_describes_retained_data():
    db=MainDB();run(db.add_bot(42007,10,'stored-token','sample_bot',-100))
    q=FakeQuery('confirm_delete_42007',uid=10);run(my_bots.confirm_delete_callback(FakeClient(),q))
    assert 'stored token, settings and associated data remain' in q.message.text
    assert 'revoke its token' in q.message.text and 'permanently delete' not in q.message.text
    assert run(db.get_bot(42007))['bot_token_encrypted']=='stored-token'


def test_competing_restores_cannot_both_replace_the_record():
    async def go():
        db=MainDB(); await db.add_bot(42008,10,'original','old',-100)
        await db.bots.update_one({'_id':42008},{'$set':{'is_deleted':True,'is_active':False}})
        async def restore(token):
            try: return await db.add_bot(42008,10,token,'saved',-101,restore_deleted=True,expected_old_token='original')
            except BotRegistrationConflict: return None
        one,two=await asyncio.gather(restore('first'),restore('second'))
        assert (one is None)!=(two is None)
        assert (await db.get_bot(42008))['bot_token_encrypted'] in ('first','second')
    run(go())


def test_ambiguous_registration_failure_clears_state_and_hides_exception(monkeypatch):
    monkeypatch.setattr(create_bot,'Client',FakeChannelClient)
    original=create_bot.main_db.add_bot
    async def wrote_then_failed(*args,**kwargs):
        await original(*args,**kwargs)
        raise RuntimeError('private synthetic:token-secret')
    monkeypatch.setattr(create_bot.main_db,'add_bot',wrote_then_failed)
    wizard_state(bot_id=42009);m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m))
    assert 'result may be uncertain' in m.sent[-1].text and 'synthetic:token-secret' not in m.sent[-1].text
    assert 10 not in create_bot._creation_state and run(MainDB().get_bot(42009))['owner_id']==10


def test_worker_start_wrapper_does_not_log_exception(monkeypatch):
    messages=[]
    monkeypatch.setattr(engine_module.log,'error',lambda msg: messages.append(msg))
    engine=engine_module.WorkerEngine()
    async def fail(_): raise RuntimeError('https://api.telegram.org/botsynthetic:token-secret/getMe')
    monkeypatch.setattr(engine,'start_worker',fail)
    run(engine._safe_start_worker({'_id':42010}))
    assert len(messages)==1 and 'synthetic:token-secret' not in messages[0]


def test_stale_restore_credential_snapshot_fails_without_changes(monkeypatch):
    async def go():
        db=MainDB();await db.add_bot(42011,10,'Original','bot',-100)
        await db.bots.update_one({'_id':42011},{'$set':{'is_deleted':True,'is_active':False,'bot_token_encrypted':'original'}})
        before=await db.get_bot(42011)
        calls=[]; original=db.bots.find_one_and_update
        async def observe(*args,**kwargs):
            calls.append(kwargs.get('collation'));return await original(*args,**kwargs)
        monkeypatch.setattr(db.bots,'find_one_and_update',observe)
        with pytest.raises(BotRegistrationConflict):
            await db.add_bot(42011,10,'replacement','bot',-101,restore_deleted=True,expected_old_token='Original')
        assert await db.get_bot(42011)==before
        assert calls and calls[0].document['locale']=='simple'
        with pytest.raises(BotRegistrationConflict):
            await db.add_bot(42011,10,'replacement','bot',-101,restore_deleted=True)
        assert await db.get_bot(42011)==before
    run(go())


def test_channel_client_constructor_failure_is_safe(monkeypatch):
    class FailClient:
        def __init__(self,*args,**kwargs): raise RuntimeError('private synthetic:token-secret')
    monkeypatch.setattr(create_bot,'Client',FailClient)
    wizard_state(bot_id=42012);m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m))
    assert 'Verification failed' in m.sent[-1].text and 'synthetic:token-secret' not in m.sent[-1].text
    assert run(MainDB().get_bot(42012)) is None


def test_wizard_stale_restore_does_not_replace_changed_credential(monkeypatch):
    db=MainDB();run(db.add_bot(42013,10,'old-token','sample_bot',-100))
    run(db.bots.update_one({'_id':42013},{'$set':{'is_deleted':True,'is_active':False,'settings.custom_caption':'retained'}}))
    async def verified(_): return {'id':42013,'username':'sample_bot','first_name':'Sample'}
    monkeypatch.setattr(create_bot,'validate_bot_token',verified)
    monkeypatch.setattr(create_bot,'Client',FakeChannelClient)
    assert run(create_bot.accept_token(FakeClient(),10,'synthetic:token-secret',FakeMsg('status')))
    run(db.bots.update_one({'_id':42013},{'$set':{'bot_token_encrypted':'changed-token'}}))
    before=run(db.get_bot(42013))
    m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m))
    assert 'saved record changed' in m.sent[-1].text and run(db.get_bot(42013))==before
    assert 10 not in create_bot._creation_state and 'changed-token' not in m.sent[-1].text


def test_wizard_restores_saved_settings_and_reports_process_local_status(monkeypatch):
    db=MainDB();run(db.add_bot(42014,10,'old-token','sample_bot',-100))
    run(db.bots.update_one({'_id':42014},{'$set':{'is_deleted':True,'is_active':False,'settings.custom_caption':'retained','shortener.api_key_encrypted':'secret'}}))
    async def verified(_): return {'id':42014,'username':'sample_bot','first_name':'Sample'}
    monkeypatch.setattr(create_bot,'validate_bot_token',verified)
    monkeypatch.setattr(create_bot,'Client',FakeChannelClient)
    class Worker:
        async def start_worker(self,doc): self.doc=doc
        def get_worker(self,bot_id): return object() if bot_id==42014 else None
    monkeypatch.setattr(engine_module,'worker_engine',Worker())
    status=FakeMsg('status');assert run(create_bot.accept_token(FakeClient(),10,'synthetic:token-secret',status))
    assert 'Restoring a removed clone' in status.text
    m=FakeMsg('-100001',uid=10);run(create_bot.handle_creation_input(FakeClient(),m))
    row=run(db.get_bot(42014))
    assert row['is_deleted'] is False and row['settings']['custom_caption']=='retained'
    assert row['shortener']['api_key_encrypted']=='secret'
    assert 'Clone restored' in m.sent[-1].text and 'Observed running in this process' in m.sent[-1].text
    assert 'which token a running worker uses' in m.sent[-1].text and 'synthetic:token-secret' not in m.sent[-1].text
    assert 10 not in create_bot._creation_state

"""Offline clone profile, owner and group-moderation contracts."""
import asyncio
import re
from types import SimpleNamespace

import pytest
from pyrogram import Client, enums

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run
from tests.test_audit import LOG_CH, Msg, WorkerClient, _dispatch

load_all()
import config
from filestore.database.main_db import MainDB
from filestore.database.worker_db import WorkerDB
from filestore.main_bot.plugins import clone_profiles
from filestore.worker_bot import engine as engine_module, group_profile


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    run(reset_db())
    monkeypatch.setattr(config, 'CLONE_PROFILES_SINGLE_INSTANCE', True)


async def bot(bot_id=4242, owner=5):
    return await MainDB().add_bot(bot_id, owner, 'enc', 'CloneBot', LOG_CH)


def test_select_is_opt_in_stopped_owner_only_and_preserves_data(monkeypatch):
    async def go():
        db=MainDB();eng=engine_module.WorkerEngine()
        await bot();await bot(5151,6)
        await WorkerDB(4242).add_admin(8)
        await db.update_setting(4242,'custom_caption','kept')
        assert await eng.select_profile(4242,8,'group')=='denied'
        assert await eng.select_profile(4242,5,'group')=='stop_first'
        await db.set_bot_active(4242,False)
        monkeypatch.setattr(config,'CLONE_PROFILES_SINGLE_INSTANCE',False)
        assert await eng.select_profile(4242,5,'group')=='disabled'
        monkeypatch.setattr(config,'CLONE_PROFILES_SINGLE_INSTANCE',True)
        assert await eng.select_profile(4242,5,'music')=='unavailable'
        assert await eng.select_profile(4242,5,'group')=='saved'
        doc=await db.get_bot(4242)
        assert doc['clone_profile']=='group' and doc['settings']['custom_caption']=='kept'
        assert (await db.get_bot(5151)).get('clone_profile') is None
        assert await WorkerDB(4242).admin_exist(8)
        assert await eng.select_profile(4242,5,'filestore')=='saved'
        assert (await db.get_bot(4242))['settings']['custom_caption']=='kept'
    run(go())


def test_profile_callback_requires_private_actual_owner_and_never_enables_music():
    async def go():
        db=MainDB();await bot();await db.set_bot_active(4242,False)
        async def invoke(uid, mode, group=False):
            q=FakeQuery(f'clprofile_set_4242_{mode}',uid=uid)
            q.matches=[re.match(r'^clprofile_set_(\d+)_(\w+)$',q.data)]
            if group: q.message.chat.type=enums.ChatType.SUPERGROUP;q.message.chat.id=-1001
            await clone_profiles.profile_select(FakeClient(),q)
            return q
        for uid,group in [(6,False),(111,False),(5,True)]:
            q=await invoke(uid,'group',group)
            assert any('denied' in (text or '').lower() for text,_ in q.answers)
        q=await invoke(5,'music');assert 'not available' in q.answers[0][0]
        assert 'clone_profile' not in await db.get_bot(4242)
    run(go())


def test_profile_write_compares_owner_and_stopped_state(monkeypatch):
    async def go():
        db=MainDB();eng=engine_module.WorkerEngine();await bot();await db.set_bot_active(4242,False)
        real=engine_module.main_db.bots.update_one
        async def transfer_first(query,update):
            await real({'_id':4242},{'$set':{'owner_id':6}})
            return await real(query,update)
        monkeypatch.setattr(engine_module.main_db.bots,'update_one',transfer_first)
        assert await eng.select_profile(4242,5,'group')=='changed'
        row=await db.get_bot(4242);assert row['owner_id']==6 and 'clone_profile' not in row
    run(go())


def test_stop_failure_blocks_switch_and_restart_until_resolved():
    async def go():
        db=MainDB();eng=engine_module.WorkerEngine();await bot();await db.set_bot_active(4242,False)
        class Flaky:
            is_connected=True
            async def stop(self,block=False): raise RuntimeError('secret stop diagnostic')
        eng.workers[4242]=Flaky();await eng.stop_worker(4242)
        assert await eng.select_profile(4242,5,'group')=='stop_first'
        with pytest.raises(RuntimeError,match='stop could not be confirmed'):
            await eng.start_worker(await db.get_bot(4242))
        assert 'clone_profile' not in await db.get_bot(4242)
    run(go())


def test_concurrent_start_and_mode_change_are_serialized_in_one_engine(monkeypatch):
    async def go():
        db=MainDB();eng=engine_module.WorkerEngine();await bot();await db.set_bot_active(4242,False)
        started=asyncio.Event();release=asyncio.Event()
        async def slow_start(doc):
            started.set();await release.wait();eng.workers[4242]=object()
        monkeypatch.setattr(eng,'_start_worker_locked',slow_start)
        task=asyncio.create_task(eng.start_worker(await db.get_bot(4242)));await started.wait()
        switching=asyncio.create_task(eng.select_profile(4242,5,'group'))
        await asyncio.sleep(0);assert not switching.done();release.set();await task
        assert await switching=='stop_first' and 'clone_profile' not in await db.get_bot(4242)
    run(go())


def test_start_fails_closed_on_missing_deleted_unknown_or_disabled_group(monkeypatch):
    async def go():
        db=MainDB();eng=engine_module.WorkerEngine()
        with pytest.raises(RuntimeError,match='unavailable'):await eng.start_worker({'_id':4242})
        await bot();await db.bots.update_one({'_id':4242},{'$set':{'clone_profile':'music'}})
        with pytest.raises(RuntimeError,match='Unsupported'):await eng.start_worker(await db.get_bot(4242))
        await db.bots.update_one({'_id':4242},{'$set':{'clone_profile':'group'}})
        monkeypatch.setattr(config,'CLONE_PROFILES_SINGLE_INSTANCE',False)
        with pytest.raises(RuntimeError,match='single worker'):await eng.start_worker(await db.get_bot(4242))
        await db.bots.update_one({'_id':4242},{'$set':{'is_deleted':True}})
        with pytest.raises(RuntimeError,match='unavailable'):await eng.start_worker({'_id':4242})
    run(go())


def test_group_runtime_is_exclusive_and_uses_fresh_profile(monkeypatch):
    async def noop(self,*a,**kw):return None
    async def get_me(self):return SimpleNamespace(id=4242,username='CloneBot')
    monkeypatch.setattr(Client,'start',noop);monkeypatch.setattr(Client,'set_bot_commands',noop)
    monkeypatch.setattr(Client,'get_me',get_me);monkeypatch.setattr(engine_module,'decrypt_token',lambda _: '4242:TOKEN')
    async def go():
        db=MainDB();eng=engine_module.WorkerEngine();stale=await bot();await db.set_bot_active(4242,False)
        assert await eng.select_profile(4242,5,'group')=='saved'
        await eng.start_worker(stale);app=eng.get_worker(4242);await asyncio.sleep(.05)
        c=WorkerClient({});m=Msg('/start something',uid=9,client=c)
        await _dispatch(app,c,m)
        assert 'Group Management' in m.replies[-1] and 'File Store commands' in m.replies[-1]
        assert not c.fetched
        m=Msg('/genlink',uid=5,client=c);await _dispatch(app,c,m)
        assert not m.replies
        eng.workers.pop(4242,None)
    run(go())


def member(status, can=True):
    return SimpleNamespace(status=status,privileges=SimpleNamespace(can_restrict_members=can))


class GroupClient:
    def __init__(self,actor=None,bot=None,target=None):
        self.me=SimpleNamespace(id=4242)
        self.members={5:actor or member(enums.ChatMemberStatus.ADMINISTRATOR),4242:bot or member(enums.ChatMemberStatus.ADMINISTRATOR),9:target or member(enums.ChatMemberStatus.MEMBER)}
        self.actions=[];self.fail_action=False
    async def get_chat_member(self,chat_id,uid):
        value=self.members[uid]
        if isinstance(value,Exception):raise value
        return value
    async def ban_chat_member(self,chat_id,uid):
        self.actions.append(('ban',chat_id,uid))
        if self.fail_action:raise RuntimeError('private token diagnostic')
    async def unban_chat_member(self,chat_id,uid):self.actions.append(('unban',chat_id,uid))


def group_msg(text='/gban 9',uid=5):
    return FakeMsg(text,uid=uid,chat_type=enums.ChatType.SUPERGROUP,chat_id=-100)


def test_group_ban_and_unban_use_live_telegram_rights():
    async def go():
        c=GroupClient();m=group_msg();await group_profile._moderate(c,m)
        assert c.actions==[('ban',-100,9)] and 'Banned' in m.replies[-1]
        c.members[9]=member(enums.ChatMemberStatus.BANNED);m=group_msg('/gunban 9')
        await group_profile._moderate(c,m,unban=True)
        assert c.actions[-1]==('unban',-100,9) and 'Unbanned' in m.replies[-1]
    run(go())


@pytest.mark.parametrize('case',['actor','bot','admin','owner','unknown','anonymous','self','private','missing','bot_actor'])
def test_group_moderation_fails_closed(case):
    async def go():
        c=GroupClient();m=group_msg()
        if case=='actor': c.members[5]=member(enums.ChatMemberStatus.MEMBER)
        if case=='bot': c.members[4242]=member(enums.ChatMemberStatus.ADMINISTRATOR,False)
        if case=='admin': c.members[9]=member(enums.ChatMemberStatus.ADMINISTRATOR)
        if case=='owner': c.members[9]=member(enums.ChatMemberStatus.OWNER)
        if case=='unknown': c.members[9]=RuntimeError('secret')
        if case=='anonymous': m.from_user=None;m.sender_chat=SimpleNamespace(id=-100)
        if case=='self': m.command=['gban','5']
        if case=='private': m.chat.type=enums.ChatType.PRIVATE
        if case=='missing': m.command=['gban']
        if case=='bot_actor': m.from_user.is_bot=True
        await group_profile._moderate(c,m)
        assert not c.actions and m.replies
        assert 'secret' not in m.replies[-1]
    run(go())


def test_group_action_failure_is_uncertain_and_not_retried(caplog):
    async def go():
        c=GroupClient();c.fail_action=True;m=group_msg();await group_profile._moderate(c,m)
        assert c.actions==[('ban',-100,9)] and 'did not confirm' in m.replies[-1]
        assert 'private token diagnostic' not in m.replies[-1] and 'private token diagnostic' not in caplog.text
    run(go())


def test_stop_waits_for_disconnect_before_permitting_profile_change():
    async def go():
        db=MainDB();eng=engine_module.WorkerEngine();await bot();await db.set_bot_active(4242,False)
        class Connected:
            is_connected=True
            is_initialized=True
            async def stop(self,block=False):
                assert block is True
                self.is_initialized=False;self.is_connected=False
        eng.workers[4242]=Connected()
        assert await eng.stop_worker(4242) is True
        assert await eng.select_profile(4242,5,'group')=='saved'
    run(go())


def test_lost_profile_write_response_does_not_repeat_or_echo_diagnostic(monkeypatch):
    async def go():
        db=MainDB();await bot();await db.set_bot_active(4242,False)
        calls=0;real=engine_module.main_db.bots.update_one
        async def lost_response(query,update):
            nonlocal calls
            calls+=1;await real(query,update);raise RuntimeError('private token diagnostic')
        monkeypatch.setattr(engine_module.main_db.bots,'update_one',lost_response)
        q=FakeQuery('clprofile_set_4242_group',uid=5)
        q.matches=[re.match(r'^clprofile_set_(\d+)_(\w+)$',q.data)]
        await clone_profiles.profile_select(FakeClient(),q)
        assert calls==1 and (await db.get_bot(4242))['clone_profile']=='group'
        assert 'Could not confirm' in q.answers[0][0] and 'diagnostic' not in q.answers[0][0]
    run(go())


def test_group_startup_failure_with_uncertain_stop_blocks_switch(monkeypatch, caplog):
    class BrokenClient:
        is_connected=False
        is_initialized=False
        def __init__(self,**kwargs):pass
        def add_handler(self,*args,**kwargs):pass
        def on_message(self,*args,**kwargs):return lambda f:f
        def on_callback_query(self,*args,**kwargs):return lambda f:f
        async def start(self):self.is_connected=True;self.is_initialized=True
        async def stop(self,block=False):raise RuntimeError('private token diagnostic')
        def set_parse_mode(self,mode):pass
    async def fail_menu(*args):raise RuntimeError('private token diagnostic')
    monkeypatch.setattr(engine_module,'Client',BrokenClient)
    monkeypatch.setattr(engine_module,'decrypt_token',lambda _: '4242:TOKEN')
    monkeypatch.setattr(group_profile,'set_group_commands',fail_menu)
    async def go():
        db=MainDB();eng=engine_module.WorkerEngine();await bot();await db.set_bot_active(4242,False)
        assert await eng.select_profile(4242,5,'group')=='saved'
        with pytest.raises(RuntimeError):await eng.start_worker(await db.get_bot(4242))
        assert await eng.select_profile(4242,5,'filestore')=='stop_first'
        assert not eng.get_worker(4242)
        assert 'private token diagnostic' not in caplog.text
    run(go())


def test_deleted_owner_cannot_select_profile():
    async def go():
        db=MainDB();await bot();await db.set_bot_active(4242,False)
        await db.bots.update_one({'_id':4242},{'$set':{'is_deleted':True}})
        q=FakeQuery('clprofile_set_4242_group',uid=5)
        q.matches=[re.match(r'^clprofile_set_(\d+)_(\w+)$',q.data)]
        await clone_profiles.profile_select(FakeClient(),q)
        assert 'denied' in q.answers[0][0].lower()
        assert 'clone_profile' not in await db.get_bot(4242)
    run(go())


def test_dashboard_toggle_does_not_claim_uncertain_stop_or_expose_start_error(monkeypatch,caplog):
    from filestore.main_bot.plugins import my_bots
    import core.ui
    messages=[];events=[]
    async def edit(message,text,reply_markup=None,**kwargs):messages.append(text)
    async def event(client,tag,user,bot,extra=''):events.append(extra)
    monkeypatch.setattr(core.ui,'smart_edit',edit)
    monkeypatch.setattr(my_bots,'_clone_log',event)
    eng=engine_module.WorkerEngine()
    monkeypatch.setattr(engine_module,'worker_engine',eng)
    async def go():
        db=MainDB();await bot()
        class Flaky:
            is_connected=True
            async def stop(self,block=True):raise RuntimeError('secret stop diagnostic')
        eng.workers[4242]=Flaky()
        q=FakeQuery('toggle_bot_4242',uid=5)
        await my_bots.toggle_bot_callback(FakeClient(),q)
        assert 'Stop could not be confirmed' in messages[-1]
        assert (await db.get_bot(4242))['is_active'] is True
        q=FakeQuery('toggle_bot_4242',uid=5)
        await my_bots.toggle_bot_callback(FakeClient(),q)
        assert 'Start could not be confirmed' in messages[-1]
        assert not any('secret' in entry for entry in messages+events)
        assert 'secret stop diagnostic' not in caplog.text
    run(go())


def test_decryption_failure_is_reported_without_leaking_error(monkeypatch, caplog):
    def fail(value):raise RuntimeError('secret credential diagnostic')
    monkeypatch.setattr(engine_module,'decrypt_token',fail)
    async def go():
        await bot();eng=engine_module.WorkerEngine()
        with pytest.raises(RuntimeError,match='credential unavailable') as error:
            await eng.start_worker(await MainDB().get_bot(4242))
        assert 'secret' not in str(error.value) and not eng.get_worker(4242)
        assert 'secret credential diagnostic' not in caplog.text
    run(go())

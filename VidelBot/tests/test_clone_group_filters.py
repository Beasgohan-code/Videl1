"""Fake Telegram/mock Mongo tests for isolated, bounded group text filters."""
import asyncio
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import reset_db, run
from tests.test_audit import _dispatch
from tests.test_clone_group_info import GroupClient, message
from tests.test_clone_group_moderation import application, admin, status
from filestore.database.mongo import get_db
from filestore.worker_bot import group_filters


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    group_filters._gates.clear()
    group_filters._inflight = 0
    group_filters._check_tokens = group_filters.CHECK_BURST
    group_filters._check_refill = None
    yield
    group_filters._gates.clear()
    group_filters._inflight = 0


async def dispatch(app, client, text, chat_id=-100, uid=5):
    msg=message(text,chat_id,uid)
    await _dispatch(app,client,msg)
    return msg


def test_settings_are_isolated_case_insensitive_and_safe(application, monkeypatch):
    async def go():
        clock=[100.0];monkeypatch.setattr(group_filters,'time',SimpleNamespace(monotonic=lambda:clock[0]))
        a,b=GroupClient(4242),GroupClient(5151)
        assert 'saved' in (await dispatch(application,a,'/filter Guide Read <rules> & more')).replies[-1]
        await dispatch(application,a,'.filter guide Other chat',-200)
        await dispatch(application,b,'/filter guide Other bot')
        assert '<code>guide</code>' in (await dispatch(application,a,'/filters',uid=9)).replies[-1]
        assert (await dispatch(application,a,'Please GUIDE!',uid=9)).replies[-1]=='Read &lt;rules&gt; &amp; more'
        assert (await dispatch(application,a,'guide',-200,9)).replies[-1]=='Other chat'
        assert (await dispatch(application,b,'guide',-100,9)).replies[-1]=='Other bot'
        docs=await get_db()[group_filters.COLLECTION].find({}).to_list(None)
        assert {(d['bot_id'],d['chat_id']) for d in docs} == {(4242,-100),(4242,-200),(5151,-100)}
        assert all(set(d)=={'_id','bot_id','chat_id','version','filters'} for d in docs)
        assert 'removed' in (await dispatch(application,a,'/stopfilter GUIDE')).replies[-1]
        clock[0]+=11
        assert not (await dispatch(application,a,'guide',uid=9)).replies
        assert (await dispatch(application,a,'/stopfilter guide')).replies[-1].endswith('not found in this group.')
    run(go())


@pytest.mark.parametrize('text', ['/filter name value', '/stopfilter name'])
def test_only_direct_admins_with_change_info_can_configure(application,text):
    async def go():
        for kind in ('member','no_right','anonymous','bot','failure'):
            c=GroupClient();m=message(text)
            if kind=='member':c.members[5]=status(enums.ChatMemberStatus.MEMBER)
            if kind=='no_right':c.members[5]=admin(can_change_info=False)
            if kind=='anonymous':m.from_user=None;m.sender_chat=SimpleNamespace(id=-100)
            if kind=='bot':m.from_user.is_bot=True
            if kind=='failure':c.members[5]=RuntimeError('private secret')
            await _dispatch(application,c,m)
            assert 'admin' in m.replies[-1].lower() and 'secret' not in m.replies[-1]
            assert await get_db()[group_filters.COLLECTION].count_documents({})==0
    run(go())


def test_input_validation_and_capacity(application):
    async def go():
        c=GroupClient()
        for text in ('/filter a.b value','/filter ü text','/filter x','/filter x '+ 'x'*1001,'/filter x '+ '"'*1000,'/stopfilter x more','/filters extra'):
            assert 'Usage' in (await dispatch(application,c,text)).replies[-1]
        for i in range(group_filters.MAX_FILTERS):
            assert 'saved' in (await dispatch(application,c,f'/filter n{i} value')).replies[-1]
        assert 'already has' in (await dispatch(application,c,'/filter extra value')).replies[-1]
        assert 'saved' in (await dispatch(application,c,'/filter n1 changed')).replies[-1]
        assert 'removed' in (await dispatch(application,c,'/stopfilter n1')).replies[-1]
        assert 'saved' in (await dispatch(application,c,'/filter extra value')).replies[-1]
    run(go())


def test_matching_word_boundaries_order_and_no_rich_injection():
    items={'help':'short','help_me':'long <b>text</b>','abc':'abc'}
    assert group_filters._match('HELP_ME and help',items)=='long <b>text</b>'
    assert group_filters._match('unhelpful',items) is None
    assert group_filters._match('éhelp',items) is None
    assert group_filters._match('(ABC)!',items)=='abc'


def test_auto_replies_are_throttled_bounded_and_do_not_process_commands_or_bots(application, monkeypatch):
    async def go():
        c=GroupClient();clock=[100.0];monkeypatch.setattr(group_filters,'time',SimpleNamespace(monotonic=lambda:clock[0]))
        await dispatch(application,c,'/filter hi Hello')
        one=await dispatch(application,c,'hi',uid=9);assert one.replies==['Hello']
        assert not (await dispatch(application,c,'hi',uid=9)).replies
        clock[0]+=10
        for value in ('/hi','.hi','!hi','#hi','x'*4097):
            assert not (await dispatch(application,c,value,uid=9)).replies
        bot=message('hi',user_id=9);bot.from_user.is_bot=True;await _dispatch(application,c,bot);assert not bot.replies
        sender=message('hi',user_id=9);sender.sender_chat=SimpleNamespace(id=-100);await _dispatch(application,c,sender);assert not sender.replies
        outgoing=message('hi',user_id=9);outgoing.outgoing=True;await _dispatch(application,c,outgoing);assert not outgoing.replies
        two=await dispatch(application,c,'hi',uid=9);assert two.replies==['Hello']
        clock[0]+=10
        await _dispatch(application,c,two);assert two.replies==['Hello']  # replay
        assert not (await dispatch(application,c,'unmatched',uid=9)).replies
        # even nonmatching lookups are rate-limited to avoid one DB read per message
        assert not (await dispatch(application,c,'hi',uid=9)).replies
        clock[0]+=1
        assert (await dispatch(application,c,'hi',uid=9)).replies==['Hello']
    run(go())


def test_busy_inflight_and_storage_failures_are_fail_closed(application,monkeypatch):
    async def go():
        c=GroupClient();clock=[100.0];monkeypatch.setattr(group_filters,'time',SimpleNamespace(monotonic=lambda:clock[0]))
        await dispatch(application,c,'/filter hi Hi')
        orig=group_filters._load;entered=asyncio.Event();release=asyncio.Event()
        async def blocked(scope):entered.set();await release.wait();return await orig(scope)
        monkeypatch.setattr(group_filters,'_load',blocked)
        m1=message('hi',user_id=9);m2=message('hi',user_id=9)
        task=asyncio.create_task(_dispatch(application,c,m1));await entered.wait()
        await _dispatch(application,c,m2);assert not m2.replies
        release.set();await task
        assert m1.replies==['Hi'] and group_filters._inflight==0
        clock[0]+=20
        async def failed(scope):raise RuntimeError('secret db')
        monkeypatch.setattr(group_filters,'_load',failed)
        m=await dispatch(application,c,'hi',uid=9);assert not m.replies and group_filters._inflight==0
        m=await dispatch(application,c,'/filters',uid=9);assert 'could not be loaded' in m.replies[-1] and 'secret' not in m.replies[-1]
    run(go())


def test_confirmed_cas_conflict_and_ambiguous_write(monkeypatch):
    async def go():
        scope={'_id':'4242:-100','bot_id':4242,'chat_id':-100}
        coll=get_db()[group_filters.COLLECTION]
        await coll.insert_one({**scope,'version':'0'*32,'filters':{'a':'old'}})
        original=coll.update_one;calls=[]
        async def race(query,update):
            calls.append(1)
            if len(calls)==1:await original(query,{'$set':{'version':'1'*32,'filters':{'a':'old','other':'saved'}}})
            return await original(query,update)
        monkeypatch.setattr(coll,'update_one',race);monkeypatch.setattr(group_filters,'get_db',lambda:{group_filters.COLLECTION:coll})
        assert await group_filters._change(scope,'a','new')=='saved' and len(calls)==2
        assert (await coll.find_one({'_id':scope['_id']}))['filters']=={'a':'new','other':'saved'}
        async def uncertain(*args,**kwargs):calls.append(1);raise RuntimeError('unknown')
        monkeypatch.setattr(coll,'update_one',uncertain)
        with pytest.raises(RuntimeError):await group_filters._change(scope,'a','x')
        assert len(calls)==3
    run(go())


def test_limiter_saturation_and_safe_eviction(monkeypatch):
    clock=[100.0];monkeypatch.setattr(group_filters,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(group_filters,'MAX_TRACKED_GROUPS',2)
    a={'bot_id':1,'chat_id':-1};b={'bot_id':1,'chat_id':-2};c={'bot_id':1,'chat_id':-3}
    first=group_filters._acquire(a,1);second=group_filters._acquire(b,1)
    assert group_filters._acquire(c,1) is None
    group_filters._release(first);group_filters._release(second)
    assert group_filters._acquire(c,2) is None
    clock[0]+=11
    third=group_filters._acquire(c,3);assert third is not None and len(group_filters._gates)==2
    group_filters._release(third)


def test_global_rate_budget_drops_excess_and_refills(monkeypatch):
    clock=[100.0];monkeypatch.setattr(group_filters,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    monkeypatch.setattr(group_filters,'CHECK_BURST',2.0)
    monkeypatch.setattr(group_filters,'MAX_CHECKS_PER_SECOND',1.0)
    group_filters._check_tokens=2.0
    for chat in (-1,-2):
        state=group_filters._acquire({'bot_id':1,'chat_id':chat},1)
        assert state is not None
        group_filters._release(state)
    assert group_filters._acquire({'bot_id':1,'chat_id':-3},1) is None
    clock[0]+=1
    state=group_filters._acquire({'bot_id':1,'chat_id':-3},2)
    assert state is not None
    group_filters._release(state)


def test_concurrent_final_slot_and_initial_duplicate_are_safe(monkeypatch):
    from pymongo.errors import DuplicateKeyError
    async def go():
        scope={'_id':'4242:-100','bot_id':4242,'chat_id':-100}
        coll=get_db()[group_filters.COLLECTION]
        await coll.insert_one({**scope,'version':'0'*32,'filters':{f'n{i}':'x' for i in range(group_filters.MAX_FILTERS-1)}})
        original=group_filters._load;count=0;ready=asyncio.Event()
        async def gated(s):
            nonlocal count
            result=await original(s);count+=1
            if count<=2:
                if count==2:ready.set()
                await ready.wait()
            return result
        monkeypatch.setattr(group_filters,'_load',gated)
        assert sorted(await asyncio.gather(group_filters._change(scope,'newa','A'),group_filters._change(scope,'newb','B')))==['full','saved']
        assert len((await coll.find_one({'_id':scope['_id']}))['filters'])==group_filters.MAX_FILTERS
        await coll.delete_many({});monkeypatch.setattr(group_filters,'_load',original)
        insert=coll.insert_one;attempts=[]
        async def race(doc):
            attempts.append(1)
            await insert({**scope,'version':'1'*32,'filters':{'other':'winner'}})
            raise DuplicateKeyError('duplicate')
        monkeypatch.setattr(coll,'insert_one',race)
        monkeypatch.setattr(group_filters,'get_db',lambda:{group_filters.COLLECTION:coll})
        assert await group_filters._change(scope,'mine','value')=='saved'
        assert (await coll.find_one({'_id':scope['_id']}))['filters']=={'mine':'value','other':'winner'}
        assert len(attempts)==1
    run(go())


def test_timeout_and_failed_reply_release_gate_and_do_not_retry(application,monkeypatch):
    async def go():
        c=GroupClient();clock=[100.0];monkeypatch.setattr(group_filters,'time',SimpleNamespace(monotonic=lambda:clock[0]))
        await dispatch(application,c,'/filter hi Hi')
        original=group_filters._load
        async def slow(scope):await asyncio.sleep(1);return await original(scope)
        monkeypatch.setattr(group_filters,'_load',slow);monkeypatch.setattr(group_filters,'READ_TIMEOUT',0.01)
        m=await dispatch(application,c,'hi',uid=9);assert not m.replies and group_filters._inflight==0
        clock[0]+=2;monkeypatch.setattr(group_filters,'_load',original)
        msg=message('hi',user_id=9);calls=[]
        async def fail(*args,**kwargs):calls.append(1);raise RuntimeError('secret')
        msg.reply_text=fail
        await _dispatch(application,c,msg)
        assert calls==[1] and group_filters._inflight==0
        clock[0]+=1;assert not (await dispatch(application,c,'hi',uid=9)).replies
    run(go())

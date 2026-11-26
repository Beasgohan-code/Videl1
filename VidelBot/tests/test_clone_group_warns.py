"""Manual warning counters: group/clone isolation, access and replay safety."""
import asyncio
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import reset_db, run
from tests.test_audit import _dispatch
from tests.test_clone_group_moderation import application, RecordingClient, admin, group, reply, status
from filestore.database.mongo import get_db
from filestore.worker_bot import group_warns


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())


async def dispatch(app, client, text, chat_id=-100, uid=5, replied=None):
    msg=group(text,uid=uid);msg.chat.id=chat_id;msg.reply_to_message=replied
    await _dispatch(app,client,msg)
    return msg


def test_manual_warns_isolated_and_reasons_are_not_stored(application):
    async def go():
        a=RecordingClient();b=RecordingClient();b.me.id=5151;b.members[5151]=admin()
        m=await dispatch(application,a,'/warn 9 <spam> & bad')
        assert 'Current count: <b>1</b>' in m.replies[-1] and '&lt;spam&gt; &amp; bad' in m.replies[-1]
        assert 'No automatic punishment' in m.replies[-1]
        await dispatch(application,a,'.warn 9 Other group',-200)
        await dispatch(application,b,'/warn 9 Other clone')
        m=await dispatch(application,a,'/warns',uid=9)
        assert '1' in m.replies[-1] and '9' in m.replies[-1]
        m=await dispatch(application,a,'/warns 9');assert '<b>1</b>' in m.replies[-1]
        m=await dispatch(application,a,'/warn 9 more');assert '<b>2</b>' in m.replies[-1]
        m=await dispatch(application,a,'/rmwarn 9');assert '<b>1</b>' in m.replies[-1]
        m=await dispatch(application,a,'/resetwarn 9');assert '<b>0</b>' in m.replies[-1]
        docs=await get_db()[group_warns.COLLECTION].find({}).to_list(None)
        assert {(d['bot_id'],d['chat_id'],d['user_id'],d['count']) for d in docs} == {(4242,-100,9,0),(4242,-200,9,1),(5151,-100,9,1)}
        assert all(set(d)=={'_id','bot_id','chat_id','user_id','version','last_message_id','count'} for d in docs)
        assert not any(item[0] in ('ban','restrict','unban') for item in a.calls+b.calls)
    run(go())


@pytest.mark.parametrize('action', ['/warn @someone bad', '/warns @someone', '/rmwarn @someone', '/resetwarn @someone'])
def test_admin_permission_required_before_target_resolution_or_storage(application,action):
    async def go():
        for kind in ('member','no_right','anonymous','bot','failure'):
            c=RecordingClient();m=group(action)
            if kind=='member': c.members[5]=status(enums.ChatMemberStatus.MEMBER)
            if kind=='no_right': c.members[5]=admin(can_restrict_members=False)
            if kind=='anonymous': m.from_user=None;m.sender_chat=SimpleNamespace(id=-100)
            if kind=='bot':m.from_user.is_bot=True
            if kind=='failure':c.members[5]=RuntimeError('secret')
            await _dispatch(application,c,m)
            assert m.replies and 'secret' not in m.replies[-1]
            assert not any(x[0]=='resolve' for x in c.calls)
            assert await get_db()[group_warns.COLLECTION].count_documents({})==0
    run(go())


def test_target_protection_and_reply_syntax(application):
    async def go():
        c=RecordingClient()
        for target in (5,4242):
            m=await dispatch(application,c,f'/warn {target} nope')
            assert 'Cannot warn' in m.replies[-1]
        for state in (enums.ChatMemberStatus.OWNER,enums.ChatMemberStatus.ADMINISTRATOR,enums.ChatMemberStatus.LEFT,enums.ChatMemberStatus.BANNED):
            c.members[9]=status(state)
            m=await dispatch(application,c,'/warn 9 nope')
            assert 'not an eligible' in m.replies[-1]
        c.members[9]=status(enums.ChatMemberStatus.MEMBER)
        m=await dispatch(application,c,'.warn reason',replied=reply())
        assert '<b>1</b>' in m.replies[-1]
        m=await dispatch(application,c,'/rmwarn',replied=reply())
        assert '<b>0</b>' in m.replies[-1]
        bad=reply();bad.chat.id=-200
        m=await dispatch(application,c,'/warn reason',replied=bad)
        assert 'Usage' in m.replies[-1]
        assert 'Usage' in (await dispatch(application,c,'/rmwarn 9 extra')).replies[-1]
        c.members[9]=status(enums.ChatMemberStatus.ADMINISTRATOR)
        assert 'reset' in (await dispatch(application,c,'/resetwarn 9')).replies[-1].lower()
    run(go())


def test_replay_and_out_of_order_commands_do_not_change_counts(application):
    async def go():
        c=RecordingClient();first=group('/warn 9');await _dispatch(application,c,first)
        reset=group('/resetwarn 9');await _dispatch(application,c,reset)
        await _dispatch(application,c,first)
        assert 'out of order' in first.replies[-1]
        await _dispatch(application,c,reset)
        assert 'already processed' in reset.replies[-1]
        row=await get_db()[group_warns.COLLECTION].find_one({'_id':'4242:-100:9'})
        assert row['count']==0 and row['last_message_id']==reset.id
    run(go())


def test_warns_counter_cap_and_unconfirmed_storage(application,monkeypatch):
    async def go():
        c=RecordingClient();m=group('/warn 9')
        scope={'_id':'4242:-100:9','bot_id':4242,'chat_id':-100,'user_id':9}
        coll=get_db()[group_warns.COLLECTION]
        await coll.insert_one({**scope,'version':'0'*32,'count':100,'last_message_id':m.id-1})
        await _dispatch(application,c,m)
        assert 'already has 100' in m.replies[-1]
        async def fail(*args):raise RuntimeError('credential secret')
        monkeypatch.setattr(group_warns,'_change',fail)
        m=group('/warn 9');await _dispatch(application,c,m)
        assert 'could not be confirmed' in m.replies[-1] and 'secret' not in m.replies[-1]
        m=group('/warns 9');monkeypatch.setattr(group_warns,'_load',fail)
        await _dispatch(application,c,m)
        assert 'could not be loaded' in m.replies[-1] and 'secret' not in m.replies[-1]
    run(go())


def test_cas_retries_conflicts_without_lost_updates_and_never_retries_uncertain(monkeypatch):
    async def go():
        scope={'_id':'4242:-100:9','bot_id':4242,'chat_id':-100,'user_id':9}
        coll=get_db()[group_warns.COLLECTION]
        await coll.insert_one({**scope,'version':'0'*32,'count':0,'last_message_id':1})
        real=coll.update_one;calls=[]
        async def race(query, update):
            calls.append(1)
            if len(calls)==1:
                await real(query,{'$set':{'version':'1'*32,'count':1,'last_message_id':2}})
            return await real(query,update)
        monkeypatch.setattr(coll,'update_one',race)
        monkeypatch.setattr(group_warns,'get_db',lambda:{group_warns.COLLECTION:coll})
        assert await group_warns._change(scope,3,'warn')==('saved',2)
        assert len(calls)==2
        async def uncertain(*args,**kwargs):
            calls.append(1);raise RuntimeError('unknown')
        monkeypatch.setattr(coll,'update_one',uncertain)
        with pytest.raises(RuntimeError):await group_warns._change(scope,4,'warn')
        assert len(calls)==3
    run(go())


def test_corrupted_document_fails_closed_and_bot_or_group_is_invalid(application):
    async def go():
        c=RecordingClient();coll=get_db()[group_warns.COLLECTION]
        await coll.insert_one({'_id':'4242:-100:9','bot_id':999,'chat_id':-100,'user_id':9,'version':'0'*32,'count':3,'last_message_id':1})
        for action in ('/warn 9','/warns 9','/rmwarn 9','/resetwarn 9'):
            m=await dispatch(application,c,action)
            assert '⚠️' in m.replies[-1]
        assert (await coll.find_one({'_id':'4242:-100:9'}))['count']==3
        c.me.id=None;m=group('/warns');await _dispatch(application,c,m);assert not m.replies
        c.me.id=4242;m=group('/warns');m.chat.type=enums.ChatType.PRIVATE;await _dispatch(application,c,m);assert not m.replies
    run(go())


def test_duplicate_initial_creation_and_older_command_suppression(monkeypatch):
    from pymongo.errors import DuplicateKeyError
    async def go():
        scope={'_id':'4242:-100:9','bot_id':4242,'chat_id':-100,'user_id':9}
        coll=get_db()[group_warns.COLLECTION];original=coll.insert_one;calls=[]
        async def race(doc):
            calls.append(1)
            await original({**scope,'version':'0'*32,'count':1,'last_message_id':10})
            raise DuplicateKeyError('duplicate')
        monkeypatch.setattr(coll,'insert_one',race)
        monkeypatch.setattr(group_warns,'get_db',lambda:{group_warns.COLLECTION:coll})
        assert await group_warns._change(scope,9,'warn') == ('stale',1)
        assert len(calls)==1 and (await coll.find_one({'_id':scope['_id']}))['count']==1
        with pytest.raises(ValueError):await group_warns._change(scope,11,'unknown')
        with pytest.raises(ValueError):await group_warns._change(scope,True,'warn')
    run(go())


def test_conflicts_exhaust_and_unacknowledged_write_fails(monkeypatch):
    async def go():
        scope={'_id':'4242:-100:9','bot_id':4242,'chat_id':-100,'user_id':9}
        coll=get_db()[group_warns.COLLECTION]
        await coll.insert_one({**scope,'version':'0'*32,'count':0,'last_message_id':1})
        calls=[]
        async def conflict(*args,**kwargs):
            calls.append(1);return SimpleNamespace(acknowledged=True,matched_count=0)
        monkeypatch.setattr(coll,'update_one',conflict)
        monkeypatch.setattr(group_warns,'get_db',lambda:{group_warns.COLLECTION:coll})
        assert await group_warns._change(scope,2,'warn') == ('conflict',None)
        assert len(calls)==group_warns.MAX_ATTEMPTS
        async def unack(*args,**kwargs):
            calls.append(1);return SimpleNamespace(acknowledged=False)
        monkeypatch.setattr(coll,'update_one',unack)
        with pytest.raises(RuntimeError):await group_warns._change(scope,2,'warn')
        assert len(calls)==group_warns.MAX_ATTEMPTS+1
    run(go())


def test_bson_int64_scope_and_counter_validation():
    import bson
    scope={'_id':'4242:-1001234567890:2345678901','bot_id':4242,'chat_id':-1001234567890,'user_id':2345678901}
    doc={**scope,'version':'0'*32,'count':1,'last_message_id':2345678901}
    decoded=bson.decode(bson.encode(doc))
    assert group_warns._valid(decoded,scope)==1
    decoded['count']=True
    with pytest.raises(group_warns.InvalidWarnings):group_warns._valid(decoded,scope)
    decoded['count']=1;decoded['user_id']=True
    with pytest.raises(group_warns.InvalidWarnings):group_warns._valid(decoded,scope)

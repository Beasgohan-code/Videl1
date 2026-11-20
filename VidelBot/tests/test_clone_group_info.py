"""Offline tests for clone-and-chat isolated rules and greetings."""
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import FakeMsg, reset_db, run
from tests.test_audit import _dispatch
from tests.test_clone_group_moderation import application, RecordingClient, admin, group, status
from filestore.database.mongo import get_db
from filestore.worker_bot import group_info


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())


class GroupClient(RecordingClient):
    def __init__(self, bot_id=4242):
        super().__init__()
        self.me.id = bot_id
        self.members[5] = admin(can_change_info=True)

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append(('send', chat_id, text, kwargs))
        if 'send' in self.fail:
            raise RuntimeError('secret access token')


def message(text, chat_id=-100, user_id=5):
    msg = group(text, uid=user_id)
    msg.chat.id = chat_id
    return msg


def joined(chat_id=-100, *users):
    msg = message('joined', chat_id)
    msg.new_chat_members = list(users)
    return msg


def user(uid=9, name='Jane', is_bot=False):
    return SimpleNamespace(id=uid, first_name=name, is_bot=is_bot)


async def dispatch(app, client, text, chat_id=-100, user_id=5):
    msg = message(text, chat_id, user_id)
    await _dispatch(app, client, msg)
    return msg


def test_group_and_clone_isolation_preserves_multiline_and_plain_html(application):
    async def go():
        a=GroupClient(4242);b=GroupClient(5151)
        await dispatch(application,a,'/setrules First line\nNo <spam> & more',-100)
        await dispatch(application,a,'.setrules Different group',-200)
        await dispatch(application,b,'/setrules Other clone',-100)
        for client,chat_id,expected,not_expected in [
            (a,-100,'First line\nNo &lt;spam&gt; &amp; more','Other clone'),
            (a,-200,'Different group','First line'),
            (b,-100,'Other clone','Different group'),
        ]:
            result=await dispatch(application,client,'/rules',chat_id,9)
            assert expected in result.replies[-1] and not_expected not in result.replies[-1]
        docs=await get_db()[group_info.COLLECTION].find({}).to_list(None)
        assert {(d['bot_id'],d['chat_id']) for d in docs}=={(4242,-100),(4242,-200),(5151,-100)}
    run(go())


@pytest.mark.parametrize('action', ['/setrules hacked','/resetrules','/setwelcome hacked','/resetwelcome','/welcome on','/welcome off','/welcome'])
def test_settings_reject_nonadmin_missing_right_anonymous_and_bot_actor(application, action):
    async def go():
        for kind in ('member','no_right','anonymous','bot','permission_error'):
            client=GroupClient();msg=message(action)
            if kind=='member':client.members[5]=status(enums.ChatMemberStatus.MEMBER)
            if kind=='no_right':client.members[5]=admin(can_change_info=False)
            if kind=='anonymous':msg.from_user=None;msg.sender_chat=SimpleNamespace(id=-100)
            if kind=='bot':msg.from_user.is_bot=True
            if kind=='permission_error':client.members[5]=RuntimeError('secret access token')
            await _dispatch(application,client,msg)
            assert msg.replies and 'admin' in msg.replies[-1].lower()
            assert await get_db()[group_info.COLLECTION].count_documents({})==0
    run(go())


def test_owner_can_set_and_any_group_member_can_read_rules(application):
    async def go():
        c=GroupClient();c.members[5]=status(enums.ChatMemberStatus.OWNER)
        m=await dispatch(application,c,'/setrules Group rule')
        assert 'saved' in m.replies[-1]
        c.calls.clear();m=await dispatch(application,c,'/rules',user_id=9)
        assert 'Group rule' in m.replies[-1] and not c.calls
    run(go())


def test_resets_only_selected_fields_and_keep_other_clone_data(application):
    async def go():
        c=GroupClient();await dispatch(application,c,'/setrules rules')
        await dispatch(application,c,'/setwelcome Hey {first}')
        await dispatch(application,c,'/welcome on')
        await dispatch(application,c,'/resetrules')
        saved=await get_db()[group_info.COLLECTION].find_one({'_id':'4242:-100'})
        assert 'rules' not in saved and saved['welcome_text']=='Hey {first}' and saved['welcome_enabled'] is True
        await dispatch(application,c,'/resetwelcome')
        saved=await get_db()[group_info.COLLECTION].find_one({'_id':'4242:-100'})
        assert 'welcome_text' not in saved and saved['welcome_enabled'] is True
        msg=joined(-100,user());await _dispatch(application,c,msg)
        assert any(x[0]=='send' and 'Welcome Jane' in x[2] for x in c.calls)
        await dispatch(application,c,'/welcome off')
        saved=await get_db()[group_info.COLLECTION].find_one({'_id':'4242:-100'})
        assert saved['welcome_enabled'] is False
    run(go())


def test_welcome_is_opt_in_and_escapes_and_does_not_expand_user_placeholders(application):
    async def go():
        c=GroupClient();msg=joined(-100,user(name='{chatname}<a>'))
        msg.chat.title='Group & Friends'
        await dispatch(application,c,'/setwelcome Hi {first}, {id} in {chatname}! <b>')
        await _dispatch(application,c,msg)
        assert not any(x[0]=='send' for x in c.calls)
        await dispatch(application,c,'/welcome on')
        await _dispatch(application,c,msg)
        send=next(x for x in c.calls if x[0]=='send')
        assert send[1]==-100 and send[2]=='Hi {chatname}&lt;a&gt;, 9 in Group &amp; Friends! &lt;b&gt;'
        assert send[3]['parse_mode']==enums.ParseMode.HTML
        c.calls.clear();await dispatch(application,c,'/welcome off');await _dispatch(application,c,msg)
        assert not any(x[0]=='send' for x in c.calls)
    run(go())


def test_welcome_enabled_per_clone_and_group_only(application):
    async def go():
        a=GroupClient(4242);b=GroupClient(5151)
        await dispatch(application,a,'/welcome on',-100)
        await dispatch(application,b,'/welcome on',-200)
        for client,chat,expected in [(a,-100,True),(a,-200,False),(b,-100,False),(b,-200,True)]:
            client.calls.clear();await _dispatch(application,client,joined(chat,user()))
            assert any(x[0]=='send' for x in client.calls) is expected
    run(go())


def test_welcome_caps_join_events_and_ignores_bot_users(application):
    async def go():
        c=GroupClient();await dispatch(application,c,'/welcome on');c.calls.clear()
        msg=joined(-100,user(4242,'clone',True),user(10,'bot',True),*[user(i,str(i)) for i in range(20,40)])
        await _dispatch(application,c,msg)
        sends=[x for x in c.calls if x[0]=='send']
        assert len(sends)==8 and all('bot' not in x[2] for x in sends)
    run(go())


def test_group_commands_do_not_run_in_private_chat_or_for_other_bot(application):
    async def go():
        c=GroupClient()
        for text in ['/setrules nope', '/welcome on', '/setwelcome nope', '/rules']:
            msg=FakeMsg(text,uid=5,chat_type=enums.ChatType.PRIVATE,chat_id=5)
            await _dispatch(application,c,msg)
            assert not msg.replies
        msg=message('/setrules@OtherBot nope');await _dispatch(application,c,msg)
        assert not msg.replies and await get_db()[group_info.COLLECTION].count_documents({})==0
    run(go())


def test_invalid_scope_and_malformed_stored_record_fail_closed(application):
    async def go():
        c=GroupClient();c.me.id=None;msg=message('/setrules nope')
        await _dispatch(application,c,msg);assert not msg.replies
        c=GroupClient();col=get_db()[group_info.COLLECTION]
        await col.insert_one({'_id':'4242:-100','bot_id':5151,'chat_id':-100,'rules':'private'})
        msg=await dispatch(application,c,'/rules');assert 'private' not in msg.replies[-1]
        msg=await dispatch(application,c,'/setrules New')
        assert 'could not be confirmed' in msg.replies[-1]
        assert (await col.find_one({'_id':'4242:-100'}))['rules']=='private'
    run(go())


def test_write_ambiguous_never_retry_and_does_not_log_secrets(application,monkeypatch,caplog):
    async def go():
        c=GroupClient();calls=[];original=group_info._write
        async def lost(scope, update):
            calls.append(1);await original(scope,update);raise RuntimeError('secret access token')
        monkeypatch.setattr(group_info,'_write',lost)
        msg=await dispatch(application,c,'/setrules new')
        assert calls==[1] and 'could not be confirmed' in msg.replies[-1]
        assert (await get_db()[group_info.COLLECTION].find_one({'_id':'4242:-100'}))['rules']=='new'
        assert 'secret access token' not in caplog.text+msg.replies[-1]
    run(go())


def test_storage_error_does_not_send_or_display_sensitive_exception(application,monkeypatch,caplog):
    async def broken(scope):raise RuntimeError('secret access token')
    monkeypatch.setattr(group_info,'_read',broken)
    async def go():
        c=GroupClient();msg=await dispatch(application,c,'/rules')
        assert 'could not be loaded' in msg.replies[-1]
        await _dispatch(application,c,joined(-100,user()))
        assert not any(x[0]=='send' for x in c.calls)
        assert 'secret access token' not in caplog.text+msg.replies[-1]
    run(go())


def test_long_or_empty_rules_and_template_rejected(application):
    async def go():
        c=GroupClient()
        for text in ['/setrules','/setwelcome','/setrules '+('x'*1001),'/setwelcome '+('{first}'*80),'/setrules '+('&'*1000)]:
            msg=await dispatch(application,c,text)
            assert 'Provide plain text' in msg.replies[-1]
        assert await get_db()[group_info.COLLECTION].count_documents({})==0
    run(go())


def test_welcome_delivery_failure_has_no_retry_or_secret_logging(application,caplog):
    async def go():
        c=GroupClient();await dispatch(application,c,'/welcome on');c.fail.add('send');c.calls.clear()
        await _dispatch(application,c,joined(-100,user()))
        assert len([x for x in c.calls if x[0]=='send'])==1
        assert 'secret access token' not in caplog.text
    run(go())


def test_unacknowledged_setting_write_is_reported_uncertain(application, monkeypatch):
    class FakeCollection:
        async def update_one(self, scope, update, upsert=False):
            assert upsert is True
            return SimpleNamespace(acknowledged=False)
    class FakeDB:
        def __getitem__(self, name):
            assert name==group_info.COLLECTION
            return FakeCollection()
    monkeypatch.setattr(group_info,'get_db',lambda: FakeDB())
    async def go():
        c=GroupClient();msg=await dispatch(application,c,'/welcome on')
        assert 'could not be confirmed' in msg.replies[-1]
        assert 'enabled' not in msg.replies[-1]
    run(go())


def test_group_fields_update_independently_and_no_settings_cache(application):
    import asyncio
    async def go():
        c=GroupClient()
        rules=message('/setrules R');welcome=message('/setwelcome Hello {first}')
        await asyncio.gather(_dispatch(application,c,rules),_dispatch(application,c,welcome))
        doc=await get_db()[group_info.COLLECTION].find_one({'_id':'4242:-100'})
        assert doc['rules']=='R' and doc['welcome_text']=='Hello {first}'
        await get_db()[group_info.COLLECTION].update_one({'_id':'4242:-100'},{'$set':{'rules':'Changed externally'}})
        msg=await dispatch(application,c,'/rules')
        assert 'Changed externally' in msg.replies[-1]
    run(go())


def test_settings_survive_group_mode_round_trip(application):
    from filestore.database.main_db import MainDB
    from filestore.worker_bot.engine import WorkerEngine
    import config
    async def go():
        c=GroupClient();await dispatch(application,c,'/setrules Keep me')
        db=MainDB();eng=WorkerEngine()
        await db.add_bot(4242,5,'enc','CloneBot',-100123)
        await db.set_bot_active(4242,False)
        assert await eng.select_profile(4242,5,'group')=='saved'
        assert await eng.select_profile(4242,5,'filestore')=='saved'
        assert await eng.select_profile(4242,5,'group')=='saved'
        msg=await dispatch(application,c,'/rules')
        assert 'Keep me' in msg.replies[-1]
    old=config.CLONE_PROFILES_SINGLE_INSTANCE
    config.CLONE_PROFILES_SINGLE_INSTANCE=True
    try:run(go())
    finally:config.CLONE_PROFILES_SINGLE_INSTANCE=old

"""Owner/admin group-clone diagnostics: fake clients, no Telegram or real Mongo."""
import asyncio
from types import SimpleNamespace

import pytest
from tests.harness import FakeClient, FakeMsg, run
from bson import BSON
from pyrogram import enums
from filestore.main_bot.plugins import group_clone_check as checks
from filestore.worker_bot import engine

BOT=2**33+42
USER=2**33+5
GROUP=-1001234567890


def admin(**rights):
    return SimpleNamespace(status=enums.ChatMemberStatus.ADMINISTRATOR, privileges=SimpleNamespace(**rights))


class Worker:
    def __init__(self):
        self.calls=[];self.actor=admin(can_restrict_members=True, can_delete_messages=False,
                                      can_pin_messages=None, can_change_info=True)
        self.bot=admin(can_restrict_members=True, can_delete_messages=True,
                       can_pin_messages=False, can_change_info=None)
        self.chat=SimpleNamespace(id=GROUP, type=enums.ChatType.SUPERGROUP)
    async def get_me(self): self.calls.append(('me',));return SimpleNamespace(id=BOT)
    async def get_chat(self, group): self.calls.append(('chat',group));return self.chat
    async def get_chat_member(self, group, user):
        self.calls.append(('member',group,user));return self.actor if user==USER else self.bot


class Engine:
    def __init__(self, worker): self.worker=worker
    def get_worker(self,bid): return self.worker


def record(**changes):
    data={'_id':BOT,'owner_id':USER,'clone_profile':'group','is_deleted':False,
          'bot_token_encrypted':'secret-token'}
    data.update(changes)
    return BSON(BSON.encode(data)).decode()


@pytest.fixture(autouse=True)
def reset_limits():
    checks._recent.clear();checks._active.clear();checks._starts.clear()
    yield
    checks._recent.clear();checks._active.clear();checks._starts.clear()


def setup(monkeypatch, worker=None, row=None):
    row=record() if row is None else row
    async def get_bot(bid): return row
    monkeypatch.setattr(checks.main_db,'get_bot',get_bot)
    eng=Engine(worker if worker is not None else Worker())
    monkeypatch.setattr(engine,'worker_engine',eng)
    return eng


def message(uid=USER): return FakeMsg(f'/clonegroupcheck {BOT} {GROUP}',uid=uid)


def test_readonly_owned_group_admin_observes_both_parties(monkeypatch):
    eng=setup(monkeypatch);m=message();run(checks.clone_group_check(FakeClient(),m))
    text=m.replies[-1]
    assert f'<code>{BOT}</code>' in text and f'<code>{GROUP}</code>' in text
    assert 'Restrict members: You Yes · Bot Yes' in text
    assert 'Delete messages: You No · Bot Yes' in text
    assert 'Pin messages: You Unknown · Bot No' in text
    assert 'Change group info: You Yes · Bot Unknown' in text
    assert 'Read-only snapshot' in text and 'actual moderation were not checked' in text
    assert 'secret-token' not in text and not checks._active
    assert eng.worker.calls==[('me',),('chat',GROUP),('member',GROUP,USER),('member',GROUP,BOT),('member',GROUP,USER)]


def test_basic_group_and_owner_privileges_are_explained(monkeypatch):
    worker=Worker();worker.chat.type=enums.ChatType.GROUP
    worker.actor=SimpleNamespace(status=enums.ChatMemberStatus.OWNER)
    worker.bot=SimpleNamespace(status=enums.ChatMemberStatus.MEMBER)
    setup(monkeypatch,worker);m=message();run(checks.clone_group_check(FakeClient(),m))
    assert 'Basic group' in m.replies[-1] and 'require a supergroup' in m.replies[-1]
    assert 'Restrict members: You Yes · Bot No' in m.replies[-1]


@pytest.mark.parametrize('change',[{'owner_id':7},{'is_deleted':True},{'is_deleted':None},
                                    {'clone_profile':'music'},{'clone_profile':None},{'_id':BOT+1}])
def test_other_or_unsupported_records_fail_before_client_calls(monkeypatch,change):
    eng=setup(monkeypatch,row=record(**change));m=message();run(checks.clone_group_check(FakeClient(),m))
    assert 'could not be verified' in m.replies[-1] and not eng.worker.calls
    assert 'secret-token' not in m.replies[-1]


@pytest.mark.parametrize('text',['/clonegroupcheck','/clonegroupcheck 1 @group',
    '/clonegroupcheck 0 -1','/clonegroupcheck 1 1','/clonegroupcheck 1 -0',
    '/clonegroupcheck 1 -99999999999999999999999999','/clonegroupcheck 1 -100 <script>',
    '/clonegroupcheck '+('9'*5000)+' -1','/clonegroupcheck 1 -1?token=secret'])
def test_invalid_arguments_never_read_registry_or_echo_input(monkeypatch,text):
    async def fail(*a): pytest.fail('invalid input must not query registry')
    monkeypatch.setattr(checks.main_db,'get_bot',fail)
    m=FakeMsg(text,uid=USER);run(checks.clone_group_check(FakeClient(),m))
    assert m.replies[-1].startswith('Usage:') and 'secret' not in m.replies[-1] and '<script>' not in m.replies[-1]


def test_untrusted_context_and_wrong_primary_are_ignored(monkeypatch):
    eng=setup(monkeypatch)
    contexts=[FakeMsg(f'/clonegroupcheck {BOT} {GROUP}',uid=USER,chat_type=enums.ChatType.GROUP,chat_id=GROUP),
              FakeMsg(f'/clonegroupcheck {BOT} {GROUP}',uid=USER,chat_id=USER+1), message()]
    contexts[-1].from_user.is_bot=True
    for m in contexts:
        run(checks.clone_group_check(FakeClient(),m));assert not m.replies
    m=message();m.sender_chat=SimpleNamespace(id=GROUP);run(checks.clone_group_check(FakeClient(),m));assert not m.replies
    c=FakeClient();c.me.id+=1;m=message();run(checks.clone_group_check(c,m));assert not m.replies
    assert not eng.worker.calls


def test_nonadmin_cannot_observe_bot_rights(monkeypatch):
    worker=Worker();worker.actor=SimpleNamespace(status=enums.ChatMemberStatus.MEMBER)
    setup(monkeypatch,worker);m=message();run(checks.clone_group_check(FakeClient(),m))
    assert 'could not be verified' in m.replies[-1] and ('member',GROUP,BOT) not in worker.calls
    assert 'Restrict members' not in m.replies[-1]


@pytest.mark.parametrize('error_stage',['identity','group','member','db','no_client'])
def test_errors_are_generic_and_release_capacity(monkeypatch,caplog,error_stage):
    worker=Worker();eng=setup(monkeypatch,worker)
    async def fail(*a): raise RuntimeError('leaked-secret-token')
    if error_stage=='identity': worker.get_me=fail
    if error_stage=='group': worker.get_chat=fail
    if error_stage=='member': worker.get_chat_member=fail
    if error_stage=='db': monkeypatch.setattr(checks.main_db,'get_bot',fail)
    if error_stage=='no_client': eng.worker=None
    m=message();run(checks.clone_group_check(FakeClient(),m))
    assert 'could not be verified' in m.replies[-1] and 'leaked-secret-token' not in m.replies[-1]+caplog.text
    assert not checks._active


@pytest.mark.parametrize('change',['owner','token','mode','worker','admin'])
def test_changes_during_inspection_do_not_return_group_details(monkeypatch,change):
    worker=Worker();eng=setup(monkeypatch,worker);calls=[]
    async def read(bid):
        calls.append(1)
        if len(calls)==1:return record()
        if change=='owner': return record(owner_id=USER+1)
        if change=='token': return record(bot_token_encrypted='new-secret')
        if change=='mode': return record(clone_profile='filestore')
        return record()
    monkeypatch.setattr(checks.main_db,'get_bot',read)
    old=worker.get_chat_member
    async def member(group,uid):
        result=await old(group,uid)
        if uid==BOT and change=='worker': eng.worker=Worker()
        if uid==USER and change=='admin' and len([x for x in worker.calls if x==('member',GROUP,USER)])==2:
            return SimpleNamespace(status=enums.ChatMemberStatus.MEMBER)
        return result
    worker.get_chat_member=member
    m=message();run(checks.clone_group_check(FakeClient(),m))
    assert 'could not be verified' in m.replies[-1] and 'Restrict members' not in m.replies[-1]
    assert 'new-secret' not in m.replies[-1]


def test_concurrent_checks_are_bounded_and_cancellation_releases_claim(monkeypatch):
    async def go():
        entered,release=asyncio.Event(),asyncio.Event()
        async def blocked(*a): entered.set();await release.wait()
        monkeypatch.setattr(checks,'_inspect',blocked)
        one=message();task=asyncio.create_task(checks.clone_group_check(FakeClient(),one));await entered.wait()
        another=message();await checks.clone_group_check(FakeClient(),another)
        assert 'busy' in another.replies[-1] and USER in checks._active
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert USER not in checks._active
    run(go())


def test_handler_is_private_and_registered_with_existing_loader():
    import run as app_run
    class Registry:
        def __init__(self):self.added=[]
        def add_handler(self,h,g):self.added.append((h,g))
    r=Registry();app_run.load_plugins(r)
    assert any(h.callback==checks.clone_group_check for h,g in r.added)
    assert not app_run.boot_import_errors
    handler=checks.clone_group_check.handlers[0][0]
    assert run(handler.filters(FakeClient(),message()))
    assert not run(handler.filters(FakeClient(),FakeMsg(f'/clonegroupcheck {BOT} {GROUP}',uid=USER,chat_type=enums.ChatType.GROUP,chat_id=GROUP)))


@pytest.mark.parametrize('kind',['wrong_id','channel','mismatched_chat','unknown_status'])
def test_mismatched_client_group_or_admin_is_rejected(monkeypatch,kind):
    worker=Worker()
    if kind=='wrong_id':
        async def wrong(): return SimpleNamespace(id=BOT+1)
        worker.get_me=wrong
    elif kind=='channel': worker.chat.type=enums.ChatType.CHANNEL
    elif kind=='mismatched_chat': worker.chat.id=GROUP-1
    else: worker.actor=SimpleNamespace(status=None)
    setup(monkeypatch,worker);m=message();run(checks.clone_group_check(FakeClient(),m))
    assert 'could not be verified' in m.replies[-1] and 'Restrict members' not in m.replies[-1]


def test_rate_limit_is_local_and_does_not_consume_inputs(monkeypatch):
    async def go():
        calls=[]
        async def inspect(*args): calls.append(args);return 'snapshot'
        monkeypatch.setattr(checks,'_inspect',inspect)
        monkeypatch.setattr(checks.time,'monotonic',lambda:100.0)
        m=message();await checks.clone_group_check(FakeClient(),m);assert m.replies==['snapshot']
        other=message();await checks.clone_group_check(FakeClient(),other)
        assert 'too recently' in other.replies[-1] and len(calls)==1
    run(go())


def test_owner_menu_remains_within_telegram_limit():
    from core.commands import USER_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS
    assert 'clonegroupcheck' in {name for name,_ in USER_COMMANDS}
    assert len(USER_COMMANDS+ADMIN_COMMANDS+OWNER_COMMANDS)<=100

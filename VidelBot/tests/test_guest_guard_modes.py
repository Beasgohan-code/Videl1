"""Synthetic WzGram guest/guardian paths; no Telegram service is contacted."""
import asyncio
from types import SimpleNamespace

import pytest
from pyrogram import ContinuePropagation, StopPropagation, enums
from pyrogram.handlers import GuestMessageHandler, ChatJoinRequestHandler
from tests.harness import FakeClient, load_all, run
import config
from core import guest_modes as modes


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    modes._seen_guest.clear(); modes._guest_users.clear(); modes._seen_guard.clear()
    modes._guest_starts.clear(); modes._guard_starts.clear()
    modes._guardcheck_recent.clear(); modes._guardcheck_active.clear(); modes._guardcheck_starts.clear()
    modes._inflight_guest = 0; modes._inflight_guard = 0
    monkeypatch.setattr(config, 'GUEST_REPLIES_ENABLED', True)
    monkeypatch.setattr(config, 'GUARD_QUEUE_ENABLED', True)


class Client:
    def __init__(self):
        self.me = SimpleNamespace(id=int(config.BOT_TOKEN.split(':')[0]))
        self.guests = []; self.guards = []; self.fail = False
    async def answer_guest_query(self, qid, result):
        self.guests.append((qid, result))
        if self.fail: raise RuntimeError('private query secret')
        return object()
    async def answer_chat_join_request_query(self, qid, result):
        self.guards.append((qid, result))
        if self.fail: raise RuntimeError('private query secret')
        return True


def guest(qid='1234', uid=7, is_bot=False):
    return SimpleNamespace(guest_query_id=qid, guest_bot_caller_user=SimpleNamespace(id=uid, is_bot=is_bot), text='private input')


def test_registered_with_existing_wzgram_dispatcher():
    modules = load_all()
    assert modes in modules
    assert any(isinstance(h, GuestMessageHandler) for h, g in modes.guest_introduction.handlers)
    assert any(isinstance(h, ChatJoinRequestHandler) and g == -10 for h, g in modes.guardian_manual_queue.handlers)


def test_guest_single_public_fixed_reply_and_replay_blocked():
    c = Client(); m = guest()
    run(modes.guest_introduction(c, m)); run(modes.guest_introduction(c, m))
    assert len(c.guests) == 1
    result = c.guests[0][1]
    assert result.input_message_content.message_text == modes._GUEST_REPLY
    assert 'private input' not in result.input_message_content.message_text
    assert result.input_message_content.parse_mode == enums.ParseMode.DISABLED


def test_guest_wrong_bot_missing_caller_and_bot_caller_ignored(monkeypatch):
    c = Client(); c.me.id += 1; run(modes.guest_introduction(c, guest()))
    c.me.id -= 1
    run(modes.guest_introduction(c, guest(is_bot=True)))
    run(modes.guest_introduction(c, SimpleNamespace(guest_query_id='123', guest_bot_caller_user=None)))
    run(modes.guest_introduction(c, guest(qid='9'*5000)))
    monkeypatch.setattr(config, 'GUEST_REPLIES_ENABLED', False)
    run(modes.guest_introduction(c, guest()))
    assert c.guests == []


def test_guest_failure_does_not_retry_and_logs_no_input(caplog):
    c = Client(); c.fail = True
    run(modes.guest_introduction(c, guest())); run(modes.guest_introduction(c, guest()))
    assert len(c.guests) == 1 and 'private input' not in caplog.text and 'private query secret' not in caplog.text


def test_guardian_queues_only_and_stops_legacy_handlers():
    c = Client(); r = SimpleNamespace(query_id='1234')
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, r))
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, r))
    assert c.guards == [('1234', enums.ChatJoinRequestQueryResult.QUEUE)]
    with pytest.raises(ContinuePropagation): run(modes.guardian_manual_queue(c, SimpleNamespace(query_id=None)))


def test_guardian_disabled_invalid_or_failure_never_reaches_legacy(monkeypatch, caplog):
    c = Client()
    monkeypatch.setattr(config, 'GUARD_QUEUE_ENABLED', False)
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, SimpleNamespace(query_id='123')))
    monkeypatch.setattr(config, 'GUARD_QUEUE_ENABLED', True)
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, SimpleNamespace(query_id='9'*5000)))
    c.fail = True
    with pytest.raises(StopPropagation): run(modes.guardian_manual_queue(c, SimpleNamespace(query_id='456')))
    assert len(c.guards) == 1 and 'private query secret' not in caplog.text


def test_guardian_cancellation_is_not_swallowed():
    async def go():
        class Slow(Client):
            async def answer_chat_join_request_query(self, *args):
                await asyncio.Event().wait()
        task = asyncio.create_task(modes.guardian_manual_queue(Slow(), SimpleNamespace(query_id='1234')))
        await asyncio.sleep(0); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    run(go())
    assert modes._inflight_guard == 0


def test_guest_article_serializes_using_installed_wzgram():
    from pyrogram import raw
    c = Client(); run(modes.guest_introduction(c, guest()))
    serializer = FakeClient(); serializer.link_preview_options = None
    article = run(c.guests[0][1].write(serializer))
    assert isinstance(article, raw.types.InputBotInlineResult)
    assert article.send_message.message == modes._GUEST_REPLY
    assert article.send_message.reply_markup is None


def test_signed_mtproto_query_ids_and_bounds():
    assert modes._query_id('-123') and modes._query_id(str(-(2**63)))
    assert not modes._query_id('0') and not modes._query_id(str(2**63))
    assert not modes._query_id(str(-(2**63)-1)) and not modes._query_id('-')
    c = Client()
    with pytest.raises(StopPropagation):
        run(modes.guardian_manual_queue(c, SimpleNamespace(query_id='-123')))
    assert c.guards == [('-123', enums.ChatJoinRequestQueryResult.QUEUE)]


def test_guard_rate_limit_and_cancellation_release_inflight():
    c = Client()
    for i in range(21):
        with pytest.raises(StopPropagation):
            run(modes.guardian_manual_queue(c, SimpleNamespace(query_id=str(i + 1))))
    assert len(c.guards) == 20
    assert modes._inflight_guard == 0


def test_real_plugin_loader_registers_priority_handlers():
    import run as app_run
    from core import fsub
    from plugins.group_mod import plugin as group_mod
    class Registry:
        def __init__(self): self.added = []
        def add_handler(self, handler, group): self.added.append((handler, group))
    registry = Registry()
    app_run.load_plugins(registry)
    by_callback = {h.callback: group for h, group in registry.added}
    assert by_callback[modes.guest_introduction] == 0
    assert by_callback[modes.guardian_manual_queue] == -10
    assert by_callback[modes.guardian_check] == 0
    assert by_callback[fsub.record_join_request] == 0
    assert by_callback[group_mod.group_join_request_handler] == 4
    assert not app_run.boot_import_errors


def test_guest_link_uses_only_valid_primary_bot_username_and_serializes():
    from pyrogram import raw
    client = Client(); client.me.username = 'Videl_TestBot'
    run(modes.guest_introduction(client, guest()))
    article = client.guests[0][1]
    button = article.reply_markup.inline_keyboard[0][0]
    assert button.url == 'https://t.me/Videl_TestBot' and button.callback_data is None
    assert 'private input' not in button.url
    serializer = FakeClient(); serializer.link_preview_options = None
    encoded = run(article.write(serializer))
    assert isinstance(encoded, raw.types.InputBotInlineResult)
    assert isinstance(encoded.send_message.reply_markup, raw.types.ReplyInlineMarkup)
    button_raw = encoded.send_message.reply_markup.rows[0].buttons[0]
    assert isinstance(button_raw.type, raw.types.InlineButtonTypeUrl)
    assert button_raw.type.url == 'https://t.me/Videl_TestBot'


@pytest.mark.parametrize('name', [None, '', 'a', 'a'*33, 'test.bot', 'test/bot',
                                   'testbot?start=secret', 'тestbot', '@testbot'])
def test_guest_invalid_username_never_creates_link(name):
    client = Client(); client.me.username = name
    run(modes.guest_introduction(client, guest()))
    assert len(client.guests) == 1 and client.guests[0][1].reply_markup is None


def test_query_id_aliases_share_replay_guard_for_guest_and_guard():
    client = Client()
    run(modes.guest_introduction(client, guest(qid='123', uid=7)))
    run(modes.guest_introduction(client, guest(qid='000123', uid=8)))
    assert len(client.guests) == 1
    for value in ('-123', '-000123'):
        with pytest.raises(StopPropagation):
            run(modes.guardian_manual_queue(client, SimpleNamespace(query_id=value)))
    assert len(client.guards) == 1


def test_guardcheck_reports_assignment_only_to_private_chat_admin(monkeypatch):
    from tests.harness import FakeMsg
    class CheckClient(Client):
        def __init__(self): super().__init__(); self.calls=[]; self.guard=self.me.id
        async def get_chat(self, target):
            self.calls.append(('chat', target))
            return SimpleNamespace(id=-10055, type=enums.ChatType.SUPERGROUP, guard_bot=SimpleNamespace(id=self.guard))
        async def get_chat_member(self, cid, uid):
            self.calls.append(('member', cid, uid))
            return SimpleNamespace(status=enums.ChatMemberStatus.ADMINISTRATOR, privileges=SimpleNamespace(can_invite_users=True))
    clock=[100.0]; monkeypatch.setattr(modes.time,'monotonic',lambda:clock[0])
    c=CheckClient(); msg=FakeMsg('/guardcheck @examplegroup', uid=7)
    run(modes.guardian_check(c,msg))
    assert c.calls==[('chat','@examplegroup'),('member',-10055,7),('member',-10055,c.me.id)]
    assert 'This bot is reported as the guard bot' in msg.replies[-1]
    assert 'Bot membership: <b>Administrator</b>' in msg.replies[-1]
    assert 'Invite users permission: <b>Yes</b>' in msg.replies[-1]
    assert 'manual-review queue answers are <b>enabled</b>' in msg.replies[-1]
    assert 'does not prove update delivery' in msg.replies[-1]
    clock[0]+=4; c.guard=999; monkeypatch.setattr(config, 'GUARD_QUEUE_ENABLED', False)
    msg=FakeMsg('/guardcheck -10055',uid=7); run(modes.guardian_check(c,msg))
    assert 'A different bot' in msg.replies[-1] and '<b>disabled</b>' in msg.replies[-1]
    assert '999' not in msg.replies[-1]
    clock[0]+=4; c.guard=None; msg=FakeMsg('/guardcheck -10055',uid=7); run(modes.guardian_check(c,msg))
    assert 'no assignment can be confirmed' in msg.replies[-1]


def test_guardcheck_rejects_nonadmins_and_no_error_details(monkeypatch):
    from tests.harness import FakeMsg
    class CheckClient(Client):
        async def get_chat(self, target):
            return SimpleNamespace(id=-10055, type=enums.ChatType.CHANNEL, guard_bot=SimpleNamespace(id=999))
        async def get_chat_member(self, cid, uid): return SimpleNamespace(status=enums.ChatMemberStatus.MEMBER)
    msg=FakeMsg('/guardcheck -10055',uid=7); run(modes.guardian_check(CheckClient(),msg))
    assert 'administrator access could not be verified' in msg.replies[-1] and '999' not in msg.replies[-1]
    class Broken(CheckClient):
        async def get_chat(self, target): raise RuntimeError('private-secret')
    modes._guardcheck_recent.clear()
    msg=FakeMsg('/guardcheck -10055',uid=7); run(modes.guardian_check(Broken(),msg))
    assert 'administrator access could not be verified' in msg.replies[-1] and 'private-secret' not in msg.replies[-1]


@pytest.mark.parametrize('text', ['/guardcheck', '/guardcheck https://t.me/+secret', '/guardcheck @abc',
                                    '/guardcheck 123', '/guardcheck -99999999999999999999', '/guardcheck -0'])
def test_guardcheck_invalid_target_never_resolves(text):
    from tests.harness import FakeMsg
    class NoCalls(Client):
        async def get_chat(self, *args): pytest.fail('invalid target resolved')
    msg=FakeMsg(text,uid=7); run(modes.guardian_check(NoCalls(),msg)); assert msg.replies


def test_guardcheck_ignores_untrusted_context_and_nonprimary_bot():
    from tests.harness import FakeMsg
    class NoCalls(Client):
        async def get_chat(self, *args): pytest.fail('untrusted context resolved')
    for message in (FakeMsg('/guardcheck -10055',uid=7,chat_type=enums.ChatType.SUPERGROUP,chat_id=-10055),
                    FakeMsg('/guardcheck -10055',uid=7,chat_id=8), FakeMsg('/guardcheck -10055',uid=0)):
        run(modes.guardian_check(NoCalls(),message)); assert not message.replies
    bot_message=FakeMsg('/guardcheck -10055',uid=7); bot_message.from_user.is_bot=True
    run(modes.guardian_check(NoCalls(),bot_message)); assert not bot_message.replies
    c=NoCalls(); c.me.id+=1; msg=FakeMsg('/guardcheck -10055',uid=7)
    run(modes.guardian_check(c,msg)); assert not msg.replies


def test_guardcheck_missing_guard_and_failed_membership_are_unknown():
    from tests.harness import FakeMsg
    class Missing(Client):
        async def get_chat(self, target): return SimpleNamespace(id=-10055, type=enums.ChatType.GROUP)
        async def get_chat_member(self, cid, uid): return SimpleNamespace(status=enums.ChatMemberStatus.OWNER)
    msg=FakeMsg('/guardcheck -10055',uid=7); run(modes.guardian_check(Missing(),msg))
    assert 'assignment is unavailable' in msg.replies[-1]
    class Failed(Missing):
        async def get_chat_member(self, cid, uid): raise TimeoutError('secret')
    modes._guardcheck_recent.clear()
    msg=FakeMsg('/guardcheck -10055',uid=7); run(modes.guardian_check(Failed(),msg))
    assert 'could not be verified' in msg.replies[-1] and 'secret' not in msg.replies[-1]


def test_guardcheck_cancellation_propagates():
    from tests.harness import FakeMsg
    async def check():
        class Slow(Client):
            async def get_chat(self, target): await asyncio.Event().wait()
        msg=FakeMsg('/guardcheck -10055',uid=7)
        task=asyncio.create_task(modes.guardian_check(Slow(),msg)); await asyncio.sleep(0); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not msg.replies
    run(check())


@pytest.mark.parametrize('status,priv,expected', [
    (enums.ChatMemberStatus.OWNER, None, ('Owner', 'Available to the owner')),
    (enums.ChatMemberStatus.ADMINISTRATOR, True, ('Administrator', 'Yes')),
    (enums.ChatMemberStatus.ADMINISTRATOR, False, ('Administrator', 'No')),
    (enums.ChatMemberStatus.ADMINISTRATOR, None, ('Administrator', 'Unavailable')),
    (enums.ChatMemberStatus.ADMINISTRATOR, 1, ('Administrator', 'Unavailable')),
    (enums.ChatMemberStatus.MEMBER, True, ('Not an administrator', 'Not applicable')),
    (enums.ChatMemberStatus.BANNED, None, ('Not an administrator', 'Not applicable')),
    (None, True, ('Unavailable', 'Unavailable')),
])
def test_guardcheck_rights_do_not_infer_missing_values(status, priv, expected):
    member=SimpleNamespace(status=status,privileges=SimpleNamespace(can_invite_users=priv))
    assert modes._guardcheck_rights(member)==expected


def test_guardcheck_bot_lookup_failure_keeps_assignment_unknown_rights():
    from tests.harness import FakeMsg
    class C(Client):
        async def get_chat(self,target): return SimpleNamespace(id=-10055,type=enums.ChatType.SUPERGROUP,guard_bot=self.me)
        async def get_chat_member(self,cid,uid):
            if uid==self.me.id: raise RuntimeError('secret-token')
            return SimpleNamespace(status=enums.ChatMemberStatus.OWNER)
    msg=FakeMsg('/guardcheck -10055',uid=7);run(modes.guardian_check(C(),msg))
    assert 'This bot is reported as the guard bot' in msg.replies[-1]
    assert 'Bot membership: <b>Unavailable</b>' in msg.replies[-1]
    assert 'Invite users permission: <b>Unavailable</b>' in msg.replies[-1]
    assert 'secret-token' not in msg.replies[-1]


def test_guardcheck_rate_limit_and_recovery(monkeypatch):
    from tests.harness import FakeMsg
    clock=[100.0];monkeypatch.setattr(modes.time,'monotonic',lambda:clock[0])
    class C(Client):
        def __init__(self):super().__init__();self.calls=0
        async def get_chat(self,target):
            self.calls+=1;return SimpleNamespace(id=-10055,type=enums.ChatType.GROUP)
        async def get_chat_member(self,cid,uid):return SimpleNamespace(status=enums.ChatMemberStatus.OWNER)
    c=C();a=FakeMsg('/guardcheck -10055',uid=7);run(modes.guardian_check(c,a));assert c.calls==1
    b=FakeMsg('/guardcheck -10055',uid=7);run(modes.guardian_check(c,b));assert 'too recently' in b.replies[-1] and c.calls==1
    other=FakeMsg('/guardcheck -10055',uid=8);run(modes.guardian_check(c,other));assert c.calls==2
    clock[0]+=3;a=FakeMsg('/guardcheck -10055',uid=7);run(modes.guardian_check(c,a));assert c.calls==3


def test_guardcheck_limits_inflight_and_releases_on_cancellation():
    from tests.harness import FakeMsg
    async def check():
        started=asyncio.Event()
        class Slow(Client):
            async def get_chat(self,target):started.set();await asyncio.Event().wait()
        c=Slow();first=FakeMsg('/guardcheck -10055',uid=7)
        task=asyncio.create_task(modes.guardian_check(c,first));await started.wait()
        second=FakeMsg('/guardcheck -10055',uid=7);await modes.guardian_check(c,second)
        assert 'busy' in second.replies[-1] and 7 in modes._guardcheck_active
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert 7 not in modes._guardcheck_active
    run(check())


def test_guardcheck_global_limit_does_not_consume_query_capacity(monkeypatch):
    from tests.harness import FakeMsg
    monkeypatch.setattr(modes.time,'monotonic',lambda:100.0)
    class C(Client):
        def __init__(self):super().__init__();self.calls=0
        async def get_chat(self,target):self.calls+=1;return SimpleNamespace(id=-10055,type=enums.ChatType.GROUP)
        async def get_chat_member(self,cid,uid):return SimpleNamespace(status=enums.ChatMemberStatus.OWNER)
    c=C()
    for uid in range(100,121):
        msg=FakeMsg('/guardcheck -10055',uid=uid);run(modes.guardian_check(c,msg))
    assert c.calls==20 and 'busy' in msg.replies[-1]
    with pytest.raises(StopPropagation):run(modes.guardian_manual_queue(c,SimpleNamespace(query_id='555')))
    assert c.guards==[('555',enums.ChatJoinRequestQueryResult.QUEUE)]

"""Synthetic Telegram and mock Mongo checks for premium-code permissions and secrecy."""
from types import SimpleNamespace

import pytest
from pyrogram import enums
from tests.harness import FakeClient, FakeMsg, load_all, no_botapi, reset_db, run

load_all()
from core import admins, botlog, growth, middleware
from core.db import vdb


@pytest.fixture(autouse=True)
def clean():
    run(reset_db()); no_botapi()
    yield
    admins._apply(87654321, False)


def test_registered_filters_restrict_all_three_commands_and_track_admin_changes():
    client = FakeClient()
    cases = [(growth.gencode_cmd, '/gencode 30'), (growth.codes_cmd, '/codes'), (growth.delcode_cmd, '/delcode CODE')]
    for fn, text in cases:
        handler = fn.handlers[0][0]
        assert handler.callback is fn
        assert run(handler.check(client, FakeMsg(text, uid=111)))
        assert run(handler.check(client, FakeMsg(text, uid=222)))
        assert not run(handler.check(client, FakeMsg(text, uid=333)))
    assert run(admins.add_admin(87654321, by=111))
    for fn, text in cases:
        assert run(fn.handlers[0][0].check(client, FakeMsg(text, uid=87654321)))
    assert run(admins.remove_admin(87654321)) == 'ok'
    for fn, text in cases:
        assert not run(fn.handlers[0][0].check(client, FakeMsg(text, uid=87654321)))


def test_nonadmins_senderless_and_spoofed_context_do_not_manage_codes():
    async def go():
        col = vdb.db['codes']
        code = (await growth.create_codes(7, by=111))[0]
        before = await col.find_one({'code':code})
        for user in (333, 0):
            for fn, text in ((growth.gencode_cmd, '/gencode 30'), (growth.codes_cmd, '/codes'), (growth.delcode_cmd, f'/delcode {code}')):
                msg = FakeMsg(text, uid=user)
                await fn(FakeClient(), msg)
                assert not msg.replies
        anonymous = FakeMsg('/gencode 30', uid=111)
        anonymous.sender_chat = SimpleNamespace(id=-100)
        await growth.gencode_cmd(FakeClient(), anonymous)
        bot = FakeMsg('/gencode 30', uid=111); bot.from_user.is_bot = True
        await growth.gencode_cmd(FakeClient(), bot)
        wrong_chat = FakeMsg('/gencode 30', uid=111, chat_id=222)
        await growth.gencode_cmd(FakeClient(), wrong_chat)
        assert len(wrong_chat.replies) == 1 and 'private chat' in wrong_chat.replies[0]
        assert await col.count_documents({}) == 1 and await col.find_one({'code':code}) == before
    run(go())


def test_admin_group_commands_never_create_reveal_or_delete_codes():
    async def go():
        col = vdb.db['codes']; code = (await growth.create_codes(7, by=111))[0]
        before = await col.find_one({'code':code})
        for chat_type in (enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
            for fn, text in ((growth.gencode_cmd, '/gencode 30 2'), (growth.codes_cmd, '/codes'), (growth.delcode_cmd, f'/delcode {code}')):
                msg = FakeMsg(text, uid=222, chat_type=chat_type, chat_id=-1001)
                await fn(FakeClient(), msg)
                assert len(msg.replies)==1 and 'private chat' in msg.replies[0]
                assert code not in msg.replies[0] and 'treat it as exposed' in msg.replies[0]
        assert await col.count_documents({}) == 1 and await col.find_one({'code':code}) == before
    run(go())


def test_admin_dm_can_create_list_and_delete_and_nonadmin_can_redeem(monkeypatch):
    async def go():
        events=[]
        async def event(tag, body, **kwargs): events.append((tag,body))
        monkeypatch.setattr(botlog, 'event', event)
        client=FakeClient(); msg=FakeMsg('/gencode 7 1 2', uid=222)
        await growth.gencode_cmd(client, msg)
        row=await vdb.db['codes'].find_one({})
        code=row['code']; assert row['created_by']==222 and row['uses_left']==2 and code in msg.replies[0]
        listing=FakeMsg('/codes',uid=111);await growth.codes_cmd(client,listing);assert code in listing.replies[0]
        redeem=FakeMsg(f'/redeem {code}',uid=333);await growth.redeem_cmd(client,redeem)
        assert any('Code redeemed' in reply for reply in redeem.replies)
        assert await vdb.db['codes'].count_documents({'code':code,'uses_left':1})==1
        assert {tag for tag,_ in events} >= {'CodesCreated','Redeem'} and all(code not in body for _,body in events)
        deletion=FakeMsg(f'/delcode {code}',uid=222);await growth.delcode_cmd(client,deletion)
        assert 'deleted' in deletion.replies[0] and not await vdb.db['codes'].find_one({'code':code})
    run(go())


@pytest.mark.parametrize('command', ['/redeem', '/REDEEM@VidelBot', '/delcode', '/DELCODE@VidelBot'])
def test_first_message_audit_does_not_log_redeem_code(monkeypatch,command):
    events=[]
    async def event(tag,body,**kwargs): events.append((tag,body))
    monkeypatch.setattr(botlog, 'event', event)
    secret='VIDEL-ABCDE-12345'
    run(middleware.track_users(FakeClient(), FakeMsg(f'{command} {secret}',uid=54321)))
    assert any(tag=='NewUser' and '[redacted]' in body for tag,body in events)
    assert all(secret not in body for _,body in events)


def test_normal_first_message_still_logs_sanitized_text(monkeypatch):
    events=[]
    async def event(tag,body,**kwargs): events.append((tag,body))
    monkeypatch.setattr(botlog, 'event', event)
    run(middleware.track_users(FakeClient(),FakeMsg('hello <team>',uid=54322)))
    assert any(tag=='NewUser' and 'hello &lt;team&gt;' in body for tag,body in events)

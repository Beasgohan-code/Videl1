"""Offline premium-code pagination and uncertain outcome contracts."""
from datetime import datetime, timezone

import pytest
from pyrogram import enums
from tests.harness import FakeClient, FakeMsg, load_all, no_botapi, reset_db, run

load_all()
from core import growth, payments
from core.db import vdb


@pytest.fixture(autouse=True)
def clean():
    run(reset_db()); no_botapi()


class DbProxy:
    def __init__(self, db, codes): self.db, self.codes = db, codes
    def __getitem__(self, key): return self.codes if key == 'codes' else self.db[key]


def test_admin_pages_include_all_codes_with_stable_tie_breaker():
    async def go():
        col = vdb.db['codes']
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        await col.insert_many([{'_id': i, 'code': f'VIDEL-{i:05}-AAAAA', 'days': 30,
                                'uses_left': 1, 'uses': 1, 'created_at': timestamp} for i in range(65)])
        client = FakeClient(); pages=[]
        for n in (1, 2, 3):
            msg = FakeMsg('/codes' if n == 1 else f'/codes {n}', uid=222)
            await growth.codes_cmd(client, msg); pages.append(msg.replies[0])
            assert f'page {n}' in pages[-1] and 'Pages can shift' in pages[-1]
        assert 'Next: <code>/codes 2</code>' in pages[0]
        assert 'Previous: <code>/codes 1</code>' in pages[1] and 'Next: <code>/codes 3</code>' in pages[1]
        assert 'Previous: <code>/codes 2</code>' in pages[2] and 'Next:' not in pages[2]
        assert [page.count('VIDEL-') for page in pages] == [30, 30, 5]
        for i in range(65): assert sum(f'VIDEL-{i:05}-AAAAA' in page for page in pages) == 1
        assert 'VIDEL-00064-AAAAA' in pages[0] and 'VIDEL-00000-AAAAA' in pages[2]
        empty = FakeMsg('/codes 4', uid=222); await growth.codes_cmd(client, empty)
        assert 'No active codes on page 4' in empty.replies[0] and '/codes 1' in empty.replies[0]
    run(go())


@pytest.mark.parametrize('text', ['/codes 0', '/codes -1', '/codes 1001', '/codes abc', '/codes 1 extra', '/codes 99999999999999', '/codes １'])
def test_invalid_page_is_rejected_without_returning_codes(text):
    async def go():
        await growth.create_codes(7, by=222)
        message = FakeMsg(text, uid=222); await growth.codes_cmd(FakeClient(), message)
        assert 'Usage:' in message.replies[0] and 'VIDEL-' not in message.replies[0]
    run(go())


def test_code_list_escapes_historic_values_and_remains_private():
    async def go():
        await vdb.db['codes'].insert_one({'code':'<secret>&', 'days':'<unsafe>', 'uses_left':1,
                                          'uses':'<u>', 'created_at':datetime.now(timezone.utc)})
        msg = FakeMsg('/codes', uid=222); await growth.codes_cmd(FakeClient(), msg)
        assert '&lt;secret&gt;&amp;' in msg.replies[0] and '&lt;unsafe&gt;' in msg.replies[0] and '&lt;u&gt;' in msg.replies[0]
        msg = FakeMsg('/codes 1', uid=222, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
        await growth.codes_cmd(FakeClient(), msg)
        assert '&lt;secret&gt;' not in msg.replies[0] and 'private chat' in msg.replies[0]
    run(go())


def test_partial_creation_failure_is_uncertain_and_does_not_retry_or_delete(monkeypatch, caplog):
    async def go():
        original_db=vdb.db; col=original_db['codes']; secret='VIDEL-AAAAA-12345'; calls=0
        class Flaky:
            async def insert_one(self, doc):
                nonlocal calls
                calls += 1
                await col.insert_one(doc)
                if calls == 2: raise RuntimeError(f'private {secret}')
        monkeypatch.setattr(vdb, '_db', DbProxy(original_db, Flaky()))
        msg=FakeMsg('/gencode 30 3',uid=222);await growth.gencode_cmd(FakeClient(),msg)
        assert calls==2 and await col.count_documents({})==2
        assert 'may have been saved' in msg.replies[0] and 'no automatic retry' in msg.replies[0]
        assert secret not in msg.replies[0] and secret not in caplog.text
    run(go())


def test_lost_delivery_warns_privately_without_recreating_or_logging_code(caplog):
    async def go():
        class LostReply(FakeMsg):
            attempts=0
            async def reply_text(self,text,*args,**kwargs):
                self.attempts+=1
                if self.attempts==1:
                    self.first_text=text  # simulate a delivery whose acknowledgement was lost
                    raise RuntimeError('failed with '+text)
                return await super().reply_text(text,*args,**kwargs)
        msg=LostReply('/gencode 7',uid=222);await growth.gencode_cmd(FakeClient(),msg)
        doc=await vdb.db['codes'].find_one({});assert await vdb.db['codes'].count_documents({})==1
        assert doc['code'] in msg.first_text and doc['code'] not in msg.replies[0] and doc['code'] not in caplog.text
        assert 'confirm delivery' in msg.replies[0] and 'check /codes' in msg.replies[0] and msg.attempts==2
    run(go())


@pytest.mark.parametrize('after_grant', [False, True])
def test_redeem_grant_failure_is_uncertain_and_never_automatically_retried(monkeypatch,caplog,after_grant):
    async def go():
        code=(await growth.create_codes(7,by=222))[0]; original=payments.grant_premium; calls=0
        async def fail(uid, days, name):
            nonlocal calls
            calls+=1
            if after_grant: await original(uid, days, name)
            raise RuntimeError('private '+code)
        monkeypatch.setattr(payments,'grant_premium',fail)
        msg=FakeMsg(f'/redeem {code}',uid=333);await growth.redeem_cmd(FakeClient(),msg)
        row=await vdb.db['codes'].find_one({'code':code})
        assert row['uses_left']==0 and row['used_by']==[333] and calls==1
        assert 'Could not confirm redemption' in msg.replies[0] and '/myplan' in msg.replies[0]
        assert code not in msg.replies[0] and code not in caplog.text
        from database.db import db
        user=await db.col.find_one({'id':333})
        assert bool(user and user.get('is_premium')) is after_grant
    run(go())


def test_lost_delete_response_is_uncertain_and_no_retry(monkeypatch,caplog):
    async def go():
        original_db=vdb.db; col=original_db['codes'];code=(await growth.create_codes(7,by=222))[0];calls=0
        class Flaky:
            async def delete_one(self, query):
                nonlocal calls
                calls+=1;await col.delete_one(query);raise RuntimeError('private '+code)
        monkeypatch.setattr(vdb,'_db',DbProxy(original_db, Flaky()))
        msg=FakeMsg(f'/delcode {code}',uid=222);await growth.delcode_cmd(FakeClient(),msg)
        assert calls==1 and not await col.find_one({'code':code})
        assert 'result may be uncertain' in msg.replies[0] and 'absence alone does not prove' in msg.replies[0]
        assert code not in msg.replies[0] and code not in caplog.text
    run(go())


def test_failed_code_read_does_not_echo_database_diagnostics(monkeypatch, caplog):
    async def go():
        original_db=vdb.db; secret='VIDEL-ZZZZZ-00000'
        class Broken:
            def find(self, query): raise RuntimeError('database diagnostic '+secret)
        monkeypatch.setattr(vdb,'_db',DbProxy(original_db,Broken()))
        msg=FakeMsg('/codes 2',uid=222);await growth.codes_cmd(FakeClient(),msg)
        assert 'Could not load active codes' in msg.replies[0]
        assert secret not in msg.replies[0] and secret not in caplog.text
    run(go())


def test_code_delivery_and_warning_both_fail_without_leaking_secret(caplog):
    async def go():
        class NoDelivery(FakeMsg):
            attempts=0
            async def reply_text(self,text,*args,**kwargs):
                self.attempts+=1
                raise RuntimeError('delivery diagnostic '+text)
        msg=NoDelivery('/gencode 7',uid=222);await growth.gencode_cmd(FakeClient(),msg)
        row=await vdb.db['codes'].find_one({})
        assert row and msg.attempts==2 and row['code'] not in caplog.text
        assert await vdb.db['codes'].count_documents({})==1
    run(go())


def test_acknowledged_batch_audit_does_not_depend_on_message_delivery(monkeypatch):
    from core import botlog
    events=[]
    async def event(tag, body, **kwargs): events.append((tag, body))
    monkeypatch.setattr(botlog, 'event', event)
    class NoDelivery(FakeMsg):
        async def reply_text(self, text, *args, **kwargs): raise RuntimeError('delivery failed: '+text)
    async def go():
        await growth.gencode_cmd(FakeClient(), NoDelivery('/gencode 7',uid=222))
        row=await vdb.db['codes'].find_one({})
        assert row and [tag for tag,_ in events]==['CodesCreated']
        assert row['code'] not in events[0][1]
    run(go())


def test_audit_failure_does_not_prevent_delivering_saved_code(monkeypatch,caplog):
    from core import botlog
    async def fail(tag,body,**kwargs): raise RuntimeError('internal audit failure')
    monkeypatch.setattr(botlog,'event',fail)
    async def go():
        msg=FakeMsg('/gencode 7',uid=222);await growth.gencode_cmd(FakeClient(),msg)
        row=await vdb.db['codes'].find_one({})
        assert row and row['code'] in msg.replies[0] and 'internal audit failure' not in caplog.text
    run(go())


@pytest.mark.parametrize('duration', [-1, True, '7', None, 999999999])
def test_invalid_legacy_duration_does_not_consume_use(duration):
    async def go():
        code='VIDEL-BAD00-00000'
        await vdb.db['codes'].insert_one({'code':code, 'days':duration, 'uses_left':1, 'used_by':[]})
        msg=FakeMsg('/redeem '+code,uid=333)
        await growth.redeem_cmd(FakeClient(),msg)
        row=await vdb.db['codes'].find_one({'code':code})
        assert row['uses_left']==1 and row['used_by']==[]
        assert 'invalid duration' in msg.replies[0] and code not in msg.replies[0]
        from database.db import db
        assert not await db.col.find_one({'id':333})
    run(go())


def test_impossible_duration_is_rejected_before_creating_any_codes():
    async def go():
        msg=FakeMsg('/gencode 999999999 3',uid=222)
        await growth.gencode_cmd(FakeClient(),msg)
        assert 'days:' in msg.replies[0] and '0 (lifetime)' in msg.replies[0]
        assert await vdb.db['codes'].count_documents({}) == 0
        with pytest.raises(ValueError):
            await growth.create_codes(999999999)
        assert await vdb.db['codes'].count_documents({}) == 0
        lifetime=FakeMsg('/gencode 0',uid=222)
        await growth.gencode_cmd(FakeClient(),lifetime)
        assert (await vdb.db['codes'].find_one({}))['days']==0
    run(go())


@pytest.mark.parametrize('change', ['replace', 'edit'])
def test_code_replaced_or_duration_changed_during_claim_never_grants_stale_duration(monkeypatch,change):
    async def go():
        db=vdb.db;col=db['codes'];code=(await growth.create_codes(7,by=222))[0]
        original=await col.find_one({'code':code})
        class RacingCollection:
            async def find_one(self, query): return await col.find_one(query)
            async def update_one(self, query, update):
                if change == 'replace':
                    await col.delete_one({'_id':original['_id']})
                    await col.insert_one({'code':code,'days':7,'uses_left':1,'used_by':[]})
                else:
                    await col.update_one({'_id':original['_id']},{'$set':{'days':30}})
                return await col.update_one(query, update)
        monkeypatch.setattr(vdb,'_db',DbProxy(db,RacingCollection()))
        ok, result, _=await growth.redeem(333,code)
        assert not ok and 'may have changed' in result and code not in result
        row=await col.find_one({'code':code})
        assert row['days']==(7 if change == 'replace' else 30) and row['uses_left']==1 and row['used_by']==[]
        from database.db import db as saver_db
        assert not await saver_db.col.find_one({'id':333})
    run(go())


def test_future_expiry_overflow_is_reported_as_uncertain_without_refund(caplog):
    async def go():
        from database.db import db
        await db.add_user(333,'Tester')
        await db.add_premium(333,'9999-12-31')
        code=(await growth.create_codes(7,by=222))[0]
        msg=FakeMsg('/redeem '+code,uid=333)
        await growth.redeem_cmd(FakeClient(),msg)
        row=await vdb.db['codes'].find_one({'code':code})
        user=await db.col.find_one({'id':333})
        assert row['uses_left']==0 and row['used_by']==[333]
        assert user['premium_expiry']=='9999-12-31'
        assert 'Could not confirm redemption' in msg.replies[0] and code not in msg.replies[0]
        assert code not in caplog.text
    run(go())

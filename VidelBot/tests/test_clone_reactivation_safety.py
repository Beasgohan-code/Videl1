"""Offline reactivation safety; fake clients and mongomock, not live Telegram."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from tests.harness import FakeClient, FakeQuery, reset_db, run
from pyrogram import enums
from filestore.database.main_db import MainDB
from filestore.main_bot.plugins import clone_lifecycle as lifecycle
from filestore.worker_bot import engine as engine_module


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())


class Engine:
    def __init__(self): self.workers = {}; self.started = []; self.stopped = []
    def get_worker(self, bot_id): return self.workers.get(bot_id)
    async def start_worker(self, bot):
        self.started.append(bot['_id']); self.workers[bot['_id']] = object()
    async def stop_worker(self, bot_id): self.stopped.append(bot_id)


async def record(**overrides):
    row = {'_id':42, 'owner_id':5, 'is_active':False, 'is_deleted':False,
           'bot_username':'valid_bot', 'bot_token_encrypted':'never-share-secret',
           'last_active':datetime(2020,1,1,tzinfo=timezone.utc),
           'deactivated_reason':'inactive'}
    row.update(overrides)
    await MainDB().bots.insert_one(row)
    return await MainDB().get_bot(42)


@pytest.mark.parametrize('profile', ['filestore','group'])
def test_success_marks_registry_and_describes_local_observation(monkeypatch, profile):
    async def go():
        eng=Engine();monkeypatch.setattr(engine_module,'worker_engine',eng)
        await record(clone_profile=profile); q=FakeQuery('cl_on_42',uid=5)
        await lifecycle.reactivate_cb(FakeClient(),q)
        saved=await MainDB().get_bot(42)
        assert saved['is_active'] is True and 'deactivated_reason' not in saved
        assert eng.started==[42] and not eng.stopped
        assert 'local client was found' in q.message.edits[-1] and 'does not guarantee' in q.message.edits[-1]
        assert 'links' not in q.message.edits[-1] and 'never-share-secret' not in str(q.message.edits)
    run(go())


def test_legacy_absent_profile_and_last_activity_are_supported(monkeypatch):
    async def go():
        eng=Engine();monkeypatch.setattr(engine_module,'worker_engine',eng)
        await record();await MainDB().bots.update_one({'_id':42},{'$unset':{'last_active':''}})
        q=FakeQuery('cl_on_42',uid=5);await lifecycle.reactivate_cb(FakeClient(),q)
        assert (await MainDB().get_bot(42))['is_active'] is True and eng.started==[42]
    run(go())


def test_only_private_human_owner_and_non_deleted_record_can_attempt(monkeypatch):
    async def go():
        eng=Engine();monkeypatch.setattr(engine_module,'worker_engine',eng);await record()
        for q in [FakeQuery('cl_on_42',uid=6), FakeQuery('cl_on_42',uid=5)]:
            if q.from_user.id==5: q.message.chat.type=enums.ChatType.SUPERGROUP
            await lifecycle.reactivate_cb(FakeClient(),q)
            assert q.answers[-1][1] and not q.message.edits
        q=FakeQuery('cl_on_42',uid=5);q.from_user.is_bot=True
        await lifecycle.reactivate_cb(FakeClient(),q);assert q.answers[-1][1]
        q=FakeQuery('cl_on_42',uid=5);q.message.chat.id=6
        await lifecycle.reactivate_cb(FakeClient(),q);assert q.answers[-1][1]
        await MainDB().bots.update_one({'_id':42},{'$set':{'is_deleted':True}})
        q=FakeQuery('cl_on_42',uid=5);await lifecycle.reactivate_cb(FakeClient(),q);assert q.answers[-1][1]
        assert not eng.started
    run(go())


@pytest.mark.parametrize('changes', [{'is_active': True}, {'is_active': None}, {'is_deleted':None},
                                    {'clone_profile':'music'}, {'clone_profile':None},
                                    {'bot_token_encrypted':''}, {'bot_token_encrypted':{'bad':'secret'}}])
def test_stale_and_malformed_records_do_not_start(monkeypatch, changes):
    async def go():
        eng=Engine();monkeypatch.setattr(engine_module,'worker_engine',eng);await record(**changes)
        q=FakeQuery('cl_on_42',uid=5);await lifecycle.reactivate_cb(FakeClient(),q)
        assert q.answers[-1][1] and not eng.started and not q.message.edits
    run(go())


def test_existing_local_client_is_not_adopted_or_stopped(monkeypatch):
    async def go():
        eng=Engine();eng.workers[42]=object();monkeypatch.setattr(engine_module,'worker_engine',eng)
        await record();q=FakeQuery('cl_on_42',uid=5);await lifecycle.reactivate_cb(FakeClient(),q)
        assert not eng.started and not eng.stopped and not (await MainDB().get_bot(42))['is_active']
        assert 'could not be confirmed' in q.message.edits[-1]
    run(go())


@pytest.mark.parametrize('field,value', [('owner_id',6),('is_deleted',True),('is_active',True),
                                        ('clone_profile','group'),('bot_token_encrypted','changed-secret'),
                                        ('last_active',datetime(2025,1,1,tzinfo=timezone.utc))])
def test_concurrent_registry_change_is_not_overwritten_or_retried(monkeypatch,field,value):
    async def go():
        class Racing(Engine):
            async def start_worker(self, bot):
                await super().start_worker(bot)
                await MainDB().bots.update_one({'_id':42},{'$set':{field:value}})
        eng=Racing();monkeypatch.setattr(engine_module,'worker_engine',eng)
        await record(clone_profile='filestore');q=FakeQuery('cl_on_42',uid=5)
        await lifecycle.reactivate_cb(FakeClient(),q)
        saved=await MainDB().get_bot(42)
        assert saved[field]==(value.replace(tzinfo=None) if isinstance(value,datetime) else value)
        if field!='is_active': assert saved['is_active'] is False
        assert eng.started==[42] and not eng.stopped and 'could not be confirmed' in q.message.edits[-1]
        assert 'changed-secret' not in str(q.message.edits)
    run(go())


def test_start_error_and_uncertain_write_never_expose_errors_or_retry(monkeypatch,caplog):
    async def go():
        class Broken(Engine):
            async def start_worker(self, bot):
                await super().start_worker(bot);raise RuntimeError('secret-token')
        eng=Broken();monkeypatch.setattr(engine_module,'worker_engine',eng);await record()
        q=FakeQuery('cl_on_42',uid=5);await lifecycle.reactivate_cb(FakeClient(),q)
        assert eng.started==[42] and not eng.stopped and not (await MainDB().get_bot(42))['is_active']
        assert 'secret-token' not in str(q.message.edits) and 'could not be confirmed' in q.message.edits[-1]
        eng=Engine();monkeypatch.setattr(engine_module,'worker_engine',eng)
        async def uncertain(bot):
            await MainDB().bots.update_one({'_id':42},{'$set':{'is_active':True}})
            raise RuntimeError('secret-write-error')
        monkeypatch.setattr(lifecycle.main_db,'reactivate_inactive_if_unchanged',uncertain)
        q=FakeQuery('cl_on_42',uid=5);await lifecycle.reactivate_cb(FakeClient(),q)
        assert eng.started==[42] and not eng.stopped and (await MainDB().get_bot(42))['is_active']
        assert 'could not be confirmed' in q.message.edits[-1] and 'secret-write-error' not in str(q.message.edits)
        assert 'secret-token' not in caplog.text and 'secret-write-error' not in caplog.text
    run(go())


def test_unacknowledged_db_result_does_not_claim_success(monkeypatch):
    async def go():
        row = await record()
        class Collection:
            async def update_one(self, *args): return SimpleNamespace(acknowledged=False)
        db=MainDB.__new__(MainDB);db.bots=Collection()
        with pytest.raises(RuntimeError): await db.reactivate_inactive_if_unchanged(row)
    run(go())


def test_malformed_username_not_echoed_or_sent_in_log(monkeypatch):
    from filestore.main_bot.plugins import my_bots
    async def go():
        eng=Engine();monkeypatch.setattr(engine_module,'worker_engine',eng)
        events=[]
        async def log(client,tag,user,bot): events.append(bot)
        monkeypatch.setattr(my_bots,'_clone_log',log)
        await record(bot_username='<b>secret-token</b>');q=FakeQuery('cl_on_42',uid=5)
        await lifecycle.reactivate_cb(FakeClient(),q)
        assert events == [{'_id':42,'bot_username':'unavailable'}]
        assert 'secret-token' not in str(q.message.edits)
    run(go())


def test_missing_local_client_after_start_never_marks_active(monkeypatch):
    async def go():
        class Missing(Engine):
            async def start_worker(self, bot): self.started.append(bot['_id'])
        eng=Missing();monkeypatch.setattr(engine_module,'worker_engine',eng);await record()
        q=FakeQuery('cl_on_42',uid=5);await lifecycle.reactivate_cb(FakeClient(),q)
        assert not (await MainDB().get_bot(42))['is_active'] and eng.started==[42] and not eng.stopped
        assert 'could not be confirmed' in q.message.edits[-1]
    run(go())


def test_malformed_identity_record_fails_closed(monkeypatch):
    async def go():
        eng=Engine();monkeypatch.setattr(engine_module,'worker_engine',eng)
        for row in [dict(_id=True, owner_id=5, is_active=False, bot_token_encrypted='token'),
                    dict(_id=42, owner_id=True, is_active=False, bot_token_encrypted='token')]:
            async def load(bot_id): return row
            monkeypatch.setattr(lifecycle.main_db,'get_bot',load)
            q=FakeQuery('cl_on_1' if row['_id'] is True else 'cl_on_42', uid=5 if row['owner_id']==5 else 1)
            await lifecycle.reactivate_cb(FakeClient(),q)
            assert q.answers[-1][1] and not eng.started
    run(go())

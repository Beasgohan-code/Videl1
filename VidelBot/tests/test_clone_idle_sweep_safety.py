"""Idle sweep safety with fake workers and mock Mongo; no live services."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from tests.harness import FakeClient, reset_db, run
from bson import BSON, Int64
import config
from core import plans
from filestore.database.main_db import MainDB
from filestore.main_bot.plugins import clone_lifecycle as lifecycle

NOW=datetime(2026,9,28,12,tzinfo=timezone.utc)
BOT=2**33+42
OWNER=2**33+5


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    run(reset_db());lifecycle._sweep_holds.clear();lifecycle._sweep_claims.clear()
    monkeypatch.setattr(lifecycle,'_sweep_paused',False)
    monkeypatch.setattr(config,'CLONE_INACTIVE_DAYS',7)
    async def days(uid): return 7
    monkeypatch.setattr(plans,'clone_idle_days',days)
    yield
    lifecycle._sweep_holds.clear();lifecycle._sweep_claims.clear()


async def add(bot_id=BOT, owner=OWNER, **kw):
    row={'_id':bot_id,'owner_id':owner,'is_active':True,'is_deleted':False,
         'last_active':NOW-timedelta(days=9),'created_at':NOW-timedelta(days=30),
         'bot_username':'group_bot','bot_token_encrypted':'private-secret',
         'clone_profile':'group','settings':{'important':'kept'}}
    row.update(kw);await MainDB().bots.insert_one(row)
    return await MainDB().get_bot(bot_id)


class Engine:
    def __init__(self,*bots): self.workers={bid:object() for bid in bots};self.stops=[]
    def get_worker(self,bid): return self.workers.get(bid)
    async def stop_worker(self,bid): self.stops.append(bid);self.workers.pop(bid,None);return True


def notices(app): return [c for c in app.called('send_message') if c[1][0]==OWNER]


def test_success_confirms_local_stop_and_preserves_settings(monkeypatch):
    async def go():
        await add();eng=Engine(BOT);app=FakeClient()
        result=await lifecycle.sweep(app,eng,now=NOW);saved=await MainDB().get_bot(BOT)
        assert result['deactivated']==[BOT] and eng.stops==[BOT] and not eng.workers
        assert saved['is_active'] is False and saved['deactivated_reason']=='inactive'
        assert saved['settings']=={'important':'kept'} and saved['bot_token_encrypted']=='private-secret'
        assert notices(app) and 'local client stop was confirmed' in notices(app)[-1][1][1]
        assert not lifecycle._sweep_holds and 'private-secret' not in str(app.calls)
    run(go())


@pytest.mark.parametrize('stop_result',[False,None,1])
def test_unconfirmed_stop_never_marks_inactive_or_retries(monkeypatch,stop_result):
    async def go():
        await add();eng=Engine(BOT)
        async def stop(bid): eng.stops.append(bid);return stop_result
        eng.stop_worker=stop;app=FakeClient()
        assert not (await lifecycle.sweep(app,eng,now=NOW))['deactivated']
        assert (await MainDB().get_bot(BOT))['is_active'] is True and BOT in lifecycle._sweep_holds
        assert not notices(app)
        await lifecycle.sweep(app,eng,now=NOW);assert eng.stops==[BOT]
    run(go())


def test_failed_stop_does_not_block_an_unrelated_clone(monkeypatch):
    async def go():
        await add();await add(bot_id=BOT+1)
        class Partial(Engine):
            async def stop_worker(self,bid):
                self.stops.append(bid)
                if bid==BOT: raise RuntimeError('stop-secret')
                self.workers.pop(bid,None);return True
        eng=Partial(BOT,BOT+1);app=FakeClient();result=await lifecycle.sweep(app,eng,now=NOW)
        assert result['deactivated']==[BOT+1] and eng.stops==[BOT,BOT+1] and lifecycle._sweep_holds=={BOT}
    run(go())


@pytest.mark.parametrize('field,value', [('owner_id',OWNER+1),('is_deleted',True),('is_active',False),
                                        ('clone_profile','filestore'),('bot_token_encrypted','changed-secret'),
                                        ('last_active',NOW),('created_at',NOW)])
def test_concurrent_changes_after_stop_do_not_get_overwritten(monkeypatch,field,value):
    async def go():
        await add()
        class Racing(Engine):
            async def stop_worker(self,bid):
                result=await super().stop_worker(bid)
                await MainDB().bots.update_one({'_id':bid},{'$set':{field:value}})
                return result
        eng=Racing(BOT);app=FakeClient();result=await lifecycle.sweep(app,eng,now=NOW)
        saved=await MainDB().get_bot(BOT)
        assert not result['deactivated'] and BOT in lifecycle._sweep_holds
        assert saved[field]==(value.replace(tzinfo=None) if isinstance(value,datetime) else value)
        assert 'deactivated_reason' not in saved and not notices(app)
    run(go())


def test_changes_before_stop_skip_without_hold(monkeypatch):
    async def go():
        original=await add();eng=Engine(BOT);app=FakeClient()
        async def list_stale():
            await MainDB().bots.update_one({'_id':BOT},{'$set':{'last_active':NOW}})
            return [original]
        monkeypatch.setattr(lifecycle.main_db,'get_active_clone_sweep_records',list_stale)
        assert not (await lifecycle.sweep(app,eng,now=NOW))['deactivated']
        assert not eng.stops and not lifecycle._sweep_holds and not notices(app)
    run(go())


def test_replaced_client_after_stop_does_not_mark_inactive(monkeypatch):
    async def go():
        await add()
        class Replacement(Engine):
            async def stop_worker(self,bid):
                await super().stop_worker(bid);self.workers[bid]=object();return True
        eng=Replacement(BOT);app=FakeClient();await lifecycle.sweep(app,eng,now=NOW)
        assert (await MainDB().get_bot(BOT))['is_active'] is True and BOT in lifecycle._sweep_holds
        assert not notices(app)
    run(go())


def test_missing_local_client_is_not_a_confirmed_fleet_stop():
    async def go():
        await add();eng=Engine();app=FakeClient();result=await lifecycle.sweep(app,eng,now=NOW)
        assert not result['deactivated'] and not eng.stops and not lifecycle._sweep_holds
        assert (await MainDB().get_bot(BOT))['is_active'] is True and not notices(app)
    run(go())


def test_uncertain_db_write_may_have_committed_but_never_claims_success(monkeypatch,caplog):
    async def go():
        await add();eng=Engine(BOT);app=FakeClient()
        async def ambiguous(bot):
            await MainDB().bots.update_one({'_id':BOT},{'$set':{'is_active':False}})
            raise RuntimeError('secret-write-error')
        monkeypatch.setattr(lifecycle.main_db,'deactivate_inactive_if_unchanged',ambiguous)
        assert not (await lifecycle.sweep(app,eng,now=NOW))['deactivated']
        assert (await MainDB().get_bot(BOT))['is_active'] is False and BOT in lifecycle._sweep_holds
        assert not notices(app) and 'secret-write-error' not in caplog.text
    run(go())


def test_cancelled_stop_holds_id_and_releases_claim(monkeypatch):
    async def go():
        await add();started=asyncio.Event()
        class Slow(Engine):
            async def stop_worker(self,bid): self.stops.append(bid);started.set();await asyncio.Event().wait()
        eng=Slow(BOT);task=asyncio.create_task(lifecycle.sweep(FakeClient(),eng,now=NOW));await started.wait()
        await lifecycle.sweep(FakeClient(),eng,now=NOW);assert eng.stops==[BOT]
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert BOT in lifecycle._sweep_holds and BOT not in lifecycle._sweep_claims
        await lifecycle.sweep(FakeClient(),eng,now=NOW);assert eng.stops==[BOT]
    run(go())


def test_hold_capacity_fails_closed(monkeypatch):
    async def go():
        monkeypatch.setattr(lifecycle,'_SWEEP_HOLD_LIMIT',1)
        lifecycle._hold_sweep(BOT);lifecycle._hold_sweep(BOT+1)
        assert lifecycle._sweep_paused is True and lifecycle._sweep_holds=={BOT}
        await add(bot_id=BOT+2);eng=Engine(BOT+2)
        assert not (await lifecycle.sweep(FakeClient(),eng,now=NOW))['deactivated'] and not eng.stops
    run(go())


def test_unacknowledged_conditional_write_is_not_success():
    async def go():
        class Collection:
            async def update_one(self,*args): return SimpleNamespace(acknowledged=False)
        db=MainDB.__new__(MainDB);db.bots=Collection()
        row=BSON(BSON.encode({'_id':BOT,'owner_id':OWNER,'is_active':True})).decode()
        assert type(row['_id']) is Int64 and type(row['owner_id']) is Int64
        with pytest.raises(RuntimeError): await db.deactivate_inactive_if_unchanged(row)
    run(go())


def test_malformed_username_and_exception_are_not_disclosed(monkeypatch,caplog):
    async def go():
        await add(bot_username='<a href="secret">bad</a>');eng=Engine(BOT);app=FakeClient()
        await lifecycle.sweep(app,eng,now=NOW)
        assert 'secret' not in str(app.calls) and '<a href' not in str(app.calls)
        assert 'private-secret' not in caplog.text
    run(go())


@pytest.mark.parametrize('value',[None,True,-1,1000000000,'7'])
def test_invalid_plan_never_falls_back_to_shorter_idle_timeout(monkeypatch,value):
    async def go():
        async def bad(uid): return value
        monkeypatch.setattr(plans,'clone_idle_days',bad)
        await add();eng=Engine(BOT);app=FakeClient()
        assert not (await lifecycle.sweep(app,eng,now=NOW))['deactivated']
        assert not eng.stops and not notices(app)
    run(go())


def test_failed_plan_lookup_skips_instead_of_using_free_plan(monkeypatch,caplog):
    async def go():
        async def bad(uid): raise RuntimeError('plan-secret')
        monkeypatch.setattr(plans,'clone_idle_days',bad)
        await add();eng=Engine(BOT);app=FakeClient()
        assert not (await lifecycle.sweep(app,eng,now=NOW))['deactivated']
        assert not eng.stops and 'plan-secret' not in caplog.text
    run(go())


def test_failed_audit_send_does_not_prevent_owner_notification(monkeypatch,caplog):
    from core import botlog
    async def go():
        await add();eng=Engine(BOT);app=FakeClient()
        async def broken(*args,**kwargs): raise RuntimeError('log-secret')
        monkeypatch.setattr(botlog,'event',broken)
        result=await lifecycle.sweep(app,eng,now=NOW)
        assert result['deactivated']==[BOT] and notices(app)
        assert 'log-secret' not in caplog.text
    run(go())


def test_real_bson_decoded_large_ids_and_field_presence_in_conditional_write():
    from mongomock_motor import AsyncMongoMockClient
    async def go():
        db=MainDB.__new__(MainDB);db.bots=AsyncMongoMockClient().diagnostics.clones
        row=BSON(BSON.encode({'_id':BOT,'owner_id':OWNER,'is_active':True,
                             'last_active':NOW-timedelta(days=9)})).decode()
        assert type(row['_id']) is Int64 and type(row['owner_id']) is Int64
        await db.bots.insert_one(row)
        await db.bots.update_one({'_id':BOT},{'$set':{'clone_profile':'group'}})
        assert await db.deactivate_inactive_if_unchanged(row) is False
        latest=await db.bots.find_one({'_id':BOT})
        assert await db.deactivate_inactive_if_unchanged(latest) is True
        assert (await db.bots.find_one({'_id':BOT}))['is_active'] is False
    run(go())


def test_sweep_records_projection_omits_unrelated_fields():
    from mongomock_motor import AsyncMongoMockClient
    async def go():
        db = MainDB.__new__(MainDB)
        db.bots = AsyncMongoMockClient().diagnostics.clones
        await db.bots.insert_one({
            '_id': BOT, 'owner_id': OWNER, 'is_active': True, 'is_deleted': False,
            'last_active': NOW - timedelta(days=9), 'created_at': NOW - timedelta(days=30),
            'bot_username': 'group_bot', 'bot_token_encrypted': 'private-secret',
            'clone_profile': 'group', 'settings': {'important': 'kept'},
            'session': 'secret-session',
        })
        await db.bots.insert_one({'_id': BOT + 1, 'owner_id': OWNER, 'is_active': False})
        records = await db.get_active_clone_sweep_records()
        assert len(records) == 1
        assert records[0]['_id'] == BOT
        assert 'settings' not in records[0] and 'session' not in records[0]
        assert records[0]['bot_token_encrypted'] == 'private-secret'
    run(go())


def test_music_idle_sweep_requests_desired_stop_without_claiming_remote_stop():
    async def go():
        await add(clone_profile="music", music_runtime_state="running",
                  music_runtime_updated_at=NOW)
        eng = Engine()  # the controller has no local Music client
        app = FakeClient()
        result = await lifecycle.sweep(app, eng, now=NOW)
        saved = await MainDB().get_bot(BOT)
        message = notices(app)[-1][1][1]

        assert result["deactivated"] == [BOT]
        assert eng.stops == []
        assert saved["is_active"] is False
        # The separate worker's report is retained as an observation; the
        # dashboard must not reinterpret this request as a confirmed stop.
        assert saved["music_runtime_state"] == "running"
        assert "requests Music stop" in message
        assert "not proof" in message
        assert "local client stop was confirmed" not in message

    run(go())

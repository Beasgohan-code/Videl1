"""Offline owner health-report and repair boundaries; no real workers or Mongo."""
from types import SimpleNamespace

import pytest
from tests.harness import FakeClient, FakeMsg, run
from filestore.main_bot.plugins import owner_cmds
from filestore.worker_bot import engine


class Status:
    def __init__(self): self.text = None
    async def finish(self, text): self.text = text


class Worker:
    def __init__(self, result=None, failure=None): self.result, self.failure = result, failure
    async def get_me(self):
        if self.failure: raise self.failure
        return self.result


class Engine:
    def __init__(self, workers=None, stop_result=True):
        self.workers = workers or {}; self.stop_result = stop_result; self.stopped = []
    def get_worker(self, bid): return self.workers.get(bid)
    async def stop_worker(self, bid): self.stopped.append(bid); return self.stop_result


def execute(monkeypatch, records, worker_engine, command='/check'):
    async def get_all(): return records
    monkeypatch.setattr(owner_cmds.main_db, 'get_all_bots', get_all)
    monkeypatch.setattr(engine, 'worker_engine', worker_engine)
    status = Status(); run(owner_cmds._check_bots(FakeClient(), FakeMsg(command,uid=111), status))
    return status.text


def test_health_report_is_bounded_escaped_and_does_not_echo_errors(monkeypatch):
    token='123456:SECRET_TOKEN_SHOULD_NOT_APPEAR'
    docs=[{'_id':1,'bot_username':'valid_bot','is_active':True},
          {'_id':2,'bot_username':'<a href="https://bad">','is_active':True},
          {'_id':3,'bot_username':token,'is_active':True},
          {'_id':token,'bot_username':token,'is_active':True},
          {'_id':4,'bot_username':'disabled_bot','is_active':False},
          {'_id':5,'bot_username':'odd_bot','is_active':None}]
    docs += [{'_id':i,'bot_username':'n'*32,'is_active':True} for i in range(100,180)]
    eng=Engine({1:Worker(SimpleNamespace(id=1)),2:Worker(failure=RuntimeError(token))})
    report=execute(monkeypatch,docs,eng)
    assert 'matching identity: <code>1</code>' in report
    assert 'check unconfirmed: <code>1</code>' in report
    assert 'inactive, no local client: <code>1</code>' in report
    assert 'Unknown or changed: <code>2</code>' in report
    assert 'Username unavailable' in report and 'Invalid record' in report
    assert token not in report and 'https://bad' not in report and '<a href' not in report
    assert 'Local process snapshot only' in report and 'fleet-wide watchdog' in report
    assert len(report)<4096 and report.count('<blockquote expandable>')==report.count('</blockquote>')
    assert '… and 69 more' in report and eng.stopped==[]


def test_health_requires_matching_identity_and_ignores_deleted(monkeypatch):
    docs=[{'_id':1,'bot_username':'valid_bot','is_active':True},
          {'_id':2,'bot_username':'other_bot','is_active':True},
          {'_id':3,'bot_username':'gone_bot','is_active':True,'is_deleted':True}]
    eng=Engine({1:Worker(SimpleNamespace(id=999)),2:Worker(SimpleNamespace(id=None))})
    report=execute(monkeypatch,docs,eng)
    assert 'matching identity: <code>0</code>' in report and 'Unknown or changed: <code>2</code>' in report
    assert 'gone_bot' not in report and not eng.stopped


@pytest.mark.parametrize('value', [None, {}, 'not a list'])
def test_invalid_registry_response_is_not_an_empty_fleet(monkeypatch, value):
    report=execute(monkeypatch,value,Engine())
    assert 'No health result is available' in report


def test_registry_exception_does_not_leak_message(monkeypatch):
    async def fail(): raise RuntimeError('secret:token')
    monkeypatch.setattr(owner_cmds.main_db,'get_all_bots',fail)
    s=Status();run(owner_cmds._check_bots(FakeClient(),FakeMsg('/check',uid=111),s))
    assert 'No health result is available' in s.text and 'secret:token' not in s.text


def test_failed_stop_blocks_watchdog_and_reports_partial(monkeypatch):
    import watchdog
    events=[]
    class Dog:
        async def heal_workers(self): events.append('heal'); return 1
    monkeypatch.setattr(watchdog,'dog',Dog())
    eng=Engine({2:Worker(failure=RuntimeError('secret:token'))},stop_result=False)
    report=execute(monkeypatch,[{'_id':2,'bot_username':'dead_bot','is_active':True}],eng,'/check fix')
    assert eng.stopped==[2] and not events
    assert 'Repair was not confirmed and may be partial' in report and 'secret:token' not in report


def test_replaced_worker_during_probe_is_not_stopped(monkeypatch):
    import watchdog
    events=[]
    class Dog:
        async def heal_workers(self): events.append('heal'); return 1
    monkeypatch.setattr(watchdog,'dog',Dog())
    eng=Engine()
    class Replaced:
        async def get_me(self):
            eng.workers[2]=Worker(SimpleNamespace(id=2));raise RuntimeError('secret')
    eng.workers[2]=Replaced()
    report=execute(monkeypatch,[{'_id':2,'bot_username':'dead_bot','is_active':True}],eng,'/check fix')
    assert 'Unknown or changed: <code>1</code>' in report and not eng.stopped and not events


def test_watchdog_failure_is_uncertain_not_success(monkeypatch):
    import watchdog
    class Dog:
        async def heal_workers(self): raise RuntimeError('secret-token')
    monkeypatch.setattr(watchdog,'dog',Dog())
    eng=Engine()
    report=execute(monkeypatch,[{'_id':2,'bot_username':'missing_bot','is_active':True}],eng,'/check fix')
    assert 'Repair was not confirmed and may be partial' in report and 'secret-token' not in report


def test_successful_repair_label_does_not_claim_verified_state(monkeypatch):
    import watchdog
    class Dog:
        async def heal_workers(self): return 3
    monkeypatch.setattr(watchdog,'dog',Dog())
    eng=Engine({2:Worker(failure=TimeoutError())})
    report=execute(monkeypatch,[{'_id':2,'bot_username':'dead_bot','is_active':True}],eng,'/check fix')
    assert eng.stopped==[2] and 'reported <code>3</code> starts/restarts' in report
    assert 'not independently verified' in report


def test_replaced_worker_before_repair_skips_stop_and_heal(monkeypatch):
    import watchdog
    events=[]
    class Dog:
        async def heal_workers(self): events.append('heal'); return 1
    monkeypatch.setattr(watchdog,'dog',Dog())
    class Changing(Engine):
        def __init__(self): super().__init__({2:Worker(failure=RuntimeError('secret'))});self.reads=0
        def get_worker(self,bid):
            self.reads+=1
            if self.reads==3:self.workers[bid]=Worker(SimpleNamespace(id=bid))
            return self.workers.get(bid)
    eng=Changing()
    report=execute(monkeypatch,[{'_id':2,'bot_username':'dead_bot','is_active':True}],eng,'/check fix')
    assert not eng.stopped and not events
    assert 'Repair was not confirmed and may be partial' in report


def test_probe_cancellation_propagates_without_repair(monkeypatch):
    import asyncio
    from tests.harness import run
    import watchdog
    events=[]
    class Dog:
        async def heal_workers(self): events.append('heal'); return 1
    monkeypatch.setattr(watchdog,'dog',Dog())
    async def check():
        entered=asyncio.Event()
        class Slow:
            async def get_me(self): entered.set();await asyncio.Event().wait()
        eng=Engine({2:Slow()});monkeypatch.setattr(engine,'worker_engine',eng)
        async def all_bots(): return [{'_id':2,'bot_username':'slow_bot','is_active':True}]
        monkeypatch.setattr(owner_cmds.main_db,'get_all_bots',all_bots)
        status=Status();task=asyncio.create_task(owner_cmds._check_bots(FakeClient(),FakeMsg('/check fix',uid=111),status))
        await entered.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not eng.stopped and not events and status.text is None
    run(check())

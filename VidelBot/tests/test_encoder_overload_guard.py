"""Offline regressions for Encoder overload admission guards."""
import pytest

from tests.harness import FakeMsg, load_all, no_botapi, reset_db, run

load_all()

import config  # noqa: E402
from VideoEncoder import data as queue  # noqa: E402
from VideoEncoder.utils import scheduler  # noqa: E402
import VideoEncoder.plugins.encode as enc_plugin  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    run(reset_db())
    no_botapi()
    saved = list(queue)
    queue.clear()
    scheduler._running.clear()
    monkeypatch.setattr(config, "ENC_MAX_TASKS_FREE", 10)
    monkeypatch.setattr(config, "ENC_MAX_TASKS_PRO", 10)
    monkeypatch.setattr(config, "ENC_MAX_QUEUE_TOTAL", 50)
    monkeypatch.setattr(config, "MEMORY_PAUSE_NEW_TASKS_PCT", 0)
    yield
    queue[:] = saved
    scheduler._running.clear()
    scheduler.MODES.clear()
    scheduler.EXTRA.clear()
    scheduler.PRIO.clear()


def _msg(uid=5):
    return FakeMsg("/dl", uid=uid)


def test_queue_full_uses_global_cap(monkeypatch):
    monkeypatch.setattr(config, "ENC_MAX_QUEUE_TOTAL", 2)
    queue.extend([_msg(1), _msg(2)])
    assert scheduler.queue_full() is True
    queue.pop()
    assert scheduler.queue_full() is False


def test_memory_overloaded_reads_threshold(monkeypatch):
    monkeypatch.setattr(config, "MEMORY_PAUSE_NEW_TASKS_PCT", 90)
    monkeypatch.setattr("core.sysinfo.memory_pressure", lambda _pct: (True, 94.5))
    assert scheduler.memory_overloaded() is True
    monkeypatch.setattr("core.sysinfo.memory_pressure", lambda _pct: (False, 10.0))
    assert scheduler.memory_overloaded() is False


def test_enqueue_rejects_when_global_queue_is_full(monkeypatch):
    async def fake_handle(message, mode):
        raise AssertionError("full queue must not start a task")

    monkeypatch.setattr(enc_plugin, "handle_tasks", fake_handle)
    monkeypatch.setattr(config, "ENC_MAX_QUEUE_TOTAL", 1)
    queue.append(_msg(9))
    m = _msg(5)
    assert run(enc_plugin._enqueue(m, "tg")) is False
    assert "queue is full" in m.replies[-1]
    assert scheduler.position(m) == 0


def test_enqueue_rejects_new_work_when_memory_overloaded_and_busy(monkeypatch):
    async def fake_handle(message, mode):
        raise AssertionError("overloaded bot must not start a task")

    monkeypatch.setattr(enc_plugin, "handle_tasks", fake_handle)
    monkeypatch.setattr(config, "MEMORY_PAUSE_NEW_TASKS_PCT", 90)
    monkeypatch.setattr("core.sysinfo.memory_pressure", lambda _pct: (True, 99.0))
    queue.append(_msg(9))
    m = _msg(5)
    assert run(enc_plugin._enqueue(m, "tg")) is False
    assert "overloaded" in m.replies[-1]
    assert scheduler.position(m) == 0


def test_restore_queue_respects_global_cap(monkeypatch):
    from VideoEncoder.utils import tasks

    msgs = [_msg(5), _msg(6), _msg(7)]
    for m in msgs:
        run(scheduler.persist(m, "tg", {}, False))
    queue.clear()
    monkeypatch.setattr(config, "ENC_MAX_QUEUE_TOTAL", 2)

    class App:
        async def get_messages(self, chat, mid):
            return next(m for m in msgs if m.chat.id == chat and m.id == mid)

    async def fake_dispatch(spawn_all=False):
        return None

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(tasks, "dispatch", fake_dispatch)
    monkeypatch.setattr(scheduler.asyncio, "sleep", no_sleep)
    assert run(scheduler.restore_queue(App())) == 2
    assert len(queue) == 2


def test_disk_low_uses_min_free_disk_threshold(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(config, "MIN_FREE_DISK_GB", 2)
    monkeypatch.setattr(scheduler.shutil, "disk_usage", lambda _path: SimpleNamespace(free=int(1.5 * 1073741824)))
    assert scheduler.disk_low() is True
    monkeypatch.setattr(scheduler.shutil, "disk_usage", lambda _path: SimpleNamespace(free=int(3 * 1073741824)))
    assert scheduler.disk_low() is False


def test_enqueue_rejects_when_disk_is_low(monkeypatch):
    async def fake_handle(message, mode):
        raise AssertionError("low disk must not start a task")

    monkeypatch.setattr(enc_plugin, "handle_tasks", fake_handle)
    monkeypatch.setattr(scheduler, "disk_low", lambda: True)
    m = _msg(5)
    assert run(enc_plugin._enqueue(m, "tg")) is False
    assert "Low disk space" in m.replies[-1]
    assert scheduler.position(m) == 0


def test_enqueue_still_starts_one_task_when_idle_under_memory_pressure(monkeypatch):
    started = []

    async def fake_handle(message, mode):
        started.append(mode)

    monkeypatch.setattr(enc_plugin, "handle_tasks", fake_handle)
    monkeypatch.setattr(config, "MEMORY_PAUSE_NEW_TASKS_PCT", 90)
    monkeypatch.setattr("core.sysinfo.memory_pressure", lambda _pct: (True, 99.0))
    m = _msg(5)
    assert run(enc_plugin._enqueue(m, "tg")) is True
    assert started == ["tg"]

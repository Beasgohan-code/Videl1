"""Process runner lifecycle tests for the Music clone worker."""
import asyncio
import time

from tests.harness import run


def test_music_clone_runner_reconciles_then_stops_on_signal():
    from music_worker.__main__ import serve

    class FakeEngine:
        active_count = 0

        def __init__(self, stop_event):
            self.calls = []
            self.stop_event = stop_event

        async def start_all(self):
            self.calls.append("start_all")
            self.active_count = 2

        async def reconcile(self):
            self.calls.append("reconcile")
            self.stop_event.set()
            return {"started": [3], "stopped": [], "failed": []}

        async def stop_all(self):
            self.calls.append("stop_all")
            return 2

    async def scenario():
        event = asyncio.Event()
        engine = FakeEngine(event)
        await serve(engine, event, sync_interval=0.001)
        assert engine.calls == ["start_all", "reconcile", "stop_all"]

    run(scenario())


def test_music_clone_runner_cleans_up_when_initial_reconcile_fails():
    from music_worker.__main__ import serve

    class BrokenEngine:
        def __init__(self):
            self.stopped = False

        async def start_all(self):
            raise RuntimeError("database unavailable")

        async def stop_all(self):
            self.stopped = True
            return 0

    async def scenario():
        engine = BrokenEngine()
        try:
            await serve(engine, asyncio.Event(), sync_interval=0.001)
        except RuntimeError:
            pass
        else:
            raise AssertionError("startup failure should propagate")
        assert engine.stopped is True

    run(scenario())


def test_music_clone_activity_middleware_keeps_idle_lifecycle_fresh(monkeypatch):
    import music_worker.engine as module

    class FakeApp:
        def __init__(self):
            self.handlers = []

        def add_handler(self, handler, group=0):
            self.handlers.append((handler, group))

    async def scenario():
        app = FakeApp()
        seen = []

        async def update_last_active(bot_id):
            seen.append(bot_id)

        monkeypatch.setattr(module.main_db, "update_last_active", update_last_active)
        module.main_db._last_active_written[811] = time.monotonic() - 61
        module._attach_activity_middleware(app, 811)
        assert len(app.handlers) == 2 and all(group == -1 for _, group in app.handlers)

        try:
            await app.handlers[0][0].callback(None, object())
        except module.ContinuePropagation:
            pass
        else:
            raise AssertionError("middleware must propagate updates to command handlers")
        await asyncio.sleep(0)
        assert seen == [811]

    run(scenario())


def test_music_clone_sync_interval_is_bounded(monkeypatch):
    from music_worker.__main__ import _sync_interval

    monkeypatch.setenv("MUSIC_WORKER_SYNC_SECONDS", "15")
    assert _sync_interval() == 15
    for value in ("1", "301", "invalid"):
        monkeypatch.setenv("MUSIC_WORKER_SYNC_SECONDS", value)
        try:
            _sync_interval()
        except SystemExit as exc:
            assert "MUSIC_WORKER_SYNC_SECONDS" in str(exc)
        else:
            raise AssertionError(f"invalid sync interval was accepted: {value}")

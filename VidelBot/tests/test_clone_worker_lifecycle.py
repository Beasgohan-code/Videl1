"""Fail-closed stop retries for standalone Group and Music clone workers."""
from tests.harness import run


class FlakyClient:
    def __init__(self, fail_stops=1):
        self.is_connected = True
        self.is_initialized = True
        self.fail_stops = fail_stops
        self.stop_calls = 0

    async def stop(self, block=True):
        self.stop_calls += 1
        if self.stop_calls <= self.fail_stops:
            raise RuntimeError("stop acknowledgement uncertain")
        self.is_connected = False
        self.is_initialized = False


def test_group_worker_retries_uncertain_client_stop():
    from group_worker.engine import GroupWorkerEngine

    async def scenario():
        engine = GroupWorkerEngine(store=object())
        client = FlakyClient()
        engine.workers[7001] = client

        assert await engine.stop_worker(7001) is False
        assert engine.active_count == 0
        assert engine._uncertain_stops[7001] is client
        assert await engine.stop_worker(7001) is True
        assert client.stop_calls == 2
        assert 7001 not in engine._uncertain_stops

    run(scenario())


def test_music_worker_stops_bot_and_assistant_and_retries_only_uncertain_client():
    from music_worker.engine import MusicWorkerEngine

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        bot_client = FlakyClient(fail_stops=0)
        assistant = FlakyClient(fail_stops=1)
        engine.workers[7002] = bot_client
        engine._assistants[7002] = assistant

        assert await engine.stop_worker(7002) is False
        assert bot_client.stop_calls == 1 and not bot_client.is_connected
        assert assistant.stop_calls == 1 and assistant.is_connected
        assert engine._uncertain_stops[7002] == (assistant,)
        assert await engine.stop_worker(7002) is True
        assert assistant.stop_calls == 2 and not assistant.is_connected
        assert 7002 not in engine._uncertain_stops

    run(scenario())


def test_cancelled_group_stop_keeps_client_fenced_for_retry():
    import asyncio
    from group_worker.engine import GroupWorkerEngine

    class CancelOnceClient(FlakyClient):
        async def stop(self, block=True):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise asyncio.CancelledError()
            self.is_connected = False
            self.is_initialized = False

    async def scenario():
        engine = GroupWorkerEngine(store=object())
        client = CancelOnceClient(fail_stops=0)
        engine.workers[7003] = client

        try:
            await engine.stop_worker(7003)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("stop cancellation should propagate")
        assert engine._uncertain_stops[7003] is client
        assert await engine.stop_worker(7003) is True
        assert client.stop_calls == 2

    run(scenario())


def test_cancelled_music_stop_preserves_both_client_handles():
    import asyncio
    from music_worker.engine import MusicWorkerEngine

    class CancelOnceClient(FlakyClient):
        async def stop(self, block=True):
            self.stop_calls += 1
            if self.stop_calls == 1:
                raise asyncio.CancelledError()
            self.is_connected = False
            self.is_initialized = False

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        bot_client = CancelOnceClient(fail_stops=0)
        assistant = FlakyClient(fail_stops=0)
        engine.workers[7004] = bot_client
        engine._assistants[7004] = assistant

        try:
            await engine.stop_worker(7004)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("stop cancellation should propagate")
        assert engine._uncertain_stops[7004] == (assistant, bot_client)
        assert await engine.stop_worker(7004) is True
        assert bot_client.stop_calls == 2 and assistant.stop_calls == 2

    run(scenario())


def test_music_worker_retries_uncertain_voice_player_shutdown():
    from music_worker.engine import MusicWorkerEngine

    class FlakyPlayer:
        def __init__(self):
            self.ready = True
            self.stop_calls = 0

        async def shutdown(self):
            self.stop_calls += 1
            if self.stop_calls == 1:
                return False
            self.ready = False
            return True

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        bot_client = FlakyClient(fail_stops=0)
        assistant = FlakyClient(fail_stops=0)
        player = FlakyPlayer()
        engine.workers[7005] = bot_client
        engine._assistants[7005] = assistant
        engine._players[7005] = player

        assert await engine.stop_worker(7005) is False
        assert player.stop_calls == 1 and player.ready
        assert bot_client.stop_calls == assistant.stop_calls == 1
        assert engine._uncertain_players[7005] is player

        assert await engine.stop_worker(7005) is True
        assert player.stop_calls == 2 and not player.ready
        assert bot_client.stop_calls == assistant.stop_calls == 1
        assert 7005 not in engine._uncertain_players

    run(scenario())


def test_music_worker_bounds_player_shutdown_and_keeps_timeout_fenced(monkeypatch):
    import asyncio
    import music_worker.engine as engine_module
    from music_worker.engine import MusicWorkerEngine

    class HangingPlayer:
        ready = True

        async def shutdown(self):
            await asyncio.Event().wait()

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        player = HangingPlayer()
        engine._players[7006] = player
        assert await engine.stop_worker(7006) is False
        assert engine._uncertain_players[7006] is player
        assert await engine.stop_worker(7006) is False

    monkeypatch.setattr(engine_module, "_PLAYER_STOP_TIMEOUT", 0.01)
    run(scenario())

"""Unit tests for the standalone clone Music worker (stage 1)."""
from tests.harness import run
from tests.test_group_worker_logic import _FakeCollection


def test_music_queue_flow_and_duration_format():
    from music_worker.logic import PlayQueue, format_duration

    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"
    assert format_duration(0) == "live"

    queue = PlayQueue(max_tracks=3)
    assert queue.enqueue(-1, {"title": "A"}) == 0
    assert queue.enqueue(-1, {"title": "B"}) == 1
    assert queue.enqueue(-1, {"title": "C"}) == 2
    assert queue.now_playing(-1)["title"] == "A"
    assert [t["title"] for t in queue.upcoming(-1)] == ["B", "C"]
    assert queue.enqueue(-2, {"title": "Other chat"}) == 0

    assert queue.remove(-1, 2)["title"] == "B"
    assert queue.skip(-1)["title"] == "C"
    assert queue.clear(-1) == 1
    assert queue.now_playing(-1) is None


def test_music_worker_store_scoping_and_commands():
    from music_worker.handlers import MUSIC_WORKER_COMMANDS
    from music_worker.store import MusicWorkerStore

    names = [n for n, _ in MUSIC_WORKER_COMMANDS]
    for cmd in ("play", "queue", "skip", "stop", "settings"):
        assert cmd in names

    async def scenario():
        store = MusicWorkerStore({"music_worker_settings": _FakeCollection()})
        await store.update_settings(1, -5, {"dj_mode": True, "bogus": 1})
        mine = await store.get_settings(1, -5)
        other = await store.get_settings(2, -5)
        assert mine["dj_mode"] is True and "bogus" not in mine
        assert other["dj_mode"] is False

    run(scenario())


def test_music_worker_engine_stop_all_stops_every_clone():
    from tests.harness import run as _run
    from music_worker.engine import MusicWorkerEngine

    class FakeApp:
        is_connected = False
        is_initialized = False

        def __init__(self):
            self.stopped = False

        async def stop(self, block=True):
            self.stopped = True

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        apps = {7: FakeApp(), 8: FakeApp()}
        engine.workers.update(apps)
        assert await engine.stop_all() == 2
        assert engine.active_count == 0
        assert all(a.stopped for a in apps.values())

    _run(scenario())


def test_music_player_compat_shim_and_controls():
    from tests.harness import run as _run
    from music_worker.player import VoicePlayer, ensure_compat

    assert ensure_compat() is True
    import pyrogram.errors as errors
    assert errors.GroupcallForbidden is errors.GroupCallInvalid
    assert errors.GroupcallInvalid is errors.GroupCallInvalid
    assert ensure_compat() is True  # idempotent

    calls_made = []

    class FakeCalls:
        async def start(self):
            calls_made.append(("start",))

        async def play(self, chat_id, stream):
            calls_made.append(("play", chat_id, stream))

        async def pause(self, chat_id):
            calls_made.append(("pause", chat_id))
            return True

        async def resume(self, chat_id):
            calls_made.append(("resume", chat_id))
            return False

        async def leave_call(self, chat_id):
            calls_made.append(("leave", chat_id))

        async def change_volume_call(self, chat_id, volume):
            calls_made.append(("volume", chat_id, volume))

    async def scenario():
        player = VoicePlayer(FakeCalls(), stream_factory=lambda s: ("stream", s))
        assert await player.start() is True and player.ready is True
        await player.play(-100, "/tmp/a.ogg")
        assert await player.pause(-100) is True
        assert await player.resume(-100) is False
        await player.set_volume(-100, 150)
        await player.stop(-100)
        assert calls_made == [
            ("start",), ("play", -100, ("stream", "/tmp/a.ogg")), ("pause", -100),
            ("resume", -100), ("volume", -100, 150), ("leave", -100)]

        class DeadCalls(FakeCalls):
            async def start(self):
                raise RuntimeError("no calls")

        dead = VoicePlayer(DeadCalls(), stream_factory=lambda s: s)
        assert await dead.start() is False and dead.ready is False

    _run(scenario())


def test_music_handlers_report_ready_player_and_pause_commands():
    from music_worker.handlers import MUSIC_WORKER_COMMANDS, _BotHandlers

    names = [n for n, _ in MUSIC_WORKER_COMMANDS]
    assert "pause" in names and "resume" in names
    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None, player=object())
    assert handlers._player_ready() is True
    assert "ready" in handlers._settings_text({"dj_mode": False, "volume": 100})
    idle = _BotHandlers(bot_id=42, owner_id=1, store=None, player=None)
    assert idle._player_ready() is False
    assert "not set up yet" in idle._settings_text({"dj_mode": False, "volume": 100})


def test_music_resolver_search_and_direct_link():
    from tests.harness import run as _run
    from music_worker.resolver import resolve_title

    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, target, download=False):
            seen["target"] = target
            if target.startswith("ytsearch1:"):
                return {"entries": [{"title": "Found Song", "duration": 187,
                                     "url": "https://stream.example/audio"}]}
            return {"title": "Direct", "duration": 60, "formats": [
                {"url": "https://stream.example/low", "vcodec": "none"},
                {"url": "https://stream.example/high", "vcodec": "none"}]}

    async def scenario():
        hit = await resolve_title("some song", extractor_factory=FakeYDL)
        assert hit == {"title": "Found Song", "duration": 187,
                       "source": "https://stream.example/audio"}
        assert seen["target"] == "ytsearch1:some song"
        direct = await resolve_title("https://youtu.be/xyz", extractor_factory=FakeYDL)
        assert direct["source"] == "https://stream.example/high"  # best audio-only
        assert await resolve_title("   ", extractor_factory=FakeYDL) is None

        class DeadYDL(FakeYDL):
            def extract_info(self, target, download=False):
                raise RuntimeError("network down")

        assert await resolve_title("anything", extractor_factory=DeadYDL) is None

    _run(scenario())

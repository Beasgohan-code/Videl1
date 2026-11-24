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
        store = MusicWorkerStore({"music_worker_settings": _FakeCollection(),
                                  "music_worker_queues": _FakeCollection(),
                                  "music_worker_playlists": _FakeCollection()})
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


def test_music_auto_advance_and_manual_suppression():
    from tests.harness import run as _run
    from music_worker.handlers import _BotHandlers

    played = []

    class FakePlayer:
        ready = True

        async def play(self, chat_id, source, ffmpeg_parameters=None):
            played.append((chat_id, source))

        async def stop(self, chat_id):
            played.append((chat_id, "STOP"))

    async def scenario():
        handlers = _BotHandlers(bot_id=42, owner_id=1, store=None, player=FakePlayer())
        handlers.queue.enqueue(-5, {"title": "A", "source": "a"})
        handlers.queue.enqueue(-5, {"title": "B", "source": "b"})
        handlers.queue.enqueue(-5, {"title": "C", "source": "c"})

        await handlers._advance(-5)  # A ended naturally → B plays
        assert played == [(-5, "b")]
        assert handlers.queue.now_playing(-5)["title"] == "B"

        handlers._note_manual(-5)    # user pressed /skip; stale end event…
        await handlers._advance(-5)  # …must not advance a second time
        assert handlers.queue.now_playing(-5)["title"] == "B"
        assert played == [(-5, "b")]

        handlers._suppress_until.clear()
        await handlers._advance(-5)  # B ended → C plays
        assert handlers.queue.now_playing(-5)["title"] == "C"
        await handlers._advance(-5)  # C ended → queue empty, player stops
        assert played[-1] == (-5, "STOP")
        assert handlers.queue.now_playing(-5) is None

    _run(scenario())


def test_music_player_binds_stream_end_events():
    from tests.harness import run as _run
    from music_worker.player import VoicePlayer

    registered = {}

    class FakeCalls:
        def on_update(self, filters=None):
            def deco(fn):
                registered["handler"] = fn
                return fn
            return deco

        async def start(self):
            pass

    async def scenario():
        seen = []

        async def on_end(chat_id):
            seen.append(chat_id)

        player = VoicePlayer(FakeCalls(), stream_factory=lambda s: s)
        player.bind_auto_advance(on_end)
        assert await player.start() is True
        assert "handler" in registered

        class Update:
            chat_id = -777

        await registered["handler"](None, Update())
        assert seen == [-777]

    _run(scenario())


def test_music_queue_persistence_roundtrip():
    from tests.harness import run as _run
    from music_worker.logic import PlayQueue
    from music_worker.store import MusicWorkerStore
    from tests.test_group_worker_logic import _FakeCollection

    queue = PlayQueue(max_tracks=2)
    queue.enqueue(-1, {"title": "A"})
    queue.enqueue(-1, {"title": "B"})
    queue.enqueue(-1, {"title": "C"})
    snap = queue.snapshot(-1)
    assert [t["title"] for t in snap] == ["A", "C"]  # cap keeps current + newest
    fresh = PlayQueue()
    fresh.restore(-1, snap)
    assert [t["title"] for t in fresh.snapshot(-1)] == ["A", "C"]
    fresh.restore(-1, [])
    assert fresh.snapshot(-1) == []

    async def scenario():
        store = MusicWorkerStore({
            "music_worker_settings": _FakeCollection(),
            "music_worker_queues": _FakeCollection(),
            "music_worker_playlists": _FakeCollection(),
        })
        await store.save_queue(9, -3, [
            {"title": "Song", "duration": 200, "requested_by": "Ana",
             "source": "https://x/y", "junk": 1}])
        loaded = await store.load_queue(9, -3)
        assert loaded == [{"title": "Song", "duration": 200,
                           "requested_by": "Ana", "source": "https://x/y"}]
        assert await store.load_queue(9, -4) == []     # other chat
        assert await store.load_queue(8, -3) == []     # other clone
        await store.save_queue(9, -3, [])
        assert await store.load_queue(9, -3) == []     # cleared deletes the doc

    _run(scenario())


def test_music_stage5_queue_ops_and_ffmpeg_params():
    from music_worker.logic import PlayQueue, ffmpeg_params

    q = PlayQueue()
    q.enqueue(-1, {"title": "A"})
    q.enqueue(-1, {"title": "B"})
    q.play_now(-1, {"title": "FORCE"})
    assert [t["title"] for t in q.snapshot(-1)] == ["FORCE", "A", "B"]
    assert q.clear_upcoming(-1) == 2
    assert [t["title"] for t in q.snapshot(-1)] == ["FORCE"]

    assert ffmpeg_params() is None
    assert ffmpeg_params(seek=65) == "-ss 65"
    assert ffmpeg_params(speed=1.5) == "-filter:a atempo=1.5"
    assert ffmpeg_params(seek=10, speed=0.5) == "-ss 10 -filter:a atempo=0.5"


def test_music_stage5_resolver_many_and_video():
    from tests.harness import run as _run
    from music_worker.resolver import resolve_many, resolve_title

    captured = {}

    class FakeYDL:
        def __init__(self, opts):
            captured["format"] = opts.get("format")

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, target, download=False):
            captured["target"] = target
            return {"entries": [{"title": "One", "duration": 11},
                                {"title": "Two", "duration": 22}, None]}

    async def scenario():
        hits = await resolve_many("lofi beats", 3, extractor_factory=FakeYDL)
        assert captured["target"] == "ytsearch3:lofi beats"
        assert [h["title"] for h in hits] == ["One", "Two"]
        assert all(h["source"] is None for h in hits)  # resolved lazily at play
        await resolve_title("x", extractor_factory=FakeYDL, video=True)
        assert captured["format"] == "best"  # video mode keeps video formats

    _run(scenario())


def test_music_stage5_playlists_and_commands():
    from tests.harness import run as _run
    from music_worker.handlers import MUSIC_WORKER_COMMANDS, _BotHandlers
    from music_worker.store import MusicWorkerStore
    from tests.test_group_worker_logic import _FakeCollection

    names = [n for n, _ in MUSIC_WORKER_COMMANDS]
    for cmd in ("vplay", "playforce", "seek", "speed", "autoplay", "clear",
                "pcreate", "padd", "premove", "pview", "pplay", "pdelete",
                "addchannel", "delchannel", "cplay", "cskip", "cstop"):
        assert cmd in names

    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None, player=None)
    text = handlers._settings_text({"dj_mode": False, "volume": 100, "autoplay": True,
                                    "speed": 1.5, "linked_channel": -100})
    assert "Autoplay" in text and "1.5×" in text and "Channel" in text

    class Cursor:
        def __init__(self, docs):
            self._docs = docs

        def __aiter__(self):
            async def gen():
                for d in self._docs:
                    yield d
            return gen()

    class PlaylistCollection(_FakeCollection):
        def find(self, query):
            docs = [dict(d) for d in self.docs.values()
                    if all(d.get(k) == v for k, v in query.items())]
            return Cursor(docs)

    async def scenario():
        store = MusicWorkerStore({
            "music_worker_settings": _FakeCollection(),
            "music_worker_queues": _FakeCollection(),
            "music_worker_playlists": PlaylistCollection(),
        })
        await store.create_playlist(5, 77, "Chill")
        await store.set_playlist_tracks(5, 77, "chill", [{"title": "A"}, {"title": "B"}])
        mine = await store.get_playlist(5, 77, "CHILL")
        assert [t["title"] for t in mine["tracks"]] == ["A", "B"]
        listing = await store.list_playlists(5, 77)
        assert [p["name"] for p in listing] == ["chill"]
        assert await store.list_playlists(5, 78) == []     # other user
        assert await store.delete_playlist(5, 77, "chill") is True
        assert await store.get_playlist(5, 77, "chill") is None

    _run(scenario())


def test_music_player_panel_markup_and_text():
    from music_worker.handlers import _BotHandlers

    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None, player=object())
    assert "Nothing playing" in handlers._panel_text(-5)

    handlers.queue.enqueue(-5, {"title": "First Song", "duration": 200,
                                "requested_by": "Ana", "source": "x"})
    handlers.queue.enqueue(-5, {"title": "Second", "duration": 90,
                                "requested_by": "Ben", "source": "y"})
    handlers._pos_set(-5, handlers.queue.now_playing(-5), playing=False)  # paused
    text = handlers._panel_text(-5)
    assert "NOW PLAYING" in text and "First Song" in text and "Second" in text
    assert "<pre>" in text

    markup = handlers._panel_markup(-5)
    datas = [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]
    assert "mw:42:ctl:-5:resume" in datas      # paused → resume toggle
    assert "mw:42:ctl:-5:skip" in datas
    assert "mw:42:ctl:-5:stop" in datas
    assert "mw:42:ctl:-5:queue" in datas

    handlers._pos_set(-5, handlers.queue.now_playing(-5), playing=True)
    datas = [b.callback_data for row in handlers._panel_markup(-5).inline_keyboard
             for b in row if b.callback_data]
    assert "mw:42:ctl:-5:pause" in datas       # playing → pause toggle

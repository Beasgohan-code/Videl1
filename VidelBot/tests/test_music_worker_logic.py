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

    assert queue.remove(-1, 1)["title"] == "B"  # first upcoming; current stays protected
    assert queue.skip(-1)["title"] == "C"
    assert queue.clear(-1) == 1
    assert queue.now_playing(-1) is None


def test_music_settings_normalization_rejects_malformed_values():
    from music_worker.logic import default_settings, normalize_settings

    assert normalize_settings({
        "dj_mode": "yes", "autoplay": False, "volume": True,
        "loop_mode": "forever", "speed": float("nan"), "linked_channel": "-1005",
    }) == {**default_settings(), "autoplay": False}
    assert normalize_settings({"volume": 999, "speed": 12.0})["volume"] == 200
    assert normalize_settings({"volume": 999, "speed": 12.0})["speed"] == 4.0


def test_music_worker_store_scoping_and_commands():
    from music_worker.handlers import MUSIC_WORKER_COMMANDS
    from music_worker.store import MusicWorkerStore

    names = [n for n, _ in MUSIC_WORKER_COMMANDS]
    for cmd in ("play", "queue", "skip", "stop", "remove", "move", "settings"):
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

        await store.update_settings(1, 0, {"autoplay": True, "loop_mode": "all", "volume": 130})
        inherited = await store.get_settings(1, -5)
        assert inherited["autoplay"] is True and inherited["loop_mode"] == "all"
        assert inherited["volume"] == 130
        await store.update_settings(1, -5, {"autoplay": False})
        overridden = await store.get_settings(1, -5)
        still_inherited = await store.get_settings(1, -6)
        assert overridden["autoplay"] is False and overridden["loop_mode"] == "all"
        assert still_inherited["autoplay"] is True

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

        async def stop(self):
            calls_made.append(("shutdown",))

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
        assert await player.shutdown() is True and not player.ready
        assert calls_made[-1] == ("shutdown",)
        assert await player.shutdown() is True  # idempotent after confirmed shutdown
        assert calls_made.count(("shutdown",)) == 1

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
    for cmd in ("vplay", "playforce", "seek", "speed", "autoplay", "clear", "loop", "shuffle",
                "pcreate", "padd", "premove", "pview", "pplay", "pdelete",
                "addchannel", "delchannel", "cplay", "cskip", "cstop"):
        assert cmd in names

    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None, player=None)
    settings = {"dj_mode": False, "volume": 100, "autoplay": True,
                "loop_mode": "one", "speed": 1.5, "linked_channel": -100}
    text = handlers._settings_text(settings)
    assert "Autoplay" in text and "Repeat" in text and "one" in text
    assert "1.5×" in text and "Channel" in text
    markup = handlers._settings_markup(settings)
    loop_button = next(button for row in markup.inline_keyboard for button in row
                       if button.callback_data == "mw:42:loop")
    assert "one" in loop_button.text

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
    assert "mw:42:ctl:-5:loop" in datas
    assert "mw:42:ctl:-5:shuffle" in datas

    handlers._pos_set(-5, handlers.queue.now_playing(-5), playing=True)
    datas = [b.callback_data for row in handlers._panel_markup(-5).inline_keyboard
             for b in row if b.callback_data]
    assert "mw:42:ctl:-5:pause" in datas       # playing → pause toggle


def test_music_queue_repeat_and_shuffle_never_mutate_current(monkeypatch):
    from music_worker import logic
    from music_worker.logic import PlayQueue

    queue = PlayQueue()
    queue.enqueue(-90, {"title": "current"})
    queue.enqueue(-90, {"title": "next-a"})
    queue.enqueue(-90, {"title": "next-b"})

    monkeypatch.setattr(logic.random, "shuffle", lambda items: items.reverse())
    assert queue.shuffle_upcoming(-90) == 2
    assert [t["title"] for t in queue.snapshot(-90)] == ["current", "next-b", "next-a"]

    assert queue.advance(-90, "one")["title"] == "current"
    assert [t["title"] for t in queue.snapshot(-90)] == ["current", "next-b", "next-a"]
    assert queue.advance(-90, "all")["title"] == "next-b"
    assert [t["title"] for t in queue.snapshot(-90)] == ["next-b", "next-a", "current"]
    assert queue.advance(-90, "off")["title"] == "next-a"
    assert [t["title"] for t in queue.snapshot(-90)] == ["next-a", "current"]


def test_music_natural_advance_obeys_repeat_mode_and_persists_queue():
    from music_worker.handlers import _BotHandlers

    class Store:
        def __init__(self, mode):
            self.mode = mode
            self.saved = []

        async def get_settings(self, bot_id, chat_id):
            return {"loop_mode": self.mode}

        async def save_queue(self, bot_id, chat_id, tracks):
            self.saved.append([dict(track) for track in tracks])

    class Player:
        ready = True

        def __init__(self):
            self.played = []
            self.stopped = []

        async def play(self, chat_id, source, ffmpeg_parameters=None):
            self.played.append((chat_id, source))

        async def stop(self, chat_id):
            self.stopped.append(chat_id)

    async def scenario():
        for mode, expected_queue, expected_source in (
            ("one", ["A", "B"], "a"),
            ("all", ["B", "A"], "b"),
            ("off", ["B"], "b"),
        ):
            store, player = Store(mode), Player()
            handlers = _BotHandlers(bot_id=42, owner_id=1, store=store, player=player)
            handlers.queue.enqueue(-91, {"title": "A", "source": "a"})
            handlers.queue.enqueue(-91, {"title": "B", "source": "b"})
            await handlers._advance(-91)
            assert [track["title"] for track in handlers.queue.snapshot(-91)] == expected_queue
            assert player.played == [(-91, expected_source)]
            assert store.saved[-1] == handlers.queue.snapshot(-91)

    run(scenario())


def test_music_queue_edits_protect_current_track():
    from music_worker.logic import PlayQueue

    queue = PlayQueue()
    for title in ("current", "one", "two", "three"):
        queue.enqueue(-92, {"title": title})

    assert queue.remove(-92, 0) is None
    assert queue.remove(-92, 1)["title"] == "one"
    assert queue.move_upcoming(-92, 2, 1) is True
    assert [track["title"] for track in queue.snapshot(-92)] == ["current", "three", "two"]
    assert queue.move_upcoming(-92, 0, 1) is False
    assert queue.move_upcoming(-92, 1, 9) is False
    assert queue.move_upcoming(-92, 1, 1) is True
    assert queue.now_playing(-92)["title"] == "current"


def test_music_engine_reconciles_desired_state_and_publishes_status(monkeypatch):
    import music_worker.engine as module
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
        old = FakeApp()
        engine.workers[7] = old
        desired = {"_id": 8, "owner_id": 5, "clone_profile": "music", "is_active": True}
        states = []

        async def active(profile):
            assert profile == "music"
            return [desired]

        async def get_bot(bot_id):
            assert bot_id == 8
            return desired

        async def set_state(bot_id, state):
            states.append((bot_id, state))
            return True

        async def fake_start(doc):
            engine.workers[doc["_id"]] = FakeApp()

        monkeypatch.setattr(module.main_db, "get_active_bots_for_profile", active)
        monkeypatch.setattr(module.main_db, "get_bot", get_bot)
        monkeypatch.setattr(module.main_db, "set_music_runtime_state", set_state)
        monkeypatch.setattr(engine, "_start_locked", fake_start)
        result = await engine.reconcile()
        assert result == {"started": [8], "stopped": [7], "failed": []}
        assert old.stopped is True and set(engine.workers) == {8}
        assert (8, "running") in states and (7, "stopped") in states

    run(scenario())


def test_music_reconcile_reloads_changed_credentials_only_after_confirmed_stop(monkeypatch):
    import music_worker.engine as module
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
        loaded = {"_id": 91, "owner_id": 5, "clone_profile": "music", "is_active": True,
                  "bot_token_encrypted": "enc:bot", "music_session_encrypted": "enc:old-assistant"}
        desired = {**loaded, "music_session_encrypted": "enc:new-assistant"}
        old = FakeApp()
        engine.workers[91] = old
        engine._loaded_configs[91] = engine._config_fingerprint(loaded)
        assert engine._config_fingerprint({**loaded, "owner_id": 6}) != engine._loaded_configs[91]
        assert engine._config_fingerprint({**loaded, "bot_token_encrypted": "enc:rotated"}) != engine._loaded_configs[91]
        states = []

        async def active(profile):
            assert profile == "music"
            return [desired]
        async def get_bot(bot_id):
            assert bot_id == 91
            return desired
        async def set_state(bot_id, state):
            states.append((bot_id, state))
            return True
        async def fake_start(doc):
            assert doc is desired
            engine.workers[91] = FakeApp()

        monkeypatch.setattr(module.main_db, "get_active_bots_for_profile", active)
        monkeypatch.setattr(module.main_db, "get_bot", get_bot)
        monkeypatch.setattr(module.main_db, "set_music_runtime_state", set_state)
        monkeypatch.setattr(engine, "_start_locked", fake_start)
        result = await engine.reconcile()
        assert result == {"started": [91], "stopped": [91], "failed": []}
        assert old.stopped and engine.workers[91] is not old
        assert engine._loaded_configs[91] == engine._config_fingerprint(desired)
        assert (91, "stopped") in states and (91, "running") in states

    run(scenario())


def test_music_reconcile_retries_uncertain_old_client_before_credential_refresh(monkeypatch):
    import music_worker.engine as module
    from music_worker.engine import MusicWorkerEngine

    class FlakyApp:
        is_connected = True
        is_initialized = True
        def __init__(self):
            self.stop_calls = 0
            self.allow_stop = False
        async def stop(self, block=True):
            self.stop_calls += 1
            if self.allow_stop:
                self.is_connected = False
                self.is_initialized = False

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        old_config = {"_id": 92, "owner_id": 5, "clone_profile": "music", "is_active": True,
                      "bot_token_encrypted": "enc:bot", "music_session_encrypted": "enc:old"}
        desired = {**old_config, "music_session_encrypted": "enc:new"}
        old = FlakyApp()
        engine.workers[92] = old
        engine._loaded_configs[92] = engine._config_fingerprint(old_config)
        starts = []
        async def active(_profile): return [desired]
        async def get_bot(_bot_id): return desired
        async def set_state(_bot_id, _state): return True
        async def fake_start(doc):
            starts.append(doc)
            engine.workers[92] = type("NewApp", (), {"is_connected": False, "is_initialized": False})()
        monkeypatch.setattr(module.main_db, "get_active_bots_for_profile", active)
        monkeypatch.setattr(module.main_db, "get_bot", get_bot)
        monkeypatch.setattr(module.main_db, "set_music_runtime_state", set_state)
        monkeypatch.setattr(engine, "_start_locked", fake_start)

        first = await engine.reconcile()
        assert first == {"started": [], "stopped": [], "failed": [92]}
        assert starts == [] and 92 in engine._uncertain_stops
        old.allow_stop = True
        second = await engine.reconcile()
        assert second == {"started": [92], "stopped": [92], "failed": []}
        assert len(starts) == 1 and 92 not in engine._uncertain_stops

    run(scenario())


def test_concurrent_music_reconciliation_passes_are_serialized(monkeypatch):
    import asyncio
    import music_worker.engine as module
    from music_worker.engine import MusicWorkerEngine

    class FakeApp:
        is_connected = False
        is_initialized = False
        async def stop(self, block=True):
            pass

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        desired = {"_id": 93, "owner_id": 5, "clone_profile": "music", "is_active": True,
                   "bot_token_encrypted": "enc:bot", "music_session_encrypted": "enc:assistant"}
        first_read = asyncio.Event()
        continue_read = asyncio.Event()
        active_calls = []
        starts = []
        async def active(_profile):
            active_calls.append(1)
            if len(active_calls) == 1:
                first_read.set()
                await continue_read.wait()
            return [desired]
        async def get_bot(_bot_id): return desired
        async def set_state(_bot_id, _state): return True
        async def fake_start(doc):
            starts.append(doc)
            engine.workers[93] = FakeApp()
        monkeypatch.setattr(module.main_db, "get_active_bots_for_profile", active)
        monkeypatch.setattr(module.main_db, "get_bot", get_bot)
        monkeypatch.setattr(module.main_db, "set_music_runtime_state", set_state)
        monkeypatch.setattr(engine, "_start_locked", fake_start)
        task1 = asyncio.create_task(engine.reconcile())
        await first_read.wait()
        task2 = asyncio.create_task(engine.reconcile())
        await asyncio.sleep(0)
        continue_read.set()
        results = await asyncio.gather(task1, task2)
        assert len(starts) == 1 and len(active_calls) == 2
        assert results[0]["started"] == [93] and results[1]["started"] == []

    run(scenario())


def test_music_reconcile_cleans_orphaned_assistant_before_start(monkeypatch):
    import music_worker.engine as module
    from music_worker.engine import MusicWorkerEngine

    class FakeApp:
        is_connected = False
        is_initialized = False
        def __init__(self): self.stopped = False
        async def stop(self, block=True): self.stopped = True

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        desired = {"_id": 94, "owner_id": 5, "clone_profile": "music", "is_active": True,
                   "bot_token_encrypted": "enc:bot", "music_session_encrypted": "enc:session"}
        orphan = FakeApp()
        engine._assistants[94] = orphan
        async def active(_profile): return [desired]
        async def get_bot(_bot_id): return desired
        async def set_state(_bot_id, _state): return True
        async def fake_start(doc): engine.workers[94] = FakeApp()
        monkeypatch.setattr(module.main_db, "get_active_bots_for_profile", active)
        monkeypatch.setattr(module.main_db, "get_bot", get_bot)
        monkeypatch.setattr(module.main_db, "set_music_runtime_state", set_state)
        monkeypatch.setattr(engine, "_start_locked", fake_start)
        result = await engine.reconcile()
        assert result == {"started": [94], "stopped": [94], "failed": []}
        assert orphan.stopped and 94 not in engine._assistants
    run(scenario())


def test_music_desired_stop_clears_failure_backoff_for_next_owner_restart(monkeypatch):
    import music_worker.engine as module
    from music_worker.engine import MusicWorkerEngine

    class FakeApp:
        is_connected = False
        is_initialized = False
        async def stop(self, block=True): pass

    async def scenario():
        engine = MusicWorkerEngine(store=object())
        desired = {"_id": 95, "owner_id": 5, "clone_profile": "music", "is_active": True,
                   "bot_token_encrypted": "enc:bot", "music_session_encrypted": "enc:session"}
        registry = [desired]
        calls = []
        async def active(_profile): return list(registry)
        async def get_bot(_bot_id): return desired
        async def set_state(_bot_id, _state): return True
        async def fake_start(doc):
            calls.append(doc)
            if len(calls) == 1:
                raise RuntimeError("startup failed")
            engine.workers[95] = FakeApp()
        monkeypatch.setattr(module.main_db, "get_active_bots_for_profile", active)
        monkeypatch.setattr(module.main_db, "get_bot", get_bot)
        monkeypatch.setattr(module.main_db, "set_music_runtime_state", set_state)
        monkeypatch.setattr(engine, "_start_locked", fake_start)

        failed = await engine.reconcile()
        assert failed["failed"] == [95] and 95 in engine._start_failures
        registry.clear()
        assert await engine.reconcile() == {"started": [], "stopped": [], "failed": []}
        assert 95 not in engine._start_failures
        registry.append(desired)
        restarted = await engine.reconcile()
        assert restarted["started"] == [95] and len(calls) == 2
    run(scenario())

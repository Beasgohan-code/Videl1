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

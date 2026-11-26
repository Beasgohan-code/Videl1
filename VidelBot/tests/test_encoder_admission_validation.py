"""Offline regressions for concurrent Encoder commands and malformed timestamps."""
import asyncio
from types import SimpleNamespace

import pytest

from tests.harness import FakeMsg, load_all, run

load_all()

import config  # noqa: E402
from VideoEncoder import data  # noqa: E402
from VideoEncoder.plugins import encode as plugin  # noqa: E402
from VideoEncoder.utils import ffcmd, scheduler, tasks  # noqa: E402


class Msg(FakeMsg):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


def video_cmd(text, unique):
    msg = Msg(text, uid=5)
    msg.reply_to_message = Msg(None, uid=5)
    msg.reply_to_message.video = SimpleNamespace(file_unique_id=unique)
    return msg


@pytest.fixture(autouse=True)
def clean_queue():
    saved = list(data)
    data.clear()
    scheduler._running.clear()
    yield
    for message in list(data):
        scheduler.finish(message)
    data[:] = saved
    scheduler._running.clear()


@pytest.mark.parametrize("same_file,limit", [(True, 5), (False, 1)])
def test_concurrent_plan_lookups_admit_only_one(monkeypatch, same_file, limit):
    monkeypatch.setattr(config, "ENC_MAX_TASKS_FREE", limit)
    monkeypatch.setattr(config, "ENCODER_WORKERS", 1)
    arrived = 0
    ready = asyncio.Event()
    persisted = []
    started = []

    async def plan(uid):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await ready.wait()
        return False

    async def persist(message, *args):
        persisted.append(message)
        await asyncio.sleep(0)  # the second handler may run while the first persists

    async def handle(message, mode):
        started.append(message)

    monkeypatch.setattr(plugin, "_is_pro", plan)
    monkeypatch.setattr(scheduler, "persist", persist)
    monkeypatch.setattr(plugin, "handle_tasks", handle)
    first = video_cmd("/dl", "file-a")
    second = video_cmd("/dl", "file-a" if same_file else "file-b")

    async def submit():
        return await asyncio.gather(plugin._enqueue(first, "tg"), plugin._enqueue(second, "tg"))

    results = run(submit())
    assert sorted(results) == [False, True]
    assert len(data) == len(persisted) == len(started) == 1
    rejected = first if not results[0] else second
    assert ("Already in your queue" if same_file else "limit 1") in rejected.replies[-1]


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "1e999", "1e308:1e308", "1:nan"])
def test_nonfinite_and_overflow_timestamps_are_rejected(value):
    assert ffcmd.parse_timestamp(value) is None
    assert tasks.parse_trim_args("/trim " + value) is None
    assert tasks.parse_sample_args("/sample " + value) is None


def test_sample_lengths_are_validated_and_clamped():
    assert tasks.parse_sample_args("/sample") == 30
    assert tasks.parse_sample_args("/sample 1:30") == 90
    assert tasks.parse_sample_args("/sample 1") == 5
    assert tasks.parse_sample_args("/sample 1e100") == 120
    for text in ("/sample 0", "/sample bad", "/sample 1 2"):
        assert tasks.parse_sample_args(text) is None


def test_invalid_sample_command_is_rejected_before_enqueue(monkeypatch):
    async def allowed(*args, **kwargs):
        return True

    async def no_enqueue(*args, **kwargs):
        raise AssertionError("invalid sample should not be queued")

    monkeypatch.setattr(plugin, "check_chat", allowed)
    monkeypatch.setattr(plugin, "AddUserToDatabase", allowed)
    monkeypatch.setattr(plugin, "_enqueue", no_enqueue)
    message = video_cmd("/sample nan", "file-a")
    run(plugin.sample_encode(None, message))
    assert "Sample encode" in message.replies[-1]


def test_invalid_restored_sample_does_not_download(monkeypatch):
    async def no_download(*args, **kwargs):
        raise AssertionError("invalid sample should not download")

    monkeypatch.setattr(tasks, "_download", no_download)
    status = Msg("status")
    run(tasks.sample_task(video_cmd("/sample inf", "file-a"), status))
    assert "Usage" in status.edits[-1]


def test_screenshot_counts_are_validated_and_clamped():
    assert tasks.parse_screens_args("/screens") == 6
    assert tasks.parse_screens_args("/screens 0") == 1
    assert tasks.parse_screens_args("/screens 99") == 10
    assert tasks.parse_screens_args("/screens 0004") == 4
    assert tasks.parse_screens_args("/screens " + "9" * 5000) == 10
    for text in ("/screens -1", "/screens 1.5", "/screens bad", "/screens 1 2", "/screens ٣"):
        assert tasks.parse_screens_args(text) is None


def test_invalid_screens_command_is_rejected_before_enqueue(monkeypatch):
    async def allowed(*args, **kwargs):
        return True

    async def no_enqueue(*args, **kwargs):
        raise AssertionError("invalid screens command should not be queued")

    monkeypatch.setattr(plugin, "check_chat", allowed)
    monkeypatch.setattr(plugin, "AddUserToDatabase", allowed)
    monkeypatch.setattr(plugin, "_enqueue", no_enqueue)
    message = video_cmd("/screens nope", "file-a")
    run(plugin.screens_video(None, message))
    assert "Screenshots" in message.replies[-1]


def test_invalid_restored_screens_does_not_download(monkeypatch):
    async def no_download(*args, **kwargs):
        raise AssertionError("invalid screens command should not download")

    monkeypatch.setattr(tasks, "_download", no_download)
    status = Msg("status")
    run(tasks.screens_task(video_cmd("/screens 1 2", "file-a"), status))
    assert "Usage" in status.edits[-1]

"""Phase 9 – performance + polish: live progress (saver / rename / tools / encoder), parallel force-sub,
instant start pics, module indexes, cache trimming."""
import asyncio
import glob
import inspect
import os
import time
from types import SimpleNamespace

import pytest
from pyrogram import StopTransmission
from pyrogram.errors import FloodWait

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run

MODULES = load_all()


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    yield


# ───────────────────────── LiveProgress ─────────────────────────
def test_live_progress_is_awaited_by_pyrogram_and_throttles():
    from core.progress import Cancelled, LiveProgress
    status = FakeMsg("…", uid=0)
    live = LiveProgress(status, "⬇️ Downloading…", every=60)
    # pyrogram only awaits real coroutine functions – anything else would run in a thread, never awaited
    assert inspect.iscoroutinefunction(live.update)
    for i in range(1, 50):
        run(live.update(i, 100))
    assert len(status.edits) == 1                       # first call edits, the rest are throttled
    run(live.update(100, 100))                          # completion always shows
    assert len(status.edits) == 2 and "100.0%" in status.edits[-1]
    run(live.update(100, 100))                          # identical text → no extra API call
    assert len(status.edits) == 2
    assert issubclass(Cancelled, StopTransmission)
    flag = {"stop": False}
    live2 = LiveProgress(status, "x", cancel=lambda: flag["stop"])
    flag["stop"] = True
    with pytest.raises(StopTransmission):
        run(live2.update(1, 10))


def test_live_progress_backs_off_on_floodwait():
    from core.progress import LiveProgress

    class Flooded(FakeMsg):
        calls = 0

        async def edit_text(self, *a, **k):
            Flooded.calls += 1
            raise FloodWait(value=30)

    live = LiveProgress(Flooded("…", uid=0), "x", every=0)
    run(live.update(1, 100))
    run(live.update(2, 100))
    assert Flooded.calls == 1 and live._next > time.time() + 25


# ───────────────────────── saver transfer ─────────────────────────
class _Acc:
    def __init__(self, tmp, cancel_uid=None):
        self.tmp = tmp
        self.cancel_uid = cancel_uid

    async def get_messages(self, chat, mid):
        doc = SimpleNamespace(file_size=1024, thumbs=None, file_name="a.bin")
        return SimpleNamespace(empty=False, document=doc, video=None, photo=None, audio=None, voice=None,
                               animation=None, sticker=None, text=None, caption=None)

    async def download_media(self, msg, file_name=None, progress=None, **k):
        assert inspect.iscoroutinefunction(progress)
        if self.cancel_uid:
            from saver.start import batch_temp
            batch_temp.IS_BATCH[self.cancel_uid] = True
            try:
                await progress(10, 1024)
            except StopTransmission:
                return None                              # what pyrogram does on StopTransmission
        await progress(1024, 1024)
        path = os.path.join(file_name, "a.bin")
        with open(path, "wb") as f:
            f.write(b"x" * 1024)
        return path


def _saver_msg(uid=31):
    m = FakeMsg("https://t.me/chan/5", uid=uid)
    return m


def test_saver_transfer_uses_live_progress_and_no_status_files(tmp_path, monkeypatch):
    from saver import start
    monkeypatch.chdir(tmp_path)
    c = FakeClient()
    m = _saver_msg()
    start.batch_temp.IS_BATCH[31] = False
    run(start.handle_restricted_content(c, _Acc(tmp_path), m, "chan", 5))
    assert c.called("send_document"), c.calls
    assert inspect.iscoroutinefunction(c.called("send_document")[0][2]["progress"])
    assert not glob.glob(str(tmp_path / "*status.txt"))
    assert c.called("delete_messages")                  # status removed after success
    assert not os.path.exists(tmp_path / "downloads" / f"{m.id}_5")


def test_saver_upload_failure_stays_visible(tmp_path, monkeypatch):
    from saver import start
    monkeypatch.chdir(tmp_path)
    c = FakeClient()
    c.fail.add("send_document")
    edited = []

    orig = c.__getattr__("send_message")

    async def send_message(*a, **k):
        msg = await orig(*a, **k)

        async def edit(text, *x, **y):
            edited.append(text)
        msg.edit = edit
        msg.edit_text = edit
        return msg
    c.send_message = send_message
    start.batch_temp.IS_BATCH[31] = False
    run(start.handle_restricted_content(c, _Acc(tmp_path), _saver_msg(), "chan", 5))
    assert any("Upload failed" in t for t in edited if isinstance(t, str))
    assert not c.called("delete_messages")              # the user must see why nothing arrived


def test_saver_cancel_during_download(tmp_path, monkeypatch):
    from saver import start
    monkeypatch.chdir(tmp_path)
    c = FakeClient()
    edited = []
    orig = c.__getattr__("send_message")

    async def send_message(*a, **k):
        msg = await orig(*a, **k)

        async def edit(text, *x, **y):
            edited.append(text)
        msg.edit = edit
        msg.edit_text = edit
        return msg
    c.send_message = send_message
    start.batch_temp.IS_BATCH[31] = False
    run(start.handle_restricted_content(c, _Acc(tmp_path, cancel_uid=31), _saver_msg(), "chan", 5))
    assert any("Cancelled" in t for t in edited if isinstance(t, str)) and not c.called("send_document")


def test_saver_cancel_button():
    from saver import start
    start.batch_temp.IS_BATCH[41] = False
    q = FakeQuery("sv_cancel", uid=41)
    run(start.cancel_transfer_cb(FakeClient(), q))
    assert start.batch_temp.IS_BATCH[41] is True and "Cancelling" in q.answers[-1][0]
    idle = FakeQuery("sv_cancel", uid=42)
    run(start.cancel_transfer_cb(FakeClient(), idle))
    assert idle.answers[-1][1] is True


# ───────────────────────── rename / tools / encoder progress ─────────────────────────
def test_rename_progress_shows_file_name_and_cancels():
    from renamer import engine
    job = engine.Job(uid=1, message=None)
    job.status = FakeMsg("…", uid=0)
    job.label = "Show S01E01.mkv"
    cb = engine.progress_cb(job, "📥 Downloading…")
    run(cb(512, 1024))
    assert "Show S01E01.mkv" in job.status.edits[-1] and "■" in job.status.edits[-1]
    job.cancelled = True
    with pytest.raises(StopTransmission):
        run(cb(600, 1024))


def test_encoder_transfer_progress_is_throttled():
    from VideoEncoder.utils.display_progress import progress_for_pyrogram
    status = FakeMsg("…", uid=0)
    start = time.time() - 10            # "10 s into the transfer" – the old check fired all through such a second
    for i in range(1, 200):
        run(progress_for_pyrogram(i, 1000, "Downloading", status, start))
    assert len(status.edits) <= 1
    run(progress_for_pyrogram(1000, 1000, "Downloading", status, start))
    assert len(status.edits) <= 2


# ───────────────────────── force-sub in parallel ─────────────────────────
def test_fsub_checks_channels_concurrently(monkeypatch):
    from pyrogram import enums
    from core import fsub
    fsub._ok_cache.clear()

    async def channels():
        return [-1001, -1002, -1003]
    monkeypatch.setattr(fsub, "all_channels", channels)

    class Slow(FakeClient):
        async def get_chat_member(self, chat, uid):
            await asyncio.sleep(0.2)
            status = enums.ChatMemberStatus.LEFT if chat == -1002 else enums.ChatMemberStatus.MEMBER
            return SimpleNamespace(status=status, is_member=status != enums.ChatMemberStatus.LEFT)

    t = time.perf_counter()
    missing = run(fsub.missing_channels(Slow(), 999))
    assert missing == [-1002]
    assert time.perf_counter() - t < 0.45, "channels were checked one after another"


# ───────────────────────── instant start pics ─────────────────────────
def test_random_start_pic_never_waits_for_the_api(monkeypatch):
    from core import ui
    monkeypatch.setattr(ui, "START_PICS", [])
    monkeypatch.setattr(ui, "RANDOM_START_PIC", True)
    refills = []

    async def fake_fill(n=ui.PIC_POOL_SIZE):
        refills.append(n)
    monkeypatch.setattr(ui, "fill_pic_pool", fake_fill)
    monkeypatch.setattr(ui, "_pic_refill", None)
    ui._pic_pool.clear()

    async def go():
        t = time.perf_counter()
        first = await ui.random_start_pic()
        await asyncio.sleep(0)
        return first, time.perf_counter() - t
    pic, took = run(go())
    assert pic in ui._FALLBACK_PICS and took < 0.05 and refills
    ui._pic_pool.extend(["https://a/1.jpg", "https://a/2.jpg"])
    got = run(ui.random_start_pic())
    assert got in ("https://a/1.jpg", "https://a/2.jpg") and got not in ui._pic_pool
    ui._pic_pool.clear()


# ───────────────────────── indexes + cache trimming ─────────────────────────
def test_module_indexes_created():
    from core.db import ensure_module_indexes
    from database.db import db as saver_db
    from VideoEncoder.utils.database.access_db import db as enc_db
    run(ensure_module_indexes())
    assert "id_1" in run(saver_db.col.index_information())
    assert "id_1" in run(enc_db.col.index_information())


def test_watchdog_trims_module_caches():
    import watchdog
    from renamer import engine as rn
    from renamer import store
    store._cache[1] = (time.time() - 3600, {})
    store._cache[2] = (time.time(), {})
    rn._locks[5] = asyncio.Lock()
    rn._pending.pop(5, None)
    watchdog.Watchdog._trim_module_caches()
    assert 1 not in store._cache and 2 in store._cache and 5 not in rn._locks
    store._cache.clear()

"""Phase 16 – encoder speed + duplicates.

• frames: -fps_mode/-vsync vfr for "source" FPS (no CFR-padding duplicates), opt-in mpdecimate dedup
• engine: ffmpeg stderr drained while it runs (no 64 KB pipe stall), tail-only progress reads, no 5 s lag
• queue: the same file / same settings can't be queued twice, re-delivered updates are ignored,
  the queue note becomes the status card (one message per task)
• downloads: parallel chunked Telegram downloads with a safe fallback (core/fastdl.py)
• uploads: Telegram-sized auto thumbnails
"""
import asyncio
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tests.harness import FakeMsg, load_all, no_botapi, reset_db, run

MODULES = load_all()

import config  # noqa: E402
from core import fastdl  # noqa: E402
from VideoEncoder import data as queue  # noqa: E402
from VideoEncoder.utils import ffcmd, hw, jobs, scheduler  # noqa: E402
import VideoEncoder.utils.encoding as encoding  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    no_botapi()
    saved = list(queue)
    queue.clear()
    scheduler._running.clear()
    yield
    queue[:] = saved
    scheduler._running.clear()
    for d in (scheduler.MODES, scheduler.EXTRA, scheduler.SRC, scheduler.NOTES):
        d.clear()
    scheduler.PRIO.clear()
    jobs._JOBS.clear()
    jobs._RECENT.clear()
    hw.set_caps(None)


class Msg(FakeMsg):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


def st(i, t, c, **k):
    return dict(index=i, codec_type=t, codec_name=c, **k)


INFO = ffcmd.summarize({"streams": [st(0, "video", "h264", width=1920, height=1080), st(1, "audio", "aac", channels=2)],
                        "format": {"duration": "600", "size": str(900 * 1024 * 1024)}})


def build(s, **k):
    return ffcmd.build_command("in.mkv", "out" + ffcmd.output_ext(ffcmd.merge(s)), s, INFO, **k)


def val(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


FF = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FF, reason="ffmpeg not installed")


# ───────────────────────── frame timing ─────────────────────────
def test_source_fps_keeps_timestamps_instead_of_padding_with_duplicates():
    cmd = build({})
    assert val(cmd, "-fps_mode") == "vfr" and "-r" not in cmd
    assert val(build({}, fps_flag="-vsync"), "-vsync") == "vfr"             # ffmpeg 4.x spelling
    assert "-fps_mode" not in build({}, fps_flag=None) and "-vsync" not in build({}, fps_flag=None)


def test_forced_fps_and_avi_stay_constant_rate():
    cmd = build({"frame": "30"})
    assert val(cmd, "-r") == "30" and "-fps_mode" not in cmd
    avi = build({"extensions": "AVI"})
    assert "-fps_mode" not in avi                                           # AVI has no timestamps


def test_dedup_drops_repeated_frames_and_ignores_forced_fps():
    cmd = build({"dedup": True, "frame": "30", "deinterlace": True, "resolution": "720"})
    vf = val(cmd, "-vf")
    # Phase 19: deinterlace → downscale → dedup (measured faster: mpdecimate compares the small picture)
    assert vf.startswith("bwdif") and vf.index("bwdif") < vf.index("scale") < vf.index("mpdecimate")
    assert val(cmd, "-fps_mode") == "vfr" and "-r" not in cmd
    assert "mpdecimate" not in (val(build({"dedup": True, "extensions": "AVI"}), "-vf") or "")
    assert "Dedup" in ffcmd.describe({"dedup": True})["filters"]


def test_both_passes_use_the_same_frame_timing():
    p1 = build({"mode": "size", "target_mb": 100, "twopass": True}, pass_no=1, passlog="/tmp/x")
    p2 = build({"mode": "size", "target_mb": 100, "twopass": True}, pass_no=2, passlog="/tmp/x")
    assert val(p1, "-fps_mode") == val(p2, "-fps_mode") == "vfr"


@pytest.mark.parametrize("text,ver,flag", [
    ("ffmpeg version 4.4.2-0ubuntu0.22.04.1 Copyright", (4, 4), "-vsync"),
    ("ffmpeg version 5.1.6-0+deb12u1 Copyright", (5, 1), "-fps_mode"),
    ("ffmpeg version n7.0.2 Copyright", (7, 0), "-fps_mode"),
    ("ffmpeg version 8.0 Copyright", (8, 0), "-fps_mode"),
    ("ffmpeg version N-117000-gdeadbeef Copyright", None, "-fps_mode"),
])
def test_fps_flag_follows_the_installed_ffmpeg(monkeypatch, text, ver, flag):
    assert hw.parse_version(text) == ver
    monkeypatch.setattr(hw, "_FPS_FLAG", None)
    monkeypatch.setattr(hw.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=text.encode()))
    assert hw.fps_mode_flag() == flag
    monkeypatch.setattr(hw.subprocess, "run", lambda *a, **k: 1 / 0)       # cached – no second probe
    assert hw.fps_mode_flag() == flag


def _frames(path) -> int:
    out = subprocess.run([FF, "-hide_banner", "-i", path, "-map", "0:v", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    import re
    return int(re.findall(r"frame=\s*(\d+)", out)[-1])


@needs_ffmpeg
def test_real_vfr_source_is_not_padded_with_duplicate_frames(tmp_path):
    src = str(tmp_path / "vfr.mkv")
    # 60 frames with a 3 s pause in the middle – a phone / screen-recording style VFR clip
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30",
                    "-frames:v", "60", "-vf", "setpts='if(lt(N,30),N/30/TB,(N/30+3)/TB)'", "-c:v", "libx264",
                    "-preset", "ultrafast", hw.fps_mode_flag(), "vfr", src], check=True)
    assert _frames(src) == 60
    s = {"preset": "uf", "extensions": "MP4"}
    new = str(tmp_path / "new.mp4")
    cmd = ffcmd.build_command(src, new, s, None, fps_flag=hw.fps_mode_flag())
    assert subprocess.run([FF] + cmd[1:], capture_output=True).returncode == 0
    old = str(tmp_path / "old.mp4")
    subprocess.run([FF] + ffcmd.build_command(src, old, s, None, fps_flag=None)[1:], capture_output=True, check=True)
    assert _frames(new) == 60 and _frames(old) > 120                         # CFR default duplicated ~90 frames


@needs_ffmpeg
def test_real_dedup_removes_repeated_frames(tmp_path):
    src = str(tmp_path / "twos.mkv")
    # animation "on twos": every picture shown for 2 frames (60 frames, 30 distinct)
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15",
                    "-frames:v", "60", "-vf", "fps=30", "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", src],
                   check=True)
    out = str(tmp_path / "out.mkv")
    cmd = ffcmd.build_command(src, out, {"preset": "uf", "dedup": True}, None, fps_flag=hw.fps_mode_flag())
    r = subprocess.run([FF] + cmd[1:], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert _frames(src) == 60 and _frames(out) <= 35


# ───────────────────────── engine ─────────────────────────
def _fake_ffmpeg(monkeypatch, script):
    """build_command → a python process that behaves like ffmpeg (writes the output, spams stderr …)."""
    def fake_build(src, out, s, info=None, **k):
        return [sys.executable, "-c", script, out]
    monkeypatch.setattr(encoding.ffcmd, "build_command", fake_build)

    async def fake_probe(_):
        return INFO
    monkeypatch.setattr(encoding, "probe", fake_probe)
    monkeypatch.setattr(encoding, "PROGRESS_TICK", 0.05)


def test_encode_does_not_stall_when_ffmpeg_floods_stderr(monkeypatch, tmp_path):
    src = tmp_path / "in.mkv"
    src.write_bytes(b"\0" * 64)
    # 1 MB of warnings before the output is finished – used to fill the pipe and freeze ffmpeg forever
    _fake_ffmpeg(monkeypatch, "import sys\nfor i in range(20000): sys.stderr.write('decode warning %06d\\n' % i)\n"
                              "open(sys.argv[1], 'wb').write(b'encoded')")
    status = Msg("s", uid=5)
    out = run(asyncio.wait_for(encoding.encode(str(src), Msg("/dl", uid=5), status,
                                               opts={"out_dir": str(tmp_path)}), 30))
    assert out and open(out, "rb").read() == b"encoded"


def test_failed_encode_keeps_the_tail_of_stderr_for_the_error_card(monkeypatch, tmp_path):
    src = tmp_path / "in.mkv"
    src.write_bytes(b"\0" * 64)
    _fake_ffmpeg(monkeypatch, "import sys\nsys.stderr.write('noise\\n' * 50000 + 'Invalid data found when processing input')\n"
                              "sys.exit(1)")
    status = Msg("s", uid=5)
    assert run(asyncio.wait_for(encoding.encode(str(src), Msg("/dl", uid=5), status,
                                                opts={"out_dir": str(tmp_path)}), 30)) is None
    assert encoding.LAST_ERROR.pop(status.id).endswith("Invalid data found when processing input")


def test_progress_reads_only_the_tail_of_a_long_progress_file(tmp_path):
    p = tmp_path / "process.txt"
    block = "frame=1\nfps=10.0\ntotal_size=100\nout_time_ms=1000000\nspeed=1.0x\nprogress=continue\n"
    with open(p, "w") as f:
        f.write(block * 20000)                                              # ~1.3 MB of old blocks
        f.write("frame=9\nfps=55.5\ntotal_size=4096\nout_time_ms=30000000\nspeed=3.5x\nprogress=continue\n")
    tail = encoding._tail(str(p))
    assert len(tail) <= 4096 and "fps=55.5" in tail
    assert encoding._tail(str(tmp_path / "missing.txt")) is None


def test_progress_loop_notices_the_exit_immediately(monkeypatch, tmp_path):
    monkeypatch.setattr(encoding, "PROGRESS_TICK", 5)

    async def go():
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(0.2)")
        t = time.monotonic()
        await encoding.handle_progress(proc, Msg("s", uid=5), Msg("/dl", uid=5), "x.mkv",
                                       progress_file=str(tmp_path / "none.txt"), total_time=10)
        return time.monotonic() - t
    assert run(go()) < 2                                                    # not the full 5 s tick


# ───────────────────────── duplicate tasks ─────────────────────────
def _video_msg(text, uid=5, unique="AgADuniq"):
    m = Msg(text, uid=uid)
    m.reply_to_message = Msg(None, uid=uid)
    m.reply_to_message.video = SimpleNamespace(file_unique_id=unique, file_id="F", file_size=5, file_name="a.mkv")
    return m


@pytest.fixture
def enq(monkeypatch):
    import VideoEncoder.plugins.encode as enc_plugin
    started = []

    async def fake_handle(message, mode):
        started.append(message)

    async def nosleep(*a, **k):
        return None
    monkeypatch.setattr(enc_plugin, "handle_tasks", fake_handle)
    monkeypatch.setattr(enc_plugin.asyncio, "sleep", nosleep)
    monkeypatch.setattr(config, "ENC_MAX_TASKS_FREE", 5)
    return enc_plugin, started


def test_same_file_is_not_queued_twice(enq):
    plugin, started = enq
    first = _video_msg("/dl")
    assert run(plugin._enqueue(first, "tg")) and started == [first]
    again = _video_msg("/dl")                                               # tapped twice / sent again
    assert run(plugin._enqueue(again, "tg")) is False
    assert "Already in your queue" in again.replies[-1] and "running now" in again.replies[-1]
    assert len(queue) == 1


def test_waiting_duplicate_reports_its_position(enq):
    plugin, started = enq
    run(plugin._enqueue(_video_msg("/dl", unique="A"), "tg"))
    queued = _video_msg("/dl", unique="B")
    run(plugin._enqueue(queued, "tg"))
    dup = _video_msg("/dl", unique="B")
    assert run(plugin._enqueue(dup, "tg")) is False and "#2" in dup.replies[-1]


def test_redelivered_update_is_ignored_silently(enq):
    plugin, started = enq
    m = _video_msg("/dl")
    run(plugin._enqueue(m, "tg"))
    assert run(plugin._enqueue(m, "tg")) is False and m.replies == [] and len(queue) == 1


def test_different_arguments_or_modes_or_users_are_not_duplicates(enq):
    plugin, started = enq
    assert run(plugin._enqueue(_video_msg("/trim 0 10"), "trim"))
    assert run(plugin._enqueue(_video_msg("/trim 20 30"), "trim"))
    assert run(plugin._enqueue(_video_msg("/sample"), "sample"))
    assert run(plugin._enqueue(_video_msg("/dl", uid=6), "tg"))
    assert run(plugin._enqueue(_video_msg("/convert mp3"), "convert", {"fmt": "mp3"}))
    assert run(plugin._enqueue(_video_msg("/convert mp3"), "convert", {"fmt": "mp3"})) is False
    assert len(queue) == 5


def test_duplicate_guard_clears_once_the_task_is_done(enq):
    plugin, started = enq
    m = _video_msg("/dl")
    run(plugin._enqueue(m, "tg"))
    scheduler.finish(m)
    assert run(plugin._enqueue(_video_msg("/dl"), "tg")) is True


def test_links_are_deduplicated_by_their_text():
    a, b = Msg("/ddl https://x.y/a.mkv", uid=5), Msg("/ddl  https://x.y/a.mkv ", uid=5)
    assert scheduler.signature(a, "url") == scheduler.signature(b, "url")
    assert scheduler.signature(Msg("/dl", uid=5), "tg") is None


def test_queue_note_becomes_the_status_card(monkeypatch, enq):
    plugin, started = enq
    from VideoEncoder.utils import tasks
    run(plugin._enqueue(_video_msg("/dl", unique="A"), "tg"))
    second = _video_msg("/dl", unique="B")
    run(plugin._enqueue(second, "tg"))
    note = second.sent[-1]
    assert scheduler.NOTES[id(second)] is note

    seen = {}

    async def fake_tg(message, msg):
        seen["msg"] = msg

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(tasks, "tg_task", fake_tg)
    monkeypatch.setattr(tasks, "on_task_complete", nothing)
    run(tasks.handle_tasks(second, "tg"))
    assert seen["msg"] is note and "Downloading" in note.edits[0]
    assert len(second.replies) == 1                                         # just the (reused) queue note


def test_task_error_is_shown_on_the_status_card(monkeypatch):
    from VideoEncoder.utils import tasks

    async def boom(message, msg):
        raise RuntimeError("disk full")

    async def nothing(*a, **k):
        return None
    monkeypatch.setattr(tasks, "tg_task", boom)
    monkeypatch.setattr(tasks, "on_task_complete", nothing)
    m = _video_msg("/dl")
    run(tasks.handle_tasks(m, "tg"))
    status = m.sent[0]
    assert len(m.replies) == 1 and "disk full" in status.edits[-1]


# ───────────────────────── parallel downloads ─────────────────────────
def _file_id():
    from pyrogram.file_id import FileId, FileType
    return FileId(file_type=FileType.VIDEO, dc_id=4, media_id=77, access_hash=88, file_reference=b"ref").encode()


class _FakeSession:
    def __init__(self, blob, redirect=False, delay=0.002):
        self.blob, self.redirect, self.delay = blob, redirect, delay
        self.inflight = self.peak = self.calls = 0

    async def invoke(self, q, sleep_threshold=None):
        from pyrogram import raw
        self.calls += 1
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        try:
            await asyncio.sleep(self.delay)
            if self.redirect:
                return raw.types.upload.FileCdnRedirect(dc_id=1, file_token=b"t", encryption_key=b"k" * 32,
                                                        encryption_iv=b"i" * 16, file_hashes=[])
            assert q.offset % fastdl.CHUNK == 0 and q.limit == fastdl.CHUNK
            return raw.types.upload.File(type=raw.types.storage.FileMp4(), mtime=0,
                                         bytes=self.blob[q.offset:q.offset + q.limit])
        finally:
            self.inflight -= 1


class _FakeClient:
    def __init__(self, parent):
        self.PARENT_DIR = parent
        self.storage = object()
        self.stock = []

    def guess_extension(self, mime):
        return ".mkv" if mime == "video/x-matroska" else None

    async def download_media(self, message, file_name=None, progress=None, progress_args=()):
        self.stock.append(file_name)
        return "stock"


def _dl_msg(size, name="movie.mkv"):
    m = Msg(None, uid=5)
    m.video = SimpleNamespace(file_id=_file_id(), file_unique_id="u", file_size=size, file_name=name,
                              mime_type="video/x-matroska", date=None)
    return m


def test_parallel_download_writes_the_exact_file(monkeypatch, tmp_path):
    blob = os.urandom(fastdl.CHUNK * 12 + 12345)                            # 12 full chunks + a partial one
    sess = _FakeSession(blob)

    async def fake_session(client, dc):
        assert dc == 4
        return sess
    monkeypatch.setattr(fastdl, "_session", fake_session)
    monkeypatch.setattr(fastdl, "MIN_SIZE", 1)
    client, seen = _FakeClient(tmp_path), []

    async def progress(cur, total, label):
        seen.append((cur, total, label))
    path = run(fastdl.download(client, _dl_msg(len(blob)), file_name=str(tmp_path / "dl") + "/",
                               progress=progress, progress_args=("📥",), workers=6))
    assert path == str(tmp_path / "dl" / "movie.mkv") and open(path, "rb").read() == blob
    assert sess.calls == 13 and sess.peak > 1 and client.stock == []        # really parallel, no fallback
    assert seen[-1] == (len(blob), len(blob), "📥")
    assert not os.path.exists(path + ".temp")


def test_small_files_and_fake_clients_use_the_stock_downloader(tmp_path):
    client = _FakeClient(tmp_path)
    assert run(fastdl.download(client, _dl_msg(1024), file_name="x/")) == "stock"
    no_storage = SimpleNamespace(stock=[])

    async def dm(message, **k):
        return "plain"
    no_storage.download_media = dm
    assert run(fastdl.download(no_storage, _dl_msg(50 * 1024 * 1024), file_name="x/")) == "plain"


def test_cdn_redirect_falls_back_and_cleans_up(monkeypatch, tmp_path):
    sess = _FakeSession(b"", redirect=True)

    async def fake_session(client, dc):
        return sess
    monkeypatch.setattr(fastdl, "_session", fake_session)
    monkeypatch.setattr(fastdl, "MIN_SIZE", 1)
    client = _FakeClient(tmp_path)
    assert run(fastdl.download(client, _dl_msg(fastdl.CHUNK * 3), file_name=str(tmp_path) + "/")) == "stock"
    assert client.stock and not os.path.exists(tmp_path / "movie.mkv.temp")


def test_cancel_returns_none_like_pyrogram(monkeypatch, tmp_path):
    from pyrogram import StopTransmission
    blob = os.urandom(fastdl.CHUNK * 8)
    sess = _FakeSession(blob, delay=0.01)

    async def fake_session(client, dc):
        return sess
    monkeypatch.setattr(fastdl, "_session", fake_session)
    monkeypatch.setattr(fastdl, "MIN_SIZE", 1)

    async def cancel(cur, total):
        raise StopTransmission
    client = _FakeClient(tmp_path)
    assert run(fastdl.download(client, _dl_msg(len(blob)), file_name=str(tmp_path) + "/", progress=cancel)) is None
    assert client.stock == [] and os.listdir(tmp_path) == []


def test_generated_name_matches_pyrogram_rules(tmp_path):
    client = _FakeClient(tmp_path)
    m = _dl_msg(10, name=None)
    p = fastdl.target_path(client, m, m.video, "video", "")
    assert p.startswith(str(tmp_path / "downloads")) and p.endswith(".mkv")
    assert fastdl.target_path(client, m, m.video, "video", "/abs/dir/") == "/abs/dir/" + os.path.basename(p)
    assert fastdl.target_path(client, m, m.video, "video", "/abs/dir/name.mp4") == "/abs/dir/name.mp4"


def test_fetch_uses_message_download_for_small_files():
    class M(Msg):
        async def download(self, file_name="", progress=None, progress_args=()):
            return "msg-download"
    m = M(None, uid=5)
    m.video = SimpleNamespace(file_size=10)
    assert run(fastdl.fetch(m, file_name="x/")) == "msg-download"


def test_fast_download_is_configurable():
    assert config.FAST_DL is True and 1 <= config.FAST_DL_WORKERS <= 16
    sample = open(os.path.join(os.path.dirname(__file__), "..", "config.env.sample")).read()
    assert "FAST_DL=" in sample and "FAST_DL_WORKERS=" in sample


# ───────────────────────── uploads ─────────────────────────
@needs_ffmpeg
def test_auto_thumbnail_fits_telegram_limits(tmp_path):
    from PIL import Image
    src = str(tmp_path / "big.mp4")
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1920x1080:rate=24:duration=1", "-c:v", "libx264", "-preset", "ultrafast", src],
                   check=True)
    thumb = encoding.get_thumbnail(src, str(tmp_path), 0.5)
    w, h = Image.open(thumb).size
    assert max(w, h) <= 320 and os.path.getsize(thumb) < 200 * 1024

"""Phase 17 – advanced logic: no duplicates across restarts / deploys, smarter + faster encoder.

• single-instance lease (core/instance.py) · orphan ffmpeg kill on restart · replayed-update guard
• source cache (no second download of the same file) · CPU split across parallel encodes
• size guard (never send a re-encode bigger than its same-codec source) · safe upload retries
"""
import asyncio
import os
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from tests.harness import FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run

MODULES = load_all()

import config  # noqa: E402
from core import instance, middleware  # noqa: E402
from core.db import vdb  # noqa: E402
from VideoEncoder.utils import ffcmd, jobs, srccache  # noqa: E402
import VideoEncoder.utils.encoding as encoding  # noqa: E402

FF = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FF, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    no_botapi()
    middleware.SEEN.clear()
    yield
    jobs._JOBS.clear()
    jobs._RECENT.clear()
    instance.STATE.update(held=False, waited=0.0, other=None)


class Msg(FakeMsg):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


# ───────────────────────── restarts: no orphan ffmpeg ─────────────────────────
def test_kill_all_stops_running_encoders():
    async def go():
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", "import time; time.sleep(30)")
        jobs.register(1, 5, 5)
        jobs.attach(1, proc)
        assert jobs.kill_all() == 1
        return await asyncio.wait_for(proc.wait(), 5)
    assert run(go()) != 0


def test_reap_orphans_only_kills_our_media_children(tmp_path, monkeypatch):
    me = os.getpid()
    for pid, comm, ppid in ((4001, "ffmpeg", me), (4002, "ffmpeg", 1), (4003, "python3", me), (4004, "ffprobe", me)):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} ({comm}) S {ppid} 1 1 0 -1 4194560")
    (tmp_path / "self").mkdir()
    killed = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: killed.append(pid))
    assert jobs.reap_orphans(proc_root=str(tmp_path)) == 2 and sorted(killed) == [4001, 4004]


def test_restart_paths_kill_encoders_and_hand_over_the_lease():
    src = open("core/admin.py").read()
    i = src.index("async def _restart")
    body = src[i:src.index("os.execl", i)]
    assert "jobs.kill_all()" in body and "instance.handover()" in body
    dog = open("watchdog.py").read()
    j = dog.index("Telegram unreachable 3x")
    assert "jobs.kill_all()" in dog[j:dog.index("os.execl", j)]


# ───────────────────────── single instance ─────────────────────────
def _col():
    return vdb.db["runtime_test"]


def test_second_copy_waits_until_the_first_releases(monkeypatch):
    col = _col()
    monkeypatch.setattr(instance, "ME", "A")
    assert run(instance.try_acquire(col))
    assert run(instance.try_acquire(col))                         # re-entrant for the owner
    monkeypatch.setattr(instance, "ME", "B")
    assert run(instance.try_acquire(col)) is False and instance.STATE["other"]["owner"] == "A"

    async def scenario():
        waiter = asyncio.ensure_future(instance.acquire(col, poll=0.02))
        await asyncio.sleep(0.1)
        assert not waiter.done()                                 # A is alive → B keeps waiting
        instance.ME = "A"
        await instance.release(col)
        instance.ME = "B"
        return await asyncio.wait_for(waiter, 5)
    assert run(scenario()) >= 0.05
    assert run(col.find_one({"_id": instance.LOCK_ID}))["owner"] == "B"


def test_an_expired_lease_is_taken_over_and_the_old_copy_notices(monkeypatch):
    from datetime import datetime, timedelta
    col = _col()
    run(col.insert_one({"_id": instance.LOCK_ID, "owner": "old", "until": datetime.utcnow() - timedelta(seconds=1)}))
    monkeypatch.setattr(instance, "ME", "new")
    assert run(instance.try_acquire(col))
    monkeypatch.setattr(instance, "ME", "old")
    assert run(instance.renew(col)) is False                      # frozen copy lost its lease

    lost = []

    async def on_lost():
        lost.append(True)
    run(asyncio.wait_for(instance._heartbeat(on_lost, col, beat=0.01), 5))
    assert lost == [True]


def test_handover_lets_the_reexeced_process_keep_the_lease(monkeypatch):
    monkeypatch.delenv(instance.ENV, raising=False)
    monkeypatch.setattr(instance, "renew", lambda *a, **k: asyncio.sleep(0))
    run(instance.handover())
    assert os.environ[instance.ENV] == instance.ME
    monkeypatch.delenv(instance.ENV)


def test_single_instance_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "SINGLE_INSTANCE", False)
    assert run(instance.acquire(_col())) == 0.0 and instance.start_heartbeat() is None


def test_run_py_takes_the_lease_before_telegram_and_releases_it():
    src = open("run.py").read()
    assert src.index("instance.acquire()") < src.index("await start_with_retry(app)")
    assert "instance.release()" in src and "reap_orphans()" in src


# ───────────────────────── replayed updates ─────────────────────────
def test_replayed_message_is_dropped_once_seen():
    from pyrogram import StopPropagation
    m = Msg("/dl", uid=5)
    run(middleware.drop_replayed_messages(None, m))                # first time → passes (no exception)
    with pytest.raises(StopPropagation):
        run(middleware.drop_replayed_messages(None, m))
    run(middleware.drop_replayed_messages(None, Msg("/dl", uid=5)))  # a different message is fine


def test_replayed_callback_is_dropped_once_seen():
    from pyrogram import StopPropagation
    q = FakeQuery("OpenSettings")
    q.id = "unique-777"
    run(middleware.drop_replayed_callbacks(None, q))
    with pytest.raises(StopPropagation):
        run(middleware.drop_replayed_callbacks(None, q))


def test_seen_cache_expires_and_stays_bounded():
    s = middleware._Seen(ttl=0.05, cap=3)
    assert s.check_and_add("a") is False and s.check_and_add("a") is True
    time.sleep(0.06)
    assert s.check_and_add("a") is False                           # expired → treated as new
    for k in "bcdef":
        s.check_and_add(k)
    assert len(s._d) <= 3


# ───────────────────────── source cache ─────────────────────────
@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(srccache, "root", lambda: str(tmp_path / ".srccache"))
    monkeypatch.setattr(config, "SOURCE_CACHE_MIN", 60, raising=False)
    monkeypatch.setattr(config, "SOURCE_CACHE_GB", 10, raising=False)
    return tmp_path


def _media(unique="AgADxyz", size=None):
    return SimpleNamespace(file_unique_id=unique, file_size=size, file_name="movie.mkv")


def test_second_task_reuses_the_download(cache):
    task1 = cache / "t1"
    task1.mkdir()
    src = task1 / "movie.mkv"
    src.write_bytes(b"v" * 1000)
    media = _media(size=1000)
    assert srccache.put(media, str(src))
    shutil.rmtree(task1)                                           # task 1 finished and cleaned up
    hit = srccache.get(media, str(cache / "t2"))
    assert hit == str(cache / "t2" / "movie.mkv") and open(hit, "rb").read() == b"v" * 1000
    assert srccache.get(_media("other"), str(cache / "t3")) is None


def test_cache_links_instead_of_copying(cache):
    src = cache / "movie.mkv"
    src.write_bytes(b"x" * 10)
    srccache.put(_media(size=10), str(src))
    assert os.stat(src).st_nlink == 2                              # same inode → no extra disk


def test_size_mismatch_drops_the_entry(cache):
    src = cache / "movie.mkv"
    src.write_bytes(b"x" * 10)
    srccache.put(_media(size=10), str(src))
    assert srccache.get(_media(size=999), str(cache / "t")) is None
    assert srccache._entries() == []


def test_prune_by_age_size_and_low_disk(cache, monkeypatch):
    for u in ("a", "b", "c"):
        f = cache / f"{u}.mkv"
        f.write_bytes(b"z" * 100)
        srccache.put(_media(u, 100), str(f))
    now = time.time()
    old = os.path.join(srccache.root(), "a")
    os.utime(old, (now - 7200, now - 7200))
    assert srccache.prune(now=now, free_ratio=0.5) == 1            # a expired (60 min TTL)
    monkeypatch.setattr(config, "SOURCE_CACHE_GB", 0)              # cap 0 → everything goes
    assert srccache.prune(now=now, free_ratio=0.5) == 2
    f = cache / "d.mkv"
    f.write_bytes(b"z")
    monkeypatch.setattr(config, "SOURCE_CACHE_GB", 10)
    srccache.put(_media("d", 1), str(f))
    assert srccache.prune(now=now, free_ratio=0.05) >= 0 and srccache._entries() == []   # disk nearly full


def test_cache_off_when_minutes_is_zero(cache, monkeypatch):
    monkeypatch.setattr(config, "SOURCE_CACHE_MIN", 0)
    f = cache / "m.mkv"
    f.write_bytes(b"1")
    assert srccache.put(_media(size=1), str(f)) is False


def test_fetch_cached_downloads_once(cache, monkeypatch):
    from core import fastdl
    from VideoEncoder.utils import tasks
    calls = []

    async def fake_fetch(message, file_name="", progress=None, progress_args=()):
        calls.append(file_name)
        os.makedirs(file_name, exist_ok=True)
        p = os.path.join(file_name, "movie.mkv")
        with open(p, "wb") as f:
            f.write(b"m" * 50)
        return p
    monkeypatch.setattr(fastdl, "fetch", fake_fetch)
    msg = Msg("status", uid=5)

    def src_msg():
        m = Msg(None, uid=5)
        m.video = _media("AgADsame", 50)
        return m
    first = run(tasks._fetch_cached(src_msg(), str(cache / "t1"), "Downloading...", msg, time.time()))
    second = run(tasks._fetch_cached(src_msg(), str(cache / "t2"), "Downloading...", msg, time.time()))
    assert len(calls) == 1 and open(second, "rb").read() == open(first, "rb").read()


def test_idle_wipe_keeps_the_cache_but_clean_removes_it(monkeypatch, tmp_path):
    from VideoEncoder.utils import helper
    dl, enc = tmp_path / "dl", tmp_path / "enc"
    for d in (dl / ".srccache" / "k", dl / "5_1", enc / "5_1"):
        d.mkdir(parents=True)
    monkeypatch.setattr(helper, "download_dir", str(dl))
    monkeypatch.setattr(helper, "encode_dir", str(enc))
    helper.delete_downloads()
    assert os.listdir(dl) == [".srccache"] and os.listdir(enc) == []
    helper.delete_downloads(keep_cache=False)
    assert os.listdir(dl) == []


def test_watchdog_age_sweep_skips_the_cache(tmp_path):
    import watchdog
    d = tmp_path / ".srccache"
    d.mkdir()
    old = time.time() - 10 * 86400
    os.utime(d, (old, old))
    assert watchdog.clean_dir(str(tmp_path), 0.001) == (0, 0) and d.exists()


# ───────────────────────── CPU split ─────────────────────────
def test_threads_are_shared_between_parallel_encodes():
    assert encoding.encoder_threads(cpus=8, workers=1) == 0
    assert encoding.encoder_threads(cpus=8, workers=2) == 4
    assert encoding.encoder_threads(cpus=16, workers=3) == 5
    assert encoding.encoder_threads(cpus=2, workers=4) == 2


def test_encode_passes_the_thread_share_to_ffmpeg(monkeypatch, tmp_path):
    src = tmp_path / "in.mkv"
    src.write_bytes(b"\0" * 64)
    captured = {}

    class Stop(Exception):
        pass

    async def fake_exec(*cmd, **k):
        captured["cmd"] = list(cmd)
        raise Stop

    async def fake_probe(_):
        return ffcmd.summarize(None)
    monkeypatch.setattr(encoding, "probe", fake_probe)
    monkeypatch.setattr(encoding.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(config, "ENCODER_WORKERS", 2)
    monkeypatch.setattr(encoding.os, "cpu_count", lambda: 12)
    with pytest.raises(Stop):
        run(encoding.encode(str(src), Msg("/dl", uid=5), Msg("s", uid=5), opts={"out_dir": str(tmp_path)}))
    cmd = captured["cmd"]
    assert cmd[len(cmd) - 1 - cmd[::-1].index("-threads") + 1] == "6"


# ───────────────────────── size guard ─────────────────────────
def st(i, t, c, **k):
    return dict(index=i, codec_type=t, codec_name=c, **k)


def _info(vcodec="h264", acodec="aac", subs=()):
    streams = [st(0, "video", vcodec, width=320, height=240), st(1, "audio", acodec, channels=2)]
    streams += [st(2 + i, "subtitle", c) for i, c in enumerate(subs)]
    return ffcmd.summarize({"streams": streams, "format": {"duration": "2", "size": "1000"}})


def test_guard_only_when_nothing_but_same_codec_recompression_was_asked():
    ok = {"audio": "aac"}
    assert ffcmd.guard_applies(ok, _info())
    assert not ffcmd.guard_applies({**ok, "hevc": True}, _info())                   # codec switch → keep it
    assert ffcmd.guard_applies({**ok, "hevc": True}, _info("hevc"))
    for change in ({"resolution": "480"}, {"hardsub": True}, {"watermark": True}, {"dedup": True},
                   {"denoise": True}, {"frame": "30"}, {"loudnorm": True}, {"channels": "2.0"}, {"bits": True},
                   {"size_guard": False}, {"extensions": "AVI"}):
        assert not ffcmd.guard_applies({**ok, **change}, _info()), change
    assert not ffcmd.guard_applies({"audio": "opus"}, _info())                       # wants a different audio codec
    assert ffcmd.guard_applies({"audio": "copy"}, _info(acodec="flac"))
    assert not ffcmd.guard_applies({"audio": "copy", "extensions": "MP4"}, _info(acodec="flac"))   # flac ∉ mp4
    assert not ffcmd.guard_applies(ok, ffcmd.summarize(None))                        # unknown source


def test_remux_command_copies_and_keeps_text_subs_in_mp4():
    cmd = ffcmd.remux_command("in.mkv", "out.mp4", {}, _info(subs=("subrip", "hdmv_pgs_subtitle")))
    assert cmd[-1] == "out.mp4" and "-c" in cmd and cmd[cmd.index("-c") + 1] == "copy"
    assert "0:2" in cmd and "0:3" not in cmd and cmd[cmd.index("-c:s") + 1] == "mov_text"
    assert "0:V" in cmd and "+faststart" in cmd
    mkv = ffcmd.remux_command("in.mp4", "out.mkv", {}, _info())
    assert "0:s?" in mkv and "0:t?" in mkv
    assert "0:s?" not in ffcmd.remux_command("in.mp4", "out.mkv", {"subtitles": False}, _info())


def _make(path, crf):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x240:rate=24:duration=2", "-f", "lavfi", "-i", "sine=duration=2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(crf), "-c:a", "aac", "-shortest", path],
                   check=True)


@needs_ffmpeg
def test_bigger_reencode_is_swapped_for_a_lossless_remux(tmp_path):
    from VideoEncoder.utils import helper
    src, out = str(tmp_path / "src.mkv"), str(tmp_path / "enc" / "src.mkv")
    os.makedirs(os.path.dirname(out))
    _make(src, 40)                                     # already tiny
    _make(out, 5)                                      # the "re-encode" came out much bigger
    big = os.path.getsize(out)
    res = encoding.Encoded(out)
    res.info, res.settings, res.sample = _info(), {"audio": "aac"}, None
    got = run(helper.size_guard(res, src, Msg("s", uid=5)))
    assert got is res and res.guard and res.guard[0] == big and os.path.getsize(out) < big
    frames = subprocess.run([FF, "-hide_banner", "-i", out, "-map", "0:v", "-f", "null", "-"],
                            capture_output=True, text=True).stderr
    assert "frame=   48" in frames or "frame=48" in frames.replace(" ", "")
    assert not [f for f in os.listdir(os.path.dirname(out)) if ".remux" in f]


@needs_ffmpeg
def test_guard_keeps_a_smaller_or_transformed_encode(tmp_path):
    from VideoEncoder.utils import helper
    src, out = str(tmp_path / "src.mkv"), str(tmp_path / "out.mkv")
    _make(src, 5)
    _make(out, 40)
    size = os.path.getsize(out)
    res = encoding.Encoded(out)
    res.info, res.settings, res.sample = _info(), {"audio": "aac"}, None
    run(helper.size_guard(res, src, Msg("s", uid=5)))
    assert res.guard is None and os.path.getsize(out) == size                       # smaller → untouched
    shutil.copy(src, out)
    res.settings = {"audio": "aac", "resolution": "480"}
    run(helper.size_guard(res, src, Msg("s", uid=5)))
    assert res.guard is None                                                         # user asked for a change


# ───────────────────────── upload retries ─────────────────────────
def test_flood_wait_is_retried_after_waiting():
    from pyrogram.errors import FloodWait
    from VideoEncoder.utils.uploads import telegram as tg
    calls, slept = [], []

    async def send():
        calls.append(1)
        if len(calls) == 1:
            raise FloodWait(value=7)
        return "link"

    async def fake_sleep(s):
        slept.append(s)
    status = Msg("s", uid=5)
    assert run(tg.with_retry(send, status, sleep=fake_sleep)) == "link"
    assert len(calls) == 2 and slept == [8] and "wait 8s" in status.edits[-1]


def test_server_errors_retry_then_give_up():
    from pyrogram.errors import InternalServerError
    from VideoEncoder.utils.uploads import telegram as tg
    calls = []

    async def send():
        calls.append(1)
        raise InternalServerError()

    async def fake_sleep(s):
        pass
    with pytest.raises(InternalServerError):
        run(tg.with_retry(send, None, retries=2, sleep=fake_sleep))
    assert len(calls) == 3


def test_other_errors_and_huge_waits_are_not_retried():
    from pyrogram.errors import FloodWait
    from VideoEncoder.utils.uploads import telegram as tg
    calls = []

    async def bad():
        calls.append(1)
        raise ValueError("file too big")

    async def huge():
        calls.append(2)
        raise FloodWait(value=5000)

    async def fake_sleep(s):
        raise AssertionError("must not sleep")
    with pytest.raises(ValueError):
        run(tg.with_retry(bad, None, sleep=fake_sleep))
    with pytest.raises(FloodWait):
        run(tg.with_retry(huge, None, sleep=fake_sleep))
    assert calls == [1, 2]


def test_new_settings_are_documented():
    sample = open("config.env.sample").read()
    for key in ("SINGLE_INSTANCE=", "SOURCE_CACHE_MIN=", "SOURCE_CACHE_GB="):
        assert key in sample
    assert ffcmd.DEFAULTS["size_guard"] is True

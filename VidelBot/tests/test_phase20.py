"""Phase 20 – smarter, faster /compress.

• size watch: an encode heading for a file bigger than the source is stopped early (the rest of the time is saved)
• warnings before starting: unreachable targets, files already under the target, already-lean sources
• quick presets (📱 Mobile · 💬 10 MB · 📧 25 MB) and `/compress mobile`
• encode-time estimate learned from this server's own past compressions
"""
import asyncio
import os
import shutil
import subprocess

import pytest

from tests.harness import load_all, no_botapi, reset_db, run
from tests.test_phase5 import _bot_api_html_problems

MODULES = load_all()

from VideoEncoder import data as queue  # noqa: E402
from VideoEncoder.utils import compress as C, ffcmd, jobs, scheduler  # noqa: E402
import VideoEncoder.utils.encoding as encoding  # noqa: E402
import VideoEncoder.plugins.compress as P  # noqa: E402
from tests.test_phase19 import FileMsg, _panel_key, _run_compress, _tap, _video, cmp, e2e, _command, st  # noqa: E402,F401

FF = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FF, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    no_botapi()
    saved = list(queue)
    queue.clear()
    scheduler._running.clear()
    P._panels.clear()
    encoding.SIZE_WATCH.clear()
    yield
    queue[:] = saved
    scheduler._running.clear()
    for d in (scheduler.MODES, scheduler.EXTRA, scheduler.NOTES):
        d.clear()
    jobs._JOBS.clear()
    jobs._RECENT.clear()


HOUR = {"name": "Film.mkv", "size": 2 * 1024 ** 3, "duration": 3600, "height": 1080, "width": 1920}


# ─────────────────────────── warnings ───────────────────────────
def test_unreachable_target_is_flagged_with_the_real_minimum():
    w = C.warnings({"target": 10}, HOUR)
    assert w and "too small" in w[0] and "1:00:00" in w[0]
    need = C.min_target_mb(3600, C.audio_kbps(C.normalize({"target": 10})))
    # at the suggested minimum the encoder's own bitrate maths is no longer clamped at its floor
    assert ffcmd.target_video_kbps(need, 3600, C.audio_kbps(C.normalize({}))) >= C.MIN_VIDEO_KBPS
    assert not [x for x in C.warnings({"target": need + 5}, HOUR) if "too small" in x]


def test_already_under_target_and_already_lean():
    small = {"size": 20 * 1024 ** 2, "duration": 60, "height": 720}
    assert any("already under 25 MB" in x for x in C.warnings({"target": 25}, small))
    lean = {"size": 60 * 1024 ** 2, "duration": 1200, "height": 1080}           # ≈ 420 kbps 1080p
    assert any("already light" in x for x in C.warnings({}, lean))
    assert not C.warnings({}, HOUR)                                             # ≈ 4.7 Mbps – fine


def test_warnings_show_on_the_panel_and_the_one_shot_card():
    lean = {"name": "x.mp4", "size": 60 * 1024 ** 2, "duration": 1200, "height": 1080}
    for text in (C.panel_text({}, lean), C.queued_text({"target": 10}, HOUR)):
        assert "⚠️" in text and not _bot_api_html_problems(text)


# ─────────────────────────── quick presets ───────────────────────────
def test_quick_presets():
    o, toast = C.apply_quick(C.DEFAULT, "mobile", HOUR)
    assert (o["res"], o["level"], o["audio"], o["target"]) == ("480", "strong", "64", 0) and "Mobile" in toast
    o, _ = C.apply_quick(C.DEFAULT, "10", HOUR)
    assert o["target"] == 10 and o["res"] == "360"
    o, _ = C.apply_quick(C.DEFAULT, "mobile", {"height": 360})
    assert o["res"] == "360"                                                   # never an upscale
    assert C.apply_quick(C.DEFAULT, "nope", HOUR)[0] == C.normalize(C.DEFAULT)
    assert C.parse_args("/compress mobile")["level"] == "strong"
    assert C.parse_args("/compress mobile 360")["res"] == "360"                # later words win


def test_quick_row_marks_the_active_preset():
    kb = C.keyboard(C.apply_quick(C.DEFAULT, "25", HOUR)[0], HOUR).inline_keyboard
    quick = kb[1]
    assert [b.callback_data for b in quick] == ["cmp:quick:mobile", "cmp:quick:10", "cmp:quick:25"]
    assert quick[2].text.startswith("✅") and not quick[0].text.startswith("✅")


def test_quick_button_on_the_panel(cmp):
    m = _command("/compress", reply=_video(height=1080))
    run(P.compress_cmd(None, m))
    panel, key = _panel_key(m)
    q = _tap("cmp:quick:mobile", panel)
    assert P._panels[key]["opts"]["level"] == "strong" and "Mobile" in q.answers[-1][0]
    assert "480p" in panel.edits[-1]


# ─────────────────────────── ETA ───────────────────────────
def test_eta_is_learned_per_codec_and_size():
    key = C.speed_key({"res": "720"}, 1080)
    assert key == "h264:720" and C.speed_key({"res": "1080"}, 720) == "h264:720"
    speeds = C.learn({}, key, 4.0)
    assert speeds[key] == 4.0
    speeds = C.learn(speeds, key, 2.0)
    assert 2.0 < speeds[key] < 4.0                                            # averaged, not replaced
    assert C.eta_seconds({"res": "720"}, HOUR, {key: 4.0}) == 900
    assert C.eta_seconds({"res": "480"}, HOUR, {key: 4.0}) is None             # never measured
    assert "Encode time" in C.panel_text({"res": "720"}, HOUR, {key: 4.0}).replace("ᴇɴᴄᴏᴅᴇ ᴛɪᴍᴇ", "Encode time")
    assert "ᴇɴᴄᴏᴅᴇ ᴛɪᴍᴇ" not in C.panel_text({"res": "720"}, HOUR, {})


def test_speed_history_round_trips_through_the_db():
    from VideoEncoder.utils import tasks
    run(tasks.compress_learn("h264:360", 6.0))
    run(tasks.compress_learn("h264:360", 3.0))
    speeds = run(tasks.compress_speeds())
    assert 3.0 < speeds["h264:360"] < 6.0


# ─────────────────────────── size watch ───────────────────────────
def test_watch_verdict():
    assert C.projected_size(10, 1, 4) == 40
    assert not C.watch_verdict(900, 10, 100, 1000)                            # 10 % – too early to judge
    assert C.watch_verdict(300, 20, 100, 1000)                                # 1500 ≥ 1150
    assert not C.watch_verdict(220, 20, 100, 1000)                            # 1100 < 1150 – give it the benefit
    assert not C.watch_verdict(300, 20, 100, 0)


class WatchProc:
    def __init__(self):
        self.returncode = None
        self.killed = False
        self._done = asyncio.Event()

    async def wait(self):
        await self._done.wait()
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._done.set()


def test_handle_progress_stops_a_growing_encode(tmp_path, monkeypatch):
    monkeypatch.setattr(encoding, "PROGRESS_TICK", 0.01)
    prog = tmp_path / "p.txt"
    prog.write_text("out_time_us=30000000\ntotal_size=600\nspeed=2x\nprogress=continue\n")  # 30 % → 2000 B

    class M:
        id = 4711

        async def edit(self, *a, **k):
            return self
    proc = WatchProc()
    run(encoding.handle_progress(proc, M(), None, "in.mkv", progress_file=str(prog), total_time=100, watch=1000))
    assert proc.killed and encoding.SIZE_WATCH[4711][0] == 2000 and abs(encoding.SIZE_WATCH[4711][1] - 0.3) < 1e-6
    proc2 = WatchProc()
    prog.write_text("out_time_us=30000000\ntotal_size=100\nspeed=2x\nprogress=continue\n")

    async def finish():
        await asyncio.sleep(0.1)
        proc2.returncode = 0
        proc2._done.set()

    async def both():
        await asyncio.gather(encoding.handle_progress(proc2, M(), None, "in.mkv", progress_file=str(prog),
                                                      total_time=100, watch=1000), finish())
    encoding.SIZE_WATCH.clear()
    run(both())
    assert not proc2.killed and not encoding.SIZE_WATCH                       # a shrinking encode is left alone


@needs_ffmpeg
def test_e2e_size_watch_stops_a_pointless_compress(e2e, monkeypatch):
    """A tiny, heavily compressed noisy source 'compressed' at a high quality would come out much bigger –
    the watch stops it partway and nothing is uploaded."""
    e = e2e
    monkeypatch.setattr(encoding, "PROGRESS_TICK", 0.2)
    monkeypatch.setitem(C.PRESET, "h264", "m")                    # slower, so the watch sees a few ticks
    src = e.tmp / "noise.mp4"
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x360:rate=24:duration=40,noise=alls=40:allf=t", "-f", "lavfi", "-i",
                    "sine=f=440:duration=40", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "51",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "32k", str(src)], check=True)
    e.infos["noise.mp4"] = ffcmd.summarize({"streams": [st(0, "video", "h264", width=640, height=360),
                                                        st(1, "audio", "aac", channels=1, sample_rate="44100")],
                                            "format": {"duration": "40", "size": str(os.path.getsize(src))}})
    fm = FileMsg(src)
    fm.video.height, fm.video.width, fm.video.duration = 360, 640, 40
    edits = _run_compress(e, fm, {"res": "360", "level": "light", "audio": "128"})
    assert not e.uploaded
    last = edits[-1]
    assert "𝗔𝗟𝗥𝗘𝗔𝗗𝗬 𝗖𝗢𝗠𝗣𝗔𝗖𝗧" in last and "ꜱᴛᴏᴘᴘᴇᴅ ᴀᴛ" in last, last
    assert not _bot_api_html_problems(last)
    import re
    pct = int(re.search(r"(\d+)% –", last).group(1))
    assert 15 <= pct < 95, pct                                    # stopped well before the end
    assert not encoding.SIZE_WATCH

"""Phase 14 – Encoder Pro: a tested ffmpeg command builder (codec-correct profiles / tunes, aspect-safe
scaling, MP4/AVI-safe streams), quick profiles, target-size mode, filters, a real ❌ Cancel,
live progress + before/after summary, /sample /trim /screens, and single-read settings menus."""
import asyncio
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run
from tests.test_phase5 import _bot_api_html_problems

MODULES = load_all()

from VideoEncoder.utils import ffcmd, jobs  # noqa: E402
import VideoEncoder.utils.encoding as encoding  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    no_botapi()
    yield
    jobs._JOBS.clear()
    jobs._RECENT.clear()


class Msg(FakeMsg):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


def st(i, t, c, **k):
    return dict(index=i, codec_type=t, codec_name=c, **k)


PROBE = {"streams": [st(0, "video", "h264", width=1280, height=544), st(1, "audio", "aac", channels=6),
                     st(2, "audio", "vorbis", channels=1), st(3, "subtitle", "subrip"),
                     st(4, "subtitle", "hdmv_pgs_subtitle"),
                     st(5, "video", "mjpeg", disposition={"attached_pic": 1})],
         "format": {"duration": "600", "size": str(700 * 1024 * 1024)}}
INFO = ffcmd.summarize(PROBE)


def cmd_for(**s):
    return ffcmd.build_command("in.mkv", "out" + ffcmd.output_ext(ffcmd.merge(s)), s, INFO)


def val(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


# ───────────────────────── command builder ─────────────────────────
@pytest.mark.parametrize("hevc,bits,lib,profile,pix", [
    (False, False, "libx264", "high", "yuv420p"), (False, True, "libx264", "high10", "yuv420p10le"),
    (True, False, "libx265", "main", "yuv420p"), (True, True, "libx265", "main10", "yuv420p10le")])
def test_profile_follows_codec_and_bit_depth(hevc, bits, lib, profile, pix):
    c = cmd_for(hevc=hevc, bits=bits)
    assert (val(c, "-c:v"), val(c, "-profile:v"), val(c, "-pix_fmt")) == (lib, profile, pix)


def test_tunes_are_valid_for_each_encoder():
    assert val(cmd_for(hevc=False, tune=False), "-tune") == "film"
    assert val(cmd_for(hevc=True, tune=False), "-tune") is None          # x265 has no `film` tune
    assert val(cmd_for(hevc=True, tune=True), "-tune") == "animation"
    x265 = cmd_for(hevc=True, cabac=True, reframe="4")
    assert "-coder" not in x265 and "-refs" not in x265                   # x264-only options
    x264 = cmd_for(cabac=True, reframe="4")
    assert val(x264, "-coder") == "1" and val(x264, "-refs") == "4"


def test_scaling_keeps_aspect_and_never_upscales():
    assert "scale=-2:480:flags=lanczos" in val(cmd_for(resolution="480"), "-vf")
    assert "-vf" not in cmd_for(resolution="720")                          # source is 544p → no upscale
    unknown = ffcmd.build_command("a", "b.mkv", {"resolution": "480"}, ffcmd.summarize(None))
    assert "scale=-2:480" in val(unknown, "-vf")


def test_only_the_real_video_stream_is_mapped():
    c = cmd_for()
    maps = [c[i + 1] for i, x in enumerate(c) if x == "-map"]
    assert maps[0] == "0:0" and "0:5" not in maps and "0:v?" not in maps
    assert ffcmd.summarize(None)["ok"] is False
    assert "0:v:0?" in ffcmd.build_command("a", "b.mkv", {}, ffcmd.summarize(None))


def test_mp4_output_is_player_safe():
    c = cmd_for(extensions="MP4", hevc=True, audio="copy")
    assert val(c, "-tag:v") == "hvc1" and val(c, "-movflags") == "+faststart"
    maps = [c[i + 1] for i, x in enumerate(c) if x == "-map"]
    assert "0:3" in maps and "0:4" not in maps and val(c, "-c:s") == "mov_text"   # PGS can't go into MP4
    assert val(c, "-c:a") == "aac"                                             # vorbis can't be copied into MP4
    mkv = cmd_for(audio="copy")
    assert val(mkv, "-c:a") == "copy" and val(mkv, "-c:s") == "copy" and "0:t?" in mkv


def test_avi_and_opus_and_loudnorm_audio_rules():
    avi = cmd_for(extensions="AVI", audio="opus")
    assert val(avi, "-c:a") == "ac3" and "-c:s" not in avi
    opus = cmd_for(audio="opus", sample="44.1K", channels="5.1")
    assert val(opus, "-ar") == "48000" and "aformat" in val(opus, "-af") and val(opus, "-ac") == "6"
    loud = cmd_for(audio="copy", loudnorm=True)
    assert val(loud, "-c:a") == "aac" and "loudnorm" in val(loud, "-af") and val(loud, "-ar") == "48000"


def test_target_size_mode_computes_a_bitrate():
    c = cmd_for(mode="size", target_mb=100, audio="aac", bitrate="128")
    assert "-crf" not in c
    kbps = int(val(c, "-b:v")[:-1])
    total_mb = (kbps + 2 * 128) * 600 / 8 / 1024
    assert 90 < total_mb < 100 and val(c, "-maxrate") and val(c, "-bufsize")
    assert ffcmd.target_video_kbps(1, 3600, 128) == 150                    # floor for impossible targets
    assert "-crf" in cmd_for(mode="size", target_mb=0)


def test_sample_hardsub_watermark_and_audio_map():
    c = ffcmd.build_command("in.mkv", "o.mkv", {"hardsub": True}, INFO, sample=(285.0, 30.0),
                            subs_file="/tmp/it's, [x].ass", watermark_file="/tmp/wm.ass", audio_map=[2, 1])
    assert c[c.index("-i") - 4:c.index("-i")] == ["-ss", "285.00", "-t", "30.00"]
    vf = val(c, "-vf")
    assert "subtitles=filename=/tmp/wm.ass" in vf and "it\\\\\\'s\\, \\[x\\].ass" in vf
    assert "-c:s" not in c                                                 # samples / hardsub: no soft subs
    assert c[c.index("0:2") - 1] == "-map" and c.index("0:2") < c.index("0:1")
    assert val(c, "-disposition:a:0") == "default" and val(c, "-disposition:a:1") == "0"


def test_every_profile_builds_and_is_detected():
    for key, (_label, _desc, vals) in ffcmd.PROFILES.items():
        s = ffcmd.merge(vals)
        assert ffcmd.matches_profile(s) == key
        assert ffcmd.build_command("a", "b.mkv", s, INFO)[-1] == "b.mkv"
    assert ffcmd.matches_profile(ffcmd.merge({"crf": 13})) is None


def test_merge_survives_legacy_and_junk_documents():
    s = ffcmd.merge({"crf": "abc", "target_mb": None, "_id": 1, "hevc": True, "unknown": 5})
    assert s["crf"] == 22 and s["target_mb"] == 0 and s["hevc"] is True and "unknown" not in s
    assert ffcmd.merge({"crf": 99})["crf"] == 51


def test_time_parsing_trim_and_screens():
    assert [ffcmd.parse_timestamp(x) for x in ("90", "1:30", "01:02:03.5", "", "a", "1:2:3:4", "-5")] == \
        [90, 90, 3723.5, None, None, None, None]
    from VideoEncoder.utils.tasks import parse_trim_args
    assert parse_trim_args("/trim 1:00 2:30") == (60, 150) and parse_trim_args("/trim 90") == (90, None)
    assert parse_trim_args("/trim 2:00 1:00") is None and parse_trim_args("/trim") is None
    assert ffcmd.screenshot_times(100, 4) == [20, 40, 60, 80] and len(ffcmd.screenshot_times(100, 50)) == 10
    assert ffcmd.sample_window(600, 30) == (285.0, 30.0) and ffcmd.sample_window(10, 30) == (0.0, 10.0)
    assert ffcmd.fmt_ts(3723) == "01:02:03"
    t = ffcmd.trim_command("a.mp4", "b.mp4", 60, 150)
    assert val(t, "-ss") == "60.000" and val(t, "-to") == "150.000" and val(t, "-c") == "copy"


# ───────────────────────── real ffmpeg (skipped when unavailable) ─────────────────────────
def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return exe


FF = _ffmpeg()
needs_ffmpeg = pytest.mark.skipif(not FF, reason="ffmpeg not installed")


@pytest.fixture
def clip(tmp_path):
    src = tmp_path / "src.mkv"
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=640x272:rate=24:duration=3", "-f", "lavfi", "-i", "sine=f=440:duration=3",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "libvorbis", str(src)], check=True)
    info = ffcmd.summarize({"streams": [st(0, "video", "h264", width=640, height=272),
                                        st(1, "audio", "vorbis", channels=1)],
                            "format": {"duration": "3", "size": str(src.stat().st_size)}})
    return src, info


def _run(cmd):
    cmd = [FF] + cmd[1:]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode, r.stderr


@needs_ffmpeg
@pytest.mark.parametrize("settings", [
    dict(hevc=True, bits=True, extensions="MP4", resolution="480", tune=False, preset="uf"),   # was: main + 10-bit → fail
    dict(hevc=True, tune=False, preset="uf"),                                                 # was: x265 -tune film → fail
    dict(bits=True, preset="uf", audio="opus", sample="44.1K", loudnorm=True, deinterlace=True, denoise=True),
    dict(audio="copy", extensions="MP4", preset="uf", mode="size", target_mb=1),
    dict(audio="opus", extensions="AVI", preset="uf"),
])
def test_real_ffmpeg_accepts_the_commands(clip, tmp_path, settings):
    src, info = clip
    out = tmp_path / ("o" + ffcmd.output_ext(ffcmd.merge(settings)))
    code, err = _run(ffcmd.build_command(str(src), str(out), settings, info, progress=str(tmp_path / "p.txt")))
    assert code == 0 and out.stat().st_size > 0, err


@needs_ffmpeg
def test_real_ffmpeg_trim_and_screenshot(clip, tmp_path):
    src, _ = clip
    out = tmp_path / "cut.mkv"
    assert _run(ffcmd.trim_command(str(src), str(out), 1.0, 2.5))[0] == 0 and out.stat().st_size
    shot = tmp_path / "s.jpg"
    assert _run(ffcmd.screenshot_command(str(src), str(shot), 1.5))[0] == 0 and shot.stat().st_size


# ───────────────────────── encode() / progress / summary ─────────────────────────
def test_encode_reads_settings_once_and_uses_a_per_task_progress_file(monkeypatch, tmp_path):
    from VideoEncoder.utils.database.access_db import db
    src = tmp_path / "in.mkv"
    src.write_bytes(b"\0" * 64)
    run(db.update_settings(5, hevc=True, bits=True, extensions="MP4"))
    calls, captured = [], {}
    real = db.get_settings

    async def counting(uid):
        calls.append(uid)
        return await real(uid)

    class Stop(Exception):
        pass

    async def fake_exec(*cmd, **k):
        captured["cmd"] = cmd
        raise Stop

    async def fake_probe(_):
        return INFO
    monkeypatch.setattr(db, "get_settings", counting)
    monkeypatch.setattr(encoding, "probe", fake_probe)
    monkeypatch.setattr(encoding.asyncio, "create_subprocess_exec", fake_exec)
    status = Msg("s", uid=5)
    with pytest.raises(Stop):
        run(encoding.encode(str(src), Msg("/dl", uid=5), status))
    cmd = list(captured["cmd"])
    assert calls == [5] and val(cmd, "-profile:v") == "main10" and cmd[-1].endswith("in.mp4")
    assert val(cmd, "-progress").endswith(f"process_{status.id}.txt")


def test_progress_card_and_summary_card():
    txt = encoding.progress_text("movie.mkv", 30, 60, 2.0, 48.0, 10 * 1024 * 1024, 20,
                                 ffcmd.merge({"hevc": True, "crf": 24}))
    assert "50%" in txt and "48 fps" in txt and "ETA 15s" in txt and "≈ 20.00 MB" in txt and "H.265" in txt
    assert not _bot_api_html_problems(txt)
    res = encoding.Encoded("/x/movie.mkv")
    res.info, res.settings, res.elapsed, res.sample = {"size": 1000, "duration": 100}, {}, 50.0, None
    card = encoding.summary_text(res, 400)
    assert "−60.0%" in card and "2.00x" in card and not _bot_api_html_problems(card)
    res.sample = (40.0, 10.0)
    sample = encoding.summary_text(res, 40, title="Sample ready")
    assert "ꜰᴜʟʟ ᴠɪᴅᴇᴏ ≈" in sample and "400" in sample and "/dl" in sample


def test_handle_progress_stops_polling_when_cancelled(monkeypatch):
    real_sleep = asyncio.sleep

    async def fast(_=0):
        await real_sleep(0)

    class Proc:
        pid, returncode = 1, None
    monkeypatch.setattr(encoding.asyncio, "sleep", fast)
    status = Msg("s", uid=5)
    jobs.register(status.id, 5, 5)
    jobs._JOBS[status.id].cancelled = True
    run(asyncio.wait_for(encoding.handle_progress(Proc(), status, Msg("/dl", uid=5), "x.mkv",
                                                  progress_file="/nonexistent", total_time=10), 2))
    assert status.edits == []


# ───────────────────────── cancel ─────────────────────────
class FakeProc:
    def __init__(self):
        self.returncode = None
        self.terminated = self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def test_cancel_kills_ffmpeg_and_checks_permissions():
    from VideoEncoder.plugins.callbacks_ import callback_handlers
    proc = FakeProc()
    jobs.register(77, 5, 5, name="movie")
    jobs.attach(77, proc)
    q = FakeQuery("enc_cancel:77", uid=6)                 # a stranger
    run(callback_handlers(FakeClient(), q))
    assert not proc.terminated and "Only the user" in q.answers[-1][0]
    q = FakeQuery("enc_cancel:77", uid=5)                 # the owner of the task
    c = FakeClient()
    run(callback_handlers(c, q))
    assert proc.terminated and jobs.is_cancelled(77) and "cancelled" in q.message.edits[-1]
    assert c.called("send_message")                       # log channel note (valid HTML, no markdown crash)
    q = FakeQuery("enc_cancel:999", uid=5)
    run(callback_handlers(FakeClient(), q))
    assert "already finished" in q.answers[-1][0]


def test_admin_can_cancel_and_legacy_cancel_button_targets_latest():
    from VideoEncoder.plugins.callbacks_ import callback_handlers
    proc = FakeProc()
    jobs.register(88, 5, 5)
    jobs.attach(88, proc)
    run(callback_handlers(FakeClient(), FakeQuery("cancel", uid=111)))
    assert proc.terminated


def test_transfer_progress_raises_stop_transmission_after_cancel():
    from pyrogram import StopTransmission
    from VideoEncoder.utils.display_progress import progress_for_pyrogram
    status = Msg("s", uid=5)
    jobs.register(status.id, 5, 5, stage="download")
    run(progress_for_pyrogram(10, 100, "Downloading...", status, 0))
    assert status.edits                                     # progress shown (with a ❌ Cancel button)
    run(jobs.cancel(status.id))
    with pytest.raises(StopTransmission):
        run(progress_for_pyrogram(20, 100, "Downloading...", status, 0))


def test_stats_alert_fits_telegram_limit():
    from VideoEncoder.plugins.callbacks_ import _stats_text, callback_handlers
    jobs.register(1, 5, 5, name="x" * 300)
    assert len(_stats_text()) <= 200
    q = FakeQuery("stats", uid=5)
    run(callback_handlers(FakeClient(), q))
    assert q.answers[-1][1] is True and len(q.answers[-1][0]) <= 200


# ───────────────────────── settings menus ─────────────────────────
def _press(data, uid=5):
    from VideoEncoder.plugins.callbacks_ import callback_handlers
    q = FakeQuery(data, uid=uid)
    run(callback_handlers(FakeClient(), q))
    return q


def _settings(uid=5):
    from VideoEncoder.utils.database.access_db import db
    return run(db.get_settings(uid))


@pytest.mark.parametrize("data,title", [("OpenSettings", "𝗘𝗡𝗖𝗢𝗗𝗘𝗥"), ("VideoSettings", "𝗩𝗜𝗗𝗘𝗢"),
                                        ("AudioSettings", "𝗔𝗨𝗗𝗜𝗢"), ("AdvancedSettings", "𝗔𝗗𝗩𝗔𝗡𝗖𝗘𝗗"),
                                        ("ExtraSettings", "𝗘𝗫𝗧𝗥𝗔𝗦"), ("EncProfiles", "𝗣𝗥𝗢𝗙𝗜𝗟𝗘𝗦")])
def test_menus_render_valid_html(data, title):
    q = _press(data)
    text = q.message.edits[-1]
    assert title in text and not _bot_api_html_problems(text)


def test_profile_one_tap_and_crf_stepper():
    q = _press("encp:balanced")
    s = _settings()
    assert s["hevc"] is True and s["resolution"] == "720" and ffcmd.matches_profile(s) == "balanced"
    assert "Balanced applied" in q.answers[-1][0]
    _press("triggerCRF")
    _press("triggerCRFdown")
    _press("triggerCRFdown")
    assert _settings()["crf"] == 25
    from VideoEncoder.utils.database.access_db import db
    run(db.update_settings(5, crf=40))
    q = _press("triggerCRF")
    assert _settings()["crf"] == 40 and "range" in q.answers[-1][0]


def test_advanced_toggles_target_size_and_legacy_label():
    _press("triggerEncMode")
    assert _settings()["mode"] == "size" and _settings()["target_mb"] == 200
    _press("triggerTargetSize")
    assert _settings()["target_mb"] == 300
    _press("triggerTargetDown")
    _press("triggerTargetDown")
    assert _settings()["target_mb"] == 100
    for d, f in (("triggerDeint", "deinterlace"), ("triggerDenoise", "denoise"), ("triggerLoudnorm:a", "loudnorm")):
        _press(d)
        assert _settings()[f] is True
    q = _press("Watermark")
    assert q.answers == [(None, False)]                    # old label buttons: silent, no “not works XD”


def test_cycles_keep_the_original_orders_and_h264_10bit_is_allowed():
    _press("triggerPreset")
    assert _settings()["preset"] == "vf"                   # sf → vf
    _press("triggerResolution")
    assert _settings()["resolution"] == "1080"
    _press("triggerBits")                                  # H.264 10-bit used to be refused
    assert _settings()["bits"] is True and _settings()["hevc"] is False


def test_menu_buttons_have_no_dead_labels():
    from VideoEncoder.utils import settings as menus
    for fn in (menus.OpenSettings, menus.VideoSettings, menus.AudioSettings, menus.AdvancedSettings,
               menus.ExtraSettings, menus.ProfileSettings):
        m = FakeMsg("menu", uid=5)
        seen = {}

        async def edit(text=None, reply_markup=None, **k):
            seen["kb"] = reply_markup
        m.edit = edit
        run(fn(m, 5))
        datas = [b.callback_data for row in seen["kb"].inline_keyboard for b in row]
        assert "Watermark" not in datas, fn.__name__


# ───────────────────────── task flows ─────────────────────────
def test_handle_encode_success_shows_summary_with_open_button(monkeypatch, tmp_path):
    from VideoEncoder.utils import helper
    src, out = tmp_path / "in.mkv", tmp_path / "out.mkv"
    src.write_bytes(b"\0" * 1000)
    out.write_bytes(b"\0" * 400)

    async def fake_encode(*a, **k):
        r = encoding.Encoded(str(out))
        r.info, r.settings, r.elapsed, r.sample = {"size": 1000, "duration": 10}, {}, 5.0, None
        return r

    async def fake_upload(*a, **k):
        return "https://t.me/c/1/2"
    monkeypatch.setattr(helper, "encode", fake_encode)
    monkeypatch.setattr(helper, "upload_worker", fake_upload)
    status, seen = Msg("s", uid=5), {}

    async def edit(text, reply_markup=None, **k):
        seen["text"], seen["kb"] = text, reply_markup
    status.edit = edit
    link = run(helper.handle_encode(str(src), Msg("/dl", uid=5), status))
    assert link == "https://t.me/c/1/2" and "−60.0%" in seen["text"]
    assert seen["kb"].inline_keyboard[0][0].url == link
    assert not src.exists() and not out.exists()
    assert _settings()["enc_count"] == 1 and _settings()["enc_in"] == 1000


def test_handle_encode_failure_shows_the_ffmpeg_error(monkeypatch, tmp_path):
    from VideoEncoder.utils import helper
    src = tmp_path / "in.mkv"
    src.write_bytes(b"\0")
    status, seen = Msg("s", uid=5), {}

    async def fake_encode(*a, **k):
        encoding.LAST_ERROR[status.id] = "Unknown encoder <libfoo>"
        return None

    async def edit(text, reply_markup=None, **k):
        seen["text"] = text
    status.edit = edit
    monkeypatch.setattr(helper, "encode", fake_encode)
    run(helper.handle_encode(str(src), Msg("/dl", uid=5), status))
    assert "Encoding failed" in seen["text"] and "&lt;libfoo&gt;" in seen["text"] and not src.exists()


def test_new_commands_queue_and_validate(monkeypatch):
    import VideoEncoder.plugins.encode as enc_plugin
    from VideoEncoder import data as queue
    recorded = []

    async def yes(*a, **k):
        return True

    async def record(message, mode):
        recorded.append(mode)
    monkeypatch.setattr(enc_plugin, "check_chat", yes)
    monkeypatch.setattr(enc_plugin, "AddUserToDatabase", yes)
    monkeypatch.setattr(enc_plugin, "handle_tasks", record)
    monkeypatch.setattr(enc_plugin.asyncio, "sleep", yes)
    saved = list(queue)
    queue.clear()
    try:
        bare = Msg("/trim 1:00", uid=5)
        run(enc_plugin.trim_video(None, bare))                     # no video → usage, not queued
        assert "Trim" in bare.replies[-1] and not queue
        video = SimpleNamespace(file_name="a.mkv")
        for cmd, fn, mode in (("/sample 20", enc_plugin.sample_encode, "sample"),
                              ("/trim 1:00 2:00", enc_plugin.trim_video, "trim"),
                              ("/screens 4", enc_plugin.screens_video, "screens")):
            queue.clear()
            m = Msg(cmd, uid=5)
            m.video = video
            run(fn(None, m))
            assert recorded[-1] == mode
        bad = Msg("/trim 2:00 1:00", uid=5)
        bad.video = video
        run(enc_plugin.trim_video(None, bad))
        assert "Trim" in bad.replies[-1]
    finally:
        queue[:] = saved


def test_on_task_complete_dispatches_new_modes(monkeypatch):
    from VideoEncoder import data as queue
    from VideoEncoder.utils import tasks
    seen = []

    async def fake(message, mode):
        seen.append(mode)
    monkeypatch.setattr(tasks, "handle_tasks", fake)
    monkeypatch.setattr(tasks, "delete_downloads", lambda: None)
    saved = list(queue)
    try:
        for cmd, mode in (("/sample", "sample"), ("/trim 1 2", "trim"), ("/screens@VidelBot 3", "screens"),
                          ("/dl", "tg")):
            queue[:] = [Msg("/x"), Msg(cmd)]
            run(tasks.on_task_complete())
            assert seen[-1] == mode
    finally:
        queue[:] = saved


def test_upload_survives_a_broken_log_channel(monkeypatch):
    from VideoEncoder.utils.uploads import telegram as tg

    class Resp:
        video = SimpleNamespace(file_id="F")
        document = None
        link = "https://t.me/c/1/9"

    async def reply_video(*a, **k):
        return Resp()

    async def boom(*a, **k):
        raise RuntimeError("CHAT_ID_INVALID")
    m = Msg("/dl", uid=5)
    m.reply_video = reply_video
    monkeypatch.setattr(tg.app, "send_video", boom, raising=False)
    assert run(tg.upload_video(m, Msg("s"), "/x/a.mkv", "a.mkv", 0, None, 10, 640, 360)) == "https://t.me/c/1/9"

    async def cancelled(*a, **k):
        return None
    m.reply_video = cancelled
    assert run(tg.upload_video(m, Msg("s"), "/x/a.mkv", "a.mkv", 0, None, 10, 640, 360)) is None


def test_queue_and_vset_screens():
    from VideoEncoder.plugins.queue import queue_doc
    from VideoEncoder.plugins.settings import vset_doc
    from VideoEncoder import data as queue
    saved = list(queue)
    try:
        queue.clear()
        assert "No active encodes" in queue_doc().classic()
        m = Msg("/dl", uid=5)
        m.video = SimpleNamespace(file_name="<b>x</b>.mkv")
        queue[:] = [m, Msg("/sample", uid=6)]
        text = queue_doc().classic()
        assert "▶️" in text and "#2" in text and "&lt;b&gt;x" in text and not _bot_api_html_problems(text)
    finally:
        queue[:] = saved
    doc = vset_doc(5, ffcmd.merge(ffcmd.PROFILES["anime"][2]))
    text = doc.classic()
    assert "🎌" in text and "H.265 10-bit" in text and not _bot_api_html_problems(text)


def test_commands_registered_and_under_the_limit():
    from core import commands as cm
    names = {n for n, _ in cm.USER_COMMANDS}
    assert {"sample", "trim", "screens"} <= names
    total = len({n for g in (cm.USER_COMMANDS, cm.ADMIN_COMMANDS, cm.OWNER_COMMANDS) for n, _ in g})
    assert total <= 100
    from core.texts import ENC_HELP
    assert "/sample" in ENC_HELP and "/trim" in ENC_HELP and "/screens" in ENC_HELP

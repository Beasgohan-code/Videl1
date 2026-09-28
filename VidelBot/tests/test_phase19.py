"""Phase 19 – faster encoding + 🗜 /compress.

• encoder speed: bicubic downscale on fast presets, deinterlace → scale → dedup → denoise order, smart audio copy,
  GPU decode with NVENC, decoder threads shared between parallel workers
• /compress: 1080p · 720p · 480p · 360p buttons, H.264 / H.265, level, audio, MP4 / MKV, target size, one-shot
  arguments, remembered choice, upscale lock, the panel becomes the status card, real-ffmpeg end-to-end run
"""
import os
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from tests.harness import FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run
from tests.test_phase5 import _bot_api_html_problems

MODULES = load_all()

from VideoEncoder import data as queue  # noqa: E402
from VideoEncoder.utils import compress as C, ffcmd, jobs, scheduler  # noqa: E402
import VideoEncoder.utils.encoding as encoding  # noqa: E402
import VideoEncoder.plugins.compress as P  # noqa: E402
from VideoEncoder.utils.database.access_db import db  # noqa: E402

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
    yield
    queue[:] = saved
    scheduler._running.clear()
    for d in (scheduler.MODES, scheduler.EXTRA, scheduler.NOTES):
        d.clear()
    scheduler.PRIO.clear()
    jobs._JOBS.clear()
    jobs._RECENT.clear()


class Msg(FakeMsg):
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


def st(i, t, c, **k):
    return dict(index=i, codec_type=t, codec_name=c, **k)


def info_of(h=1080, w=1920, audio=(("aac", 2, "48000"),), dur=600, size=900 * 1024 * 1024, interlaced=False):
    v = st(0, "video", "h264", width=w, height=h, field_order="tt" if interlaced else "progressive")
    a = [st(i + 1, "audio", c, channels=ch, sample_rate=sr) for i, (c, ch, sr) in enumerate(audio)]
    return ffcmd.summarize({"streams": [v] + a, "format": {"duration": str(dur), "size": str(size)}})


def val(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


# ═════════════════════════ encoder speed ═════════════════════════
def test_scaler_is_bicubic_on_fast_presets_and_lanczos_on_slow():
    for p in ("uf", "sf", "vf", "f"):
        assert "flags=bicubic" in val(ffcmd.build_command("i", "o.mkv", {"resolution": "720", "preset": p}, info_of()), "-vf")
    for p in ("m", "s"):
        assert "flags=lanczos" in val(ffcmd.build_command("i", "o.mkv", {"resolution": "720", "preset": p}, info_of()), "-vf")


def test_filters_run_on_the_small_picture():
    vf = val(ffcmd.build_command("i", "o.mkv", {"resolution": "480", "deinterlace": True, "dedup": True,
                                                "denoise": True}, info_of()), "-vf")
    order = [vf.index(x) for x in ("bwdif", "scale=", "mpdecimate", "hqdn3d")]
    assert order == sorted(order)


def test_360p_is_a_real_resolution_now():
    assert ffcmd.RESOLUTIONS["360"] == 360
    assert "scale=-2:360" in val(ffcmd.build_command("i", "o.mkv", {"resolution": "360"}, info_of()), "-vf")
    from VideoEncoder.plugins.callbacks_ import CYCLES
    assert "360" in CYCLES["triggerResolution"][1]


@pytest.mark.parametrize("s,audio,copy", [
    ({"audio": "aac"}, (("aac", 2, "48000"),), True),                    # already AAC → copy
    ({"audio": "aac", "bitrate": "128"}, (("aac", 2, "48000"),), False),  # explicit bitrate → re-encode
    ({"audio": "aac", "channels": "2.0"}, (("aac", 6, "48000"),), False),  # 5.1 → stereo needs an encode
    ({"audio": "aac", "channels": "2.0"}, (("aac", 2, "48000"),), True),   # already stereo
    ({"audio": "aac", "sample": "44.1K"}, (("aac", 2, "48000"),), False),
    ({"audio": "aac", "loudnorm": True}, (("aac", 2, "48000"),), False),
    ({"audio": "aac"}, (("aac", 2, "48000"), ("ac3", 6, "48000")), False),  # one track isn't AAC
    ({"audio": "opus"}, (("opus", 2, "48000"),), True),
    ({"audio": "dd"}, (("ac3", 6, "48000"),), True),
])
def test_smart_audio_copy(s, audio, copy):
    info = info_of(audio=audio)
    cmd = ffcmd.build_command("i", "o.mkv", s, info)
    assert (val(cmd, "-c:a") == "copy") is copy
    if copy:
        assert "-b:a" not in cmd and "-ac" not in cmd


def test_smart_audio_copy_follows_the_chosen_tracks_and_unknown_probes():
    info = info_of(audio=(("aac", 2, "48000"), ("ac3", 6, "48000")))
    assert ffcmd.audio_copy_ok({"audio": "aac"}, info, "aac", audio_map=[1])
    assert not ffcmd.audio_copy_ok({"audio": "aac"}, info, "aac", audio_map=[2])
    assert not ffcmd.audio_copy_ok({"audio": "aac"}, ffcmd.summarize(None), "aac")    # unknown → encode (safe)
    mp4 = ffcmd.build_command("i", "o.mp4", {"audio": "copy", "extensions": "MP4"}, info_of(audio=(("flac", 2, "48000"),)))
    assert val(mp4, "-c:a") == "aac"                                     # MP4 can't hold FLAC – still converted


def test_gpu_decode_and_decoder_threads():
    nv = ffcmd.build_command("i", "o.mkv", {}, info_of(), encoder="h264_nvenc")
    assert nv.index("-hwaccel") < nv.index("-i") and val(nv, "-hwaccel") == "cuda"
    cpu = ffcmd.build_command("i", "o.mkv", {}, info_of())
    assert "-hwaccel" not in cpu
    first = ffcmd.build_command("i", "o.mkv", {"mode": "size", "target_mb": 100, "twopass": True}, info_of(),
                                encoder="h264_nvenc", pass_no=1)
    assert "-hwaccel" not in first
    shared = ffcmd.build_command("i", "o.mkv", {}, info_of(), threads=3)
    assert shared[shared.index("-i") - 2:shared.index("-i")] == ["-threads", "3"] and shared[-3:-1] == ["-threads", "3"]
    assert shared.count("-threads") == 2
    assert ffcmd.build_command("i", "o.mkv", {}, info_of(), threads=0).count("-threads") == 1


@needs_ffmpeg
def test_smart_audio_copy_with_a_real_ffmpeg(tmp_path):
    src = tmp_path / "in.mp4"
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=1",
                    "-f", "lavfi", "-i", "sine=f=440:duration=1", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2", str(src)], check=True)
    out = tmp_path / "out.mkv"
    cmd = ffcmd.build_command(str(src), str(out), {"audio": "aac", "preset": "uf"}, info_of(h=240, w=320, dur=1))
    assert val(cmd, "-c:a") == "copy"
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0 and out.stat().st_size, r.stderr


# ═════════════════════════ /compress logic ═════════════════════════
def test_parse_args():
    assert C.parse_args("/compress") == {}
    assert C.parse_args("/compress 480p strong hevc 50mb mkv 64k") == {
        "res": "480", "level": "strong", "codec": "hevc", "target": 50, "fmt": "MKV", "audio": "64"}
    assert C.parse_args("/compress 1.5gb x264 keep 720") == {"target": 1500, "codec": "h264", "audio": "copy", "res": "720"}
    assert C.parse_args("/compress banana 999") == {}


def test_normalize_repairs_anything():
    o = C.normalize({"res": "4k", "codec": "vp9", "level": "?", "audio": 7, "target": "x", "fmt": "avi", "junk": 1})
    assert o == C.DEFAULT
    assert C.normalize({"res": "360p", "target": "200"})["res"] == "360"


def test_override_is_a_fast_clean_profile():
    o, notes = C.override({"res": "480"}, info_of(), 900 * 1024 * 1024)
    assert o["preset"] == "vf" and o["crf"] == 26 and o["cabac"] and not o["hevc"] and o["extensions"] == "MP4"
    assert not any(o[k] for k in ("watermark", "logo", "hardsub", "twopass", "av1", "denoise", "loudnorm"))
    cmd = ffcmd.build_command("in.mkv", "out.mp4", o, info_of())
    assert val(cmd, "-preset") == "veryfast" and val(cmd, "-coder") == "1" and val(cmd, "-crf") == "26"
    assert "scale=-2:480:flags=bicubic" in val(cmd, "-vf") and val(cmd, "-b:a") == "128k"
    hevc, _ = C.override({"codec": "hevc", "level": "strong"}, info_of())
    assert hevc["hevc"] and hevc["preset"] == "sf" and hevc["crf"] == 32


def test_override_audio_target_interlace_and_no_upscale():
    o, notes = C.override({"audio": "96"}, info_of(audio=(("ac3", 6, "48000"),)))
    assert o["channels"] == "2.0" and o["bitrate"] == "96" and "surround audio → stereo" in notes
    o, _ = C.override({"audio": "copy"}, info_of())
    assert o["audio"] == "copy"
    o, _ = C.override({"target": 100}, info_of(), 900 * 1024 * 1024)
    assert o["mode"] == "size" and o["target_mb"] == 100
    o, notes = C.override({"target": 500}, info_of(), 300 * 1024 * 1024)
    assert o["mode"] == "crf" and any("quality mode" in n for n in notes)
    o, _ = C.override({}, info_of(interlaced=True))
    assert o["deinterlace"]
    _, notes = C.override({"res": "1080"}, info_of(h=544, w=1280))
    assert any("never upscaled" in n for n in notes)


def test_upscale_lock_and_scope_films():
    meta720 = {"height": 720}
    assert [C.can_pick(r, meta720) for r in C.RES] == [False, True, True, True]
    assert C.can_pick("1080", {"height": 800})                        # 1920×800 scope film is 1080p
    assert not C.can_pick("1080", {"height": 536, "width": 1280}) and C.can_pick("720", {"height": 536, "width": 1280})
    assert [C.size_class(h) for h in (360, 480, 544, 720, 1080, 2160)] == [360, 480, 720, 720, 1080, 1080]
    assert all(C.can_pick(r, {}) for r in C.RES)                        # unknown height → everything allowed
    kb = C.keyboard(C.DEFAULT, meta720).inline_keyboard
    assert kb[0][0].callback_data == "cmp:lock:1080" and "🔒" in kb[0][0].text
    assert kb[0][1].text.startswith("✅")                                 # 720p selected


def test_estimates_are_ordered_and_capped():
    meta = {"duration": 1200, "size": 2 * 1024 ** 3, "height": 1080}
    e = {r: C.estimate({"res": r}, meta) for r in C.RES}
    assert e["1080"] > e["720"] > e["480"] > e["360"]
    lv = [C.estimate({"level": k}, meta) for k in ("light", "balanced", "strong")]
    assert lv == sorted(lv, reverse=True)
    assert C.estimate({"codec": "hevc"}, meta) < C.estimate({"codec": "h264"}, meta)
    assert C.estimate({"res": "1080", "level": "light"}, {"duration": 7200, "size": 100 * 1024 ** 2}) == 100 * 1024 ** 2
    assert C.estimate({"target": 50}, meta) == 50 * 1024 * 1024
    assert C.estimate({}, {"size": 5}) is None


def test_apply_cycles():
    o = C.normalize({})
    o, t = C.apply(o, "res", "360")
    assert o["res"] == "360" and "360p" in t
    o, _ = C.apply(o, "target")
    assert o["target"] == 10                                              # Phase 20 added a 10 MB step
    o, _ = C.apply(o, "level", "strong")
    assert o["level"] == "strong" and o["target"] == 0                   # a level switches target mode off
    seen = []
    for _ in C.AUDIO:
        o, _ = C.apply(o, "audio")
        seen.append(o["audio"])
    assert sorted(seen) == sorted(C.AUDIO)
    o, _ = C.apply(o, "fmt")
    assert o["fmt"] == "MKV"
    assert C.apply(o, "res", "9999")[0] == o


def test_cards_are_valid_html_and_small_caps():
    meta = {"name": "Movie <2024> & co.mkv", "size": 1500 * 1024 ** 2, "duration": 5400, "height": 1080}
    texts = [C.panel_text(C.DEFAULT, meta), C.panel_text({"target": 200, "codec": "hevc", "fmt": "MKV", "audio": "copy"}, meta),
             C.usage_text(), C.queued_text(C.DEFAULT, meta),
             C.working_text(C.DEFAULT, info_of(), ["kept 544p – never upscaled"]),
             C.done_text("Movie [720p].mp4", C.DEFAULT, info_of(), 1500 * 1024 ** 2, 400 * 1024 ** 2, 300.0, []),
             C.bigger_text(C.DEFAULT, 100, 120)]
    for t in texts:
        assert not _bot_api_html_problems(t), t
    assert "&lt;2024&gt; &amp; co" in texts[0]                           # the file name is escaped
    assert "ǫᴜᴀʟɪᴛʏ" in texts[0] and "ꜰɪʟᴇ" not in texts[0].split("━")[0]   # small-caps labels
    assert "−73%" in texts[5] or "(−73.3%)" in texts[5]
    kb_text = " ".join(b.text for row in C.keyboard(C.DEFAULT, meta).inline_keyboard for b in row)
    assert "ʙᴀʟᴀɴᴄᴇᴅ" in kb_text and "ꜱᴛᴀʀᴛ" in kb_text and "360ᴘ" in kb_text


# ═════════════════════════ /compress handlers ═════════════════════════
def _video(uid=5, height=1080, name="Movie.mkv"):
    m = Msg(None, uid=uid)
    m.video = SimpleNamespace(file_name=name, file_size=1400 * 1024 * 1024, duration=5400, height=height,
                              width=int(height * 16 / 9), mime_type="video/x-matroska", file_unique_id="U" + name)
    return m


@pytest.fixture
def cmp(monkeypatch):
    import VideoEncoder.plugins.tools as t
    queued = []

    async def yes(*a, **k):
        return True

    async def fake_enqueue(message, mode, extra=None, card=None):
        queued.append((message, mode, extra, card))
        return True
    monkeypatch.setattr(t, "check_chat", yes)
    monkeypatch.setattr(t, "AddUserToDatabase", yes)
    monkeypatch.setattr(P, "_enqueue", fake_enqueue)
    P.queued = queued
    return P


def _command(text, reply=None, uid=5):
    m = Msg(text, uid=uid)
    m.reply_to_message = reply
    return m


def _panel_key(m):
    panel = m.sent[-1]
    return panel, (m.chat.id, panel.id)


def _tap(data, panel, uid=5):
    q = FakeQuery(data, uid=uid, message=panel)
    panel.markups = getattr(panel, "markups", [])

    async def edit(text, reply_markup=None, **k):
        panel.edits.append(text)
        panel.markups.append(reply_markup)
    panel.edit = panel.edit_text = edit
    run(P.compress_cb(None, q))
    return q


def test_bare_command_shows_the_guide(cmp):
    m = _command("/compress")
    run(P.compress_cmd(None, m))
    assert "File compressor".upper() in m.replies[-1].replace("𝗙𝗜𝗟𝗘 𝗖𝗢𝗠𝗣𝗥𝗘𝗦𝗦𝗢𝗥", "FILE COMPRESSOR") \
        and "/compress 480" in m.replies[-1] and not P._panels


def test_panel_flow_select_and_start(cmp, monkeypatch):
    spawned = []
    import core.bg as bg
    monkeypatch.setattr(bg, "spawn", lambda coro, name=None: spawned.append(coro))
    m = _command("/compress", reply=_video(height=720))
    run(P.compress_cmd(None, m))
    panel, key = _panel_key(m)
    assert key in P._panels and P._panels[key]["opts"]["res"] == "720"
    q = _tap("cmp:res:480", panel)
    assert P._panels[key]["opts"]["res"] == "480" and "480p" in panel.edits[-1] and "480p" in q.answers[-1][0]
    q = _tap("cmp:lock:1080", panel)
    assert q.answers[-1][1] and "720p" in q.answers[-1][0]              # upscale refused with an alert
    _tap("cmp:codec:hevc", panel)
    _tap("cmp:target", panel)
    assert P._panels[key]["opts"]["codec"] == "hevc" and P._panels[key]["opts"]["target"] == 10
    q = _tap("cmp:go", panel)
    assert key not in P._panels and len(spawned) == 1
    run(spawned[0])
    msg, mode, extra, card = P.queued[-1]
    assert msg is m and mode == "compress" and card is panel and extra["res"] == "480" and extra["codec"] == "hevc"
    assert run(db._get_user(5))["cmp_last"]["codec"] == "hevc"           # remembered
    m2 = _command("/compress", reply=_video(height=1080, name="Other.mkv"))
    run(P.compress_cmd(None, m2))
    assert P._panels[_panel_key(m2)[1]]["opts"]["codec"] == "hevc"


def test_only_the_owner_can_press_and_panels_expire(cmp):
    m = _command("/compress", reply=_video())
    run(P.compress_cmd(None, m))
    panel, key = _panel_key(m)
    q = _tap("cmp:res:360", panel, uid=77)
    assert q.answers[-1][1] and "Only" in q.answers[-1][0] and P._panels[key]["opts"]["res"] != "360"
    q = _tap("cmp:res:360", panel, uid=222)                              # admins may help out
    assert P._panels[key]["opts"]["res"] == "360"
    P._panels[key]["ts"] -= P.PANEL_TTL + 5
    P._prune()
    q = _tap("cmp:res:480", panel)
    assert "expired" in q.answers[-1][0]


def test_close_removes_the_panel(cmp):
    m = _command("/compress", reply=_video())
    run(P.compress_cmd(None, m))
    panel, key = _panel_key(m)
    deleted = []

    async def delete(*a, **k):
        deleted.append(True)
    panel.delete = delete
    _tap("cmp:close", panel)
    assert key not in P._panels and deleted


def test_one_shot_arguments_skip_the_panel(cmp):
    m = _command("/compress 360 strong", reply=_video(height=1080))
    run(P.compress_cmd(None, m))
    msg, mode, extra, card = P.queued[-1]
    assert mode == "compress" and extra["res"] == "360" and extra["level"] == "strong" and card is m.sent[-1]
    assert not P._panels
    up = _command("/compress 1080", reply=_video(height=480, name="small.mp4"))
    run(P.compress_cmd(None, up))
    assert P.queued[-1][2]["res"] == "480"                               # no upscale, even when typed


def test_enqueue_reuses_the_card(monkeypatch):
    import VideoEncoder.plugins.encode as enc
    started = []

    async def fake_handle(message, mode):
        started.append(scheduler.NOTES.get(id(message)))

    async def nosleep(*a, **k):
        return None
    monkeypatch.setattr(enc, "handle_tasks", fake_handle)
    monkeypatch.setattr(enc.asyncio, "sleep", nosleep)
    card = Msg("panel", uid=0)
    first = Msg("/compress", uid=5)
    assert run(enc._enqueue(first, "compress", {"res": "480"}, card=card))
    assert started == [card] and not first.replies                      # the panel IS the status card
    card2 = Msg("panel", uid=0)
    second = Msg("/compress", uid=6)
    run(enc._enqueue(second, "compress", {"res": "360"}, card=card2))
    assert "Added to the queue" in card2.edits[-1] and not second.replies
    assert scheduler.NOTES[id(second)] is card2


def test_compress_is_a_registered_restorable_mode():
    from core.commands import GROUP_COMMANDS, USER_COMMANDS
    assert "compress" in [c for c, _ in USER_COMMANDS] and "compress" in [c for c, _ in GROUP_COMMANDS]
    assert scheduler.COMMAND_MODES["/compress"] == "compress"
    from VideoEncoder.utils import tasks
    assert tasks._MODE_TITLE["compress"]
    from core.texts import ENC_HELP
    assert "/compress" in ENC_HELP


# ═════════════════════════ end-to-end (real ffmpeg, fake Telegram) ═════════════════════════
def _make(path, size="854x480", dur=2):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate=24:duration={dur}", "-f", "lavfi", "-i", f"sine=f=440:duration={dur}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-qp", "4", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-ar", "48000", "-ac", "2", str(path)], check=True)
    w, h = map(int, size.split("x"))
    return ffcmd.summarize({"streams": [st(0, "video", "h264", width=w, height=h, pix_fmt="yuv420p", r_frame_rate="24/1"),
                                        st(1, "audio", "aac", channels=2, sample_rate="48000")],
                            "format": {"duration": str(dur), "size": str(os.path.getsize(path))}})


class FileMsg(Msg):
    def __init__(self, src, uid=5):
        super().__init__(None, uid=uid)
        self._src = str(src)
        self.video = SimpleNamespace(file_name=os.path.basename(src), mime_type="video/mp4", file_id="F",
                                     file_size=os.path.getsize(src), file_unique_id="UQ" + os.path.basename(src),
                                     height=480, width=854, duration=2)

    async def download(self, file_name="", progress=None, progress_args=()):
        dest = os.path.join(file_name or ".", os.path.basename(self._src))
        shutil.copy(self._src, dest)
        return dest


@pytest.fixture
def e2e(monkeypatch, tmp_path):
    from VideoEncoder.utils import tasks
    from VideoEncoder.utils import uploads
    infos, uploaded = {}, []

    async def fake_probe(p):
        return dict(infos.get(os.path.basename(p)) or ffcmd.summarize(None), ok=True)

    async def fake_upload(path, message, msg):
        r = subprocess.run([FF, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
        uploaded.append((os.path.basename(path), os.path.getsize(path), r.stderr))
        return "https://t.me/c/1/99"
    monkeypatch.setattr(encoding, "probe", fake_probe)
    monkeypatch.setattr(uploads, "upload_worker", fake_upload)
    monkeypatch.setattr(tasks, "delete_downloads", lambda: None)
    return SimpleNamespace(infos=infos, uploaded=uploaded, tasks=tasks, tmp=tmp_path)


def _run_compress(e, src_msg, extra):
    m = Msg("/compress", uid=5)
    m.reply_to_message = src_msg
    status = []

    async def reply_text(t, *a, **k):
        s = Msg(t, uid=0)
        s.edits = []

        async def edit(tx, *a, **k):
            s.edits.append(tx)
            return s
        s.edit = s.edit_text = edit

        async def markup(*a, **k):
            return s
        s.edit_reply_markup = markup
        status.append(s)
        return s
    m.reply_text = m.reply = reply_text
    scheduler.add(m, "compress", extra=extra)
    run(e.tasks.handle_tasks(m, "compress"))
    assert not queue
    return status[0].edits


@needs_ffmpeg
def test_e2e_compress_to_360p(e2e):
    e = e2e
    src = e.tmp / "Big Show.mp4"
    e.infos["Big Show.mp4"] = _make(src)
    edits = _run_compress(e, FileMsg(src), {"res": "360", "audio": "copy", "level": "strong"})
    assert e.uploaded, edits
    name, size, probe = e.uploaded[0]
    assert name == "Big Show [360p].mp4" and size < os.path.getsize(src)
    assert re.search(r"Video: h264.*\b640x360\b", probe) and re.search(r"Audio: aac", probe)
    done = edits[-1]
    assert "360p H.264" in done and "−" in done and not _bot_api_html_problems(done)
    assert run(db.get_settings(5))["enc_count"] == 1


@needs_ffmpeg
def test_e2e_bigger_result_is_not_sent(e2e, monkeypatch):
    e = e2e
    src = e.tmp / "tiny.mp4"
    e.infos["tiny.mp4"] = _make(src, size="320x240", dur=1)
    real_encode = encoding.encode

    async def bloated(filepath, message, msg, audio_map=None, opts=None):
        out = await real_encode(filepath, message, msg, audio_map=audio_map, opts=opts)
        with open(out, "ab") as f:
            f.write(b"\0" * (os.path.getsize(filepath) * 2))
        return out
    monkeypatch.setattr(encoding, "encode", bloated)
    edits = _run_compress(e, FileMsg(src), {"res": "360"})
    assert not e.uploaded and "ᴀʟʀᴇᴀᴅʏ ᴄᴏᴍᴘᴀᴄᴛ".upper() not in edits[-1]
    assert "𝗔𝗟𝗥𝗘𝗔𝗗𝗬 𝗖𝗢𝗠𝗣𝗔𝗖𝗧" in edits[-1] and "nothing was sent".translate(str.maketrans(
        "abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ")) in edits[-1]


def test_compress_needs_a_video_stream(e2e, tmp_path):
    e = e2e
    src = tmp_path / "song.mp4"
    src.write_bytes(b"\0" * 1000)
    e.infos["song.mp4"] = ffcmd.summarize({"streams": [st(0, "audio", "aac", channels=2)], "format": {"duration": "3"}})
    edits = _run_compress(e, FileMsg(src), {"res": "480"})
    assert "No video stream" in edits[-1] and not e.uploaded

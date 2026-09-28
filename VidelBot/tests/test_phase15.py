"""Phase 15 – GPU / AV1 / 2-pass encoders, logo + text watermarks, parallel workers with Pro priority,
restart-proof queue, /mux /merge /convert /leech /watermark, auto-split above 2 GB, Mega / Drive / direct leech."""
import asyncio
import base64
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run
from tests.test_phase5 import _bot_api_html_problems

MODULES = load_all()

import config  # noqa: E402
from VideoEncoder import data as queue  # noqa: E402
from VideoEncoder.utils import ffcmd, hw, jobs, leech, scheduler  # noqa: E402
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
    scheduler.MODES.clear()
    scheduler.EXTRA.clear()
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


def val(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def build(s, **k):
    return ffcmd.build_command("in.mkv", "out" + ffcmd.output_ext(ffcmd.merge(s)), s, INFO, **k)


# ───────────────────────── encoders: AV1 / GPU / 2-pass ─────────────────────────
def test_av1_software_encoders():
    svt = build({"av1": True, "crf": 22, "preset": "m"}, encoder="libsvtav1")
    assert val(svt, "-c:v") == "libsvtav1" and val(svt, "-preset") == "6"
    assert val(svt, "-crf") == str(ffcmd.av1_crf(22)) and "-tune" not in svt
    aom = build({"av1": True, "crf": 22, "preset": "uf"}, encoder="libaom-av1")
    assert val(aom, "-c:v") == "libaom-av1" and val(aom, "-cpu-used") == "8" and val(aom, "-b:v") == "0"
    assert ffcmd.video_codec({"av1": True, "hevc": True}) == "av1" and ffcmd.av1_crf(99) == 63


@pytest.mark.parametrize("enc,flag", [("h264_nvenc", "-cq"), ("hevc_qsv", "-global_quality"),
                                      ("h264_vaapi", "-qp")])
def test_gpu_encoders_get_their_own_quality_flags(enc, flag):
    c = build({"hevc": enc.startswith("hevc"), "crf": 23}, encoder=enc)
    assert val(c, "-c:v") == enc and val(c, flag) is not None
    assert "-crf" not in c and "-x265-params" not in c
    if "vaapi" in enc:
        assert "hwupload" in val(c, "-vf") and "-vaapi_device" in c


def test_pick_encoder_prefers_gpu_but_falls_back():
    caps = {"kind": "nvenc", "encoders": {"h264": "h264_nvenc", "hevc": "hevc_nvenc"}, "av1_sw": "libaom-av1"}
    assert hw.pick_encoder({}, caps) == ("h264_nvenc", "GPU (nvenc)")
    assert hw.pick_encoder({"hw": False}, caps)[0] == "libx264"
    assert hw.pick_encoder({"bits": True}, caps)[0] == "libx264"            # 10-bit H.264 → CPU
    assert hw.pick_encoder({"mode": "size", "target_mb": 100, "twopass": True}, caps)[0] == "libx264"
    assert hw.pick_encoder({"av1": True}, caps) == ("libaom-av1", "CPU")
    assert hw.pick_encoder({"av1": True}, {"kind": None, "encoders": {}, "av1_sw": None})[0] is None


def test_two_pass_commands():
    s = {"mode": "size", "target_mb": 200, "twopass": True}
    p1 = ffcmd.build_command("in.mkv", os.devnull, s, INFO, pass_no=1, passlog="/tmp/pl")
    p2 = build(s, pass_no=2, passlog="/tmp/pl")
    assert val(p1, "-pass") == "1" and "-an" in p1 and val(p1, "-f") == "null"
    assert val(p2, "-pass") == "2" and val(p2, "-passlogfile") == "/tmp/pl" and val(p2, "-b:v")
    x265 = build(dict(s, hevc=True), pass_no=2, passlog="/tmp/pl")
    assert "pass=2" in val(x265, "-x265-params")
    assert ffcmd.describe(s)["rate"] == "2-pass"


def test_logo_and_text_watermarks():
    c = build({"logo": True, "wm_pos": "tl", "wm_size": "l", "wm_opacity": "50"}, logo_file="logo.png")
    fc = val(c, "-filter_complex")
    assert c.count("-i") == 2 and "overlay=W*0.02:H*0.03" in fc and "colorchannelmixer=aa=0.5" in fc
    assert f"scale=-2:{ffcmd.logo_height({'wm_size': 'l'}, INFO)}" in fc and "-vf" not in c
    ass = ffcmd.text_watermark_ass("My {Channel}", "c", "s", "30")
    assert "My (Channel)" in ass and "WM,Arial,34,&HB3FFFFFF" in ass and ",1,2,1,5,40,40,30,1" in ass
    t = build({}, text_wm_file="wm.ass")
    assert "wm.ass" in (val(t, "-vf") or "")


# ───────────────────────── real ffmpeg ─────────────────────────
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


def _encoders():
    if not FF:
        return ""
    return subprocess.run([FF, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout


def _run(cmd):
    r = subprocess.run([FF] + list(cmd[1:]), capture_output=True, text=True)
    return r.returncode, r.stderr


def _make(path, size="320x240", dur=2, audio=True, vcodec="libx264", rate=24):
    cmd = [FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:duration={dur}"]
    cmd += ["-c:v", vcodec, "-preset", "ultrafast", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if audio else []) + [str(path)]
    subprocess.run(cmd, check=True)
    w, h = map(int, size.split("x"))
    return ffcmd.summarize({"streams": [st(0, "video", "h264", width=w, height=h, pix_fmt="yuv420p",
                                           r_frame_rate=f"{rate}/1")]
                           + ([st(1, "audio", "aac", channels=1, sample_rate="44100")] if audio else []),
                           "format": {"duration": str(dur), "size": str(os.path.getsize(path))}})


@needs_ffmpeg
def test_real_av1_logo_text_and_two_pass(tmp_path):
    src = tmp_path / "in.mp4"
    info = _make(src, dur=1)
    if "libaom-av1" in _encoders():
        out = tmp_path / "av1.mkv"
        code, err = _run(ffcmd.build_command(str(src), str(out), {"av1": True, "preset": "uf", "crf": 40}, info,
                                             encoder="libaom-av1"))
        assert code == 0 and out.stat().st_size, err
    logo = tmp_path / "logo.png"
    subprocess.run([FF, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1",
                    str(logo)], check=True)
    out = tmp_path / "logo.mkv"
    code, err = _run(ffcmd.build_command(str(src), str(out), {"logo": True, "preset": "uf"}, info, logo_file=str(logo)))
    assert code == 0 and out.stat().st_size, err
    wm = tmp_path / "wm.ass"
    wm.write_text(ffcmd.text_watermark_ass("Videl", "br", "m", "75"), encoding="utf-8")
    out = tmp_path / "text.mkv"
    code, err = _run(ffcmd.build_command(str(src), str(out), {"watermark": True, "preset": "uf"}, info,
                                         text_wm_file=str(wm)))
    assert code == 0 and out.stat().st_size, err
    s = {"mode": "size", "target_mb": 1, "twopass": True, "preset": "uf"}
    log = str(tmp_path / "2pass")
    assert _run(ffcmd.build_command(str(src), os.devnull, s, info, pass_no=1, passlog=log))[0] == 0
    out = tmp_path / "2p.mkv"
    code, err = _run(ffcmd.build_command(str(src), str(out), s, info, pass_no=2, passlog=log))
    assert code == 0 and out.stat().st_size, err


@needs_ffmpeg
@pytest.mark.parametrize("container", [".mkv", ".mp4"])
def test_real_mux_subtitle_and_audio(tmp_path, container):
    src = tmp_path / f"v{container}"
    info = _make(src, dur=2)
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,500\nHello\n", encoding="utf-8")
    out = tmp_path / ("o" + ffcmd.mux_ext(str(src)))
    code, err = _run(ffcmd.mux_command(str(src), str(srt), str(out), "sub", info))
    assert code == 0 and out.stat().st_size > src.stat().st_size * 0.9, err
    mp3 = tmp_path / "a.mp3"
    subprocess.run([FF, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:duration=2", str(mp3)], check=True)
    out2 = tmp_path / ("o2" + ffcmd.mux_ext(str(src)))
    code, err = _run(ffcmd.mux_command(str(src), str(mp3), str(out2), "audio", info, "mp3"))
    assert code == 0 and out2.stat().st_size, err


@needs_ffmpeg
def test_real_merge_copy_and_normalised(tmp_path):
    a, b, c = tmp_path / "a.mp4", tmp_path / "b.mp4", tmp_path / "c.mp4"
    ia, ib = _make(a), _make(b)
    ic = _make(c, size="640x360", audio=False, rate=30)
    assert ffcmd.can_concat_copy([ia, ib]) and not ffcmd.can_concat_copy([ia, ic])
    lst = tmp_path / "l.txt"
    lst.write_text(ffcmd.concat_list([str(a), str(b)]))
    out = tmp_path / "m.mp4"
    assert _run(ffcmd.concat_command(str(lst), str(out)))[0] == 0 and out.stat().st_size
    w, h, fps = ffcmd.merge_target([ia, ic])
    norm = []
    for i, (p, inf) in enumerate(((a, ia), (c, ic))):
        n = tmp_path / f"n{i}.mp4"
        code, err = _run(ffcmd.normalize_command(str(p), str(n), inf, w, h, fps))
        assert code == 0, err
        norm.append(str(n))
    lst.write_text(ffcmd.concat_list(norm))
    out = tmp_path / "m2.mp4"
    code, err = _run(ffcmd.concat_command(str(lst), str(out)))
    assert code == 0 and out.stat().st_size, err


@needs_ffmpeg
@pytest.mark.parametrize("fmt", ["mp3", "m4a", "opus", "flac", "wav"])
def test_real_audio_extract(tmp_path, fmt):
    src = tmp_path / "v.mkv"
    _make(src, dur=1)
    ext = ffcmd.CONVERT_FORMATS[fmt][0]
    out = tmp_path / ("a" + ext)
    code, err = _run(ffcmd.audio_extract_command(str(src), str(out), fmt))
    assert code == 0 and out.stat().st_size, err


@needs_ffmpeg
def test_real_gif_and_split(tmp_path):
    src = tmp_path / "v.mkv"
    _make(src, dur=4)
    start, length = ffcmd.gif_window(4, None, None)
    assert 0 <= start and length <= ffcmd.GIF_MAX
    gif = tmp_path / "a.gif"
    code, err = _run(ffcmd.gif_command(str(src), str(gif), start, min(length, 2)))
    assert code == 0 and gif.read_bytes()[:3] == b"GIF", err
    assert ffcmd.gif_window(100, 90, 60) == (90.0, 10.0) or ffcmd.gif_window(100, 90, 60)[1] <= 20


@needs_ffmpeg
def test_split_for_upload_keyframe_parts_and_byte_fallback(tmp_path, monkeypatch):
    src = tmp_path / "long.mkv"
    subprocess.run([FF, "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=24",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "24", "-b:v", "400k", str(src)], check=True)
    size = src.stat().st_size

    async def fake_probe(p):
        return {"ok": True, "video": {"codec_name": "h264"}, "duration": 24.0, "size": os.path.getsize(p)}
    monkeypatch.setattr(encoding, "probe", fake_probe)
    monkeypatch.setattr(ffcmd, "split_plan", lambda size_, dur, limit: 10.0 * limit / size * 2.2)
    limit = size // 2 + 20000
    parts = run(encoding.split_for_upload(str(src), limit))
    assert len(parts) >= 2 and all(os.path.getsize(p) <= limit for p in parts)
    assert all(".part" in os.path.basename(p) for p in parts)
    blob = tmp_path / "data.bin"
    blob.write_bytes(os.urandom(250_000))

    async def no_video(p):
        return {"ok": True, "video": None, "duration": 0}
    monkeypatch.setattr(encoding, "probe", no_video)
    parts = run(encoding.split_for_upload(str(blob), 100_000))
    assert [os.path.basename(p) for p in parts] == ["data.bin.001", "data.bin.002", "data.bin.003"]
    assert b"".join(open(p, "rb").read() for p in parts) == blob.read_bytes()
    assert run(encoding.split_for_upload(str(blob), 10 ** 9)) == [str(blob)]


def test_upload_splits_big_files_into_captioned_parts(monkeypatch, tmp_path):
    from VideoEncoder.utils.uploads import telegram as tg
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x" * 10)
    parts_dir = tmp_path / "movie.parts"
    parts_dir.mkdir()
    parts = []
    for i in range(3):
        p = parts_dir / f"movie.part{i:03d}.mkv"
        p.write_bytes(b"y")
        parts.append(str(p))

    async def fake_split(path, limit, key=None):
        return parts
    sent = []

    async def fake_one(path, message, msg, as_doc=None, caption=None):
        sent.append((os.path.basename(path), caption))
        return f"https://t.me/c/1/{len(sent)}"
    monkeypatch.setattr(tg, "split_limit", lambda: 5)
    monkeypatch.setattr(encoding, "split_for_upload", fake_split)
    monkeypatch.setattr(tg, "_upload_one", fake_one)
    link = run(tg.upload_to_tg(str(f), Msg("/dl", uid=5), Msg("s", uid=5)))
    assert link == "https://t.me/c/1/1" and len(sent) == 3
    assert "Part 1 of 3" in sent[0][1] and "Part 3 of 3" in sent[2][1]
    assert not parts_dir.exists()
    monkeypatch.undo()
    monkeypatch.setattr(config, "SPLIT_SIZE_MB", 99999)
    assert tg.split_limit() == 1990 * 1024 * 1024                  # always under the 2 GB bot upload cap
    monkeypatch.setattr(config, "SPLIT_SIZE_MB", 1)
    assert tg.split_limit() == 50 * 1024 * 1024


# ───────────────────────── scheduler ─────────────────────────
def test_priority_limits_and_parallel_workers(monkeypatch):
    free1, free2, pro = Msg("/dl", uid=5), Msg("/dl", uid=6), Msg("/dl", uid=222)
    assert scheduler.add(free1, "tg") == 1
    scheduler.mark_running(free1)
    assert scheduler.add(free2, "tg") == 2
    assert scheduler.add(pro, "tg", priority=True) == 2          # jumps ahead of waiting free tasks only
    assert queue == [free1, pro, free2] and scheduler.next_waiting() is pro
    assert scheduler.slots_free() == 0 and not scheduler.can_start(pro)
    monkeypatch.setattr(config, "ENCODER_WORKERS", 2)
    assert scheduler.can_start(pro) and not scheduler.can_start(free2)
    assert scheduler.user_tasks(5) == 1
    scheduler.finish(free1)
    assert queue == [pro, free2] and scheduler.running() == []


def test_enqueue_respects_limits_and_reports_priority(monkeypatch):
    import VideoEncoder.plugins.encode as enc_plugin
    started = []

    async def fake_handle(message, mode):
        started.append(mode)

    async def nosleep(*a, **k):
        return None
    monkeypatch.setattr(enc_plugin, "handle_tasks", fake_handle)
    monkeypatch.setattr(enc_plugin.asyncio, "sleep", nosleep)
    monkeypatch.setattr(config, "ENC_MAX_TASKS_FREE", 2)
    first = Msg("/dl", uid=5)
    assert run(enc_plugin._enqueue(first, "tg")) and started == ["tg"]
    second = Msg("/dl", uid=5)
    run(enc_plugin._enqueue(second, "tg"))
    assert "Added to the queue" in second.replies[-1] and "#2" in second.replies[-1]
    third = Msg("/dl", uid=5)
    assert run(enc_plugin._enqueue(third, "tg")) is False and "limit 2" in third.replies[-1]
    admin = Msg("/dl", uid=222)
    run(enc_plugin._enqueue(admin, "tg"))
    assert "priority" in admin.replies[-1] and scheduler.position(admin) == 2


def test_queue_survives_a_restart(monkeypatch):
    m = Msg("/convert mp3", uid=5)
    run(scheduler.persist(m, "convert", {"fmt": "mp3"}, False))
    got = []

    class App:
        async def get_messages(self, chat, mid):
            got.append((chat, mid))
            return m

    async def fake_dispatch(spawn_all=False):
        return None
    from VideoEncoder.utils import tasks
    monkeypatch.setattr(tasks, "dispatch", fake_dispatch)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(scheduler.asyncio, "sleep", lambda *_: real_sleep(0))
    assert run(scheduler.restore_queue(App())) == 1
    assert got == [(m.chat.id, m.id)] and queue == [m]
    assert scheduler.mode_of(m) == "convert" and scheduler.extra_of(m) == {"fmt": "mp3"}
    assert "back in the queue" in m.replies[-1]


def test_watchdog_keeps_live_task_dirs(tmp_path, monkeypatch):
    import time as _t
    import watchdog
    base = tmp_path / "enc"
    base.mkdir()
    m = Msg("/dl", uid=5)
    live, stale = base / scheduler.task_key(m), base / "1_1"
    for d in (live, stale):
        d.mkdir()
        (d / "f").write_bytes(b"1")
        old = _t.time() - 30 * 3600
        os.utime(d / "f", (old, old))
    queue.append(m)
    monkeypatch.setattr(watchdog, "ENCODER_DIRS", [str(base)])
    monkeypatch.setattr(watchdog, "TEMP_DIRS", [])
    watchdog.Watchdog(None).clean_files()
    assert live.exists() and not stale.exists()


# ───────────────────────── leech ─────────────────────────
def test_link_kinds_and_names():
    assert leech.kind_of("https://mega.nz/file/abc#k") == "mega"
    assert leech.kind_of("https://drive.google.com/file/d/1AbCdEfGhIjK/view") == "gdrive"
    assert leech.kind_of("https://example.com/a.mkv") == "direct"
    assert leech.gdrive_id("https://drive.google.com/file/d/1AbCdEfGhIjKlmn/view?usp=sharing") == "1AbCdEfGhIjKlmn"
    assert leech.safe_name("../../etc/passwd") == "passwd" and leech.safe_name("") == "file"
    assert leech.parse_mega_url("https://mega.nz/#!ID1!KEY2") == ("ID1", "KEY2")
    with pytest.raises(leech.LeechError):
        leech.parse_mega_url("https://mega.nz/folder/abc#def")


def test_private_addresses_are_blocked(monkeypatch):
    monkeypatch.setattr(leech, "ALLOW_PRIVATE", False)
    for url in ("http://127.0.0.1:8080/admin", "http://169.254.169.254/latest/meta-data", "http://[::1]/x",
                "http://10.0.0.5/file", "http://localhost/x", "ftp://example.com/a", "file:///etc/passwd"):
        with pytest.raises(leech.LeechError):
            run(leech.check_public(url))
    run(leech.check_public("http://8.8.8.8/file.bin"))          # public literal IP is fine (no DNS needed)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _serve(routes):
    """Start an aiohttp server on 127.0.0.1 in a background loop → (base_url, stop)."""
    from aiohttp import web
    import threading
    loop = asyncio.new_event_loop()
    app = web.Application()
    for method, path, handler in routes:
        app.router.add_route(method, path, handler)
    runner = web.AppRunner(app)
    ready = threading.Event()
    port = {}

    def go():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "127.0.0.1", 0)
        loop.run_until_complete(site.start())
        port["p"] = site._server.sockets[0].getsockname()[1]
        ready.set()
        loop.run_forever()
    t = threading.Thread(target=go, daemon=True)
    t.start()
    ready.wait(5)

    def stop():
        asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        t.join(5)
    return f"http://127.0.0.1:{port['p']}", stop


def test_mega_download_decrypts(tmp_path, monkeypatch):
    from aiohttp import web
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    monkeypatch.setattr(leech, "ALLOW_PRIVATE", True)
    raw_key = os.urandom(32)
    aes, iv = leech.mega_keys(_b64(raw_key))
    plain = os.urandom(3 * 1024 * 1024 + 123)
    enc = Cipher(algorithms.AES(aes), modes.CTR(iv)).encryptor()
    cipher = enc.update(plain) + enc.finalize()
    attr = b'MEGA{"n":"My Movie.mkv"}'
    attr += b"\0" * (-len(attr) % 16)
    e = Cipher(algorithms.AES(aes), modes.CBC(b"\0" * 16)).encryptor()
    at = _b64(e.update(attr) + e.finalize())
    state = {}

    async def api(request):
        body = await request.json()
        if body[0]["p"] != "FILEID":
            return web.json_response([-9])
        return web.json_response([{"s": len(plain), "at": at, "g": state["base"] + "/dl"}])

    async def dl(request):
        return web.Response(body=cipher, content_type="application/octet-stream")
    base, stop = _serve([("POST", "/cs", api), ("GET", "/dl", dl)])
    state["base"] = base
    try:
        path = run(leech.download(f"https://mega.nz/file/FILEID#{_b64(raw_key)}", str(tmp_path),
                                  mega_api=base + "/cs"))
        assert os.path.basename(path) == "My Movie.mkv" and open(path, "rb").read() == plain
        with pytest.raises(leech.LeechError, match="not found"):
            run(leech.download(f"https://mega.nz/file/OTHER#{_b64(raw_key)}", str(tmp_path), mega_api=base + "/cs"))
        with pytest.raises(leech.LeechError, match="limit"):
            run(leech.download(f"https://mega.nz/file/FILEID#{_b64(raw_key)}", str(tmp_path),
                               mega_api=base + "/cs", max_bytes=1024))
    finally:
        stop()


def test_direct_download_redirects_limits_and_html(tmp_path, monkeypatch):
    from aiohttp import web
    monkeypatch.setattr(leech, "ALLOW_PRIVATE", True)
    body = os.urandom(200_000)

    async def file_(request):
        return web.Response(body=body, headers={"Content-Disposition": 'attachment; filename="clip.mp4"'},
                            content_type="video/mp4")

    async def hop(request):
        raise web.HTTPFound("/file")

    async def page(request):
        return web.Response(text="<html>login</html>", content_type="text/html")
    base, stop = _serve([("GET", "/file", file_), ("GET", "/go", hop), ("GET", "/page", page)])
    try:
        path = run(leech.download(base + "/go", str(tmp_path)))
        assert os.path.basename(path) == "clip.mp4" and open(path, "rb").read() == body
        path = run(leech.download(base + "/file", str(tmp_path), name="renamed.mp4"))
        assert os.path.basename(path) == "renamed.mp4"
        with pytest.raises(leech.LeechError, match="limit"):
            run(leech.download(base + "/file", str(tmp_path), max_bytes=1000))
        with pytest.raises(leech.LeechError, match="web page"):
            run(leech.download(base + "/page", str(tmp_path)))
        monkeypatch.setattr(leech, "ALLOW_PRIVATE", False)          # the default: local servers are refused
        with pytest.raises(leech.LeechError, match="private"):
            run(leech.download(base + "/file", str(tmp_path)))
    finally:
        stop()


# ───────────────────────── commands ─────────────────────────
@pytest.fixture
def tools(monkeypatch):
    import VideoEncoder.plugins.tools as t
    queued = []

    async def yes(*a, **k):
        return True

    async def fake_enqueue(message, mode, extra=None):
        queued.append((message, mode, extra))
        return True
    monkeypatch.setattr(t, "check_chat", yes)
    monkeypatch.setattr(t, "AddUserToDatabase", yes)
    monkeypatch.setattr(t, "_enqueue", fake_enqueue)
    t._mux.clear()
    t._merge.clear()
    t.queued = queued
    return t


def _video_msg(uid=5, name="ep1.mkv"):
    m = Msg(None, uid=uid)
    m.video = SimpleNamespace(file_name=name, mime_type="video/x-matroska")
    return m


def _doc_msg(name, mime="application/octet-stream", uid=5):
    m = Msg(None, uid=uid)
    m.document = SimpleNamespace(file_name=name, mime_type=mime, file_id="F")
    return m


def _capture(t, m):
    from pyrogram import StopPropagation
    assert t._pending_filter(None, None, m)
    with pytest.raises(StopPropagation):
        run(t.capture_pending(None, m))


def test_mux_flow(tools):
    t = tools
    bare = Msg("/mux", uid=5)
    run(t.mux_cmd(None, bare))
    assert "Reply" in bare.replies[-1] and not t._mux
    cmd = Msg("/mux", uid=5)
    cmd.reply_to_message = _video_msg()
    run(t.mux_cmd(None, cmd))
    assert (5, 5) in t._mux and not t.queued
    other = Msg("hello", uid=5)
    assert not t._pending_filter(None, None, other)                  # text isn't captured
    wrong = _doc_msg("notes.pdf")
    _capture(t, wrong)
    assert "not a subtitle" in wrong.replies[-1] and (5, 5) in t._mux
    sub = _doc_msg("ep1.en.srt")
    _capture(t, sub)
    msg, mode, extra = t.queued[-1]
    assert msg is cmd and mode == "mux" and extra == {"track": [5, sub.id], "kind": "sub"} and not t._mux


def test_merge_flow_and_free_limit(tools):
    t = tools
    start = Msg("/merge", uid=5)
    run(t.merge_cmd(None, start))
    assert "up to <b>3</b>" in start.replies[-1]
    early = Msg("/merge done", uid=5)
    run(t.merge_cmd(None, early))
    assert "at least 2" in early.replies[-1] and not t.queued
    vids = [_video_msg(name=f"p{i}.mp4") for i in range(4)]
    for v in vids[:3]:
        _capture(t, v)
    _capture(t, vids[3])
    assert "maximum" in vids[3].replies[-1]
    done = Msg("/merge done", uid=5)
    run(t.merge_cmd(None, done))
    assert t.queued[-1][1] == "merge" and t.queued[-1][2]["parts"] == [[5, v.id] for v in vids[:3]]
    admin = Msg("/merge", uid=222)
    run(t.merge_cmd(None, admin))
    assert "up to <b>10</b>" in admin.replies[-1]


def test_convert_and_leech_validation(tools):
    t = tools
    bad = Msg("/convert xyz", uid=5)
    bad.reply_to_message = _video_msg()
    run(t.convert_cmd(None, bad))
    assert "Convert" in bad.replies[-1] and not t.queued
    ok = Msg("/convert gif 1:20 30", uid=5)
    ok.reply_to_message = _video_msg()
    run(t.convert_cmd(None, ok))
    assert t.queued[-1][2] == {"fmt": "gif", "start": 80.0, "length": 20.0}
    shortcut = Msg("/toaudio", uid=5)
    shortcut.reply_to_message = _video_msg()
    run(t.convert_cmd(None, shortcut))
    assert t.queued[-1][2] == {"fmt": "mp3"}
    usage = Msg("/leech", uid=5)
    run(t.leech_cmd(None, usage))
    assert "Link uploader" in usage.replies[-1] and not _bot_api_html_problems(usage.replies[-1])
    lk = Msg("/leech https://example.com/a.mkv | Good name.mkv", uid=5)
    run(t.leech_cmd(None, lk))
    assert t.queued[-1][1:] == ("leech", {"url": "https://example.com/a.mkv", "name": "Good name.mkv"})


def test_watermark_command(tools):
    from VideoEncoder.utils.database.access_db import db
    t = tools
    run(t.watermark_cmd(None, Msg("/watermark @MyChannel", uid=5)))
    s = run(db.get_settings(5))
    assert s["wm_text"] == "@MyChannel" and s["watermark"] is True
    photo = Msg("/watermark", uid=5)
    photo.reply_to_message = Msg(None, uid=5)
    photo.reply_to_message.photo = SimpleNamespace(file_id="PHOTO")
    run(t.watermark_cmd(None, photo))
    assert "Encoder Pro" in photo.replies[-1] and not run(db.get_settings(5))["logo_id"]
    ap = Msg("/watermark", uid=222)
    ap.reply_to_message = photo.reply_to_message
    run(t.watermark_cmd(None, ap))
    s = run(db.get_settings(222))
    assert s["logo_id"] == "PHOTO" and s["logo"] is True
    status = Msg("/watermark", uid=5)
    run(t.watermark_cmd(None, status))
    assert "@MyChannel" in status.replies[-1] and not _bot_api_html_problems(status.replies[-1])
    run(t.watermark_cmd(None, Msg("/watermark clear", uid=222)))
    assert not run(db.get_settings(222))["logo_id"]


# ───────────────────────── menus ─────────────────────────
def _press(data, uid=5):
    from VideoEncoder.plugins.callbacks_ import callback_handlers
    q = FakeQuery(data, uid=uid)
    run(callback_handlers(FakeClient(), q))
    return q


def _settings(uid=5):
    from VideoEncoder.utils.database.access_db import db
    return run(db.get_settings(uid))


def test_codec_cycle_gates_av1_for_free_users():
    _press("triggerCodec")
    assert ffcmd.video_codec(_settings()) == "hevc"
    q = _press("triggerCodec")                                    # free user: AV1 → back to H.264 + pitch
    assert ffcmd.video_codec(_settings()) == "h264" and "Encoder Pro" in q.answers[-1][0]
    _press("triggerCodec", uid=222)
    _press("triggerCodec", uid=222)
    assert ffcmd.video_codec(_settings(222)) == "av1"


def test_pro_toggles_and_watermark_style_menu():
    q = _press("triggerTwopass")
    assert not _settings()["twopass"] and "Encoder Pro" in q.answers[-1][0]
    _press("triggerTwopass", uid=222)
    assert _settings(222)["twopass"] is True
    q = _press("triggerLogo", uid=222)
    assert not _settings(222)["logo"] and "Set a logo first" in q.answers[-1][0]
    q = _press("WmSettings")
    text = q.message.edits[-1]
    assert "𝗦𝗧𝗬𝗟𝗘" in text or "Style" in text or "style" in text
    assert not _bot_api_html_problems(text)
    _press("triggerWmPos")
    _press("triggerWmSize")
    _press("triggerWmOpacity")
    s = _settings()
    assert (s["wm_pos"], s["wm_size"], s["wm_opacity"]) == ("bl", "l", "50")


def _menu(data, uid=5):
    """Render a menu through a message that records the keyboard → (text, [callback_data …])."""
    from VideoEncoder.plugins.callbacks_ import callback_handlers
    msg = FakeMsg(text="menu", uid=uid)
    seen = {}

    async def edit(text=None, reply_markup=None, **k):
        seen["text"], seen["kb"] = text, reply_markup
    msg.edit = edit
    q = FakeQuery(data, uid=uid, message=msg)
    run(callback_handlers(FakeClient(), q))
    return seen["text"], [b.callback_data for row in seen["kb"].inline_keyboard for b in row]


def test_gpu_toggle_only_shows_with_a_gpu():
    hw.set_caps({"kind": None, "encoders": {}, "av1_sw": "libaom-av1", "listed": set()})
    text, buttons = _menu("VideoSettings")
    assert "libx264 · CPU" in text and "triggerHw" not in buttons and "triggerCodec" in buttons
    hw.set_caps({"kind": "nvenc", "encoders": {"h264": "h264_nvenc"}, "av1_sw": None, "listed": set()})
    text, buttons = _menu("VideoSettings")
    assert "h264_nvenc · GPU (nvenc)" in text and "triggerHw" in buttons
    _press("triggerHw")
    text, _ = _menu("VideoSettings")
    assert "libx264 · CPU" in text                                  # GPU switched off → CPU
    text, buttons = _menu("AdvancedSettings")
    assert "triggerTwopass" not in buttons                           # only in target-size mode
    _press("triggerEncMode")
    text, buttons = _menu("AdvancedSettings")
    assert "triggerTwopass" in buttons and "1-pass" in text
    text, buttons = _menu("ExtraSettings")
    assert "WmSettings" in buttons and "triggerLogo" in buttons and not _bot_api_html_problems(text)


# ───────────────────────── task runners end-to-end (real ffmpeg, fake Telegram) ─────────────────────────
class FileMsg(Msg):
    """A message carrying a real local file; .download() copies it like pyrogram would."""

    def __init__(self, src, uid=5, kind="video", text=None):
        super().__init__(text, uid=uid)
        self._src = str(src)
        meta = SimpleNamespace(file_name=os.path.basename(src), mime_type="video/mp4", file_id="F", file_size=1)
        setattr(self, kind, meta)
        self.photos, self.audios, self.animations = [], [], []

    async def download(self, file_name="", progress=None, progress_args=()):
        dest = os.path.join(file_name or ".", os.path.basename(self._src))
        shutil.copy(self._src, dest)
        return dest


@pytest.fixture
def e2e(monkeypatch, tmp_path):
    from VideoEncoder.utils import tasks
    from VideoEncoder.utils.uploads import telegram as tg
    infos, registry, uploads = {}, {}, []

    async def fake_probe(p):
        return dict(infos.get(os.path.basename(p)) or ffcmd.summarize(None), ok=True)

    async def fake_upload(path, message, msg, as_doc=None, caption=None):
        uploads.append((os.path.basename(path), os.path.getsize(path), as_doc))
        return "https://t.me/c/1/99"

    class Client:
        async def get_messages(self, chat, mid):
            return registry.get(mid)
    monkeypatch.setattr(encoding, "probe", fake_probe)
    monkeypatch.setattr(tg, "upload_to_tg", fake_upload)
    monkeypatch.setattr(tasks, "delete_downloads", lambda: None)
    return SimpleNamespace(infos=infos, registry=registry, uploads=uploads, client=Client(), tasks=tasks, tmp=tmp_path)


def _cmd(e, text, reply=None, uid=5):
    m = Msg(text, uid=uid)
    m.reply_to_message = reply
    m._client = e.client
    sent = []

    async def reply_text(t, *a, **k):
        s = Msg(t, uid=0)
        s.edits = []

        async def edit(tx, *a, **k):
            s.edits.append(tx)
            return s
        s.edit = edit
        s.edit_text = edit

        async def markup(*a, **k):
            return s
        s.edit_reply_markup = markup
        sent.append(s)
        return s
    m.reply_text = m.reply = reply_text

    async def media(path, *a, **k):
        m.replies.append((os.path.basename(path), os.path.getsize(path)))
    m.reply_audio = m.reply_animation = media
    m.status = sent
    return m


def _go(e, m, mode, extra=None):
    scheduler.add(m, mode, extra=extra)
    run(e.tasks.handle_tasks(m, mode))
    assert not queue                                  # the task removed itself …
    assert not os.path.exists(os.path.join(scheduler.download_dir, scheduler.task_key(m)))   # … and its folders
    return m.status[0].edits


@needs_ffmpeg
def test_e2e_mux_merge_convert(e2e):
    e = e2e
    v1, v2 = e.tmp / "ep1.mp4", e.tmp / "ep2.mp4"
    e.infos["ep1.mp4"], e.infos["ep2.mp4"] = _make(v1), _make(v2)
    srt = e.tmp / "ep1.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
    track = FileMsg(srt, kind="document")
    e.registry[track.id] = track
    edits = _go(e, _cmd(e, "/mux", FileMsg(v1)), "mux", {"track": [5, track.id], "kind": "sub"})
    assert "Track added" in edits[-1] or "𝗧𝗥𝗔𝗖𝗞" in edits[-1], edits
    assert e.uploads[-1][0] == "ep1.muxed.mp4"

    p1, p2 = FileMsg(v1), FileMsg(v2)
    e.registry[p1.id], e.registry[p2.id] = p1, p2
    edits = _go(e, _cmd(e, "/merge done"), "merge", {"parts": [[5, p1.id], [5, p2.id]]})
    assert e.uploads[-1][0] == "ep1.merged.mp4" and any("Lossless" in x for x in edits if isinstance(x, str)), edits

    m = _cmd(e, "/convert mp3", FileMsg(v1))
    edits = _go(e, m, "convert", {"fmt": "mp3"})
    assert m.replies[-1][0] == "ep1.mp3" and m.replies[-1][1] > 0 and "Audio ready" in edits[-1]
    m = _cmd(e, "/convert gif", FileMsg(v1))
    edits = _go(e, m, "convert", {"fmt": "gif", "length": 1.0})
    assert m.replies[-1][0] == "ep1.gif" and "GIF ready" in edits[-1]


def test_e2e_leech_reports_errors_and_uploads(e2e, monkeypatch):
    e = e2e

    async def fail(url, dest, **k):
        raise leech.LeechError("The file is 9 GB – your limit is 4 GB.")
    monkeypatch.setattr(leech, "download", fail)
    edits = _go(e, _cmd(e, "/leech https://x.io/a"), "leech", {"url": "https://x.io/a"})
    assert "Download failed" in edits[-1] and "Encoder Pro" in edits[-1]

    async def boom(url, dest, **k):
        raise ConnectionResetError("peer reset")
    monkeypatch.setattr(leech, "download", boom)
    edits = _go(e, _cmd(e, "/leech https://x.io/a"), "leech", {"url": "https://x.io/a"})
    assert "ConnectionResetError" in edits[-1]

    async def ok(url, dest, name=None, **k):
        p = os.path.join(dest, name or "file.zip")
        open(p, "wb").write(b"z" * 1234)
        return p
    monkeypatch.setattr(leech, "download", ok)
    edits = _go(e, _cmd(e, "/leech https://x.io/a"), "leech", {"url": "https://x.io/a", "name": "pack.zip"})
    assert e.uploads[-1] == ("pack.zip", 1234, True) and "Direct link" in edits[-1]

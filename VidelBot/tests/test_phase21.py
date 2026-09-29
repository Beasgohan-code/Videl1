"""Phase 21 – /compress: 📚 whole albums, ✂️ cut a part, 🖼 before / after frame, 🌈 HDR → SDR, 🤖 auto-compress;
🌐 web dashboard that opens from Telegram (/dashboard → Mini App / one-time link)."""
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
from urllib.parse import urlencode

import pytest

from tests.harness import load_all, no_botapi, reset_db, run
from tests.test_phase5 import _bot_api_html_problems

MODULES = load_all()

import config  # noqa: E402
from VideoEncoder import data as queue  # noqa: E402
from VideoEncoder.utils import compress as C, ffcmd, hw, jobs, scheduler  # noqa: E402
import VideoEncoder.utils.encoding as encoding  # noqa: E402
import VideoEncoder.plugins.compress as P  # noqa: E402
from tests.test_phase19 import (FileMsg, _command, _panel_key, _run_compress, _tap, _video, cmp, e2e,  # noqa: E402,F401
                                info_of, st, val)

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
    P._auto_cache.clear()
    encoding.SIZE_WATCH.clear()
    yield
    queue[:] = saved
    scheduler._running.clear()
    for d in (scheduler.MODES, scheduler.EXTRA, scheduler.NOTES):
        d.clear()
    jobs._JOBS.clear()
    jobs._RECENT.clear()


HOUR = {"name": "Film.mkv", "size": 2 * 1024 ** 3, "duration": 3600, "height": 1080, "width": 1920}


# ═════════════════════════ ✂️ cut ═════════════════════════
def test_parse_cut_forms():
    assert C.parse_cut("10:00-25:00") == [600, 1500]
    assert C.parse_cut("1:30-") == [90, 0]
    assert C.parse_cut("-5:00") == [0, 300]
    assert C.parse_cut("1:02:03-1:10:00") == [3723, 4200]
    assert C.parse_cut("90–120") == [90, 120]                     # en dash from phone keyboards
    for bad in ("25:00-10:00", "-", "abc", "480", "10mb", "5-5"):
        assert C.parse_cut(bad) is None, bad


def test_args_cut_is_not_one_shot_but_quality_is():
    a = C.parse_args("/compress 10:00-25:00")
    assert a == {"cut": [600, 1500]} and not C.is_oneshot(a)
    b = C.parse_args("/compress 480 10:00-25:00 keephdr nocompare")
    assert b["res"] == "480" and b["cut"] == [600, 1500] and b["sdr"] is False and b["compare"] is False
    assert C.is_oneshot(b)


def test_cut_is_never_remembered():
    o = C.normalize({"res": "480", "cut": [1, 9], "album": True})
    r = C.remembered(o)
    assert r["res"] == "480" and r["cut"] is None and r["album"] is False


def test_cut_window_rules():
    assert ffcmd.cut_window([600, 1500], 3600) == (600, 900)
    assert ffcmd.cut_window([600, 0], 3600) == (600, 3000)        # to the end
    assert ffcmd.cut_window([600, 9999], 3600) == (600, 3000)     # clamped
    assert ffcmd.cut_window([0, 3600], 3600) is None              # the whole video anyway
    assert ffcmd.cut_window([4000, 5000], 3600) is None           # past the end
    assert ffcmd.cut_window([60, 0], 0) is None                   # "to the end" of an unknown length
    assert ffcmd.cut_window([60, 90], 0) == (60, 30)


def test_view_and_estimate_follow_the_cut():
    o = C.normalize({"cut": [600, 1500]})
    v = C.view(o, HOUR)
    assert v["duration"] == 900 and v["size"] == HOUR["size"] // 4 and v["span"] == (600, 900)
    assert C.estimate(o, HOUR) is not None
    assert C.estimate(o, v) < C.estimate(C.normalize({}), HOUR)
    text = C.panel_text(o, HOUR)
    assert "✂️" in text and "10:00–25:00" in text and not _bot_api_html_problems(text)


def test_build_command_cut_seeks_input_and_targets_the_part():
    info = info_of(dur=3600)
    cmd = ffcmd.build_command("in.mkv", "o.mp4", {"resolution": "480", "mode": "size", "target_mb": 50,
                                                  "extensions": "MP4"}, info, cut=(600, 900))
    i = cmd.index("-i")
    assert cmd[cmd.index("-ss")] and cmd.index("-ss") < i and val(cmd, "-ss") == "600.00" and val(cmd, "-t") == "900.00"
    kbps = int(val(cmd, "-b:v").rstrip("k"))
    assert kbps == ffcmd.target_video_kbps(50, 900, ffcmd.audio_kbps(ffcmd.merge({"resolution": "480"}), info))
    assert val(cmd, "-map_chapters") == "-1"
    whole = ffcmd.build_command("in.mkv", "o.mp4", {"resolution": "480", "mode": "size", "target_mb": 50}, info)
    assert int(val(whole, "-b:v").rstrip("k")) < kbps and "-ss" not in whole and val(whole, "-map_chapters") == "0"


# ═════════════════════════ 🌈 HDR → SDR ═════════════════════════
def _hdr_info(transfer="smpte2084"):
    v = st(0, "video", "hevc", width=3840, height=2160, color_transfer=transfer, color_primaries="bt2020")
    return ffcmd.summarize({"streams": [v, st(1, "audio", "aac", channels=2, sample_rate="48000")],
                            "format": {"duration": "60", "size": str(400 * 1024 ** 2)}})


def test_hdr_is_detected():
    assert _hdr_info()["hdr"] == "HDR10" and _hdr_info("arib-std-b67")["hdr"] == "HLG"
    assert info_of()["hdr"] == "" and ffcmd.summarize(None)["hdr"] == ""
    assert C.wants_tonemap({}, _hdr_info()) == "HDR10"
    assert C.wants_tonemap({"sdr": False}, _hdr_info()) == ""
    assert C.wants_tonemap({}, info_of()) == ""


def test_tonemap_runs_after_the_downscale_and_tags_bt709():
    cmd = ffcmd.build_command("i", "o.mp4", {"resolution": "720", "preset": "vf"}, _hdr_info(), tonemap=True)
    vf = val(cmd, "-vf")
    assert vf.index("scale=-2:720") < vf.index("tonemap=tonemap=hable") and vf.endswith("format=yuv420p")
    assert val(cmd, "-color_trc") == "bt709" and val(cmd, "-colorspace") == "bt709"
    plain = ffcmd.build_command("i", "o.mp4", {"resolution": "720"}, _hdr_info())
    assert "tonemap" not in (val(plain, "-vf") or "") and "-color_trc" not in plain
    vaapi = ffcmd.build_command("i", "o.mp4", {"resolution": "720"}, _hdr_info(), encoder="h264_vaapi", tonemap=True)
    assert "tonemap" not in (val(vaapi, "-vf") or "")               # GPU surfaces: skipped, never broken


@needs_ffmpeg
def test_has_filter_reads_this_ffmpeg():
    hw._FILTERS.clear()
    assert hw.has_filter("scale") and hw.has_filter("hstack") and not hw.has_filter("no_such_filter_x")


@needs_ffmpeg
def test_real_tonemap_chain_encodes(tmp_path):
    hw._FILTERS.clear()
    if not hw.has_filter("zscale"):
        pytest.skip("ffmpeg without zscale")
    src = tmp_path / "hdr.mkv"
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=24:duration=2", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p10le", "-color_primaries", "bt2020", "-color_trc", "smpte2084",
                    "-colorspace", "bt2020nc", str(src)], check=True)
    out = tmp_path / "sdr.mp4"
    info = ffcmd.summarize({"streams": [st(0, "video", "h264", width=1280, height=720, color_transfer="smpte2084")],
                            "format": {"duration": "2"}})
    cmd = ffcmd.build_command(str(src), str(out), {"resolution": "480", "preset": "uf", "extensions": "MP4"},
                              info, tonemap=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    probe = subprocess.run([FF, "-hide_banner", "-i", str(out)], capture_output=True, text=True).stderr
    assert "bt709" in probe and "480" in probe


# ═════════════════════════ 🖼 before / after ═════════════════════════
@needs_ffmpeg
def test_compare_frame_is_two_pictures_side_by_side(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for path, size in ((a, "1280x720"), (b, "640x360")):
        subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=size={size}:rate=24:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
                        "-pix_fmt", "yuv420p", str(path)], check=True)
    dest = tmp_path / "cmp.jpg"
    r = subprocess.run(ffcmd.compare_command(str(a), str(b), 1.2, 1.2, 360, str(dest)), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    from PIL import Image
    with Image.open(dest) as im:
        assert im.size == (1280, 360)                              # 640 + 640 at the result's height
    cap = C.compare_caption({"res": "360"}, {"height": 720}, 50 * 1024 ** 2, 9 * 1024 ** 2, 72)
    assert "720p" in cap and "360p" in cap and "1:12" in cap and not _bot_api_html_problems(cap)


# ═════════════════════════ e2e: cut + compare through compress_task ═════════════════════════
@needs_ffmpeg
def test_e2e_compress_a_cut_with_before_after_frame(e2e, monkeypatch):
    e = e2e
    src = e.tmp / "clip.mp4"
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=1280x720:rate=24:duration=12", "-f", "lavfi", "-i", "sine=f=440:duration=12",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "128k", str(src)], check=True)
    e.infos["clip.mp4"] = ffcmd.summarize({"streams": [st(0, "video", "h264", width=1280, height=720),
                                                       st(1, "audio", "aac", channels=2, sample_rate="48000")],
                                           "format": {"duration": "12", "size": str(os.path.getsize(src))}})
    photos = []
    from tests.harness import FakeMsg

    async def reply_photo(self, photo, *a, **k):
        from PIL import Image
        with Image.open(photo) as im:
            photos.append((im.size, k.get("caption")))
        return None
    monkeypatch.setattr(FakeMsg, "reply_photo", reply_photo)
    fm = FileMsg(src)
    fm.video.height, fm.video.width, fm.video.duration = 720, 1280, 12
    edits = _run_compress(e, fm, {"res": "360", "level": "balanced", "audio": "64", "cut": [3, 9]})
    assert e.uploaded, edits[-1]
    name, size, probe = e.uploaded[0]
    assert "[360p 0.03–0.09]" in name and "00:00:06" in probe       # 6 s cut, named after the part (no ':')
    assert "𝗖𝗢𝗠𝗣𝗥𝗘𝗦𝗦𝗘𝗗" in edits[-1] and C.sc("cut") + " 0:03–0:09" in edits[-1]
    assert not _bot_api_html_problems(edits[-1])
    assert photos and photos[0][0] == (1280, 360)                    # before + after, 640×360 each
    assert "360p" in photos[0][1] and "720p" in photos[0][1]


# ═════════════════════════ 📚 album ═════════════════════════
def _album_video(i, gid="G1", uid=5, height=1080):
    m = _video(uid=uid, height=height, name=f"Ep{i}.mkv")
    m.media_group_id = gid
    m.video.file_size = (100 + i) * 1024 ** 2
    m.video.duration = 600
    return m


class AlbumApp:
    def __init__(self, items):
        self.items = items

    async def get_media_group(self, chat_id, msg_id):
        return self.items


def test_album_view_totals_and_estimate():
    items = [{"size": 100 * 1024 ** 2, "duration": 600, "height": 1080},
             {"size": 200 * 1024 ** 2, "duration": 1200, "height": 720}]
    meta = dict(items[0], album=items)
    v = C.view({"album": True}, meta)
    assert v["size"] == 300 * 1024 ** 2 and v["duration"] == 1800 and v["height"] == 1080 and len(v["items"]) == 2
    one = C.estimate({"album": False}, meta)
    both = C.estimate({"album": True}, meta)
    assert both > one
    text = C.panel_text({"album": True}, meta)
    assert "📚" in text and "≤ 1080p" in text and not _bot_api_html_problems(text)
    kb = C.keyboard(C.normalize({"album": True}), meta)
    datas = [b.callback_data for r in kb.inline_keyboard for b in r]
    assert "cmp:album:on" in datas and "cmp:album:off" in datas
    assert [b.callback_data for b in kb.inline_keyboard[1]][0].startswith("cmp:quick:")   # quick row stays row 1


def test_album_panel_and_start_queues_every_video(cmp, monkeypatch):
    spawned = []
    import core.bg as bg
    monkeypatch.setattr(bg, "spawn", lambda coro, name=None: spawned.append(coro))
    items = [_album_video(i) for i in range(3)]
    m = _command("/compress", reply=items[1])
    run(P.compress_cmd(AlbumApp(items), m))
    panel, key = _panel_key(m)
    st_ = P._panels[key]
    assert st_["opts"]["album"] is True and len(st_["album"]) == 3
    assert "3" in m.replies[-1]
    _tap("cmp:album:off", panel)
    assert P._panels[key]["opts"]["album"] is False
    _tap("cmp:album:on", panel)
    _tap("cmp:go", panel)
    assert key not in P._panels and len(spawned) == 1
    run(spawned.pop())                                            # _start_album → one spawn per video
    assert len(spawned) == 3
    for coro in spawned:
        run(coro)
    msgs = [q[0] for q in P.queued]
    assert [x.reply_to_message for x in msgs] == items
    assert len({scheduler.task_key(x) for x in msgs}) == 3            # own folders / duplicate check each
    assert all(x.from_user.id == 5 and x.chat.id == m.chat.id for x in msgs)
    assert all(q[2]["album"] is False and q[3] is not None for q in P.queued)   # each has its own status card
    assert "𝗔𝗟𝗕𝗨𝗠" in panel.edits[-1] and "3" in panel.edits[-1]


def test_album_respects_the_queue_limit(cmp, monkeypatch):
    spawned = []
    import core.bg as bg
    monkeypatch.setattr(bg, "spawn", lambda coro, name=None: spawned.append(coro))
    monkeypatch.setattr(config, "ENC_MAX_TASKS_FREE", 2)
    items = [_album_video(i) for i in range(5)]
    m = _command("/compress 480", reply=items[0])                   # one-shot on an album
    run(P.compress_cmd(AlbumApp(items), m))
    assert len(spawned) == 2
    for coro in spawned:
        run(coro)
    assert [q[0].reply_to_message for q in P.queued] == items[:2]
    assert "<b>2</b> / 5" in m.replies[-1] and "3 " + C.sc("skipped") in m.replies[-1]


def test_cut_on_an_album_is_about_one_video(cmp):
    items = [_album_video(i) for i in range(2)]
    m = _command("/compress 1:00-2:00", reply=items[0])
    run(P.compress_cmd(AlbumApp(items), m))
    _panel, key = _panel_key(m)
    assert P._panels[key]["opts"]["album"] is False and P._panels[key]["opts"]["cut"] == [60, 120]


def test_album_item_proxy_is_a_normal_task_for_the_scheduler():
    cmd = _command("/compress")
    item = _album_video(7)
    x = P.AlbumItem(cmd, item)
    assert x.id == item.id and x.reply_to_message is item and x.from_user is cmd.from_user and x.video is None
    assert scheduler.signature(x, "compress", {}) != scheduler.signature(P.AlbumItem(cmd, _album_video(8)),
                                                                         "compress", {})


# ═════════════════════════ 🖼 🌈 🤖 buttons ═════════════════════════
def test_new_toggles_on_the_panel(cmp):
    m = _command("/compress", reply=_video(height=1080))
    run(P.compress_cmd(None, m))
    panel, key = _panel_key(m)
    assert P._panels[key]["opts"]["compare"] is True and P._panels[key]["opts"]["sdr"] is True
    _tap("cmp:compare", panel)
    _tap("cmp:sdr", panel)
    o = P._panels[key]["opts"]
    assert o["compare"] is False and o["sdr"] is False
    assert C.sc("keep HDR") in panel.edits[-1] and C.sc("before / after") not in panel.edits[-1]
    assert not _bot_api_html_problems(panel.edits[-1])
    q = _tap("cmp:auto", panel)
    assert P._panels[key]["auto"] is True and q.answers[-1][1] is True       # explained in an alert
    assert run(P._auto_on(5, fresh=True)) is True
    assert run(P._last(5))["compare"] is False                               # auto uses what's on screen


def test_cut_clear_button(cmp):
    m = _command("/compress 10:00-20:00", reply=_video(height=1080))
    run(P.compress_cmd(None, m))
    panel, key = _panel_key(m)
    kb = C.keyboard(P._panels[key]["opts"], P._panels[key]["meta"])
    assert "cmp:cut:clear" in [b.callback_data for r in kb.inline_keyboard for b in r]
    _tap("cmp:cut:clear", panel)
    assert P._panels[key]["opts"]["cut"] is None


# ═════════════════════════ 🤖 auto-compress ═════════════════════════
def test_compress_auto_on_off_command(cmp):
    m = _command("/compress auto on")
    run(P.compress_cmd(None, m))
    assert run(P._auto_on(5, fresh=True)) is True and "480p" not in m.replies[-1]
    assert not _bot_api_html_problems(m.replies[-1])
    m = _command("/compress auto off")
    run(P.compress_cmd(None, m))
    assert run(P._auto_on(5, fresh=True)) is False
    m = _command("/compress auto")                                 # bare → toggle
    run(P.compress_cmd(None, m))
    assert run(P._auto_on(5, fresh=True)) is True


def test_auto_filter_rules(cmp, monkeypatch):
    big = _video(height=1080)
    assert run(P._auto_filter(None, None, big)) is False            # off by default
    run(P._set_auto(5, True))
    assert run(P._auto_filter(None, None, big)) is True
    small = _video(height=720)
    small.video.file_size = 2 * 1024 ** 2
    assert run(P._auto_filter(None, None, small)) is False           # nothing worth saving
    cmd = _video(height=1080)
    cmd.caption = "/dl"
    assert run(P._auto_filter(None, None, cmd)) is False             # a command with the file attached
    import renamer.handlers as rn
    rn._sequences[5] = []
    try:
        assert run(P._auto_filter(None, None, big)) is False         # busy with a rename sequence
    finally:
        rn._sequences.pop(5, None)


def test_auto_compress_handler_queues_with_the_saved_choice(cmp):
    run(P._set_auto(5, True))
    run(P._remember(5, C.normalize({"res": "480", "level": "strong"})))
    v = _video(height=1080)
    run(P.auto_compress(None, v))
    msg, mode, extra, card = P.queued[-1]
    assert msg is v and mode == "compress" and extra["res"] == "480" and extra["level"] == "strong"
    assert "<code>/compress auto off</code>" in v.replies[-1] and not _bot_api_html_problems(v.replies[-1])


def test_auto_handler_is_registered_before_auto_rename():
    """Same group (0) as the renamer's incoming_file and registered earlier – so it wins while it's on."""
    import run as runner

    class Recorder:
        def __init__(self):
            self.added = []

        def add_handler(self, handler, group=0):
            cb = getattr(handler, "original_callback", None) or handler.callback     # pyrofork wraps it
            self.added.append((group, cb.__module__, cb.__name__))
    app = Recorder()
    runner.load_plugins(app)
    g0 = [(mod, name) for g, mod, name in app.added if g == 0]
    auto = g0.index(("VideoEncoder.plugins.compress", "auto_compress"))
    rename = g0.index(("renamer.handlers", "incoming_file"))
    assert auto < rename


# ═════════════════════════ 🌐 web dashboard ═════════════════════════
TOKEN = "123456:TEST-token"


def _init_data(uid=111, age=0, token=TOKEN, extra=None, drop_signature_in_hash=False):
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "AAE",
              "user": json.dumps({"id": uid, "first_name": "Owner"}, separators=(",", ":"))}
    fields.update(extra or {})
    use = {k: v for k, v in fields.items() if not (drop_signature_in_hash and k == "signature")}
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(use.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_check_init_data():
    import keep_alive as K
    assert K.check_init_data(_init_data(), TOKEN)["id"] == 111
    assert K.check_init_data(_init_data(extra={"signature": "abc"}), TOKEN)["id"] == 111
    assert K.check_init_data(_init_data(extra={"signature": "abc"}, drop_signature_in_hash=True), TOKEN)["id"] == 111
    assert K.check_init_data(_init_data(token="999:other"), TOKEN) is None       # another bot's data
    assert K.check_init_data(_init_data(age=7200), TOKEN) is None                 # stale
    tampered = _init_data().replace("111", "112")
    assert K.check_init_data(tampered, TOKEN) is None
    assert K.check_init_data("", TOKEN) is None and K.check_init_data("x=1", TOKEN) is None


def _web(calls, token=""):
    import keep_alive as K
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        K._fails.clear()
        client = TestClient(TestServer(K.make_app()))
        await client.start_server()
        try:
            out = []
            for method, path, headers, body in calls:
                if callable(body):
                    body = body(out)
                if callable(headers):
                    headers = headers(out)
                r = await (client.post(path, json=body, headers=headers) if method == "POST"
                           else client.get(path, headers=headers, allow_redirects=False))
                out.append((r.status, await r.read(), dict(r.headers)))
            return out
        finally:
            await client.close()
    return run(go())


@pytest.fixture
def web(monkeypatch):
    import keep_alive as K
    monkeypatch.setattr(config, "WEB_DASHBOARD", True)
    monkeypatch.setattr(config, "ADMIN_WEB_TOKEN", "")
    monkeypatch.setattr(config, "BOT_TOKEN", TOKEN)
    K._links.clear()
    K._sessions.clear()
    return K


def _sess(out, i=-1):
    return {"X-Admin-Token": json.loads(out[i][1])["session"]}


def test_one_time_link_signs_in_once(web):
    key = web.issue_link()
    res = _web([("POST", "/admin/api/login", {}, {"link": key}),
                ("GET", "/admin/api/stats?days=7", lambda out: _sess(out, 0), None),
                ("POST", "/admin/api/login", {}, {"link": key})])                 # second use
    assert res[0][0] == 200 and res[1][0] == 200 and res[2][0] == 401
    data = json.loads(res[1][1])
    assert data["days"] == 7 and "live" in data


def test_expired_link_is_refused(web):
    key = web.issue_link()
    web._links[web._h(key)] = time.time() - 1
    assert _web([("POST", "/admin/api/login", {}, {"link": key})])[0][0] == 401


def test_mini_app_sign_in_admins_only(web):
    ok = _web([("POST", "/admin/api/login", {}, {"init": _init_data(uid=111)}),
               ("GET", "/admin/api/stats", lambda out: _sess(out, 0), None)])
    assert ok[0][0] == 200 and json.loads(ok[0][1])["via"] == "telegram" and ok[1][0] == 200
    stranger = _web([("POST", "/admin/api/login", {}, {"init": _init_data(uid=999)})])
    assert stranger[0][0] == 403
    forged = _web([("POST", "/admin/api/login", {}, {"init": _init_data(uid=111, token="1:x")})])
    assert forged[0][0] == 401


def test_page_can_open_inside_telegram_web(web):
    page = _web([("GET", "/admin", {}, None)])[0]
    assert page[0] == 200 and "X-Frame-Options" not in page[2]
    assert "web.telegram.org" in page[2]["Content-Security-Policy"] and "frame-ancestors" in page[2]["Content-Security-Policy"]
    html = page[1].decode()
    assert "tgWebAppData" in html and "admin/api/login" in html and "/dashboard" in html


def test_login_attempts_are_rate_limited(web):
    res = _web([("POST", "/admin/api/login", {}, {"link": f"bad{i}"}) for i in range(11)])
    assert [r[0] for r in res[:10]] == [401] * 10 and res[10][0] == 429


def test_legacy_token_still_works(web, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_WEB_TOKEN", "s3cret")
    res = _web([("GET", "/admin/api/stats", {"X-Admin-Token": "s3cret"}, None)])
    assert res[0][0] == 200


def test_dashboard_buttons_private_vs_group(web, monkeypatch):
    from core import analytics
    monkeypatch.setattr(config, "KEEP_ALIVE_URL", "https://videl.example.com/")
    rows = analytics.web_rows(True)
    btns = rows[0]
    assert btns[0].web_app.url == "https://videl.example.com/admin"
    assert btns[1].url.startswith("https://videl.example.com/admin#l.") and len(web._links) == 1
    assert analytics.web_rows(False) == []                          # never a sign-in link in a group
    assert "videl.example.com/admin" in analytics.web_hint(True)
    monkeypatch.setattr(config, "KEEP_ALIVE_URL", "")
    assert analytics.web_rows(True) == [] and "KEEP_ALIVE_URL" in analytics.web_hint(True)
    monkeypatch.setattr(config, "WEB_DASHBOARD", False)
    assert analytics.web_hint(True) == ""


def test_live_queue_rows(web):
    m = _command("/compress", reply=_video(height=1080, name="Big.mkv"))
    scheduler.add(m, "compress", extra={})
    rows = web._live()
    assert rows and rows[0]["mode"] == "compress" and rows[0]["file"] == "Big.mkv" and rows[0]["user"] == 5

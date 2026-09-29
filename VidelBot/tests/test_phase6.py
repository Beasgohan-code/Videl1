"""Phase 6 – Auto-Rename module (templates · metadata · sequence · leaderboard · verification),
dynamic admins and per-channel force-sub modes."""
import os
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from pyrogram import StopPropagation, StopTransmission, enums

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run

MODULES = load_all()


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    from renamer import engine, handlers, store, verify
    store._cache.clear()
    handlers._state.clear()
    handlers._sequences.clear()
    handlers._seq_notes.clear()
    verify._input.clear()
    engine._recent.clear()
    engine._cancel_before.clear()
    engine._pending.clear()
    engine._jobs.clear()
    yield


def _filter(f, msg):
    import inspect
    r = f(None, msg)
    return run(r) if inspect.isawaitable(r) else bool(r)


def file_msg(name="Show.S01E02.720p.mkv", uid=5, size=1000, kind="document", unique="u1"):
    m = FakeMsg(text=None, uid=uid)
    media = SimpleNamespace(file_name=name, file_size=size, file_unique_id=unique, file_id="fid-" + unique,
                            mime_type="video/x-matroska", thumbs=None, duration=0, width=0, height=0)
    m.document = m.video = m.audio = None
    setattr(m, kind, media)
    m.media = kind
    m.photo = None
    return m


# ───────────────────────── extraction & templates ─────────────────────────
def test_extractors_cover_common_release_names():
    from renamer.extract import parse
    p = parse("[SubsPlease] Solo Leveling - 05 (1080p) [ABC123].mkv")
    assert (p["episode"], p["quality"], p["title"]) == (5, "1080p", "Solo Leveling")
    p = parse("Naruto.Shippuden.S02E15.720p.WEB-DL.Hindi.Eng.Dual.Audio.x265.mp4")
    assert (p["season"], p["episode"], p["quality"], p["codec"]) == (2, 15, "720p", "x265")
    assert p["audio"].startswith("Dual") and "Hindi" in p["audio"]
    p = parse("Bleach_TYBW_S1_E12_720p_Eng_Sub.mkv")
    assert (p["season"], p["episode"], p["quality"], p["title"]) == (1, 12, "720p", "Bleach TYBW")
    p = parse("Attack on Titan S04EP28 [Tamil] 4K HEVC.mkv")
    assert (p["season"], p["episode"], p["quality"]) == (4, 28, "4K")
    # resolutions / years / words containing "ep" are never episodes
    assert parse("Deep.Sea.2023.720p.BluRay.mkv")["episode"] is None
    assert parse("One Piece Episode 1071 480p.mkv")["episode"] == 1071


def test_render_classic_and_brace_templates():
    from renamer.extract import new_filename, render
    name = "Naruto.S02E15.720p.Hindi.mp4"
    assert new_filename("[SSeason] [EPEpisode] [Quality] [Audio] @Chan", name, to_mkv=True) == \
        "[S02] [EP15] [720p] [Hindi] @Chan.mkv"
    assert new_filename("{title} S{season}E{episode} {quality}", name) == "Naruto S02E15 720p.mp4"
    # movie (no season / episode) → S··E·· dropped (Phase 12); empty brackets removed; bad chars stripped
    assert render("{title} S{season}E{episode} [{audio}] a/b:c", "Movie.mkv") == "Movie a b c"
    # an episode without a season still defaults the season to 01
    assert render("{title} S{season}E{episode}", "Show - 07.mkv") == "Show S01E07"
    # keyword replacement never touches inserted values
    assert render("{title} - Audio", "The Audio Show S01E02 AAC.mkv") == "The Audio Show - AAC"


def test_nsfw_is_word_based():
    from renamer.extract import is_nsfw
    assert is_nsfw("Some.Hentai.Video.mkv") == "hentai video"      # most specific keyword, every run
    assert is_nsfw("Some.Hentai.Clip.mkv") == "hentai"
    for ok in ("Mass Effect.mkv", "Assassination Classroom S01E01.mkv", "Cosplay Culture Doc.mkv",
               "Glass Onion 2022.mkv", "Essex Crimes.mkv"):
        assert not is_nsfw(ok), ok


def test_sequence_sort_key():
    from renamer.extract import sort_key
    names = ["Show S02E01 720p.mkv", "Show S01E10 1080p.mkv", "Show S01E02 1080p.mkv", "Show S01E02 480p.mkv"]
    assert sorted(names, key=sort_key) == ["Show S01E02 480p.mkv", "Show S01E02 1080p.mkv",
                                           "Show S01E10 1080p.mkv", "Show S02E01 720p.mkv"]


def test_ffmpeg_command_maps_every_metadata_field():
    from renamer.engine import ffmpeg_command
    cmd = ffmpeg_command("in.mp4", "out.mkv", {"title": "T", "audio": "A", "subtitle": "S", "video": "V",
                                               "encoded_by": "E", "author": ""}, to_mkv=True, srt=True)
    s = " ".join(cmd)
    assert "-map 0 -c copy -c:s srt" in s and "-f matroska" in s and cmd[-1] == "out.mkv"
    assert "-metadata title=T" in s and "-metadata:s:a title=A" in s and "-metadata:s:s title=S" in s
    assert "-metadata:s:v title=V" in s and "encoded_by=E" in s and "author=" not in s


# ───────────────────────── commands & panels ─────────────────────────
def test_autorename_saves_template_and_panel():
    from renamer import store
    from renamer.handlers import autorename_cmd
    m = FakeMsg("/autorename {title} S{season}E{episode} [{quality}]")
    run(autorename_cmd(FakeClient(), m))
    assert "Template saved" in m.replies[-1] and "S02E05" in m.replies[-1]
    assert run(store.get(5))["template"] == "{title} S{season}E{episode} [{quality}]"
    m2 = FakeMsg("/autorename")
    run(autorename_cmd(FakeClient(), m2))
    assert "🟢 Active" in m2.replies[-1]


def test_panel_toggles_and_media_choice():
    from renamer import store
    from renamer.handlers import rename_cb
    run(store.set_template(5, "{title}"))
    for data in ("rn:auto", "rn:mkv", "rn:mt:video", "rn:mon:1"):
        q = FakeQuery(data)
        run(rename_cb(FakeClient(), q))
        assert q.answers
    s = run(store.get(5))
    assert s["auto"] is False and s["mkv"] is False and s["media"] == "video" and s["meta_on"] is True
    run(rename_cb(FakeClient(), FakeQuery("rn:mt:auto")))
    assert "media" not in run(store.get(5))


def test_metadata_input_flow_and_commands():
    from renamer import handlers, store
    q = FakeQuery("rn:mf:title")
    run(handlers.rename_cb(FakeClient(), q))
    msg = FakeMsg("My Release", uid=5)
    msg.photo = None
    assert _filter(handlers.input_filter, msg)
    with pytest.raises(StopPropagation):
        run(handlers.rename_input(FakeClient(), msg))
    assert run(store.get(5))["meta"]["title"] == "My Release"
    m = FakeMsg("/setencoded_by Videl Team")
    run(handlers.set_meta_cmd(FakeClient(), m))
    assert run(store.get(5))["meta"]["encoded_by"] == "Videl Team"
    m = FakeMsg("/setencoded_by")
    run(handlers.set_meta_cmd(FakeClient(), m))
    assert "encoded_by" not in run(store.get(5))["meta"]


def test_auto_filter_needs_active_template():
    from core.db import vdb
    from renamer import handlers, store
    msg = file_msg()
    assert not _filter(handlers.auto_filter, msg)           # no template → normal Videl behaviour
    run(store.set_template(5, "{title}"))
    assert _filter(handlers.auto_filter, msg)
    run(store.update(5, auto=False))
    assert not _filter(handlers.auto_filter, msg)            # paused
    run(store.update(5, auto=True))
    run(vdb.set_setting("rn_enabled", False))
    assert not _filter(handlers.auto_filter, msg)            # disabled by admin
    run(vdb.set_setting("rn_enabled", True))
    from filestore.main_bot.plugins.create_bot import _creation_state
    _creation_state[5] = {"step": "token"}
    try:
        assert not _filter(handlers.auto_filter, msg)        # clone wizard has priority
    finally:
        _creation_state.pop(5, None)


def test_incoming_file_queues_and_dedupes(monkeypatch):
    from renamer import engine, handlers, store
    run(store.set_template(5, "{title}"))
    got = []

    async def fake_submit(client, message):
        got.append(message)
        return len(got)
    monkeypatch.setattr(engine, "submit", fake_submit)
    m1 = file_msg(unique="a")
    run(handlers.incoming_file(FakeClient(), m1))
    run(handlers.incoming_file(FakeClient(), file_msg(unique="a")))   # duplicate update ignored
    m2 = file_msg(unique="b")
    run(handlers.incoming_file(FakeClient(), m2))
    assert got == [m1, m2] and "#2" in m2.replies[-1]


def test_sequence_collects_and_sends_sorted(monkeypatch):
    from renamer import engine, handlers, store
    run(store.set_template(5, "{title} {episode}"))
    order = []

    async def fake_submit(client, message):
        order.append(engine.original_name(message))
        return 1
    monkeypatch.setattr(engine, "submit", fake_submit)
    c = FakeClient()
    run(handlers.start_sequence_cmd(c, FakeMsg("/start_sequence")))
    for i, n in enumerate(["Show E03.mkv", "Show E01.mkv", "Show E02.mkv"]):
        m = file_msg(n, unique=f"s{i}")
        assert _filter(handlers.auto_filter, m)
        run(handlers.incoming_file(c, m))
    end = FakeMsg("/end_sequence")
    run(handlers.end_sequence_cmd(c, end))
    assert order == ["Show E01.mkv", "Show E02.mkv", "Show E03.mkv"]
    assert "3 file(s) queued in order" in end.replies[-1]
    assert 5 not in handlers._sequences


def test_testrename_preview():
    from renamer import store
    from renamer.handlers import testrename_cmd
    run(store.set_template(5, "{title} S{season}E{episode}"))
    m = FakeMsg("/testrename Some.Show.S03E07.1080p.mp4")
    run(testrename_cmd(FakeClient(), m))
    assert "Some Show S03E07.mkv" in m.replies[-1] and "1080p" in m.replies[-1]


# ───────────────────────── engine pipeline ─────────────────────────
class PipelineClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.uploads = []

    async def download_media(self, message, file_name=None, progress=None, **k):
        os.makedirs(os.path.dirname(file_name), exist_ok=True)
        with open(file_name, "wb") as f:
            f.write(b"x" * 2048)
        if progress:
            await progress(2048, 2048)
        return file_name

    async def send_document(self, chat_id, path, **k):
        assert os.path.exists(path)
        self.uploads.append(("document", os.path.basename(path), k))
        sent = FakeMsg(text=None, uid=0)
        sent.copies = []

        async def copy(chat, **kk):
            sent.copies.append((chat, kk))
        sent.copy = copy
        self.last_sent = sent
        return sent

    async def send_video(self, chat_id, path, **k):
        self.uploads.append(("video", os.path.basename(path), k))
        return FakeMsg(text=None, uid=0)


def test_process_renames_uploads_counts_and_dumps():
    from core.db import vdb
    from renamer import engine, store
    run(store.set_template(5, "{title} S{season}E{episode} [{quality}]"))
    run(vdb.set_setting("rn_dump", -1001))
    c = PipelineClient()
    msg = file_msg("Show.S01E02.720p.mkv")
    job = engine.Job(uid=5, message=msg)
    run(engine.process(c, job))
    kind, name, k = c.uploads[-1]
    assert (kind, name, k["file_name"]) == ("document", "Show S01E02 [720p].mkv", "Show S01E02 [720p].mkv")
    assert "Show S01E02 [720p].mkv" in k["caption"]
    assert run(store.get(5))["count"] == 1
    assert c.last_sent.copies and c.last_sent.copies[0][0] == -1001
    assert not os.listdir(engine.WORK_DIR) or all(not d.startswith("5_") for d in os.listdir(engine.WORK_DIR))


def test_process_mp4_without_ffmpeg_keeps_container(monkeypatch):
    from renamer import engine, store
    run(store.set_template(5, "{title} E{episode}"))
    monkeypatch.setattr(engine, "remux", lambda *a, **k: _async(""))
    c = PipelineClient()
    job = engine.Job(uid=5, message=file_msg("Show.E05.mp4"))
    run(engine.process(c, job))
    assert c.uploads[-1][1] == "Show E05.mp4"
    assert "skipped" in job.status.edits[-1] and not job.status.deleted


async def _async(v):
    return v


def test_process_rejects_nsfw_and_huge_files():
    from renamer import engine, store
    run(store.set_template(5, "{title}"))
    c = PipelineClient()
    bad = file_msg("hot.hentai.video.mkv")
    run(engine.process(c, engine.Job(uid=5, message=bad)))
    assert "NSFW" in bad.replies[-1] and not c.uploads
    big = file_msg("Show.mkv", size=engine.TG_LIMIT + 1, unique="big")
    run(engine.process(c, engine.Job(uid=5, message=big)))
    assert "Too big" in big.replies[-1]


def test_media_preference_video_and_progress_cancel():
    from renamer import engine, store
    run(store.set_template(5, "{title}"))
    run(store.update(5, media="video"))
    c = PipelineClient()
    run(engine.process(c, engine.Job(uid=5, message=file_msg("Clip.mkv"))))
    assert c.uploads[-1][0] == "video" and c.uploads[-1][2]["supports_streaming"] is True
    job = engine.Job(uid=5, message=file_msg())
    job.status = FakeMsg("s", uid=0)
    job.cancelled = True
    with pytest.raises(StopTransmission):
        run(engine.progress_cb(job, "x")(1, 10))


def test_cancel_user_drops_queue():
    from renamer import engine, handlers
    j = engine.Job(uid=5, message=file_msg())
    engine._jobs[j.id] = j
    handlers._sequences[5] = []
    done = handlers.clear_user(5)
    assert j.cancelled and any("rename job" in d for d in done) and 5 not in handlers._sequences


def test_pick_type_guesses_like_original():
    from renamer.engine import pick_type
    assert pick_type(None, "a.mp3") == "audio"
    assert pick_type(None, "a.mkv") == "document"
    assert pick_type("video", "a.mkv") == "video"


# ───────────────────────── leaderboard ─────────────────────────
def test_leaderboard_periods_and_rank():
    from renamer import store
    from renamer.handlers import leaderboard_view
    from tests.harness import FakeUser
    for uid, n in ((1, 3), (2, 5), (5, 1)):
        for _ in range(n):
            run(store.record_rename(FakeUser(uid, first_name=f"U{uid}")))
    rows, rank, mine = run(store.leaderboard("all", 5))
    assert [r[0] for r in rows] == [2, 1, 5] and (rank, mine) == (3, 1)
    rows, rank, mine = run(store.leaderboard("today", 1))
    assert rows[0][:2] == (2, 5) and (rank, mine) == (2, 3)
    text, kb = run(leaderboard_view("week", 5))
    assert "U2" in text and "Your rank:</b> #3" in text


def test_leaderboard_autodeletes_in_groups(monkeypatch):
    from renamer import handlers
    scheduled = []
    monkeypatch.setattr(handlers, "_delete_later", lambda msgs, d: _record(scheduled, msgs, d))
    m = FakeMsg("/leaderboard", chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
    run(handlers.leaderboard_cmd(FakeClient(), m))
    run(_async(None))
    assert "top 10 renamers" in m.replies[-1]
    assert len(scheduled) == 1 and scheduled[0][1] == handlers.LEADERBOARD_DELETE_TIMER
    assert m in scheduled[0][0] and len(scheduled[0][0]) == 2
    private = FakeMsg("/leaderboard")
    run(handlers.leaderboard_cmd(FakeClient(), private))
    run(_async(None))
    assert len(scheduled) == 1                                # never auto-deleted in DMs


async def _record(bucket, msgs, d):
    bucket.append((msgs, d))


# ───────────────────────── verification ─────────────────────────
def _enable_verify(monkeypatch, result="https://short.ly/abc"):
    from renamer import store
    cfg = run(store.verify_settings())
    cfg["s1"].update(on=True, site="short.ly", api="KEY")
    run(store.save_verify_settings(cfg))
    import filestore.utils.shortener as sh
    calls = []

    async def fake_short(url, api_key="", domain="", provider="adlinkfly"):
        calls.append(url)
        return result or url
    monkeypatch.setattr(sh, "shorten_url", fake_short)
    return calls


def test_verification_gate_and_token_flow(monkeypatch):
    from renamer import store, verify
    c = FakeClient()
    assert run(verify.gate(c, file_msg()))                    # off → allowed
    calls = _enable_verify(monkeypatch)
    m = file_msg()
    assert not run(verify.gate(c, m))
    assert "not verified" in m.replies[-1] and "start=rnv_" in calls[-1]
    token = run(store.get_token_doc(5))["token"]
    # wrong token
    bad = FakeMsg("/start rnv_deadbeef")
    run(verify.handle_start_token(c, bad, "deadbeef"))
    assert "Invalid" in bad.replies[-1]
    # too fast → bypass detected and token burned
    fast = FakeMsg(f"/start rnv_{token}")
    run(verify.handle_start_token(c, fast, token))
    assert "Bypass" in fast.replies[-1] and not run(store.get_token_doc(5)).get("token")
    # new link, finished after 2 minutes → verified
    run(verify.gate(c, file_msg()))
    token = run(store.get_token_doc(5))["token"]
    run(store._vcol().update_one({"_id": 5}, {"$set": {"token_at": store.naive_now() - timedelta(minutes=2)}}))
    ok = FakeMsg(f"/start rnv_{token}")
    run(verify.handle_start_token(c, ok, token))
    assert "successful" in ok.replies[-1]
    assert run(verify.gate(c, file_msg()))
    assert run(store.verify_counts())["today"] == 1


def test_verification_exemptions_and_shortener_failure(monkeypatch):
    import datetime
    from database.db import db as saver_db
    from renamer import verify
    _enable_verify(monkeypatch, result="")                   # shortener returns the input → failure
    m = file_msg()
    assert not run(verify.gate(FakeClient(), m))
    assert "Couldn't create" in m.replies[-1]
    assert run(verify.gate(FakeClient(), file_msg(uid=222)))  # admin
    run(saver_db.add_premium(7, (datetime.date.today() + datetime.timedelta(days=3)).isoformat()))
    assert run(verify.gate(FakeClient(), file_msg(uid=7)))    # premium


def test_start_deeplink_routes_verification(monkeypatch):
    from core.menus import start_cmd
    import renamer.verify as verify
    seen = []

    async def fake(client, message, token):
        seen.append(token)
    monkeypatch.setattr(verify, "handle_start_token", fake)
    run(start_cmd(FakeClient(), FakeMsg("/start rnv_abc123")))
    assert seen == ["abc123"]


def test_verify_settings_admin_input(monkeypatch):
    from renamer import handlers, store, verify
    q = FakeQuery("rnv:set:s2", uid=222)
    run(verify.verify_settings_cb(FakeClient(), q))
    site = FakeMsg("https://gplinks.com/", uid=222)
    assert _filter(handlers.input_filter, site)
    with pytest.raises(StopPropagation):
        run(handlers.rename_input(FakeClient(), site))
    key = FakeMsg("abcdef123456", uid=222)
    with pytest.raises(StopPropagation):
        run(handlers.rename_input(FakeClient(), key))
    cfg = run(store.verify_settings())
    assert cfg["s2"] == {"on": True, "site": "gplinks.com", "api": "abcdef123456"} and key.deleted
    q = FakeQuery("rnv:tog:s1", uid=5)
    run(verify.verify_settings_cb(FakeClient(), q))
    assert "Admins" in q.answers[-1][0] and q.answers[-1][1] is True
    assert run(store.verify_settings())["s1"]["on"] is False


# ───────────────────────── dynamic admins ─────────────────────────
def test_dynamic_admins_apply_to_admin_filters_only():
    import config
    from core import admins
    from core.admin import admin_filter, owner_filter
    c = FakeClient()
    m = FakeMsg("/add_admin 777", uid=111)
    run(admins.add_admin_cmd(c, m))
    try:
        assert 777 in config.ADMINS and 777 in admin_filter and 777 not in owner_filter
        m = FakeMsg("/deladmin 222", uid=111)
        run(admins.del_admin_cmd(c, m))
        assert "env vars" in m.replies[-1] and 222 in config.ADMINS
        text, _ = run(admins._admins_view(111))
        assert "777" in text and "👑 <code>111</code>" in text
    finally:
        run(admins.remove_admin(777))
    assert 777 not in config.ADMINS and 777 not in admin_filter


def test_db_admins_loaded_at_boot():
    import config
    from core import admins
    run(admins._col().insert_one({"_id": 888, "ts": time.time()}))
    try:
        assert run(admins.load_db_admins()) == [888] and 888 in config.ADMINS
    finally:
        run(admins.remove_admin(888))


# ───────────────────────── force-sub per-channel mode ─────────────────────────
def test_fsub_mode_toggle_and_member_left():
    from core import fsub
    from core.db import vdb
    run(vdb.set_setting("fsub_channels", [-1005]))
    c = FakeClient()
    assert run(fsub.missing_channels(c, 5)) == [-1005]
    run(vdb.db["fsub_requests"].insert_one({"chat": "-1005", "user": 5}))
    fsub._ok_cache.clear()
    assert run(fsub.missing_channels(c, 5)) == [-1005]       # request mode off → request doesn't count
    q = FakeQuery("fsm:t:-1005", uid=222)
    q.matches = [SimpleNamespace(group=lambda i: "-1005")]
    run(fsub.fsub_mode_cb(c, q))
    assert run(fsub.request_mode(-1005)) is True
    fsub._ok_cache.clear()
    assert run(fsub.missing_channels(c, 5)) == []
    upd = SimpleNamespace(chat=SimpleNamespace(id=-1005, username=None),
                          new_chat_member=SimpleNamespace(status=enums.ChatMemberStatus.LEFT,
                                                          user=SimpleNamespace(id=5)))
    run(fsub.fsub_member_left(c, upd))
    assert run(vdb.db["fsub_requests"].count_documents({"user": 5})) == 0


# ───────────────────────── menus & credits ─────────────────────────
def test_menus_register_rename_commands_within_limit():
    import core.commands as cm
    user = {n for n, _ in cm.USER_COMMANDS}
    assert {"autorename", "setmedia", "metadata", "start_sequence", "end_sequence", "leaderboard", "verify"} <= user
    assert {"renameset", "admins", "fsub_mode"} <= {n for n, _ in cm.ADMIN_COMMANDS}
    owner = {n for g in (cm.USER_COMMANDS, cm.ADMIN_COMMANDS, cm.OWNER_COMMANDS) for n, _ in g}
    assert len(owner) <= 100


def test_no_upstream_credits_in_new_code():
    banned = ("cantarella", "rexbots", "seishiro", "clutch008", "abhinai", "madflix", "jishudeveloper",
              "botskingdom", "adityaabhinav", "made by abhi")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for rel in ("renamer", "core/admins.py", "core/fsub.py"):
        path = os.path.join(root, rel)
        files = [os.path.join(dp, f) for dp, _, fs in os.walk(path) for f in fs if f.endswith(".py")] \
            if os.path.isdir(path) else [path]
        for f in files:
            text = open(f, encoding="utf-8").read().lower()
            for word in banned:
                assert word not in text, (f, word)


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not installed")
def test_process_real_ffmpeg_mp4_to_mkv_with_metadata(tmp_path):
    import shutil
    import subprocess
    from renamer import engine, store
    src = tmp_path / "in.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
                    "-c:v", "libx264", str(src)], check=True)
    run(store.set_template(5, "{title} E{episode}"))
    run(store.update(5, meta_on=True))
    run(store.set_meta(5, "title", "Videl Test"))
    seen = {}

    class RealClient(PipelineClient):
        async def download_media(self, message, file_name=None, progress=None, **k):
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            shutil.copy(src, file_name)
            return file_name

        async def send_document(self, chat_id, path, **k):
            seen["probe"] = subprocess.run(["ffmpeg", "-hide_banner", "-i", path], capture_output=True, text=True).stderr
            return await super().send_document(chat_id, path, **k)

    c = RealClient()
    job = engine.Job(uid=5, message=file_msg("Show.E07.mp4", size=src.stat().st_size))
    run(engine.process(c, job))
    assert c.uploads[-1][1] == "Show E07.mkv"
    assert "matroska" in seen["probe"] and "Videl Test" in seen["probe"]

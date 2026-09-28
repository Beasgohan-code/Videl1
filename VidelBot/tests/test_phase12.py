"""Phase 12 – Auto-Rename upgrade: smarter names (source · group · ranges · movie-aware), tag cleaning,
word rules, presets, manual mode, caption placeholders, frame thumbnails, history, queue controls
and /rename on the shared pipeline."""
import os
import shutil
import subprocess

import pytest
from pyrogram import StopPropagation

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run
from tests.test_phase6 import PipelineClient, _filter, file_msg

MODULES = load_all()


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    from renamer import engine, handlers, store, verify
    store._cache.clear()
    handlers._state.clear()
    handlers._sequences.clear()
    handlers._seq_notes.clear()
    handlers._prompts.clear()
    verify._input.clear()
    engine._recent.clear()
    engine._cancel_before.clear()
    engine._pending.clear()
    engine._jobs.clear()
    yield
    handlers._prompts.clear()


def _cbs(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row]


async def _async(v):
    return v


# ───────────────────────── extraction ─────────────────────────
def test_source_group_and_episode_ranges():
    from renamer.extract import parse
    p = parse("Show.S01E01-E03.720p.WEB-DL.x264-RARBG.mkv")
    assert (p["season"], p["episode"], p["episode_end"], p["source"], p["group"]) == (1, 1, 3, "WEB-DL", "RARBG")
    assert parse("Show S01E01E02.mkv")["episode_end"] == 2
    assert parse("[SubsPlease] Solo Leveling - 05 (1080p) [ABC123].mkv")["group"] == "SubsPlease"
    assert parse("Movie.Name.2023.1080p.BluRay.x265-YTS.mp4")["source"] == "BluRay"
    # single episodes, resolutions and years are never ranges; hashes are never groups
    assert parse("Show S01E05 1080p.mkv")["episode_end"] is None
    assert parse("Show - 05 [1080p].mkv")["group"] is None
    assert parse("[1080p] Show - 05.mkv")["group"] is None
    # anime " - 12" beats a number inside the title; 2x05; S02E01-02 ranges
    p = parse("[Erai-raws] Kaiju No. 8 - 12 [1080p][Multiple Subtitle].mkv")
    assert (p["episode"], p["title"], p["group"]) == (12, "Kaiju No 8", "Erai-raws")
    p = parse("Jujutsu Kaisen 2x05 720p.mkv")
    assert (p["season"], p["episode"], p["title"]) == (2, 5, "Jujutsu Kaisen")
    assert parse("Naruto 1920x1080 E05.mkv")["title"] == "Naruto" and parse("Naruto 1920x1080 E05.mkv")["season"] is None
    assert parse("The.Office.US.S02E01-02.720p.mkv")["episode_end"] == 2
    assert parse("Frieren - 28 END [1080p].mkv")["episode"] == 28
    assert parse("Show S01E01 - 1080p.mkv")["episode_end"] is None


def test_movie_aware_render_and_ranges():
    from renamer.extract import new_filename, render
    assert render("{title} S{season}E{episode} [{quality}]", "Movie.Name.2023.1080p.mkv") == "Movie Name [1080p]"
    assert render("[S{season}-E{episode}] {title}", "Movie 2020.mkv") == "Movie"
    assert render("{title} Season {season} Episode {episode}", "Movie 2020.mkv") == "Movie"
    assert render("{title} - S{season}E{episode} - {quality}", "Movie 2020 720p.mkv") == "Movie - 720p"
    assert render("[SSeason] [EPEpisode] {title}", "Movie 2020.mkv") == "Movie"
    # season pack keeps the season; episode without season defaults to S01; ranges render 01-03
    assert render("{title} S{season}E{episode}", "Show S02 Complete 720p.mkv") == "Show S02"
    assert render("{title} S{season}E{episode}", "Show - 07.mkv") == "Show S01E07"
    assert new_filename("{title} S{season}E{episode}", "Show.S01E01-E03.mkv") == "Show S01E01-03.mkv"
    assert new_filename("{title} {size}", "Show.mkv", size="1.2 GB") == "Show 1.2 GB.mkv"
    assert new_filename("{title} [{source}] {group}", "Movie.2023.WEBRip.x264-GRP.mp4", to_mkv=True) == \
        "Movie [WEBRip] GRP.mkv"


def test_clean_tags_and_word_rules():
    from renamer.extract import apply_words, clean_tags, new_filename, parse_word_rules, prepare_source
    assert clean_tags("www.1TamilMV.com - Leo (2023) 720p @Anime_Hindi") == "Leo (2023) 720p"
    assert clean_tags("Show [@MyChan] t.me/xyz https://a.b/c S01E01") == "Show S01E01"
    assert clean_tags("Show TamilBlasters.net S01E02") == "Show S01E02"
    # dotted release names and TV tags are never mistaken for websites
    assert clean_tags("Show.S01E01.720p.WEB-DL.x264") == "Show.S01E01.720p.WEB-DL.x264"
    rules = parse_word_rules("HQ\n[ESub] => ESubs\nTamil Dubbed | Tamil\n\n")
    assert rules == [["HQ", ""], ["[ESub]", "ESubs"], ["Tamil Dubbed", "Tamil"]]
    assert apply_words("Show HQ [ESub] HQfoo tamil dubbed", rules).split() == ["Show", "ESubs", "HQfoo", "Tamil"]
    assert len(parse_word_rules("\n".join(f"w{i}" for i in range(50)))) == 30
    # clean + rules run on the source name only – template text (your own @tag) survives
    name = "www.Site.com - Show S01E02 HQ 720p @Spam.mkv"
    assert prepare_source(name, True, rules) == "Show S01E02 720p.mkv"
    assert new_filename("{title} E{episode} @MyChannel", name, clean=True, words=rules) == "Show E02 @MyChannel.mkv"


def test_manual_filename_keeps_extension():
    from renamer.extract import manual_filename
    assert manual_filename("My Show S01E01", "a.mp4", to_mkv=True) == "My Show S01E01.mkv"
    assert manual_filename("My.Show.S01E01", "a.mkv") == "My.Show.S01E01.mkv"
    assert manual_filename("x.mp4", "a.mkv") == "x.mp4"
    assert manual_filename("Dr. Stone", "a.mkv") == "Dr. Stone.mkv"
    assert manual_filename("a/b:c?", "z.srt") == "a b c.srt"
    assert manual_filename("   ", "Orig.Name.mkv") == "Orig.Name.mkv"


# ───────────────────────── caption ─────────────────────────
def test_caption_placeholders_escape_and_keep_unknown():
    from renamer.engine import build_caption
    cap = build_caption("{title} | E{episode} | {quality} | {size} | {original} | {nope}",
                        "Show <x> S01E02-E03 720p.mkv", 2048, 65, "old&name.mp4")
    assert cap == "Show &lt;x&gt; | E02-03 | 720p | 2.00 KB | old&amp;name.mp4 | {nope}"
    assert build_caption("", "A<b>.mkv", 1, 0) == "<code>A&lt;b&gt;.mkv</code>"
    assert build_caption("{broken", "A.mkv", 1, 0) == "{broken"     # bad template → shown as typed


# ───────────────────────── panel & settings ─────────────────────────
def test_panel_shows_mode_clean_words_and_queue():
    from renamer import engine, handlers, store
    run(store.update(5, template="{title}", clean=True, words=[["HQ", ""]]))
    text, kb = run(handlers.panel_view(5))
    assert "🤖 Auto" in text and "Clean tags:</b> ✅" in text and "Word rules:</b> 1" in text
    assert {"rn:mode", "rn:clean", "rn:words", "rn:hist"} <= set(_cbs(kb)) and "rn:cq" not in _cbs(kb)
    engine._pending[5] = 2
    text, kb = run(handlers.panel_view(5))
    assert "2 file(s) in progress" in text and "rn:cq" in _cbs(kb)


def test_mode_clean_preset_and_cancel_queue_callbacks():
    from renamer import engine, handlers, store
    q = FakeQuery("rn:mode")
    run(handlers.rename_cb(FakeClient(), q))
    s = run(store.get(5))
    assert s["mode"] == "manual" and s["auto"] is True
    assert "Manual" in q.message.edits[-1] and "Pause" in str(run(handlers.panel_view(5))[1])
    run(handlers.rename_cb(FakeClient(), FakeQuery("rn:clean")))
    assert run(store.get(5))["clean"] is True
    run(handlers.rename_cb(FakeClient(), FakeQuery("rn:tpl")))
    text, kb = handlers.template_prompt()
    assert {"rn:pre:anime", "rn:pre:series", "rn:pre:movie", "rn:pre:clean"} <= set(_cbs(kb))
    run(handlers.rename_cb(FakeClient(), FakeQuery("rn:pre:movie")))
    assert run(store.get(5))["template"] == store.PRESETS["movie"][1] and 5 not in handlers._state
    run(store.update(5, clean=False))
    run(handlers.rename_cb(FakeClient(), FakeQuery("rn:pre:clean")))
    s = run(store.get(5))
    assert s["template"] == "{filename}" and s["clean"] is True
    job = engine.Job(uid=5, message=file_msg())
    engine._jobs[job.id] = job
    q = FakeQuery("rn:cq")
    run(handlers.rename_cb(FakeClient(), q))
    assert job.cancelled and "Cancelled 1" in q.answers[-1][0]


def test_word_rules_input_flow():
    from renamer import handlers, store
    run(handlers.rename_cb(FakeClient(), FakeQuery("rn:wset")))
    assert handlers._state[5]["kind"] == "words"
    m = FakeMsg("HQ\n[ESub] => ESubs")
    with pytest.raises(StopPropagation):
        run(handlers.rename_input(FakeClient(), m))
    assert run(store.get(5))["words"] == [["HQ", ""], ["[ESub]", "ESubs"]]
    assert "2 word rule(s) saved" in m.replies[-1] and "ESubs" in m.replies[-1]
    run(handlers.rename_cb(FakeClient(), FakeQuery("rn:wclr")))
    assert not run(store.get(5)).get("words")


def test_testrename_uses_clean_and_rules():
    from renamer import handlers, store
    run(store.update(5, template="{title} E{episode}", clean=True, words=[["HQ", ""]]))
    m = FakeMsg("/testrename www.Site.com - Show S01E04 HQ.mp4")
    run(handlers.testrename_cmd(FakeClient(), m))
    assert "Show E04.mkv" in m.replies[-1] and "cleaned" in m.replies[-1]


# ───────────────────────── manual mode ─────────────────────────
def _manual_setup(template=None):
    from renamer import store
    fields = {"mode": "manual"}
    if template:
        fields["template"] = template
    run(store.update(5, **fields))


def test_manual_mode_filter_needs_no_template():
    from renamer import handlers, store
    msg = file_msg()
    assert not _filter(handlers.auto_filter, msg)
    _manual_setup()
    assert _filter(handlers.auto_filter, msg)
    run(store.update(5, auto=False))
    assert not _filter(handlers.auto_filter, msg)           # paused applies to manual mode too


def test_manual_mode_prompt_reply_and_buttons(monkeypatch):
    from renamer import engine, handlers
    _manual_setup("{title} S{season}E{episode}")
    got = []

    async def fake_submit(client, message, name="", send_as="", user=None):
        got.append((message, name, send_as, user.id if user else None))
        return len(got)
    monkeypatch.setattr(engine, "submit", fake_submit)

    f1 = file_msg("Show.S01E02.720p.mkv", unique="m1")
    run(handlers.incoming_file(FakeClient(), f1))
    assert not got and "Send the new name" in f1.replies[-1] and "Show S01E02.mkv" in f1.replies[-1]
    entry = handlers._prompts[5][f1.id]
    kb = handlers._prompt_kb(f1.id, entry)
    assert {f"rnm:use:{f1.id}", f"rnm:keep:{f1.id}", f"rnm:x:{f1.id}", f"rnm:t:{f1.id}:video"} <= set(_cbs(kb))

    # the only pending prompt → plain text is taken as the name
    name_msg = FakeMsg("My Custom Name")
    assert _filter(handlers.input_filter, name_msg)
    with pytest.raises(StopPropagation):
        run(handlers.rename_input(FakeClient(), name_msg))
    assert got[-1][:3] == (f1, "My Custom Name", "") and entry["prompt"].deleted and not handlers._prompts

    # two pending: plain text is ambiguous (not captured); a reply to the prompt or the file picks one
    f2 = file_msg("A.S01E01.mkv", unique="m2")
    f3 = file_msg("B.S01E09.mkv", unique="m3")
    run(handlers.incoming_file(FakeClient(), f2))
    run(handlers.incoming_file(FakeClient(), f3))
    assert not _filter(handlers.input_filter, FakeMsg("Ambiguous"))
    reply = FakeMsg("Name For B")
    reply.reply_to_message = handlers._prompts[5][f3.id]["prompt"]
    with pytest.raises(StopPropagation):
        run(handlers.rename_input(FakeClient(), reply))
    assert got[-1][:2] == (f3, "Name For B")

    # send-as toggle, then 💡 suggestion
    q = FakeQuery(f"rnm:t:{f2.id}:video")
    run(handlers.manual_prompt_cb(FakeClient(), q))
    assert handlers._prompts[5][f2.id]["send_as"] == "video" and "Video" in q.message.edits[-1]
    run(handlers.manual_prompt_cb(FakeClient(), FakeQuery(f"rnm:use:{f2.id}")))
    assert got[-1][:3] == (f2, "A S01E01.mkv", "video") and not handlers._prompts


def test_manual_mode_keep_skip_links_and_expiry(monkeypatch):
    from renamer import engine, handlers
    _manual_setup()
    got = []

    async def fake_submit(client, message, name="", send_as="", user=None):
        got.append((message, name))
        return 3
    monkeypatch.setattr(engine, "submit", fake_submit)
    f1 = file_msg("Plain Name.mkv", unique="k1")
    run(handlers.incoming_file(FakeClient(), f1))
    assert "Suggestion" not in f1.replies[-1]                  # no template / rules → nothing to suggest
    assert not _filter(handlers.input_filter, FakeMsg("https://t.me/c/1/2"))   # saver links pass through
    assert not _filter(handlers.input_filter, FakeMsg("/start"))
    run(handlers.manual_prompt_cb(FakeClient(), FakeQuery(f"rnm:keep:{f1.id}")))
    assert got[-1] == (f1, "Plain Name.mkv") and "#3" in f1.replies[-1]
    f2 = file_msg("Other.mkv", unique="k2")
    run(handlers.incoming_file(FakeClient(), f2))
    run(handlers.manual_prompt_cb(FakeClient(), FakeQuery(f"rnm:x:{f2.id}")))
    assert len(got) == 1 and not handlers._live_prompts(5)
    q = FakeQuery(f"rnm:use:{f2.id}")
    run(handlers.manual_prompt_cb(FakeClient(), q))
    assert "expired" in q.answers[-1][0] and q.message.deleted
    # expiry + /cancel + watchdog prune
    f3 = file_msg("Third.mkv", unique="k3")
    run(handlers.incoming_file(FakeClient(), f3))
    assert "rename prompts" in handlers.clear_user(5)
    run(handlers.incoming_file(FakeClient(), file_msg("Fourth.mkv", unique="k4")))
    for e in handlers._prompts[5].values():
        e["ts"] -= 10 ** 6
    handlers.prune_prompts()
    assert 5 not in handlers._prompts


def test_manual_prompt_limit(monkeypatch):
    from renamer import handlers
    _manual_setup()
    monkeypatch.setattr(handlers, "MAX_PROMPTS", 2)
    for i in range(2):
        run(handlers.incoming_file(FakeClient(), file_msg(f"F{i}.mkv", unique=f"l{i}")))
    extra = file_msg("F9.mkv", unique="l9")
    run(handlers.incoming_file(FakeClient(), extra))
    assert "already waiting" in extra.replies[-1] and len(handlers._prompts[5]) == 2


# ───────────────────────── engine ─────────────────────────
def test_named_job_uses_exact_name_history_and_send_as(monkeypatch):
    from renamer import engine, handlers, store
    c = PipelineClient()
    job = engine.Job(uid=5, message=file_msg("Old.Name.mp4"), name="Brand New", send_as="video")
    monkeypatch.setattr(engine, "remux", lambda *a, **k: _async(""))
    monkeypatch.setattr(engine, "probe_media", lambda p: _async((42, 1280, 720)))
    run(engine.process(c, job))                               # no template needed for a named job
    kind, name, k = c.uploads[-1]
    assert (kind, name) == ("video", "Brand New.mp4")      # MKV step skipped without ffmpeg → keeps .mp4
    assert (k["duration"], k["width"], k["height"]) == (42, 1280, 720)
    rows = run(store.recent(5))
    assert rows[0]["name"] == "Brand New.mp4" and rows[0]["old"] == "Old.Name.mp4"
    text, _ = run(handlers.history_view(5))
    assert "Brand New.mp4" in text and "Old.Name.mp4" in text


def test_template_job_applies_clean_rules_and_frame_thumbnail(monkeypatch):
    from renamer import engine, store
    run(store.update(5, template="{title} S{season}E{episode}", clean=True, words=[["HQ", ""]], mkv=False))
    frames = []

    async def fake_frame(path, dst, duration=0):
        frames.append(path)
        return ""
    monkeypatch.setattr(engine, "frame_thumbnail", fake_frame)
    c = PipelineClient()
    run(engine.process(c, engine.Job(uid=5, message=file_msg("www.X.com - Show S01E03 HQ @Spam.mkv"))))
    assert c.uploads[-1][1] == "Show S01E03.mkv" and frames      # no custom / embedded thumb → frame grab
    frames.clear()
    run(engine.process(c, engine.Job(uid=5, message=file_msg("Song.mp3", unique="s1"))))
    assert not frames                                            # never for non-video files


def test_rename_command_routes_through_engine(monkeypatch):
    from core import tools
    from renamer import engine
    got = []

    async def fake_submit(client, message, name="", send_as="", user=None):
        got.append((message, name, user.id))
        return 2
    monkeypatch.setattr(engine, "submit", fake_submit)
    target = file_msg("x.mkv", uid=123456)                    # a file the bot itself sent
    m = FakeMsg("/rename New Title")
    m.reply_to_message = target
    run(tools.rename_cmd(FakeClient(), m))
    assert got == [(target, "New Title", 5)] and "#2" in m.replies[-1]


def test_rename_command_non_engine_media_escapes_caption(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from core import tools
    monkeypatch.setattr(tools, "TMP", str(tmp_path))
    photo_msg = FakeMsg(text=None)
    photo_msg.document = photo_msg.video = photo_msg.audio = None
    photo_msg.voice = SimpleNamespace(file_name=None, file_size=10, file_unique_id="v")

    class C(FakeClient):
        async def download_media(self, message, file_name=None, progress=None, **k):
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            open(file_name, "wb").write(b"x")
            return file_name
    c = C()
    m = FakeMsg("/rename a<b>")
    m.reply_to_message = photo_msg
    run(tools.rename_cmd(c, m))
    cap = c.called("send_document")[-1][2]["caption"]
    assert cap == "<code>a&lt;b&gt;</code>"


def test_frame_thumbnail_without_ffmpeg(monkeypatch, tmp_path):
    from renamer import engine
    monkeypatch.setattr(engine.shutil, "which", lambda n: None)
    assert run(engine.frame_thumbnail("x.mkv", str(tmp_path / "t.jpg"))) == ""
    assert run(engine.probe_media("x.mkv")) == (0, 0, 0)


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg not installed")
def test_frame_thumbnail_and_probe_real_ffmpeg(tmp_path):
    from renamer import engine
    src = str(tmp_path / "in.mp4")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=10",
                    "-t", "3", "-pix_fmt", "yuv420p", src], check=True)
    assert run(engine.probe_media(src)) == (3, 640, 360)
    out = run(engine.frame_thumbnail(src, str(tmp_path / "f.jpg"), 3))
    assert out and os.path.getsize(out) > 0


def test_rename_ui_html_is_valid():
    from renamer import handlers, store
    from tests.test_phase5 import _bot_api_html_problems
    run(store.update(5, template="{title} <b>", words=[["<x>", "&y"]], mode="manual"))
    entry = {"msg": file_msg("<a>&.mkv"), "sugg": "<s>", "send_as": "video"}
    texts = {"panel": run(handlers.panel_view(5))[0], "words": run(handlers.words_view(5))[0],
             "history": run(handlers.history_view(5))[0], "tpl": handlers.template_prompt()[0],
             "words_help": handlers.WORDS_HELP, "tutorial": handlers.TUTORIAL,
             "prompt": handlers._prompt_text(entry), "thumb": run(handlers.thumb_view(5))[0]}
    bad = {k: _bot_api_html_problems(v) for k, v in texts.items() if _bot_api_html_problems(v)}
    assert not bad, bad

"""
Stage F subsystem & database-integrity regression suite:
  1. Revoked dynamic admin immediately loses `/admin` web dashboard session access (`keep_alive._authorised`)
  2. `core.bg.track` + `keep_alive._live` work cleanly with weakref-backed `scheduler.mode_of`
  3. FileStore `bot_settings` enforces owner check before decrypting token, sets `no_updates=True`,
     always stops `temp_client` in `finally`, and HTML-escapes Telegram RPC errors
  4. Saver `build_caption` HTML-escapes `{filename}` & `apply_word_filters` handles literal backslashes
  5. Saver `save()` rejects malformed `/c/`, `/b/`, and inverted ranges
  6. Renamer `engine.py` sanitizes temp file extensions & HTML-escapes download errors
  7. Codebase-wide AST check: zero unescaped `str(e)` passed inside HTML-parsed bot send/edit calls
  8. `core.db_doctor` read-only dry-run + non-destructive duplicate merge (preserving Lifetime,
     sessions, thumbnails, captions, word filters, and max counters) + `/backup check` & `bkr:dedupe_ok`
"""
import ast
import asyncio
from pathlib import Path
import time
from types import SimpleNamespace
import weakref

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, no_botapi, reset_db, run


@pytest.fixture(autouse=True)
def _clean_env():
    run(reset_db())
    no_botapi()
    yield


# ───────────────────── 1. Web dashboard session revocation ─────────────────────
def test_keep_alive_revokes_demoted_admin_session_immediately(monkeypatch):
    import config
    import keep_alive

    demoted_uid = 889900
    keep_alive._sessions.clear()
    keep_alive._fails.clear()
    monkeypatch.setattr(config, "ADMINS", list(config.OWNERS) + [demoted_uid])

    tok = "sess_demoted_1"
    keep_alive._sessions[keep_alive._h(tok)] = (time.time() + 3600, demoted_uid)
    owner_tok = "sess_passphrase_0"
    keep_alive._sessions[keep_alive._h(owner_tok)] = (time.time() + 3600, 0)

    req_admin = SimpleNamespace(headers={"X-Admin-Token": tok}, remote="127.0.0.1")
    req_pass = SimpleNamespace(headers={"X-Admin-Token": owner_tok}, remote="127.0.0.1")

    assert keep_alive._authorised(req_admin) is True
    assert keep_alive._authorised(req_pass) is True

    # Simulate `/deladmin 889900` removing the user from `config.ADMINS`
    monkeypatch.setattr(config, "ADMINS", list(config.OWNERS))

    assert keep_alive._authorised(req_admin) is False
    assert keep_alive._h(tok) not in keep_alive._sessions  # evicted immediately
    # Passphrase session (`uid == 0`) remains valid
    assert keep_alive._authorised(req_pass) is True
    keep_alive._sessions.clear()
    keep_alive._fails.clear()


# ───────────────────── 2. core.bg.track + keep_alive._live ─────────────────────
def test_bg_track_and_keep_alive_live_queue_use_mode_of():
    from core import bg
    import keep_alive
    from VideoEncoder.utils import scheduler

    async def _exercise():
        t = asyncio.create_task(asyncio.sleep(0.01))
        tracked = bg.track(t, name="test-tracked")
        assert tracked is t
        await tracked

    run(_exercise())

    class _TaskMsg:
        def __init__(self):
            self.from_user = SimpleNamespace(id=42)
            self.video = SimpleNamespace(file_name="clip.mkv")
            self.document = None

    m = _TaskMsg()
    scheduler.data.append(m)
    scheduler.MODES[id(m)] = "hevc"
    scheduler._REFS[id(m)] = weakref.ref(m)
    try:
        rows = keep_alive._live()
        assert any(r["user"] == 42 and r["mode"] == "hevc" and r["file"] == "clip.mkv" for r in rows)
    finally:
        scheduler.data.clear()
        scheduler.MODES.clear()
        scheduler._REFS.clear()


# ───────────────────── 3. FileStore bot_settings security & temp_client cleanup ─────────────────────
def test_filestore_bot_settings_owner_check_and_temp_client_finally(monkeypatch):
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins import bot_settings
    from filestore.utils.security import encrypt_token

    mdb = bot_settings.main_db
    owner_id = 5001
    attacker_id = 9999
    bot_id = 77001
    run(mdb.bots.insert_one({
        "_id": bot_id,
        "owner_id": owner_id,
        "bot_token_encrypted": encrypt_token("123456:FAKE_SECRET_TOKEN"),
        "bot_username": "demo_clone_bot",
        "log_channel_id": -100111222,
        "is_active": True,
    }))

    created_clients = []

    class _DummyTempClient:
        def __init__(self, name, **kwargs):
            self.name = name
            self.kwargs = kwargs
            self.started = False
            self.stopped = False
            self.is_connected = False
            created_clients.append(self)

        async def start(self):
            self.started = True
            self.is_connected = True

        async def stop(self, block=False):
            self.stopped = True
            self.is_connected = False

        async def send_message(self, chat_id, text):
            raise RuntimeError("<ChatAdminRequired & Bad>")

        async def get_chat(self, chat_id):
            raise RuntimeError("<ChannelPrivate & Forbidden>")

    monkeypatch.setattr(bot_settings, "Client", _DummyTempClient)
    c = FakeClient()
    state = {"data": {"bot_id": bot_id}}

    # 3a) Non-owner with forged/stale state is rejected BEFORE decrypting or starting temp_client
    msg_att = FakeMsg("-1001234567890", uid=attacker_id, client=c)
    run(bot_settings.handle_log_channel_input(c, msg_att, state))
    assert created_clients == []
    assert any("Access denied" in str(r) for r in msg_att.replies)

    msg_att_fsub = FakeMsg("-1001234567890", uid=attacker_id, client=c)
    run(bot_settings.handle_fsub_channel_input(c, msg_att_fsub, state))
    assert created_clients == []
    assert any("Access denied" in str(r) for r in msg_att_fsub.replies)

    # 3b) Legitimate owner hits RPC error -> `no_updates=True`, `stop()` runs in `finally`, error escaped
    msg_own = FakeMsg("-1001234567890", uid=owner_id, client=c)
    run(bot_settings.handle_log_channel_input(c, msg_own, state))
    assert len(created_clients) == 1
    assert created_clients[0].kwargs.get("no_updates") is True
    assert created_clients[0].kwargs.get("in_memory") is True
    assert created_clients[0].stopped is True
    log_replies = " ".join(str(r) for r in msg_own.replies)
    assert "&lt;ChatAdminRequired &amp; Bad&gt;" in log_replies
    assert "<ChatAdminRequired" not in log_replies

    # 3c) Force-sub channel check also enforces `no_updates=True`, `stop()`, and HTML escaping
    msg_fsub = FakeMsg("-1009876543210", uid=owner_id, client=c)
    run(bot_settings.handle_fsub_channel_input(c, msg_fsub, state))
    assert len(created_clients) == 2
    assert created_clients[1].kwargs.get("no_updates") is True
    assert created_clients[1].stopped is True
    fsub_edits = " ".join(str(e) for s in msg_fsub.sent for e in s.edits)
    assert "&lt;ChannelPrivate &amp; Forbidden&gt;" in fsub_edits


# ───────────────────── 4 & 5. Saver caption escaping, regex safety & link bounds ─────────────────────
def test_saver_caption_escaping_word_filter_backslashes_and_link_validation(monkeypatch):
    from database.db import db as saver_db
    from saver import start as saver_start

    uid = 6100
    run(saver_db.add_user(uid, "SaverTester"))
    run(saver_db.set_caption(uid, "🎬 <b>{filename}</b> ({size})"))
    run(saver_db.set_replace_words(uid, {"MKV": r"RAW\1\g<0>\\"}))

    cap = run(saver_start.build_caption(uid, "Episode <01> & Bonus.MKV", 1048576, ""))
    assert "&lt;01&gt; &amp; Bonus." in cap
    assert "<01>" not in cap
    assert r"RAW\1\g<0>\\" in cap  # literal backslashes preserved without re.error

    # Malformed & inverted links rejected cleanly without uncaught ValueError/IndexError
    c = FakeClient()
    for bad_link in (
        "https://t.me/c/notanint/10",
        "https://t.me/c/123456",
        "https://t.me/somechannel/not_a_number",
        "https://t.me/somechannel/50-10",
        "https://t.me/somechannel/0",
    ):
        m = FakeMsg(bad_link, uid=uid, client=c)
        run(saver_start.save(c, m))
        assert m.replies or c.calls, f"Expected safe rejection reply for {bad_link}"
        assert saver_start.batch_temp.IS_BATCH.get(uid, True) is True



# ───────────────────── 6. Renamer extension sanitization & HTML error escaping ─────────────────────
def test_renamer_sanitizes_extension_and_escapes_download_errors(monkeypatch, tmp_path):
    from core import fastdl
    from renamer import engine

    uid = 7200
    monkeypatch.setattr(engine, "WORK_DIR", str(tmp_path))

    seen_dest = []

    async def _boom_download(client, msg, file_name=None, progress=None):
        seen_dest.append(file_name)
        raise RuntimeError("<BadRequest & Timeout>")

    monkeypatch.setattr(fastdl, "download", _boom_download)

    c = FakeClient()
    m = FakeMsg("", uid=uid, client=c)
    m.document = SimpleNamespace(
        file_name="../../evil.tar.gz/../../weird..name$bad",
        file_size=1024,
        thumbs=None,
    )
    m.media = "document"
    job = engine.Job(id=1, uid=uid, message=m, name="Clean_Name.mkv", user=m.from_user)
    run(engine.process(c, job))
    assert len(seen_dest) == 1
    # Must stay strictly inside WORK_DIR/<job_workdir>/src without path separators in extension
    dest_path = Path(seen_dest[0]).resolve()
    assert tmp_path.resolve() in dest_path.parents
    assert dest_path.name == "src"
    edits = " ".join(str(e) for s in m.sent for e in s.edits)
    assert "&lt;BadRequest &amp; Timeout&gt;" in edits
    assert "<BadRequest" not in edits


# ───────────────────── 7. Whole-codebase AST check: zero unescaped `str(e)` in HTML calls ─────────────────────
def test_zero_unescaped_str_e_in_html_messages():
    root = Path(__file__).resolve().parents[1]
    send_methods = {"reply_text", "reply", "edit_text", "edit", "send_message"}
    offenders = []
    for p in sorted(root.rglob("*.py")):
        if "tests" in p.parts or "__pycache__" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in send_methods or not node.args:
                continue
            # Check text argument (arg 0 for reply_text/edit_text, arg 1 for client.send_message)
            text_args = node.args[:2] if node.func.attr == "send_message" else node.args[:1]
            for arg in text_args:
                src = ast.unparse(arg)
                if "str(e)" in src and "html.escape" not in src and "_esc(" not in src:
                    offenders.append(f"{p.relative_to(root)}:{node.lineno}: {src}")
    assert offenders == [], "Unescaped str(e) in HTML message send/edit call:\n" + "\n".join(offenders)


# ───────────────────── 8. Non-destructive DB doctor & `/backup check` ─────────────────────
def test_db_doctor_merges_duplicates_preserving_entitlements_and_secrets():
    import config
    from core import backup, db_doctor
    from database.db import db as saver_db
    from VideoEncoder.utils.database.access_db import db as enc_db

    uid = 90909
    # Insert 3 legacy duplicate rows in `saver.users` with split valuable state
    run(saver_db.col.insert_many([
        {
            "id": uid,
            "name": "First",
            "is_premium": False,
            "premium_expiry": None,
            "session": "ENCRYPTED_SESSION_KEEP_ME",
            "total_saves": 5,
            "daily_usage": 1,
            "delete_words": ["@ads"],
            "replace_words": {"foo": "bar"},
        },
        {
            "id": uid,
            "name": "Second",
            "is_premium": True,
            "premium_expiry": None,  # Lifetime entitlement!
            "session": None,
            "thumbnail": "thumb_file_id_123",
            "total_saves": 42,
            "daily_usage": 3,
            "delete_words": ["@spam", "@ads"],
            "replace_words": {"old": "new"},
        },
        {
            "id": uid,
            "name": "Third",
            "is_premium": True,
            "premium_expiry": "2027-01-01",
            "caption": "Custom {filename}",
            "dump_chat": -100555666,
            "total_saves": 12,
        },
    ]))

    # Insert 2 legacy duplicate rows in `encoder.users` (one default, one customized)
    u_default = enc_db.new_user(uid)
    u_custom = dict(enc_db.new_user(uid), crf=18, codec="libx265", encodes=19)
    run(enc_db.col.insert_many([u_default, u_custom]))

    # Dry-run MUST NOT delete or mutate documents
    dry = run(db_doctor.inspect_and_deduplicate(dry_run=True))
    assert dry["dry_run"] is True
    assert dry["saver_duplicate_users"] == 1
    assert dry["saver_redundant_docs"] == 2
    assert dry["encoder_duplicate_users"] == 1
    assert dry["encoder_redundant_docs"] == 1
    assert dry["total_redundant_docs"] == 3
    assert run(saver_db.col.count_documents({"id": uid})) == 3
    assert run(enc_db.col.count_documents({"id": uid})) == 2

    # Owner `/backup check` renders rich report + `bkr:dedupe_ok` button
    owner_id = config.OWNERS[0]
    c = FakeClient()
    m_chk = FakeMsg("/backup check", uid=owner_id, client=c)
    m_chk.command = ["backup", "check"]
    run(backup.backup_cmd(c, m_chk))
    assert m_chk.sent

    # Owner clicks `bkr:dedupe_ok` -> merges non-destructively
    q = FakeQuery("bkr:dedupe_ok", uid=owner_id, message=m_chk.sent[-1])
    run(backup.backup_restore_cb(c, q))

    assert run(saver_db.col.count_documents({"id": uid})) == 1
    assert run(enc_db.col.count_documents({"id": uid})) == 1

    merged_saver = run(saver_db.col.find_one({"id": uid}))
    assert merged_saver["session"] == "ENCRYPTED_SESSION_KEEP_ME"
    assert merged_saver["is_premium"] is True
    assert merged_saver["premium_expiry"] is None  # Lifetime beat timed date!
    assert merged_saver["thumbnail"] == "thumb_file_id_123"
    assert merged_saver["caption"] == "Custom {filename}"
    assert merged_saver["dump_chat"] == -100555666
    assert merged_saver["total_saves"] == 42
    assert set(merged_saver["delete_words"]) == {"@ads", "@spam"}
    assert merged_saver["replace_words"] == {"foo": "bar", "old": "new"}

    merged_enc = run(enc_db.col.find_one({"id": uid}))
    assert merged_enc["crf"] == 18
    assert merged_enc["codec"] == "libx265"
    assert merged_enc["encodes"] == 19


# ───────────────────── 9. VideoEncoder hw.has_filter supports FFmpeg 4..9 ─────────────────────
def test_hw_has_filter_parses_both_legacy_and_modern_ffmpeg_columns(monkeypatch):
    from VideoEncoder.utils import hw

    sample_out = (
        b"Filters:\n"
        b"  T.. = Timeline support\n"
        b"  ------\n"
        b" ... scale             V->V       Scale the input video size (FFmpeg 4-6 3-flag format).\n"
        b" .. zscale            V->V       Apply resizing, colorspace and bit depth (FFmpeg 7-9 2-flag format).\n"
        b" TS hstack            N->V       Stack video inputs horizontally.\n"
    )
    hw._FILTERS.clear()
    monkeypatch.setattr(hw.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=sample_out))
    assert hw.has_filter("scale") is True
    assert hw.has_filter("zscale") is True
    assert hw.has_filter("hstack") is True
    assert hw.has_filter("T..") is False
    hw._FILTERS.clear()


def test_saver_premium_full_range_and_free_batch_limit(monkeypatch):
    from database.db import db as saver_db
    from saver import start as saver_start

    async def fast_sleep(_delay):
        return None

    async def no_traffic(_user_id):
        return None

    monkeypatch.setattr(saver_start.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(saver_db, "add_traffic", no_traffic)
    # An old deployed setting must no longer silently truncate Premium requests.
    monkeypatch.setenv("MAX_SAVER_BATCH", "25")
    premium_uid, free_uid = 6110, 6111
    run(saver_db.add_user(premium_uid, "Premium"))
    run(saver_db.add_premium(premium_uid, None))
    run(saver_db.add_user(free_uid, "Free"))

    client = FakeClient()
    premium = FakeMsg("https://t.me/somechannel/54550-54799", uid=premium_uid, client=client)
    run(saver_start.save(client, premium))
    ids = [call[2]["message_id"] for call in client.called("copy_message")]
    assert ids == list(range(54550, 54800))
    assert not any("capped" in str(reply).lower() for reply in premium.replies)
    assert saver_start.batch_temp.IS_BATCH[premium_uid] is True

    client.calls.clear()
    free = FakeMsg("https://t.me/somechannel/54550-54799", uid=free_uid, client=client)
    run(saver_start.save(client, free))
    ids = [call[2]["message_id"] for call in client.called("copy_message")]
    assert ids == list(range(54550, 54555))
    assert any("Free users" in str(reply) for reply in free.replies)
    assert saver_start.batch_temp.IS_BATCH[free_uid] is True


def test_saver_premium_long_batch_can_be_cancelled(monkeypatch):
    from database.db import db as saver_db
    from saver import start as saver_start

    async def fast_sleep(_delay):
        return None

    async def no_traffic(_user_id):
        return None

    monkeypatch.setattr(saver_start.asyncio, "sleep", fast_sleep)
    monkeypatch.setattr(saver_db, "add_traffic", no_traffic)
    uid = 6112
    run(saver_db.add_user(uid, "Premium"))
    run(saver_db.add_premium(uid, None))
    client = FakeClient()
    original_copy = client.copy_message

    async def cancel_after_210(*args, **kwargs):
        result = await original_copy(*args, **kwargs)
        if len(client.called("copy_message")) == 210:
            saver_start.batch_temp.IS_BATCH[uid] = True
        return result

    monkeypatch.setattr(client, "copy_message", cancel_after_210, raising=False)
    message = FakeMsg("https://t.me/somechannel/1-500", uid=uid, client=client)
    run(saver_start.save(client, message))
    assert len(client.called("copy_message")) == 210
    assert saver_start.batch_temp.IS_BATCH[uid] is True

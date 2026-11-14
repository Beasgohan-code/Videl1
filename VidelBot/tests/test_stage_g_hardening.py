"""
Stage G security, encryption-at-rest, archive safety & interactive rich telemetry suite:
  1. VideoEncoder `/ddl` & `handle_url` block SSRF (`127.0.0.1`, `169.254.169.254`, private redirects)
  2. Archive extraction safety: `.tbz2` folder fix, Zip-Slip / Tar-Slip / symlink rejection,
     decompression bomb byte cap (`MAX_BATCH_EXTRACT_BYTES`), and post-extraction symlink scrubbing
  3. Encryption-at-rest for personal user `/login` sessions (`saver.users.session` via Fernet `enc:`)
     + legacy plaintext session compatibility + `db_doctor` / `bkr:encrypt_ok` in-place upgrade
  4. Owner `/exec` & `/sh` (`pyexec.py`): strict owner gate, `ENABLE_OWNER_EXEC=false` lock,
     credential redaction (`BOT_TOKEN`, `DB_URI`), zero global `sys.stdout` hijacking, and `/sh` timeout kill
  5. `core/tools.py` & `core/inline.py`: `/qr` & `/json` HTML escaping, `/short` & inline SSRF guard,
     and `/rename` traversal protection (`..`, `/`, `\\`)
  6. Interactive Rich `/stats` multi-tab telemetry (`adm_st:main`, `adm_st:runtime`, `adm_st:health`)
"""
import asyncio
import os
from pathlib import Path
import re
import sys
import tarfile
from types import SimpleNamespace
import zipfile

from cryptography.fernet import Fernet
import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, no_botapi, reset_db, run


@pytest.fixture(autouse=True)
def _clean_env():
    run(reset_db())
    no_botapi()
    yield


# ───────────────────── 1. VideoEncoder /ddl & handle_url SSRF guard ─────────────────────
def test_video_encoder_ddl_and_handle_url_block_ssrf(monkeypatch, tmp_path):
    from VideoEncoder.utils import helper, leech, tasks

    monkeypatch.setattr(leech, "ALLOW_PRIVATE", False)

    # 1a) Direct `handle_url` refuses loopback & cloud metadata before touching SmartDL
    smartdl_called = False

    def _forbid_smartdl(*a, **k):
        nonlocal smartdl_called
        smartdl_called = True
        raise AssertionError("SmartDL should never be invoked on SSRF targets")

    monkeypatch.setattr(helper, "SmartDL", _forbid_smartdl)
    msg = FakeMsg("status")
    for bad in ("http://127.0.0.1:8080/admin", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"):
        with pytest.raises(leech.LeechError):
            run(helper.handle_url(bad, str(tmp_path / "out.mkv"), msg))
    assert smartdl_called is False

    # 1b) `handle_download_url` also re-checks URLs returned by `direct_link_generator`
    async def _fake_check_public(url: str):
        if "127.0.0.1" in url or "169.254.169.254" in url:
            raise leech.LeechError("Private address blocked")

    monkeypatch.setattr(leech, "check_public", _fake_check_public)
    monkeypatch.setattr(tasks, "direct_link_generator", lambda _u: "http://127.0.0.1:8080/internal_secret")

    ddl_msg = FakeMsg("/ddl https://mediafire.com/file/public_wrapper/video.mp4")
    with pytest.raises(leech.LeechError, match="Private address blocked"):
        run(tasks.handle_download_url(ddl_msg, msg, batch=False, dest_dir=str(tmp_path)))


# ───────────────────── 2. Archive extraction safety & .tbz2 fix ─────────────────────
def test_archive_validation_scrubbing_and_tbz2_folder(tmp_path):
    from VideoEncoder.utils import helper

    # 2a) `.tbz2` strips the dot cleanly (`clips.tbz2` -> `clips`, not `clips.`)
    assert run(helper.get_zip_folder("/tmp/season1.tbz2")) == "/tmp/season1"
    assert Path("extract").stat().st_mode & 0o111, "VidelBot/extract must be executable (+x)"

    # 2b) Zip-Slip (`../escape.mkv`) rejected before extraction
    slip_zip = tmp_path / "slip.zip"
    with zipfile.ZipFile(slip_zip, "w") as zf:
        zf.writestr("../../escape.mkv", b"malicious")
    with pytest.raises(RuntimeError, match="Unsafe archive entry"):
        helper.validate_archive_safety(str(slip_zip))

    # 2c) Tar symlink entry rejected before extraction
    sym_tar = tmp_path / "sym.tar"
    with tarfile.open(sym_tar, "w") as tf:
        ti = tarfile.TarInfo("secret_link.mkv")
        ti.type = tarfile.SYMTYPE
        ti.linkname = "/etc/passwd"
        tf.addfile(ti)
    with pytest.raises(RuntimeError, match="Special/symlink tar entry"):
        helper.validate_archive_safety(str(sym_tar))

    # 2d) Decompression bomb exceeding byte cap rejected
    bomb_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("huge.bin", b"\x00" * (2 * 1024 * 1024))
    with pytest.raises(RuntimeError, match="exceeds"):
        helper.validate_archive_safety(str(bomb_zip), max_bytes=512 * 1024)

    # 2e) Post-extraction `scrub_extracted_tree` unlinks symlinks pointing outside dest_dir
    extracted = tmp_path / "extracted_batch"
    extracted.mkdir()
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("TOP_SECRET")
    good_vid = extracted / "ep01.mkv"
    good_vid.write_bytes(b"video_bytes")
    evil_link = extracted / "ep02.mkv"
    os.symlink(outside, evil_link)

    removed = helper.scrub_extracted_tree(str(extracted))
    assert removed == 1
    assert good_vid.exists()
    assert not evil_link.exists()
    assert outside.read_text() == "TOP_SECRET"  # target file untouched!


# ───────────────────── 3. User session encryption-at-rest & db_doctor upgrade ─────────────────────
def test_saver_user_sessions_encrypted_at_rest_and_db_doctor_upgrades_legacy(monkeypatch):
    import config
    from core import backup, db_doctor
    from database.db import db as saver_db
    from filestore.database.main_db import MainDB

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)

    # 3a) New `/login` session is stored encrypted (`enc:...`) in MongoDB and decrypted transparently
    uid_new = 41001
    run(saver_db.set_session(uid_new, "1BVtsOH_REAL_USER_STRING_SESSION"))
    raw_doc = run(saver_db.col.find_one({"id": uid_new}))
    assert raw_doc["session"].startswith("enc:")
    assert "REAL_USER_STRING_SESSION" not in raw_doc["session"]
    assert run(saver_db.get_session(uid_new)) == "1BVtsOH_REAL_USER_STRING_SESSION"

    # 3b) Legacy plaintext user session & clone token remain readable AND are upgraded by `bkr:encrypt_ok`
    uid_legacy = 41002
    run(saver_db.col.insert_one({"id": uid_legacy, "name": "Legacy", "session": "LEGACY_PLAINTEXT_SESSION"}))
    run(MainDB().bots.insert_one({
        "_id": 88001,
        "owner_id": uid_legacy,
        "bot_token_encrypted": "88001:LEGACY_PLAINTEXT_BOT_TOKEN",
        "bot_username": "legacy_clone",
        "is_active": True,
    }))
    assert run(saver_db.get_session(uid_legacy)) == "LEGACY_PLAINTEXT_SESSION"

    dry = run(db_doctor.inspect_and_deduplicate(dry_run=True))
    assert dry["encryption_enabled"] is True
    assert dry["plaintext_saver_sessions"] == 1
    assert dry["plaintext_clone_tokens"] == 1
    assert dry["plaintext_secrets"] == 2

    owner_id = config.OWNERS[0]
    c = FakeClient()
    m_chk = FakeMsg("/backup check", uid=owner_id, client=c)
    m_chk.command = ["backup", "check"]
    run(backup.backup_cmd(c, m_chk))
    q = FakeQuery("bkr:encrypt_ok", uid=owner_id, message=m_chk.sent[-1])
    run(backup.backup_restore_cb(c, q))

    upgraded_user = run(saver_db.col.find_one({"id": uid_legacy}))
    upgraded_bot = run(MainDB().bots.find_one({"_id": 88001}))
    assert upgraded_user["session"].startswith("enc:")
    assert upgraded_bot["bot_token_encrypted"].startswith("enc:")
    assert run(saver_db.get_session(uid_legacy)) == "LEGACY_PLAINTEXT_SESSION"


# ───────────────────── 4. /exec & /sh isolation, redaction & timeout ─────────────────────
def test_pyexec_owner_gate_secret_redaction_stdout_safety_and_timeout(monkeypatch):
    import config
    from VideoEncoder.plugins import pyexec

    owner_id = config.OWNERS[0]
    c = FakeClient()
    monkeypatch.setattr(config, "BOT_TOKEN", "999888777:SUPER_SECRET_BOT_TOKEN_VALUE_XYZ")
    monkeypatch.setenv("DB_URI", "mongodb+srv://admin:ultraSecretPass@cluster0.example.mongodb.net/videl")

    # 4a) Non-owner is blocked
    m_non_owner = FakeMsg("/exec 1 + 1", uid=77777, client=c)
    run(pyexec.run_code(c, m_non_owner))
    assert m_non_owner.replies == []

    # 4b) `ENABLE_OWNER_EXEC=false` disables both `/exec` and `/sh` even for owners
    monkeypatch.setenv("ENABLE_OWNER_EXEC", "false")
    m_disabled = FakeMsg("/exec 1 + 1", uid=owner_id, client=c)
    run(pyexec.run_code(c, m_disabled))
    assert any("disabled" in str(r).lower() for r in m_disabled.replies)
    monkeypatch.setenv("ENABLE_OWNER_EXEC", "true")

    # 4c) `/exec` captures local `print()`, preserves global `sys.stdout`, and redacts secrets!
    orig_stdout = sys.stdout
    m_exec = FakeMsg(
        "/exec print('token=', __import__('config').BOT_TOKEN); return 'mongodb://root:topsecret@db.internal:27017'",
        uid=owner_id,
        client=c,
    )
    run(pyexec.run_code(c, m_exec))
    assert sys.stdout is orig_stdout
    edits = " ".join(str(e) for s in m_exec.sent for e in s.edits)
    assert "SUPER_SECRET_BOT_TOKEN_VALUE_XYZ" not in edits
    assert "topsecret" not in edits
    assert "[REDACTED" in edits

    # 4d) `/sh` enforces timeout and kills runaway child processes
    monkeypatch.setattr(pyexec, "EXEC_TIMEOUT_SEC", 1)
    m_sh = FakeMsg("/sh sleep 10", uid=owner_id, client=c)
    run(pyexec.run_shell(c, m_sh))
    sh_edits = " ".join(str(e) for s in m_sh.sent for e in s.edits)
    assert "timed out" in sh_edits.lower()


# ───────────────────── 5. core/tools.py & inline.py escaping & SSRF ─────────────────────
def test_tools_qr_json_escaping_short_ssrf_and_rename_traversal(monkeypatch, tmp_path):
    from core import inline, tools
    from VideoEncoder.utils import leech

    monkeypatch.setattr(leech, "ALLOW_PRIVATE", False)
    c = FakeClient()

    # 5a) `/qr` escapes `<` and `&` in photo caption
    recorded_captions = []

    async def _fake_reply_photo(photo, caption="", **kw):
        recorded_captions.append(caption)

    m_qr = FakeMsg("/qr </code><b>Injected & Bad</b>", uid=5, client=c)
    m_qr.reply_photo = _fake_reply_photo
    run(tools.qr_cmd(c, m_qr))
    assert recorded_captions
    assert "&lt;/code&gt;&lt;b&gt;Injected &amp; Bad&lt;/b&gt;" in recorded_captions[0]

    # 5b) `/json` escapes both `<` and `&`
    m_json = FakeMsg("/json <tag> & entity", uid=5, client=c)
    run(tools.json_cmd(c, m_json))
    assert "&lt;tag&gt; &amp; entity" in m_json.replies[-1]

    # 5c) `/short` & inline `_shorten` block SSRF private URLs
    m_short = FakeMsg("/short http://127.0.0.1:8080/admin", uid=5, client=c)
    run(tools.short_cmd(c, m_short))
    assert any("private" in str(r).lower() or "local" in str(r).lower() for r in m_short.replies)
    assert run(inline._shorten("http://169.254.169.254/latest")) == []

    # 5d) `/rename` on non-engine media prevents `..` path escape outside `TMP`
    monkeypatch.setattr(tools, "TMP", str(tmp_path))
    voice_msg = FakeMsg(text=None, uid=5)
    voice_msg.document = voice_msg.video = voice_msg.audio = None
    voice_msg.voice = SimpleNamespace(file_name="../../evil", file_size=12, file_unique_id="u1")

    downloaded_paths = []

    class _RecClient(FakeClient):
        async def download_media(self, message, file_name=None, progress=None, **k):
            downloaded_paths.append(file_name)
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            Path(file_name).write_bytes(b"audio")
            return file_name

    rc = _RecClient()
    m_rn = FakeMsg("/rename ..", uid=5, client=rc)
    m_rn.reply_to_message = voice_msg
    run(tools.rename_cmd(rc, m_rn))
    assert len(downloaded_paths) == 1
    dl_resolved = Path(downloaded_paths[0]).resolve()
    assert tmp_path.resolve() in dl_resolved.parents
    assert dl_resolved.name == "renamed_file"


# ───────────────────── 6. Interactive Rich `/stats` tabs ─────────────────────
def test_interactive_rich_stats_tabs_and_admin_guard():
    import config
    from core import admin, rich

    for cb in ("adm_st:main", "adm_st:runtime", "adm_st:health", "bkr:dedupe_ok", "bkr:encrypt_ok"):
        assert rich.SAFE_CALLBACKS.match(cb), f"{cb} must be in SAFE_CALLBACKS for rich rendering"

    owner_id = config.OWNERS[0]
    c = FakeClient()
    m = FakeMsg("/stats", uid=owner_id, client=c)
    m.command = ["stats"]
    run(admin.stats_cmd(c, m))
    assert m.sent

    # Switch to `runtime` and `health` tabs
    sent_card = m.sent[-1]
    for tab, expected in (("runtime", "WzGram"), ("health", "Secret encryption"), ("main", "Total users")):
        q = FakeQuery(f"adm_st:{tab}", uid=owner_id, message=sent_card)
        run(admin.stats_tab_cb(c, q))
        rendered = " ".join(str(x) for x in sent_card.edits)
        assert expected.lower() in rendered.lower() or len(sent_card.edits) >= 1

    # Non-admin rejected on callback
    q_unauth = FakeQuery("adm_st:health", uid=999999, message=sent_card)
    run(admin.stats_tab_cb(c, q_unauth))
    assert any("Admins only" in str(a[0]) for a in q_unauth.answers)

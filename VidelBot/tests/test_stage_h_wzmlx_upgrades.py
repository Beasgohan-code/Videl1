"""
Stage H regression suite — WZML-X reference audit & selective Videl1 upgrades:
  1. Single-instance MongoDB lease fail-closed (`InstanceLeaseError`), transient retry & split-brain heartbeat guard
  2. Post-persistence cache invariants across `core.db` and `core.plugin_manager`
  3. Payment audit fields & clone disabled-price (`premium_stars == 0`) guard
  4. Backup SHA-256 verification, tamper detection & per-collection error isolation
  5. Google Drive upload/download resilience, headless auth guard, retry backoff, cancellation & path safety
  6. Unified live task telemetry, paginated `/status` filters & callback cancel authorization
  7. Plugin `startup`/`shutdown` hooks, hook timeouts, `required_env` & permission validation
  8. Container-aware `sysinfo`, secret-free `diagnostics`, storage backend registry & `/admin` web task APIs
"""
from __future__ import annotations

import asyncio
import json
import os
import types

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, FakeUser, run


# ─── 1. Single-instance lease: fail-closed, transient recovery & heartbeat split-brain guard ───
def test_instance_lease_fail_closed_and_transient_recovery(monkeypatch):
    import config
    from core import instance
    from core.db import vdb

    monkeypatch.setattr(config, "SINGLE_INSTANCE", True)
    instance.STATE.update(held=False, waited=0.0, other=None, last_error=None)

    class DeadCollection:
        async def update_one(self, *a, **k):
            raise RuntimeError("Mongo network unreachable")

        async def insert_one(self, *a, **k):
            raise RuntimeError("Mongo network unreachable")

    with pytest.raises(instance.InstanceLeaseError, match="MongoDB error"):
        run(instance.acquire(DeadCollection(), poll=0.01, max_db_errors=3))
    assert instance.STATE["held"] is False

    # Transient failure (fails 2 times, succeeds on 3rd attempt)
    real_col = vdb.db["runtime_stage_h_test"]
    run(real_col.delete_many({}))
    attempts = {"n": 0}

    class FlakyCollection:
        async def update_one(self, *a, **k):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise ConnectionError("transient primary stepdown")
            return await real_col.update_one(*a, **k)

        async def insert_one(self, *a, **k):
            return await real_col.insert_one(*a, **k)

        async def find_one(self, *a, **k):
            return await real_col.find_one(*a, **k)

    monkeypatch.setattr(instance, "ME", "node-A")
    waited = run(instance.acquire(FlakyCollection(), poll=0.01, max_db_errors=5))
    assert waited >= 0.0 and instance.STATE["held"] is True and attempts["n"] == 3

    # Second node times out fail-closed when SINGLE_INSTANCE_TIMEOUT / timeout is set
    monkeypatch.setattr(instance, "ME", "node-B")
    with pytest.raises(instance.InstanceLeaseError, match="Timed out"):
        run(instance.acquire(real_col, poll=0.02, timeout=0.06))
    assert instance.STATE["held"] is False

    # Heartbeat triggers on_lost when MongoDB fails consecutively past missed-beats threshold
    lost_events = []

    async def on_lost():
        lost_events.append("lost")

    run(
        asyncio.wait_for(
            instance._heartbeat(on_lost, DeadCollection(), beat=0.01, max_missed_beats=3),
            timeout=2.0,
        )
    )
    assert lost_events == ["lost"] and instance.STATE["held"] is False


# ─── 2. Post-persistence cache invariants (`core/db.py` & `core/plugin_manager.py`) ───
def test_db_and_plugin_caches_mutate_only_after_persistence(monkeypatch, tmp_path):
    from core.db import VidelDB, vdb
    from core.plugin_manager import PluginManager

    uid = 884422
    vdb._known.discard(uid)
    vdb._banned.discard(uid)

    class BrokenCollection:
        async def update_one(self, *a, **k):
            raise RuntimeError("simulated disk full")

    monkeypatch.setattr(VidelDB, "users", property(lambda self: BrokenCollection()))

    with pytest.raises(RuntimeError):
        run(vdb.track(FakeUser(uid, "TransientUser")))
    assert uid not in vdb._known, "Failed DB write must not poison _known cache"

    with pytest.raises(RuntimeError):
        run(vdb.ban(uid, "spam"))
    assert uid not in vdb._banned, "Failed DB ban must not mutate _banned cache"

    # Seed banned state, then verify failed unban keeps user banned in memory
    monkeypatch.undo()
    assert run(vdb.track(FakeUser(uid, "TransientUser"))) is True
    assert uid in vdb._known
    run(vdb.ban(uid, "spam"))
    assert vdb.is_banned(uid) is True

    monkeypatch.setattr(VidelDB, "users", property(lambda self: BrokenCollection()))
    with pytest.raises(RuntimeError):
        run(vdb.unban(uid))
    assert vdb.is_banned(uid) is True, "Failed DB unban must keep user banned in memory"

    monkeypatch.undo()
    run(vdb.unban(uid))
    assert vdb.is_banned(uid) is False

    # PluginManager cache-after-persistence check
    pdir = tmp_path / "sample_plg"
    pdir.mkdir()
    (pdir / "plugin.yaml").write_text(
        "name: sample_plg\nversion: 1.0.0\nconfig_schema:\n  limit:\n    type: int\n    default: 5\n",
        encoding="utf-8",
    )
    (pdir / "plugin.py").write_text("X = 1\n", encoding="utf-8")

    pm = PluginManager(tmp_path)
    pm.discover()
    assert pm.is_enabled("sample_plg") is True

    class BrokenDBProxy:
        def __getitem__(self, _name):
            return BrokenCollection()

    monkeypatch.setattr(VidelDB, "db", property(lambda self: BrokenDBProxy()))
    with pytest.raises(RuntimeError):
        run(pm.set_enabled("sample_plg", False))
    assert pm.is_enabled("sample_plg") is True, "Failed DB toggle must not mutate _enabled"

    with pytest.raises(RuntimeError):
        run(pm.configure_plugin("sample_plg", "limit", "20"))
    assert pm.get_plugin_config("sample_plg")["limit"] == 5, "Failed DB config write must not mutate _configs"


# ─── 3. Payment audit metadata & clone disabled-premium guard ───
def test_payment_audit_metadata_and_clone_zero_price_guard():
    from core import payments
    from core.db import vdb

    uid = 771133
    payload = payments.make_payload(30, uid)
    expected_stars = payments.PLANS[30]
    msg = FakeMsg("", uid=uid)
    msg.successful_payment = types.SimpleNamespace(
        payload=payload,
        telegram_payment_charge_id="chg_stage_h_001",
        provider_payment_charge_id="prov_stage_h_001",
        total_amount=expected_stars,
        currency="XTR",
    )
    c = FakeClient()
    run(payments.successful_payment(c, msg))
    stored = run(vdb.db["payments"].find_one({"charge_id": "chg_stage_h_001"}))
    assert stored is not None
    assert stored["fulfilled"] is True
    assert stored["payload"] == payload
    assert stored["provider_charge_id"] == "prov_stage_h_001"

    # Replaying identical charge_id is a strict no-op
    before_count = len(c.calls)
    run(payments.successful_payment(c, msg))
    assert len(c.calls) == before_count

    # Verify clone extras.py rejects `xp` when `premium_stars <= 0`
    extras_src = open("filestore/worker_bot/extras.py", encoding="utf-8").read()
    assert "if cur_stars <= 0 or cur_stars != stars:" in extras_src


# ─── 4. Backup SHA-256 verification, tamper detection & per-collection isolation ───
def test_backup_sha256_tamper_detection_and_collection_isolation(monkeypatch, tmp_path):
    import gzip
    from core import backup
    from core.db import vdb

    run(vdb.users.update_one({"id": 60601}, {"$set": {"id": 60601, "name": "BackupUser"}}, upsert=True))
    bpath = str(tmp_path / "stage_h.json.gz")
    dump_rep = run(backup.dump_with_report(bpath))
    assert len(dump_rep["sha256"]) == 64 and dump_rep["total_docs"] >= 1 and dump_rep["errors"] == {}

    ver = backup.verify_backup_file(bpath, expected_sha256=dump_rep["sha256"])
    assert ver["valid"] is True and ver["total_docs"] == dump_rep["total_docs"]

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        backup.verify_backup_file(bpath, expected_sha256="0" * 64)

    # Tampered document list caught by integrity total_docs check in load()
    tampered_path = str(tmp_path / "tampered.json.gz")
    raw_obj = backup.load(bpath)
    raw_obj["integrity"]["total_docs"] = raw_obj["integrity"]["total_docs"] + 99
    with gzip.open(tampered_path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(raw_obj, default=str))
    with pytest.raises(ValueError, match="integrity mismatch"):
        backup.load(tampered_path)

    # Per-collection restore error isolation: one failing collection doesn't block healthy ones
    payload = {
        "meta": {"format": backup.FORMAT, "created": "2026-11-02T00:00:00Z"},
        "dbs": {
            "videl": {
                "broken_coll": [{"_id": "b1", "v": 1}],
                "healthy_coll": [{"_id": "h1", "v": 42}],
            }
        },
    }
    real_dbs = backup.databases()

    class BrokenColl:
        async def update_one(self, *a, **k):
            raise RuntimeError("collection write error")

    class PartialVidelDB:
        def __getitem__(self, name):
            if name == "broken_coll":
                return BrokenColl()
            return real_dbs["videl"][name]

    monkeypatch.setattr(backup, "databases", lambda: {**real_dbs, "videl": PartialVidelDB()})
    rep = run(backup.restore_with_report(payload, dry_run=False))
    assert "videl.broken_coll" in rep["errors"]
    assert rep["counts"]["videl.healthy_coll"] == 1
    assert run(vdb.db["healthy_coll"].find_one({"_id": "h1"}))["v"] == 42


# ─── 5. Google Drive upload/download resilience, headless auth, retries, cancel & safety ───
def test_google_drive_upload_download_retries_cancel_and_path_safety(monkeypatch, tmp_path):
    from googleapiclient.errors import HttpError
    import config
    from VideoEncoder.utils import jobs
    from VideoEncoder.utils.uploads import drive as drive_pkg
    from VideoEncoder.utils.uploads.drive.download import Downloader, _safe_filename
    from VideoEncoder.utils.uploads.drive.upload import Uploader
    from VideoEncoder.utils.uploads import storage
    import VideoEncoder.plugins.upload as up_plugin

    # 5a. Headless missing-credentials guard
    monkeypatch.setattr(config, "GDRIVE_TOKEN_FILE", str(tmp_path / "missing_token.json"))
    monkeypatch.setattr(config, "GDRIVE_SA_FILE", "")
    assert drive_pkg.is_drive_configured() is False
    with pytest.raises(drive_pkg.DriveNotConfiguredError):
        drive_pkg.DriveAPI()

    # 5b. Path-traversal & URL ID sanitization
    assert _safe_filename("../../etc/passwd") == "passwd"
    assert _safe_filename("..\\..\\secret<.mkv") == "secret.mkv"
    assert drive_pkg._get_file_id("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view") == "1AbCdEfGhIjKlMnOpQrStUv"
    assert drive_pkg._get_file_id("https://example.com/no-id-here") is None

    # 5c. get_drive_url works both WITH and WITHOUT INDEX_URL and escapes HTML
    fake_service = types.SimpleNamespace()
    up_inst = Uploader(service=fake_service)
    url_no_idx = up_inst.get_drive_url("/tmp/<bad>&name.mkv", "1AbCdEfGhIj", index_base="")
    assert "&lt;bad&gt;&amp;name.mkv" in url_no_idx and "Index" not in url_no_idx
    url_with_idx = up_inst.get_drive_url("/tmp/movie.mkv", "1AbCdEfGhIj", index_base="https://idx.example.com/")
    assert "☁️ Drive" in url_with_idx and "⚡ Index" in url_with_idx and "▶️ View" in url_with_idx

    # 5d. Uploader retries transient HttpError(503) and succeeds without blocking event loop
    sample_file = tmp_path / "clip.mp4"
    sample_file.write_bytes(b"x" * 1024)
    chunks_called = {"n": 0}

    class FakeCreateReq:
        def next_chunk(self, num_retries=0):
            chunks_called["n"] += 1
            if chunks_called["n"] == 1:
                resp = types.SimpleNamespace(status=503, reason="Service Unavailable")
                raise HttpError(resp, b"transient 503")
            if chunks_called["n"] == 2:
                st = types.SimpleNamespace(total_size=1024, resumable_progress=512)
                return st, None
            return None, {"id": "DRV_FILE_999"}

    class FakeFilesAPI:
        def create(self, **kw):
            return FakeCreateReq()

    monkeypatch.setattr("VideoEncoder.utils.uploads.drive.upload._sleep_interruptible", lambda s, c: c())
    ok_service = types.SimpleNamespace(files=lambda: FakeFilesAPI())
    u_ok = Uploader(service=ok_service)
    c = FakeClient()
    user_msg = FakeMsg("/gupload", uid=42, client=c)
    status_msg = FakeMsg("Uploading...", uid=42, client=c)
    monkeypatch.setattr(config, "LOG_CHANNEL", 0)
    link = run(u_ok.upload_to_drive(str(sample_file), user_msg, status_msg, poll_interval=0.05))
    assert link is not None and chunks_called["n"] == 3
    assert any("DRV_FILE_999" in str(call[2].get("text", "")) for call in c.called("send_message"))

    # 5e. Permanent Drive error returns None cleanly (no UnboundLocalError: ms!)
    class FailCreateReq:
        def next_chunk(self, num_retries=0):
            resp = types.SimpleNamespace(status=403, reason="Quota Exceeded")
            raise HttpError(resp, b"forbidden")

    u_fail = Uploader(service=types.SimpleNamespace(files=lambda: types.SimpleNamespace(create=lambda **k: FailCreateReq())))
    status_fail = FakeMsg("Uploading...", uid=42, client=c)
    assert run(u_fail.upload_to_drive(str(sample_file), user_msg, status_fail, poll_interval=0.05)) is None
    assert any("failed" in str(e).lower() for e in status_fail.edits)

    # 5f. Mid-upload cancellation via jobs.cancel(msg.id) stops worker & returns None
    class SlowCreateReq:
        def next_chunk(self, num_retries=0):
            time.sleep(0.03)
            return types.SimpleNamespace(total_size=10000, resumable_progress=1000), None

    import time
    u_cancel = Uploader(service=types.SimpleNamespace(files=lambda: types.SimpleNamespace(create=lambda **k: SlowCreateReq())))
    status_cancel = FakeMsg("Uploading...", uid=42, client=c)
    jobs.register(status_cancel.id, 42, user_msg.chat.id, name="clip.mp4")

    async def cancel_scenario():
        task = asyncio.create_task(u_cancel.upload_to_drive(str(sample_file), user_msg, status_cancel, poll_interval=0.05))
        await asyncio.sleep(0.05)
        await jobs.cancel(status_cancel.id)
        return await asyncio.wait_for(task, timeout=3.0)

    assert run(cancel_scenario()) is None
    jobs.unregister(status_cancel.id)
    assert any("cancelled" in str(e).lower() for e in status_cancel.edits)

    # 5g. Downloader removes partial file when download fails mid-stream
    class BrokenDownloadFiles:
        def get_media(self, **kw):
            return object()

    dl = Downloader(service=types.SimpleNamespace(files=lambda: BrokenDownloadFiles()))

    class FakeMediaDownload:
        def __init__(self, fh, req, chunksize=0):
            self.fh = fh

        def next_chunk(self, num_retries=0):
            self.fh.write(b"partial-bytes")
            raise OSError("socket reset")

    monkeypatch.setattr("VideoEncoder.utils.uploads.drive.download.MediaIoBaseDownload", FakeMediaDownload)
    monkeypatch.setattr("VideoEncoder.utils.uploads.drive.download._sleep_interruptible", lambda s, fn: fn())
    with pytest.raises(OSError):
        dl.downloadFile(str(tmp_path), "partial_video.mkv", id="file123")
    assert not (tmp_path / "partial_video.mkv").exists(), "Incomplete Drive download must be cleaned up"

    # 5h. Storage dispatcher falls back to Telegram when Drive is unconfigured
    tg_called = []

    async def fake_tg_upload(path, m, sm, *a, **k):
        tg_called.append(path)
        return "https://t.me/c/1/99"

    monkeypatch.setattr("VideoEncoder.utils.uploads.telegram.upload_to_tg", fake_tg_upload)
    monkeypatch.setattr("VideoEncoder.utils.database.access_db.db.get_drive", lambda _u: asyncio.sleep(0, result=True))
    assert run(storage.dispatch_upload(str(sample_file), user_msg, status_msg)) == "https://t.me/c/1/99"
    assert tg_called == [str(sample_file)]

    # 5i. Manual /dupload blocks sensitive credential files
    secret_env = tmp_path / "config.env"
    secret_env.write_text("BOT_TOKEN=secret", encoding="utf-8")
    assert up_plugin._is_forbidden_upload_target(str(secret_env)) is True
    assert up_plugin._is_forbidden_upload_target("/etc/passwd") is True
    assert up_plugin._is_forbidden_upload_target(str(sample_file)) is False


# ─── 6. Unified task telemetry, paginated `/status` & callback cancel authorization ───
def test_unified_task_telemetry_paginated_status_and_callback_auth(monkeypatch):
    import config
    from VideoEncoder import data as enc_queue
    from VideoEncoder.plugins import status as status_mod
    from VideoEncoder.utils import jobs, scheduler

    enc_queue.clear()
    for j in list(jobs.active()):
        jobs.unregister(j.key)

    try:
        # Populate 7 tasks across 2 users (exceeds PAGE_SIZE=5 -> 2 pages)
        msgs = []
        for idx in range(7):
            uid = 5001 if idx % 2 == 0 else 5002
            m = FakeMsg(f"/dl file_{idx}.mkv", uid=uid)
            m.video = types.SimpleNamespace(file_name=f"episode_{idx + 1}.mkv", file_size=104857600)
            enc_queue.append(m)
            msgs.append(m)

        scheduler.mark_running(msgs[0])
        jobs.register(91001, 5001, msgs[0].chat.id, name="episode_1.mkv", stage="Encoding · CPU", engine="FFmpeg")
        jobs.update_progress(91001, current=52428800, total=104857600, pct=50.0, speed_label="1.6x · 48 fps", eta=35)

        p1_html = status_mod.status_doc(page=1, filter_mode="all", viewer_id=5001).classic()
        p2_html = status_mod.status_doc(page=2, filter_mode="all", viewer_id=5001).classic()
        assert "episode_1.mkv" in p1_html and "FFmpeg" in p1_html and "50%" in p1_html
        assert "episode_6.mkv" in p2_html and "episode_7.mkv" in p2_html

        mine_entries = status_mod.collect_task_entries(filter_mode="mine", viewer_id=5002)
        assert len(mine_entries) == 3 and all(e["uid"] == 5002 for e in mine_entries)

        run_entries = status_mod.collect_task_entries(filter_mode="running", viewer_id=5001)
        assert len(run_entries) == 1 and run_entries[0]["job"]["key"] == 91001

        # Callback authorization: unrelated user 5002 CANNOT cancel user 5001's task
        c = FakeClient()
        card = FakeMsg("status card", uid=5002, client=c)
        q_unauth = FakeQuery("status cancel 91001", uid=5002, message=card)
        run(status_mod.status_pages(c, q_unauth))
        assert jobs.is_cancelled(91001) is False
        assert any("Only the user" in a[0] for a in q_unauth.answers)

        # Task owner 5001 CAN cancel their own task
        q_owner = FakeQuery("status cancel 91001", uid=5001, message=card)
        run(status_mod.status_pages(c, q_owner))
        assert jobs.is_cancelled(91001) is True
        jobs.unregister(91001)

        # Clicking cancel again after task finished does not crash
        q_stale = FakeQuery("status cancel 91001", uid=config.OWNERS[0], message=card)
        run(status_mod.status_pages(c, q_stale))
        assert any("already finished" in a[0] for a in q_stale.answers)
    finally:
        scheduler._running.clear()
        enc_queue.clear()
        jobs.unregister(91001)


# ─── 7. Plugin `startup`/`shutdown` hooks, timeout isolation, `required_env` & permissions ───
def test_plugin_lifecycle_startup_shutdown_timeout_and_env_validation(monkeypatch, tmp_path):
    from core.plugin_manager import PluginManager

    monkeypatch.delenv("STAGE_H_REQUIRED_SECRET", raising=False)

    # 1) Healthy plugin with startup() and shutdown() hooks
    p_ok = tmp_path / "alpha_ok"
    p_ok.mkdir()
    (p_ok / "plugin.yaml").write_text(
        "name: alpha_ok\nversion: 1.2.0\npermissions:\n  - network\ncommands:\n  - name: alpha\n",
        encoding="utf-8",
    )
    (p_ok / "plugin.py").write_text(
        "from core.plugin_manager import PluginBase\n"
        "EVENTS = []\n"
        "class Plugin(PluginBase):\n"
        "    async def on_load(self, client=None):\n"
        "        EVENTS.append('on_load')\n"
        "    async def startup(self, client=None):\n"
        "        EVENTS.append('startup')\n"
        "    async def shutdown(self, client=None):\n"
        "        EVENTS.append('shutdown')\n"
        "    async def on_unload(self, client=None):\n"
        "        EVENTS.append('on_unload')\n",
        encoding="utf-8",
    )

    # 2) Plugin missing required_env
    p_env = tmp_path / "beta_missing_env"
    p_env.mkdir()
    (p_env / "plugin.yaml").write_text(
        "name: beta_missing_env\nversion: 1.0.0\nrequired_env:\n  - STAGE_H_REQUIRED_SECRET\n",
        encoding="utf-8",
    )
    (p_env / "plugin.py").write_text("X = 1\n", encoding="utf-8")

    # 3) Hung startup plugin (isolated by hook_timeout)
    p_hung = tmp_path / "gamma_hung"
    p_hung.mkdir()
    (p_hung / "plugin.yaml").write_text("name: gamma_hung\nversion: 1.0.0\n", encoding="utf-8")
    (p_hung / "plugin.py").write_text(
        "import asyncio\n"
        "from core.plugin_manager import PluginBase\n"
        "class Plugin(PluginBase):\n"
        "    async def startup(self, client=None):\n"
        "        await asyncio.sleep(5)\n",
        encoding="utf-8",
    )

    # 4) Invalid permission manifest
    p_bad_perm = tmp_path / "delta_bad_perm"
    p_bad_perm.mkdir()
    (p_bad_perm / "plugin.yaml").write_text(
        "name: delta_bad_perm\nversion: 1.0.0\npermissions:\n  - raw_kernel_access\n",
        encoding="utf-8",
    )
    (p_bad_perm / "plugin.py").write_text("X = 1\n", encoding="utf-8")

    pm = PluginManager(tmp_path)
    pm.hook_timeout = 0.05
    loaded = run(pm.initialize(client=FakeClient()))
    assert loaded == 1

    rep = pm.health_report()
    assert rep["total"] == 3
    assert rep["healthy"] == 1
    assert "beta_missing_env" in rep["errors"] and rep["missing_env"] == {"beta_missing_env": ["STAGE_H_REQUIRED_SECRET"]}
    assert "gamma_hung" in rep["errors"] and "timed out" in rep["errors"]["gamma_hung"]
    assert "delta_bad_perm" in rep["manifest_errors"]

    alpha_mod = pm.modules["alpha_ok"]
    assert alpha_mod.EVENTS == ["on_load", "startup"]

    unloaded = run(pm.unload_all(client=FakeClient()))
    assert unloaded == 1
    assert alpha_mod.EVENTS == ["on_load", "startup", "shutdown", "on_unload"]


# ─── 8. Container `sysinfo`, secret-free `diagnostics` & `/admin` web console endpoints ───
def test_diagnostics_secret_hygiene_and_admin_web_task_endpoints(monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer
    import config
    import keep_alive
    from core import diagnostics, sysinfo
    from VideoEncoder import data as enc_queue
    from VideoEncoder.utils import jobs

    snap = sysinfo.system_snapshot(".")
    assert snap["cpu_cores"] >= 1 and snap["mem_total_bytes"] > 0

    diag = diagnostics.collect_diagnostics()
    serialized = json.dumps(diag)
    for secret_val in (config.BOT_TOKEN, config.API_HASH, config.DB_URI):
        if secret_val and len(str(secret_val)) > 4:
            assert str(secret_val) not in serialized, "Secret leaked in collect_diagnostics()"

    async def web_scenario():
        monkeypatch.setattr(config, "ADMIN_WEB_TOKEN", "stage-h-admin-tok")
        keep_alive._fails.clear()
        enc_queue.clear()
        waiting_msg = FakeMsg("/dl wait.mkv", uid=801)
        enc_queue.append(waiting_msg)
        jobs.register(77001, 801, waiting_msg.chat.id, name="live_encode.mkv", stage="Encoding", engine="FFmpeg")
        jobs.update_progress(77001, current=250, total=1000, pct=25.0, speed_label="2.0x", eta=12)

        client = TestClient(TestServer(keep_alive.make_app()))
        await client.start_server()
        try:
            # Browser HTML landing vs plain keep-alive probe
            r_html = await client.get("/", headers={"Accept": "text/html"})
            html_body = await r_html.text()
            assert r_html.status == 200 and "running" in html_body and "/admin" in html_body

            r_ping = await client.get("/ping")
            assert r_ping.status == 200 and "running" in await r_ping.text()

            # Unauthenticated task control rejected
            r_unauth = await client.post("/admin/api/tasks/cancel", json={"key": 77001})
            assert r_unauth.status == 401

            hdrs = {"X-Admin-Token": "stage-h-admin-tok"}
            r_diag = await client.get("/admin/api/diagnostics", headers=hdrs)
            assert r_diag.status == 200
            d_json = await r_diag.json()
            assert d_json["runtime"]["engine"] == "WzGram" and len(d_json["storage"]) >= 4
            assert "stage-h-admin-tok" not in json.dumps(d_json)

            r_stats = await client.get("/admin/api/stats?days=7", headers=hdrs)
            assert r_stats.status == 200
            s_json = await r_stats.json()
            assert any(item["key"] == 77001 and item["progress"] == 25.0 for item in s_json["live"])

            # Cancel running task via web API
            r_cancel = await client.post("/admin/api/tasks/cancel", headers=hdrs, json={"key": 77001})
            assert r_cancel.status == 200 and (await r_cancel.json())["cancelled"] is True
            assert jobs.is_cancelled(77001) is True

            # Purge waiting queue via web API
            r_clear = await client.post("/admin/api/tasks/clear", headers=hdrs)
            assert r_clear.status == 200 and (await r_clear.json())["removed"] == 1
            assert len(enc_queue) == 0
        finally:
            jobs.unregister(77001)
            enc_queue.clear()
            await client.close()

    run(web_scenario())

"""
Stage B–D regression suite:
  • Atomic & idempotent Telegram Stars payments (main bot + clone workers) & tamper rejection
  • SSRF protection & output URL validation in filestore/utils/shortener.py
  • HTML escaping & subprocess safety in /admins, /update, watchdog, and clone /id
  • Orphan clone worker cleanup when post-start initialization fails
  • Shared Motor connection pool + atomic concurrent VideoEncoder user creation
  • Clean background task drain (core/bg.cancel_all + watchdog.stop)
  • Backup decompression size cap, schema validation, dry-run preview & post-restore cache reload
  • Idempotent load_plugins() handler guard + actionable plugin health/error diagnostics
"""
import asyncio
from datetime import date, timedelta
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, no_botapi, reset_db, run


@pytest.fixture(autouse=True)
def _clean_env():
    run(reset_db())
    no_botapi()
    yield


# ───────────────────── 1 & 2. Main-bot Stars idempotency & validation ─────────────────────
def _sp_msg(payload: str, *, uid: int = 5, stars: int = 100, currency: str = "XTR", charge_id: str = "ch_unique_1"):
    m = FakeMsg(text=None, uid=uid)
    m.successful_payment = SimpleNamespace(
        payload=payload,
        total_amount=stars,
        currency=currency,
        telegram_payment_charge_id=charge_id,
    )
    return m


def test_stars_payment_replay_is_atomic_and_idempotent():
    from core import plans
    from core.db import vdb
    from core.payments import (
        make_gift_payload,
        make_payload,
        make_sub_payload,
        successful_payment,
    )
    from database.db import db as saver_db

    c = FakeClient()

    # 1) Direct Premium purchase replayed 3x with identical charge_id
    pm = _sp_msg(make_payload(30, 5), uid=5, stars=100, charge_id="ch_buy_once")
    for _ in range(3):
        run(successful_payment(c, pm))
    u = run(saver_db.col.find_one({"id": 5}))
    assert u["is_premium"] is True
    assert u["premium_expiry"] == (date.today() + timedelta(days=30)).isoformat()
    assert run(vdb.db["payments"].count_documents({"charge_id": "ch_buy_once"})) == 1

    # 2) Monthly subscription replayed 3x → renewals stays 0, expiry extended once (+30d more = +60d)
    sub_msg = _sp_msg(make_sub_payload(5), uid=5, stars=90, charge_id="ch_sub_once")
    for _ in range(3):
        run(successful_payment(c, sub_msg))
    sub = run(vdb.db["subscriptions"].find_one({"user": 5}))
    assert sub["renewals"] == 0
    assert run(saver_db.col.find_one({"id": 5}))["premium_expiry"] == (date.today() + timedelta(days=60)).isoformat()

    # 3) Gift purchase replayed 3x → friend gets +30d once and only 1 gift DM
    gift_msg = _sp_msg(make_gift_payload(30, 5, 42), uid=5, stars=100, charge_id="ch_gift_once")
    for _ in range(3):
        run(successful_payment(c, gift_msg))
    friend = run(saver_db.col.find_one({"id": 42}))
    assert friend["premium_expiry"] == (date.today() + timedelta(days=30)).isoformat()
    gift_dms = [a for name, a, _ in c.calls if name == "send_message" and a and a[0] == 42]
    assert len(gift_dms) == 1

    # 4) Add-on product plan replayed 3x → granted once
    prod_price = plans.PRODUCTS["encpro"]["stars"]
    prod_msg = _sp_msg("vx:encpro:5:1", uid=5, stars=prod_price, charge_id="ch_prod_once")
    for _ in range(3):
        run(successful_payment(c, prod_msg))
    assert run(vdb.db["payments"].count_documents({"charge_id": "ch_prod_once"})) == 1
    until = run(plans.until(5, "encpro"))
    assert until is not None and 29 <= (until.date() - date.today()).days <= 31


def test_stars_payment_rejects_currency_amount_and_sender_tampering():
    from core.db import vdb
    from core.payments import make_payload, make_sub_payload, successful_payment
    from database.db import db as saver_db

    c = FakeClient()

    # Wrong currency
    run(successful_payment(c, _sp_msg(make_payload(30, 5), uid=5, stars=100, currency="USD", charge_id="bad_cur")))
    # Tampered amount (paid 1 Star for 100-Star tier)
    run(successful_payment(c, _sp_msg(make_payload(30, 5), uid=5, stars=1, currency="XTR", charge_id="bad_amt")))
    # Sender mismatch (uid 999 replaying payload issued for payer 5)
    run(successful_payment(c, _sp_msg(make_payload(30, 5), uid=999, stars=100, currency="XTR", charge_id="bad_uid")))
    # Empty charge_id
    run(successful_payment(c, _sp_msg(make_payload(30, 5), uid=5, stars=100, currency="XTR", charge_id="")))

    assert run(vdb.db["payments"].count_documents({})) == 0
    assert not (run(saver_db.col.find_one({"id": 5})) or {}).get("is_premium")

    # Grandfathered subscription renewal: existing active sub at 75 Stars still renews cleanly
    run(vdb.db["subscriptions"].insert_one({
        "user": 77, "charge_id": "orig_sub", "last_charge": "orig_sub",
        "stars": 75, "renewals": 0, "active": True, "canceled": False,
    }))
    run(successful_payment(c, _sp_msg(make_sub_payload(77), uid=77, stars=75, currency="XTR", charge_id="renew_75")))
    assert run(vdb.db["payments"].count_documents({"charge_id": "renew_75"})) == 1
    assert run(vdb.db["subscriptions"].find_one({"user": 77}))["renewals"] == 1


# ───────────────────── 3. Clone-bot Stars idempotency & tamper guard ─────────────────────
def test_clone_stars_payment_idempotency_and_tamper_rejection():
    from pyrogram import StopPropagation
    from filestore.database.extras_db import CloneExtras
    from filestore.database.main_db import MainDB
    from filestore.database.worker_db import WorkerDB
    from filestore.worker_bot.extras import setup_extras

    bot_id = 7777
    mdb, wdb, xdb = MainDB(), WorkerDB(bot_id), CloneExtras(bot_id)
    run(mdb.bots.insert_one({
        "_id": bot_id,
        "owner_id": 111,
        "log_channel_id": -100555,
        "settings": {"premium_stars": 50, "premium_days": 30},
    }))

    captured = {}

    class DummyApp:
        me = SimpleNamespace(id=bot_id, username="clone7777_bot")

        def on_message(self, flt, group=0):
            def dec(fn):
                if group == -10:
                    captured["paid"] = fn
                return fn
            return dec

        def on_callback_query(self, *a, **k):
            return lambda fn: fn

        def on_inline_query(self, *a, **k):
            return lambda fn: fn

        def on_pre_checkout_query(self, *a, **k):
            return lambda fn: fn

    deliveries = []

    async def fake_deliver(client, message, payload, **kw):
        deliveries.append((payload, kw))

    async def fresh_doc():
        return await mdb.get_bot(bot_id)

    async def is_admin(uid):
        return uid == 111

    async def gate(client, message, *a, **k):
        return True

    ctx = SimpleNamespace(
        bot_id=bot_id,
        owner_id=111,
        log_channel_id=-100555,
        worker_db=wdb,
        xdb=xdb,
        main_db=mdb,
        is_admin=is_admin,
        gate=gate,
        deliver=fake_deliver,
        fresh_doc=fresh_doc,
        tasks=[],
    )
    setup_extras(DummyApp(), ctx)
    paid_handler = captured["paid"]
    c = FakeClient()

    # Valid clone premium payment replayed twice
    m_ok = FakeMsg("", uid=9, client=c)
    m_ok.successful_payment = SimpleNamespace(
        payload="xp:30:9:1", total_amount=50, currency="XTR", telegram_payment_charge_id="c_clone_1"
    )
    for _ in range(2):
        with pytest.raises(StopPropagation):
            run(paid_handler(c, m_ok))

    is_prem, until = run(xdb.premium_until(9))
    assert is_prem and 29 <= (until.date() - date.today()).days <= 31
    assert run(xdb.stars_earned()) == 50

    # Tampered clone payments (wrong currency, wrong payer, wrong amount)
    for bad_sp in (
        SimpleNamespace(payload="xp:30:9:1", total_amount=50, currency="USD", telegram_payment_charge_id="c_bad_1"),
        SimpleNamespace(payload="xp:30:99:1", total_amount=50, currency="XTR", telegram_payment_charge_id="c_bad_2"),
        SimpleNamespace(payload="xp:30:9:1", total_amount=1, currency="XTR", telegram_payment_charge_id="c_bad_3"),
    ):
        m_bad = FakeMsg("", uid=9, client=c)
        m_bad.successful_payment = bad_sp
        with pytest.raises(StopPropagation):
            run(paid_handler(c, m_bad))
    assert run(xdb.stars_earned()) == 50


# ───────────────────── 4. Shortener SSRF & URL output validation ─────────────────────
def test_shortener_blocks_ssrf_private_ips_imds_redirects_and_bad_schemes(monkeypatch):
    from filestore.utils import shortener

    for unsafe in (
        "http://127.0.0.1:8080",
        "http://localhost/api",
        "http://169.254.169.254/latest/meta-data/",
        "http://10.0.0.8",
        "http://192.168.1.20",
        "http://172.16.5.4",
        "http://100.64.1.1",
        "http://[::1]:8000",
        "http://[::ffff:127.0.0.1]",
        "https:// internal.local",
        "https://service.internal",
        "file:///etc/passwd",
        "https://user:secret@short.example.com",
    ):
        assert run(shortener.validate_public_endpoint(unsafe)) is None, unsafe

    # Hostname whose DNS resolves to cloud metadata IP 169.254.169.254 is blocked before HTTP
    monkeypatch.setattr(
        shortener.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(shortener.socket.AF_INET, shortener.socket.SOCK_STREAM, 6, "", ("169.254.169.254", port))],
    )
    assert run(shortener.validate_public_endpoint("rebind.evil.example")) is None

    # Public IP passes validation; custom AdLinkFly call disables redirects and validates output scheme
    monkeypatch.setattr(
        shortener.socket,
        "getaddrinfo",
        lambda host, port, *a, **k: [(shortener.socket.AF_INET, shortener.socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))],
    )
    assert run(shortener.validate_public_endpoint("short.example.com/v1/")) == "https://short.example.com/v1"

    seen_kwargs = {}

    class FakeResp:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def json(self, **k):
            return {"status": "success", "shortenedUrl": "javascript:alert(1)"}

        async def text(self):
            return "javascript:alert(1)"

    class FakeSession:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, url, **kw):
            seen_kwargs.update(kw)
            return FakeResp()

    monkeypatch.setattr(shortener.aiohttp, "ClientSession", FakeSession)
    orig = "https://t.me/VidelBot?start=abc"
    assert run(shortener.shorten_url(orig, api_key="k", domain="short.example.com", provider="adlinkfly")) == orig
    assert seen_kwargs.get("allow_redirects") is False
    assert not shortener.is_valid_http_url("javascript:alert(1)")
    assert not shortener.is_valid_http_url("http://127.0.0.1/x")
    assert shortener.is_valid_http_url("https://short.example.com/ok123")


# ───────────────────── 5. HTML escaping & subprocess safety ─────────────────────
def test_html_escaping_in_admins_update_and_watchdog(monkeypatch):
    from core import admin as admin_mod
    from core import admins as admins_mod
    import watchdog

    # 1) /admins escapes `<` and `&` in stored admin display names
    run(admins_mod.add_admin(777, by=111, name="<script>bad</script> & Co"))
    try:
        txt, _ = run(admins_mod._admins_view(111))
        assert "<script>" not in txt and "&lt;script&gt;bad&lt;/script&gt; &amp; Co" in txt
    finally:
        run(admins_mod.remove_admin(777))

    # 2) /update uses create_subprocess_exec, redacts git credentials, and HTML-escapes git output
    exec_calls = []

    class FakeProc:
        returncode = 1

        async def communicate(self):
            return b"fatal: https://ghp_SECRETTOKEN@github.com/org/repo.git <broken & tag>", b""

    async def fake_exec(*args, **kw):
        exec_calls.append(args)
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    c = FakeClient()
    m = FakeMsg("/update", uid=111, client=c)
    run(admin_mod.update_cmd(c, m))
    assert exec_calls == [("git", "pull", "--ff-only")]
    # FakeMsg.reply_text returns a child status FakeMsg whose edit_text records on status.edits; inspect client or reply
    # Wait: FakeMsg.reply_text returns FakeMsg with edits tracked on the returned status object. Let's check admin.py source too:
    assert "create_subprocess_shell" not in Path(admin_mod.__file__).read_text()

    # Check exact edited text by capturing status message
    edited = []

    class StatusMsg:
        async def edit_text(self, text, **kw):
            edited.append(text)

    class UpdateMsg:
        from_user = SimpleNamespace(id=111, first_name="Owner")

        async def reply_text(self, text, **kw):
            return StatusMsg()

    run(admin_mod.update_cmd(c, UpdateMsg()))
    assert len(edited) == 1
    assert "ghp_SECRETTOKEN" not in edited[0]
    assert "https://***@github.com" in edited[0]
    assert "&lt;broken &amp; tag&gt;" in edited[0]

    # 3) Watchdog escapes clone startup exception strings on 5th consecutive failure
    events = []
    dog = watchdog.Watchdog(c)
    dog.worker_failures[9999] = 4

    async def fake_active():
        return [{"_id": 9999, "owner_id": 111, "bot_username": "broken_bot", "is_active": True}]

    async def fail_start(doc):
        raise RuntimeError("<BadHTML & Co>")

    async def capture_event(tag, doc, what):
        events.append((tag, what))

    import filestore.database.main_db as main_db_mod
    import filestore.worker_bot.engine as eng_mod

    monkeypatch.setattr(main_db_mod.MainDB, "get_all_active_bots", lambda self: fake_active())
    monkeypatch.setattr(eng_mod.worker_engine, "start_worker", fail_start)
    monkeypatch.setattr(dog, "_clone_event", capture_event)
    run(dog.heal_workers())
    assert events and events[0][0] == "CloneFailed"
    assert "<BadHTML" not in events[0][1] and "&lt;BadHTML &amp; Co&gt;" in events[0][1]


# ───────────────────── 6. Orphan clone worker cleanup ─────────────────────
def test_clone_start_worker_stops_orphan_client_if_after_start_fails(monkeypatch):
    import filestore.worker_bot.engine as eng_mod
    import filestore.worker_bot.extras as extras_mod
    from filestore.utils.security import encrypt_token

    stopped = []

    class FakeWorkerApp:
        def __init__(self, **kw):
            self.is_connected = False
            self.me = SimpleNamespace(id=54321, username="orphan_test_bot")

        def add_handler(self, *a, **k):
            pass

        def on_message(self, *a, **k):
            return lambda fn: fn

        def on_callback_query(self, *a, **k):
            return lambda fn: fn

        def on_inline_query(self, *a, **k):
            return lambda fn: fn

        def on_pre_checkout_query(self, *a, **k):
            return lambda fn: fn

        def on_chat_join_request(self, *a, **k):
            return lambda fn: fn

        async def start(self):
            self.is_connected = True

        def set_parse_mode(self, mode):
            pass

        async def stop(self, block=False):
            self.is_connected = False
            stopped.append(True)

    async def boom_after_start(app, ctx):
        raise RuntimeError("menu sync exploded")

    monkeypatch.setattr(eng_mod, "Client", FakeWorkerApp)
    monkeypatch.setattr(extras_mod, "after_start", boom_after_start)

    engine = eng_mod.WorkerEngine()
    doc = {
        "_id": 54321,
        "bot_token_encrypted": encrypt_token("54321:TEST_TOKEN"),
        "log_channel_id": -100777,
        "owner_id": 111,
        "bot_username": "orphan_test_bot",
    }
    with pytest.raises(RuntimeError, match="menu sync exploded"):
        run(engine.start_worker(doc))

    assert stopped == [True]
    assert 54321 not in engine.workers


# ───────────────────── 7 & 8. Shared Motor pool, atomic encoder user & bg drain ─────────────────────
def test_shared_motor_pool_and_atomic_concurrent_encoder_add_user():
    from core.db import vdb
    from database.db import db as saver_db
    from filestore.database.mongo import get_motor_client
    from VideoEncoder.utils.database.access_db import db as enc_db

    assert saver_db._client is get_motor_client()
    assert enc_db._client is get_motor_client()
    assert vdb.db.client is get_motor_client()

    async def hammer():
        await asyncio.gather(*(enc_db.add_user(8888) for _ in range(25)))
        return await enc_db.col.count_documents({"id": 8888})

    assert run(hammer()) == 1


def test_bg_cancel_all_and_watchdog_stop_drain_cleanly():
    from core import bg
    import watchdog

    async def go():
        ev = asyncio.Event()
        for idx in range(3):
            bg.spawn(ev.wait(), name=f"worker-{idx}")
        assert len(bg.active_names()) >= 3
        watchdog.start(FakeClient())
        assert watchdog.dog is not None
        await watchdog.stop()
        assert watchdog.dog is None
        cancelled = await bg.cancel_all(timeout=2.0)
        assert cancelled >= 3 and bg.running() == 0

    run(go())


# ───────────────────── 9. Backup validation, size cap, dry-run & cache reload ─────────────────────
def test_backup_validation_size_cap_dry_run_and_cache_reload(tmp_path):
    from core import backup
    from core.db import vdb
    from database.db import db as saver_db

    # 1) Schema validation rejects malformed payloads
    with pytest.raises(ValueError):
        backup.validate_backup_payload({"meta": {"format": backup.FORMAT}, "dbs": []})
    with pytest.raises(ValueError):
        backup.validate_backup_payload({
            "meta": {"format": backup.FORMAT},
            "dbs": {"videl": {"system.indexes": []}},
        })

    async def go():
        await saver_db.add_user(901, "Alice")
        await saver_db.set_session(901, "LIVE_SECRET_SESSION")
        await vdb.ban(902, "spammer")
        bpath = str(tmp_path / "valid.json.gz")
        await backup.dump(bpath)

        # Size cap guard blocks oversized uncompressed stream
        with pytest.raises(ValueError, match="maximum uncompressed size"):
            backup.load(bpath, max_bytes=32)

        data = backup.load(bpath)
        # Mutate live DB: unban 902, rename 901, delete nothing, and inject an invalid doc into payload
        await vdb.unban(902)
        assert not vdb.is_banned(902)
        data["dbs"]["videl"]["videl_users"].append({"id": 999})  # missing _id -> skipped_invalid
        data["dbs"]["videl"]["videl_users"].append({"_id": "new_oid_1", "id": 903, "name": "BrandNew", "banned": False})

        # Dry-run makes ZERO changes to live MongoDB or vdb._banned
        dry_rep = await backup.restore_with_report(data, dry_run=True)
        assert dry_rep["dry_run"] is True
        assert dry_rep["would_insert"] >= 1
        assert dry_rep["would_update"] >= 1
        assert dry_rep["skipped_invalid"] >= 1
        assert dry_rep["preserved_secrets"] >= 1
        assert await vdb.users.find_one({"id": 903}) is None
        assert not vdb.is_banned(902)

        # Real restore applies changes, preserves live session secret, and refreshes vdb._banned immediately
        live_rep = await backup.restore_with_report(data, dry_run=False)
        assert live_rep["dry_run"] is False
        assert (await vdb.users.find_one({"id": 903}))["name"] == "BrandNew"
        assert (await saver_db.col.find_one({"id": 901}))["session"] == "LIVE_SECRET_SESSION"
        assert vdb.is_banned(902) is True

    run(go())


# ───────────────────── 10. Plugin duplicate guard & health reporting ─────────────────────
def test_load_plugins_duplicate_guard_and_plugin_health_diagnostics(tmp_path):
    import run as run_mod
    from core.plugin_manager import PluginManager
    from core import plugins_cmd

    class CountingApp:
        def __init__(self):
            self.added = []

        def add_handler(self, h, g=0):
            self.added.append((h, g))

    app = CountingApp()
    first = run_mod.load_plugins(app)
    second = run_mod.load_plugins(app)
    assert first > 20 and second == 0

    # Isolated PluginManager with 1 healthy plugin, 1 broken manifest, 1 broken entry import
    pdir = tmp_path / "plugins"
    ok_dir = pdir / "ok_demo"
    ok_dir.mkdir(parents=True)
    (ok_dir / "plugin.yaml").write_text(
        "name: ok_demo\nversion: 1.0.0\ndescription: healthy\nentry: plugin.py\ncommands:\n  - name: okdemo\n",
        encoding="utf-8",
    )
    (ok_dir / "plugin.py").write_text("VALUE = 1\n", encoding="utf-8")

    bad_yml = pdir / "broken_manifest"
    bad_yml.mkdir()
    (bad_yml / "plugin.yaml").write_text("name: INVALID NAME WITH SPACES\nversion: bad\n", encoding="utf-8")

    bad_code = pdir / "broken_code"
    bad_code.mkdir()
    (bad_code / "plugin.yaml").write_text(
        "name: broken_code\nversion: 0.2.0\ndescription: broken\nentry: plugin.py\n",
        encoding="utf-8",
    )
    (bad_code / "plugin.py").write_text("raise ImportError('missing native_codec_xyz')\n", encoding="utf-8")

    mgr = PluginManager(plugins_dir=pdir)
    loaded = run(mgr.initialize())
    assert loaded == 1
    health = mgr.health_report()
    assert health["total"] == 2
    assert health["healthy"] == 1
    assert "broken_manifest" in health["manifest_errors"]
    assert "missing native_codec_xyz" in health["errors"]["broken_code"]

    # Verify /plugins UI surfaces ⚠️ Error and diagnostic alert
    orig_mgr = plugins_cmd.plugin_manager
    plugins_cmd.plugin_manager = mgr
    try:
        list_doc, _ = plugins_cmd.plugins_list_doc()
        rendered = list_doc.classic()
        assert "⚠️ Error" in rendered and "missing native_codec_xyz" in rendered
        info_doc, _ = plugins_cmd.plugin_info_doc("broken_code")
        assert "missing native_codec_xyz" in info_doc.classic()
    finally:
        plugins_cmd.plugin_manager = orig_mgr


def test_no_unsafe_deserializers_across_videlbot_sources():
    import ast
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for p in root.rglob("*.py"):
        if "__pycache__" in p.parts or "tests" in p.parts:
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=str(p))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in ("yaml.load", "pickle.load", "pickle.loads"):
                offenders.append(f"{p.relative_to(root)}:{node.lineno}")
    assert not offenders, offenders


def test_scheduler_prevents_stale_message_id_reuse_collision():
    import weakref
    from VideoEncoder import data as queue
    from VideoEncoder.utils import scheduler

    saved = list(queue)
    try:
        dead_target = FakeMsg("/screens 3")
        fresh_msg = FakeMsg("/dl")
        # Simulate CPython reusing dead_target's memory address for fresh_msg after GC
        mid = id(fresh_msg)
        scheduler.MODES[mid] = "screens"
        scheduler.EXTRA[mid] = {"stale": True}
        scheduler._REFS[mid] = weakref.ref(dead_target)
        assert scheduler.mode_of(fresh_msg) == "tg"
        assert scheduler.extra_of(fresh_msg) == {}
    finally:
        queue[:] = saved
        scheduler.finish(fresh_msg)



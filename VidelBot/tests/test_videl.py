"""End-to-end-ish tests for Videl's core modules (run: python -m pytest tests -q)."""
import os
import shutil
import tempfile
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run

from pyrogram import StopPropagation, enums  # noqa: E402
from pyrogram.errors import UserNotParticipant  # noqa: E402

MODULES = load_all()


@pytest.fixture(autouse=True)
def fresh_db():
    run(reset_db())
    from core import fsub
    fsub._ok_cache.clear()
    fsub._chat_cache.clear()
    fsub._last_prompt.clear()
    from core import botlog
    botlog._start_seen.clear()
    yield


# ─────────────────────────── loading ───────────────────────────
def test_all_plugins_import_and_register():
    import run as runner
    from client import app
    n = runner.load_plugins(app)
    assert n >= 150
    run(__import__("asyncio").sleep(0.2))
    groups = app.dispatcher.groups
    assert sorted(k for k in groups if k < 0) == [-10, -4, -3, -2]
    assert any(h.callback.__module__ == "core.errors" for h in app.dispatcher.error_handlers)


def test_no_credits_in_user_facing_texts():
    from core import texts
    blob = " ".join(str(v) for k, v in vars(texts).items() if k.isupper())
    for bad in ("abhinai", "songoku", "SonGoku", "cantarella", "Powered By", "WeebZone", "ᴅᴇᴠᴇʟᴏᴘᴇʀ"):
        assert bad.lower() not in blob.lower(), bad


# ─────────────────────────── payments ───────────────────────────
def test_payload_roundtrip_and_foreign():
    from core.payments import make_payload, parse_payload
    assert parse_payload(make_payload(30, 42)) == (30, 42)
    assert parse_payload(make_payload(0, 7).encode()) == (0, 7)
    with pytest.raises(ValueError):
        parse_payload("xx:1:2:3")


def test_new_expiry_extends_future_only():
    from core.payments import new_expiry
    today = date.today()
    assert new_expiry(None, 30) == (today + timedelta(days=30)).isoformat()
    future = (today + timedelta(days=10)).isoformat()
    assert new_expiry(future, 30) == (today + timedelta(days=40)).isoformat()
    past = (today - timedelta(days=5)).isoformat()
    assert new_expiry(past, 30) == (today + timedelta(days=30)).isoformat()
    assert new_expiry(future, 0) is None


def test_pre_checkout_validation():
    from core.payments import make_payload, pre_checkout
    results = []

    class Q:
        def __init__(self, payload, amount, uid=5, currency="XTR"):
            self.payload, self.total_amount, self.currency = payload, amount, currency
            self.from_user = SimpleNamespace(id=uid)

        async def answer(self, success, error=None):
            results.append(success)

    c = FakeClient()
    run(pre_checkout(c, Q(make_payload(30, 5), 100)))
    run(pre_checkout(c, Q(make_payload(30, 5), 1)))            # wrong amount
    run(pre_checkout(c, Q(make_payload(30, 6), 100)))          # other user
    run(pre_checkout(c, Q(make_payload(7, 5), 100)))           # unknown plan
    run(pre_checkout(c, Q(make_payload(30, 5), 100, currency="USD")))
    run(pre_checkout(c, Q("garbage", 100)))
    assert results == [True, False, False, False, False, False]


def _payment_msg(days, uid=5, stars=100):
    from core.payments import make_payload
    m = FakeMsg(text=None, uid=uid)
    m.successful_payment = SimpleNamespace(payload=make_payload(days, uid), total_amount=stars, currency="XTR",
                                           telegram_payment_charge_id=f"ch_{days}_{time.time()}")
    return m


def test_successful_payment_activates_and_extends():
    from core.db import vdb
    from core.payments import successful_payment
    from database.db import db
    c = FakeClient()
    run(successful_payment(c, _payment_msg(30)))
    u = run(db.col.find_one({"id": 5}))
    assert u["is_premium"] and u["premium_expiry"] == (date.today() + timedelta(days=30)).isoformat()
    run(successful_payment(c, _payment_msg(90, stars=250)))
    u = run(db.col.find_one({"id": 5}))
    assert u["premium_expiry"] == (date.today() + timedelta(days=120)).isoformat()
    run(successful_payment(c, _payment_msg(0, stars=500)))
    assert run(db.col.find_one({"id": 5}))["premium_expiry"] is None
    # lifetime stays lifetime even if a timed plan is bought later
    run(successful_payment(c, _payment_msg(30)))
    assert run(db.col.find_one({"id": 5}))["premium_expiry"] is None
    assert run(vdb.db["payments"].count_documents({})) == 4
    # receipt sent with an effect + LOG_CHANNEL notified
    sends = c.called("send_message")
    assert any(k.get("message_effect_id") for _, _, k in sends)
    assert any(a and a[0] == -100123 for _, a, _ in sends)


def test_refund_removes_premium():
    from core.payments import refund_cmd, successful_payment
    from core.db import vdb
    from database.db import db
    c = FakeClient()
    pm = _payment_msg(30)
    run(successful_payment(c, pm))
    charge = pm.successful_payment.telegram_payment_charge_id
    msg = FakeMsg(f"/refund 5 {charge}", uid=111)
    run(refund_cmd(c, msg))
    assert c.called("refund_star_payment")
    assert run(db.col.find_one({"id": 5}))["is_premium"] is False
    assert run(vdb.db["payments"].find_one({"charge_id": charge}))["refunded"] is True


def test_stars_buy_sends_xtr_invoice():
    from core.payments import stars_buy
    c = FakeClient()
    q = FakeQuery("stars_buy:90")
    import re
    q.matches = [re.match(r"^stars_buy:(\d+)$", q.data)]
    run(stars_buy(c, q))
    (_, _, k), = c.called("send_invoice")
    assert k["currency"] == "XTR" and k["prices"][0].amount == 250 and k["chat_id"] == 5
    q2 = FakeQuery("stars_buy:3")
    q2.matches = [re.match(r"^stars_buy:(\d+)$", q2.data)]
    run(stars_buy(c, q2))
    assert q2.answers[-1][1] is True  # alert: plan unavailable


def test_stars_stats_command():
    from core.payments import stars_stats, successful_payment
    c = FakeClient()
    run(successful_payment(c, _payment_msg(30)))
    m = FakeMsg("/stars", uid=111)
    run(stars_stats(c, m))
    assert "Stars earned: <code>100</code>" in m.replies[-1]


# ─────────────────────────── middleware ───────────────────────────
def test_ban_and_maintenance_gate():
    from core.db import vdb
    from core.middleware import gate_callbacks, gate_messages
    c = FakeClient()
    run(vdb.ban(5))
    m = FakeMsg("hello", uid=5)
    with pytest.raises(StopPropagation):
        run(gate_messages(c, m))
    assert "banned" in m.replies[-1].lower()
    with pytest.raises(StopPropagation):
        run(gate_callbacks(c, FakeQuery("start_btn", uid=5)))
    run(vdb.unban(5))
    run(gate_messages(c, FakeMsg("hello", uid=5)))       # passes
    run(vdb.set_setting("maintenance", True))
    with pytest.raises(StopPropagation):
        run(gate_messages(c, FakeMsg("hello", uid=5)))
    run(gate_messages(c, FakeMsg("hello", uid=111)))     # owner bypasses maintenance


def test_new_user_tracking_logs_once():
    from core.middleware import track_users
    from database.db import db
    c = FakeClient()
    run(track_users(c, FakeMsg("/start", uid=77)))
    run(track_users(c, FakeMsg("/start", uid=77)))
    assert run(db.is_user_exist(77))
    logs = [x for x in c.called("send_message") if "#NewUser" in x[1][1]]
    assert len(logs) == 1


# ─────────────────────────── force subscribe ───────────────────────────
def test_fsub_gate_blocks_until_joined(monkeypatch):
    from core import fsub
    monkeypatch.setattr(fsub, "FSUB_CHANNELS", [-1001])
    c = FakeClient()
    c.members[(-1001, 5)] = UserNotParticipant()
    m = FakeMsg("/start", uid=5)
    with pytest.raises(StopPropagation):
        run(fsub.fsub_gate_messages(c, m))
    sent = c.called("send_web_page") or c.called("send_message")
    assert sent and "fsub_reload" in str(sent[-1][2]["reply_markup"])
    # anti-spam: a normal message right after doesn't resend the prompt
    n = len(c.calls)
    with pytest.raises(StopPropagation):
        run(fsub.fsub_gate_messages(c, FakeMsg("hi", uid=5)))
    assert not [x for x in c.calls[n:] if x[0] in ("send_web_page", "send_message")]
    # callback gate
    with pytest.raises(StopPropagation):
        run(fsub.fsub_gate_callbacks(c, FakeQuery("help_btn", uid=5)))
    # reload while still not joined → alert
    q = FakeQuery("fsub_reload", uid=5)
    run(fsub.fsub_reload(c, q))
    assert q.answers[0][1] is True
    # join → reload shows home, gates pass
    c.members[(-1001, 5)] = enums.ChatMemberStatus.MEMBER
    q = FakeQuery("fsub_reload", uid=5)
    run(fsub.fsub_reload(c, q))
    assert c.called("invoke") or q.message.edits
    run(fsub.fsub_gate_messages(c, FakeMsg("hi", uid=5)))
    # admins never gated
    run(fsub.fsub_gate_messages(c, FakeMsg("hi", uid=222)))


def test_fsub_join_request_counts(monkeypatch):
    from core import fsub
    monkeypatch.setattr(fsub, "FSUB_CHANNELS", [-1001])
    monkeypatch.setattr(fsub, "FSUB_REQUEST_MODE", True)
    c = FakeClient()
    c.members[(-1001, 5)] = UserNotParticipant()
    assert run(fsub.missing_channels(c, 5)) == [-1001]
    req = SimpleNamespace(chat=SimpleNamespace(id=-1001, username=None), from_user=SimpleNamespace(id=5))
    run(fsub.record_join_request(c, req))
    assert run(fsub.missing_channels(c, 5)) == []


def test_fsub_misconfigured_channel_does_not_lock_users(monkeypatch):
    from core import fsub
    monkeypatch.setattr(fsub, "FSUB_CHANNELS", [-1002])
    c = FakeClient()
    c.members[(-1002, 5)] = RuntimeError("CHAT_ADMIN_REQUIRED")
    assert run(fsub.missing_channels(c, 5)) == []


def test_fsub_admin_commands():
    from core import fsub
    c = FakeClient()
    c.members[(-1005, "me")] = enums.ChatMemberStatus.ADMINISTRATOR
    m = FakeMsg("/add_fsub -1005", uid=111)
    run(fsub.add_fsub(c, m))
    assert "Added" in m.replies[-1]
    assert run(fsub.all_channels()) == [-1005]
    m = FakeMsg("/fsub_list", uid=111)
    run(fsub.fsub_list(c, m))
    assert "-1005" in m.replies[-1]
    m = FakeMsg("/del_fsub -1005", uid=111)
    run(fsub.del_fsub(c, m))
    assert run(fsub.all_channels()) == []


# ─────────────────────────── menus ───────────────────────────
@pytest.mark.parametrize("arg", ["", "premium", "clone", "help", "settings"])
def test_start_variants(arg):
    from core.menus import start_cmd
    c = FakeClient()
    m = FakeMsg(f"/start {arg}".strip(), uid=5)
    run(start_cmd(c, m))
    assert c.calls or m.replies
    if not arg:
        (_, a, k), = c.called("send_web_page")
        assert k["large_media"] and k["invert_media"] and "Uptime" in k["text"] or "uptime" in k["text"].lower()
        assert m.reactions  # random reaction like the original saver


MENU_CALLBACKS = ["start_btn", "help_btn", "about_btn", "settings_btn", "v_settings", "hub_save", "buy_premium",
                  "back_menu", "channels_info", "help_enc", "help_tools", "help_admin", "help_clone",
                  "clone_help", "clone_about", "help_saver"]


@pytest.mark.parametrize("data", MENU_CALLBACKS)
def test_menu_callbacks_render(data, caplog):
    from core.menus import menu_callbacks
    c = FakeClient()
    q = FakeQuery(data, uid=111)
    run(menu_callbacks(c, q))
    assert q.message.edits or c.called("invoke") or q.answers, data
    assert "smart_edit failed" not in caplog.text


def test_saver_settings_callbacks():
    from saver.settings import settings_callbacks
    from database.db import db
    run(db.add_user(5, "T"))
    for data in ("cmd_list_btn", "dump_chat_btn", "thumb_btn", "caption_btn", "user_stats_btn", "settings_back_btn"):
        c = FakeClient()
        q = FakeQuery(data, uid=5)
        run(settings_callbacks(c, q))
        assert q.message.edits or q.answers or c.calls, data


def test_plan_and_premium_views():
    from saver.premium import my_plan, premium_info, premium_users
    from database.db import db
    c = FakeClient()
    m = FakeMsg("/myplan", uid=5)
    run(my_plan(c, m))
    assert "Free Tier" in m.replies[-1]
    m = FakeMsg("/premium", uid=5)
    run(premium_info(c, m))
    assert m.replies
    run(db.add_user(9, "P"))
    run(db.add_premium(9, None))
    run(db.add_user(10, "Old"))
    run(db.add_premium(10, (date.today() - timedelta(days=2)).isoformat()))
    m = FakeMsg("/premium_users", uid=111)
    run(premium_users(c, m))
    assert "<code>9</code>" in m.replies[-1] and "<code>10</code>" not in m.replies[-1]


def test_cancel_command_clears_clone_flow():
    from core.menus import cancel_cmd
    from filestore.main_bot.plugins.create_bot import _creation_state
    _creation_state[5] = {"step": "awaiting_token", "data": {}}
    m = FakeMsg("/cancel", uid=5)
    run(cancel_cmd(FakeClient(), m))
    assert 5 not in _creation_state and "Cancelled" in m.replies[-1]


# ─────────────────────────── admin ───────────────────────────
def test_stats_and_watchdog_commands():
    import watchdog
    from core.admin import stats_cmd, watchdog_cmd
    c = FakeClient()
    m = FakeMsg("/stats", uid=111)
    run(stats_cmd(c, m))
    assert m.replies or m.edits
    watchdog.dog = watchdog.Watchdog(c)
    m = FakeMsg("/watchdog run", uid=111)
    run(watchdog_cmd(c, m))
    out = (m.replies + [e for r in m.replies for e in []])
    assert watchdog.dog.sweeps == 1


# ─────────────────────────── watchdog ───────────────────────────
def _touch(path, age_h, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)
    t = time.time() - age_h * 3600
    os.utime(path, (t, t))


def test_clean_dir_respects_age_and_activity():
    from watchdog import clean_dir
    base = tempfile.mkdtemp()
    try:
        _touch(f"{base}/old/a.mp4", 10, 1000)
        _touch(f"{base}/active/part1", 10)
        _touch(f"{base}/active/part2", 0.01)       # still being written → keep whole dir
        _touch(f"{base}/fresh.bin", 1)
        _touch(f"{base}/old.bin", 7, 500)
        _touch(f"{base}/.keep", 99)
        count, freed = clean_dir(base, 6)
        assert count == 2 and freed == 1500
        assert sorted(os.listdir(base)) == [".keep", "active", "fresh.bin"]
    finally:
        shutil.rmtree(base)


def test_watchdog_sweep_cleans_and_expires_states(monkeypatch):
    import config
    import watchdog
    from filestore.main_bot.plugins.create_bot import _creation_state
    from saver import session
    base = tempfile.mkdtemp()
    monkeypatch.setattr(watchdog, "TEMP_DIRS", [base])
    monkeypatch.setattr(watchdog, "ENCODER_DIRS", [])
    monkeypatch.setattr(config, "STATE_TIMEOUT_MIN", 0)
    _touch(f"{base}/123_4/video.mp4", 12, 2048)
    disconnected = []

    class TmpClient:
        async def disconnect(self):
            disconnected.append(1)

    session.LOGIN_STATE[5] = {"step": "WAITING_CODE", "data": {"client": TmpClient()}}
    _creation_state[6] = {"step": "awaiting_token", "data": {}}
    c = FakeClient()
    dog = watchdog.Watchdog(c)
    try:
        r1 = run(dog.sweep())
        assert r1["files"] == 1 and not os.listdir(base)
        time.sleep(0.01)
        r2 = run(dog.sweep())   # second sighting after the (0 min) timeout → dropped
        assert r1["states_dropped"] + r2["states_dropped"] == 2
        assert 5 not in session.LOGIN_STATE and 6 not in _creation_state and disconnected
        assert any("timed out" in x[1][1] for x in c.called("send_message"))
        assert r2["telegram_ok"] and r2["ram_mb"] > 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_watchdog_restarts_after_three_failed_checks(monkeypatch):
    import watchdog
    c = FakeClient()
    c.fail.add("get_me")
    called = []
    monkeypatch.setattr(watchdog.os, "execl", lambda *a: called.append(a))
    dog = watchdog.Watchdog(c)
    for _ in range(3):
        run(dog.check_connection())
    assert called and dog.conn_failures == 3


def test_watchdog_heals_missing_clone(monkeypatch):
    import watchdog
    from filestore.database.main_db import MainDB
    from filestore.worker_bot.engine import worker_engine
    started = []

    async def fake_start(doc):
        started.append(doc["_id"])
        worker_engine.workers[doc["_id"]] = SimpleNamespace(is_connected=True)

    monkeypatch.setattr(worker_engine, "start_worker", fake_start)
    run(MainDB().bots.insert_one({"_id": 4242, "owner_id": 5, "is_active": True, "bot_username": "x_bot"}))
    run(MainDB().bots.insert_one({"_id": 4343, "owner_id": 5, "is_active": False}))
    dog = watchdog.Watchdog(FakeClient())
    try:
        assert run(dog.heal_workers()) == 1 and started == [4242]
        assert run(dog.heal_workers()) == 0      # already running
    finally:
        worker_engine.workers.pop(4242, None)


def test_watchdog_worker_backoff(monkeypatch):
    import watchdog
    from filestore.database.main_db import MainDB
    from filestore.worker_bot.engine import worker_engine

    async def boom(doc):
        raise RuntimeError("token revoked")

    monkeypatch.setattr(worker_engine, "start_worker", boom)
    run(MainDB().bots.insert_one({"_id": 5151, "owner_id": 5, "is_active": True}))
    dog = watchdog.Watchdog(FakeClient())
    dog.sweeps = 1
    for _ in range(4):
        run(dog.heal_workers())
    assert dog.worker_failures[5151] == 3      # 4th call skipped by back-off


# ─────────────────────────── keep-alive ───────────────────────────
def test_keep_alive_health_endpoint(monkeypatch):
    import aiohttp
    import config
    import keep_alive
    monkeypatch.setattr(config, "PORT", 18931)

    async def go():
        await keep_alive.start_server()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get("http://127.0.0.1:18931/health") as r:
                    data = await r.json()
                async with s.get("http://127.0.0.1:18931/") as r:
                    root = await r.text()
        finally:
            await keep_alive.stop_server()
        return data, root

    data, root = run(go())
    assert data["status"] == "ok" and "clone_bots_running" in data and "running" in root


# ─────────────────────────── errors / inline ───────────────────────────
def test_error_handler_reports_once_per_minute():
    from core import errors
    errors._last_sent.clear()
    c = FakeClient()
    upd = FakeMsg("/boom", uid=5)
    run(errors.on_error(c, upd, ValueError("kaboom")))
    run(errors.on_error(c, upd, ValueError("kaboom")))
    from pyrogram.errors import MessageNotModified
    run(errors.on_error(c, upd, MessageNotModified()))
    assert len(c.called("send_message")) == 1


def test_inline_share_and_qr(monkeypatch):
    from core import inline

    async def fake_short(url):
        return [("is.gd", "https://is.gd/abc")]

    monkeypatch.setattr(inline, "_shorten", fake_short)
    got = {}

    class IQ:
        def __init__(self, q):
            self.query = q
            self.from_user = SimpleNamespace(id=5)

        async def answer(self, results, **k):
            got[self.query] = results

    c = FakeClient()
    run(inline.inline_handler(c, IQ("")))
    run(inline.inline_handler(c, IQ("https://example.com/<x>")))
    assert len(got[""]) == 1
    assert len(got["https://example.com/<x>"]) == 2
    assert "&lt;x&gt;" in got["https://example.com/<x>"][1].caption


# ─────────────────────────── owner log channel ───────────────────────────
def _logs(c, tag=None, chat=None):
    out = []
    for name, a, k in c.calls:
        if name != "send_message":
            continue
        text = a[1] if len(a) > 1 else k.get("text", "")
        if tag and f"#{tag}" not in text:
            continue
        if chat is not None and a[0] != chat:
            continue
        out.append((a[0], text))
    return out


def test_log_targets_channel_and_owner_dm():
    from core import botlog
    c = FakeClient()
    run(botlog.event("CloneCreated", "x", client=c))      # in OWNER_DM_EVENTS
    run(botlog.event("Ban", "y", client=c))               # channel only
    assert {chat for chat, _ in _logs(c, "CloneCreated")} == {-100123, 111}
    assert {chat for chat, _ in _logs(c, "Ban")} == {-100123}
    assert "🕒" in _logs(c, "Ban")[0][1]


def test_log_falls_back_to_owner_dm_without_channel(monkeypatch):
    import config
    from core import botlog
    monkeypatch.setattr(config, "LOG_CHANNEL", 0)
    c = FakeClient()
    run(botlog.event("Ban", "y", client=c))
    assert [chat for chat, _ in _logs(c, "Ban")] == [111]


def test_log_never_raises_on_send_failure():
    from core import botlog
    c = FakeClient()
    c.fail.add("send_message")
    run(botlog.event("NewUser", "z", client=c))   # must not raise


def test_new_user_log_has_details_and_source():
    from core.middleware import track_users
    c = FakeClient()
    run(track_users(c, FakeMsg("/start ref_abc", uid=88)))
    (chat, text), = _logs(c, "NewUser")
    assert chat == -100123
    assert "<code>88</code>" in text and "@tester" in text and "ref_abc" in text and "Total users" in text


def test_start_log_for_returning_users_is_throttled():
    from core.menus import start_cmd
    from core.middleware import track_users
    c = FakeClient()
    run(track_users(c, FakeMsg("/start", uid=90)))
    run(start_cmd(c, FakeMsg("/start", uid=90)))       # same visit as #NewUser → no #Start
    assert not _logs(c, "Start")
    from core import botlog
    botlog._start_seen.clear()                          # cooldown passed
    run(start_cmd(c, FakeMsg("/start clone", uid=90)))
    run(start_cmd(c, FakeMsg("/start", uid=90)))        # within cooldown
    starts = _logs(c, "Start")
    assert len(starts) == 1 and "clone" in starts[0][1]


def test_clone_event_helper_and_boot_report():
    from core import botlog
    from filestore.main_bot.plugins.my_bots import _clone_log
    from tests.harness import FakeUser
    c = FakeClient()
    run(_clone_log(c, "CloneDeleted", FakeUser(5), {"_id": 77, "bot_username": "my_store_bot"}))
    logs = _logs(c, "CloneDeleted")
    assert {x[0] for x in logs} == {-100123, 111} and "@my_store_bot" in logs[0][1]
    botlog.set_restart_reason("Restart by Owner (111)")
    run(botlog.boot_report(c, c.me, handlers=170, boot_seconds=3.2))
    boot = _logs(c, "BotStarted")
    assert {x[0] for x in boot} == {-100123, 111}
    assert "@VidelBot" in boot[0][1] and "Restart by Owner" in boot[0][1] and "Handlers" in boot[0][1]
    assert botlog.pop_restart_reason() == ""


def test_daily_report_counts():
    from core import botlog
    from core.middleware import track_users
    c = FakeClient()
    run(track_users(c, FakeMsg("/start", uid=301)))
    run(track_users(c, FakeMsg("/start", uid=302)))
    body = run(botlog.daily_report(c))
    assert "New users: <code>2</code>" in body
    assert _logs(c, "DailyReport")


def test_login_logout_logged_with_masked_phone():
    from saver import session
    from database.db import db
    c = FakeClient()
    from core import botlog
    botlog.bind(c)
    run(db.add_user(5, "T"))
    m = FakeMsg("/logout", uid=5)
    run(session.logout(c, m))
    assert _logs(c, "Logout")
    assert session._mask_phone("+919876543210") == "+91•••••••210"
    run(session._log_session("Login", 5, None, "+919876543210"))
    (_, text), = _logs(c, "Login")
    assert "9876543210" not in text and "+91" in text


def test_admin_actions_are_logged():
    from core.admin import ban_cmd, maintenance_cmd, unban_cmd
    from saver.premium import add_premium_admin, remove_premium_admin
    c = FakeClient()
    run(ban_cmd(c, FakeMsg("/ban 55 spam", uid=111)))
    run(unban_cmd(c, FakeMsg("/unban 55", uid=111)))
    run(maintenance_cmd(c, FakeMsg("/maintenance on", uid=111)))
    run(add_premium_admin(c, FakeMsg("/add_premium 55 30", uid=111)))
    run(remove_premium_admin(c, FakeMsg("/remove_premium 55", uid=111)))
    for tag in ("Ban", "Unban", "Maintenance", "PremiumAdded", "PremiumRemoved"):
        assert _logs(c, tag), tag


def test_stars_payment_logged_and_dm():
    from core.payments import successful_payment
    c = FakeClient()
    run(successful_payment(c, _payment_msg(30)))
    logs = _logs(c, "StarsPayment")
    assert {x[0] for x in logs} == {-100123, 111} and "Charge ID" in logs[0][1]


def test_clone_link_logs_are_routed_and_can_be_disabled(monkeypatch):
    import config
    from filestore.utils import helpers
    c = FakeClient()
    monkeypatch.setattr(helpers, "main_bot_client", None)
    run(helpers.send_main_log(c, "<b>🔗 Link Generated</b>\n• Bot: @x"))
    assert _logs(c, "LinkGenerated")
    monkeypatch.setattr(config, "LOG_LINKS", False)
    n = len(c.calls)
    run(helpers.send_main_log(c, "<b>🔗 Link Generated</b>"))
    assert len(c.calls) == n


def test_watchdog_low_disk_alert_logged():
    import watchdog
    c = FakeClient()
    dog = watchdog.Watchdog(c)
    run(dog._alert_disk(0.5))
    run(dog._alert_disk(0.4))    # rate-limited
    assert len([x for x in _logs(c, "LowDisk") if x[0] == -100123]) == 1


def test_logtest_command():
    from core.botlog import logtest_cmd
    c = FakeClient()
    m = FakeMsg("/logtest", uid=111)
    run(logtest_cmd(c, m))
    assert _logs(c, "LogTest") and "-100123" in m.replies[-1]


# ─────────────────────────── command menus ───────────────────────────
def test_register_commands_all_scopes():
    from core.commands import ADMIN_COMMANDS, OWNER_COMMANDS, USER_COMMANDS, register_commands
    c = FakeClient()
    done = run(register_commands(c))
    assert set(done) == {"default", "private", "groups", "user:111", "user:222"}
    by_scope = {type(k["scope"]).__name__ + str(getattr(k["scope"], "chat_id", "")): a[0]
                for _, a, k in c.called("set_bot_commands")}
    owner = by_scope["BotCommandScopeChat111"]
    admin = by_scope["BotCommandScopeChat222"]
    assert len(owner) == len(USER_COMMANDS) + len(ADMIN_COMMANDS) + len(OWNER_COMMANDS) <= 100
    assert "refund" in [x.command for x in owner] and "refund" not in [x.command for x in admin]
    assert "broadcast" in [x.command for x in admin]
    assert "broadcast" not in [x.command for x in by_scope["BotCommandScopeDefault"]]


def test_every_menu_command_has_a_handler():
    import re
    from core.commands import ADMIN_COMMANDS, GROUP_COMMANDS, OWNER_COMMANDS, USER_COMMANDS
    handled = set()
    for root, _, files in os.walk("."):
        if "tests" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                src = open(os.path.join(root, f), encoding="utf-8").read()
                for m in re.finditer(r"(?<!~)filters\.command\((\[[^\]]*\]|[^)]*)\)", src):
                    handled |= set(re.findall(r"[\"']([a-z_0-9]+)[\"']", m.group(1)))
    menu = {c for g in (USER_COMMANDS, GROUP_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS) for c, _ in g}
    assert menu <= handled, menu - handled


def test_public_stats_for_non_admins():
    from VideoEncoder.plugins.start import show_status_count
    c = FakeClient()
    m = FakeMsg("/stats", uid=5)
    run(show_status_count(c, m))
    assert "CPU" in m.replies[-1] and "Users" in m.replies[-1]


def test_check_clone_health(monkeypatch):
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins.owner_cmds import check_bots
    from filestore.worker_bot.engine import worker_engine

    class Alive:
        async def get_me(self):
            return SimpleNamespace(id=1)

    class Dead:
        async def get_me(self):
            raise ConnectionError("gone")

    run(MainDB().bots.insert_one({"_id": 1, "bot_username": "ok_bot", "is_active": True, "owner_id": 5}))
    run(MainDB().bots.insert_one({"_id": 2, "bot_username": "dead_bot", "is_active": True, "owner_id": 5}))
    run(MainDB().bots.insert_one({"_id": 3, "bot_username": "gone_bot", "is_active": True, "owner_id": 5}))
    run(MainDB().bots.insert_one({"_id": 4, "bot_username": "off_bot", "is_active": False, "owner_id": 5}))
    worker_engine.workers.update({1: Alive(), 2: Dead()})
    try:
        c = FakeClient()
        m = FakeMsg("/check", uid=111)
        run(check_bots(c, m))
        report = m.sent[-1].edits[-1]
        assert "reachable: <code>1</code>" in report and "responding: <code>1</code>" in report
        assert "not running: <code>1</code>" in report and "hibernated: <code>1</code>" in report
        assert "dead_bot" in report and "gone_bot" in report and "/check fix" in report
    finally:
        worker_engine.workers.pop(1, None)
        worker_engine.workers.pop(2, None)

"""Phase 10 – clone lifecycle (7-day inactivity → deactivated), Bot API 10.3 disabled buttons,
professional home screen, faster /start."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, enable_blocking_logs, load_all, reset_db, run

MODULES = load_all()
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    yield


def _naive(dt):                     # mongomock stores naive UTC datetimes
    return dt.replace(tzinfo=None)


class Engine:
    def __init__(self):
        self.stopped, self.started, self.workers = [], [], {}

    async def stop_worker(self, bot_id):
        self.stopped.append(bot_id)
        self.workers.pop(bot_id, None)

    async def start_worker(self, bot):
        self.started.append(bot["_id"])
        self.workers[bot["_id"]] = object()

    def get_worker(self, bot_id):
        return self.workers.get(bot_id)


def _bot(bot_id, idle_days, owner=5, **extra):
    from filestore.database.main_db import MainDB
    doc = {"_id": bot_id, "bot_username": f"b{bot_id}_bot", "owner_id": owner, "is_active": True,
           "last_active": _naive(NOW - timedelta(days=idle_days)), "created_at": _naive(NOW - timedelta(days=30))}
    doc.update(extra)
    run(MainDB().bots.insert_one(doc))


def _doc(bot_id):
    from filestore.database.main_db import MainDB
    return run(MainDB().bots.find_one({"_id": bot_id}))


def _markup_data(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


# ───────────────────────── lifecycle ─────────────────────────
def test_sweep_warns_then_deactivates_after_seven_days(monkeypatch):
    import config
    from filestore.main_bot.plugins import clone_lifecycle as cl
    monkeypatch.setattr(config, "CLONE_INACTIVE_DAYS", 7)
    enable_blocking_logs()
    _bot(1, 8)          # over the limit
    _bot(2, 6.5)        # inside the warning window
    _bot(3, 1)          # busy
    eng, app = Engine(), FakeClient()
    done = run(cl.sweep(app, eng, now=NOW))
    assert done == {"warned": [2], "deactivated": [1]}
    assert eng.stopped == [1]
    d1 = _doc(1)
    assert d1["is_active"] is False and d1["deactivated_reason"] == "inactive" and d1.get("deactivated_at")
    assert _doc(2).get("inactive_warned") and _doc(2)["is_active"] is True
    assert _doc(3)["is_active"] is True and not _doc(3).get("inactive_warned")
    sent = [c for c in app.called("send_message") if c[1][0] == 5]
    datas = [d for c in sent for d in _markup_data(c[2]["reply_markup"])]
    assert "cl_on_1" in datas and "cl_keep_2" in datas
    logged = " ".join(str(c[1][1]) for c in app.called("send_message") if len(c[1]) > 1)
    assert "#CloneDeactivated" in logged
    # the warning is sent once, the deactivated clone is no longer swept
    done = run(cl.sweep(app, eng, now=NOW + timedelta(hours=1)))
    assert done == {"warned": [], "deactivated": []}


def test_sweep_disabled_with_zero_days(monkeypatch):
    import config
    from filestore.main_bot.plugins import clone_lifecycle as cl
    monkeypatch.setattr(config, "CLONE_INACTIVE_DAYS", 0)
    _bot(1, 100)
    assert run(cl.sweep(FakeClient(), Engine(), now=NOW)) == {"warned": [], "deactivated": []}
    assert _doc(1)["is_active"] is True


def test_legacy_docs_without_last_active_use_created_at(monkeypatch):
    import config
    from filestore.main_bot.plugins import clone_lifecycle as cl
    monkeypatch.setattr(config, "CLONE_INACTIVE_DAYS", 7)
    _bot(1, 0, last_active=None)          # created 30 days ago, never used
    assert run(cl.sweep(FakeClient(), Engine(), now=NOW))["deactivated"] == [1]


def test_reactivating_restarts_the_inactivity_clock(monkeypatch):
    """Old bug: a woken clone kept its old last_active and was switched off again by the next sweep."""
    import config
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins import clone_lifecycle as cl
    monkeypatch.setattr(config, "CLONE_INACTIVE_DAYS", 7)
    _bot(1, 10, is_active=False, deactivated_reason="inactive", inactive_warned=_naive(NOW))
    run(MainDB().set_bot_active(1, True))
    d = _doc(1)
    assert d["is_active"] and "deactivated_reason" not in d and "inactive_warned" not in d
    assert run(cl.sweep(FakeClient(), Engine()))["deactivated"] == []


def test_activity_clears_the_warning():
    from filestore.database.main_db import MainDB
    _bot(1, 6.5, inactive_warned=_naive(NOW))
    MainDB._last_active_written.pop(1, None)
    run(MainDB().update_last_active(1))
    assert "inactive_warned" not in _doc(1)


def test_reactivate_button_is_owner_only_and_starts_the_worker(monkeypatch):
    from filestore.main_bot.plugins import clone_lifecycle as cl
    from filestore.worker_bot import engine as eng_mod
    eng = Engine()
    monkeypatch.setattr(eng_mod, "worker_engine", eng)
    _bot(1, 9, is_active=False, deactivated_reason="inactive")
    stranger = FakeQuery("cl_on_1", uid=77)
    run(cl.reactivate_cb(FakeClient(), stranger))
    assert stranger.answers[-1][1] is True and eng.started == []
    q = FakeQuery("cl_on_1", uid=5)
    run(cl.reactivate_cb(FakeClient(), q))
    assert eng.started == [1] and _doc(1)["is_active"] is True
    assert "back online" in q.message.edits[-1]


def test_keep_running_resets_the_timer(monkeypatch):
    from filestore.main_bot.plugins import clone_lifecycle as cl
    _bot(1, 6.5, inactive_warned=_naive(NOW))
    q = FakeQuery("cl_keep_1", uid=5)
    run(cl.keep_running_cb(FakeClient(), q))
    d = _doc(1)
    assert "inactive_warned" not in d
    assert _naive(d["last_active"]) > _naive(datetime.now(timezone.utc) - timedelta(minutes=1))


def test_dashboard_and_list_show_idle_state(monkeypatch):
    from filestore.main_bot.plugins.my_bots import dashboard_callback, my_bots_callback
    _bot(4242, 9, is_active=False, deactivated_reason="inactive")
    q = FakeQuery("dashboard_4242", uid=5)
    run(dashboard_callback(FakeClient(), q))
    assert "ᴅᴇᴀᴄᴛɪᴠᴀᴛᴇᴅ" in q.message.edits[-1] and "ʟᴀsᴛ ᴜsᴇᴅ" in q.message.edits[-1]
    q = FakeQuery("my_bots", uid=5)
    run(my_bots_callback(FakeClient(), q))
    assert "💤" in q.message.edits[-1]


def test_status_helpers():
    from filestore.main_bot.plugins.clone_lifecycle import ago, status_of, warn_after
    assert status_of({}, True)[0] == "🟢"
    assert status_of({"deactivated_reason": "inactive"}, False)[0] == "💤"
    assert status_of({}, False)[0] == "🔴"
    assert warn_after(timedelta(days=7)) == timedelta(days=6)
    assert warn_after(timedelta(days=1)) == timedelta(hours=18)
    assert ago(timedelta(days=2, hours=3)) == "2d 3h ago" and ago(timedelta(seconds=5)) == "just now"


# ───────────────────────── Bot API 10.3 ─────────────────────────
def test_noop_buttons_become_disabled_buttons():
    from pyrogram.types import InlineKeyboardButton as B, InlineKeyboardMarkup as K
    from core import botapi
    kb = botapi.convert_markup(K([[B("✅ Premium · active", callback_data="noop:plan"),
                                   B("⭐ 50 · 30 days", callback_data="stars_buy:30")]]))
    chip, buy = kb.inline_keyboard[0]
    assert chip.disabled is not None and chip.callback_data is None and chip.style is None
    assert buy.callback_data == "stars_buy:30" and buy.disabled is None
    assert kb.model_dump(exclude_none=True)["inline_keyboard"][0][0] == {"text": "✅ Premium · active", "disabled": {}}
    assert not botapi.is_noop("noopish") and botapi.is_noop("noop") and botapi.is_noop("noop:x")


def test_noop_fallback_handler_answers_silently():
    from core.menus import noop_callback
    q = FakeQuery("noop:plan")
    run(noop_callback(FakeClient(), q))
    assert q.answers == [(None, False)]


def test_premium_screen_shows_plan_chip_only_for_premium():
    from core.menus import premium_kb
    assert "noop:plan" not in _markup_data(premium_kb())
    assert _markup_data(premium_kb(datetime(2026, 12, 1)))[0] == "noop:plan"
    assert "Lifetime" in premium_kb("Permanent").inline_keyboard[0][0].text


# ───────────────────────── home screen / speed ─────────────────────────
def test_home_text_is_honest_and_shows_the_plan():
    from core import texts
    from core.menus import start_text
    assert "10x" not in texts.START_TXT and "End-to-End" not in texts.START_TXT
    assert "High-Speed Server" not in texts.ABOUT_TXT
    c = FakeClient()
    text = run(start_text(c, FakeMsg(uid=5).from_user))
    assert "Free" in text and "Uptime" in text


def test_home_keyboard_keeps_every_callback():
    from core.menus import home_kb
    data = set(_markup_data(home_kb()))
    assert {"back_menu", "help_enc", "help_rename", "help_tools", "settings_btn", "help_btn",
            "buy_premium", "refer_btn", "about_btn"} <= data


def test_start_sends_one_draft_and_no_typewriter_delay():
    import time
    from core.menus import start_cmd
    from tests.test_phase4 import _drafts
    c = FakeClient()
    m = FakeMsg("/start", uid=5, client=c)
    t0 = time.perf_counter()
    run(start_cmd(c, m))
    assert time.perf_counter() - t0 < 0.4           # the 3-step typewriter used to sleep 0.5 s
    assert len(_drafts(c)) == 1


def test_about_shows_api_version_and_uptime():
    from core.menus import about_text
    text = run(about_text(FakeClient()))
    assert "Bot API 10." in text and "Uptime" in text


def test_clone_help_mentions_the_idle_rule(monkeypatch):
    import config
    from core.menus import clone_help_text
    monkeypatch.setattr(config, "CLONE_INACTIVE_DAYS", 7)
    t = clone_help_text()
    assert "7 ᴅᴀʏs" in t and "{idle_days}" not in t
    monkeypatch.setattr(config, "CLONE_INACTIVE_DAYS", 0)
    t = clone_help_text()
    assert "💤" not in t and "{idle_days}" not in t and "ʜᴏᴡ ᴛᴏ ᴄʀᴇᴀᴛᴇ" in t

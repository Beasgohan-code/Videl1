"""Phase 8 – clone feature packs A–D (smart links, premium, index/search, analytics, broadcasts,
requests, file buttons, anti-flood, help/about), ownership transfer, restore keys, purge prefix fix."""
import asyncio
import re
from datetime import timedelta
from types import SimpleNamespace

import pytest

from tests.harness import FakeClient, FakeQuery, load_all, reset_db, run
from tests.test_audit import LOG_CH, Msg, Stored, WorkerClient, _dispatch, _start_worker, _stop

MODULES = load_all()


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    yield


# ───────────────────────── pure helpers ─────────────────────────
def test_parse_duration_and_human():
    from filestore.worker_bot.extras import human_duration, parse_duration
    assert parse_duration("30m") == 1800
    assert parse_duration("1d12h") == 36 * 3600
    assert parse_duration("2w") == 14 * 86400
    for bad in ("", "abc", "10", "5x", "400d", "1h extra"):
        assert parse_duration(bad) is None, bad
    assert human_duration(90061) == "1d 1h 1m"
    assert human_duration(5) == "<1m"


def test_parse_smartlink_options():
    from filestore.worker_bot.extras import parse_smartlink_options
    o = parse_smartlink_options(["24h", "x50", "pass=abc", "stars=25", "note=Season1"])
    assert o == {"expires": 86400, "max_uses": 50, "password": "abc", "stars": 25, "note": "Season1"}
    assert parse_smartlink_options([])["max_uses"] == 0
    for bad in (["stars=0"], ["stars=abc"], ["x0"], ["pass="], ["wat"], ["uses=-1"]):
        with pytest.raises(ValueError):
            parse_smartlink_options(bad)


def test_parse_buttons():
    from filestore.worker_bot.extras import file_buttons_markup, parse_buttons
    rows = parse_buttons("Channel - https://t.me/x | Group - https://t.me/y\nSite - https://a.b")
    assert rows == [[["Channel", "https://t.me/x"], ["Group", "https://t.me/y"]], [["Site", "https://a.b"]]]
    kb = file_buttons_markup({"file_buttons": rows})
    assert kb.inline_keyboard[0][1].url == "https://t.me/y"
    assert file_buttons_markup({}) is None
    for bad in ("no dash here", "X - notalink", "A - https://a | B - https://b | C - https://c | D - https://d", ""):
        with pytest.raises(ValueError):
            parse_buttons(bad)


def test_parse_when_and_sparkline():
    from filestore.database.extras_db import now
    from filestore.worker_bot.extras import parse_when, sparkline
    t = parse_when(["pin", "in", "2h"], "Asia/Kolkata")
    assert timedelta(minutes=119) < t - now() <= timedelta(hours=2)
    t = parse_when(["at", "21:30"], "Asia/Kolkata")
    assert timedelta(0) < t - now() <= timedelta(days=1) and t.tzinfo is None
    assert parse_when(["pin", "silent"], "UTC") is None
    for bad in (["in", "soon"], ["at", "25:00"], ["at", "9pm"]):
        with pytest.raises(ValueError):
            parse_when(bad, "UTC")
    assert sparkline([0, 0]) == "▁▁"
    assert sparkline([1, 8])[-1] == "█"


def test_extract_payload_accepts_links_and_rejects_junk():
    from filestore.utils.helpers import encode
    from filestore.worker_bot.extras import extract_payload
    code = run(encode(f"get-{5 * abs(LOG_CH)}"))
    assert run(extract_payload(code)) == code
    assert run(extract_payload(f"https://t.me/CloneBot?start={code}")) == code
    assert run(extract_payload("https://example.com")) is None
    assert run(extract_payload(run(encode("hello-world")))) is None


def test_settings_panel_and_toggle_defaults():
    from filestore.worker_bot.extras import TOGGLE_KEYS, setting, settings_panel
    assert setting({}, "requests") is True and setting({}, "search_public") is False
    assert setting({"requests": False}, "requests") is False
    rows = settings_panel({}, "xtg_1_")
    datas = [b.callback_data for r in rows for b in r]
    assert all(len(d.encode()) <= 64 for d in datas)
    assert {d.split("_", 2)[2] for d in datas} == set(TOGGLE_KEYS)


# ───────────────────────── storage layer ─────────────────────────
def test_claim_use_limit_is_per_user():
    from filestore.database.extras_db import CloneExtras
    x = CloneExtras(77)
    link = run(x.create_link("Z2V0LTE", 1, max_uses=2))
    assert run(x.claim_use(link, 10)) and run(x.claim_use(link, 11))
    assert not run(x.claim_use(run(x.get_link(link["_id"])), 12))      # third user blocked
    assert run(x.claim_use(run(x.get_link(link["_id"])), 10))          # first user may reopen
    assert run(x.get_link(link["_id"]))["uses"] == 2


def test_password_is_hashed_and_checked():
    from filestore.database.extras_db import CloneExtras
    x = CloneExtras(77)
    link = run(x.create_link("Z2V0LTE", 1, password="s3cret"))
    assert link["password"] != "s3cret"
    assert x.check_password(link, " s3cret ") and not x.check_password(link, "nope")
    assert not x.check_password(run(x.create_link("Z2V0LTE", 1)), "")


def test_premium_extends_and_expires():
    from filestore.database.extras_db import CloneExtras, now
    x = CloneExtras(77)
    first = run(x.add_premium(9, 10))
    second = run(x.add_premium(9, 5))
    assert timedelta(days=14) < second - now() <= timedelta(days=15) and second > first
    assert run(x.is_premium(9))
    run(x.premium.update_one({"_id": 9}, {"$set": {"until": now() - timedelta(seconds=1)}}))
    assert not run(x.is_premium(9)) and run(x.premium.count_documents({})) == 0
    assert run(x.add_premium(8, 0)) is None and run(x.premium_until(8)) == (True, None)


def test_search_matches_all_words():
    from filestore.database.extras_db import CloneExtras
    x = CloneExtras(77)
    for i, name in enumerate(["Naruto S01E01 720p.mkv", "Naruto S01E02 1080p.mkv", "One Piece 720p.mp4"], 1):
        run(x.files.insert_one({"_id": i, "name": name, "name_lc": name.lower()}))
    docs, total = run(x.search("naruto 720p"))
    assert total == 1 and docs[0]["_id"] == 1
    assert run(x.search("720P"))[1] == 2
    assert run(x.search("(.*"))[1] == 0            # regex characters are escaped


def test_purge_does_not_touch_bots_with_longer_ids():
    from filestore.database.worker_db import WorkerDB
    a, b = WorkerDB(12), WorkerDB(123)
    run(a.add_user(1))
    run(b.add_user(2))
    run(a.drop_all_collections())
    assert run(b.present_user(2)), "purging bot 12 deleted bot 123's users"


def test_restore_accepts_backup_key_names():
    import inspect
    from filestore.main_bot.plugins import create_bot
    src = inspect.getsource(create_bot)
    for key in ("start_message", "custom_caption", "force_pic", "file_buttons", '"start_msg": "start_message"'):
        assert key in src


# ───────────────────────── clone worker, end to end ─────────────────────────
def _link(n=77):
    from filestore.utils.helpers import encode
    return run(encode(f"get-{n * abs(LOG_CH)}"))


def _make_smartlink(app, c, opts=""):
    m = Msg(f"/smartlink https://t.me/CloneBot?start={_link()} {opts}".strip(), uid=5, client=c)
    run(_dispatch(app, c, m))
    assert "Smart link created" in m.replies[-1], m.replies
    return re.search(r"start=sl_(\w+)", m.replies[-1]).group(1)


def test_smartlink_user_limit(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        store = {}
        c = WorkerClient(store)
        code = _make_smartlink(app, c, "x1 note=Ep1")
        u9 = Msg(f"/start sl_{code}", uid=9, client=c)
        run(_dispatch(app, c, u9))
        assert store[77].copies == [9]
        u10 = Msg(f"/start sl_{code}", uid=10, client=c)
        run(_dispatch(app, c, u10))
        assert "reached its limit" in u10.replies[-1] and store[77].copies == [9]
        run(_dispatch(app, c, Msg(f"/start sl_{code}", uid=9, client=c)))
        assert store[77].copies == [9, 9]
    finally:
        _stop(app)


def test_smartlink_password_flow(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        store = {}
        c = WorkerClient(store)
        code = _make_smartlink(app, c, "pass=open123")
        start = Msg(f"/start sl_{code}", uid=9, client=c)
        run(_dispatch(app, c, start))
        assert "password" in start.replies[-1] and 77 not in store
        wrong = Msg("nope", uid=9, client=c)
        run(_dispatch(app, c, wrong))
        assert "Wrong password" in wrong.replies[-1]
        right = Msg("open123", uid=9, client=c)
        run(_dispatch(app, c, right))
        assert right.deleted and store[77].copies == [9]
    finally:
        _stop(app)


def test_smartlink_stars_sends_invoice_and_expiry(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        code = _make_smartlink(app, c, "stars=15")
        run(_dispatch(app, c, Msg(f"/start sl_{code}", uid=9, client=c)))
        inv = c.called("send_invoice")
        assert inv and inv[0][2]["currency"] == "XTR" and inv[0][2]["prices"][0].amount == 15
        assert inv[0][2]["payload"].startswith(f"xl:{code}:9:")

        from filestore.database.extras_db import CloneExtras, now
        x = CloneExtras(4242)
        old = run(x.create_link(_link(), 5, expires_at=now() - timedelta(minutes=1)))
        m = Msg(f"/start sl_{old['_id']}", uid=9, client=c)
        run(_dispatch(app, c, m))
        assert "expired" in m.replies[-1]
        gone = Msg("/start sl_doesnotexist", uid=9, client=c)
        run(_dispatch(app, c, gone))
        assert "removed" in gone.replies[-1]
    finally:
        _stop(app)


def test_non_admin_cannot_make_smartlinks(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        m = Msg(f"/smartlink {_link()} x5", uid=9, client=c)
        run(_dispatch(app, c, m))
        assert not m.replies
    finally:
        _stop(app)


def test_requests_and_daily_limit(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        m = Msg("/request Naruto season 2 please", uid=9, client=c)
        run(_dispatch(app, c, m))
        from filestore.database.extras_db import CloneExtras
        x = CloneExtras(4242)
        reqs = run(x.open_requests())
        assert len(reqs) == 1 and reqs[0]["text"] == "Naruto season 2 please"
        for _ in range(3):
            last = Msg("/request more", uid=9, client=c)
            run(_dispatch(app, c, last))
        assert run(x.requests_today(9)) == 3 and "3 requests a day" in last.replies[-1]
        inbox = Msg("/requests", uid=5, client=c)
        run(_dispatch(app, c, inbox))
        assert "Naruto" in str(inbox.replies)
    finally:
        _stop(app)


def test_channel_posts_are_indexed_and_searchable(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        post = Msg(None, uid=0, chat_id=LOG_CH, client=c)
        post.chat.type = __import__("pyrogram").enums.ChatType.CHANNEL
        post.document = SimpleNamespace(file_name="Naruto S01E05 [720p].mkv", file_size=5 * 2 ** 20,
                                        mime_type="video/x-matroska")
        post.media = __import__("pyrogram").enums.MessageMediaType.DOCUMENT
        run(_dispatch(app, c, post))
        from filestore.database.extras_db import CloneExtras
        assert run(CloneExtras(4242).indexed_count()) == 1
        s = Msg("/search naruto 720p", uid=5, client=c)
        run(_dispatch(app, c, s))
        assert "1 result" in str(s.replies)       # results are buttons under this message
        # public search is off by default → normal users are told so
        u = Msg("/search naruto", uid=9, client=c)
        run(_dispatch(app, c, u))
        assert u.replies and "result" not in str(u.replies)
    finally:
        _stop(app)


def test_antiflood_blocks_spammers_but_not_admins(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        msgs = [Msg("/help", uid=9, client=c) for _ in range(12)]
        for m in msgs:
            run(_dispatch(app, c, m))
        assert any("Slow down" in str(m.replies) for m in msgs)
        assert not msgs[-1].replies                        # blocked silently afterwards
        owner = [Msg("/help", uid=5, client=c) for _ in range(12)]
        for m in owner:
            run(_dispatch(app, c, m))
        assert not any("Slow down" in str(m.replies) for m in owner)
    finally:
        _stop(app)


def test_setbuttons_and_custom_help(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        run(_dispatch(app, c, Msg("/setbuttons Join - https://t.me/mychan", uid=5, client=c)))
        run(_dispatch(app, c, Msg("/sethelp Hi {first}, ask in the group!", uid=5, client=c)))
        from filestore.database.main_db import MainDB
        s = run(MainDB().get_bot(4242))["settings"]
        assert s["file_buttons"] == [["Join", "https://t.me/mychan"]] or s["file_buttons"] == [[["Join", "https://t.me/mychan"]]]
        h = Msg("/help", uid=9, client=c)
        run(_dispatch(app, c, h))
        assert "ask in the group" in str(h.replies) and "{first}" not in str(h.replies)
    finally:
        _stop(app)


# ───────────────────────── main bot: ✨ Extras + ownership transfer ─────────────────────────
def _cb(handler_name, data, uid=5):
    from filestore.main_bot.plugins import clone_extras_panel as p
    q = FakeQuery(data, uid=uid)
    q.matches = [re.match(r"^\w+?_(\d+)(?:_(\w+))?$", data)]
    run(getattr(p, handler_name)(FakeClient(), q))
    return q


def test_extras_panel_toggle_and_access(monkeypatch):
    from filestore.database.main_db import MainDB
    run(MainDB().add_bot(4242, 5, "enc", "CloneBot", LOG_CH))
    q = _cb("extras_toggle_cb", "xtg_4242_search_public")
    assert run(MainDB().get_bot(4242))["settings"]["search_public"] is True
    assert q.message.edits and "EXTRA" in q.message.edits[-1].upper() or "𝗘𝗫𝗧𝗥𝗔" in q.message.edits[-1]
    stranger = _cb("extras_toggle_cb", "xtg_4242_requests", uid=6)
    assert stranger.answers[-1][1] is True and "requests" not in run(MainDB().get_bot(4242))["settings"]
    a = _cb("extras_analytics_cb", "xan_4242")
    assert "CloneBot" in a.message.edits[-1] or a.message.edits


def test_ownership_transfer(monkeypatch):
    from database.db import db as saver_db
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins import clone_extras_panel as p
    from filestore.main_bot.plugins.bot_settings import _get_state
    run(MainDB().add_bot(4242, 5, "enc", "CloneBot", LOG_CH))
    _cb("transfer_owner_cb", "xown_4242")
    state = _get_state()[5]
    assert state["step"] == "awaiting_new_owner"

    unknown = Msg("777", uid=5)
    run(p.handle_new_owner_input(FakeClient(), unknown, state))
    assert "hasn't started" in unknown.replies[-1]
    run(saver_db.add_user(777, "New"))
    ok = Msg("777", uid=5)
    run(p.handle_new_owner_input(FakeClient(), ok, state))
    assert "Transfer" in ok.replies[-1] and 5 not in _get_state()

    q = FakeQuery("xownok_4242_777", uid=5)
    q.matches = [re.match(r"^xownok_(\d+)_(\d+)$", q.data)]
    c = FakeClient()
    run(p.transfer_owner_confirm_cb(c, q))
    assert run(MainDB().get_bot(4242))["owner_id"] == 777
    assert any(call[1][:1] == (777,) for call in c.called("send_message"))
    # the old owner lost access
    again = FakeQuery("xownok_4242_5", uid=5)
    again.matches = [re.match(r"^xownok_(\d+)_(\d+)$", again.data)]
    run(p.transfer_owner_confirm_cb(FakeClient(), again))
    assert run(MainDB().get_bot(4242))["owner_id"] == 777


# ───────────────────────── Stars payments, broadcasts, callbacks ─────────────────────────
def _handler(app, kind, **match):
    from pyrogram import handlers
    cls = getattr(handlers, kind)
    for grp in sorted(app.dispatcher.groups):
        for hd in app.dispatcher.groups[grp]:
            if isinstance(hd, cls) and hasattr(hd, "filters"):
                yield hd


async def _dispatch_cb(app, client, query):
    import inspect
    for hd in _handler(app, "CallbackQueryHandler"):
        r = hd.filters(client, query) if hd.filters else True
        if await r if inspect.isawaitable(r) else bool(r):
            from pyrogram import ContinuePropagation
            try:
                return await (getattr(hd, "original_callback", None) or hd.callback)(client, query)
            except ContinuePropagation:
                continue
    raise AssertionError(f"no handler for {query.data}")


class Checkout:
    def __init__(self, payload, uid, amount, currency="XTR"):
        self.payload, self.total_amount, self.currency = payload, amount, currency
        self.from_user = SimpleNamespace(id=uid)
        self.result = None

    async def answer(self, success=None, error=None, **k):
        self.result = success


def test_stars_link_checkout_and_delivery(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        store = {}
        c = WorkerClient(store)
        code = _make_smartlink(app, c, "stars=20 x1")
        pre = next(_handler(app, "PreCheckoutQueryHandler")).callback
        good = Checkout(f"xl:{code}:9:1", 9, 20)
        run(pre(c, good))
        assert good.result is True
        for bad in (Checkout(f"xl:{code}:9:1", 9, 1),          # wrong price
                    Checkout(f"xl:{code}:9:1", 10, 20),        # someone else's invoice
                    Checkout(f"xl:{code}:9:1", 9, 20, "USD"),
                    Checkout("xl:nope:9:1", 9, 20)):
            run(pre(c, bad))
            assert bad.result is False
        # another user fills the only slot, the buyer still gets the files after paying
        from filestore.database.extras_db import CloneExtras
        x = CloneExtras(4242)
        run(x.claim_use(run(x.get_link(code)), 55))
        pay = Msg("", uid=9, client=c)
        pay.successful_payment = SimpleNamespace(payload=f"xl:{code}:9:1", total_amount=20,
                                                 telegram_payment_charge_id="ch_1")
        run(_dispatch(app, c, pay))
        assert store[77].copies == [9] and run(x.has_purchase(code, 9)) and run(x.stars_earned()) == 20
        # reopening later is free (no new invoice)
        before = len(c.called("send_invoice"))
        run(_dispatch(app, c, Msg(f"/start sl_{code}", uid=9, client=c)))
        assert len(c.called("send_invoice")) == before and store[77].copies == [9, 9]
    finally:
        _stop(app)


def test_premium_sale_and_shortener_skip(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        run(_dispatch(app, c, Msg("/setpremium 50 30", uid=5, client=c)))
        pre = next(_handler(app, "PreCheckoutQueryHandler")).callback
        ok, stale = Checkout("xp:30:9:1", 9, 50), Checkout("xp:7:9:1", 9, 50)
        run(pre(c, ok))
        run(pre(c, stale))
        assert ok.result is True and stale.result is False
        pay = Msg("", uid=9, client=c)
        pay.successful_payment = SimpleNamespace(payload="xp:30:9:1", total_amount=50, telegram_payment_charge_id="c2")
        run(_dispatch(app, c, pay))
        from filestore.database.extras_db import CloneExtras
        assert run(CloneExtras(4242).is_premium(9))
        plan = Msg("/plan", uid=9, client=c)
        run(_dispatch(app, c, plan))
        assert "active" in plan.replies[-1]
    finally:
        _stop(app)


def test_broadcast_now_and_scheduled(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        from filestore.database.worker_db import WorkerDB
        for uid in (21, 22, 23):
            run(WorkerDB(4242).add_user(uid))
        c = WorkerClient({})
        m = Msg("/broadcast silent", uid=5, client=c)
        m.reply_to_message = Msg("hello all", uid=5, client=c)
        run(_dispatch(app, c, m))
        run(asyncio.sleep(0.5))
        sent = c.called("copy_message")
        assert sorted(call[1][0] for call in sent) == [21, 22, 23]
        assert all(call[2]["disable_notification"] for call in sent)
        assert "done" in str(m.sent[-1].edits[-1])

        s = Msg("/broadcast pin in 2h", uid=5, client=c)
        s.reply_to_message = Msg("later", uid=5, client=c)
        run(_dispatch(app, c, s))
        assert "Scheduled" in s.replies[-1]
        from filestore.database.extras_db import CloneExtras
        items = run(CloneExtras(4242).list_schedules())
        assert len(items) == 1 and items[0]["flags"]["pin"]
        q = FakeQuery(f"xb:del:{items[0]['_id']}", uid=5)
        run(_dispatch_cb(app, c, q))
        assert run(CloneExtras(4242).list_schedules()) == []
        denied = FakeQuery("xb:stop", uid=9)
        run(_dispatch_cb(app, c, denied))
        assert denied.answers[-1] == ("Admins only", True)
    finally:
        _stop(app)


def test_links_callback_delete_and_export_owner_only(monkeypatch):
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        code = _make_smartlink(app, c, "7d")
        q = FakeQuery(f"xl:del:{code}", uid=5)
        run(_dispatch_cb(app, c, q))
        from filestore.database.extras_db import CloneExtras
        assert run(CloneExtras(4242).get_link(code)) is None
        stranger = Msg("/export", uid=9, client=c)
        run(_dispatch(app, c, stranger))
        assert not stranger.replies
        owner = Msg("/export", uid=5, client=c)
        run(_dispatch(app, c, owner))
        assert [r[0] for r in owner.replies] == ["document", "document"]
        help_q = FakeQuery("xh:help", uid=9)
        run(_dispatch_cb(app, c, help_q))
    finally:
        _stop(app)


def test_new_owner_step_is_wired_into_state_machine(monkeypatch):
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins import create_bot
    from filestore.main_bot.plugins.bot_settings import _get_state
    run(MainDB().add_bot(4242, 5, "enc", "CloneBot", LOG_CH))
    _get_state()[5] = {"step": "awaiting_new_owner", "data": {"bot_id": 4242}}
    m = Msg("abc", uid=5)
    run(create_bot.handle_creation_input(FakeClient(), m))
    assert "numeric user ID" in m.replies[-1]
    _get_state().pop(5, None)

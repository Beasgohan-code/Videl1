"""Tests for the phase-4 features: streaming drafts, bot photo, Stars subscriptions & gifts,
native pickers, referrals, redeem codes, trial, support inbox and the admin user panel."""
import time
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from pyrogram import StopPropagation, enums, raw, types

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run

MODULES = load_all()


@pytest.fixture(autouse=True)
def fresh_db():
    run(reset_db())
    from core import stream, support
    stream._disabled = False
    support._last.clear()
    support._pending.clear()
    yield


def _drafts(c):
    return [a[0] for n, a, _ in c.calls
            if n == "invoke" and isinstance(a[0], raw.functions.messages.SetTyping)
            and isinstance(a[0].action, raw.types.SendMessageTextDraftAction)]


# ───────────────────────── streaming (sendMessageDraft) ─────────────────────────
def test_chunks_are_growing_and_html_safe():
    from core.stream import _close_tags, chunks
    text = "<b>Hello there,</b> this is a <i>fairly long</i> message &amp; it has <a href='x'>a link</a> in it."
    parts = chunks(text, 4)
    assert parts[-1] == text and len(parts) >= 2
    for p in parts:
        assert p.count("<b>") == p.count("</b>") and p.count("<i>") == p.count("</i>")
        assert "&am" not in p.replace("&amp;", "")          # never cut an entity
    assert _close_tags("<b>open <i>both") == "<b>open <i>both</i></b>"
    assert chunks("short", 4) == ["short"]


def test_typewriter_sends_animated_drafts_in_private_only():
    from core import stream
    c = FakeClient()
    long_text = "<b>Welcome!</b> " + "word " * 30
    run(stream.typewriter(c, 5, long_text, steps=3, delay=0))
    drafts = _drafts(c)
    assert len(drafts) == 2                                     # final text is sent as a real message
    assert len({d.action.random_id for d in drafts}) == 1       # same draft id → animated
    assert all(d.action.text.text for d in drafts)
    c2 = FakeClient()
    run(stream.typewriter(c2, -100123, long_text, delay=0))   # groups: no drafts
    assert _drafts(c2) == []


def test_thinking_placeholder_and_progress():
    from core import stream
    c = FakeClient()

    async def body():
        async with stream.thinking(c, 5):
            pass
    run(body())
    d = _drafts(c)
    assert len(d) == 1 and d[0].action.text.text == ""          # empty draft = "Thinking…"

    # Progress: private → draft + reply; group → placeholder message edited
    m = FakeMsg("/stats", uid=5, client=c)

    async def use(msg):
        async with stream.progress(msg, "wait") as p:
            await p.finish("done")
    run(use(m))
    assert m.replies == ["done"]
    g = FakeMsg("/stats", uid=5, chat_type=enums.ChatType.SUPERGROUP, chat_id=-1005, client=c)
    run(use(g))
    assert g.replies == ["wait"] and g.sent[0].edits == ["done"]


def test_stream_disables_itself_when_unsupported():
    from core import stream

    class Rejecting(FakeClient):
        async def invoke(self, query, *a, **k):
            self.calls.append(("invoke", (query,), {}))
            e = Exception("[400 BOT_METHOD_INVALID]")
            e.ID = "BOT_METHOD_INVALID"
            raise e

    c = Rejecting()
    assert run(stream.draft(c, 5, "hi")) is None
    assert not stream.enabled()
    run(stream.typewriter(c, 5, "x " * 50, delay=0))
    assert len(c.called("invoke")) == 1                         # no more attempts


# ───────────────────────── bot profile photo ─────────────────────────
def test_set_bot_photo_falls_back_to_raw_upload():
    from core.profile import remove_bot_photo, set_bot_photo
    c = FakeClient()
    run(set_bot_photo(c, "x.jpg"))
    assert c.called("set_profile_photo") and not c.called("invoke")
    c.fail.add("set_profile_photo")
    run(set_bot_photo(c, "x.jpg"))
    up = [a[0] for n, a, _ in c.calls if n == "invoke"][-1]
    assert isinstance(up, raw.functions.photos.UploadProfilePhoto) and isinstance(up.bot, raw.types.InputUserSelf)
    run(remove_bot_photo(c))
    rm = [a[0] for n, a, _ in c.calls if n == "invoke"][-1]
    assert isinstance(rm, raw.functions.photos.UpdateProfilePhoto) and isinstance(rm.id, raw.types.InputPhotoEmpty)


def test_setbotpic_requires_photo_and_is_owner_only():
    from core.profile import setbotpic_cmd
    c = FakeClient()
    m = FakeMsg("/setbotpic", uid=111)
    m.photo = None
    m.document = None
    run(setbotpic_cmd(c, m))
    assert "Reply to a <b>photo</b>" in m.replies[0]


# ───────────────────────── payloads & checkout ─────────────────────────
def test_decode_payload_kinds():
    from core.payments import decode_payload, make_gift_payload, make_payload, make_sub_payload, parse_payload
    assert decode_payload(make_payload(30, 5)) == {"kind": "plan", "days": 30, "payer": 5, "target": 5}
    assert decode_payload(make_sub_payload(5).encode())["kind"] == "sub"
    g = decode_payload(make_gift_payload(90, 5, 42))
    assert g == {"kind": "gift", "days": 90, "payer": 5, "target": 42}
    with pytest.raises(ValueError):
        parse_payload(make_sub_payload(5))
    with pytest.raises(ValueError):
        decode_payload("zz:1:2:3")


def test_pre_checkout_subscription_and_gift():
    from core.payments import make_gift_payload, make_sub_payload, pre_checkout
    results = []

    class Q:
        def __init__(self, payload, amount, uid=5):
            self.payload, self.total_amount, self.currency = payload, amount, "XTR"
            self.from_user = SimpleNamespace(id=uid)

        async def answer(self, success, error=None):
            results.append(success)

    c = FakeClient()
    run(pre_checkout(c, Q(make_sub_payload(5), 90)))
    run(pre_checkout(c, Q(make_sub_payload(5), 100)))              # wrong price
    run(pre_checkout(c, Q(make_gift_payload(30, 5, 42), 100)))
    run(pre_checkout(c, Q(make_gift_payload(30, 5, 5), 100)))      # self-gift
    run(pre_checkout(c, Q(make_gift_payload(30, 6, 42), 100)))     # payer mismatch
    assert results == [True, False, True, False, False]


def _paid(payload, uid=5, stars=100):
    m = FakeMsg(text=None, uid=uid)
    m.successful_payment = SimpleNamespace(payload=payload, total_amount=stars, currency="XTR",
                                           telegram_payment_charge_id=f"ch_{time.time_ns()}")
    return m


def test_subscription_payment_and_renewal():
    from core.db import vdb
    from core.payments import make_sub_payload, successful_payment
    from database.db import db
    c = FakeClient()
    first = _paid(make_sub_payload(5), stars=90)
    run(successful_payment(c, first))
    sub = run(vdb.db["subscriptions"].find_one({"user": 5}))
    assert sub["active"] and sub["renewals"] == 0 and sub["charge_id"] == first.successful_payment.telegram_payment_charge_id
    assert run(db.col.find_one({"id": 5}))["premium_expiry"] == (date.today() + timedelta(days=30)).isoformat()
    run(successful_payment(c, _paid(make_sub_payload(5), stars=90)))   # renewal
    sub = run(vdb.db["subscriptions"].find_one({"user": 5}))
    assert sub["renewals"] == 1 and sub["charge_id"] == first.successful_payment.telegram_payment_charge_id
    assert run(db.col.find_one({"id": 5}))["premium_expiry"] == (date.today() + timedelta(days=60)).isoformat()
    assert run(vdb.db["subscriptions"].count_documents({})) == 1
    logs = [a[1] for n, a, _ in c.calls if n == "send_message" and a and a[0] == -100123]
    assert any("renewal #1" in t for t in logs)


def test_cancel_and_resume_subscription():
    from core.db import vdb
    from core.payments import make_sub_payload, sub_toggle_cb, successful_payment
    c = FakeClient()
    pm = _paid(make_sub_payload(5), stars=90)
    run(successful_payment(c, pm))
    q = FakeQuery("sub_toggle:cancel", uid=5)
    q.matches = [SimpleNamespace(group=lambda i: "cancel")]
    run(sub_toggle_cb(c, q))
    call = [a[0] for n, a, _ in c.calls if n == "invoke"][-1]
    assert isinstance(call, raw.functions.payments.BotCancelStarsSubscription)
    assert call.charge_id == pm.successful_payment.telegram_payment_charge_id and not call.restore
    assert run(vdb.db["subscriptions"].find_one({"user": 5}))["canceled"] is True
    q2 = FakeQuery("sub_toggle:resume", uid=5)
    q2.matches = [SimpleNamespace(group=lambda i: "resume")]
    run(sub_toggle_cb(c, q2))
    assert [a[0] for n, a, _ in c.calls if n == "invoke"][-1].restore is True
    assert run(vdb.db["subscriptions"].find_one({"user": 5}))["canceled"] is False


def test_subscription_link_uses_30_day_period():
    from core.payments import subscription_link

    class C(FakeClient):
        async def invoke(self, query, *a, **k):
            self.calls.append(("invoke", (query,), {}))
            return SimpleNamespace(url="https://t.me/$sub")

    c = C()
    assert run(subscription_link(c, 5)) == "https://t.me/$sub"
    q = c.called("invoke")[0][1][0]
    inv = q.invoice_media.invoice
    assert inv.currency == "XTR" and inv.subscription_period == 2592000 and inv.prices[0].amount == 90
    assert q.invoice_media.payload.startswith(b"vs:30:5:")


def test_gift_payment_gives_friend_premium():
    from core.payments import make_gift_payload, successful_payment
    from database.db import db
    c = FakeClient()
    run(successful_payment(c, _paid(make_gift_payload(30, 5, 42))))
    friend = run(db.col.find_one({"id": 42}))
    assert friend["is_premium"] and friend["premium_expiry"] == (date.today() + timedelta(days=30)).isoformat()
    assert not (run(db.col.find_one({"id": 5})) or {}).get("is_premium")
    assert any(a and a[0] == 42 and "gifted you" in a[1] for n, a, _ in c.calls if n == "send_message")


def test_gift_picker_shared_user():
    from core.payments import GIFT_BUTTON, gift_target_shared, shared_peer_filter
    c = FakeClient()
    m = FakeMsg(text=None, uid=5)
    m.chats_shared = types.RequestedChats(button_id=GIFT_BUTTON,
                                          users=[types.RequestedUser(user_id=42, first_name="Friend")])
    assert run(shared_peer_filter(GIFT_BUTTON)(c, m))
    assert not run(shared_peer_filter(11)(c, m))
    with pytest.raises(StopPropagation):
        run(gift_target_shared(c, m))
    assert "Friend selected" in m.replies[0] and "Gift Premium to Friend" in m.replies[1]


def test_stars_stats_shows_live_balance():
    from core.payments import stars_stats
    c = FakeClient()

    async def bal(*a, **k):
        return 1234
    c.get_stars_balance = bal
    m = FakeMsg("/stars", uid=111)
    run(stars_stats(c, m))
    assert "1234" in m.replies[-1] and "Active subscriptions" in m.replies[-1]


# ───────────────────────── clone wizard channel picker ─────────────────────────
def test_clone_wizard_accepts_shared_channel(monkeypatch):
    import filestore.main_bot.plugins.create_bot as cb

    class NoClient:
        def __init__(self, *a, **k):
            pass

        async def start(self):
            raise RuntimeError("offline (test)")

    monkeypatch.setattr(cb, "Client", NoClient)
    cb._creation_state[5] = {"step": "awaiting_channel", "picker": True,
                             "data": {"token": "1:x", "bot_info": {"id": 1, "username": "b"}}}
    c = FakeClient()
    m = FakeMsg(text=None, uid=5)
    m.photo = m.document = None
    m.chats_shared = types.RequestedChats(button_id=cb.CHANNEL_PICKER_ID, chats=[
        types.RequestedChat(chat_id=-1009876, chat_type=enums.ChatType.CHANNEL, name="My DB")])
    try:
        run(cb.handle_creation_input(c, m))
    finally:
        cb._creation_state.pop(5, None)
    assert m.text == "-1009876"
    # keyboard removed + channel name confirmed, then validation ran
    assert any("My DB" in (a[1] if len(a) > 1 else "") for n, a, _ in c.calls if n == "send_message")
    assert any("Validating channel" in r for r in m.replies)


# ───────────────────────── growth ─────────────────────────
def test_referrals_count_and_reward():
    from core.db import vdb
    from core.growth import on_new_user
    from database.db import db
    c = FakeClient()
    run(vdb.users.insert_one({"id": 5, "name": "Ref"}))
    assert not run(on_new_user(c, SimpleNamespace(id=5, first_name="Self"), "/start ref_5"))   # self
    assert not run(on_new_user(c, SimpleNamespace(id=6, first_name="X"), "/start ref_999"))    # unknown referrer
    for uid in range(10, 15):
        run(vdb.users.insert_one({"id": uid}))
        assert run(on_new_user(c, SimpleNamespace(id=uid, first_name=f"U{uid}", username=None,
                                                  last_name=None, language_code=None), "/start ref_5"))
    assert not run(on_new_user(c, SimpleNamespace(id=10, first_name="again"), "/start ref_5"))  # once only
    doc = run(vdb.get_user(5))
    assert doc["referrals"] == 5 and doc["referral_rewards"] == 1
    assert run(db.col.find_one({"id": 5}))["premium_expiry"] == (date.today() + timedelta(days=3)).isoformat()


def test_refer_view_has_link_and_share():
    from core.growth import refer_view
    text, kb = run(refer_view(FakeClient(), 5))
    assert "https://t.me/VidelBot?start=ref_5" in text
    assert kb.inline_keyboard[0][0].url.startswith("https://t.me/share/url")


def test_redeem_codes():
    from core.growth import create_codes, redeem
    from database.db import db
    code = run(create_codes(7, 1, 2, by=111))[0]
    ok, expiry, days = run(redeem(5, code.lower()))
    assert ok and days == 7 and expiry == (date.today() + timedelta(days=7)).isoformat()
    assert run(redeem(5, code))[0] is False                   # same user twice
    assert run(redeem(6, code))[0] is True
    ok, msg, _ = run(redeem(7, code))
    assert not ok and "fully used" in msg
    assert run(redeem(8, "NOPE"))[1] == "❌ Invalid code."
    assert run(db.col.find_one({"id": 6}))["is_premium"]


def test_gencode_command():
    from core.growth import gencode_cmd
    c = FakeClient()
    m = FakeMsg("/gencode 30 3", uid=222)
    run(gencode_cmd(c, m))
    assert m.replies[0].count("VIDEL-") == 3
    bad = FakeMsg("/gencode x", uid=222)
    run(gencode_cmd(c, bad))
    assert "Usage" in bad.replies[0] or "gencode" in bad.replies[0]


def test_trial_once():
    from core.db import vdb
    from core.growth import start_trial
    run(vdb.users.insert_one({"id": 5}))
    ok, expiry = run(start_trial(5, "T"))
    assert ok and expiry == (date.today() + timedelta(days=1)).isoformat()
    ok, msg = run(start_trial(5))
    assert not ok and "Premium" in msg                          # already premium now
    from database.db import db
    run(db.remove_premium(5))
    ok, msg = run(start_trial(5))
    assert not ok and "already used" in msg


# ───────────────────────── support inbox ─────────────────────────
def test_support_roundtrip():
    from core import support
    c = FakeClient()
    m = FakeMsg("/support my files are missing", uid=5, client=c)
    run(support.support_cmd(c, m))
    assert "sent to support" in m.replies[-1]
    owner_msgs = [a for n, a, _ in c.calls if n == "send_message" and a and a[0] == 111]
    assert owner_msgs and "my files are missing" in owner_msgs[0][1]
    mapping = run(support._col().find_one({"chat": 111}))
    assert mapping["peer"] == 5

    # owner replies to the header → copied to the user
    reply = FakeMsg("Fixed now!", uid=111, client=c)
    reply.reply_to_message_id = mapping["msg"]
    copied = []

    async def copy(chat_id, **k):
        copied.append(chat_id)
        return FakeMsg(text="Fixed now!", uid=0, chat_id=chat_id)
    reply.copy = copy
    assert run(support.mapped_reply_filter(c, reply))
    with pytest.raises(StopPropagation):
        run(support.support_reply_router(c, reply))
    assert copied == [5] and "delivered" in reply.replies[-1]

    # the user's answer to the reply goes back to the owners
    user_map = run(support._col().find_one({"chat": 5}))
    assert user_map["peer"] == support.TO_OWNERS
    follow = FakeMsg("thanks!", uid=5, client=c)
    follow.reply_to_message_id = user_map["msg"]
    follow.copy = copy
    assert run(support.mapped_reply_filter(c, follow))
    support._last.clear()
    with pytest.raises(StopPropagation):
        run(support.support_reply_router(c, follow))
    assert "sent to support" in follow.replies[-1] and copied[-1] == 111


def test_support_pending_and_cooldown():
    from core import support
    c = FakeClient()
    m = FakeMsg("/support", uid=5, client=c)
    run(support.support_cmd(c, m))
    nxt = FakeMsg("hello?", uid=5, client=c)
    assert run(support.pending_filter(c, nxt))
    assert not run(support.pending_filter(c, FakeMsg("/start", uid=5)))
    nxt.copy = lambda *a, **k: _async(FakeMsg(text="x", uid=0))
    with pytest.raises(StopPropagation):
        run(support.support_pending_msg(c, nxt))
    assert 5 not in support._pending
    again = FakeMsg("/support spam", uid=5, client=c)
    run(support.support_cmd(c, again))
    assert "Please wait" in again.replies[-1]


async def _async(v):
    return v


# ───────────────────────── admin user panel ─────────────────────────
def test_user_panel_and_actions():
    from core.db import vdb
    from core.userpanel import profile, uadm_cb, user_cmd
    from database.db import db
    run(vdb.users.insert_one({"id": 5, "name": "Tester", "username": "tester", "referrals": 2}))
    c = FakeClient()
    m = FakeMsg("/user 5", uid=222, client=c)
    run(user_cmd(c, m))
    text = m.replies[-1]
    assert "<code>5</code>" in text and "Referrals:</b> 2" in text and "Clone bots" in text

    q = FakeQuery("uadm:p30:5", uid=222)
    q.matches = [SimpleNamespace(group=lambda i: {1: "p30", 2: "5"}[i])]
    run(uadm_cb(c, q))
    assert run(db.col.find_one({"id": 5}))["is_premium"]
    q = FakeQuery("uadm:ban:5", uid=222)
    q.matches = [SimpleNamespace(group=lambda i: {1: "ban", 2: "5"}[i])]
    run(uadm_cb(c, q))
    assert vdb.is_banned(5)
    text, kb = run(profile(c, 5))
    assert "🚫 banned" in text and kb.inline_keyboard[0][0].callback_data == "uadm:unban:5"
    # non-admins can't use the buttons
    q = FakeQuery("uadm:unban:5", uid=5)
    q.matches = [SimpleNamespace(group=lambda i: {1: "unban", 2: "5"}[i])]
    run(uadm_cb(c, q))
    assert vdb.is_banned(5) and q.answers[-1][1] is True


def test_export_csv():
    from core.db import vdb
    from core.userpanel import export_cmd
    run(vdb.users.insert_one({"id": 5, "name": "A"}))
    run(vdb.users.insert_one({"id": 6, "name": "B"}))
    m = FakeMsg("/export", uid=222)
    run(export_cmd(FakeClient(), m))
    assert m.replies[-1] == ("document", "📤 <b>2</b> users exported.")


# ───────────────────────── menus ─────────────────────────
def test_new_commands_registered_and_under_limit():
    from core import commands as cm
    names = {n for n, _ in cm.USER_COMMANDS}
    assert {"gift", "mysub", "trial", "redeem", "refer", "support"} <= names
    assert {"user", "msg", "export", "gencode", "codes", "delcode"} <= {n for n, _ in cm.ADMIN_COMMANDS}
    assert {"setbotpic", "delbotpic"} <= {n for n, _ in cm.OWNER_COMMANDS}
    total = len({n for g in (cm.USER_COMMANDS, cm.ADMIN_COMMANDS, cm.OWNER_COMMANDS) for n, _ in g})
    assert total <= 100


def test_home_and_premium_keyboards_have_new_buttons():
    from core.menus import home_kb, premium_kb
    home = [b.callback_data for row in home_kb().inline_keyboard for b in row]
    assert "refer_btn" in home and "support_btn" in home
    prem = [b.callback_data for row in premium_kb().inline_keyboard for b in row]
    assert {"stars_sub", "gift_premium", "trial_btn"} <= set(prem)


def test_start_streams_before_sending():
    from core.menus import start_cmd
    c = FakeClient()
    m = FakeMsg("/start", uid=5, client=c)
    run(start_cmd(c, m))
    assert _drafts(c), "expected typing drafts before the home message"

"""Phase 5 – aiogram (Bot API 10.3) bridge: coloured buttons, ephemeral replies, drafts,
rich messages, managed bots, Stars via Bot API, profile photos, gifts – plus the
shortener / verification fixes found while auditing."""
import json
from types import SimpleNamespace

import pytest
from pyrogram import enums
from pyrogram.types import InlineKeyboardButton as Btn
from pyrogram.types import InlineKeyboardMarkup, LoginUrl

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run, use_botapi

MODULES = load_all()


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    from core import stream
    stream._disabled = False
    yield
    no_botapi()


def _j(v):
    return json.loads(v) if isinstance(v, str) else v


# ───────────────────────── keyboards ─────────────────────────
def test_convert_markup_styles_and_button_kinds():
    from core import botapi
    use_botapi()
    kb = InlineKeyboardMarkup([
        [Btn("✅ Confirm", callback_data="ok"), Btn("🗑 Delete", callback_data="confirm_delete_5")],
        [Btn("⚡ Create", callback_data="x"), Btn("Plain", callback_data="plain")],
        [Btn("Site", url="https://example.com"), Btn("Copy", copy_text="abc")],
        [Btn("Inline", switch_inline_query_current_chat="q")],
    ])
    out = botapi.convert_markup(kb)
    rows = out.inline_keyboard
    assert rows[0][0].style == "success" and rows[0][1].style == "danger"
    assert rows[1][0].style == "primary" and rows[1][1].style is None
    assert rows[2][0].url == "https://example.com" and rows[2][1].copy_text.text == "abc"
    assert rows[3][0].switch_inline_query_current_chat == "q"
    # login_url can't be represented → caller must use MTProto
    assert botapi.convert_markup(InlineKeyboardMarkup([[Btn("L", login_url=LoginUrl(url="https://a.b"))]])) is None


def test_colored_buttons_can_be_disabled(monkeypatch):
    from core import botapi
    monkeypatch.setattr(botapi, "COLORED_BUTTONS", False)
    assert botapi.style_for("✅ yes") is None and not botapi.colored()


def test_send_with_preview_goes_through_bot_api_with_styles():
    from core.ui import send_with_preview
    s = use_botapi()
    c = FakeClient()
    kb = InlineKeyboardMarkup([[Btn("💎 Buy", callback_data="buy_premium"), Btn("❌ Close", callback_data="close_btn")]])
    run(send_with_preview(c, 5, "<b>Hi</b>", kb, pic="https://img/x.jpg", reply_to=9, effect_id=5104841245755180586))
    p = s.last("SendMessage")
    assert p["chat_id"] == 5 and p["text"] == "<b>Hi</b>" and p["parse_mode"] == "HTML"
    lp = _j(p["link_preview_options"])
    assert lp == {"url": "https://img/x.jpg", "prefer_large_media": True, "show_above_text": True}
    btns = _j(p["reply_markup"])["inline_keyboard"][0]
    assert btns[0]["style"] == "success" and btns[1]["style"] == "danger"
    assert _j(p["reply_parameters"])["message_id"] == 9 and p["message_effect_id"] == 5104841245755180586
    assert not c.called("send_web_page") and not c.called("send_message")


def test_send_with_preview_falls_back_to_mtproto_on_bot_api_error():
    from aiogram.exceptions import TelegramBadRequest
    from core.ui import send_with_preview
    use_botapi(fail={"SendMessage": TelegramBadRequest(method=None, message="Bad Request: nope")})
    c = FakeClient()
    run(send_with_preview(c, 5, "hi", None, pic="https://img/x.jpg"))
    assert c.called("send_web_page")


def test_effects_are_not_sent_to_groups():
    from core.ui import send_with_preview
    s = use_botapi()
    run(send_with_preview(FakeClient(), -100123, "hi", None, effect_id=1))
    assert "message_effect_id" not in s.last("SendMessage")


def test_smart_edit_uses_bot_api_and_ignores_not_modified():
    from aiogram.exceptions import TelegramBadRequest
    from core.ui import smart_edit
    s = use_botapi()
    m = FakeMsg(text="old", uid=5)
    run(smart_edit(m, "new", InlineKeyboardMarkup([[Btn("🔙 Back", callback_data="home")]])))
    p = s.last("EditMessageText")
    assert p["message_id"] == m.id and p["text"] == "new" and _j(p["link_preview_options"]) == {"is_disabled": True}
    assert m.edits == []  # pyrofork not used
    use_botapi(fail={"EditMessageText": TelegramBadRequest(method=None, message="message is not modified")})
    run(smart_edit(m, "same"))
    assert m.edits == []  # "not modified" is success, no MTProto retry


def test_bot_api_backoff_after_network_errors():
    from aiogram.exceptions import TelegramNetworkError
    from core import botapi
    use_botapi(fail={"GetMe": TelegramNetworkError(method=None, message="down")})
    for _ in range(3):
        run(botapi.try_call(lambda b: b.get_me()))
    assert not botapi.enabled()  # 60 s back-off → handlers go straight to MTProto


# ───────────────────────── ephemeral / drafts / rich ─────────────────────────
def test_group_reply_is_ephemeral_in_groups_only():
    from core.ui import group_reply
    s = use_botapi()
    g = FakeMsg("/id", uid=7, chat_type=enums.ChatType.SUPERGROUP, chat_id=-1001)
    run(group_reply(g, "secret", InlineKeyboardMarkup([[Btn("Open", url="https://t.me/x")]])))
    p = s.last("SendMessage")
    assert _j(p["ephemeral_message_parameters"]) == {"receiver_user_id": 7} and g.replies == []
    # callback buttons can't be used on ephemeral messages (layer 220) → normal reply
    run(group_reply(g, "menu", InlineKeyboardMarkup([[Btn("x", callback_data="x")]])))
    assert g.replies == ["menu"]
    pm = FakeMsg("/id", uid=7)
    run(group_reply(pm, "hello"))
    assert pm.replies == ["hello"]


def test_help_and_id_in_groups_are_ephemeral():
    from core.menus import help_cmd
    from core.tools import id_cmd
    s = use_botapi()
    c = FakeClient()
    g = FakeMsg("/help", uid=7, chat_type=enums.ChatType.GROUP, chat_id=-55)
    run(help_cmd(c, g))
    run(id_cmd(c, FakeMsg("/id", uid=7, chat_type=enums.ChatType.GROUP, chat_id=-55)))
    eph = [p for n, p in s.payloads if n == "SendMessage" and "ephemeral_message_parameters" in p]
    assert len(eph) == 2 and "t.me/VidelBot?start=help" in json.dumps(eph[0]["reply_markup"])
    assert "-55" in str(eph[1]["text"]) and g.replies == []


def test_ephemeral_falls_back_when_disabled():
    from core.ui import group_reply
    no_botapi()
    g = FakeMsg("/id", uid=7, chat_type=enums.ChatType.GROUP, chat_id=-55)
    run(group_reply(g, "x"))
    assert g.replies == ["x"]


def test_drafts_prefer_bot_api():
    from core import stream
    s = use_botapi()
    c = FakeClient()
    did = run(stream.draft(c, 5, "<b>typing</b>"))
    p = s.last("SendMessageDraft")
    assert did and p["chat_id"] == 5 and p["text"] == "<b>typing</b>" and p["parse_mode"] == "HTML"
    assert 0 < p["draft_id"] < 2**31 and not c.called("invoke")


def test_drafts_fall_back_to_mtproto():
    from core import stream
    use_botapi(fail={"SendMessageDraft": RuntimeError("x")})
    c = FakeClient()
    assert run(stream.draft(c, 5, "hi")) and c.called("invoke")


def test_rich_guide_blocks_and_fallback():
    from core.extras import guide_cmd
    s = use_botapi()
    c = FakeClient()
    run(guide_cmd(c, FakeMsg("/guide", uid=5)))
    rich = _j(s.last("SendRichMessage")["rich_message"])
    types_ = [b["type"] for b in rich["blocks"]]
    assert types_[0] == "heading" and "table" in types_ and "details" in types_ and types_[-1] == "footer"
    tables = [b for b in rich["blocks"] if b["type"] == "table"]
    assert all(t["cells"][0][0]["is_header"] for t in tables)
    assert any("⭐ 100" in json.dumps(t, ensure_ascii=False) for t in tables)
    no_botapi()
    c2 = FakeClient()
    run(guide_cmd(c2, FakeMsg("/guide", uid=5)))
    assert c2.called("send_message")  # classic HTML help


def test_guide_deep_link_and_group_redirect():
    from core.extras import guide_cmd
    from core.menus import start_cmd
    s = use_botapi()
    run(start_cmd(FakeClient(), FakeMsg("/start guide", uid=5)))
    assert s.last("SendRichMessage")
    g = FakeMsg("/guide", uid=5, chat_type=enums.ChatType.GROUP, chat_id=-9)
    run(guide_cmd(FakeClient(), g))
    assert "start=guide" in json.dumps(s.last("SendMessage")["reply_markup"])


# ───────────────────────── managed bots ─────────────────────────
def test_suggested_username_and_link():
    import re
    from filestore.main_bot.plugins.managed_bots import newbot_link, suggested_username
    for name in ["Ann", "", "123abc", "Ünïcødé Nâme Långer Than Twelve", "a"]:
        u = suggested_username(name)
        assert re.fullmatch(r"[a-z][a-z0-9]{0,11}_[0-9a-f]{6}_bot", u) and 5 <= len(u) <= 32
    assert suggested_username("Ann") != suggested_username("Ann")
    assert newbot_link("VidelBot", "ann_abc123_bot", "Ann's FileStore") == \
        "https://t.me/newbot/VidelBot/ann_abc123_bot?name=Ann%27s%20FileStore"


def _kb_datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row if b.callback_data]


def test_create_bot_offers_one_tap_only_with_bot_management(monkeypatch):
    from aiogram import types as at
    from filestore.main_bot.plugins import create_bot
    seen = {}

    async def edit_text(text=None, reply_markup=None, **k):
        seen["kb"] = reply_markup

    q = FakeQuery("create_bot", uid=5)
    q.message.edit_text = edit_text
    use_botapi()  # GetMe → can_manage_bots=True
    run(create_bot.create_bot_callback(FakeClient(), q))
    assert "managed_new" in _kb_datas(seen["kb"])
    use_botapi(responses={"GetMe": at.User(id=1, is_bot=True, first_name="V", can_manage_bots=False)})
    run(create_bot.create_bot_callback(FakeClient(), q))
    assert "managed_new" not in _kb_datas(seen["kb"])


def _patch_validate(monkeypatch):
    from filestore.main_bot.plugins import create_bot

    async def fake_validate(token):
        return {"id": 424242, "username": "ann_abc123_bot", "first_name": "Ann FS"}
    monkeypatch.setattr(create_bot, "validate_bot_token", fake_validate)
    monkeypatch.setattr(create_bot, "offer_channel_picker", lambda *a, **k: _noop())


async def _noop():
    return None


def test_watch_username_fetches_token_and_continues_wizard(monkeypatch):
    from filestore.main_bot.plugins import create_bot, managed_bots
    _patch_validate(monkeypatch)
    monkeypatch.setattr(managed_bots, "POLL_START", 0)
    s = use_botapi(responses={"GetManagedBotToken": "424242:AAmanagedtokenxxxxxxxxxxxxxxxx"})
    c = FakeClient()
    looked = []

    async def get_users(username):
        looked.append(username)
        return SimpleNamespace(id=424242, is_bot=True) if len(looked) >= 2 else None
    c.get_users = get_users
    create_bot._creation_state[5] = {"step": "awaiting_token", "data": {"managed_username": "ann_abc123_bot"}}
    status = FakeMsg(text="waiting", uid=0)
    assert run(managed_bots.watch_username(c, 5, "ann_abc123_bot", status, timeout=5)) is True
    assert _j(s.last("GetManagedBotToken")["user_id"]) == 424242
    st = create_bot._creation_state[5]
    assert st["step"] == "awaiting_channel" and st["data"]["managed"] and st["data"]["token"].startswith("424242:")
    assert any("Token verified" in e for e in status.edits)
    create_bot._creation_state.pop(5, None)


def test_watch_username_stops_when_cancelled(monkeypatch):
    from filestore.main_bot.plugins import managed_bots
    monkeypatch.setattr(managed_bots, "POLL_START", 0)
    use_botapi()
    assert run(managed_bots.watch_username(FakeClient(), 77, "x_000000_bot", FakeMsg(uid=0), timeout=3)) is False


def test_claim_foreign_bot_is_refused(monkeypatch):
    from aiogram.exceptions import TelegramBadRequest
    from filestore.main_bot.plugins import create_bot, managed_bots
    use_botapi(fail={"GetManagedBotToken": TelegramBadRequest(method=None, message="BOT_NOT_MANAGED")})
    c = FakeClient()

    async def get_users(username):
        return SimpleNamespace(id=111, is_bot=True)
    c.get_users = get_users
    create_bot._creation_state[5] = {"step": "awaiting_token", "data": {"managed_username": "ann_abc123_bot"}}
    m = FakeMsg("@someone_elses_bot", uid=5)
    assert run(managed_bots.claim_username(c, m, 5, "@someone_elses_bot")) is True
    assert any("wasn't created through" in e for e in m.sent[0].edits)
    assert create_bot._creation_state[5]["step"] == "awaiting_token"
    create_bot._creation_state.pop(5, None)


def test_claim_requires_start_proof_from_the_same_user(monkeypatch):
    from aiogram import types as at
    from filestore.main_bot.plugins import create_bot, managed_bots
    _patch_validate(monkeypatch)
    state = {"code": None}

    def updates(method):
        if method.timeout == 0:
            return []
        code = state["code"]
        chat = at.Chat(id=9, type="private")
        mk = lambda uid, txt, n: at.Update(update_id=n, message=at.Message(  # noqa: E731
            message_id=n, date=0, chat=chat, from_user=at.User(id=uid, is_bot=False, first_name="u"), text=txt))
        return [mk(666, f"/start vd{code}", 1), mk(5, "/start wrong", 2), mk(5, f"/start vd{code}", 3)]

    s = use_botapi(responses={"GetManagedBotToken": "424242:AAmanagedtokenxxxxxxxxxxxxxxxx", "GetUpdates": updates})
    c = FakeClient()

    async def get_users(username):
        return SimpleNamespace(id=424242, is_bot=True)
    c.get_users = get_users
    create_bot._creation_state[5] = {"step": "awaiting_token", "data": {"managed_username": "orig_000000_bot"}}
    m = FakeMsg("@ann_renamed_bot", uid=5)
    spawned = []
    monkeypatch.setattr(managed_bots, "_spawn", lambda coro: spawned.append(coro))
    run(managed_bots.claim_username(c, m, 5, "@ann_renamed_bot"))
    status = m.sent[0]
    assert "Prove @ann_renamed_bot is yours" in status.edits[-1]
    # the secret code only lives in the button URL → read it from the pending coroutine's arguments
    state["code"] = spawned[0].cr_frame.f_locals["code"]
    assert run(spawned[0]) is True
    assert create_bot._creation_state[5]["step"] == "awaiting_channel"
    assert s.names().count("GetUpdates") >= 1
    create_bot._creation_state.pop(5, None)


def test_rotate_token_for_managed_clone(monkeypatch):
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins import managed_bots
    from filestore.utils.security import decrypt_token, encrypt_token
    db = MainDB()
    run(db.add_bot(bot_id=424242, owner_id=5, bot_token_encrypted=encrypt_token("424242:old"),
                   bot_username="ann_bot", log_channel_id=-100))
    use_botapi(responses={"ReplaceManagedBotToken": "424242:newtoken"})
    q = FakeQuery("rotate_tok_do_424242", uid=5)
    run(managed_bots.rotate_token(FakeClient(), q))
    assert q.answers[0][0] == "❌ Access denied!"  # not flagged managed yet
    run(db.bots.update_one({"_id": 424242}, {"$set": {"managed": True}}))
    restarted = []

    class Eng:
        def get_worker(self, i):
            return None

        async def start_worker(self, doc):
            restarted.append(doc["bot_token_encrypted"])
    import filestore.worker_bot.engine as eng
    monkeypatch.setattr(eng, "worker_engine", Eng())
    q = FakeQuery("rotate_tok_do_424242", uid=5)
    run(managed_bots.rotate_token(FakeClient(), q))
    doc = run(db.get_bot(424242))
    assert decrypt_token(doc["bot_token_encrypted"]) == "424242:newtoken" and restarted
    assert any("Token rotated" in e for e in q.message.edits)
    # other users can't rotate
    q2 = FakeQuery("rotate_tok_do_424242", uid=6)
    run(managed_bots.rotate_token(FakeClient(), q2))
    assert q2.answers[0][0] == "❌ Access denied!"
    assert managed_bots.rotate_row(doc) and not managed_bots.rotate_row({"_id": 1})


# ───────────────────────── Stars / gifts / photos ─────────────────────────
def test_subscription_link_via_bot_api_and_fallback():
    from core import payments
    s = use_botapi()
    c = FakeClient()
    assert run(payments.subscription_link(c, 5)) == "https://t.me/$invoice"
    p = s.last("CreateInvoiceLink")
    assert p["currency"] == "XTR" and p["subscription_period"] == 2592000
    assert p["payload"].startswith("vs:30:5:") and _j(p["prices"])[0]["amount"] == payments.SUBSCRIPTION_STARS
    use_botapi(fail={"CreateInvoiceLink": RuntimeError("x")})
    c2 = FakeClient()

    async def invoke(q, *a, **k):
        c2.calls.append(("invoke", (q,), {}))
        return SimpleNamespace(url="https://t.me/$raw")
    c2.invoke = invoke
    assert run(payments.subscription_link(c2, 5)) == "https://t.me/$raw"


def test_star_balance_and_ledger():
    from aiogram import types as at
    from core import payments
    tx = at.StarTransactions(transactions=[at.StarTransaction(
        id="t1", amount=50, date=1700000000,
        source=at.TransactionPartnerUser(transaction_type="invoice_payment", user=at.User(id=5, is_bot=False,
                                                                                          first_name="A")))])
    use_botapi(responses={"GetStarTransactions": tx})
    assert run(payments.star_balance(FakeClient())) == 4321
    lines = run(payments.telegram_transactions())
    assert lines and "⭐50" in lines[0] and "<code>5</code>" in lines[0]


def test_sub_toggle_uses_edit_user_star_subscription():
    from core import payments
    from core.db import vdb
    run(vdb.db["subscriptions"].insert_one({"user": 5, "active": True, "charge_id": "ch1"}))
    s = use_botapi()
    q = FakeQuery("sub_toggle:cancel", uid=5)
    q.matches = [SimpleNamespace(group=lambda i: "cancel")]
    c = FakeClient()
    run(payments.sub_toggle_cb(c, q))
    p = s.last("EditUserStarSubscription")
    assert p["telegram_payment_charge_id"] == "ch1" and p["is_canceled"] is True and not c.called("invoke")
    assert run(vdb.db["subscriptions"].find_one({"user": 5}))["canceled"] is True


def test_bot_photo_via_bot_api_with_clone_token(tmp_path):
    from core import profile
    f = tmp_path / "p.jpg"
    f.write_bytes(b"\xff\xd8jpeg")
    s = use_botapi()
    run(profile.set_bot_photo(None, str(f), token="424242:clonetoken"))
    p = s.last("SetMyProfilePhoto")
    assert _j(p["photo"])["type"] == "static" and "<file" in json.dumps(p)
    run(profile.remove_bot_photo(None, token="424242:clonetoken"))
    assert "RemoveMyProfilePhoto" in s.names()
    # Bot API refuses → MTProto through the running client
    use_botapi(fail={"SetMyProfilePhoto": RuntimeError("no")})
    c = FakeClient()
    run(profile.set_bot_photo(c, str(f)))
    assert c.called("set_profile_photo")


def test_giftpremium_and_gifts_commands():
    from aiogram import types as at
    from core import extras
    gifts = at.Gifts(gifts=[at.Gift(id="g1", star_count=15, sticker=at.Sticker(
        file_id="f", file_unique_id="u", type="regular", width=1, height=1, is_animated=False, is_video=False,
        emoji="🧸"))])
    s = use_botapi(responses={"GetAvailableGifts": gifts})
    c = FakeClient()

    async def get_users(x):
        return SimpleNamespace(id=77, mention="Bob")
    c.get_users = get_users
    m = FakeMsg("/giftpremium 77 6 enjoy!", uid=111)
    run(extras.giftpremium_cmd(c, m))
    p = s.last("GiftPremiumSubscription")
    assert p["user_id"] == 77 and p["month_count"] == 6 and p["star_count"] == 1500 and p["text"] == "enjoy!"
    assert "6 months" in m.replies[-1]
    bad = FakeMsg("/giftpremium 77 5", uid=111)
    run(extras.giftpremium_cmd(c, bad))
    assert "3|6|12" in bad.replies[-1]
    g = FakeMsg("/gifts", uid=111)
    run(extras.gifts_cmd(c, g))
    assert "🧸 ⭐15" in g.replies[-1] and "g1" in g.replies[-1]
    sg = FakeMsg("/sendgift 77 g1 hi", uid=111)
    run(extras.sendgift_cmd(c, sg))
    assert s.last("SendGift")["gift_id"] == "g1" and "Gift sent" in sg.replies[-1]


def test_botapi_status_command():
    from core import extras
    s = use_botapi()
    c = FakeClient()
    run(extras.botapi_cmd(c, FakeMsg("/botapi", uid=111)))
    p = s.last("SendMessage")
    assert "aiogram 3.31" in p["text"] and "✅ Manage bots" in p["text"]
    styles = [b.get("style") for row in _j(p["reply_markup"])["inline_keyboard"] for b in row]
    assert {"success", "danger", "primary"} <= set(styles)
    no_botapi()
    m = FakeMsg("/botapi", uid=111)
    run(extras.botapi_cmd(c, m))
    assert "AIOGRAM_ENABLED" in m.replies[-1]


def test_status_line_and_boot_report_mention_bot_api():
    from core import botapi
    use_botapi()
    line = run(botapi.status_line())
    assert line.startswith("✅ aiogram") and "managed bots ✅" in line
    no_botapi()
    assert "off" in run(botapi.status_line())


# ───────────────────────── audit fixes ─────────────────────────
def test_shortener_api_key_is_encrypted_and_legacy_steps_work(monkeypatch):
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins import bot_settings
    from filestore.utils.security import decrypt_token, encrypt_token

    class Eng:
        async def stop_worker(self, i):
            pass

        async def start_worker(self, d):
            pass
    import filestore.worker_bot.engine as eng
    from cryptography.fernet import Fernet
    from filestore.utils import security
    monkeypatch.setattr(eng, "worker_engine", Eng())
    monkeypatch.setattr(security, "_fernet", Fernet(Fernet.generate_key()))  # ENCRYPTION_KEY configured
    db = MainDB()
    run(db.add_bot(bot_id=11, owner_id=5, bot_token_encrypted=encrypt_token("11:x"), bot_username="b",
                   log_channel_id=-1))
    st = {"step": "settings", "action": "short_api", "data": {"bot_id": 11}}
    run(bot_settings.handle_shortener_input(FakeClient(), FakeMsg("MYSECRETKEY", uid=5), st))
    stored = run(db.get_bot(11))["shortener"]["api_key_encrypted"]
    assert stored != "MYSECRETKEY" and decrypt_token(stored) == "MYSECRETKEY"
    # older awaiting_shortener_domain step (4-arg call) no longer crashes
    st2 = {"step": "awaiting_shortener_domain", "data": {"bot_id": 11}}
    run(bot_settings.handle_shortener_input(FakeClient(), FakeMsg("https://short.io/", uid=5), st2, "domain"))
    assert run(db.get_bot(11))["shortener"]["domain"] == "short.io"
    bad = FakeMsg("nodomain", uid=5)
    run(bot_settings.handle_shortener_input(FakeClient(), bad, dict(st2), "domain"))
    assert "Invalid domain" in bad.replies[-1]


def test_shortener_cannot_be_enabled_without_settings():
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins import bot_settings
    from filestore.utils.security import encrypt_token
    db = MainDB()
    run(db.add_bot(bot_id=12, owner_id=5, bot_token_encrypted=encrypt_token("12:x"), bot_username="b",
                   log_channel_id=-1))
    q = FakeQuery("short_toggle_12", uid=5)
    run(bot_settings.short_toggle_callback(FakeClient(), q))
    assert "first" in q.answers[0][0] and not run(db.get_bot(12))["shortener"]["enabled"]


def test_verify_tokens_are_single_use_and_personal():
    from datetime import datetime
    from filestore.database.worker_db import WorkerDB
    w = WorkerDB(1234)
    tok = run(w.new_verify_token(5))
    assert not run(w.consume_verify_token(6, tok))       # someone else's token
    assert not run(w.consume_verify_token(5, "wrong"))
    assert run(w.consume_verify_token(5, tok))
    assert not run(w.consume_verify_token(5, tok))       # single use
    assert run(w.is_verified(5, 3600))
    # naive datetimes (what Mongo returns) are handled
    run(w.verify.update_one({"_id": 8}, {"$set": {"verified_at": datetime.utcnow()}}, upsert=True))
    assert run(w.is_verified(8, 3600))


def test_menus_fit_telegram_limits():
    from core import commands as cm
    owner = {n for g in (cm.USER_COMMANDS, cm.ADMIN_COMMANDS, cm.OWNER_COMMANDS) for n, _ in g}
    assert len(owner) <= 100 and {"guide", "botapi", "giftpremium", "gifts"} <= owner
    assert "guide" in {n for n, _ in cm.GROUP_COMMANDS}


def test_zippyshare_fails_cleanly():
    from VideoEncoder.utils.direct_link_generator import DirectDownloadLinkException, zippy_share
    with pytest.raises(DirectDownloadLinkException):
        zippy_share("https://www1.zippyshare.com/v/abc/file.html")


def test_no_handler_swallows_new_callbacks():
    """Run every registered callback filter (group ≥ 0; negative groups are pass-through
    gates such as ban / force-sub) against the new callback data."""
    import asyncio
    import inspect
    from pyrogram.handlers import CallbackQueryHandler
    owners = {}
    for mod in MODULES:
        for name, obj in vars(mod).items():
            hs = getattr(obj, "handlers", None) if callable(obj) else None
            for h, _group in (hs if isinstance(hs, list) else []):
                if isinstance(h, CallbackQueryHandler) and _group >= 0:  # <0 = middleware gates
                    owners.setdefault(id(h), (mod.__name__, name, h))
    for data, expected in [("managed_new", "managed_new"), ("rotate_tok_424242", "rotate_token"),
                           ("rotate_tok_do_424242", "rotate_token"), ("botapi_demo", "botapi_demo")]:
        hits = []
        for mod, name, h in owners.values():
            q = FakeQuery(data, uid=111)
            try:
                r = h.filters(None, q) if h.filters else True      # sync custom filters return a bool
                ok = asyncio.get_event_loop().run_until_complete(r) if inspect.isawaitable(r) else bool(r)
            except Exception:
                ok = False
            if ok:
                hits.append(name)
        assert hits == [expected], (data, hits)


BOT_API_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler", "a", "code",
                "pre", "blockquote", "tg-emoji", "tg-time"}


def _bot_api_html_problems(text: str) -> list:
    import re
    from html.parser import HTMLParser
    problems = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in BOT_API_TAGS:
                problems.append(f"<{tag}>")
    P(convert_charrefs=True).feed(text)
    # bare '&' that isn't an entity is rejected by the Bot API parser
    problems += [m.group(0) for m in re.finditer(r"&(?![a-zA-Z]+;|#\d+;|#x[0-9a-fA-F]+;)", text)]
    return problems


def test_menu_texts_are_valid_bot_api_html():
    """Menus go out through the Bot API (coloured buttons) – its HTML parser is stricter than pyrofork's."""
    import core.texts as t
    bad = {}
    for name, val in vars(t).items():
        if name.isupper() and isinstance(val, str):
            probs = _bot_api_html_problems(val)
            if probs:
                bad[name] = probs
    assert not bad, bad


def test_every_callback_button_has_a_handler():
    """Every callback_data literal in the code reaches exactly one handler per group."""
    import asyncio
    import inspect
    import pathlib
    import re
    from pyrogram.handlers import CallbackQueryHandler
    hs = {}
    for mod in MODULES:
        for name, obj in vars(mod).items():
            lst = getattr(obj, "handlers", None) if callable(obj) else None
            for h, g in (lst if isinstance(lst, list) else []):
                if isinstance(h, CallbackQueryHandler) and g >= 0:
                    hs[id(h)] = (f"{mod.__name__}.{name}", g, h)
    datas = set()
    for p in pathlib.Path(".").rglob("*.py"):
        if "tests" in p.parts or "worker_bot" in p.parts:  # worker callbacks live on clone clients
            continue
        for m in re.finditer(r'callback_data\s*=\s*(f?)(["\'])(.+?)\2', p.read_text()):
            d = m.group(3)
            if m.group(1):
                d = re.sub(r"\{[^}]*\}", "123", d)
            if not re.fullmatch(r"(uadm:)?123:123(123)?", d):   # built from several variables
                datas.add(d)
    loop = asyncio.new_event_loop()
    unhandled, clashes = [], []
    for d in sorted(datas):
        per_group = {}
        for name, g, h in hs.values():
            try:
                r = h.filters(None, FakeQuery(d, uid=111)) if h.filters else True
                ok = loop.run_until_complete(r) if inspect.isawaitable(r) else bool(r)
            except Exception:
                ok = False
            if ok:
                per_group.setdefault(g, []).append(name)
        if not per_group:
            unhandled.append(d)
        clashes += [(d, ns) for ns in per_group.values() if len(ns) > 1]
    loop.close()
    assert len(datas) > 100 and not unhandled and not clashes, (unhandled, clashes)


def test_bot_api_is_never_used_for_other_bots_clients():
    """aiogram talks with Videl's token – a clone worker's client must stay on MTProto."""
    from core import botapi
    from core.ui import send_with_preview, smart_edit
    s = use_botapi()
    main_id = int(__import__("config").BOT_TOKEN.split(":")[0])
    clone = FakeClient()
    clone.me = SimpleNamespace(id=main_id + 1, username="CloneBot")
    run(send_with_preview(clone, 5, "hi", None))
    m = FakeMsg(text="x", uid=5)
    m._client = clone
    run(smart_edit(m, "y"))
    assert s.payloads == [] and clone.called("send_message") and m.edits == ["y"]
    main = FakeClient()
    main.me = SimpleNamespace(id=main_id, username="VidelBot")
    assert botapi.is_main(main) and botapi.is_main(None) and not botapi.is_main(clone)

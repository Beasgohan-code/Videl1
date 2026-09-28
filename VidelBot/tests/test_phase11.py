"""Phase 11 – full UI upgrade: design system (sans-bold headings, cards, collapsible sections, tables),
restyled menus and the richer /guide."""
import json
import re

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run, use_botapi

MODULES = load_all()


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    yield
    no_botapi()


def _visible_len(html_text: str) -> int:
    import html
    return len(html.unescape(re.sub(r"<[^>]+>", "", html_text)).encode("utf-16-le")) // 2


# ───────────────────────── design system ─────────────────────────
def test_sans_bold_keeps_entities_emoji_and_round_trips():
    from core.design import plain, sans
    s = sans("Help &amp; Guide 2026 🚀")
    assert s.startswith("𝗛𝗲𝗹𝗽 &amp; 𝗚𝘂𝗶𝗱𝗲 𝟮𝟬𝟮𝟲") and s.endswith("🚀")
    assert plain(s) == "Help &amp; Guide 2026 🚀"


def test_building_blocks():
    from core.design import bar, card, details, page, section, table
    assert card() == "" and section("🧩", "Empty") == ""
    assert details("🧩", "X", "a").endswith("<blockquote expandable>a</blockquote>")
    assert page("a", "", None, "b") == "a\n\nb"
    assert bar(3, 10) == "▰▰▰▱▱▱▱▱▱▱" and bar(0, 0) == "▱" * 10
    t = table([("30 days", "⭐ 100")], header=("Plan", "Price"))
    assert "▸ 30 days · <b>⭐ 100</b>" in t and "𝗣𝗹𝗮𝗻" in t


def test_every_main_text_fits_a_message_and_uses_sans_headings():
    from core import texts
    from renamer.handlers import TUTORIAL
    from saver.strings import COMMANDS_TXT, HELP_TXT as SAVER_HELP
    from saver.start import premium_text
    for name in ("START_TXT", "HELP_TXT", "ABOUT_TXT", "CLONE_START_MSG", "CLONE_HELP_MSG", "CLONE_ABOUT_MSG",
                 "ADMIN_HELP", "TOOLS_HELP", "SETTINGS_HUB", "ENC_HELP", "FORCE_MSG"):
        assert _visible_len(getattr(texts, name)) < 4000, name
    for t in (TUTORIAL, COMMANDS_TXT, SAVER_HELP, premium_text()):
        assert _visible_len(t) < 4000
    assert "𝗤𝘂𝗶𝗰𝗸 𝘀𝘁𝗮𝗿𝘁" in texts.START_TXT and "<blockquote expandable>" in texts.START_TXT
    assert "𝗙𝗿𝗲𝗲 𝘃𝘀 𝗣𝗿𝗲𝗺𝗶𝘂𝗺" in texts.HELP_TXT
    # templates keep exactly their placeholders
    texts.START_TXT.format(mention="m", username="u", first_name="f", uptime="1h", plan="p")
    texts.ABOUT_TXT.format(username="u", first_name="f", api="10.3", uptime="1h")
    texts.CLONE_START_MSG.format(mention="m")
    texts.FORCE_MSG.format(mention="m")
    texts.SETTINGS_HUB.format(badge="b", user_id=1)
    texts.TOOLS_HELP.format(username="u")


def test_premium_screen_has_a_plans_table():
    from config import STARS_PLANS
    from saver.start import premium_text
    t = premium_text()
    days, price = STARS_PLANS[0]
    assert f"⭐ {price}" in t and "𝗣𝗹𝗮𝗻𝘀" in t
    assert "Other payment" not in t          # no UPI/QR configured → no manual section


def test_my_plan_shows_quota_bar():
    from saver.premium import plan_view
    text, _ = run(plan_view(5, "Tester"))
    assert "▰" in text and "𝗙𝗿𝗲𝗲 𝗧𝗶𝗲𝗿" in text


def test_help_has_illustrated_guide_button_that_sends_rich_message():
    from core.extras import guide_btn
    from core.menus import help_kb
    assert "guide_btn" in [b.callback_data for row in help_kb(5).inline_keyboard for b in row]
    s = use_botapi()
    q = FakeQuery("guide_btn", uid=5)
    run(guide_btn(FakeClient(), q))
    assert s.last("SendRichMessage") and q.answers


def test_rich_guide_has_comparison_pull_quote_and_two_button_rows():
    s = use_botapi()
    from core.extras import send_guide
    run(send_guide(FakeClient(), 5, "VidelBot"))
    rm = s.last("SendRichMessage")["rich_message"]
    blocks = json.loads(rm) if isinstance(rm, str) else rm
    types_ = [b["type"] for b in blocks["blocks"]]
    assert types_.count("table") == 2 and types_.count("buttons") == 2 and "pullquote" in types_
    summaries = json.dumps([b.get("summary") for b in blocks["blocks"] if b["type"] == "details"], ensure_ascii=False)
    assert "Auto-Rename" in summaries and "Tools" in summaries


def test_clone_dashboard_and_list_use_the_new_layout():
    from datetime import datetime
    from filestore.database.main_db import MainDB
    from filestore.main_bot.plugins.my_bots import dashboard_callback, my_bots_callback
    run(MainDB().bots.insert_one({"_id": 4242, "bot_username": "demo_bot", "owner_id": 5, "is_active": True,
                                  "last_active": datetime.utcnow(), "created_at": datetime.utcnow()}))
    q = FakeQuery("dashboard_4242", uid=5)
    run(dashboard_callback(FakeClient(), q))
    t = q.message.edits[-1]
    assert "𝗕𝗼𝘁 𝗱𝗮𝘀𝗵𝗯𝗼𝗮𝗿𝗱" in t and "@demo_bot" in t and "Auto-delete" in t
    q = FakeQuery("my_bots", uid=5)
    run(my_bots_callback(FakeClient(), q))
    assert "𝗠𝘆 𝗯𝗼𝘁𝘀" in q.message.edits[-1]


def test_empty_my_bots_uses_smart_edit_layout():
    from filestore.main_bot.plugins.my_bots import my_bots_callback
    q = FakeQuery("my_bots", uid=9)
    run(my_bots_callback(FakeClient(), q))
    assert "haven't created a bot" in q.message.edits[-1]


def test_start_home_renders_with_the_new_text():
    from core.menus import start_text
    text = run(start_text(FakeClient(), FakeMsg(uid=5).from_user))
    assert "𝗤𝘂𝗶𝗰𝗸 𝘀𝘁𝗮𝗿𝘁" in text and "Uptime" in text and "Free" in text

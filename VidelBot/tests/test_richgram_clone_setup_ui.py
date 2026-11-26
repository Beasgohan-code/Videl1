"""Richgram surfaces and native button styles for clone setup and welcome settings."""
from pyrogram import enums
import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run

load_all()
from core import rich
from filestore.database.main_db import MainDB
from filestore.main_bot.plugins import bot_settings, create_bot, my_bots


@pytest.fixture(autouse=True)
def clear_ui_state():
    bot_settings._get_state().pop(42, None)
    yield
    bot_settings._get_state().pop(42, None)


def test_clone_mode_picker_is_richgram_and_uses_colored_buttons(monkeypatch):
    captured = {}

    async def rich_edit(message, doc, reply_markup=None):
        captured.update(doc=doc, markup=reply_markup)
        return message

    monkeypatch.setattr(rich, "edit", rich_edit)
    state = {"step": "verifying_channel", "data": {}}
    create_bot._creation_state[4040] = state
    status = FakeMsg("Choose a mode", uid=4040)
    try:
        run(create_bot._show_creation_profiles(status, 4040, state))
        doc, markup = captured["doc"], captured["markup"]
        buttons = [button for row in markup.inline_keyboard for button in row]
        assert isinstance(doc, rich.Doc)
        assert doc.title == "Choose a bot mode"
        headings = [block[2] for block in doc.blocks if block[0] == "h"]
        assert headings == ["File Store", "Group Management", "Music"]
        assert [button.style for button in buttons[:3]] == [
            enums.ButtonStyle.PRIMARY, enums.ButtonStyle.PRIMARY, enums.ButtonStyle.SUCCESS]
        assert buttons[-1].style == enums.ButtonStyle.DANGER
        assert rich.markup_ok(markup)
    finally:
        timer = state.pop("profile_timer", None)
        if timer:
            timer.cancel()
        create_bot._creation_state.pop(4040, None)


def test_welcome_configuration_uses_richgram_and_escapes_saved_user_text(monkeypatch):
    captured = {}

    async def rich_edit(message, doc, reply_markup=None):
        captured.update(doc=doc, markup=reply_markup)
        return message

    async def go():
        await reset_db()
        db = MainDB()
        await db.add_bot(4041, 41, "enc:token", "SampleBot", -1001)
        await db.update_setting(4041, "start_message", "<b>Hello</b><script>bad</script>")
        monkeypatch.setattr(rich, "edit", rich_edit)
        query = FakeQuery("startcfg_4041", uid=41)
        await bot_settings.startcfg_callback(FakeClient(), query)
        return query

    query = run(go())
    doc, markup = captured["doc"], captured["markup"]
    classic = doc.classic()
    buttons = [button for row in markup.inline_keyboard for button in row]
    assert isinstance(doc, rich.Doc)
    assert doc.title == "Welcome experience" and "Hello" in classic
    assert "<script>" not in classic and "&lt;script&gt;" in classic
    assert all(button.style == enums.ButtonStyle.PRIMARY for button in buttons[:3])
    assert rich.markup_ok(markup)
    assert not query.answers[-1][1]


def test_welcome_edit_prompt_is_richgram_with_danger_cancel(monkeypatch):
    captured = {}

    async def rich_edit(message, doc, reply_markup=None):
        captured.update(doc=doc, markup=reply_markup)
        return message

    async def go():
        await reset_db()
        db = MainDB()
        await db.add_bot(4042, 42, "enc:token", "SampleBot", -1001)
        monkeypatch.setattr(rich, "edit", rich_edit)
        query = FakeQuery("set_startmsg_4042", uid=42)
        await bot_settings.set_startmsg_callback(FakeClient(), query)
        return query

    query = run(go())
    doc, markup = captured["doc"], captured["markup"]
    button = markup.inline_keyboard[0][0]
    assert isinstance(doc, rich.Doc)
    assert doc.title == "Edit welcome message" and "HTML formatting" in doc.rich()
    assert button.style == enums.ButtonStyle.DANGER
    assert button.callback_data == "startcfg_4042" and rich.markup_ok(markup)
    assert bot_settings._get_state()[42]["action"] == "set_startmsg"


def test_my_bots_list_is_richgram_color_coded_and_sanitizes_names(monkeypatch):
    captured = {}

    async def rich_edit(message, doc, reply_markup=None):
        captured.update(doc=doc, markup=reply_markup)
        return message

    class Workers:
        def get_worker(self, bot_id):
            return object() if bot_id == 4051 else None

    async def clone_limit(_uid):
        return 5

    async def bots(_uid):
        return [
            {"_id": 4051, "bot_username": "active_clone", "is_active": True},
            {"_id": 4052, "bot_username": "<script>bad</script>", "is_active": True},
        ]

    async def go():
        await reset_db()
        monkeypatch.setattr(rich, "edit", rich_edit)
        monkeypatch.setattr(my_bots.main_db, "get_user_bots", bots)
        monkeypatch.setattr("core.plans.clone_limit", clone_limit)
        monkeypatch.setattr("filestore.worker_bot.engine.worker_engine", Workers())
        query = FakeQuery("my_bots", uid=51)
        await my_bots.my_bots_callback(FakeClient(), query)
        return query

    query = run(go())
    doc, markup = captured["doc"], captured["markup"]
    buttons = [button for row in markup.inline_keyboard for button in row]
    callbacks = [button.callback_data for button in buttons]
    assert doc.title == "My bots" and "Live snapshot" in doc.classic()
    assert "<script>" not in str([button.text for button in buttons])
    assert any("@active_clone" in button.text for button in buttons)
    assert any("@unknown" in button.text for button in buttons)
    assert "dashboard_4051" in callbacks and "dashboard_4052" in callbacks
    assert any(button.style == enums.ButtonStyle.SUCCESS for button in buttons)
    assert rich.markup_ok(markup)
    assert not query.answers[-1][1]

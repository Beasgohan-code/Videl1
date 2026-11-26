"""Main-bot Group Management clone settings panel: pure rendering checks."""
from tests.harness import load_all

load_all()


def test_group_clone_settings_panel_text_and_buttons():
    from filestore.main_bot.plugins.group_clone_settings import (
        format_chat_line, settings_panel_buttons, settings_panel_text,
    )

    empty = settings_panel_text("@GroupCloneBot", [])
    assert "No groups have configured" in empty

    chats = [{
        "chat_id": -100123, "shield": True, "greetings": True,
        "dm_alerts": False, "captcha_on": True, "night_on": False,
        "guard_kinds": ["links", "media"], "lang": "hi",
    }]
    line = format_chat_line(-100123, chats[0])
    assert "-100123" in line and "shield" in line and "links" in line
    text = settings_panel_text("@GroupCloneBot", chats)
    assert "1 configured group(s)" in text

    markup = settings_panel_buttons(55, chats)
    callbacks = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "clgset_t_55_-100123_greetings" in callbacks
    assert "clgset_55" in callbacks and "clprofile_55" in callbacks

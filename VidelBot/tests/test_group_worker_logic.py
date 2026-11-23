"""Unit tests for the standalone clone Group Management worker logic/store."""
from types import SimpleNamespace

from tests.harness import run


def test_group_worker_duration_greetings_and_violation_logic():
    from group_worker.logic import (
        content_violation, default_settings, parse_duration, render_greeting,
    )

    assert parse_duration("30m").total_seconds() == 1800
    assert parse_duration("2w").total_seconds() == 1209600
    assert parse_duration("0m") is None and parse_duration("soon") is None

    text = render_greeting("Hi {name} in {chat} ({id})", name="Ada", chat="Devs", user_id=7)
    assert text == "Hi Ada in Devs (7)"
    assert default_settings()["warn_limit"] == 3

    link = SimpleNamespace(text="see https://example.com", caption=None)
    photo = SimpleNamespace(text=None, caption=None, photo=object())
    clean = SimpleNamespace(text="hello", caption=None)
    assert content_violation(link, ["links"]) == "links"
    assert content_violation(photo, ["media"]) == "media"
    assert content_violation(clean, ["links", "media"]) == ""
    assert content_violation(clean, ["all"]) == "all"


def test_group_worker_warn_ledger_and_flood_watch():
    from group_worker.logic import FloodWatch, WarnLedger

    warns = WarnLedger(limit=3)
    assert warns.add(-1, 9) == (1, False)
    assert warns.add(-1, 9) == (2, False)
    assert warns.add(-1, 9) == (3, True)   # resets after reaching the limit
    assert warns.count(-1, 9) == 0
    warns.add(-1, 9)
    assert warns.forgive(-1, 9) == 0

    flood = FloodWatch()
    assert not flood.hit(-1, 9, limit=3, window=10.0, now=100.0)
    assert not flood.hit(-1, 9, limit=3, window=10.0, now=101.0)
    assert flood.hit(-1, 9, limit=3, window=10.0, now=102.0)
    assert not flood.hit(-1, 9, limit=0, window=10.0, now=103.0)  # off


class _FakeCollection:
    def __init__(self):
        self.docs = {}

    async def find_one(self, key):
        doc = self.docs.get(tuple(sorted(key.items())))
        return dict(doc) if doc else None

    async def update_one(self, key, update, upsert=False):
        k = tuple(sorted(key.items()))
        doc = self.docs.setdefault(k, dict(key))
        doc.update(update.get("$set", {}))

    async def find_one_and_update(self, key, update, upsert=False, return_document=False):
        k = tuple(sorted(key.items()))
        doc = self.docs.setdefault(k, dict(key))
        doc["count"] = doc.get("count", 0) + update["$inc"]["count"]
        return dict(doc)

    async def delete_one(self, key):
        self.docs.pop(tuple(sorted(key.items())), None)


def test_group_worker_store_is_scoped_per_clone_and_chat():
    from group_worker.store import GroupWorkerStore

    async def scenario():
        db = {"group_worker_settings": _FakeCollection(), "group_worker_warns": _FakeCollection()}
        store = GroupWorkerStore(db)
        await store.update_settings(111, -5, {"shield": True, "not_a_setting": 1})
        mine = await store.get_settings(111, -5)
        other_clone = await store.get_settings(222, -5)
        other_chat = await store.get_settings(111, -6)
        assert mine["shield"] is True and "not_a_setting" not in mine
        assert other_clone["shield"] is False and other_chat["shield"] is False

        assert await store.add_warn(111, -5, 9, 3) == (1, False)
        assert await store.add_warn(111, -5, 9, 3) == (2, False)
        assert await store.add_warn(111, -5, 9, 3) == (3, True)
        assert await store.warn_count(111, -5, 9) == 0  # reset at limit
        assert await store.warn_count(222, -5, 9) == 0  # other clone unaffected

    run(scenario())


def test_group_worker_uses_normal_rose_style_commands_and_settings_ui():
    from group_worker.handlers import GROUP_WORKER_COMMANDS, _BotHandlers
    from group_worker.logic import default_settings

    names = [name for name, _ in GROUP_WORKER_COMMANDS]
    assert not any(name.startswith("mod") for name in names)
    assert "panel" not in names
    for expected in ("settings", "ban", "tban", "sban", "unban", "kick", "mute",
                     "tmute", "unmute", "warn", "unwarn", "purge", "pin", "unpin",
                     "rules", "setrules", "filter", "stop", "filters", "report",
                     "setwelcome", "setgoodbye", "save", "get", "notes", "clear"):
        assert expected in names

    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None)
    settings = default_settings()
    main = handlers._settings_markup("main", settings)
    labels = [btn.text for row in main.inline_keyboard for btn in row]
    assert any("Shield" in label for label in labels)
    assert any("Greetings" in label for label in labels)
    callbacks = [btn.callback_data for row in main.inline_keyboard for btn in row]
    assert all(cb.startswith("gw:42:") for cb in callbacks)

    shield = handlers._settings_markup("shield", {**settings, "guard_kinds": ["links"]})
    shield_labels = [btn.text for row in shield.inline_keyboard for btn in row]
    assert any(label.startswith("✅") and "Links" in label for label in shield_labels)
    assert any("Back" in label for label in shield_labels)

    text = handlers._settings_text("main", settings, "Dev <Hub>")
    assert "SETTINGS" in text and "admins" in text and "<Hub>" not in text


def test_group_worker_reply_matching_and_new_defaults():
    from group_worker.logic import default_settings, match_reply

    settings = default_settings()
    assert settings["dm_alerts"] is True and settings["rules"] == ""
    assert settings["replies"] == {}

    replies = {"hello": "Hi there!", "price list": "See /rules"}
    assert match_reply("well HELLO friend", replies) == ("hello", "Hi there!")
    assert match_reply("the price list please", replies) == ("price list", "See /rules")
    assert match_reply("helloagain", replies) is None       # whole-word only
    assert match_reply("/hello", replies) is None            # commands ignored
    assert match_reply("", replies) is None


def test_group_worker_quiet_hours_and_night_settings_ui():
    from group_worker.handlers import _BotHandlers
    from group_worker.logic import default_settings, in_quiet_hours

    assert in_quiet_hours(23, 23, 7) and in_quiet_hours(3, 23, 7)
    assert not in_quiet_hours(7, 23, 7) and not in_quiet_hours(12, 23, 7)
    assert in_quiet_hours(10, 9, 17) and not in_quiet_hours(17, 9, 17)
    assert not in_quiet_hours(5, 5, 5)

    settings = default_settings()
    assert settings["night_on"] is False and settings["notes"] == {}
    handlers = _BotHandlers(bot_id=7, owner_id=1, store=None)
    night = handlers._settings_markup("night", settings)
    callbacks = [b.callback_data for row in night.inline_keyboard for b in row]
    assert "gw:7:tog:night_on" in callbacks and "gw:7:nightstart" in callbacks
    text = handlers._settings_text("night", {**settings, "night_on": True}, "G")
    assert "23:00–07:00 UTC" in text


def test_group_worker_join_watch_and_security_settings_ui():
    from group_worker.handlers import _BotHandlers
    from group_worker.logic import JoinWatch, default_settings

    watch = JoinWatch()
    for i in range(5):
        assert watch.record(-1, now=100.0 + i) is False
    assert watch.record(-1, now=105.0) is True          # 6th join in a minute trips
    assert watch.locked(-1, now=106.0) is True
    assert watch.record(-1, now=107.0) is True          # still locked
    assert watch.locked(-1, now=10000.0) is False       # lock expired
    assert watch.record(-2, now=100.0) is False         # other chat unaffected

    settings = default_settings()
    assert settings["captcha_on"] is False and settings["raid_on"] is False
    handlers = _BotHandlers(bot_id=7, owner_id=1, store=None)
    page = handlers._settings_markup("security", settings)
    callbacks = [b.callback_data for row in page.inline_keyboard for b in row]
    assert "gw:7:tog:captcha_on" in callbacks and "gw:7:tog:raid_on" in callbacks
    text = handlers._settings_text("security", settings, "G")
    assert "CAPTCHA" in text and "Anti-raid" in text

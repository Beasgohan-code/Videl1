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


def test_group_worker_uses_normal_rose_style_commands_and_polished_panel():
    from group_worker.handlers import GROUP_WORKER_COMMANDS, _BotHandlers
    from group_worker.logic import default_settings

    names = [name for name, _ in GROUP_WORKER_COMMANDS]
    assert not any(name.startswith("mod") for name in names)
    for expected in ("ban", "tban", "sban", "unban", "kick", "mute", "tmute",
                     "unmute", "warn", "unwarn", "purge", "panel"):
        assert expected in names

    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None)
    markup = handlers._panel_markup(default_settings())
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert any("Shield" in label for label in labels)
    assert any("links" in label for label in labels)
    assert any("Flood watch" in label for label in labels)
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert all(cb.startswith("gw:42:") for cb in callbacks)

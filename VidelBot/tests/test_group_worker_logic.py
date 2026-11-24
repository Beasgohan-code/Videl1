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
        db = {"group_worker_settings": _FakeCollection(), "group_worker_warns": _FakeCollection(), "group_worker_modlog": _FakeCollection(), "group_worker_afk": _FakeCollection(), "group_worker_feds": _FakeCollection(), "group_worker_fed_bans": _FakeCollection()}
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


def test_group_worker_uses_standard_commands_and_settings_ui():
    from group_worker.handlers import GROUP_WORKER_COMMANDS, _BotHandlers
    from group_worker.logic import default_settings

    names = [name for name, _ in GROUP_WORKER_COMMANDS]
    assert not any(name in ("modwarn","modmute","modkick","modban","modunban","modpurge") for name in names)
    assert "panel" not in names
    for expected in ("settings", "ban", "tban", "sban", "unban", "kick", "mute",
                     "tmute", "unmute", "warn", "unwarn", "purge", "pin", "unpin",
                     "rules", "setrules", "filter", "stop", "filters", "report",
                     "setwelcome", "setgoodbye", "save", "get", "notes", "clear", "whois"):
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


def test_group_worker_whois_card_buttons_and_styled_toggles():
    from group_worker.handlers import GROUP_WORKER_COMMANDS, _BotHandlers, _btn
    from group_worker.logic import default_settings

    assert "whois" in [name for name, _ in GROUP_WORKER_COMMANDS]
    button = _btn("Test", "gw:1:close", style="success")
    assert button.text == "Test" and button.callback_data == "gw:1:close"

    handlers = _BotHandlers(bot_id=7, owner_id=1, store=None)
    main = handlers._settings_markup("main", default_settings())
    shield_btn = main.inline_keyboard[0][0]
    assert shield_btn.callback_data == "gw:7:page:shield"


def test_group_worker_approvals_exemption_logic():
    from group_worker.logic import default_settings, is_approved

    settings = default_settings()
    assert settings["approved"] == {}
    assert not is_approved(settings, 42)
    settings["approved"] = {"42": "trusted"}
    assert is_approved(settings, 42)
    assert not is_approved(settings, 43)

    from group_worker.handlers import GROUP_WORKER_COMMANDS
    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    for cmd in ("approve", "unapprove", "approval", "approved", "unapproveall"):
        assert cmd in names


def test_group_worker_connections_commands_registered():
    from group_worker.handlers import GROUP_WORKER_COMMANDS

    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    for cmd in ("connect", "disconnect", "connection"):
        assert cmd in names

    from group_worker.handlers import _BotHandlers
    handlers = _BotHandlers(bot_id=1, owner_id=2, store=None)
    handlers.connections[99] = -100123
    assert handlers.connections[99] == -100123


def test_group_worker_no_plugin_style_commands_and_log_defaults():
    from group_worker.handlers import GROUP_WORKER_COMMANDS
    from group_worker.logic import default_settings

    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    assert not any(n.startswith("g") and n not in ("get",) for n in names), names
    assert "gsettings" not in names and "gban" not in names and "gcheck" not in names
    for cmd in ("setlog", "logchannel", "unsetlog"):
        assert cmd in names
    assert default_settings()["log_chat"] == 0


def test_group_worker_welcome_family_and_media_defaults():
    from group_worker.handlers import GROUP_WORKER_COMMANDS
    from group_worker.logic import default_settings

    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    for cmd in ("welcome", "resetwelcome", "resetgoodbye"):
        assert cmd in names
    settings = default_settings()
    assert settings["greet_media"] == "" and settings["clean_welcome"] is False

    from group_worker.handlers import _BotHandlers
    handlers = _BotHandlers(bot_id=7, owner_id=1, store=None)
    greet = handlers._settings_markup("greet", settings)
    callbacks = [b.callback_data for row in greet.inline_keyboard for b in row]
    assert "gw:7:tog:clean_welcome" in callbacks


def test_group_worker_language_and_clean_service():
    from group_worker.handlers import GROUP_WORKER_COMMANDS, _BotHandlers
    from group_worker.logic import default_settings, tr

    assert "language" in [n for n, _ in GROUP_WORKER_COMMANDS]
    settings = default_settings()
    assert settings["lang"] == "en" and settings["clean_service"] is False
    assert tr(settings, "settings") == "SETTINGS"
    assert tr({**settings, "lang": "hi"}, "settings") == "सेटिंग्स"
    assert tr({**settings, "lang": "xx"}, "settings") == "SETTINGS"  # safe fallback

    handlers = _BotHandlers(bot_id=7, owner_id=1, store=None)
    main = handlers._settings_markup("main", settings)
    callbacks = [b.callback_data for row in main.inline_keyboard for b in row]
    assert "gw:7:lang" in callbacks and "gw:7:tog:clean_service" in callbacks
    text = handlers._settings_text("main", {**settings, "lang": "es"}, "G")
    assert "AJUSTES" in text and "<blockquote>" in text


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *_a, **_k):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs[:length] if length else self._docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeLogCollection(_FakeCollection):
    def __init__(self):
        super().__init__()
        self.rows = []

    async def insert_one(self, doc):
        doc = dict(doc)
        doc.setdefault("_id", len(self.rows) + 1)
        self.rows.append(doc)

    def find(self, key, *_a, **_k):
        rows = [r for r in self.rows
                if all(r.get(k) == v for k, v in key.items())]
        return _FakeCursor(list(reversed(rows)))

    async def delete_many(self, query):
        ids = set(query.get("_id", {}).get("$in", []))
        self.rows = [r for r in self.rows if r.get("_id") not in ids]


def test_group_worker_modlog_records_and_scopes_actions():
    from group_worker.handlers import GROUP_WORKER_COMMANDS
    from group_worker.store import GroupWorkerStore

    assert "modlog" in [n for n, _ in GROUP_WORKER_COMMANDS]

    async def scenario():
        db = {"group_worker_settings": _FakeCollection(),
              "group_worker_warns": _FakeCollection(),
              "group_worker_modlog": _FakeLogCollection(), "group_worker_afk": _FakeLogCollection(), "group_worker_feds": _FakeLogCollection(), "group_worker_fed_bans": _FakeLogCollection()}
        store = GroupWorkerStore(db)
        await store.log_action(1, -5, "ban", 9, 2, "spam")
        await store.log_action(1, -5, "warn", 8, 2, "")
        await store.log_action(2, -5, "ban", 9, 2, "other clone")
        entries = await store.recent_actions(1, -5)
        assert [e["action"] for e in entries] == ["warn", "ban"]  # newest first
        assert entries[1]["reason"] == "spam"
        assert await store.recent_actions(1, -6) == []

    run(scenario())


def test_group_worker_afk_store_and_import_sanitizer():
    from group_worker.logic import default_settings, sanitize_settings_import
    from group_worker.store import GroupWorkerStore

    cleaned = sanitize_settings_import(
        {"shield": True, "warn_limit": 5, "guard_kinds": ["links"],
         "replies": {"hi": "hello"}, "unknown": 1, "lang": "hi"},
        default_settings())
    assert cleaned == {"shield": True, "warn_limit": 5, "guard_kinds": ["links"],
                       "replies": {"hi": "hello"}, "lang": "hi"}
    assert sanitize_settings_import({"shield": "yes"}, default_settings()) is None
    assert sanitize_settings_import(["nope"], default_settings()) is None

    async def scenario():
        db = {"group_worker_settings": _FakeCollection(),
              "group_worker_warns": _FakeCollection(),
              "group_worker_modlog": _FakeLogCollection(),
              "group_worker_afk": _FakeLogCollection(), "group_worker_feds": _FakeLogCollection(), "group_worker_fed_bans": _FakeLogCollection()}
        store = GroupWorkerStore(db)
        await store.set_afk(1, -5, 9, "Ada", "lunch")
        doc = await store.get_afk(1, -5, 9)
        assert doc["name"] == "Ada" and doc["reason"] == "lunch"
        assert await store.get_afk(2, -5, 9) is None  # other clone isolated
        cleared = await store.clear_afk(1, -5, 9)
        assert cleared["name"] == "Ada"
        assert await store.get_afk(1, -5, 9) is None

    run(scenario())

    from group_worker.handlers import GROUP_WORKER_COMMANDS
    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    assert "afk" in names and "export" in names and "import" in names


def test_group_worker_sticker_blocklist_logic():
    from types import SimpleNamespace

    from group_worker.handlers import GROUP_WORKER_COMMANDS
    from group_worker.logic import default_settings, sticker_blocked

    assert default_settings()["blocked_stickers"] == []
    sticker_msg = SimpleNamespace(sticker=SimpleNamespace(file_unique_id="AgADx"))
    text_msg = SimpleNamespace(sticker=None, text="hi")
    assert sticker_blocked(sticker_msg, ["AgADx"]) is True
    assert sticker_blocked(sticker_msg, ["other"]) is False
    assert sticker_blocked(text_msg, ["AgADx"]) is False

    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    for cmd in ("blocksticker", "unblocksticker", "blockedstickers"):
        assert cmd in names


class _FakeFedCollection(_FakeLogCollection):
    async def find_one(self, key):
        for row in self.rows:
            if all(row.get(k) == v for k, v in key.items()):
                return dict(row)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))

    async def update_one(self, key, update, upsert=False):
        for row in self.rows:
            if all(row.get(k) == v for k, v in key.items()):
                row.update(update.get("$set", {}))
                return
        if upsert:
            doc = dict(key)
            doc.update(update.get("$set", {}))
            self.rows.append(doc)

    async def delete_one(self, key):
        self.rows = [r for r in self.rows
                     if not all(r.get(k) == v for k, v in key.items())]


def test_group_worker_federation_store_flow():
    from group_worker.handlers import GROUP_WORKER_COMMANDS
    from group_worker.store import GroupWorkerStore

    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    for cmd in ("createfed", "joinfed", "leavefed", "fedinfo", "fedban", "fedunban", "fedbans"):
        assert cmd in names

    async def scenario():
        db = {"group_worker_settings": _FakeCollection(),
              "group_worker_warns": _FakeCollection(),
              "group_worker_modlog": _FakeLogCollection(),
              "group_worker_afk": _FakeLogCollection(),
              "group_worker_feds": _FakeFedCollection(),
              "group_worker_fed_bans": _FakeFedCollection()}
        store = GroupWorkerStore(db)
        fed = await store.create_fed(1, 99, "Safe Groups", "fed-test1")
        assert fed["chats"] == []
        await store.set_fed_chats(1, "fed-test1", [-1, -2])
        assert (await store.get_fed(1, "fed-test1"))["chats"] == [-1, -2]
        assert await store.get_fed(2, "fed-test1") is None  # other clone isolated

        await store.fed_ban(1, "fed-test1", 7, "spam")
        assert (await store.get_fed_ban(1, "fed-test1", 7))["reason"] == "spam"
        assert await store.get_fed_ban(1, "fed-test1", 8) is None
        assert await store.get_fed_ban(1, "", 7) is None
        await store.fed_unban(1, "fed-test1", 7)
        assert await store.get_fed_ban(1, "fed-test1", 7) is None

    run(scenario())


def test_group_worker_rich_start_pages_and_buttons():
    from group_worker.handlers import _BotHandlers

    handlers = _BotHandlers(bot_id=42, owner_id=777, store=None)
    home = handlers._start_text("home")
    help_text = handlers._start_text("help")
    about = handlers._start_text("about")
    assert "Add me to a group" in home and "Help" in home
    assert "/settings" in help_text and "/warn" in help_text
    assert "Group Management clone" in about

    markup = handlers._start_markup("MyGroupBot")
    rows = markup.inline_keyboard
    assert rows[0][0].url == "https://t.me/MyGroupBot?startgroup=true"
    callbacks = [b.callback_data for row in rows for b in row if b.callback_data]
    assert "gw:42:nav:help" in callbacks and "gw:42:nav:about" in callbacks
    urls = [b.url for row in rows for b in row if b.url]
    assert "tg://user?id=777" in urls

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
                     "rules", "setrules", "filter", "mfilter", "stop", "filters", "stopall", "report",
                     "setwelcome", "setgoodbye", "save", "get", "notes", "clear", "whois",
                     "anime", "manga", "character", "gettags", "browse", "anisettings",
                     "airing", "topanime", "antichannel"):
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
    assert text.startswith("<b>") and "group admins" in text.lower() and "<Hub>" not in text


def test_group_worker_reply_matching_and_new_defaults():
    from group_worker.logic import default_settings, match_reply, parse_filter_command, render_filter_reply
    from types import SimpleNamespace

    settings = default_settings()
    assert settings["dm_alerts"] is True and settings["rules"] == ""
    assert settings["replies"] == {} and settings["media_replies"] == {}
    assert settings["media_reply_captions"] == {}

    replies = {"hello": "Hi there!", "price list": "See /rules"}
    assert match_reply("well HELLO friend", replies) == ("hello", "Hi there!")
    assert match_reply("the price list please", replies) == ("price list", "See /rules")
    assert match_reply("the hello price list", {"hello": "short", "hello price": "long"}) == (
        "hello price", "long")
    assert match_reply("hello!", replies) == ("hello", "Hi there!")
    assert match_reply("helloagain", replies) is None       # whole-word only
    assert match_reply("/hello", replies) is None            # commands ignored
    assert match_reply("", replies) is None
    assert parse_filter_command('/filter "red flag" **Hello {first}!**') == (
        "red flag", "**Hello {first}!**")
    assert parse_filter_command("/filter missing-reply") is None
    user = SimpleNamespace(id=9, first_name="A_lice", last_name="Member", username="alice")
    chat = SimpleNamespace(id=-1001, title="Test [group]")
    rendered = render_filter_reply(
        "{mention} / {first} / {last} / {fullname} / {username} / {id} / {chatname} / {chatid}",
        user, chat)
    assert r"[A\_lice Member](tg://user?id=9)" in rendered
    assert r"A\_lice" in rendered and "@alice" in rendered
    assert "-1001" in rendered and "Test \\[group\\]" in rendered


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
    assert not any(n.startswith("g") and n not in ("get", "gettags") for n in names), names
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
    doc = handlers._settings_doc("main", {**settings, "lang": "es"}, "G")
    assert "AJUSTES" in doc.rich() and "<table bordered striped>" in doc.rich()


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
    assert sticker_blocked(sticker_msg, [[], "AgADx"]) is True
    assert sticker_blocked(sticker_msg, {"AgADx"}) is True
    assert sticker_blocked(sticker_msg, "AgADx") is False

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


def test_group_worker_rich_start_pages_and_buttons(monkeypatch):
    import config
    from group_worker.handlers import _BotHandlers

    monkeypatch.setattr(config, "SUPPORT_URL", "https://t.me/videl_support")
    monkeypatch.setattr(config, "UPDATES_URL", "https://t.me/videl_updates")
    handlers = _BotHandlers(bot_id=42, owner_id=777, store=None)
    home = handlers._start_doc("home")
    help_doc = handlers._start_doc("help")
    about = handlers._start_doc("about")
    assert "Add me to a group" in home.classic() and "Tap Help" in home.classic()
    assert "registered commands" in help_doc.sub
    assert "/commands" in help_doc.rich()
    assert "Group Management clone" in about.title

    markup = handlers._start_markup("MyGroupBot")
    rows = markup.inline_keyboard
    assert rows[0][0].url == "https://t.me/MyGroupBot?startgroup=true"
    callbacks = [b.callback_data for row in rows for b in row if b.callback_data]
    assert {"gw:42:nav:help", "gw:42:nav:about", "gw:42:nav:features",
            "gw:42:nav:commands"}.issubset(set(callbacks))
    assert not any(button.callback_data == "gw:42:nav:home"
                   for row in rows for button in row)
    help_markup = handlers._start_markup("MyGroupBot", "help")
    help_callbacks = [b.callback_data for row in help_markup.inline_keyboard
                      for b in row if b.callback_data]
    assert "gw:42:nav:features" in help_callbacks
    assert any("commands" in button.text.lower()
               for row in help_markup.inline_keyboard for button in row)
    assert "/commands" in handlers._start_doc("help").rich()
    urls = [b.url for row in rows for b in row if b.url]
    assert "tg://user?id=777" in urls
    assert "https://t.me/videl_support" in urls
    assert "https://t.me/videl_updates" in urls
    labels = [b.text for row in rows for b in row]
    assert any("Support" in label for label in labels)
    assert any("Updates" in label for label in labels)


def test_group_worker_start_and_settings_use_rich_docs_and_colored_buttons():
    from group_worker.handlers import _BotHandlers
    from group_worker.logic import default_settings
    from pyrogram.enums import ButtonStyle

    handlers = _BotHandlers(bot_id=42, owner_id=777, store=None)
    home = handlers._start_doc("home")
    help_doc = handlers._start_doc("help")
    settings = handlers._settings_doc("main", default_settings(), "Garden group")
    assert "<h1>🛡️ Group Management</h1>" in home.rich()
    assert "<table bordered striped>" in help_doc.rich()
    assert settings.sub == "Garden group"
    buttons = [button for row in handlers._settings_markup("main", default_settings()).inline_keyboard
               for button in row]
    assert buttons and all(button.style != ButtonStyle.DEFAULT for button in buttons)


def test_group_worker_start_links_fail_closed_and_support_falls_back_to_owner(monkeypatch):
    import config
    from group_worker.handlers import _BotHandlers

    monkeypatch.setattr(config, "SUPPORT_URL", "javascript:alert(1)")
    monkeypatch.setattr(config, "CONTACT_URL", "http://unsafe.example")
    monkeypatch.setattr(config, "UPDATES_URL", "data:text/html,unsafe")
    handlers = _BotHandlers(bot_id=42, owner_id=777, store=None)
    rows = handlers._start_markup("bad username!").inline_keyboard
    urls = [b.url for row in rows for b in row if b.url]
    labels = [b.text for row in rows for b in row]
    assert "https://t.me/bad username!?startgroup=true" not in urls
    assert "tg://user?id=777" in urls
    assert any("Support" in label for label in labels)
    assert any("Updates" in label for label in labels)
    assert "https://github.com/Beasgohan-code/Videl1/releases" in urls


def test_group_worker_admin_tools_registered():
    from group_worker.handlers import GROUP_WORKER_COMMANDS, _BotHandlers

    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    for cmd in ("promote", "demote", "title", "chatinfo"):
        assert cmd in names
    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None)
    from group_worker.handlers import _command_help_categories
    command_docs = "".join(handlers._start_doc(f"commands_{slug}").rich()
                            for slug, _, _ in _command_help_categories())
    assert "<td>/promote</td>" in command_docs


def test_group_worker_engine_stop_all_stops_every_clone():
    from group_worker.engine import GroupWorkerEngine

    class FakeApp:
        is_connected = False
        is_initialized = False

        def __init__(self):
            self.stopped = False

        async def stop(self, block=True):
            self.stopped = True

    async def scenario():
        engine = GroupWorkerEngine(store=object())
        apps = {1: FakeApp(), 2: FakeApp()}
        engine.workers.update(apps)
        assert await engine.stop_all() == 2
        assert engine.active_count == 0
        assert all(a.stopped for a in apps.values())
        assert await engine.stop_all() == 0

    run(scenario())


def test_group_worker_lock_kind_aliases():
    from group_worker.logic import lock_kind

    assert lock_kind("links") == "links"
    assert lock_kind("LINK") == "links"
    assert lock_kind("url") == "links"
    assert lock_kind("Sticker") == "stickers"
    assert lock_kind("fwd") == "forwards"
    assert lock_kind("everything") == "all"
    assert lock_kind("commands") == ""
    assert lock_kind("") == ""


def test_group_worker_locks_registered():
    from group_worker.handlers import GROUP_WORKER_COMMANDS, _BotHandlers

    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    for cmd in ("lock", "unlock", "locks"):
        assert cmd in names
    from group_worker.handlers import _command_help_categories
    docs = [_BotHandlers(bot_id=42, owner_id=1, store=None)
            ._start_doc(f"commands_{slug}").rich()
            for slug, _, _ in _command_help_categories()]
    assert any("<td>/lock</td>" in doc for doc in docs)


def test_group_worker_disabled_commands_logic():
    from group_worker.logic import NEVER_DISABLE, command_blocked, normalize_cmd

    settings = {"disabled_cmds": ["afk", "whois", "commands"]}
    assert command_blocked(settings, "afk") is True
    assert "commands" in NEVER_DISABLE
    assert command_blocked(settings, "/commands") is False
    assert command_blocked(settings, "/AFK@MyBot") is True
    assert command_blocked(settings, "notes") is False
    for protected in NEVER_DISABLE:
        assert command_blocked({"disabled_cmds": list(NEVER_DISABLE)}, protected) is False
    assert normalize_cmd("/Warn@Bot") == "warn"
    assert command_blocked({}, "afk") is False


def test_group_worker_rich_ui_tables_and_new_commands():
    from group_worker.handlers import GROUP_WORKER_COMMANDS, _BotHandlers, _table
    from group_worker.logic import default_settings

    table = _table([("Alpha", "1"), ("Longer label", "✅ on")])
    assert table.startswith("<pre>") and table.endswith("</pre>")
    assert "Alpha         1" in table

    names = [n for n, _ in GROUP_WORKER_COMMANDS]
    for cmd in ("disable", "enable", "disabled", "adminlist", "stats"):
        assert cmd in names

    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None)
    main = handlers._settings_doc("main", default_settings(), "G").rich()
    assert "<table bordered striped>" in main and "Shield" in main and "Language" in main
    help_doc = handlers._start_doc("help")
    assert "<table bordered striped>" in help_doc.rich()
    from group_worker.handlers import _command_help_categories
    categories = _command_help_categories()
    categorized_names = [name for _, _, commands in categories for name, _ in commands]
    registered_names = [name for name, _ in GROUP_WORKER_COMMANDS]
    assert sorted(categorized_names) == sorted(registered_names)
    assert len(categorized_names) == len(set(categorized_names))
    assert "91 commands" in handlers._start_doc("commands").sub
    from group_worker.handlers import _command_search_results
    search_matches = _command_search_results("/ban timed")
    assert any(name == "tban" for name, _, _ in search_matches)
    assert not _command_search_results("no-such-command-xyz")
    assert "gw:42:nav:commands" in [button.callback_data
                                    for row in handlers._start_markup("MyGroupBot", "commands_search").inline_keyboard
                                    for button in row if button.callback_data]
    command_docs = [handlers._start_doc(f"commands_{slug}").rich()
                    for slug, _, _ in categories]
    all_commands = "".join(command_docs)
    assert all(f"<td>/{name}</td>" in all_commands for name, _ in GROUP_WORKER_COMMANDS)
    assert len(categories) > 1
    category_links = [button.callback_data for row in handlers._start_markup("MyGroupBot", "commands").inline_keyboard
                      for button in row if button.callback_data]
    assert all(f"gw:42:nav:commands_{slug}" in category_links for slug, _, _ in categories)
    second_slug = categories[1][0]
    category_markup = handlers._start_markup("MyGroupBot", f"commands_{second_slug}")
    category_callbacks = [button.callback_data for row in category_markup.inline_keyboard
                          for button in row if button.callback_data]
    assert f"gw:42:nav:commands_{categories[0][0]}" in category_callbacks
    assert f"gw:42:nav:commands_{categories[2][0]}" in category_callbacks
    feature_doc = handlers._start_doc("features").rich()
    assert "<table bordered striped>" in feature_doc and "Content shield" in feature_doc
    about = handlers._start_doc("about")
    assert "Federations" in about.rich() and "<table bordered striped>" in about.rich()


def test_group_worker_flood_controls_are_bounded_and_visible():
    from group_worker.handlers import _BotHandlers
    from group_worker.logic import FLOOD_LIMITS, FLOOD_WINDOWS, default_settings, next_flood_value

    assert next_flood_value(6, FLOOD_LIMITS, 6) == 8
    assert next_flood_value(FLOOD_LIMITS[-1], FLOOD_LIMITS, 6) == FLOOD_LIMITS[0]
    assert next_flood_value(10.0, FLOOD_WINDOWS, 10) == 15
    assert next_flood_value("bad", FLOOD_WINDOWS, 10) == 10
    assert next_flood_value(float("inf"), FLOOD_LIMITS, 6) == 6

    handlers = _BotHandlers(bot_id=42, owner_id=1, store=None)
    settings = {**default_settings(), "flood_limit": 8, "flood_window": 15.0}
    page = handlers._settings_markup("flood", settings)
    buttons = [button for row in page.inline_keyboard for button in row]
    by_callback = {button.callback_data: button.text for button in buttons}
    assert by_callback["gw:42:floodlimit"] == "🎚 Limit: 8 msgs"
    assert by_callback["gw:42:floodwindow"] == "⏱ Window: 15s"
    assert "gw:42:tog:flood" in by_callback



def test_group_worker_antichannel_match_exempts_anonymous_group_sender():
    from group_worker.logic import default_settings, is_external_channel_post
    from group_worker.handlers import _BotHandlers
    from types import SimpleNamespace

    assert default_settings()["anti_channel"] is False
    external = SimpleNamespace(chat=SimpleNamespace(id=-1001),
                               sender_chat=SimpleNamespace(id=-2002, type="channel"),
                               is_automatic_forward=False)
    anonymous_group_admin = SimpleNamespace(chat=SimpleNamespace(id=-1001),
                                             sender_chat=SimpleNamespace(id=-1001, type="supergroup"),
                                             is_automatic_forward=False)
    automatic_forward = SimpleNamespace(chat=SimpleNamespace(id=-1001),
                                        sender_chat=SimpleNamespace(id=-2002, type="channel"),
                                        is_automatic_forward=True)
    no_sender = SimpleNamespace(chat=SimpleNamespace(id=-1001), sender_chat=None)
    assert is_external_channel_post(external)
    assert not is_external_channel_post(anonymous_group_admin)
    assert is_external_channel_post(automatic_forward)
    assert not is_external_channel_post(no_sender)

    handlers = _BotHandlers(bot_id=12, owner_id=4, store=None)
    page = handlers._settings_markup("security", {**default_settings(), "anti_channel": True})
    buttons = [button for row in page.inline_keyboard for button in row]
    assert any(button.callback_data == "gw:12:tog:anti_channel" for button in buttons)
    security = handlers._settings_doc("security", {**default_settings(), "anti_channel": True}).rich()
    assert "Anti-channel" in security and "✅ on" in security
    assert "anonymous group admins are exempt" in security


def test_group_worker_filter_import_bounds_and_validates_media_file_ids():
    from group_worker.logic import default_settings, sanitize_settings_import

    defaults = default_settings()
    file_id = "F" * 1200
    imported = sanitize_settings_import({
        "media_replies": {"  Receipt  ": f"photo:{file_id}"},
        "media_reply_captions": {"receipt": "**For {first}**"},
        "replies": {"Hello": "Hi {first}"},
    }, defaults)
    assert imported is not None
    assert imported["media_replies"]["receipt"] == f"photo:{file_id}"
    assert imported["replies"]["hello"] == "Hi {first}"
    assert sanitize_settings_import({"media_replies": {"x": "unknown:id"}}, defaults) is None
    assert sanitize_settings_import({"replies": {"   ": "empty trigger"}}, defaults) is None
    too_many = {f"word{i}": "reply" for i in range(101)}
    assert sanitize_settings_import({"replies": too_many}, defaults) is None


def test_group_worker_blacklist_terms_are_bounded_and_match_whole_words():
    from group_worker.logic import (
        BLACKLIST_MODES, default_settings, match_blacklisted_word,
        normalize_blacklist_words,
    )

    assert default_settings()["blacklist_mode"] == "del"
    assert default_settings()["blacklist_words"] == []
    assert default_settings()["clean_commands"] is False
    assert BLACKLIST_MODES == ("off", "del", "warn", "ban", "kick", "mute", "tban", "tmute")
    words = normalize_blacklist_words([" Spam ", "spam", "Red Flag", "x" * 70, None])
    assert words == ["spam", "red flag", "x" * 64]
    assert match_blacklisted_word("Please stop SPAM now", words) == "spam"
    assert match_blacklisted_word("spammy content", ["spam"]) is None
    assert match_blacklisted_word("A RED flag appears", ["red flag"]) == "red flag"
    assert match_blacklisted_word("anything", "malformed") is None
    assert normalize_blacklist_words(["x"], limit=0) == []


def test_group_worker_blacklist_and_sticker_commands_are_registered():
    from group_worker.handlers import GROUP_WORKER_COMMANDS

    names = [name for name, _ in GROUP_WORKER_COMMANDS]
    for command in ("blacklist", "addblacklist", "unblacklist", "blacklistmode",
                    "blsticker", "addblsticker", "unblsticker", "cleaner"):
        assert command in names


def test_group_worker_blacklist_timed_actions_are_bounded_and_logged():
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from group_worker.handlers import _BotHandlers, _bounded_blacklist_duration

    class Store:
        def __init__(self):
            self.actions = []
            self.warn_result = (1, False)

        async def add_warn(self, *_args):
            return self.warn_result

        async def log_action(self, *args):
            self.actions.append(args)

        async def get_settings(self, *_args):
            return {"dm_alerts": False}

    class Client:
        def __init__(self):
            self.bans = []

        async def ban_chat_member(self, chat_id, user_id, until_date=None):
            self.bans.append((chat_id, user_id, until_date))

        async def unban_chat_member(self, *_args):
            return None

        async def restrict_chat_member(self, *_args, **_kwargs):
            return None

    class Message:
        chat = SimpleNamespace(id=-1005)
        from_user = SimpleNamespace(id=88, first_name="Member")

        def __init__(self):
            self.deleted = False
            self.replies = []

        async def delete(self):
            self.deleted = True

        async def reply_text(self, text, **_kwargs):
            self.replies.append(text)

    async def scenario():
        store, client, message = Store(), Client(), Message()
        worker = _BotHandlers(bot_id=42, owner_id=9, store=store)
        await worker._apply_blacklist_action(
            client, message,
            {"blacklist_mode": "tban", "blacklist_duration": 600, "dm_alerts": False},
            item="spoiler",
        )
        assert message.deleted is True
        assert len(client.bans) == 1
        chat_id, user_id, until = client.bans[0]
        assert (chat_id, user_id) == (-1005, 88)
        remaining = (until - datetime.now(timezone.utc)).total_seconds()
        assert 590 <= remaining <= 600
        assert store.actions[0][2] == "blacklist_tban"

        failed = Message()
        async def fail_delete():
            raise RuntimeError("no delete rights")
        failed.delete = fail_delete
        before = len(store.actions)
        await worker._apply_blacklist_action(
            client, failed, {"blacklist_mode": "del", "dm_alerts": False}, item="word")
        assert len(store.actions) == before

        store.warn_result = (1, False)
        warned = Message()
        await worker._apply_blacklist_action(
            client, warned, {"blacklist_mode": "warn", "warn_limit": 2, "dm_alerts": False},
            item="word")
        assert warned.deleted is False and warned.replies
        assert store.actions[-1][2] == "blacklist_warn"

        store.warn_result = (2, True)
        escalated = Message()
        await worker._apply_blacklist_action(
            client, escalated,
            {"blacklist_mode": "warn", "warn_limit": 2, "warn_action": "kick", "dm_alerts": False},
            item="sticker")
        assert escalated.deleted is True
        assert (escalated.chat.id, escalated.from_user.id, None) in client.bans
        assert store.actions[-1][2] == "blacklist_kick"

        untouched = Message()
        before = len(store.actions)
        await worker._apply_blacklist_action(
            client, untouched, {"blacklist_mode": "off", "dm_alerts": False}, item="word")
        assert untouched.deleted is False and len(store.actions) == before

    run(scenario())
    assert _bounded_blacklist_duration(10) == 60
    assert _bounded_blacklist_duration(365 * 24 * 60 * 60 * 2) == 365 * 24 * 60 * 60
    assert _bounded_blacklist_duration("bad") == 3600


def test_group_worker_filter_commands_save_text_media_stop_and_owner_clear():
    from types import SimpleNamespace
    from pyrogram import enums
    from tests.harness import FakeClient, FakeMsg
    from group_worker.handlers import _BotHandlers
    from group_worker.logic import default_settings

    class Store:
        def __init__(self):
            self.settings = default_settings()

        async def get_settings(self, *_args):
            return dict(self.settings)

        async def update_settings(self, _bot_id, _chat_id, updates):
            self.settings.update(updates)
            return dict(self.settings)

    class App:
        def __init__(self):
            self.callbacks = {}

        def on_message(self, *_args, **_kwargs):
            return lambda callback: self.callbacks.__setitem__(callback.__name__, callback) or callback

        def on_callback_query(self, *_args, **_kwargs):
            return lambda callback: callback

    async def scenario():
        store, app = Store(), App()
        worker = _BotHandlers(bot_id=42, owner_id=5, store=store)
        worker.attach(app)
        handler = app.callbacks["_filters_cmd"]
        client = FakeClient()
        client.members[(-1007, 5)] = enums.ChatMemberStatus.ADMINISTRATOR
        client.members[(-1007, 6)] = enums.ChatMemberStatus.ADMINISTRATOR

        text_filter = FakeMsg('/filter "red flag" **Hello {first}!**', uid=5,
                              chat_type=enums.ChatType.SUPERGROUP, chat_id=-1007, client=client)
        text_filter.command = ["filter", '"red', 'flag"', '**Hello', '{first}!**']
        await handler(client, text_filter)
        assert store.settings["replies"] == {"red flag": "**Hello {first}!**"}

        media_filter = FakeMsg("/mfilter receipt", uid=5,
                               chat_type=enums.ChatType.SUPERGROUP, chat_id=-1007, client=client)
        media_filter.command = ["mfilter", "receipt"]
        media_filter.reply_to_message = FakeMsg("", uid=6, chat_type=enums.ChatType.SUPERGROUP,
                                                chat_id=-1007, client=client)
        media_filter.reply_to_message.photo = SimpleNamespace(file_id="photo-file-id")
        media_filter.reply_to_message.caption = "**See {chatname}**"
        await handler(client, media_filter)
        assert store.settings["replies"] == {"red flag": "**Hello {first}!**"}
        assert store.settings["media_replies"] == {"receipt": "photo:photo-file-id"}
        assert store.settings["media_reply_captions"] == {"receipt": "**See {chatname}**"}

        stop = FakeMsg("/stop receipt", uid=5, chat_type=enums.ChatType.SUPERGROUP,
                       chat_id=-1007, client=client)
        stop.command = ["stop", "receipt"]
        await handler(client, stop)
        assert store.settings["media_replies"] == {}
        assert store.settings["media_reply_captions"] == {}

        store.settings["replies"] = {"hello": "hi"}
        denied = FakeMsg("/stopall", uid=6, chat_type=enums.ChatType.SUPERGROUP,
                         chat_id=-1007, client=client)
        denied.command = ["stopall"]
        await handler(client, denied)
        assert store.settings["replies"] == {"hello": "hi"}
        owner = FakeMsg("/stopall", uid=5, chat_type=enums.ChatType.SUPERGROUP,
                        chat_id=-1007, client=client)
        owner.command = ["stopall"]
        await handler(client, owner)
        assert not store.settings["replies"] and not store.settings["media_replies"]

    run(scenario())


def test_group_worker_guard_sends_dynamic_text_and_media_filters():
    from pyrogram import enums
    from tests.harness import FakeClient, FakeMsg
    from group_worker.handlers import _BotHandlers
    from group_worker.logic import default_settings

    class Store:
        def __init__(self):
            self.settings = default_settings()
            self.settings["replies"] = {"hello": "**Hi {first}!** From {chatname}."}
            self.settings["media_replies"] = {"receipt": "photo:photo-id"}
            self.settings["media_reply_captions"] = {"receipt": "Saved for {fullname} in {chatname}"}

        async def get_settings(self, *_args):
            return dict(self.settings)

        async def get_afk(self, *_args):
            return None

        async def get_fed_ban(self, *_args):
            return None

    class App:
        def __init__(self):
            self.callbacks = {}

        def on_message(self, *_args, **_kwargs):
            return lambda callback: self.callbacks.__setitem__(callback.__name__, callback) or callback

        def on_callback_query(self, *_args, **_kwargs):
            return lambda callback: callback

    async def scenario():
        store, app, client = Store(), App(), FakeClient()
        worker = _BotHandlers(bot_id=42, owner_id=5, store=store)
        worker.attach(app)
        guard = app.callbacks["_guard"]
        text = FakeMsg("hello!", uid=9, chat_type=enums.ChatType.SUPERGROUP,
                       chat_id=-1007, client=client)
        text.from_user.first_name = "A_lice"
        text.chat.title = "Test group"
        await guard(client, text)
        assert r"Hi A\_lice!" in text.replies[0]
        assert "Test group" in text.replies[0]

        media = FakeMsg("receipt please", uid=9, chat_type=enums.ChatType.SUPERGROUP,
                        chat_id=-1007, client=client)
        media.from_user.first_name = "A_lice"
        media.from_user.last_name = "Member"
        media.chat.title = "Test group"
        await guard(client, media)
        assert client.called("send_photo")
        call = next(call for call in client.calls if call[0] == "send_photo")
        assert call[1][0:2] == (-1007, "photo-id")
        assert r"A\_lice Member" in call[2]["caption"]

    run(scenario())


def test_group_worker_id_reports_own_reply_and_forward_identity_safely():
    from types import SimpleNamespace
    from pyrogram import enums
    from tests.harness import FakeClient, FakeMsg
    from group_worker.handlers import _BotHandlers

    class Store:
        pass

    class App:
        def __init__(self):
            self.callbacks = {}

        def on_message(self, *_args, **_kwargs):
            return lambda callback: self.callbacks.__setitem__(callback.__name__, callback) or callback

        def on_callback_query(self, *_args, **_kwargs):
            return lambda callback: callback

    async def scenario():
        app = App()
        _BotHandlers(bot_id=42, owner_id=5, store=Store()).attach(app)
        handler = app.callbacks["_id"]
        client = FakeClient()
        own = FakeMsg("/id", uid=5, chat_type=enums.ChatType.SUPERGROUP,
                      chat_id=-1007, client=client)
        own.chat.title = "Group <A>"
        own.chat.username = "safe_group"
        await handler(client, own)
        assert "Chat ID:</b> <code>-1007" in own.replies[0]
        assert "Username:</b> @tester" in own.replies[0]
        assert "@safe_group" in own.replies[0]
        assert "Group &lt;A&gt;" not in own.replies[0]  # chat title isn't emitted as markup

        reply = SimpleNamespace(
            id=123, chat=own.chat,
            from_user=SimpleNamespace(id=6, first_name="Reply user", username="reply_user"),
            forward_origin=SimpleNamespace(
                chat=SimpleNamespace(id=-1009, title="Source <channel>", username="source")))
        own.reply_to_message = reply
        await handler(client, own)
        assert "User ID:</b> <code>6" in own.replies[-1]
        assert "Forwarded from:</b> Source &lt;channel&gt;" in own.replies[-1]
        assert "ID <code>-1009" in own.replies[-1]

        reply.forward_origin = SimpleNamespace(sender_user_name="Hidden Sender")
        reply.from_user = None
        await handler(client, own)
        assert "Hidden Sender (hidden identity" in own.replies[-1]
        assert "User ID:</b>" not in own.replies[-1]  # don't misidentify the command sender

        reply.forward_origin = SimpleNamespace(
            chat=SimpleNamespace(id=-1010, title="Origin channel", username="origin"))
        await handler(client, own)
        assert "Origin channel" in own.replies[-1] and "-1010" in own.replies[-1]

    run(scenario())

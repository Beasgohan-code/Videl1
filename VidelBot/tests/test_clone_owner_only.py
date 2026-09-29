"""A clone's settings belong to its owner alone.

Clone admins (added by the owner) keep their day-to-day powers – links, batches, bans, premium grants,
broadcasts – but cannot change settings. Videl's own owner/admins get no back door either, and strangers
are ignored. An ownership transfer takes effect at once, even before the clone restarts.

Identities: clone 4242 is owned by user 5 · 8 = clone admin · 111 = Videl OWNER · 222 = Videl ADMINS · 6 = stranger.
"""
import asyncio
import importlib
import inspect
import pkgutil
import re

import pytest
from pyrogram import ContinuePropagation, StopPropagation
from pyrogram.handlers import CallbackQueryHandler

from tests.harness import FakeClient, FakeQuery, load_all, reset_db, run
from tests.test_audit import LOG_CH, Msg, WorkerClient, _dispatch, _start_worker, _stop

MODULES = load_all()

OWNER, CLONE_ADMIN, VIDEL_OWNER, VIDEL_ADMIN, STRANGER = 5, 8, 111, 222, 6
NOT_OWNERS = (CLONE_ADMIN, VIDEL_OWNER, VIDEL_ADMIN, STRANGER)

# command → the settings it writes (all stored on the clone's document in registered_bots)
SETTINGS_COMMANDS = [
    "/setbuttons Join - https://t.me/x", "/delbuttons", "/sethelp hacked", "/setabout hacked",
    "/maintenance on", "/requestmode off", "/antiflood autoban on", "/searchmode on",
    "/autolink dm on", "/setpostchannel off", "/setpremium 10 30",
]


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    yield


async def _snapshot() -> dict:
    """Every document in every database – any difference means something was written."""
    import filestore.database.mongo as mongo
    from database.db import db as saver_db
    from VideoEncoder.utils.database.access_db import db as enc_db
    out = {}
    for client in {id(c): c for c in (mongo.get_motor_client(), saver_db._client, enc_db._client)}.values():
        for dbn in await client.list_database_names():
            for coll in await client[dbn].list_collection_names():
                docs = await client[dbn][coll].find().to_list(None)
                for d in docs:
                    for k in ("last_active", "updated_at"):      # activity clocks, not settings
                        d.pop(k, None)
                out[f"{dbn}.{coll}"] = sorted(map(repr, docs))
    return out


def _changed(before: dict, after: dict) -> list:
    return sorted(k.split(".")[-1] for k in set(before) | set(after) if before.get(k) != after.get(k))


# ───────────────────────── main bot: 🤖 My Bots panel ─────────────────────────
def _panel_handlers():
    import filestore.main_bot.plugins as pkg
    for info in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module(f"filestore.main_bot.plugins.{info.name}")
        for name, fn in vars(mod).items():
            for handler, _group in getattr(fn, "handlers", None) or []:
                if isinstance(handler, CallbackQueryHandler):
                    yield f"{info.name}.{name}", handler, fn


def _regexes(f):
    if f is None:
        return
    if hasattr(f, "p"):
        yield f.p.pattern
    for attr in ("base", "other"):
        yield from _regexes(getattr(f, attr, None))


def _callback_data(pattern: str) -> str:
    """Fill a handler's own regex: the first bot id is the victim clone, a second id is the attacker's clone."""
    ids = iter(["4242", "5151"])

    def fill(m):
        return {r"(-?\d+)": "-1001", r"(\w+)": "requests", "(do_)?": "do_"}.get(m.group(0)) or next(ids)
    return re.sub(r"\((?:-\?)?\\[dw]\+\)|\(do_\)\?", fill, pattern.strip("^$"))


PANEL = [(name, h, fn) for name, h, fn in _panel_handlers()
         if any(r"\d+" in p for p in _regexes(h.filters))]


def test_panel_scan_found_the_settings_buttons():
    names = {n for n, _, _ in PANEL}
    assert len(PANEL) >= 50, "the handler scan went blind"
    for must in ("bot_settings.auto_delete_callback", "bot_settings.add_admin_callback",
                 "clone_extras_panel.extras_toggle_cb", "my_bots.toggle_maintenance_callback",
                 "my_bots.delete_bot_callback", "clone_extras_panel.transfer_owner_confirm_cb"):
        assert must in names


@pytest.mark.parametrize("name,handler,fn", PANEL, ids=[n for n, _, _ in PANEL])
@pytest.mark.parametrize("uid", [STRANGER, VIDEL_OWNER, CLONE_ADMIN])
def test_panel_button_refuses_everyone_but_the_owner(name, handler, fn, uid):
    from filestore.database.main_db import MainDB
    from filestore.database.worker_db import WorkerDB
    from filestore.main_bot.plugins.bot_settings import _get_state
    run(MainDB().add_bot(4242, OWNER, "enc", "CloneBot", LOG_CH))
    run(MainDB().add_bot(5151, uid, "enc", "TheirBot", LOG_CH))     # the attacker owns a clone of their own
    run(WorkerDB(4242).add_admin(CLONE_ADMIN))
    pattern = next(_regexes(handler.filters))
    data = _callback_data(pattern)
    match = re.match(pattern, data)
    assert match and "4242" in data, (pattern, data)

    q = FakeQuery(data, uid=uid)
    q.matches, q.inline_message_id = [match], None
    _get_state().clear()
    before = run(_snapshot())
    run(fn(FakeClient(), q))
    assert _changed(before, run(_snapshot())) == [], f"{name} let user {uid} change clone 4242"
    assert not _get_state(), f"{name} started an input step for user {uid}"
    assert any(alert and "denied" in (text or "") for text, alert in q.answers)


# ───────────────────────── inside the clone ─────────────────────────
@pytest.fixture
def clone(monkeypatch):
    from filestore.database.main_db import MainDB
    from filestore.database.worker_db import WorkerDB
    app = _start_worker(monkeypatch)
    run(MainDB().update_setting(4242, "antiflood", False))     # rapid-fire test commands must not trip it
    run(WorkerDB(4242).add_admin(CLONE_ADMIN))
    yield app
    _stop(app)


def _send(app, text, uid):
    c = WorkerClient({})
    m = Msg(text, uid=uid, client=c)
    before = run(_snapshot())
    run(asyncio.wait_for(_dispatch(app, c, m), 5))
    return m, _changed(before, run(_snapshot()))


def _tap(app, data, uid):
    async def go():
        c = WorkerClient({})
        q = FakeQuery(data, uid=uid)
        q.inline_message_id = None
        for grp in sorted(app.dispatcher.groups):
            for hd in app.dispatcher.groups[grp]:
                if not isinstance(hd, CallbackQueryHandler) or not hasattr(hd, "filters"):
                    continue
                ok = hd.filters(c, q) if hd.filters else True
                if not (await ok if inspect.isawaitable(ok) else ok):
                    continue
                try:
                    await (getattr(hd, "original_callback", None) or hd.callback)(c, q)
                except ContinuePropagation:
                    pass
                except StopPropagation:
                    return q
                break
        return q
    before = run(_snapshot())
    q = run(asyncio.wait_for(go(), 5))
    return q, _changed(before, run(_snapshot()))


@pytest.mark.parametrize("cmd", SETTINGS_COMMANDS)
def test_only_the_owner_changes_settings_by_command(clone, cmd):
    from filestore.worker_bot.extras import OWNER_ONLY
    for uid in NOT_OWNERS:
        m, changed = _send(clone, cmd, uid)
        assert changed == [], f"user {uid} changed {changed} with {cmd}"
        if uid in (CLONE_ADMIN, VIDEL_OWNER):          # admins learn why; strangers are simply ignored
            assert m.replies == [OWNER_ONLY], (uid, m.replies)
        else:
            assert m.replies == [], (uid, m.replies)
    m, changed = _send(clone, cmd, OWNER)
    assert changed == ["registered_bots"], f"the owner could not use {cmd}: {m.replies}"


def test_only_the_owner_flips_the_settings_switches(clone):
    from filestore.database.main_db import MainDB
    from filestore.worker_bot.extras import OWNER_ONLY_ALERT, TOGGLE_KEYS
    m, _ = _send(clone, "/settings", CLONE_ADMIN)
    assert "Feature switches" not in str(m.replies)                    # the panel isn't even shown
    for key in TOGGLE_KEYS:
        for uid in NOT_OWNERS:
            q, changed = _tap(clone, f"xt:{key}", uid)
            assert changed == [] and q.answers == [(OWNER_ONLY_ALERT, True)], (key, uid)
        was = (run(MainDB().get_bot(4242)).get("settings") or {}).get(key)
        q, changed = _tap(clone, f"xt:{key}", OWNER)
        assert changed == ["registered_bots"], key
        assert (run(MainDB().get_bot(4242))["settings"]).get(key) != was


def test_clone_admins_keep_their_day_to_day_powers(clone):
    """Owner-only is for settings – admins still moderate and hand out premium."""
    m, changed = _send(clone, "/ban 7", CLONE_ADMIN)
    assert changed == ["bot_4242_banned"], m.replies
    m, changed = _send(clone, "/addpremium 7 30", CLONE_ADMIN)
    assert changed == ["bot_4242_premium"], m.replies
    m, _ = _send(clone, "/requests", CLONE_ADMIN)
    assert m.replies, "clone admins lost /requests"


def test_ownership_transfer_applies_without_a_restart(clone):
    from filestore.database.main_db import MainDB
    run(MainDB().transfer_owner(4242, 9))              # the running clone was started with owner 5
    m, changed = _send(clone, "/sethelp from the old owner", OWNER)
    assert changed == [], "the previous owner kept control of the settings"
    m, changed = _send(clone, "/sethelp from the new owner", 9)
    assert changed == ["registered_bots"], m.replies
    assert run(MainDB().get_bot(4242))["settings"]["help_text"] == "from the new owner"


def test_help_shows_settings_commands_to_the_owner_only(clone):
    admin, _ = _send(clone, "/help", CLONE_ADMIN)
    owner, _ = _send(clone, "/help", OWNER)
    stranger, _ = _send(clone, "/help", STRANGER)
    assert "/broadcast" in admin.replies[0] and "/sethelp" not in admin.replies[0]
    assert "/broadcast" in owner.replies[0] and "/sethelp" in owner.replies[0] and "Owner" in owner.replies[0]
    assert "/broadcast" not in stranger.replies[0] and "/sethelp" not in stranger.replies[0]

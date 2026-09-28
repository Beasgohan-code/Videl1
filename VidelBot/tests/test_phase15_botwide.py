"""Phase 15 bot-wide: analytics + charts, /admin web dashboard, daily backups + restore, languages, menus."""
import asyncio
import gzip
import json
import os

import pytest

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run
from tests.test_phase5 import _bot_api_html_problems

MODULES = load_all()

import config  # noqa: E402
from core import analytics, backup, i18n  # noqa: E402
from core.db import vdb  # noqa: E402


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    no_botapi()
    analytics._seen.clear()
    analytics._today = ""
    i18n._cache.clear()
    yield


async def _settle():
    for _ in range(5):
        await asyncio.sleep(0)


# ───────────────────────── analytics ─────────────────────────
def test_counters_active_users_and_series():
    async def go():
        for uid in (1, 2, 2, 3):
            await analytics.touch(uid)
        analytics._seen.clear()                      # a restart the same day must not double count
        await analytics.touch(1)
        await analytics.bump("encode", 2)
        analytics.bump_later("rename")
        await _settle()
        from datetime import datetime
        await vdb.db["payments"].insert_one({"user": 1, "stars": 250, "date": datetime.utcnow(), "refunded": False})
        await vdb.db["payments"].insert_one({"user": 2, "stars": 99, "date": datetime.utcnow(), "refunded": True})
        await vdb.users.insert_one({"id": 9, "joined": datetime.utcnow()})
        return await analytics.series(7)
    s = run(go())
    assert len(s["days"]) == 7 and s["days"][-1] == analytics.day()
    assert (s["active"][-1], s["encode"][-1], s["rename"][-1]) == (3, 2, 1)
    assert s["stars"][-1] == 250 and s["new"][-1] == 1


def test_saves_renames_and_clones_are_counted_by_hooks():
    async def go():
        from database.db import db as saver_db
        await saver_db.add_user(77, "x")
        await saver_db.add_traffic(77)
        from core import botlog
        await botlog.event("CloneCreated", "test")
        await _settle()
        return await analytics.series(1)
    s = run(go())
    assert s["save"][-1] == 1 and s["clone"][-1] == 1


def test_charts_are_real_pngs_and_summary_is_valid_html():
    run(analytics.bump("encode", 5))
    users, work, data = run(analytics.charts(30))
    for png in (users, work):
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 3000
    from PIL import Image
    import io
    assert Image.open(io.BytesIO(work)).size == (1100, 560)
    text = run(analytics.summary_text(7))
    assert "Encodes" in text or "ᴇɴᴄᴏᴅᴇꜱ" in text
    assert not _bot_api_html_problems(text)


def test_analytics_command_sends_two_charts_and_a_summary():
    c = FakeClient()
    m = FakeMsg("/analytics 7", uid=222)
    run(analytics.analytics_cmd(c, m))
    names = [n for n, *_ in c.calls]
    assert "send_media_group" in names and "send_message" in names
    kb = [k for n, a, k in c.calls if n == "send_message"][-1]["reply_markup"]
    assert [b.callback_data for b in kb.inline_keyboard[0]] == ["anl:7", "anl:30", "anl:90"]
    q = FakeQuery("anl:30", uid=5)
    run(analytics.analytics_cb(FakeClient(), q))
    assert q.answers[-1] == ("Admins only.", True)


# ───────────────────────── web dashboard ─────────────────────────
def _web(token, calls):
    import keep_alive
    from aiohttp.test_utils import TestClient, TestServer

    async def go():
        old = config.ADMIN_WEB_TOKEN
        config.ADMIN_WEB_TOKEN = token
        keep_alive._fails.clear()
        client = TestClient(TestServer(keep_alive.make_app()))
        await client.start_server()
        try:
            out = []
            for path, headers in calls:
                r = await client.get(path, headers=headers, allow_redirects=False)
                out.append((r.status, await r.read(), r.headers.get("Content-Type", "")))
            return out
        finally:
            await client.close()
            config.ADMIN_WEB_TOKEN = old
    return run(go())


def test_dashboard_is_off_without_a_token():
    res = _web("", [("/admin", {}), ("/admin/api/stats", {"X-Admin-Token": ""})])
    assert [r[0] for r in res] == [404, 404]


def test_dashboard_auth_data_chart_and_rate_limit():
    good = {"X-Admin-Token": "s3cret-token"}
    res = _web("s3cret-token", [("/admin", {}), ("/admin/", {}), ("/admin/api/stats?days=7", good),
                                ("/admin/chart/users.png?days=7", good), ("/admin/api/stats", {"X-Admin-Token": "nope"}),
                                ("/admin/api/stats?token=s3cret-token", {})])
    page, slash, stats, chart, bad, query_token = res
    assert page[0] == 200 and b"X-Admin-Token" in page[1] and b"s3cret" not in page[1]
    assert slash[0] == 302
    data = json.loads(stats[1])
    assert stats[0] == 200 and data["days"] == 7 and len(data["series"]["days"]) == 7 and "users" in data["totals"]
    assert chart[0] == 200 and chart[1][:4] == b"\x89PNG"
    assert bad[0] == 401 and query_token[0] == 401            # tokens in URLs are refused (they leak into logs)
    res = _web("s3cret-token", [("/admin/api/stats", {"X-Admin-Token": f"bad{i}"}) for i in range(11)] + [
        ("/admin/api/stats", good)])
    assert [r[0] for r in res[:10]] == [401] * 10 and res[10][0] == 429 and res[11][0] == 429


# ───────────────────────── backups ─────────────────────────
def test_backup_roundtrip_strips_secrets_and_restore_upserts(tmp_path):
    async def go():
        from database.db import db as saver_db
        await saver_db.add_user(501, "Ann")
        await saver_db.set_session(501, "1BQANOTREAL-session-string")
        await vdb.users.insert_one({"id": 501, "name": "Ann", "lang": "ml"})
        from VideoEncoder.utils.database.access_db import db as enc_db
        await enc_db.update_settings(501, crf=24)
        path = str(tmp_path / "b.json.gz")
        counts = await backup.dump(path)
        raw = gzip.open(path, "rt").read()
        assert "NOTREAL" not in raw and '"session": null' in raw
        data = backup.load(path)
        assert data["meta"]["format"] == backup.FORMAT and data["meta"]["secrets"] is False
        # damage the live data, then restore
        await vdb.users.update_one({"id": 501}, {"$set": {"name": "CHANGED"}})
        await vdb.users.insert_one({"id": 502, "name": "new since backup"})
        restored = await backup.restore(data)
        ann = await vdb.users.find_one({"id": 501})
        kept = await vdb.users.find_one({"id": 502})
        session = (await saver_db.col.find_one({"id": 501})).get("session")
        return counts, restored, ann, kept, session
    counts, restored, ann, kept, session = run(go())
    assert counts["videl.videl_users"] == 1 and counts["encoder.users"] >= 1
    assert ann["name"] == "Ann" and kept is not None                 # upsert: fixes data, deletes nothing
    assert session == "1BQANOTREAL-session-string"                    # stripped secret doesn't wipe the live one
    assert restored["videl.videl_users"] == 1


def test_backup_command_and_restore_confirmation(monkeypatch, tmp_path):
    sent = []

    class C(FakeClient):
        async def send_document(self, chat_id, path, caption=None, file_name=None, **k):
            sent.append((chat_id, os.path.getsize(path), caption))
    m = FakeMsg("/backup", uid=111)
    run(backup.backup_cmd(C(), m))
    assert sent and sent[0][0] == m.chat.id and "stripped" in sent[0][2] and not _bot_api_html_problems(sent[0][2])
    bad = FakeMsg("/backup", uid=111)
    bad.reply_to_message = FakeMsg("x", uid=111)
    bad.reply_to_message.document = type("D", (), {"file_name": "notes.txt", "file_size": 5})()
    run(backup.backup_cmd(C(), bad))
    assert "json.gz" in bad.replies[-1]
    q = FakeQuery("bkr:yes", uid=5)
    run(backup.backup_restore_cb(FakeClient(), q))
    assert q.answers[-1] == ("Owners only.", True)
    q = FakeQuery("bkr:yes", uid=111)
    run(backup.backup_restore_cb(FakeClient(), q))
    assert "Expired" in q.answers[-1][0]


def test_backup_schedule_math():
    s = backup._seconds_until(4)
    assert 0 < s <= 24 * 3600


# ───────────────────────── languages ─────────────────────────
def test_english_is_unchanged_and_every_language_renders():
    from core import texts
    assert i18n.start_template("en") == texts.START_TXT
    kw = dict(mention="<a href='tg://user?id=5'>Ann</a>", username="VidelBot", first_name="Videl", uptime="1h", plan="Free")
    for code in i18n.LANGS:
        text = i18n.start_template(code).format(**kw)
        assert "VidelBot" in text and "1h" in text and not _bot_api_html_problems(text), code
        for key, entry in i18n.S.items():
            assert code in entry, (key, code)                     # no missing translations
    assert i18n.start_template("ar").startswith("\u200f")


def test_language_detection_choice_and_home_screen():
    from pyrogram.types import User
    u = User(id=42, first_name="Ravi", language_code="ml")
    assert run(i18n.get_lang(u)) == "ml"
    assert run(i18n.get_lang(User(id=43, first_name="X", language_code="pt-BR"))) == "en"
    run(i18n.set_lang(42, "hi"))
    i18n._cache.clear()
    assert run(i18n.get_lang(u)) == "hi"                         # stored choice beats Telegram's language
    from core.menus import home_kb
    en = [b.text for row in home_kb().inline_keyboard for b in row]
    hi = [b.text for row in home_kb("hi").inline_keyboard for b in row]
    assert "⚡ Clone Bot" in en and "⚡ क्लोन बॉट" in hi
    assert "lang_menu" in [b.callback_data for row in home_kb().inline_keyboard for b in row]
    m = FakeMsg("/lang", uid=42)
    run(i18n.lang_cmd(FakeClient(), m))
    assert "setlang" in str(m.replies) or m.replies


def test_setlang_callback_rerenders_home(monkeypatch):
    seen = {}

    async def fake_render(client, query):
        seen["lang"] = await i18n.get_lang(query.from_user)
    import core.menus
    monkeypatch.setattr(core.menus, "render_home", fake_render)
    q = FakeQuery("setlang:es", uid=44)
    run(i18n.setlang_cb(FakeClient(), q))
    assert seen["lang"] == "es" and "Español" in q.answers[-1][0]
    q = FakeQuery("setlang:zz", uid=44)
    run(i18n.setlang_cb(FakeClient(), q))
    assert seen["lang"] == "en"                                  # unknown code → English


# ───────────────────────── menus ─────────────────────────
def test_commands_fit_and_new_ones_are_listed():
    from core import commands as cm
    user = {n for n, _ in cm.USER_COMMANDS}
    assert {"mux", "merge", "convert", "watermark", "leech", "lang", "plans"} <= user
    assert "analytics" in {n for n, _ in cm.ADMIN_COMMANDS} and "backup" in {n for n, _ in cm.OWNER_COMMANDS}
    total = len({n for g in (cm.USER_COMMANDS, cm.ADMIN_COMMANDS, cm.OWNER_COMMANDS) for n, _ in g})
    assert total <= 100
    from pyrogram.filters import AndFilter, OrFilter
    import run as runner

    def cmds(f):
        if isinstance(f, (AndFilter, OrFilter)):
            return cmds(f.base) | cmds(f.other)
        return set(getattr(f, "commands", None) or ())
    names = set()
    for mod in MODULES:
        for obj in vars(mod).values():
            hs = getattr(obj, "handlers", None) if callable(obj) else None
            for h, _g in hs if isinstance(hs, list) else []:
                names |= cmds(getattr(h, "filters", None))
    missing = (user | {"analytics", "backup"}) - names
    assert not missing, missing
    assert runner.PLUGIN_ROOTS[0] == "core"

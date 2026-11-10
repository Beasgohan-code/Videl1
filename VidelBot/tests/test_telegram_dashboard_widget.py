"""Offline tests for the private admin statistics widget."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import zlib

import pytest
from tests.harness import FakeClient, FakeMsg, reset_db, run
from pyrogram import enums

import config
from core import analytics
from plugins.widget import dashboard


def sample():
    days = [(datetime(2026, 1, 1) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(90)]
    return {"days": days, **{key: [value] * 90 for key, value in {
        "active": 4, "new": 2, "encode": 1, "rename": 2, "save": 3,
        "leech": 4, "tool": 5, "stars": 7}.items()}}


def test_snapshot_contains_only_aggregates_and_is_bounded():
    data = dashboard.snapshot_data(sample(), datetime(2026, 4, 1, tzinfo=timezone.utc))
    assert set(data) == {"days", "active", "new", "jobs", "stars", "generated", "default_range"}
    assert data["jobs"] == [15] * 90 and data["generated"] == "2026-04-01 00:00"
    html = dashboard.dashboard_html(data)
    assert len(html.encode()) <= 32768 and len(zlib.compress(html.encode(), 9)) <= 4096
    assert '"jobs":{"r":"Dw8P' in html and "allow_queries" not in html
    assert "TelegramWidget.request" not in html and "eval(" not in html
    malformed = sample(); malformed["active"][0] = -1
    with pytest.raises(ValueError): dashboard.snapshot_data(malformed, datetime.now(timezone.utc))
    malformed = sample(); malformed["days"][0] = '</script><script>alert(1)</script>'
    with pytest.raises(ValueError): dashboard.snapshot_data(malformed, datetime.now(timezone.utc))
    # Script-context escaping is also enforced independently at serialization.
    assert "</script><script>alert" not in dashboard.dashboard_html({"x": "</script><script>alert"})


def test_dashboard_filters_and_handler_are_admin_only(monkeypatch):
    monkeypatch.setattr(config, "ADMINS", [222])
    client = FakeClient()
    filt = analytics.dashboard_cmd.handlers[0][0].filters
    users = filt.other; original = set(users); users.clear(); users.add(222)
    try:
        assert not run(filt(client, FakeMsg("/dashboard", uid=5)))
        assert run(filt(client, FakeMsg("/dashboard", uid=222)))
        assert not run(filt(client, FakeMsg("/analytics", uid=222)))
    finally:
        users.clear(); users.update(original)
    calls = []
    async def widget(*args): calls.append("widget")
    async def charts(*args): calls.append("analytics")
    monkeypatch.setattr(dashboard, "send_dashboard", widget)
    monkeypatch.setattr(analytics, "analytics_cmd", charts)
    for uid in (5, None): run(analytics.dashboard_cmd(client, FakeMsg("/dashboard", uid=uid)))
    assert not calls
    run(analytics.dashboard_cmd(client, FakeMsg("/dashboard 7", uid=222)))
    assert calls == ["widget", "analytics"]


@pytest.mark.parametrize("chat_type", [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL, None])
def test_stats_widget_never_sent_outside_private_chat(monkeypatch, chat_type):
    monkeypatch.setattr(config, "ADMINS", [222])
    def fail(*args, **kwargs): pytest.fail("must not touch plugin or outgoing transport")
    monkeypatch.setattr(dashboard, "plugin_enabled", fail)
    msg = FakeMsg("/dashboard", uid=222, chat_type=chat_type, chat_id=-100)
    assert not run(dashboard.send_dashboard(FakeClient(), msg))
    assert len(msg.replies) == 1 and "private chat" in msg.replies[0]
    # Non-admins don't get a widget or privileged details, even through a direct call.
    other = FakeMsg("/dashboard", uid=5)
    assert not run(dashboard.send_dashboard(FakeClient(), other)) and not other.replies


def test_widget_sends_real_series_and_no_queries(monkeypatch):
    from plugins.widget import dashboard_sender
    monkeypatch.setattr(config, "ADMINS", [222])
    monkeypatch.setattr(dashboard, "plugin_enabled", lambda name: True)
    monkeypatch.setattr(dashboard.botapi, "enabled", lambda: True)
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: SimpleNamespace(id=123456, token="secret"))
    monkeypatch.setenv("WIDGET_API_BETA", "true"); monkeypatch.setenv("WIDGET_API_MODE", "beta")
    calls = []
    async def series(days, *, strict=False):
        assert (days, strict) == (90, True)
        return sample()
    async def send(bot, chat_id, html, mode): calls.append((chat_id, html, mode))
    monkeypatch.setattr(analytics, "series", series); monkeypatch.setattr(dashboard_sender, "send", send)
    msg = FakeMsg("/dashboard 7", uid=222)
    assert run(dashboard.send_dashboard(FakeClient(), msg))
    assert calls[0][0] == 222 and calls[0][2] == "beta" and '"jobs":{"r":"Dw8P' in calls[0][1]
    assert '"default_range":7' in calls[0][1]
    method = dashboard_sender.SendDashboardWidget(chat_id=222, html=calls[0][1]).model_dump(exclude_none=True)
    assert method["is_shared"] is False and method["allow_queries"] is False
    assert "public_data" not in method and "chat_data" not in method
    assert "snapshot" in method["caption"] and "refresh" in method["caption"]


def test_database_failure_never_fabricates_zero_or_sends(monkeypatch):
    from plugins.widget import dashboard_sender
    monkeypatch.setattr(config, "ADMINS", [222]); monkeypatch.setattr(dashboard, "plugin_enabled", lambda _: True)
    monkeypatch.setattr(dashboard.botapi, "enabled", lambda: True)
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: SimpleNamespace(id=123456, token="secret"))
    monkeypatch.setenv("WIDGET_API_BETA", "true"); monkeypatch.setenv("WIDGET_API_MODE", "beta")
    class BrokenDB:
        def __getitem__(self, name): raise RuntimeError("database unavailable")
    monkeypatch.setattr(analytics, "_db", lambda: BrokenDB())
    monkeypatch.setattr(dashboard_sender, "send", lambda *a: pytest.fail("must not send bogus zeros"))
    with pytest.raises(RuntimeError): run(analytics.series(90, strict=True))
    msg = FakeMsg("/dashboard", uid=222)
    assert not run(dashboard.send_dashboard(FakeClient(), msg))
    assert "unavailable" in msg.replies[-1]


def test_series_reads_real_aggregate_sources():
    run(reset_db())
    db = analytics._db(); now = datetime.now(timezone.utc); today = analytics.day(now)
    async def seed():
        await db["stats_daily"].insert_one({"_id": today, "active": 6, "encode": 2, "rename": 3})
        await db["videl_users"].insert_one({"_id": 123, "joined": now.replace(tzinfo=None)})
        await db["payments"].insert_one({"date": now.replace(tzinfo=None), "stars": 15, "refunded": False})
        await db["payments"].insert_one({"date": now.replace(tzinfo=None), "stars": 99, "refunded": True})
    run(seed())
    data = dashboard.snapshot_data(run(analytics.series(90, strict=True)), now)
    assert data["active"][-1] == 6 and data["new"][-1] == 1
    assert data["jobs"][-1] == 5 and data["stars"][-1] == 15


def test_admin_dashboard_group_keeps_existing_charts(monkeypatch):
    calls = []
    async def send_charts(client, chat_id, days, private=None): calls.append((chat_id, days))
    monkeypatch.setattr(analytics, "send_analytics", send_charts); monkeypatch.setattr(config, "ADMINS", [222])
    msg = FakeMsg("/dashboard 7", uid=222, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
    run(analytics.dashboard_cmd(FakeClient(), msg))
    assert calls == [(-100, 7)] and "private chat" in msg.replies[0] and msg.sent[-1].deleted


def test_menu_keeps_dashboard_admin_only_and_under_limit():
    from core.commands import USER_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS
    assert "dashboard" not in {name for name, _ in USER_COMMANDS}
    assert "dashboard" in {name for name, _ in ADMIN_COMMANDS}
    assert len({name for group in (USER_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS) for name, _ in group}) <= 100


def test_aiogram_sender_routes_modes_and_closes_beta_session(monkeypatch):
    from plugins.widget import dashboard_sender as sender
    sent = []
    class Outgoing:
        token = "123:token"
        async def __call__(self, method): sent.append(("configured", method)); return "accepted"
    class Session:
        def __init__(self, **kwargs): self.kwargs, self.closed = kwargs, False
        async def close(self): self.closed = True
    sessions = []
    def new_session(**kwargs):
        session = Session(**kwargs); sessions.append(session); return session
    class BetaBot:
        def __init__(self, token, session): assert token == "123:token"; self.session = session
        async def __call__(self, method): sent.append(("beta", method)); return "accepted"
    monkeypatch.setattr(sender, "AiohttpSession", new_session); monkeypatch.setattr(sender, "Bot", BetaBot)
    assert run(sender.send(Outgoing(), 222, "html", "configured")) == "accepted"
    assert run(sender.send(Outgoing(), 222, "html", "beta")) == "accepted"
    assert [mode for mode, _ in sent] == ["configured", "beta"]
    assert sent[0][1].allow_queries is False and sent[1][1].is_shared is False
    assert sessions[0].kwargs["api"].base.startswith(sender.BETA_BASE) and sessions[0].closed
    with pytest.raises(ValueError): run(sender.send(Outgoing(), 222, "html", "unknown"))


def test_configuration_identity_and_timeout_diagnostics(monkeypatch, caplog):
    from plugins.widget import dashboard_sender
    monkeypatch.setattr(config, "ADMINS", [222])
    msg = FakeMsg("/dashboard", uid=222); client = FakeClient()
    monkeypatch.setattr(dashboard, "plugin_enabled", lambda _: False)
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: pytest.fail("disabled plugin must not send"))
    assert not run(dashboard.send_dashboard(client, msg)) and "disabled" in msg.replies[-1]
    monkeypatch.setattr(dashboard, "plugin_enabled", lambda _: True)
    monkeypatch.setattr(dashboard.botapi, "enabled", lambda: True)
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: SimpleNamespace(id=999, token="secret-token"))
    monkeypatch.setenv("WIDGET_API_BETA", "true")
    assert not run(dashboard.send_dashboard(client, msg)) and "primary bot" in msg.replies[-1]
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: SimpleNamespace(id=123456, token="secret-token"))
    monkeypatch.setenv("WIDGET_API_MODE", "invalid")
    assert not run(dashboard.send_dashboard(client, msg)) and "WIDGET_API_MODE" in msg.replies[-1]
    monkeypatch.setenv("WIDGET_API_MODE", "beta")
    async def series(*args, **kwargs): return sample()
    calls = []
    async def fail(*args): calls.append(1); raise TimeoutError("secret-token")
    monkeypatch.setattr(analytics, "series", series); monkeypatch.setattr(dashboard_sender, "send", fail)
    assert not run(dashboard.send_dashboard(client, msg))
    assert len(calls) == 1 and "check this chat before retrying" in msg.replies[-1].lower()
    assert "secret-token" not in caplog.text and "secret-token" not in msg.replies[-1]


def test_analytics_still_sent_if_stats_widget_disabled(monkeypatch):
    monkeypatch.setattr(config, "ADMINS", [222]); monkeypatch.setattr(dashboard, "plugin_enabled", lambda _: False)
    calls = []
    async def charts(client, chat_id, days, private=None): calls.append((chat_id, days))
    monkeypatch.setattr(analytics, "send_analytics", charts)
    msg = FakeMsg("/dashboard 12", uid=222)
    run(analytics.dashboard_cmd(FakeClient(), msg))
    assert "disabled" in msg.replies[0] and calls == [(222, 12)] and msg.sent[-1].deleted


def test_dashboard_default_range_is_bounded():
    now = datetime.now(timezone.utc)
    assert dashboard.snapshot_data(sample(), now, default_range=7)['default_range'] == 7
    assert dashboard.snapshot_data(sample(), now, default_range=90)['default_range'] == 90
    assert dashboard.snapshot_data(sample(), now, default_range=180)['default_range'] == 30


def test_compact_payload_round_trips_exact_buckets_peaks_and_dates():
    import base64
    import json
    now = datetime.now(timezone.utc)
    original = sample()
    original['active'] = [i * 9876543 for i in range(90)]
    data = dashboard.snapshot_data(original, now)
    html = dashboard.dashboard_html(data)
    payload = json.loads(html.split('const DATA=', 1)[1].split(';', 1)[0])
    def unpack(text):
        result, value, mul = [], 0, 1
        for byte in base64.b64decode(text):
            value += (byte & 127) * mul
            if byte < 128:
                result.append(value); value, mul = 0, 1
            else:
                mul *= 128
        assert mul == 1
        return result
    encoded = payload['active']
    assert payload['days'] == original['days'][0]
    assert unpack(encoded['r']) == original['active'][-30:]
    assert unpack(encoded['g']) == [sum(original['active'][i:i+3]) for i in range(0, 90, 3)]
    assert encoded['p'] == sum(original['active'][-60:-30]) and encoded['x'] == max(original['active'])
    original['days'][1] = original['days'][0]
    with pytest.raises(ValueError): dashboard.snapshot_data(original, now)


def test_snapshot_template_markers_remain_data():
    html = dashboard.dashboard_html({'x': '/*JS*/'})
    assert '/*JS*/' in html and html.count("(()=>{'use strict'") == 1
    assert html.index('/*JS*/') < html.index("(()=>{'use strict'")


def test_oversized_snapshot_reports_limit_without_retry(monkeypatch):
    from plugins.widget import dashboard_sender
    monkeypatch.setattr(config, 'ADMINS', [222]); monkeypatch.setattr(dashboard, 'plugin_enabled', lambda _: True)
    monkeypatch.setattr(dashboard.botapi, 'enabled', lambda: True)
    monkeypatch.setattr(dashboard.botapi, 'bot', lambda: SimpleNamespace(id=123456, token='secret'))
    monkeypatch.setenv('WIDGET_API_BETA', 'true'); monkeypatch.setenv('WIDGET_API_MODE', 'beta')
    async def series(*args, **kwargs): return sample()
    monkeypatch.setattr(analytics, 'series', series)
    monkeypatch.setattr(dashboard, 'dashboard_html', lambda _: (_ for _ in ()).throw(dashboard.WidgetPayloadTooLarge()))
    monkeypatch.setattr(dashboard_sender, 'send', lambda *args: pytest.fail('must not send'))
    msg = FakeMsg('/dashboard', uid=222)
    assert not run(dashboard.send_dashboard(FakeClient(), msg))
    assert 'too large' in msg.replies[-1] and 'charts are still available' in msg.replies[-1]

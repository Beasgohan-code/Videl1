"""Offline routing, authorization and sender contracts for /dashboard."""
from types import SimpleNamespace
import zlib

import pytest
from tests.harness import FakeClient, FakeMsg, run
from pyrogram import enums

import config
from core import analytics
from plugins.widget import dashboard


def test_html_is_local_and_within_limits():
    html = dashboard.dashboard_html()
    assert len(html.encode()) <= 32768
    assert len(zlib.compress(html.encode(), 9)) <= 4096
    assert all(f'data-tab="{tab}"' in html for tab in ("calc", "bill", "unit", "count"))
    assert "TelegramWidget.request" not in html and "eval(" not in html
    assert "<script src=" not in html and "<input" not in html


def test_dashboard_routing_never_gives_analytics_to_non_admin(monkeypatch):
    calls = []
    async def widget(client, message): calls.append(("widget", message.from_user))
    async def charts(client, message): calls.append(("analytics", message.from_user))
    monkeypatch.setattr(dashboard, "send_dashboard", widget)
    monkeypatch.setattr(analytics, "analytics_cmd", charts)
    monkeypatch.setattr(config, "ADMINS", [222])
    for uid in (5, None):
        run(analytics.dashboard_cmd(FakeClient(), FakeMsg("/dashboard", uid=uid)))
    assert [name for name, _ in calls] == ["widget", "widget"]
    run(analytics.dashboard_cmd(FakeClient(), FakeMsg("/dashboard 7", uid=222)))
    assert [name for name, _ in calls] == ["widget", "widget", "widget", "analytics"]


def test_command_filters_separate_admin_analytics_from_public_dashboard(monkeypatch):
    monkeypatch.setattr(config, "ADMINS", [222])
    # The WzGram user filter captures config when imported. Isolate it from
    # collection/import order, then restore it for the rest of the suite.
    client = FakeClient()
    analytics_filter = analytics.analytics_cmd.handlers[0][0].filters
    dashboard_filter = analytics.dashboard_cmd.handlers[0][0].filters
    users = analytics_filter.other
    original = set(users)
    users.clear()
    users.add(222)
    try:
        assert not run(analytics_filter(client, FakeMsg("/analytics", uid=5)))
        assert run(analytics_filter(client, FakeMsg("/analytics", uid=222)))
        assert not run(analytics_filter(client, FakeMsg("/dashboard", uid=222)))
        assert run(dashboard_filter(client, FakeMsg("/dashboard", uid=5)))
        assert run(dashboard_filter(client, FakeMsg("/dashboard", uid=222)))
        assert not run(dashboard_filter(client, FakeMsg("/analytics", uid=222)))
    finally:
        users.clear()
        users.update(original)


@pytest.mark.parametrize("chat_type", [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP, enums.ChatType.CHANNEL, None])
def test_no_group_send_or_proactive_dm(monkeypatch, chat_type):
    def fail(*args, **kwargs): pytest.fail("must not access plugin or outgoing transport")
    monkeypatch.setattr(dashboard, "plugin_enabled", fail)
    monkeypatch.setattr(dashboard.botapi, "bot", fail)
    msg = FakeMsg("/dashboard", uid=5, chat_type=chat_type, chat_id=-100)
    assert not run(dashboard.send_dashboard(FakeClient(), msg))
    assert len(msg.replies) == 1 and "private chat" in msg.replies[0]


def test_local_sender_disabled_identity_and_configuration(monkeypatch):
    msg = FakeMsg("/dashboard", uid=5)
    client = FakeClient()
    monkeypatch.setattr(dashboard, "plugin_enabled", lambda name: False)
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: pytest.fail("disabled plugin must not send"))
    assert not run(dashboard.send_dashboard(client, msg))
    assert "disabled" in msg.replies[-1]

    monkeypatch.setattr(dashboard, "plugin_enabled", lambda name: True)
    monkeypatch.setattr(dashboard.botapi, "enabled", lambda: True)
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: SimpleNamespace(id=999, token="secret"))
    monkeypatch.setenv("WIDGET_API_BETA", "true")
    assert not run(dashboard.send_dashboard(client, msg))
    assert "primary bot" in msg.replies[-1]
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: SimpleNamespace(id=123456, token="secret"))
    monkeypatch.setenv("WIDGET_API_MODE", "typo")
    assert not run(dashboard.send_dashboard(client, msg))
    assert "WIDGET_API_MODE" in msg.replies[-1]


def test_local_sender_is_outgoing_only_and_never_retries(monkeypatch, caplog):
    from plugins.widget import dashboard_sender
    outgoing = SimpleNamespace(id=123456, token="secret-token")
    monkeypatch.setattr(dashboard, "plugin_enabled", lambda name: True)
    monkeypatch.setattr(dashboard.botapi, "enabled", lambda: True)
    monkeypatch.setattr(dashboard.botapi, "bot", lambda: outgoing)
    monkeypatch.setenv("WIDGET_API_BETA", "true")
    monkeypatch.setenv("WIDGET_API_MODE", "beta")
    calls = []
    async def send(bot, chat_id, html, mode): calls.append((bot, chat_id, html, mode))
    monkeypatch.setattr(dashboard_sender, "send", send)
    msg = FakeMsg("/dashboard", uid=5)
    assert run(dashboard.send_dashboard(FakeClient(), msg))
    assert len(calls) == 1 and calls[0][1] == 5 and calls[0][3] == "beta"
    assert calls[0][2] == dashboard.dashboard_html() and not msg.replies
    async def fail(*args):
        calls.append("failed")
        raise TimeoutError("secret-token")
    monkeypatch.setattr(dashboard_sender, "send", fail)
    assert not run(dashboard.send_dashboard(FakeClient(), msg))
    assert calls[-1] == "failed" and len(calls) == 2
    assert "check this chat before retrying" in msg.replies[-1].lower()
    assert "secret-token" not in caplog.text and "secret-token" not in msg.replies[-1]


def test_aiogram_method_serializes_without_queries_or_shared_state():
    from plugins.widget.dashboard_sender import SendDashboardWidget
    method = SendDashboardWidget(chat_id=5, html=dashboard.dashboard_html())
    payload = method.model_dump(exclude_none=True)
    assert method.__api_method__ == "sendWidget"
    assert payload["is_shared"] is False and payload["allow_queries"] is False
    assert "public_data" not in payload and "chat_data" not in payload
    assert "requires a compatible" in payload["caption"]


def test_private_menu_includes_dashboard_under_telegram_limit():
    from core.commands import USER_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS
    assert "dashboard" in {name for name, _ in USER_COMMANDS}
    assert len({name for group in (USER_COMMANDS, ADMIN_COMMANDS, OWNER_COMMANDS) for name, _ in group}) <= 100


def test_dashboard_admin_keeps_real_analytics_flow_with_days_and_group_guard(monkeypatch):
    calls = []
    async def send_charts(client, chat_id, days, private=None): calls.append((chat_id, days))
    monkeypatch.setattr(analytics, "send_analytics", send_charts)
    monkeypatch.setattr(config, "ADMINS", [222])
    # In group chats the toolkit never sends, but the old admin charts still do.
    msg = FakeMsg("/dashboard 7", uid=222, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
    run(analytics.dashboard_cmd(FakeClient(), msg))
    assert calls == [(-100, 7)]
    assert "private chat" in msg.replies[0]
    assert msg.sent[-1].deleted
    msg = FakeMsg("/dashboard 9999", uid=222)
    monkeypatch.setattr(dashboard, "plugin_enabled", lambda _: False)
    run(analytics.dashboard_cmd(FakeClient(), msg))
    assert calls[-1] == (222, 180)
    assert "disabled" in msg.replies[0]


def test_aiogram_sender_routes_modes_and_closes_beta_session(monkeypatch):
    from plugins.widget import dashboard_sender as sender
    sent = []
    class Outgoing:
        token = "123:token"
        async def __call__(self, method):
            sent.append(("configured", method))
            return "accepted"
    class Session:
        def __init__(self, **kwargs): self.kwargs, self.closed = kwargs, False
        async def close(self): self.closed = True
    sessions = []
    def new_session(**kwargs):
        s = Session(**kwargs)
        sessions.append(s)
        return s
    class BetaBot:
        def __init__(self, token, session):
            assert token == "123:token"
            self.session = session
        async def __call__(self, method):
            sent.append(("beta", method))
            return "accepted"
    monkeypatch.setattr(sender, "AiohttpSession", new_session)
    monkeypatch.setattr(sender, "Bot", BetaBot)
    assert run(sender.send(Outgoing(), 5, "html", "configured")) == "accepted"
    assert run(sender.send(Outgoing(), 5, "html", "beta")) == "accepted"
    assert [mode for mode, _ in sent] == ["configured", "beta"]
    assert sent[0][1].allow_queries is False and sent[1][1].is_shared is False
    assert sessions[0].kwargs["api"].base.startswith(sender.BETA_BASE)
    assert sessions[0].closed
    with pytest.raises(ValueError):
        run(sender.send(Outgoing(), 5, "html", "unknown"))

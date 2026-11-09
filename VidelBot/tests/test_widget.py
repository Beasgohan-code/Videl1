from types import SimpleNamespace
import asyncio
import json
import zlib

import pytest

from plugins.widget import plugin as widget


def run(coro):
    return asyncio.run(coro)


def test_beta_flag_defaults_on_and_allows_explicit_opt_out(monkeypatch):
    monkeypatch.delenv("WIDGET_API_BETA", raising=False)
    assert widget._beta_enabled()
    monkeypatch.setenv("WIDGET_API_BETA", "false")
    assert not widget._beta_enabled()


def test_safe_calculator_grammar_and_bounds():
    assert widget.evaluate_expression("2 + 3 * (4 - 1)") == "11"
    assert widget.evaluate_expression("-3 / 2") == "-1.5"
    assert widget.evaluate_expression(".5 + 1.") == "1.5"
    assert widget.evaluate_expression("0") == "0"
    for bad in ("1/0", "__import__('os')", "2**10", "".join(["9"] * 100), "1+", "[1]"):
        with pytest.raises(ValueError):
            widget.evaluate_expression(bad)


def test_widget_is_self_contained_and_within_telegram_limits():
    html = widget.widget_html()
    raw = html.encode("utf-8")
    assert len(raw) <= 32768
    assert len(zlib.compress(raw, 9)) <= 4096
    assert "tw.request(" in html
    assert "watchPublicData" in html
    assert "eval(" not in html and "<script src=" not in html


def test_widget_id_extractor_handles_response_envelopes():
    assert widget._widget_id_from_result({"widget_id": 17}) == 17
    assert widget._widget_id_from_result({"widget": {"id": 18}}) == 18
    assert widget._widget_id_from_result({"message": {"media": {"widget_id": "19"}}}) == 19
    assert widget._widget_id_from_result({"id": 123}) is None
    assert widget._widget_id_from_result({"widget_id": "0"}) is None


def test_plugin_discovery_replaces_game_with_widget():
    from core.plugin_manager import plugin_manager
    manifests = plugin_manager.discover()
    assert "widget" in manifests
    assert "game" not in manifests
    assert not plugin_manager.command_conflicts(manifests["widget"])


def test_send_widget_is_opt_in_identity_checked_and_reuses_shared_object(monkeypatch):
    class Outgoing:
        id = 123
        token = "test-token"

    class Message:
        chat = SimpleNamespace(id=-100123)
        message_thread_id = 44
        replies = []

        async def reply_text(self, text):
            self.replies.append(text)

    outgoing = Outgoing()
    calls = []
    async def fake_request(token, method, payload, **kwargs):
        calls.append((token, method, payload))
        return {"widget_id": 987} if method == "sendWidget" else True

    monkeypatch.setattr(widget.botapi, "enabled", lambda: True)
    monkeypatch.setattr(widget.botapi, "bot", lambda: outgoing)
    monkeypatch.setattr(widget.transport, "request", fake_request)
    monkeypatch.setattr(widget.vdb, "get_setting", lambda key, default=None: asyncio.sleep(0, result=None))
    stored = []
    async def set_setting(key, value):
        stored.append((key, value))
    monkeypatch.setattr(widget.vdb, "set_setting", set_setting)
    monkeypatch.setenv("WIDGET_API_BETA", "false")
    message = Message()
    client = SimpleNamespace(me=SimpleNamespace(id=123))
    assert not run(widget._send_widget(client, message))
    assert not calls

    monkeypatch.setenv("WIDGET_API_BETA", "true")
    client.me.id = 999
    assert not run(widget._send_widget(client, message))
    assert not calls

    client.me.id = 123
    assert run(widget._send_widget(client, message))
    method, payload = calls[-1][1:]
    assert method == "sendWidget"
    assert payload["chat_id"] == -100123 and payload["message_thread_id"] == 44
    assert payload["is_shared"] is True and payload["allow_queries"] is True
    assert stored == [(widget.SETTING_KEY, 987)]

    async def saved(key, default=None):
        return 987
    monkeypatch.setattr(widget.vdb, "get_setting", saved)
    assert run(widget._send_widget(client, message))
    assert calls[-1][1] == "sendSharedWidget"
    assert calls[-1][2]["widget_id"] == 987


def test_query_updates_shared_state_then_answers(monkeypatch):
    calls = []
    async def setting(key, default=None):
        return 246
    async def fake_request(token, method, payload, **kwargs):
        calls.append((method, payload))
        return True
    monkeypatch.setattr(widget.vdb, "get_setting", setting)
    monkeypatch.setattr(widget.transport, "request", fake_request)
    monkeypatch.setattr(widget.config, "BOT_TOKEN", "test-token")
    monkeypatch.setenv("WIDGET_API_MODE", "beta")

    run(widget.handle_widget_query({"update_id": 1, "widget_query": {
        "id": 42, "widget_id": 246, "data": "c:2*(3+4)"}}))
    assert [m for m, _ in calls] == ["editSharedWidgetData", "answerWidgetQuery"]
    state = json.loads(calls[0][1]["public_data"])
    assert state["expression"] == "2*(3+4)" and state["result"] == "14"
    answer = json.loads(calls[1][1]["data"])
    assert answer == {"ok": True, "expression": "2*(3+4)", "result": "14"}
    assert calls[1][1]["widget_query_id"] == 42


def test_query_rejects_code_and_still_answers_without_edit(monkeypatch):
    calls = []
    async def setting(key, default=None):
        return 246
    async def fake_request(token, method, payload, **kwargs):
        calls.append((method, payload))
        return True
    monkeypatch.setattr(widget.vdb, "get_setting", setting)
    monkeypatch.setattr(widget.transport, "request", fake_request)
    monkeypatch.setattr(widget.config, "BOT_TOKEN", "test-token")
    run(widget.handle_widget_query({"shared_widget_query": {
        "id": 43, "widget_id": 246, "data": "c:__import__('os')"}}))
    assert [m for m, _ in calls] == ["answerWidgetQuery"]
    answer = json.loads(calls[0][1]["data"])
    assert answer["ok"] is False


def test_polling_refuses_existing_webhook(monkeypatch):
    calls = []
    async def fake_request(token, method, payload=None, **kwargs):
        calls.append(method)
        return {"url": "https://example.invalid/webhook"}
    monkeypatch.setattr(widget.transport, "request", fake_request)
    with pytest.raises(widget.transport.WidgetApiError):
        run(widget.transport.poll_widget_queries("token", widget.handle_widget_query))
    assert calls == ["getWebhookInfo"]

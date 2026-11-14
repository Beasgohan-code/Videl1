"""Synthetic HTTP failure must never put bot tokens in logs or API responses."""
import asyncio
import pytest
from tests.harness import load_all, run
load_all()
from filestore.utils import helpers
from webapp import services_bots


def test_http_error_does_not_log_request_url_or_token(monkeypatch):
    secret = '123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef'
    messages = []
    class Session:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def get(self, url, **kwargs):
            raise RuntimeError(f'request failed: {url} token={secret}')
    monkeypatch.setattr(helpers.aiohttp, 'ClientSession', Session)
    monkeypatch.setattr(helpers.log, 'error', lambda message: messages.append(message))
    assert run(helpers.validate_bot_token(secret)) is None
    result = run(services_bots.verify_telegram_token_diagnostic(secret))
    assert result['verified_with_telegram'] is False
    assert all(secret not in message and 'api.telegram.org' not in message for message in messages)
    assert secret not in str(result)
    assert len(messages) == 2


def test_http_timeout_remains_sanitized(monkeypatch):
    messages = []
    class Session:
        def __init__(self, **kwargs): raise asyncio.TimeoutError('123456:SECRET')
    monkeypatch.setattr(helpers.aiohttp, 'ClientSession', Session)
    monkeypatch.setattr(helpers.log, 'warning', lambda message: messages.append(message))
    assert run(helpers.validate_bot_token('123456:SECRET')) is None
    assert messages == ['Bot token validation timed out']

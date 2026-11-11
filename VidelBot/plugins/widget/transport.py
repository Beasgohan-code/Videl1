"""Outgoing-only Bot API transport for Telegram snapshot widgets.

This module sends the existing admin dashboard and detail snapshots. It does
not poll Bot API updates or handle widget queries.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp
import config

BETA_BASE = "https://api.telegram.org/beta"


class WidgetApiError(RuntimeError):
    """Bot API failure with no token, URL, or response body in its message."""

    def __init__(self, error_code: int | None = None, *, category: str = "unknown"):
        super().__init__("widget Bot API request failed")
        self.error_code = error_code if type(error_code) is int and 100 <= error_code <= 599 else None
        allowed = {"unknown", "timeout", "network", "invalid_response", "rejected",
                   "authentication", "permission", "endpoint", "conflict", "rate_limit", "server"}
        self.category = category if category in allowed else "unknown"


def _status_category(code: int | None) -> str:
    return {400: "rejected", 401: "authentication", 403: "permission", 404: "endpoint",
            409: "conflict", 429: "rate_limit"}.get(code, "server" if code and 500 <= code <= 599 else "unknown")


def api_base(mode: str | None = None) -> str:
    selected = (mode or os.environ.get("WIDGET_API_MODE", "beta")).strip().lower()
    if selected == "beta":
        return BETA_BASE
    if selected == "configured":
        return (getattr(config, "BOT_API_URL", "") or "https://api.telegram.org").rstrip("/")
    raise ValueError("Invalid widget API mode")


async def request(token: str, method: str, payload: dict[str, Any] | None = None,
                  *, mode: str | None = None, timeout: int = 12) -> Any:
    """Make one outgoing Bot API request without exposing credential-bearing errors."""
    if not token or not method.isidentifier():
        raise ValueError("Invalid widget API request")
    base = api_base(mode)
    deadline = aiohttp.ClientTimeout(total=timeout)
    url = f"{base}/bot{token}/{method}"
    try:
        async with aiohttp.ClientSession(timeout=deadline) as session:
            async with session.post(url, json=payload or {}) as response:
                status = response.status
                try:
                    data = await response.json(content_type=None)
                except (ValueError, aiohttp.ClientError):
                    if status >= 400:
                        raise WidgetApiError(status, category=_status_category(status)) from None
                    raise WidgetApiError(category="invalid_response") from None
                if response.status >= 400 or not isinstance(data, dict) or data.get("ok") is not True:
                    code = data.get("error_code") if isinstance(data, dict) else None
                    if type(code) is not int or not 100 <= code <= 599:
                        code = status if status >= 400 else None
                    raise WidgetApiError(code, category=_status_category(code))
                return data.get("result")
    except WidgetApiError:
        raise
    except asyncio.TimeoutError:
        raise WidgetApiError(category="timeout") from None
    except aiohttp.ClientError:
        raise WidgetApiError(category="network") from None
    except Exception:
        # Never retain an exception chain: aiohttp errors may include token URLs.
        raise WidgetApiError(category="unknown") from None

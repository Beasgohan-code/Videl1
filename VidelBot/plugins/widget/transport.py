"""Isolated Bot API 10.4 beta transport for native HTML widgets.

The normal WzGram receiver remains the only command/callback receiver. This
optional Bot API poller subscribes only to widget query update types and stops
when a webhook is configured. It never logs token-bearing URLs or response text.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Awaitable, Callable

import aiohttp
import config

BETA_BASE = "https://api.telegram.org/beta"


class WidgetApiError(RuntimeError):
    """Bot API failure with no token, URL, or response body in its message."""

    def __init__(self, error_code: int | None = None):
        super().__init__(f"widget Bot API request failed ({error_code or 'unknown'})")
        self.error_code = error_code


def api_base(mode: str | None = None) -> str:
    selected = (mode or os.environ.get("WIDGET_API_MODE", "beta")).strip().lower()
    if selected == "beta":
        return BETA_BASE
    if selected == "configured":
        return (getattr(config, "BOT_API_URL", "") or "https://api.telegram.org").rstrip("/")
    raise ValueError("Invalid widget API mode")


async def request(token: str, method: str, payload: dict[str, Any] | None = None,
                  *, mode: str | None = None, timeout: int = 12) -> Any:
    if not token or not method.isidentifier():
        raise ValueError("Invalid widget API request")
    base = api_base(mode)
    deadline = aiohttp.ClientTimeout(total=timeout)
    url = f"{base}/bot{token}/{method}"
    try:
        async with aiohttp.ClientSession(timeout=deadline) as session:
            async with session.post(url, json=payload or {}) as response:
                data = await response.json(content_type=None)
                if response.status >= 400 or not isinstance(data, dict) or data.get("ok") is not True:
                    raise WidgetApiError(data.get("error_code") if isinstance(data, dict) else response.status)
                return data.get("result")
    except WidgetApiError:
        raise
    except Exception as exc:
        # Do not retain aiohttp's exception string: it may contain the token URL.
        raise WidgetApiError() from exc


async def poll_widget_queries(token: str,
                              handle_update: Callable[[dict[str, Any]], Awaitable[None]],
                              *, mode: str | None = None,
                              stop_event: asyncio.Event | None = None) -> None:
    """Poll only Bot API widget queries. Never start beside a webhook."""
    stop = stop_event or asyncio.Event()
    webhook = await request(token, "getWebhookInfo", mode=mode)
    if isinstance(webhook, dict) and webhook.get("url"):
        raise WidgetApiError(409)

    offset: int | None = None
    while not stop.is_set():
        try:
            updates = await request(token, "getUpdates", {
                "offset": offset,
                "timeout": 20,
                "allowed_updates": ["widget_query", "shared_widget_query"],
            }, mode=mode, timeout=30)
            if not isinstance(updates, list):
                raise WidgetApiError()
            for update in updates:
                if not isinstance(update, dict):
                    continue
                update_id = update.get("update_id")
                try:
                    next_offset = int(update_id) + 1
                except (TypeError, ValueError):
                    continue
                if "widget_query" in update or "shared_widget_query" in update:
                    await handle_update(update)
                offset = max(offset or next_offset, next_offset)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Back off and keep credentials and Bot API response bodies out of logs.
            try:
                await asyncio.wait_for(stop.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass

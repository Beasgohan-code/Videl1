"""Shared safety helpers for Telegram dashboard and detail snapshot widgets.

The interactive `/widget` calculator and its query receiver have been retired.
This module remains as the manifest entry and shared helper for outgoing-only
`/dashboard`, `/info`, and `/sys` snapshots.
"""
from __future__ import annotations

import os

from core import botapi
from core.plugin_manager import PluginBase


def _beta_enabled() -> bool:
    """Whether outgoing snapshot sends may use Telegram's Bot API beta."""
    return os.environ.get("WIDGET_API_BETA", "true").strip().lower() in {"1", "true", "yes", "on"}


async def _primary_bot(client):
    """Return the outgoing Bot API client only for Videl's primary bot identity."""
    outgoing = botapi.bot()
    if outgoing is None or not getattr(client, "me", None) or client.me.id != outgoing.id:
        return None
    return outgoing


class Plugin(PluginBase):
    """Manifest toggle for the outgoing dashboard and detail snapshot features."""

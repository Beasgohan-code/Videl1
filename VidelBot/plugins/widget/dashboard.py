"""Admin-only, outgoing-only statistics widget. No queries or live sync."""
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import zlib

import config
from pyrogram import enums

from core import botapi
from core.plugin_manager import plugin_enabled
from plugins.widget.plugin import _beta_enabled, _primary_bot

log = logging.getLogger("videl.dashboard.widget")
ASSETS = Path(__file__).with_name("dashboard_assets")


def snapshot_data(series: dict, generated: datetime) -> dict:
    """Reduce trusted analytics to bounded numeric aggregates; never embed user records."""
    days = series["days"]
    if len(days) != 90 or any(not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in days):
        raise ValueError("Invalid snapshot dates")
    def values(key):
        data = series[key]
        if len(data) != 90 or any(type(v) is not int or v < 0 or v > 1_000_000_000 for v in data):
            raise ValueError("Invalid snapshot values")
        return data
    work = [values(k) for k in ("encode", "rename", "save", "leech", "tool")]
    jobs = [sum(row) for row in zip(*work)]
    return {"days": days, "active": values("active"), "new": values("new"),
            "jobs": jobs, "stars": values("stars"),
            "generated": generated.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")}


def dashboard_html(data: dict) -> str:
    shell = (ASSETS / "shell.html").read_text(encoding="utf-8")
    css = (ASSETS / "dashboard.css").read_text(encoding="utf-8")
    js = (ASSETS / "dashboard.js").read_text(encoding="utf-8")
    if any(shell.count(marker) != 1 for marker in ("/*CSS*/", "/*JS*/", "/*DATA*/")):
        raise ValueError("Invalid dashboard widget template")
    # The caller constructs data with snapshot_data: bounded numbers, ISO dates
    # and a UTC timestamp only. Escaping protects the HTML script context too.
    serialized = json.dumps(data, separators=(",", ":"), ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
    html = shell.replace("/*CSS*/", css).replace("/*DATA*/", serialized).replace("/*JS*/", js)
    raw = html.encode("utf-8")
    if len(raw) > 32768 or len(zlib.compress(raw, 9)) > 4096:
        raise ValueError("Dashboard widget exceeds Telegram's HTML size limit")
    return html


async def send_dashboard(client, message) -> bool:
    # This guard also protects direct callers, not just the command filter.
    sender = getattr(message, "from_user", None)
    if sender is None or sender.id not in config.ADMINS:
        return False
    if getattr(getattr(message, "chat", None), "type", None) != enums.ChatType.PRIVATE:
        await message.reply_text("Send /dashboard in my private chat to view the stats widget.")
        return False
    if not plugin_enabled("widget") or not _beta_enabled() or not botapi.enabled():
        await message.reply_text("The stats widget or aiogram bridge is disabled or unavailable. "
                                 "An operator can check the widget plugin, WIDGET_API_BETA and AIOGRAM_ENABLED.")
        return False
    outgoing = await _primary_bot(client)
    if outgoing is None:
        await message.reply_text("The stats widget is available only on Videl's primary bot.")
        return False
    mode = os.environ.get("WIDGET_API_MODE", "beta").strip().lower()
    if mode not in {"beta", "configured"}:
        await message.reply_text("Widget configuration is invalid. An operator should check WIDGET_API_MODE.")
        return False
    try:
        from core.analytics import series
        data = snapshot_data(await series(90, strict=True), datetime.now(timezone.utc))
        html = dashboard_html(data)
        from plugins.widget.dashboard_sender import send
    except Exception as exc:
        log.warning("Stats widget preparation failed (type=%s)", type(exc).__name__)
        await message.reply_text("Could not prepare the stats widget. Analytics may be unavailable; "
                                 "an operator can check the server configuration and database.")
        return False
    try:
        await send(outgoing, message.chat.id, html, mode)
    except Exception as exc:
        # No exception text: it can contain a token-bearing URL. A timeout may
        # have succeeded, so never retry or claim that the message was not sent.
        log.warning("Stats widget send could not be confirmed (type=%s)", type(exc).__name__)
        await message.reply_text("Could not confirm the stats widget send. Check this chat before retrying; "
                                 "an operator can inspect the safe server logs.")
        return False
    return True

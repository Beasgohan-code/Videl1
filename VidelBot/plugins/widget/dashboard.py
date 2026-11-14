"""Admin-only, outgoing-only statistics widget. No queries or live sync."""
from datetime import datetime, timedelta, timezone
import base64
import hashlib
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


class WidgetPayloadTooLarge(ValueError):
    """The exact snapshot cannot fit Telegram's widget size limit."""


def snapshot_data(series: dict, generated: datetime, *, default_range: int = 30) -> dict:
    """Reduce trusted analytics to bounded numeric aggregates; never embed user records."""
    days = series["days"]
    if len(days) != 90 or any(not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in days):
        raise ValueError("Invalid snapshot dates")
    try:
        first = datetime.strptime(days[0], "%Y-%m-%d")
        if any((first + timedelta(days=i)).strftime("%Y-%m-%d") != d for i, d in enumerate(days)):
            raise ValueError("Non-consecutive snapshot dates")
    except (ValueError, OverflowError):
        raise ValueError("Invalid snapshot dates") from None
    def values(key):
        data = series[key]
        if len(data) != 90 or any(type(v) is not int or v < 0 or v > 1_000_000_000 for v in data):
            raise ValueError("Invalid snapshot values")
        return data
    work = [values(k) for k in ("encode", "rename", "save", "leech", "tool")]
    jobs = [sum(row) for row in zip(*work)]
    return {"days": days, "active": values("active"), "new": values("new"),
            "jobs": jobs, "stars": values("stars"), "default_range": default_range if default_range in (7, 30, 90) else 30,
            "generated": generated.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")}


def _compact_series(values):
    """Encode non-negative counts as unsigned varints without losing precision."""
    packed = bytearray()
    for value in values:
        while value >= 128:
            packed.append((value & 127) | 128)
            value >>= 7
        packed.append(value)
    return base64.b64encode(packed).decode("ascii")


def dashboard_html(data: dict) -> str:
    shell = (ASSETS / "shell.html").read_text(encoding="utf-8")
    css = (ASSETS / "dashboard.css").read_text(encoding="utf-8")
    # Keep the generated, compact runtime tied to its readable source.
    source = (ASSETS / "dashboard.js").read_bytes()
    header, js = (ASSETS / "dashboard.min.js").read_text(encoding="utf-8").split("\n", 1)
    if header != f"/* source-sha256:{hashlib.sha256(source).hexdigest()} */":
        raise ValueError("dashboard runtime needs rebuilding")
    if any(shell.count(marker) != 1 for marker in ("/*CSS*/", "/*JS*/", "/*DATA*/")):
        raise ValueError("Invalid dashboard widget template")
    # The caller constructs data with snapshot_data: bounded numbers, ISO dates
    # and a UTC timestamp only. Escaping protects the HTML script context too.
    packed = dict(data)
    if isinstance(packed.get("days"), list) and len(packed["days"]) == 90:
        packed["days"] = packed["days"][0]
    for key in ("active", "new", "jobs", "stars"):
        values = packed.get(key)
        if isinstance(values, list) and len(values) == 90 and all(type(v) is int and v >= 0 for v in values):
            # 31 daily values support 30 complete days without the latest day.
            # The two previous 30-day totals cover both comparison windows.
            buckets = [sum(values[i:i + 3]) for i in range(0, 90, 3)]
            packed[key] = _compact_series(values[-31:] + buckets +
                                          [sum(values[-60:-30]), sum(values[-61:-31]), max(values)])
    serialized = json.dumps(packed, separators=(",", ":"), ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
    html = shell.replace("/*CSS*/", css).replace("/*JS*/", js).replace("/*DATA*/", serialized)
    raw = html.encode("utf-8")
    # Allow some headroom for differences between zlib implementations.
    if len(raw) > 32768 or len(zlib.compress(raw, 9)) > 4000:
        raise WidgetPayloadTooLarge("Dashboard widget exceeds Telegram's HTML size limit")
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
        parts = (getattr(message, "text", "") or "").split()
        requested = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        data = snapshot_data(await series(90, strict=True), datetime.now(timezone.utc), default_range=requested)
        html = dashboard_html(data)
        from plugins.widget.dashboard_sender import send
    except WidgetPayloadTooLarge:
        log.warning("Stats widget exceeds Telegram's size limit")
        await message.reply_text("The stats widget is too large for Telegram. The regular analytics charts are still available.")
        return False
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

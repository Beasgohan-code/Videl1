"""Built-in, outgoing-only private snapshots for existing commands."""
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import zlib

import config
from pyrogram import enums
from core import botapi
from core.plugin_manager import plugin_enabled
from plugins.widget.plugin import _beta_enabled, _primary_bot

log = logging.getLogger("videl.widget.details")
ASSETS = Path(__file__).with_name("detail_assets")


def _text(value, limit=80):
    if value is None or value == "":
        return "Unavailable"
    s = str(value).replace("\x00", "").strip()
    if not s:
        return "Unavailable"
    return s if len(s) <= limit else s[:limit - 1] + "…"


def _flag(value):
    return "Yes" if value is True else "No" if value is False else "Unavailable"


def _row(label, value, percent=None, *, copy_id=False):
    result = {"label": label, "value": _text(value, 48)}
    if copy_id and type(value) is int:
        result["copy"] = True
    if percent is not None:
        p = float(percent)
        if not math.isfinite(p) or not 0 <= p <= 100:
            raise ValueError("Invalid percent")
        result["percent"] = round(p, 1)
    return result


def _paged_row(label, value):
    """Bound long Telegram-visible text, but let the viewer page through it."""
    text = _text(value, 480)
    pages = [text[i:i + 32] for i in range(0, len(text), 32)]
    row = {"label": label, "value": pages[0]}
    if len(pages) > 1:
        row["pages"] = pages
    return row


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _payload(kicker, title, subtitle, sections, refresh):
    return {"kicker": kicker, "title": _text(title, 24), "subtitle": _text(subtitle, 64),
            "sections": sections, "foot": f"Snapshot · {refresh} to refresh"}


def user_payload(user):
    first, last = getattr(user, "first_name", ""), getattr(user, "last_name", "")
    name = " ".join(str(x) for x in (first, last) if x)
    username = getattr(user, "username", None)
    status = getattr(user, "status", None)
    seen = getattr(status, "name", None).replace("_", " ").title() if status and getattr(status, "name", None) else "Hidden or unavailable"
    is_bot = getattr(user, "is_bot", None)
    return _payload("VIDEL · USER", name, "Telegram-visible · " + _now(), [
        {"label": "Profile", "rows": [_row("User ID", getattr(user, "id", None), copy_id=True),
         _row("Username", "@" + str(username) if username else None),
         _row("Account", "Bot" if is_bot is True else "User" if is_bot is False else None), _row("Last seen", seen)]},
        {"label": "Signals", "rows": [_row("Premium", _flag(getattr(user, "is_premium", None))),
         _row("Verified", _flag(getattr(user, "is_verified", None))), _row("Scam", _flag(getattr(user, "is_scam", None))),
         _row("Fake", _flag(getattr(user, "is_fake", None))), _row("Restricted", _flag(getattr(user, "is_restricted", None))) ]},
        {"label": "Technical", "rows": [_row("Data center", getattr(user, "dc_id", None)),
         _row("Deleted", _flag(getattr(user, "is_deleted", None))),
         _row("Support account", _flag(getattr(user, "is_support", None))),
         _row("Visibility", "Only fields returned by Telegram")]}], "/info")


def group_payload(chat, role, count):
    username = getattr(chat, "username", None)
    chat_type = getattr(getattr(chat, "type", None), "name", "Group").replace("_", " ").title()
    return _payload("VIDEL · GROUP", getattr(chat, "title", None), "Verified · " + _now(), [
        {"label": "Overview", "rows": [_row("Group ID", getattr(chat, "id", None), copy_id=True),
         _row("Username", "@" + str(username) if username else None), _row("Type", chat_type), _row("Members", count)]},
        {"label": "Access", "rows": [_row("Your role", role), _row("Forum", _flag(getattr(chat, "is_forum", None))),
         _row("Protected content", _flag(getattr(chat, "has_protected_content", None))),
         _row("Verified", _flag(getattr(chat, "is_verified", None)))]},
        {"label": "About", "rows": [_paged_row("Description", getattr(chat, "description", None)),
         _row("Scope", "Only this accessible group")]}], "/info group")


def encoder_queue_snapshot():
    """Read only process-local scheduler counts; never expose job records."""
    try:
        from VideoEncoder.utils import scheduler
        return len(scheduler.running()), len(scheduler.waiting())
    except Exception:
        return None, None


def system_payload(cpu, mem, disk, uptime, workers, *, encoder_running=None, encoder_waiting=None):
    def mb(n): return f"{int(n) // 1048576:,} MiB" if n is not None else None
    now = _now()
    return _payload("VIDEL · SYSTEM", "Host resources", "Captured " + now, [
        {"label": "Resources", "rows": [_row("CPU", f"{cpu:.1f}%", cpu),
         _row("RAM", f"{mem.percent:.1f}%", mem.percent), _row("Disk", f"{disk.percent:.1f}%", disk.percent)]},
        {"label": "Capacity", "rows": [_row("RAM used", mb(mem.used)), _row("RAM total", mb(mem.total)), _row("RAM available", mb(getattr(mem, "available", None))),
         _row("Disk used", mb(disk.used)), _row("Disk total", mb(disk.total)),
         _row("Disk free", mb(getattr(disk, "free", None)))]},
        {"label": "Runtime", "rows": [_row("Host uptime", str(uptime).split(".")[0]),
         _row("Active clone workers", workers), _row("Encoder running", encoder_running),
         _row("Encoder waiting", encoder_waiting), _row("Scope", "This host / process only")]}], "/sys")


def detail_html(data):
    shell = (ASSETS / "shell.html").read_text(encoding="utf-8")
    css = (ASSETS / "detail.css").read_text(encoding="utf-8")
    js = (ASSETS / "detail.js").read_text(encoding="utf-8")
    if any(shell.count(marker) != 1 for marker in ("/*CSS*/", "/*DATA*/", "/*JS*/")):
        raise ValueError("Invalid widget template")
    serialized = json.dumps(data, separators=(",", ":"), ensure_ascii=True, allow_nan=False).replace("<", "\\u003c")
    # Insert trusted assets before untrusted values: a name such as /*JS*/
    # must never be mistaken for a template marker after serialization.
    html = shell.replace("/*CSS*/", css).replace("/*JS*/", js).replace("/*DATA*/", serialized)
    raw = html.encode("utf-8")
    if len(raw) > 32768 or len(zlib.compress(raw, 9)) > 4096:
        raise ValueError("Widget exceeds HTML size limit")
    return html


async def send_detail(client, message, data, *, owner_only=False):
    sender = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    if not sender or not chat or chat.type != enums.ChatType.PRIVATE or chat.id != sender.id:
        return False
    if owner_only and sender.id not in config.OWNERS:
        return False
    if not plugin_enabled("widget") or not _beta_enabled() or not botapi.enabled():
        return False
    outgoing = await _primary_bot(client)
    if outgoing is None:
        return False
    mode = os.environ.get("WIDGET_API_MODE", "beta").strip().lower()
    if mode not in {"beta", "configured"}:
        log.warning("Detail widget configuration invalid (WIDGET_API_MODE)")
        return False
    try:
        html = detail_html(data)
        from plugins.widget.detail_sender import send
    except Exception as exc:
        log.warning("Detail widget preparation failed (type=%s)", type(exc).__name__)
        return False
    try:
        await send(outgoing, chat.id, html, mode,
                   "Videl detail snapshot. Requires a compatible Telegram client; rerun the command to refresh.")
        return True
    except Exception as exc:
        # Existing command output remains the fallback. No exception text, data,
        # identity or token-bearing URL in logs. Never retry an ambiguous send.
        log.warning("Detail widget send could not be confirmed (type=%s)", type(exc).__name__)
        try:
            await message.reply_text("Could not confirm the widget send. Check this chat before retrying; the normal details are above.")
        except Exception:
            pass
        return False

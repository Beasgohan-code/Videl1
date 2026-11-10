"""Local, query-free dashboard widget; the analytics command owns routing."""
from pathlib import Path
import logging
import os
import zlib

from pyrogram import enums

from core import botapi
from core.plugin_manager import plugin_enabled
from plugins.widget.plugin import _beta_enabled, _primary_bot

log = logging.getLogger("videl.dashboard.widget")
ASSETS = Path(__file__).with_name("dashboard_assets")


def dashboard_html() -> str:
    shell = (ASSETS / "shell.html").read_text(encoding="utf-8")
    css = (ASSETS / "dashboard.css").read_text(encoding="utf-8")
    js = (ASSETS / "dashboard.js").read_text(encoding="utf-8")
    if shell.count("/*CSS*/") != 1 or shell.count("/*JS*/") != 1:
        raise ValueError("Invalid dashboard widget template")
    html = shell.replace("/*CSS*/", css).replace("/*JS*/", js)
    data = html.encode("utf-8")
    if len(data) > 32768 or len(zlib.compress(data, 9)) > 4096:
        raise ValueError("Dashboard widget exceeds Telegram's HTML size limit")
    return html


async def send_dashboard(client, message) -> bool:
    # The bot never sends a widget to a group or opens a DM on the user's behalf.
    if getattr(getattr(message, "chat", None), "type", None) != enums.ChatType.PRIVATE:
        await message.reply_text("Open this bot in a private chat and send /dashboard for the toolkit widget.")
        return False
    if not plugin_enabled("widget") or not _beta_enabled() or not botapi.enabled():
        await message.reply_text("The dashboard widget or aiogram bridge is disabled or unavailable. "
                                 "An operator can check the widget plugin, WIDGET_API_BETA and AIOGRAM_ENABLED.")
        return False
    outgoing = await _primary_bot(client)
    if outgoing is None:
        await message.reply_text("The dashboard widget is available only on Videl's primary bot.")
        return False
    mode = os.environ.get("WIDGET_API_MODE", "beta").strip().lower()
    if mode not in {"beta", "configured"}:
        await message.reply_text("Widget configuration is invalid. An operator should check WIDGET_API_MODE.")
        return False
    try:
        html = dashboard_html()
        from plugins.widget.dashboard_sender import send
    except Exception as exc:
        log.warning("Dashboard widget preparation failed (type=%s)", type(exc).__name__)
        await message.reply_text("Could not prepare the dashboard widget. An operator should check the server configuration.")
        return False
    try:
        await send(outgoing, message.chat.id, html, mode)
    except Exception as exc:
        # No exception text: it can contain a token-bearing URL. A timeout may
        # have succeeded, so never retry or claim that the message was not sent.
        log.warning("Dashboard widget send could not be confirmed (type=%s)", type(exc).__name__)
        await message.reply_text("Could not confirm the dashboard widget send. Check this chat before retrying; "
                                 "an operator can inspect the safe server logs.")
        return False
    return True

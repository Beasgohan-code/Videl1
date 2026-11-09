"""Native shared calculator widget for Telegram Bot API 10.4 beta.

The widget calculates locally as the user types. Pressing '=' sends a bounded,
validated expression to the bot; the bot answers the query and updates the
shared widget's public state. Beta API use defaults on; operators can disable it.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import os
import re
import zlib
from pathlib import Path
from typing import Any

import config
from pyrogram import Client, filters

from core import botapi
from core.db import vdb
from core.plugin_manager import PluginBase, plugin_filter
from plugins.widget import transport, tool_sender

log = logging.getLogger("videl.plugins.widget")
HTML_PATH = Path(__file__).with_name("calculator.html")
TOOLS_PATH = Path(__file__).with_name("tools")
SETTING_KEY = "shared_calculator_widget_id"
MAX_EXPR_BYTES = 56
EXPR_RE = re.compile(r"\A[0-9+\-*/().\s]{1,56}\Z")


def _beta_enabled() -> bool:
    return os.environ.get("WIDGET_API_BETA", "true").strip().lower() in {"1", "true", "yes", "on"}


def widget_html() -> str:
    raw = HTML_PATH.read_text(encoding="utf-8")
    packed = zlib.compress(raw.encode("utf-8"), 9)
    if len(raw.encode("utf-8")) > 32768 or len(packed) > 4096:
        raise ValueError("Calculator widget exceeds Telegram's HTML size limit")
    if "<script src=" in raw.lower() or "<link " in raw.lower():
        raise ValueError("Calculator widget must remain self-contained")
    return raw


def tools_html() -> str:
    """Assemble trusted built-in assets; Telegram receives one self-contained HTML document."""
    shell = (TOOLS_PATH / "shell.html").read_text(encoding="utf-8")
    css = (TOOLS_PATH / "tools.css").read_text(encoding="utf-8")
    js = (TOOLS_PATH / "tools.js").read_text(encoding="utf-8")
    html = shell.replace("/*CSS*/", css).replace("/*JS*/", js)
    raw = html.encode("utf-8")
    if len(raw) > 32768 or len(zlib.compress(raw, 9)) > 4096:
        raise ValueError("Tools widget exceeds Telegram's HTML size limit")
    return html


async def _send_tools(client, message) -> bool:
    if not _beta_enabled() or not botapi.enabled():
        await message.reply_text("Widget sending is disabled or unavailable. An operator should check the widget and aiogram settings.")
        return False
    outgoing = await _primary_bot(client)
    if outgoing is None:
        await message.reply_text("Widgets are available only on Videl's primary bot.")
        return False
    mode = os.environ.get("WIDGET_API_MODE", "beta").strip().lower()
    try:
        await tool_sender.send(outgoing, message.chat.id, getattr(message, "message_thread_id", None), tools_html(), mode)
        return True
    except Exception as exc:
        log.warning("Tools widget send failed (type=%s)", type(exc).__name__)
        await message.reply_text("Could not confirm the tools widget was sent. Check this chat before retrying; an operator can inspect the server logs.")
        return False


def evaluate_expression(expression: str) -> str:
    """Evaluate a tiny arithmetic grammar without eval/exec or Python builtins."""
    if not isinstance(expression, str) or len(expression.encode("utf-8")) > MAX_EXPR_BYTES:
        raise ValueError("Expression is too long")
    if not EXPR_RE.fullmatch(expression):
        raise ValueError("Use numbers and +, −, ×, ÷, and parentheses only")
    try:
        root = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise ValueError("Check the expression") from exc
    count = 0

    def visit(node: ast.AST, depth: int = 0) -> float:
        nonlocal count
        count += 1
        if count > 64 or depth > 16:
            raise ValueError("Expression is too complex")
        if isinstance(node, ast.Expression):
            return visit(node.body, depth + 1)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            value = float(node.value)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1)
            if isinstance(node.op, ast.USub):
                value = -value
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                value = left + right
            elif isinstance(node.op, ast.Sub):
                value = left - right
            elif isinstance(node.op, ast.Mult):
                value = left * right
            else:
                if right == 0:
                    raise ValueError("Cannot divide by zero")
                value = left / right
        else:
            raise ValueError("Unsupported expression")
        if not math.isfinite(value) or abs(value) > 1e15:
            raise ValueError("Result is out of range")
        return value

    value = visit(root)
    if value == 0:
        return "0"
    return format(value, ".12g")


def _shared_state(expression: str, result: str, revision: str) -> str:
    value = json.dumps({"expression": expression, "result": result, "revision": revision},
                       ensure_ascii=True, separators=(",", ":"))
    if len(value.encode("utf-8")) > 1024:
        raise ValueError("Widget state is too large")
    return value


def _widget_id_from_result(result: Any) -> int | None:
    """Tolerate Bot API's possible message/widget response envelope variants."""
    if not isinstance(result, dict):
        if hasattr(result, "model_dump"):
            result = result.model_dump(exclude_none=True)
        elif hasattr(result, "__dict__"):
            result = vars(result)
        else:
            return None
    value = result.get("widget_id")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) > 0:
        return int(value)
    widget = result.get("widget")
    if isinstance(widget, dict):
        value = widget.get("id")
        if isinstance(value, int) and value > 0:
            return value
        if isinstance(value, str) and value.isdigit() and int(value) > 0:
            return int(value)
    for key in ("widget", "media", "message", "result"):
        found = _widget_id_from_result(result.get(key))
        if found:
            return found
    return None


async def _primary_bot(client):
    outgoing = botapi.bot()
    if outgoing is None or not getattr(client, "me", None) or client.me.id != outgoing.id:
        return None
    return outgoing


_SEND_FAILURE_MESSAGES = {
    "rejected": "Telegram rejected the widget request. An operator should check the widget payload, chat eligibility and saved shared ID.",
    "authentication": "The widget API rejected bot authentication. An operator should check the bot configuration.",
    "permission": "The widget API refused this request. An operator should check bot and chat permissions.",
    "endpoint": "The configured widget API endpoint or method was not found. An operator should check WIDGET_API_MODE.",
    "conflict": "The widget API reported a conflict. An operator should check the bot's API configuration.",
    "rate_limit": "The widget API is rate-limiting requests. Please wait before trying again.",
    "server": "The widget API returned a server error. Please try again later.",
    "timeout": "The widget request timed out. It may have been sent; check this chat before retrying.",
    "network": "The widget request could not be completed over the network. It may have been sent; check this chat before retrying.",
    "invalid_response": "The widget API returned an unreadable response. Check this chat before retrying.",
    "unknown": "The widget request could not be completed. Check this chat before retrying; an operator can inspect the safe server logs.",
}


async def _send_widget(client, message) -> bool:
    if not _beta_enabled() or not botapi.enabled():
        await message.reply_text(
            "The calculator widget or aiogram bridge is disabled or unavailable. "
            "An operator should check WIDGET_API_BETA, AIOGRAM_ENABLED and the bot configuration."
        )
        return False
    outgoing = await _primary_bot(client)
    if outgoing is None:
        await message.reply_text("The native widget is available only on Videl's primary bot.")
        return False
    mode = os.environ.get("WIDGET_API_MODE", "beta").strip().lower()
    chat_id = message.chat.id
    thread_id = getattr(message, "message_thread_id", None)
    caption = "Videl Calculator · live arithmetic, shared across copies on supported Telegram clients."
    try:
        # Validate before any network call; never silently switch endpoints.
        transport.api_base(mode)
    except ValueError:
        log.warning("Calculator widget configuration invalid (WIDGET_API_MODE)")
        await message.reply_text("Widget configuration is invalid. An operator should check WIDGET_API_MODE.")
        return False
    try:
        widget_id = await vdb.get_setting(SETTING_KEY, None)
        if widget_id:
            payload = {"chat_id": chat_id, "widget_id": int(widget_id), "caption": caption}
            method = "sendSharedWidget"
        else:
            payload = {"chat_id": chat_id, "html": widget_html(), "caption": caption,
                       "is_shared": True, "allow_queries": True,
                       "public_data": _shared_state("", "0", "0")}
            method = "sendWidget"
        if thread_id is not None:
            payload["message_thread_id"] = int(thread_id)
    except Exception as exc:
        log.warning("Calculator widget preparation failed (%s)", type(exc).__name__)
        await message.reply_text("Could not prepare the calculator widget. An operator should check the server configuration and storage.")
        return False
    try:
        result = await transport.request(outgoing.token, method, payload, mode=mode)
    except transport.WidgetApiError as exc:
        log.warning("Calculator widget send failed (method=%s mode=%s category=%s code=%s)",
                    method, mode, exc.category, exc.error_code or "unknown")
        await message.reply_text(_SEND_FAILURE_MESSAGES[exc.category])
        return False
    except Exception as exc:
        log.warning("Calculator widget send failed (method=%s type=%s)", method, type(exc).__name__)
        await message.reply_text(_SEND_FAILURE_MESSAGES["unknown"])
        return False

    # An accepted send must never be described as a send failure or retried:
    # either can produce duplicate messages/shared objects.
    if method == "sendWidget":
        widget_id = _widget_id_from_result(result)
        if not widget_id:
            log.warning("Widget API accepted sendWidget but returned no usable shared widget id")
            await message.reply_text("The widget was sent, but Telegram did not return a reusable shared ID. Shared sync may not work.")
            return False
        try:
            await vdb.set_setting(SETTING_KEY, int(widget_id))
        except Exception as exc:
            log.warning("Widget sent but shared ID storage failed (%s)", type(exc).__name__)
            await message.reply_text("The widget was sent, but its shared ID could not be saved. Shared sync may not work; contact an operator.")
            return False
    try:
        await ensure_query_listener()
    except Exception as exc:
        log.warning("Widget sent but listener could not start (%s)", type(exc).__name__)
        await message.reply_text("The widget was sent, but shared sync could not start. Contact an operator.")
        return False
    return True


async def handle_widget_query(update: dict[str, Any]) -> None:
    query = update.get("widget_query") or update.get("shared_widget_query")
    if not isinstance(query, dict):
        return
    query_id = query.get("id")
    widget_id = query.get("widget_id")
    data = query.get("data")
    if not query_id:
        return
    result: dict[str, Any]
    expression = ""
    token = getattr(config, "BOT_TOKEN", "")
    try:
        if not isinstance(data, str) or not data.startswith("c:"):
            raise ValueError("Unknown calculator action")
        expression = data[2:]
        answer = evaluate_expression(expression or "0")
        result = {"ok": True, "expression": expression, "result": answer}
        expected_id = await vdb.get_setting(SETTING_KEY, None)
        if widget_id and expected_id and int(widget_id) == int(expected_id):
            shared = _shared_state(expression, answer, str(query_id))
            try:
                await transport.request(token, "editSharedWidgetData", {
                    "widget_id": int(widget_id), "public_data": shared, "cache_time": 5,
                }, mode=os.environ.get("WIDGET_API_MODE", "beta"))
            except Exception as exc:
                log.warning("Shared calculator state update failed (%s)", type(exc).__name__)
    except Exception as exc:
        result = {"ok": False, "error": str(exc) if isinstance(exc, ValueError) else "Calculation unavailable"}
    try:
        await transport.request(token, "answerWidgetQuery", {
            "widget_query_id": query_id,
            "data": json.dumps(result, ensure_ascii=True, separators=(",", ":")),
        }, mode=os.environ.get("WIDGET_API_MODE", "beta"))
    except Exception as exc:
        log.warning("Widget query answer failed (%s)", type(exc).__name__)


class Plugin(PluginBase):
    def __init__(self, manifest):
        super().__init__(manifest)
        self._query_task: asyncio.Task | None = None
        self._client = None

    async def startup(self, client=None) -> None:
        self._client = client
        if _beta_enabled() and botapi.enabled():
            saved_id = await vdb.get_setting(SETTING_KEY, None)
            if saved_id:
                self._start_listener()

    def _start_listener(self) -> None:
        if self._query_task is None or self._query_task.done():
            token = getattr(config, "BOT_TOKEN", "")
            if token:
                self._query_task = asyncio.create_task(
                    transport.poll_widget_queries(token, handle_widget_query,
                        mode=os.environ.get("WIDGET_API_MODE", "beta")),
                    name="videl-widget-query-poller",
                )
                self._query_task.add_done_callback(self._task_finished)

    @staticmethod
    def _task_finished(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            log.warning("Widget query listener stopped (%s)", type(exc).__name__)

    async def shutdown(self, client=None) -> None:
        if self._query_task and not self._query_task.done():
            self._query_task.cancel()
            try:
                await self._query_task
            except asyncio.CancelledError:
                pass
        self._query_task = None

    async def on_disable(self) -> None:
        await self.shutdown(self._client)

    async def on_enable(self) -> None:
        if _beta_enabled() and botapi.enabled():
            self._start_listener()


async def ensure_query_listener() -> None:
    from core.plugin_manager import plugin_manager
    instance = plugin_manager.instances.get("widget")
    if instance is not None:
        instance._start_listener()


@Client.on_message(filters.command("widget") & (filters.private | filters.group) & plugin_filter("widget"))
async def widget_command(client, message):
    parts = (getattr(message, "text", "") or "").split()
    option = parts[1].lower() if len(parts) == 2 else "" if len(parts) == 1 else "invalid"
    if option in ("", "calculator"):
        await _send_widget(client, message)
    elif option == "tools":
        await _send_tools(client, message)
    else:
        await message.reply_text("Widget platform: /widget or /widget calculator for the shared calculator; /widget tools for split bill, unit converter and counter.")

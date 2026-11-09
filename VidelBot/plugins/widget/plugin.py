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
from plugins.widget import transport

log = logging.getLogger("videl.plugins.widget")
HTML_PATH = Path(__file__).with_name("calculator.html")
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


async def _send_widget(client, message) -> bool:
    if not _beta_enabled() or not botapi.enabled():
        await message.reply_text(
            "The calculator widget is experimental and currently disabled. "
            "An operator must opt in with WIDGET_API_BETA=True and enable the aiogram bridge."
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
        widget_id = await vdb.get_setting(SETTING_KEY, None)
        if widget_id:
            payload = {"chat_id": chat_id, "widget_id": int(widget_id), "caption": caption}
            if thread_id is not None:
                payload["message_thread_id"] = int(thread_id)
            await transport.request(outgoing.token, "sendSharedWidget", payload, mode=mode)
        else:
            payload = {
                "chat_id": chat_id,
                "html": widget_html(),
                "caption": caption,
                "is_shared": True,
                "allow_queries": True,
                "public_data": _shared_state("", "0", "0"),
            }
            if thread_id is not None:
                payload["message_thread_id"] = int(thread_id)
            result = await transport.request(outgoing.token, "sendWidget", payload, mode=mode)
            widget_id = _widget_id_from_result(result)
            if not widget_id:
                log.warning("Widget API accepted sendWidget but returned no usable shared widget id")
                await message.reply_text("The widget was sent, but Telegram did not return a reusable shared ID.")
                return False
            await vdb.set_setting(SETTING_KEY, int(widget_id))
        await ensure_query_listener()
        return True
    except Exception as exc:
        log.warning("Calculator widget unavailable (%s)", type(exc).__name__)
        await message.reply_text(
            "Telegram's Bot API 10.4 widget beta is unavailable right now. "
            "The regular bot remains online; try again later or use a client that supports HTML widgets."
        )
        return False


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
    await _send_widget(client, message)

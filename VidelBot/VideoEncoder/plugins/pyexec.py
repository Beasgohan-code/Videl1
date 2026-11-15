"""
Owner-only runtime inspection commands (`/exec`, `/sh`) with:
  • Strict `config.OWNERS` filter + `ENABLE_OWNER_EXEC` environment gate
  • Local `print()` capture (never mutating global `sys.stdout` / `sys.stderr` across `await`)
  • Bounded execution timeout (`EXEC_TIMEOUT_SEC`, default 60s) with child-process termination
  • Automatic redaction of sensitive credentials (`BOT_TOKEN`, `API_HASH`, `DB_URI`, `ENCRYPTION_KEY`, etc.)
"""

import ast
import asyncio
import html
import inspect
from io import BytesIO, StringIO
import os
import re
import traceback

from pyrogram import Client, filters

import config
from ..utils.helper import check_chat

EXEC_TIMEOUT_SEC = max(5, int(os.environ.get("EXEC_TIMEOUT_SEC", "60")))


def _owner_exec_enabled() -> bool:
    return os.environ.get("ENABLE_OWNER_EXEC", "true").strip().lower() not in ("0", "false", "no", "off")


def _redact_secrets(text: str) -> str:
    if not text:
        return text
    out = str(text)
    secret_keys = (
        "BOT_TOKEN",
        "API_HASH",
        "DB_URI",
        "MONGO_URI",
        "ENCRYPTION_KEY",
        "ADMIN_WEB_TOKEN",
        "BACKEND_API_SECRET",
    )
    candidates: set[str] = set()
    for key in secret_keys:
        val = getattr(config, key, None) or os.environ.get(key)
        if isinstance(val, str) and len(val) >= 6:
            candidates.add(val)
    for secret in sorted(candidates, key=len, reverse=True):
        out = out.replace(secret, "[REDACTED]")
    out = re.sub(r"(mongodb(?:\+srv)?://)([^/\s@]+)@", r"\1[REDACTED]@", out)
    out = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b", "[REDACTED_BOT_TOKEN]", out)
    return out


@Client.on_message(filters.command("exec") & filters.user(config.OWNERS))
async def run_code(client, message):
    if not _owner_exec_enabled():
        return await message.reply_text("🔒 <code>/exec</code> is disabled by <code>ENABLE_OWNER_EXEC=false</code>.")
    if not message.from_user or message.from_user.id not in config.OWNERS:
        return
    c = await check_chat(message, chat="Owner")
    if c is None:
        return

    class UniqueExecReturnIdentifier:
        pass

    parts = (message.text or "").split(None, 1)
    code = parts[1].strip() if len(parts) > 1 else ""
    if not code:
        await message.reply_text("code 100")
        return

    buf = StringIO()

    def _local_print(*args, sep=" ", end="\n", file=None, flush=False):
        target = buf if file is None else file
        target.write(sep.join(str(a) for a in args) + end)

    tree = ast.parse(code)
    obody = tree.body
    body = obody.copy()
    body.append(ast.Return(ast.Name("_ueri", ast.Load())))

    def _gf(fn_body):
        func = ast.AsyncFunctionDef(
            "ex",
            ast.arguments(
                [],
                [ast.arg(i, None, None) for i in ["m", "message", "c", "client", "_ueri", "print"]],
                None,
                [],
                [],
                None,
                [],
            ),
            fn_body,
            [],
            None,
            None,
        )
        ast.fix_missing_locations(func)
        mod = ast.parse("")
        mod.body = [func]
        fl = locals().copy()
        exec(compile(mod, "<ast>", "exec"), globals(), fl)
        return fl["ex"]

    try:
        exx = _gf(body)
    except SyntaxError as ex:
        if ex.msg != "'return' with value in async generator":
            raise
        exx = _gf(obody)

    escaped_code = html.escape(_redact_secrets(code))
    async_obj = exx(message, message, client, client, UniqueExecReturnIdentifier, _local_print)
    reply = await message.reply_text(f"Type[py]\n<code>{escaped_code}</code>\nState[Executing]")

    try:
        async def _consume():
            if inspect.isasyncgen(async_obj):
                return [i async for i in async_obj]
            res = [await async_obj]
            return [] if res == [UniqueExecReturnIdentifier] else res

        returned = await asyncio.wait_for(_consume(), timeout=EXEC_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        return await reply.edit_text(f"⏱ Execution timed out after {EXEC_TIMEOUT_SEC}s.")
    except Exception:
        tb = _redact_secrets(traceback.format_exc())
        await message.reply_text(tb[:4000], parse_mode=None)
        return

    printed = buf.getvalue().strip("\n")
    items = []
    if printed.strip():
        items.append(printed)
    items.extend(returned)
    safe_items = [html.escape(_redact_secrets(str(i).strip("\n"))) for i in items]
    out_block = "\n".join(f"<code>{i}</code>" for i in safe_items).strip() or "undefined"
    await reply.edit_text(f"Type[py]\n<code>{escaped_code}</code>\nState[Executed]\nOutput \\\n{out_block}"[:4096])


@Client.on_message(filters.command("sh") & filters.user(config.OWNERS))
async def run_shell(client, message):
    if not _owner_exec_enabled():
        return await message.reply_text("🔒 <code>/sh</code> is disabled by <code>ENABLE_OWNER_EXEC=false</code>.")
    if not message.from_user or message.from_user.id not in config.OWNERS:
        return
    c = await check_chat(message, chat="Owner")
    if c is None:
        return
    if len((message.text or "").split(None, 1)) < 2:
        await message.reply_text("Usage: /sh [command]")
        return
    command = message.text.split(None, 1)[1]
    if not command:
        await message.reply_text("code 100")
        return
    reply = await message.reply_text("Executing...")
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(process.communicate(), timeout=EXEC_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        return await reply.edit_text(f"⏱ Shell command timed out after {EXEC_TIMEOUT_SEC}s and was terminated.")

    returncode = process.returncode
    stdout = _redact_secrets(stdout_b.decode("utf-8", errors="replace").replace("\r", "").strip("\n").rstrip())
    stderr = _redact_secrets(stderr_b.decode("utf-8", errors="replace").replace("\r", "").strip("\n").rstrip())

    text = f"<b>Exit Code:</b> <code>{returncode}</code>\n"
    if stderr:
        text += f"<code>{html.escape(stderr)}</code>\n"
    if stdout:
        text += f"<code>{html.escape(stdout)}</code>"

    if len(text) > 4096:
        out = (stderr.strip() + "\n" + stdout.strip()).strip()
        f = BytesIO(out.encode("utf-8"))
        f.name = "output.txt"
        await reply.delete()
        await message.reply_document(f, caption=f"<b>Exit Code:</b> <code>{returncode}</code>")
    else:
        await reply.edit_text(text)

"""
Scheduled Downloads & Group Announcements Runner (`VidelBot/platform_core/scheduler_daemon.py`).
"""
from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import Any

from core import bg
from platform_core import download_engine, models_db

log = logging.getLogger("videl.scheduler_daemon")

_daemon_task: asyncio.Task | None = None


async def tick_once(*, now: float | None = None) -> list[dict[str, Any]]:
    """Execute all due `scheduled_jobs` (`kind == 'download'` or `kind == 'group_announcement'`)."""
    ts = float(now if now is not None else time.time())
    due = await models_db.due_scheduled_jobs(ts)
    executed: list[dict[str, Any]] = []

    for job in due:
        jid = str(job["job_id"])
        kind = str(job.get("kind") or "download")
        uid = int(job.get("user_id") or 0)
        try:
            if kind == "download":
                res = await download_engine.engine.enqueue_url(
                    user_id=uid,
                    url=str(job["target"]),
                    destination=str(job.get("destination") or "local"),
                    auto_select_all=True,
                )
                status = f"ok:{res.get('mode')}"
            elif kind == "group_announcement":
                chat_id = int(job["target"])
                text = str((job.get("options") or {}).get("message") or "").strip()
                if not text:
                    raise ValueError("Empty announcement message.")
                import client as bot_client

                tg_bot = getattr(bot_client, "bot", None)
                if tg_bot is not None and getattr(tg_bot, "is_connected", False):
                    await tg_bot.send_message(chat_id, f"📢 {html.escape(text)}")
                    status = "ok:sent"
                else:
                    status = "deferred:bot_offline"
            else:
                status = f"error:unknown_kind:{kind}"
        except Exception as exc:
            status = f"error:{str(exc)[:180]}"
            log.warning("Scheduled job %s failed: %s", jid, exc)

        await models_db.mark_scheduled_job_ran(
            jid, status=status, interval_minutes=int(job.get("interval_minutes") or 0), now=ts
        )
        executed.append({"job_id": jid, "kind": kind, "status": status})

    return executed


async def _loop() -> None:
    while True:
        try:
            await tick_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Scheduler tick error: %s", exc)
        await asyncio.sleep(30.0)


def start_daemon() -> asyncio.Task:
    global _daemon_task
    if _daemon_task is None or _daemon_task.done():
        _daemon_task = bg.spawn(_loop(), name="platform-scheduler-daemon")
    return _daemon_task


def stop_daemon() -> None:
    global _daemon_task
    if _daemon_task and not _daemon_task.done():
        _daemon_task.cancel()
    _daemon_task = None

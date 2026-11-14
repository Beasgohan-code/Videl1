"""
Unified interactive task & server status console (`/status`).

Features:
  • Container-aware CPU, RAM (cgroup quota + process RSS), disk, network, and uptime metrics.
  • Live task telemetry (`progress`, `speed`, `ETA`, `processed/total`, `stage`, `engine`) from `jobs.snapshot()`
    merged with waiting queue positions from `VideoEncoder.data`.
  • Interactive pagination (`status page <n> <mode>`), filter modes (`all`, `mine`, `running`),
    flood-safe refresh (`status ref`), and authorized per-task cancellation (`status cancel <key>`).
"""
from __future__ import annotations

import html as _html
from time import time

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, MessageDeleteForbidden, MessageIdInvalid, MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from core.sysinfo import system_snapshot
from core.ui import styled_button as btn
from .. import botStartTime, data, download_dir, owner, sudo_users
from ..utils import jobs, scheduler
from ..utils.database.access_db import db as enc_db
from ..utils.display_progress import TimeFormatter, humanbytes
from ..utils.helper import check_chat

PAGE_SIZE = 5
_LAST_REFRESH: dict[tuple[int, int], float] = {}


def get_readable_time(seconds):
    return TimeFormatter(seconds)


def get_readable_file_size(size):
    return humanbytes(size) or "0 B"


def get_task_info(task_msg):
    user = _html.escape(task_msg.from_user.first_name) if getattr(task_msg, "from_user", None) and task_msg.from_user.first_name else "Unknown User"
    user_id = task_msg.from_user.id if getattr(task_msg, "from_user", None) else "No ID"

    task_type = "Unknown Task"
    filename = "Unknown File"

    src = getattr(task_msg, "reply_to_message", None) or task_msg
    text_content = getattr(task_msg, "text", None) or getattr(task_msg, "caption", None)
    if text_content:
        parts = text_content.split(None, 1)
        cmd = parts[0].lower() if parts else ""
        if "/sample" in cmd:
            task_type = "Sample Encode"
        elif "/trim" in cmd:
            task_type = "Trim"
        elif "/screens" in cmd:
            task_type = "Screenshots"
        elif "/dl" in cmd:
            task_type = "Telegram Encode"
        elif "/compress" in cmd:
            task_type = "Target Size Compress"
        elif "/af" in cmd:
            task_type = "Audio Processing"
        elif "/ddl" in cmd:
            task_type = "Direct Download"
        elif "/batch" in cmd:
            task_type = "Batch Process"

    if getattr(src, "document", None):
        filename = src.document.file_name or "Document"
        if task_type == "Unknown Task":
            task_type = "Telegram Encode"
    elif getattr(src, "video", None):
        filename = src.video.file_name or "Video"
        if task_type == "Unknown Task":
            task_type = "Telegram Encode"
    elif text_content and ("/ddl" in text_content or "/batch" in text_content):
        filename = "URL Task"

    return f"{task_type}: {filename}\n   └ User: <a href='tg://user?id={user_id}'>{user}</a>"


def task_parts(task_msg):
    """(task type, file name, user link html) for one queued message."""
    info = get_task_info(task_msg)
    head, _, user_line = info.partition("\n")
    kind, _, name = head.partition(": ")
    user = user_line.replace("└ User:", "").strip()
    return kind, name, user or _html.escape("Unknown User")


def _mini_bar(pct: float, width: int = 8) -> str:
    pct = max(0.0, min(100.0, float(pct or 0.0)))
    filled = int(round((pct / 100.0) * width))
    return "▰" * filled + "▱" * (width - filled)


def collect_task_entries(filter_mode: str = "all", viewer_id: int | None = None) -> list[dict]:
    """Combine live `jobs.snapshot()` telemetry with queued `VideoEncoder.data` tasks."""
    live_map = jobs.snapshot()
    running_msgs = scheduler.running()
    entries: list[dict] = []
    matched_job_keys: set = set()

    # Match running queue items to active Job entries (by user_id or order)
    user_jobs: dict[int, list[dict]] = {}
    for j in live_map.values():
        user_jobs.setdefault(int(j.get("user_id") or 0), []).append(j)

    for idx, m in enumerate(list(data), 1):
        kind, name, user_html = task_parts(m)
        uid = int(getattr(getattr(m, "from_user", None), "id", 0) or 0)
        is_running = any(m is r for r in running_msgs) or (idx == 1 and bool(live_map) and not running_msgs)
        job_info = None
        if is_running:
            cands = [j for j in user_jobs.get(uid, []) if j["key"] not in matched_job_keys]
            if not cands:
                cands = [j for j in live_map.values() if j["key"] not in matched_job_keys]
            if cands:
                job_info = cands[0]
                matched_job_keys.add(job_info["key"])

        entries.append({
            "pos": idx,
            "running": is_running,
            "uid": uid,
            "kind": kind,
            "name": (job_info.get("name") if job_info and job_info.get("name") else name) or "Media",
            "user_html": user_html,
            "job": job_info,
        })

    # Include standalone transfer jobs (e.g. /gupload or standalone downloads) not in `data`
    for k, j in live_map.items():
        if k in matched_job_keys:
            continue
        uid = int(j.get("user_id") or 0)
        entries.append({
            "pos": len(entries) + 1,
            "running": True,
            "uid": uid,
            "kind": j.get("stage") or "Transfer",
            "name": j.get("name") or f"Task #{k}",
            "user_html": f"<a href='tg://user?id={uid}'>{uid}</a>" if uid else "System",
            "job": j,
        })

    if filter_mode == "running":
        entries = [e for e in entries if e["running"]]
    elif filter_mode == "mine" and viewer_id:
        entries = [e for e in entries if e["uid"] == viewer_id]
    return entries


def status_doc(page: int = 1, filter_mode: str = "all", viewer_id: int | None = None):
    """System status + paginated live tasks as a Rich screen."""
    from core.rich import Doc, Raw

    snap = system_snapshot(download_dir)
    entries = collect_task_entries(filter_mode=filter_mode, viewer_id=viewer_id)
    total_tasks = len(entries)
    pages = max(1, (total_tasks + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(pages, int(page or 1)))
    start = (page - 1) * PAGE_SIZE
    page_items = entries[start : start + PAGE_SIZE]

    mem_label = f"{snap['mem_percent']}% ({get_readable_file_size(snap['mem_used_bytes'])} / {get_readable_file_size(snap['mem_total_bytes'])})"
    if snap["container_mem"]:
        mem_label += " · container"

    subtitle = f"{total_tasks} task{'s' if total_tasks != 1 else ''}"
    if pages > 1:
        subtitle += f" · page {page}/{pages}"
    if filter_mode != "all":
        subtitle += f" · {filter_mode}"

    doc = Doc("📈", "Server & Task Status", subtitle)
    doc.table([
        ("⚙️ CPU", f"{snap['cpu_percent']} % ({snap['cpu_cores']} vCPU)"),
        ("🧠 RAM", mem_label),
        ("💽 Free disk", f"{get_readable_file_size(snap['disk_free_bytes'])} ({100.0 - snap['disk_percent']:.1f}% free)"),
        ("📤 Up / 📥 Down", f"{get_readable_file_size(snap['net_sent_bytes'])} / {get_readable_file_size(snap['net_recv_bytes'])}"),
        ("⏱ Uptime", get_readable_time(time() - botStartTime)),
    ], header=("System", "Value"))

    doc.h("🎬", f"Active tasks ({total_tasks})")
    if page_items:
        rows = []
        for item in page_items:
            j = item["job"]
            if j:
                pct = float(j.get("progress") or 0.0)
                spd = j.get("speed_label") or (f"{get_readable_file_size(j['speed'])}/s" if j.get("speed") else "—")
                eta_txt = get_readable_time(j["eta"]) if j.get("eta") else "—"
                size_txt = ""
                if j.get("total"):
                    size_txt = f" · {get_readable_file_size(j['processed'])}/{get_readable_file_size(j['total'])}"
                elif j.get("processed"):
                    size_txt = f" · {get_readable_file_size(j['processed'])}"
                eng = f" [{j['engine']}]" if j.get("engine") else ""
                icon = "⏸" if j.get("paused") else "▶️"
                state_prefix = f"{icon} {'Paused · ' if j.get('paused') else ''}{j.get('stage') or item['kind']}{eng}"
                detail = f"{state_prefix} · {_mini_bar(pct)} {pct:.0f}% · {spd} · ETA {eta_txt}{size_txt}"
            elif item["running"]:
                detail = f"▶️ {item['kind']} · starting…"
            else:
                prio_tag = " ⚡" if id(data[item["pos"] - 1]) in scheduler.PRIO and item["pos"] <= len(data) else ""
                detail = f"⏳ Queued #{item['pos']}{prio_tag} · {item['kind']}"
            rows.append((item["pos"], detail, str(item["name"])[:42], Raw(item["user_html"])))
        doc.table(rows, header=("#", "Progress / State", "File", "User"), compact=True)
    else:
        empty_note = "No tasks match this filter." if filter_mode != "all" else "No active encodes."
        doc.text(f"<i>🥱 {empty_note}</i>")
    return doc


def _status_kb(page: int = 1, filter_mode: str = "all", viewer_id: int | None = None) -> InlineKeyboardMarkup:
    entries = collect_task_entries(filter_mode=filter_mode, viewer_id=viewer_id)
    total_tasks = len(entries)
    pages = max(1, (total_tasks + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(pages, int(page or 1)))
    start = (page - 1) * PAGE_SIZE
    page_items = entries[start : start + PAGE_SIZE]

    rows: list[list] = []

    # Per-task Pause/Resume, Stop & Boost controls for items on this page
    action_btns = []
    is_viewer_admin = bool(viewer_id and (viewer_id in owner or viewer_id in sudo_users))
    if not is_viewer_admin and viewer_id:
        import config
        is_viewer_admin = viewer_id in getattr(config, "ADMINS", ()) or viewer_id in getattr(config, "OWNERS", ())

    for item in page_items:
        j = item.get("job")
        if j and j.get("key") is not None:
            k = j["key"]
            if j.get("paused"):
                action_btns.append(btn(f"▶️ Resume #{item['pos']}", callback_data=f"status resume {k}", style="success"))
            else:
                action_btns.append(btn(f"⏸ Pause #{item['pos']}", callback_data=f"status pause {k}"))
            action_btns.append(
                btn(f"✖️ Stop #{item['pos']}", callback_data=f"status cancel {k}", style="danger")
            )
        elif not item.get("running") and is_viewer_admin and item["pos"] > 1:
            action_btns.append(
                btn(f"⚡ Boost #{item['pos']}", callback_data=f"status boost {item['pos']}", style="primary")
            )
    if action_btns:
        for i in range(0, len(action_btns), 3):
            rows.append(action_btns[i : i + 3])

    # Pagination controls when tasks span multiple pages
    if pages > 1:
        prev_p = pages if page <= 1 else page - 1
        next_p = 1 if page >= pages else page + 1
        rows.append([
            btn("⬅️ Prev", callback_data=f"status page {prev_p} {filter_mode}"),
            btn(f"📄 {page}/{pages}", callback_data=f"status page {page} {filter_mode}", style="primary"),
            btn("Next ➡️", callback_data=f"status page {next_p} {filter_mode}"),
        ])

    # Filter & refresh row
    rows.append([
        btn("🌐 All", callback_data="status filter all", style="primary" if filter_mode == "all" else None),
        btn("▶️ Running", callback_data="status filter running", style="primary" if filter_mode == "running" else None),
        btn("👤 Mine", callback_data="status filter mine", style="primary" if filter_mode == "mine" else None),
        btn("🔄 Refresh", callback_data=f"status page {page} {filter_mode}" if (page != 1 or filter_mode != "all") else "status ref", style="success"),
    ])
    return InlineKeyboardMarkup(rows)


async def _can_cancel_job(uid: int, job) -> bool:
    if job is not None and int(getattr(job, "user_id", 0) or 0) == uid:
        return True
    import config
    if uid in getattr(config, "ADMINS", ()) or uid in getattr(config, "OWNERS", ()):
        return True
    if uid in owner or uid in sudo_users:
        return True
    try:
        return str(uid) in str(await enc_db.get_sudo() or "").replace(",", " ").split()
    except Exception:
        return False


@Client.on_message(filters.command("status"))
async def mirror_status(client, message: Message):
    c = await check_chat(message, chat="Both")
    if not c:
        return
    uid = getattr(getattr(message, "from_user", None), "id", None)
    doc = status_doc(page=1, filter_mode="all", viewer_id=uid)
    kb = _status_kb(page=1, filter_mode="all", viewer_id=uid)
    if message.chat.id < 0:     # groups: a normal public message, so everyone can use Refresh & pages
        return await message.reply_text(doc.classic(), reply_markup=kb)
    from core import rich
    await rich.reply(message, doc, reply_markup=kb)


@Client.on_callback_query(filters.regex(r"^status"))
async def status_pages(client, query: CallbackQuery):
    parts = (query.data or "").split()
    cmd = parts[1] if len(parts) > 1 else "ref"
    uid = getattr(getattr(query, "from_user", None), "id", 0)
    msg = query.message
    msg_key = (getattr(getattr(msg, "chat", None), "id", 0), getattr(msg, "id", 0))

    if cmd in ("cancel", "pause", "resume") and len(parts) >= 3:
        raw_key = parts[2]
        job_key = int(raw_key) if raw_key.lstrip("-").isdigit() else raw_key
        job = jobs.get(job_key)
        if job is None:
            await query.answer("Nothing to update – that task already finished.", show_alert=True)
        elif not await _can_cancel_job(uid, job):
            return await query.answer("Only the user who started this task (or an admin) can control it.", show_alert=True)
        elif cmd == "cancel":
            await jobs.cancel(job_key)
            await query.answer("✖️ Task cancelled.")
        elif cmd == "pause":
            jobs.pause(job_key)
            await query.answer("⏸ Task paused.")
        elif cmd == "resume":
            jobs.resume(job_key)
            await query.answer("▶️ Task resumed.")
        page, mode = 1, "all"
    elif cmd == "boost" and len(parts) >= 3:
        if not await _can_cancel_job(uid, None):
            return await query.answer("Only an admin or owner can prioritize queued tasks.", show_alert=True)
        pos_num = int(parts[2]) if parts[2].isdigit() else 0
        if scheduler.promote_to_front(pos_num):
            await query.answer(f"⚡ Promoted task #{pos_num} to priority.")
        else:
            await query.answer("Task is already running or no longer waiting.", show_alert=True)
        page, mode = 1, "all"
    elif cmd == "page":
        page = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1
        mode = parts[3] if len(parts) >= 4 and parts[3] in ("all", "mine", "running") else "all"
    elif cmd == "filter":
        page = 1
        mode = parts[2] if len(parts) >= 3 and parts[2] in ("all", "mine", "running") else "all"
    elif cmd == "ref":
        now = time()
        if now - _LAST_REFRESH.get(msg_key, 0.0) < 2.0:
            return await query.answer("⏳ Just refreshed – wait a moment.")
        _LAST_REFRESH[msg_key] = now
        if len(_LAST_REFRESH) > 500:
            _LAST_REFRESH.clear()
        page, mode = 1, "all"
    else:
        return await query.answer("Unknown status action")

    doc = status_doc(page=page, filter_mode=mode, viewer_id=uid)
    kb = _status_kb(page=page, filter_mode=mode, viewer_id=uid)
    from core import rich
    try:
        if msg is not None and getattr(getattr(msg, "chat", None), "id", 0) < 0:
            await msg.edit_text(doc.classic(), reply_markup=kb)
        else:
            await rich.edit(msg, doc, reply_markup=kb)
        if cmd not in ("cancel", "pause", "resume", "boost"):
            await query.answer("Refreshed!" if cmd == "ref" else f"View: {mode} · page {page}")
    except MessageNotModified:
        if cmd not in ("cancel", "pause", "resume", "boost"):
            await query.answer("Already up to date.")
    except (MessageIdInvalid, MessageDeleteForbidden):
        await query.answer("This status card is no longer active.", show_alert=True)
    except FloodWait as fw:
        await query.answer(f"⏳ Slow down ({getattr(fw, 'value', 5)}s).", show_alert=True)
    except Exception as e:
        await query.answer(f"Error: {e}"[:190])

"""
Optional Rclone cloud transfer backend (`VideoEncoder/utils/uploads/rclone.py`).

Design goals:
  • Uses `asyncio.create_subprocess_exec("rclone", ...)` directly — never spawns a shell.
  • Enforces strict `remote_name:subpath` target syntax (`validate_rclone_remote`) so Rclone
    can never be pointed at local filesystem paths or `..` traversal segments.
  • Integrates with `VideoEncoder.utils.jobs` (`jobs.register`, `jobs.attach`, `jobs.update_progress`)
    so active Rclone uploads appear live in `/status` and `/admin`, and support instant cancellation
    plus POSIX `SIGSTOP`/`SIGCONT` pause/resume.
  • Never exposes `rclone.conf` credentials, tokens, or client secrets in logs or Telegram messages.
"""
from __future__ import annotations

import asyncio
import configparser
import html
import logging
import os
import re
import shutil
import time

import config
from .. import jobs
from ..display_progress import humanbytes

log = logging.getLogger("videl.rclone")

_REMOTE_RE = re.compile(r"^([A-Za-z0-9_-]{1,64}):([^\x00\r\n]*)$")
_STATS_RE = re.compile(
    r"Transferred:\s+([\d.]+\s*[A-Za-z]+)\s*/\s*([\d.]+\s*[A-Za-z]+),\s*([\d.]+)%\s*,\s*([\d.]+\s*[A-Za-z]+/s),\s*ETA\s*([0-9wdhms-]+)",
    re.IGNORECASE,
)
_SECRET_KV_RE = re.compile(
    r"(token|secret|password|pass|api_key|access_key|client_secret)\s*[:=]\s*\S+",
    re.IGNORECASE,
)


class RcloneNotConfiguredError(RuntimeError):
    """Raised when `rclone` binary, `rclone.conf`, or remote target is not configured."""


def rclone_config_file() -> str:
    return (getattr(config, "RCLONE_CONFIG_PATH", "") or os.environ.get("RCLONE_CONFIG", "rclone.conf")).strip()


def redact_rclone_error(text: str) -> str:
    cleaned = _SECRET_KV_RE.sub(r"\1=[REDACTED]", str(text or ""))
    return cleaned.strip()[:300]


def list_rclone_remotes(cfg_path: str | None = None) -> list[str]:
    """Return configured Rclone remote names (`[section]` headers only — zero secret values returned)."""
    path = cfg_path if cfg_path is not None else rclone_config_file()
    if not path or not os.path.isfile(path):
        return []
    parser = configparser.RawConfigParser()
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            parser.read_file(fh)
        return [s.strip() for s in parser.sections() if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", s.strip())]
    except Exception:
        return []


def validate_rclone_remote(remote_spec: str, *, allowed_remotes: list[str] | None = None) -> tuple[str, str]:
    """Validate `<remote>:<folder>` syntax and reject local paths or `..` traversal."""
    raw = (remote_spec or "").strip()
    m = _REMOTE_RE.fullmatch(raw)
    if not m:
        raise ValueError("Invalid Rclone remote target (expected 'remote:folder').")
    remote_name, subpath = m.group(1), m.group(2).strip().strip("/")
    parts = [p for p in subpath.replace("\\", "/").split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise ValueError("Path traversal ('..') is not allowed in Rclone destination.")
    if allowed_remotes is not None and remote_name not in allowed_remotes:
        raise ValueError(f"Rclone remote '{remote_name}' is not defined in rclone.conf.")
    clean_sub = "/".join(parts)
    return remote_name, f"{remote_name}:{clean_sub}" if clean_sub else f"{remote_name}:"


def _parse_eta_seconds(raw_eta: str) -> float:
    if not raw_eta or raw_eta == "-":
        return 0.0
    total = 0.0
    for val, unit in re.findall(r"(\d+)([wdhms])", raw_eta.lower()):
        mult = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
        total += int(val) * mult
    return total


async def upload_with_rclone(
    file_path: str,
    message,
    status_msg,
    *,
    remote_spec: str | None = None,
    fetch_link: bool = True,
) -> str | None:
    """Upload `file_path` to an Rclone cloud remote with live progress, pause/resume, and cancellation."""
    binary = shutil.which("rclone")
    cfg_path = rclone_config_file()
    if not binary:
        raise RcloneNotConfiguredError("rclone binary is not installed on this server.")
    if not cfg_path or not os.path.isfile(cfg_path):
        raise RcloneNotConfiguredError("rclone.conf is not configured on this server.")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    remotes = list_rclone_remotes(cfg_path)
    if not remotes:
        raise RcloneNotConfiguredError("No valid remotes found in rclone.conf.")

    chosen_spec = (remote_spec or getattr(config, "RCLONE_DEFAULT_REMOTE", "") or f"{remotes[0]}:VidelUploads").strip()
    remote_name, target_dir = validate_rclone_remote(chosen_spec, allowed_remotes=remotes)

    filename = os.path.basename(file_path)
    total_bytes = os.path.getsize(file_path)
    job_key = getattr(status_msg, "id", None)
    uid = getattr(getattr(message, "from_user", None), "id", 0)
    chat_id = getattr(getattr(message, "chat", None), "id", 0)
    own_job = False
    if job_key is not None and jobs.get(job_key) is None:
        jobs.register(job_key, uid, chat_id, name=filename, stage="Upload · Rclone", engine="Rclone")
        own_job = True
    elif job_key is not None:
        jobs.update_progress(job_key, name=filename, total=total_bytes, stage="Upload · Rclone", engine="Rclone")

    cmd = [
        binary,
        "copy",
        "--config",
        cfg_path,
        "--stats=2s",
        "--stats-one-line",
        "--retries=3",
        "--low-level-retries=5",
        file_path,
        target_dir,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    if job_key is not None:
        jobs.attach(job_key, proc)

    last_edit = 0.0
    tail_lines: list[str] = []
    try:
        while True:
            if job_key is not None and jobs.is_cancelled(job_key):
                if proc.returncode is None:
                    proc.terminate()
                break
            try:
                raw_line = await asyncio.wait_for(proc.stdout.readline(), timeout=2.5)
            except asyncio.TimeoutError:
                if proc.returncode is not None:
                    break
                continue
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            tail_lines.append(line)
            if len(tail_lines) > 12:
                tail_lines.pop(0)
            m = _STATS_RE.search(line)
            if m:
                done_str, tot_str, pct_str, spd_str, eta_str = m.groups()
                pct = max(0.0, min(100.0, float(pct_str)))
                eta_s = _parse_eta_seconds(eta_str)
                cur_est = int(total_bytes * (pct / 100.0))
                if job_key is not None:
                    jobs.update_progress(
                        job_key,
                        current=cur_est,
                        total=total_bytes,
                        pct=pct,
                        speed_label=spd_str,
                        eta=eta_s,
                        stage="Upload · Rclone",
                        engine="Rclone",
                    )
                now = time.time()
                if status_msg is not None and now - last_edit >= 4.0:
                    last_edit = now
                    bar_fill = int(round(pct / 5.0))
                    bar_txt = "█" * bar_fill + "░" * (20 - bar_fill)
                    try:
                        await status_msg.edit_text(
                            f"☁️ <b>Uploading via Rclone ({html.escape(remote_name)})…</b>\n"
                            f"📄 <code>{html.escape(filename)[:60]}</code>\n"
                            f"<code>[{bar_txt}]</code> <b>{pct:.1f}%</b>\n\n"
                            f"💾 {html.escape(done_str)} / {html.escape(tot_str)}\n"
                            f"⚡ {html.escape(spd_str)} · ⏳ {html.escape(eta_str)}"
                        )
                    except Exception:
                        pass

        rc = await proc.wait()
        if job_key is not None and jobs.is_cancelled(job_key):
            if status_msg is not None:
                try:
                    await status_msg.edit_text("🚫 <b>Rclone upload cancelled.</b>")
                except Exception:
                    pass
            return None

        if rc != 0:
            err_summary = redact_rclone_error(" | ".join(tail_lines[-3:]) or f"rclone exited with {rc}")
            log.warning("Rclone upload failed (%s): %s", rc, err_summary)
            if status_msg is not None:
                try:
                    await status_msg.edit_text(
                        f"❌ <b>Rclone upload failed:</b>\n<code>{html.escape(err_summary)}</code>"
                    )
                except Exception:
                    pass
            return None

        dest_item = f"{target_dir.rstrip('/')}/{filename}" if not target_dir.endswith(":") else f"{target_dir}{filename}"
        share_url = ""
        if fetch_link:
            try:
                lproc = await asyncio.create_subprocess_exec(
                    binary, "link", "--config", cfg_path, dest_item,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                out_b, _ = await asyncio.wait_for(lproc.communicate(), timeout=20.0)
                cand = (out_b or b"").decode("utf-8", errors="replace").strip().splitlines()
                if lproc.returncode == 0 and cand and cand[-1].startswith(("http://", "https://")):
                    share_url = cand[-1].strip()
            except Exception:
                share_url = ""

        safe_name = html.escape(filename)
        safe_remote = html.escape(remote_name)
        link_line = (
            f"🔗 <a href='{html.escape(share_url, quote=True)}'>☁️ Open Cloud Link</a>"
            if share_url
            else f"📁 Stored at <code>{html.escape(dest_item)}</code>"
        )
        summary = (
            f"✅ <b>Uploaded via Rclone ({safe_remote})</b>\n"
            f"📄 <code>{safe_name}</code> ({humanbytes(total_bytes) or '0 B'})\n"
            f"{link_line}"
        )
        client_obj = getattr(message, "_client", None)
        if client_obj is not None:
            await client_obj.send_message(chat_id=chat_id, text=summary, reply_to_message_id=getattr(message, "id", None))
        else:
            await message.reply_text(summary)
        return share_url or dest_item
    finally:
        if own_job and job_key is not None:
            jobs.unregister(job_key)

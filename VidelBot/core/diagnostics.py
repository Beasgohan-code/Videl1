"""
Centralized runtime configuration, external binary, storage backend, security,
and plugin readiness diagnostics.

Designed for `/stats` (`🩺 Health`), `/admin` Web Dashboard (`/admin/api/diagnostics`),
and startup sanity checks. Strictly sanitizes all output so secret values (tokens,
API hashes, encryption keys, MongoDB URIs, OAuth JSONs) are never included.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

import config
from core.sysinfo import system_snapshot

BINARIES = (
    ("ffmpeg", "FFmpeg", True),
    ("ffprobe", "FFprobe", True),
    ("mediainfo", "MediaInfo", False),
    ("mkvmerge", "MKVToolNix", False),
    ("7z", "7-Zip", False),
    ("deno", "Deno (yt-dlp JS)", False),
    ("rclone", "Rclone", False),
)


def binary_status() -> list[dict[str, Any]]:
    """Return availability indicators for native media/archive tools without blocking subprocesses."""
    out: list[dict[str, Any]] = []
    for cmd, label, required in BINARIES:
        found = bool(shutil.which(cmd))
        if cmd == "7z" and not found:
            found = any(shutil.which(alt) for alt in ("7za", "7zz"))
        out.append({
            "cmd": cmd,
            "label": label,
            "available": found,
            "required": required,
        })
    return out


def collect_diagnostics() -> dict[str, Any]:
    """Build a secret-free snapshot of runtime stack, resources, binaries, storage, security, and warnings."""
    import client as client_mod
    from core import bg, botapi, instance
    from core.plugin_manager import plugin_manager
    from filestore.utils import security
    from VideoEncoder.utils.uploads.storage import storage_backends_status

    st = client_mod.runtime_stack()
    res = system_snapshot(getattr(config, "DOWNLOAD_DIR", "."))
    bins = binary_status()
    storage = storage_backends_status()
    plugins = plugin_manager.health_report()

    enc_active = bool(security.is_encryption_enabled())
    exec_enabled = os.environ.get("ENABLE_OWNER_EXEC", "true").strip().lower() not in ("0", "false", "no", "off")
    backup_on = bool(getattr(config, "BACKUP_ENABLED", True))
    single_inst = bool(getattr(config, "SINGLE_INSTANCE", True))
    lease_held = bool(instance.STATE.get("held")) if single_inst else False
    log_channel_set = bool(getattr(config, "LOG_CHANNEL", 0))
    web_dash = bool(getattr(config, "WEB_DASHBOARD", True) or getattr(config, "ADMIN_WEB_TOKEN", ""))

    warnings: list[str] = []
    for b in bins:
        if b["required"] and not b["available"]:
            warnings.append(f"Required binary '{b['cmd']}' is missing from PATH.")
    if not enc_active:
        warnings.append("ENCRYPTION_KEY is unset — session strings and clone tokens fall back to unencrypted storage.")
    if not single_inst:
        warnings.append("SINGLE_INSTANCE=false — concurrent deployments may process updates twice.")
    if not log_channel_set:
        warnings.append("LOG_CHANNEL is not set — boot/payment/audit events only go to owner DM.")
    if res["disk_percent"] >= 90.0:
        warnings.append(f"Disk usage is critically high ({res['disk_percent']:.1f}%).")
    if res["mem_percent"] >= 90.0:
        warnings.append(f"Memory usage is high ({res['mem_percent']:.1f}%).")
    if plugins["errors"] or plugins["manifest_errors"]:
        n_err = len(plugins["errors"]) + len(plugins["manifest_errors"])
        warnings.append(f"{n_err} modular plugin(s) reported load or manifest errors.")

    return {
        "runtime": {
            "engine": st.get("engine", "WzGram"),
            "version": st.get("version", ""),
            "layer": st.get("layer", 0),
            "warpcrypto": st.get("warpcrypto", ""),
            "richgram": st.get("richgram", ""),
            "rich_available": bool(st.get("rich_available")),
            "botapi_engine": st.get("botapi_engine", "aiogram"),
            "botapi_enabled": bool(botapi.enabled()),
            "bg_tasks": bg.running(),
            "encoder_workers": int(getattr(config, "ENCODER_WORKERS", 1) or 1),
        },
        "resources": res,
        "binaries": bins,
        "storage": storage,
        "security": {
            "encryption_active": enc_active,
            "single_instance": single_inst,
            "lease_held": lease_held,
            "backup_enabled": backup_on,
            "web_dashboard": web_dash,
            "log_channel_set": log_channel_set,
            "owner_exec_enabled": exec_enabled,
        },
        "plugins": plugins,
        "warnings": warnings,
    }

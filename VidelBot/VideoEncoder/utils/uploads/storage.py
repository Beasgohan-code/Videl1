"""
Clean Videl1-native storage destination registry & readiness checks.

Provides a unified status and dispatch layer across Videl1's upload destinations:
  • `telegram` — Telegram Cloud via WzGram (automatic playable keyframe / archive part splitting)
  • `gdrive`   — Google Drive API v3 (OAuth `token.json` or Service Account `GDRIVE_SA_FILE`)
  • `ddl`      — Ephemeral/public DDL mirrors (`GoFile` / `Catbox` / `Litterbox` via `core.ui.upload_to_host`)
  • `rclone`   — Optional Rclone cloud remote check (only reported ready when `rclone` binary + `rclone.conf` exist)

Never exposes tokens, OAuth JSONs, or Rclone remote secrets in status output.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any

import config
from .drive import DriveNotConfiguredError, is_drive_configured


@dataclass(frozen=True)
class StorageBackendStatus:
    key: str
    label: str
    available: bool
    detail: str
    optional: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "available": self.available,
            "optional": self.optional,
            "detail": self.detail,
        }


def is_rclone_configured() -> tuple[bool, str]:
    """Check whether optional `rclone` binary and config file are present without reading secrets."""
    binary = shutil.which("rclone")
    cfg_path = os.environ.get("RCLONE_CONFIG", "rclone.conf").strip()
    has_cfg = bool(cfg_path) and os.path.isfile(cfg_path)
    if binary and has_cfg:
        return True, "rclone binary + config present"
    if binary and not has_cfg:
        return False, "rclone installed (no rclone.conf)"
    return False, "not installed (optional)"


def storage_backends_status() -> list[dict[str, Any]]:
    """Return structured, secret-free readiness indicators for all supported storage destinations."""
    split_mb = max(50, min(1990, int(getattr(config, "SPLIT_SIZE_MB", 1950) or 1950)))
    tg = StorageBackendStatus(
        key="telegram",
        label="Telegram Cloud",
        available=bool(getattr(config, "BOT_TOKEN", "")),
        optional=False,
        detail=f"Auto-split > {split_mb} MiB · WzGram MTProto",
    )

    gd_ok = is_drive_configured()
    idx_set = bool((getattr(config, "INDEX_URL", "") or "").strip())
    gd = StorageBackendStatus(
        key="gdrive",
        label="Google Drive",
        available=gd_ok,
        optional=True,
        detail=(
            f"Ready{' + Index URL' if idx_set else ''}"
            if gd_ok
            else "Unconfigured (set token.json or GDRIVE_SA_FILE)"
        ),
    )

    ddl = StorageBackendStatus(
        key="ddl",
        label="DDL Mirrors (Catbox / Litterbox)",
        available=True,
        optional=True,
        detail="HTTPS multipart upload with timeout",
    )

    rc_ok, rc_detail = is_rclone_configured()
    rc = StorageBackendStatus(
        key="rclone",
        label="Rclone Cloud",
        available=rc_ok,
        optional=True,
        detail=rc_detail,
    )

    return [b.to_dict() for b in (tg, gd, ddl, rc)]


async def dispatch_upload(new_file: str, message, msg, *, force_telegram: bool = False) -> str | None:
    """Upload `new_file` to the user's selected backend, falling back cleanly to Telegram if Drive is unconfigured."""
    from ..database.access_db import db
    from .drive.upload import Uploader
    from .telegram import upload_to_tg

    uid = getattr(getattr(message, "from_user", None), "id", 0)
    use_drive = (not force_telegram) and bool(await db.get_drive(uid))
    if use_drive:
        if not is_drive_configured():
            try:
                await msg.edit_text(
                    "ℹ️ <b>Google Drive is not configured on this server</b> – uploading to Telegram instead…"
                )
            except Exception:
                pass
            return await upload_to_tg(new_file, message, msg)
        try:
            return await Uploader().upload_to_drive(new_file, message, msg)
        except DriveNotConfiguredError:
            return await upload_to_tg(new_file, message, msg)
    return await upload_to_tg(new_file, message, msg)

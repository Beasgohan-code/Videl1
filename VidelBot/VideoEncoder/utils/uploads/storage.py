"""
Clean Videl1-native storage destination registry, readiness inspection & upload dispatcher.

Supported storage destinations:
  • `telegram` — Telegram Cloud via WzGram (automatic playable keyframe / archive part splitting)
  • `gdrive`   — Google Drive API v3 (OAuth `token.json` or Service Account `GDRIVE_SA_FILE` + optional `INDEX_URL`)
  • `rclone`   — Optional Rclone cloud remotes (`rclone` binary + `RCLONE_CONFIG_PATH` / `rclone.conf`)
  • `gofile`   — Optional streaming GoFile direct cloud upload (`GOFILE_TOKEN` or guest upload server)
  • `ddl`      — Ephemeral/public DDL mirrors (`Catbox` / `Litterbox` via `core.ui.upload_to_host`)

Never exposes tokens, OAuth JSONs, or Rclone remote secrets in status output or error messages.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Any

import config
from .drive import DriveNotConfiguredError, is_drive_configured
from .rclone import RcloneNotConfiguredError, list_rclone_remotes, rclone_config_file

VALID_UPLOAD_DESTS = ("telegram", "gdrive", "rclone", "gofile")


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
    """Check whether optional `rclone` binary, `rclone.conf`, and >=1 remote exist without reading secrets."""
    binary = shutil.which("rclone")
    cfg_path = rclone_config_file()
    has_cfg = bool(cfg_path) and os.path.isfile(cfg_path)
    if binary and has_cfg:
        remotes = list_rclone_remotes(cfg_path)
        if remotes:
            return True, f"Ready ({len(remotes)} remote{'s' if len(remotes) != 1 else ''})"
        return False, "rclone.conf has no remotes"
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

    rc_ok, rc_detail = is_rclone_configured()
    rc = StorageBackendStatus(
        key="rclone",
        label="Rclone Cloud",
        available=rc_ok,
        optional=True,
        detail=rc_detail,
    )

    gf_tok = bool((getattr(config, "GOFILE_TOKEN", "") or "").strip())
    gf = StorageBackendStatus(
        key="gofile",
        label="GoFile Cloud",
        available=True,
        optional=True,
        detail="Authenticated API token configured" if gf_tok else "Ready (guest server mode)",
    )

    ddl = StorageBackendStatus(
        key="ddl",
        label="DDL Mirrors (Catbox / Litterbox)",
        available=True,
        optional=True,
        detail="HTTPS multipart upload with timeout",
    )

    return [b.to_dict() for b in (tg, gd, rc, gf, ddl)]


async def resolve_user_destination(uid: int) -> str:
    """Resolve user's preferred upload destination (`upload_dest` string or legacy `drive` boolean)."""
    from ..database.access_db import db

    try:
        s = await db.get_settings(uid)
        dest = str((s or {}).get("upload_dest") or "").strip().lower()
        if dest in VALID_UPLOAD_DESTS:
            return dest
    except Exception:
        pass
    try:
        if bool(await db.get_drive(uid)):
            return "gdrive"
    except Exception:
        pass
    return "telegram"


async def dispatch_upload(
    new_file: str,
    message,
    msg,
    *,
    force_telegram: bool = False,
    backend: str | None = None,
    rclone_remote: str | None = None,
) -> str | None:
    """Upload `new_file` to the requested/user-selected backend, falling back safely to Telegram if unconfigured."""
    from .drive.upload import Uploader
    from .gofile import upload_to_gofile
    from .rclone import upload_with_rclone
    from .telegram import upload_to_tg

    if force_telegram:
        return await upload_to_tg(new_file, message, msg)

    uid = getattr(getattr(message, "from_user", None), "id", 0)
    target = (backend or await resolve_user_destination(uid)).strip().lower()

    if target == "gdrive":
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

    if target == "rclone":
        rc_ok, _ = is_rclone_configured()
        if not rc_ok:
            try:
                await msg.edit_text(
                    "ℹ️ <b>Rclone is not configured on this server</b> – uploading to Telegram instead…"
                )
            except Exception:
                pass
            return await upload_to_tg(new_file, message, msg)
        try:
            return await upload_with_rclone(new_file, message, msg, remote_spec=rclone_remote)
        except RcloneNotConfiguredError:
            return await upload_to_tg(new_file, message, msg)

    if target == "gofile":
        res = await upload_to_gofile(new_file, message, msg)
        if res is not None:
            return res
        return None

    return await upload_to_tg(new_file, message, msg)

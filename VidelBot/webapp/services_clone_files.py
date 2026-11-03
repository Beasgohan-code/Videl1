"""
Bot Clone Studio, Configuration Migration Center, File Store Studio,
File Intelligence Center & Bot/Owner Analytics (`VidelBot/webapp/services_clone_files.py`).
"""
from __future__ import annotations

import copy
import hashlib
import csv
import io
import mimetypes
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from platform_core import models_db, providers
from webapp import db_models, services_bots

CLONEABLE_SECTIONS = (
    "profile",
    "commands",
    "features",
    "filestore",
    "welcome",
    "ui_preferences",
    "notifications",
    "plugin_settings",
    "automations",
    "text_templates",
)

DEFAULT_FILESTORE_CONFIG: dict[str, Any] = {
    "storage_channel_id": 0,
    "destination": "local",
    "allowed_types": ["video", "audio", "image", "pdf", "document", "archive", "dataset", "other"],
    "max_file_mb": 2000,
    "caption_template": "{filename} · {filesize}",
    "thumbnail_mode": "auto",
    "search_enabled": True,
    "page_size": 25,
    "duplicate_policy": "warn",  # warn | allow | block
    "cleanup_days": 0,           # 0 = never auto-clean
    "access_policy": "link_holders",  # public | link_holders | authorized_only
}


# ─────────────────────────── Bot Clone Studio & Migration Center (Sections 7 & 24) ───────────────────────────
def validate_clone_sections(sections: list[str] | None) -> list[str]:
    if sections is None:
        return list(CLONEABLE_SECTIONS)
    if not isinstance(sections, list) or not sections or any(
        not isinstance(section, str) or section not in CLONEABLE_SECTIONS for section in sections
    ):
        raise services_bots.SaaSValidationError("Select at least one supported configuration section.")
    return list(dict.fromkeys(sections))


async def extract_cloneable_bundle(workspace_id: str, bot_id: str) -> dict[str, Any]:
    """Extract ONLY safe, non-secret configuration sections from an owned source bot."""
    bot = await services_bots.get_owned_workspace_bot(workspace_id, bot_id)
    cmds = await services_bots.list_bot_commands(workspace_id, bot_id)
    fs_cfg = await get_filestore_config(workspace_id, bot_id)
    autos = await db_models.automations_col().find({"workspace_id": workspace_id, "bot_id": bot_id}).to_list(length=50)
    for a in autos:
        a.pop("_id", None)

    settings = db_models.strip_sensitive_fields(bot.get("settings") or {})
    features = db_models.strip_sensitive_fields(bot.get("features") or {})

    return {
        "bot_id": bot["bot_id"],
        "display_name": bot.get("display_name", ""),
        "bot_type": bot.get("bot_type", "custom"),
        "sections": {
            "profile": {
                "description": bot.get("description", ""),
                "category": bot.get("category", "Custom"),
                "avatar_emoji": bot.get("avatar_emoji", "🤖"),
                "tags": list(bot.get("tags") or []),
            },
            "commands": [
                {
                    "command": c["command"],
                    "description": c["description"],
                    "category": c.get("category", "General"),
                    "visibility": c.get("visibility", "public"),
                    "permission": c.get("permission", "everyone"),
                    "enabled": bool(c.get("enabled", True)),
                    "custom_reply_text": c.get("custom_reply_text", ""),
                }
                for c in cmds
            ],
            "features": features,
            "filestore": db_models.strip_sensitive_fields({
                k: v for k, v in fs_cfg.items() if k not in ("workspace_id", "bot_id", "storage_channel_id")
            }),
            "welcome": {
                "welcome_message": settings.get("welcome_message", ""),
            },
            "ui_preferences": {
                "compact_cards": bool(settings.get("compact_cards", False)),
                "rich_formatting": bool(settings.get("rich_formatting", True)),
            },
            "notifications": {
                "notify_on_error": bool(settings.get("notify_on_error", True)),
                "notify_on_upload": bool(settings.get("notify_on_upload", False)),
            },
            "plugin_settings": db_models.strip_sensitive_fields(bot.get("plugin_settings") or {}),
            "automations": [
                {
                    "name": a.get("name", ""),
                    "trigger": a.get("trigger", ""),
                    "conditions": db_models.strip_sensitive_fields(a.get("conditions") or []),
                    "actions": db_models.strip_sensitive_fields(a.get("actions") or []),
                }
                for a in autos
            ],
            "text_templates": {
                "custom_caption": settings.get("custom_caption", ""),
            },
        },
        "excluded_security_fields": sorted(db_models.NEVER_CLONE_OR_EXPORT_KEYS),
    }


async def preview_clone_operation(
    workspace_id: str,
    *,
    source_bot_id: str,
    target_bot_id: str,
    sections: list[str] | None = None,
    excluded_fields: list[str] | None = None,
) -> dict[str, Any]:
    if source_bot_id == target_bot_id:
        raise services_bots.SaaSValidationError("Source bot and destination bot must be different.")
    src_bundle = await extract_cloneable_bundle(workspace_id, source_bot_id)
    tgt_bundle = await extract_cloneable_bundle(workspace_id, target_bot_id)

    chosen = validate_clone_sections(sections)

    if excluded_fields is not None and (not isinstance(excluded_fields, list) or
                                        any(not isinstance(field, str) for field in excluded_fields)):
        raise services_bots.SaaSValidationError("Excluded fields must be a list of field names.")
    excl = set(excluded_fields or [])
    compatibility_warnings: list[str] = []
    if src_bundle["bot_type"] != tgt_bundle["bot_type"]:
        compatibility_warnings.append(
            f"Bot type differs ({src_bundle['bot_type']} → {tgt_bundle['bot_type']}); incompatible feature dependencies will be validated."
        )

    section_diffs: dict[str, Any] = {}
    conflicts_count = 0
    for sec in chosen:
        s_val = copy.deepcopy(src_bundle["sections"].get(sec))
        t_val = copy.deepcopy(tgt_bundle["sections"].get(sec))
        if isinstance(s_val, dict):
            for ef in excl:
                s_val.pop(ef, None)
            diffs = services_bots.compute_config_diff(t_val if isinstance(t_val, dict) else {}, s_val, prefix=sec)
            conflicts_count += sum(1 for d in diffs if d.get("conflict"))
            section_diffs[sec] = diffs
        elif isinstance(s_val, list):
            section_diffs[sec] = [{"field": f"{sec}[]", "before": len(t_val or []), "after": len(s_val), "conflict": bool(t_val)}]
            if t_val:
                conflicts_count += 1

    # Validate feature dependencies on prospective merged features
    if "features" in chosen:
        merged_feat = dict(tgt_bundle["sections"]["features"])
        merged_feat.update({k: v for k, v in src_bundle["sections"]["features"].items() if k not in excl})
        dep_errs = services_bots.validate_feature_dependencies(merged_feat)
        if dep_errs:
            raise services_bots.SaaSValidationError("; ".join(dep_errs))

    return {
        "valid": True,
        "source_bot": {"bot_id": src_bundle["bot_id"], "display_name": src_bundle["display_name"], "bot_type": src_bundle["bot_type"]},
        "target_bot": {"bot_id": tgt_bundle["bot_id"], "display_name": tgt_bundle["display_name"], "bot_type": tgt_bundle["bot_type"]},
        "selected_sections": chosen,
        "excluded_fields": sorted(excl),
        "conflicts_count": conflicts_count,
        "compatibility_warnings": compatibility_warnings,
        "diffs": section_diffs,
        "sensitive_fields_filtered": True,
        "free_text_not_scanned": True,
    }


async def execute_clone_operation(
    workspace_id: str,
    actor_id: int,
    *,
    source_bot_id: str,
    target_bot_id: str,
    sections: list[str] | None = None,
    excluded_fields: list[str] | None = None,
    kind: str = "clone",  # clone | migration
) -> dict[str, Any]:
    preview = await preview_clone_operation(
        workspace_id,
        source_bot_id=source_bot_id,
        target_bot_id=target_bot_id,
        sections=sections,
        excluded_fields=excluded_fields,
    )
    src_bundle = await extract_cloneable_bundle(workspace_id, source_bot_id)
    tgt_raw = await services_bots.get_owned_workspace_bot(workspace_id, target_bot_id)
    chosen = preview["selected_sections"]
    excl = set(preview["excluded_fields"])
    now = time.time()

    upd_bot: dict[str, Any] = {"updated_ts": now}
    next_settings = dict(tgt_raw.get("settings") or {})
    next_features = dict(tgt_raw.get("features") or {})

    if "profile" in chosen:
        prof = {k: v for k, v in src_bundle["sections"]["profile"].items() if k not in excl}
        upd_bot.update(prof)
    if "features" in chosen:
        for k, v in src_bundle["sections"]["features"].items():
            if k not in excl:
                next_features[k] = bool(v)
    if "welcome" in chosen and "welcome_message" not in excl:
        next_settings["welcome_message"] = src_bundle["sections"]["welcome"].get("welcome_message", "")
    if "ui_preferences" in chosen:
        for k, v in src_bundle["sections"]["ui_preferences"].items():
            if k not in excl:
                next_settings[k] = v
    if "notifications" in chosen:
        for k, v in src_bundle["sections"]["notifications"].items():
            if k not in excl:
                next_settings[k] = v
    if "text_templates" in chosen and "custom_caption" not in excl:
        next_settings["custom_caption"] = src_bundle["sections"]["text_templates"].get("custom_caption", "")
    if "plugin_settings" in chosen:
        upd_bot["plugin_settings"] = db_models.strip_sensitive_fields(src_bundle["sections"]["plugin_settings"])

    upd_bot["features"] = next_features
    # Do not strip unrelated target settings when cloning selected fields.
    upd_bot["settings"] = next_settings
    upd_bot["config_version"] = int(tgt_raw.get("config_version") or 1) + 1
    guard: dict[str, Any] = {"bot_id": target_bot_id, "workspace_id": workspace_id,
                             "config_version": tgt_raw.get("config_version"),
                             "features": tgt_raw.get("features"), "settings": tgt_raw.get("settings")}
    for key in ("description", "category", "avatar_emoji", "tags", "plugin_settings"):
        if key in upd_bot:
            guard[key] = tgt_raw.get(key)
    result = await db_models.bots_col().update_one(guard, {"$set": upd_bot})
    if not result.matched_count:
        raise services_bots.SaaSValidationError("Destination changed concurrently; preview the clone again.")

    if "commands" in chosen:
        for cmd_item in src_bundle["sections"]["commands"]:
            await services_bots.upsert_bot_command(workspace_id, target_bot_id, actor_id, cmd_item)

    if "filestore" in chosen:
        await update_filestore_config(workspace_id, target_bot_id, actor_id, src_bundle["sections"]["filestore"])

    if "automations" in chosen:
        for wf in src_bundle["sections"]["automations"]:
            await db_models.automations_col().insert_one({
                "workflow_id": f"wf_{secrets.token_hex(6)}",
                "workspace_id": workspace_id,
                "bot_id": target_bot_id,
                "name": f"{wf.get('name', 'Cloned Workflow')} (Cloned)",
                "trigger": wf.get("trigger", "file_uploaded"),
                "conditions": wf.get("conditions") or [],
                "actions": wf.get("actions") or [],
                "enabled": False,  # Safe default: cloned automations start paused until owner enables
                "version": 1,
                "created_by": int(actor_id),
                "created_at": db_models.utcnow_iso(),
                "created_ts": now,
            })

    # Rollback is offered only for bot-record sections where the prior values
    # are known. Cross-collection commands, policies, plugins and automations
    # need transactional history before they can be restored safely.
    rollback_supported = not bool(set(chosen) & {"commands", "filestore", "automations", "plugin_settings"})
    if "features" in chosen and (
        db_models.strip_sensitive_fields(tgt_raw.get("features") or {}) != (tgt_raw.get("features") or {})
        or any(not isinstance(v, bool) for v in (tgt_raw.get("features") or {}).values())
    ):
        rollback_supported = False
    bot_fields = [k for k in ("description", "category", "avatar_emoji", "tags", "features")
                  if k in upd_bot and (k != "features" or "features" in chosen)]
    settings_fields = []
    for sec, keys in (("welcome", ("welcome_message",)), ("ui_preferences", ("compact_cards", "rich_formatting")),
                      ("notifications", ("notify_on_error", "notify_on_upload")), ("text_templates", ("custom_caption",))):
        if sec in chosen:
            settings_fields.extend(k for k in keys if k not in excl)
    old_settings = tgt_raw.get("settings") or {}
    rollback_snapshot = {
        "bot_before": {k: {"present": k in tgt_raw, "value": copy.deepcopy(tgt_raw.get(k))} for k in bot_fields},
        "bot_after": {k: copy.deepcopy(upd_bot[k]) for k in bot_fields},
        "settings_before": {k: {"present": k in old_settings, "value": copy.deepcopy(old_settings.get(k))} for k in settings_fields},
        "settings_after": {k: copy.deepcopy(next_settings.get(k)) for k in settings_fields},
        "expected_version": upd_bot["config_version"],
    } if rollback_supported else {}
    op_id = f"{'mig' if kind == 'migration' else 'cln'}_{secrets.token_hex(6)}"
    report = {
        "clone_id": op_id,
        "migration_id": op_id,
        "kind": kind,
        "workspace_id": workspace_id,
        "actor_id": int(actor_id),
        "source_bot_id": source_bot_id,
        "target_bot_id": target_bot_id,
        "sections": chosen,
        "excluded_fields": sorted(excl),
        "conflicts_resolved": preview["conflicts_count"],
        "warnings": preview["compatibility_warnings"],
        "sensitive_fields_filtered": True,
        "free_text_not_scanned": True,
        "rollback_snapshot": rollback_snapshot,
        "rollback_supported": rollback_supported,
        "rolled_back": False,
        "status": "completed",
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
    }
    await db_models.clones_col().insert_one(dict(report))
    if kind == "migration":
        await db_models.migrations_col().insert_one(dict(report))

    await models_db.log_audit(
        actor_id, f"bot_{kind}_execute", target=f"{source_bot_id}->{target_bot_id}", detail=",".join(chosen)
    )
    await db_models.create_notification(
        workspace_id,
        bot_id=target_bot_id,
        category="clone_completed",
        title=f"Configuration {kind} completed",
        message=f"Copied {len(chosen)} section(s) from {src_bundle['display_name']} to {preview['target_bot']['display_name']}.",
        severity="success",
    )
    report.pop("_id", None)
    report.pop("rollback_snapshot", None)
    target_after = services_bots._public_bot_doc(await services_bots.get_owned_workspace_bot(workspace_id, target_bot_id))
    return {**report, "clone": dict(report), "target_bot": target_after}


async def rollback_clone_operation(workspace_id: str, clone_id: str, actor_id: int) -> dict[str, Any]:
    rec = await db_models.clones_col().find_one({"clone_id": str(clone_id), "workspace_id": workspace_id})
    if not rec:
        raise KeyError("Clone/migration record not found in this workspace.")
    if rec.get("rolled_back"):
        raise services_bots.SaaSValidationError("This operation has already been rolled back.")

    if not rec.get("rollback_supported"):
        raise services_bots.SaaSValidationError("Rollback is unavailable for this operation; its full state cannot be restored safely.")
    snap = rec.get("rollback_snapshot") or {}
    if not all(k in snap for k in ("bot_before", "bot_after", "settings_before", "settings_after", "expected_version")):
        raise services_bots.SaaSValidationError("Rollback snapshot is incomplete.")
    target_bot_id = rec["target_bot_id"]
    tgt = await services_bots.get_owned_workspace_bot(workspace_id, target_bot_id)
    current_settings = tgt.get("settings") or {}
    if int(tgt.get("config_version") or 1) != snap["expected_version"]:
        raise services_bots.SaaSValidationError("Destination changed after cloning; rollback would overwrite newer changes.")
    if any(tgt.get(k) != v for k, v in snap["bot_after"].items()) or any(
        current_settings.get(k) != v for k, v in snap["settings_after"].items()
    ):
        raise services_bots.SaaSValidationError("Destination changed after cloning; rollback would overwrite newer changes.")

    restored_settings = copy.deepcopy(current_settings)
    for key, state in snap["settings_before"].items():
        if state["present"]:
            restored_settings[key] = copy.deepcopy(state["value"])
        else:
            restored_settings.pop(key, None)
    updates: dict[str, Any] = {"settings": restored_settings, "updated_ts": time.time(),
                               "config_version": snap["expected_version"] + 1}
    unset: dict[str, str] = {}
    for key, state in snap["bot_before"].items():
        if state["present"]:
            updates[key] = copy.deepcopy(state["value"])
        else:
            unset[key] = ""
    # Compare-and-set detects writes occurring after the snapshot check.
    match = {"workspace_id": workspace_id, "bot_id": target_bot_id,
             "config_version": snap["expected_version"], "settings": current_settings,
             **snap["bot_after"]}
    patch: dict[str, Any] = {"$set": updates}
    if unset:
        patch["$unset"] = unset
    result = await db_models.bots_col().update_one(match, patch)
    if not result.matched_count:
        raise services_bots.SaaSValidationError("Destination changed concurrently; reload before rollback.")
    await db_models.clones_col().update_one(
        {"workspace_id": workspace_id, "clone_id": str(clone_id)},
        {"$set": {"rolled_back": True, "rolled_back_at": db_models.utcnow_iso()}},
    )
    await models_db.log_audit(actor_id, "bot_clone_rollback", target=f"{workspace_id}:{clone_id}")
    target_restored = services_bots._public_bot_doc(await services_bots.get_owned_workspace_bot(workspace_id, target_bot_id))
    return {"clone_id": clone_id, "target_bot_id": target_bot_id, "rolled_back": True, "target_bot": target_restored}


async def list_clone_history(workspace_id: str, *, kind: str = "") -> list[dict[str, Any]]:
    q: dict[str, Any] = {"workspace_id": workspace_id}
    if kind:
        q["kind"] = kind
    rows = await db_models.clones_col().find(q).sort("created_ts", -1).limit(50).to_list(length=50)
    for r in rows:
        r.pop("_id", None)
        r.pop("rollback_snapshot", None)
    return rows


# ─────────────────────────── File Store Studio & Intelligence (Sections 11, 12, 13) ───────────────────────────
async def get_filestore_config(workspace_id: str, bot_id: str = "default") -> dict[str, Any]:
    doc = await db_models.filestore_cfg_col().find_one({"workspace_id": workspace_id, "bot_id": str(bot_id)})
    merged = copy.deepcopy(DEFAULT_FILESTORE_CONFIG)
    if doc:
        for k in merged:
            if k in doc:
                merged[k] = doc[k]
    merged["workspace_id"] = workspace_id
    merged["bot_id"] = str(bot_id)
    return merged


async def update_filestore_config(
    workspace_id: str, bot_id: str, actor_id: int, patch: dict[str, Any]
) -> dict[str, Any]:
    current = await get_filestore_config(workspace_id, bot_id)
    for k in DEFAULT_FILESTORE_CONFIG:
        if k in patch:
            current[k] = patch[k]
    current["max_file_mb"] = max(1, min(4000, int(current.get("max_file_mb") or 2000)))
    current["page_size"] = max(5, min(100, int(current.get("page_size") or 25)))
    current["updated_at"] = db_models.utcnow_iso()
    current["updated_ts"] = time.time()
    await db_models.filestore_cfg_col().update_one(
        {"workspace_id": workspace_id, "bot_id": str(bot_id)},
        {"$set": current},
        upsert=True,
    )
    await models_db.log_audit(actor_id, "filestore_config_update", target=f"{workspace_id}:{bot_id}")
    return current


def workspace_file_prefix(workspace_id: str) -> str:
    """Return a filesystem-safe namespace, distinct from legacy user-wide files."""
    digest = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()
    return f"_saas/{digest}/"


def workspace_storage_user_dir(owner_id: int) -> str:
    # Deliberately separate from the legacy user-wide download file manager.
    import config
    base = os.path.realpath(os.path.join(getattr(config, "DOWNLOAD_DIR", "download") or "download", "saas_files"))
    user_dir = os.path.join(base, f"u_{int(owner_id)}")
    if os.path.realpath(user_dir) != user_dir:
        raise PermissionError("Invalid File Store storage directory.")
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


def resolve_workspace_file(workspace_id: str, owner_id: int, path: str, *, must_exist: bool = True) -> str:
    require_workspace_file_path(workspace_id, owner_id, path)
    candidate = os.path.join(workspace_storage_user_dir(owner_id), *path.split("/"))
    if must_exist and not os.path.isfile(candidate):
        raise FileNotFoundError("File Store file not found.")
    return candidate


def require_workspace_file_path(workspace_id: str, owner_id: int, path: str) -> None:
    # Legacy user-wide files cannot safely be attributed to a workspace.
    # Reject rather than guessing and potentially deleting another workspace's data.
    parts = path.replace("\\", "/").split("/")
    if ("\\" in path or any(part in ("", ".", "..") for part in parts)
            or not path.startswith(workspace_file_prefix(workspace_id))):
        raise services_bots.SaaSValidationError(
            "This legacy file is not in workspace-isolated storage; physical operations require migration."
        )
    base = workspace_storage_user_dir(owner_id)
    root = os.path.join(base, *workspace_file_prefix(workspace_id).strip("/").split("/"))
    full = os.path.join(base, *parts)
    if os.path.realpath(root) != root or not os.path.realpath(full).startswith(root + os.sep):
        raise PermissionError("File path escapes its workspace storage namespace.")


async def sync_disk_and_clone_files(workspace_id: str, user_id: int, bot_id: str = "default") -> None:
    """Do not automatically index user-wide disk files into arbitrary workspaces."""
    return None


async def register_filestore_entry(
    workspace_id: str,
    owner_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    fname = providers.sanitize_filename(str(payload.get("filename") or "file.bin"))
    bot_id = str(payload.get("bot_id") or "default")
    if bot_id not in ("default", "all", ""):
        await services_bots.get_owned_workspace_bot(workspace_id, bot_id)
    if bot_id == "all":
        raise services_bots.SaaSValidationError("Choose a specific bot or default File Store.")
    fs_cfg = await get_filestore_config(workspace_id, bot_id)
    size_bytes = max(0, int(payload.get("size_bytes") or 0))
    if size_bytes > int(fs_cfg["max_file_mb"]) * 1024 * 1024:
        raise services_bots.SaaSValidationError(
            f"File exceeds configured File Store limit ({fs_cfg['max_file_mb']} MiB)."
        )
    mime = str(payload.get("mime_type") or mimetypes.guess_type(fname)[0] or "application/octet-stream")
    cat = str(payload.get("category") or providers.category_for_filename(fname, mime))
    if fs_cfg.get("allowed_types") and cat not in fs_cfg["allowed_types"]:
        raise services_bots.SaaSValidationError(f"File category '{cat}' is not permitted by File Store policy.")

    sha256 = str(payload.get("sha256") or "").strip().lower()
    if fs_cfg.get("duplicate_policy") == "block" and sha256:
        dup = await db_models.files_col().find_one(
            {"workspace_id": workspace_id, "sha256": sha256, "deleted": False}
        )
        if dup:
            raise services_bots.SaaSValidationError(f"Duplicate file blocked ('{dup.get('filename')}' matches SHA-256).")

    now = time.time()
    doc = {
        "file_uid": f"fsf_{secrets.token_hex(6)}",
        "workspace_id": workspace_id,
        "bot_id": bot_id,
        "owner_id": int(owner_id),
        "filename": fname,
        "local_rel_path": str(payload.get("local_rel_path") or ""),
        "size_bytes": size_bytes,
        "mime_type": mime[:80],
        "category": cat,
        "tags": [str(t).strip()[:24] for t in (payload.get("tags") or []) if str(t).strip()][:12],
        "description": str(payload.get("description") or "")[:300],
        "sha256": sha256,
        "destination": str(payload.get("destination") or fs_cfg.get("destination") or "local"),
        "share_code": "",
        "access_count": 0,
        "deleted": False,
        "uploaded_at": db_models.utcnow_iso(),
        "uploaded_ts": now,
    }
    await db_models.files_col().insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def query_filestore_files(
    workspace_id: str,
    *,
    user_id: int,
    bot_id: str = "",
    search: str = "",
    category: str = "",
    mime_filter: str = "",
    tag: str = "",
    min_bytes: int = 0,
    max_bytes: int = 0,
    include_deleted: bool = False,
    sort_by: str = "uploaded_ts",
    sort_dir: str = "desc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    await sync_disk_and_clone_files(workspace_id, user_id, bot_id or "default")
    q: dict[str, Any] = {"workspace_id": workspace_id}
    if not include_deleted:
        q["deleted"] = False
    if bot_id and bot_id != "all":
        q["bot_id"] = str(bot_id)
    if category and category != "all":
        q["category"] = category

    rows = await db_models.files_col().find(q).to_list(length=2000)
    needle = (search or "").strip().lower()
    mf = (mime_filter or "").strip().lower()
    tf = (tag or "").strip().lower()

    filtered: list[dict[str, Any]] = []
    for r in rows:
        r.pop("_id", None)
        sz = int(r.get("size_bytes") or 0)
        if min_bytes and sz < min_bytes:
            continue
        if max_bytes and sz > max_bytes:
            continue
        if mf and mf not in str(r.get("mime_type") or "").lower():
            continue
        if tf and tf not in [t.lower() for t in (r.get("tags") or [])]:
            continue
        if needle:
            hay = f"{r.get('filename', '')} {r.get('file_uid', '')} {r.get('description', '')} {' '.join(r.get('tags') or [])}".lower()
            if needle not in hay:
                continue
        filtered.append(r)

    s_field = sort_by if sort_by in ("uploaded_ts", "size_bytes", "filename", "access_count") else "uploaded_ts"
    filtered.sort(key=lambda x: x.get(s_field) or 0, reverse=(sort_dir != "asc"))

    ps = max(1, min(100, int(page_size)))
    total = len(filtered)
    pages = max(1, (total + ps - 1) // ps)
    pg = max(1, min(pages, int(page)))
    start = (pg - 1) * ps
    return {
        "total": total,
        "page": pg,
        "pages": pages,
        "page_size": ps,
        "total_bytes": sum(int(x.get("size_bytes") or 0) for x in filtered),
        "items": filtered[start : start + ps],
    }


async def operate_filestore_file(
    workspace_id: str,
    file_uid: str,
    actor_id: int,
    *,
    action: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    fdoc = await db_models.files_col().find_one({"file_uid": str(file_uid), "workspace_id": workspace_id})
    if not fdoc:
        raise KeyError("File not found in this workspace.")

    act = action.strip().lower()
    now = time.time()

    if act in ("rename", "purge") and fdoc.get("local_rel_path"):
        aliases = await db_models.files_col().count_documents({
            "owner_id": fdoc["owner_id"], "local_rel_path": fdoc["local_rel_path"],
            "file_uid": {"$ne": fdoc["file_uid"]},
        })
        if aliases:
            raise services_bots.SaaSValidationError("Shared physical paths require migration before rename or purge.")

    if act == "rename":
        new_name = providers.sanitize_filename(str(payload.get("new_name") or ""))
        if fdoc.get("local_rel_path"):
            owner_id = int(fdoc["owner_id"])
            src = resolve_workspace_file(workspace_id, owner_id, fdoc["local_rel_path"])
            new_path = fdoc["local_rel_path"].rsplit("/", 1)[0] + "/" + new_name
            dst = resolve_workspace_file(workspace_id, owner_id, new_path, must_exist=False)
            if os.path.exists(dst) and dst != src:
                raise FileExistsError("A file with that name already exists.")
            os.replace(src, dst)
            await db_models.files_col().update_one(
                {"file_uid": file_uid, "workspace_id": workspace_id},
                {"$set": {"filename": new_name, "local_rel_path": new_path, "updated_ts": now}},
            )
        else:
            await db_models.files_col().update_one({"file_uid": file_uid}, {"$set": {"filename": new_name, "updated_ts": now}})

    elif act == "update_metadata":
        upd: dict[str, Any] = {"updated_ts": now}
        if "description" in payload:
            upd["description"] = str(payload["description"])[:300]
        if "category" in payload:
            upd["category"] = str(payload["category"])[:32]
        if "tags" in payload and isinstance(payload["tags"], list):
            upd["tags"] = [str(t).strip()[:24] for t in payload["tags"] if str(t).strip()][:12]
        await db_models.files_col().update_one({"file_uid": file_uid}, {"$set": upd})

    elif act == "share_link":
        raise services_bots.SaaSValidationError("Web File Store sharing is not implemented.")

    elif act == "copy":
        raise services_bots.SaaSValidationError("Copying stored files is not implemented safely yet.")

    elif act == "move":
        target_bot = str(payload.get("target_bot_id") or "default")
        if target_bot != "default":
            await services_bots.get_owned_workspace_bot(workspace_id, target_bot)
        await db_models.files_col().update_one(
            {"file_uid": file_uid}, {"$set": {"bot_id": target_bot, "updated_ts": now}}
        )

    elif act == "soft_delete":
        if not payload.get("confirmed"):
            raise services_bots.SaaSValidationError("Deletion requires explicit confirmation (`confirmed: true`).")
        await db_models.files_col().update_one(
            {"file_uid": file_uid},
            {"$set": {"deleted": True, "deleted_at": db_models.utcnow_iso(), "updated_ts": now}},
        )

    elif act == "restore":
        await db_models.files_col().update_one(
            {"file_uid": file_uid},
            {"$set": {"deleted": False, "updated_ts": now}, "$unset": {"deleted_at": ""}},
        )

    elif act == "purge":
        if not payload.get("confirmed"):
            raise services_bots.SaaSValidationError("Permanent deletion requires explicit confirmation.")
        if not fdoc.get("deleted"):
            raise services_bots.SaaSValidationError("Move the file to trash before permanent deletion.")
        if fdoc.get("local_rel_path"):
            target = resolve_workspace_file(workspace_id, int(fdoc["owner_id"]), fdoc["local_rel_path"])
            # Do not discard the record if deleting the physical file fails.
            os.remove(target)
        await db_models.files_col().delete_one({"file_uid": file_uid})
        await models_db.log_audit(actor_id, "filestore_purge", target=f"{workspace_id}:{file_uid}")
        return {"action": "purge", "deleted": True, "file_uid": file_uid}
    else:
        raise services_bots.SaaSValidationError(f"Unsupported file action '{act}'.")

    fresh = await db_models.files_col().find_one({"file_uid": file_uid})
    fresh.pop("_id", None)
    return {"action": act, "file": fresh}


async def compute_file_intelligence(workspace_id: str, user_id: int) -> dict[str, Any]:
    """Compute accurate File Intelligence insights strictly from collected records (zero auto-delete!)."""
    await sync_disk_and_clone_files(workspace_id, user_id)
    query = {"workspace_id": workspace_id, "deleted": False}
    files = await db_models.files_col().find(query).to_list(length=5000)
    truncated = await db_models.files_col().count_documents(query) > len(files)
    for f in files:
        f.pop("_id", None)

    total_bytes = sum(int(f.get("size_bytes") or 0) for f in files)
    largest = sorted(files, key=lambda x: int(x.get("size_bytes") or 0), reverse=True)[:10]
    recent = sorted(files, key=lambda x: float(x.get("uploaded_ts") or 0), reverse=True)[:10]
    # Access counts are not instrumented, so popularity and unused status are unknown.

    # Duplicate candidates by SHA-256 (when known) or exact (lowercase filename, non-zero byte size)
    groups: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        sz = int(f.get("size_bytes") or 0)
        if sz <= 0:
            continue
        key = f"sha:{f['sha256']}" if f.get("sha256") else f"ns:{f.get('filename', '').lower()}:{sz}"
        groups.setdefault(key, []).append(f)

    duplicate_groups: list[dict[str, Any]] = []
    reclaimable_bytes = 0
    for gk, members in groups.items():
        if len(members) > 1:
            unit_sz = int(members[0].get("size_bytes") or 0)
            wasted = unit_sz * (len(members) - 1)
            reclaimable_bytes += wasted
            duplicate_groups.append({
                "match_basis": "sha256" if gk.startswith("sha:") else "filename_and_size",
                "count": len(members),
                "reclaimable_bytes": wasted,
                "files": [{"file_uid": m["file_uid"], "filename": m["filename"], "size_bytes": m["size_bytes"]} for m in members],
            })

    by_category: dict[str, dict[str, int]] = {}
    by_destination: dict[str, int] = {}
    missing_metadata: list[dict[str, Any]] = []
    for f in files:
        cat = f.get("category") or "other"
        slot = by_category.setdefault(cat, {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += int(f.get("size_bytes") or 0)

        dest = f.get("destination") or "local"
        by_destination[dest] = by_destination.get(dest, 0) + 1

        if not f.get("description") and not f.get("tags"):
            missing_metadata.append({"file_uid": f["file_uid"], "filename": f["filename"]})
    # Growth rate over the last 7 days (strictly observed uploads, never fabricated)
    cutoff_7d = time.time() - 7 * 86400
    bytes_last_7d = sum(int(f.get("size_bytes") or 0) for f in files if float(f.get("uploaded_ts") or 0) >= cutoff_7d)
    projected_30d_bytes = int((bytes_last_7d / 7.0) * 30.0) if bytes_last_7d > 0 else 0

    return {
        "total_files": len(files),
        "total_bytes": total_bytes,
        "largest_files": largest,
        "recent_files": recent,
        "popular_files": None,
        "duplicate_candidates": duplicate_groups,
        "duplicate_reclaimable_bytes": reclaimable_bytes,
        "type_distribution": by_category,
        "destination_distribution": by_destination,
        "missing_metadata_count": len(missing_metadata),
        "missing_metadata": missing_metadata[:25],
        "integrity_issues": None,
        "unused_files_count": None,
        "unused_files": None,
        "unavailable_metrics": ["file_access", "integrity"],
        "sample_truncated": truncated,
        "bytes_added_7d": bytes_last_7d,
        "projected_30d_growth_bytes": projected_30d_bytes,
        "auto_cleanup_note": "Recommendations are advisory only; Videl never deletes files automatically from estimates.",
    }


async def compute_owner_and_bot_analytics(
    workspace_id: str,
    user_id: int,
    *,
    bot_id: str = "",
    days: int = 30,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """Compute private Owner & Per-Bot Analytics with period comparison and honest availability flags."""
    now_dt = datetime.now(timezone.utc)
    if start_date and end_date:
        try:
            s_dt = datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc)
            e_dt = datetime.fromisoformat(end_date).replace(tzinfo=timezone.utc) + timedelta(days=1)
            span_days = max(1, min(365, (e_dt - s_dt).days))
        except ValueError:
            span_days = max(1, min(180, int(days)))
            e_dt = now_dt
            s_dt = e_dt - timedelta(days=span_days)
    else:
        span_days = max(1, min(180, int(days)))
        e_dt = now_dt
        s_dt = e_dt - timedelta(days=span_days)

    prev_s_dt = s_dt - timedelta(days=span_days)
    s_ts, e_ts, prev_s_ts = s_dt.timestamp(), e_dt.timestamp(), prev_s_dt.timestamp()

    file_q: dict[str, Any] = {"workspace_id": workspace_id, "deleted": False}
    if bot_id and bot_id != "all":
        if bot_id != "default":
            await services_bots.get_owned_workspace_bot(workspace_id, bot_id)
        file_q["bot_id"] = str(bot_id)
    files = await db_models.files_col().find(file_q).to_list(length=5000)
    file_count = await db_models.files_col().count_documents(file_q)

    # Build daily buckets
    day_labels = [
        (s_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(span_days)
    ]
    uploads_by_day = {d: 0 for d in day_labels}
    bytes_by_day = {d: 0 for d in day_labels}

    cur_files = prev_files = 0
    for f in files:
        ts = float(f.get("uploaded_ts") or 0)
        if s_ts <= ts <= e_ts:
            cur_files += 1
            dk = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            if dk in uploads_by_day:
                uploads_by_day[dk] += 1
                bytes_by_day[dk] += int(f.get("size_bytes") or 0)
        elif prev_s_ts <= ts < s_ts:
            prev_files += 1

    # Legacy transfer histories and command counters are not workspace-scoped
    # or comprehensively instrumented. Never attribute those values here.

    return {
        "range_days": span_days,
        "bot_id": bot_id or "all",
        "totals": {
            "stored_files": file_count,
            "storage_bytes": sum(int(f.get("size_bytes") or 0) for f in files),
            "period_uploads": cur_files,
            "period_downloads": None,
            "failed_uploads": None,
            "failed_downloads": None,
            "telegram_impressions": None,  # Explicitly None ("unavailable"), never fabricated as 0!
        },
        "comparison_previous_period": {
            "files_delta": cur_files - prev_files,
            "downloads_delta": None,
        },
        "series": {
            "days": day_labels,
            "uploads": [uploads_by_day[d] for d in day_labels],
            "downloads": None,
            "storage_bytes_added": [bytes_by_day[d] for d in day_labels],
        },
        "command_usage": None,
        "unavailable_metrics": ["telegram_impressions", "downloads", "failed_uploads", "failed_downloads", "command_usage"],
        "file_sample_truncated": file_count > len(files),
        "data_availability": {
            "telegram_global_reach": "unavailable (Telegram Bot API does not expose passive channel impressions)",
            "workspace_operations": "transfer history unavailable: legacy records are not workspace-scoped",
            "file_metrics": "sampled at 5000 active records" if file_count > len(files) else "active indexed File Store records only",
        },
    }


def export_analytics_csv(analytics_data: dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "uploads", "downloads", "storage_bytes_added"])
    s = analytics_data.get("series") or {}
    days = s.get("days") or []
    ups = s.get("uploads") or []
    dls = s.get("downloads") or []
    bts = s.get("storage_bytes_added") or []
    for i, d in enumerate(days):
        writer.writerow([d, ups[i] if i < len(ups) else 0, dls[i] if i < len(dls) else "", bts[i] if i < len(bts) else 0])
    return buf.getvalue()

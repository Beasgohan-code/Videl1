"""
Bot Clone Studio, Configuration Migration Center, File Store Studio,
File Intelligence Center & Bot/Owner Analytics (`VidelBot/webapp/services_clone_files.py`).
"""
from __future__ import annotations

import copy
import csv
import io
import mimetypes
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from platform_core import destinations, models_db, providers
from webapp import db_models, services_bots

CLONEABLE_SECTIONS = (
    "profile",
    "commands",
    "features",
    "filestore",
    "welcome",
    "ui_preferences",
    "notifications",
    "permissions_template",
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
            "permissions_template": {
                "default_command_scope": "everyone",
            },
            "plugin_settings": db_models.strip_sensitive_fields(bot.get("plugin_settings") or {}),
            "automations": [
                {
                    "name": a.get("name", ""),
                    "trigger": a.get("trigger", ""),
                    "conditions": a.get("conditions") or [],
                    "actions": a.get("actions") or [],
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

    chosen = [s for s in (sections or list(CLONEABLE_SECTIONS)) if s in CLONEABLE_SECTIONS]
    if not chosen:
        raise services_bots.SaaSValidationError("Select at least one configuration section to clone.")

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
        "credentials_stripped": True,
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
    tgt_snapshot = await extract_cloneable_bundle(workspace_id, target_bot_id)

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
    upd_bot["settings"] = db_models.strip_sensitive_fields(next_settings)
    upd_bot["config_version"] = int(tgt_raw.get("config_version") or 1) + 1
    await db_models.bots_col().update_one({"bot_id": target_bot_id}, {"$set": upd_bot})

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
        "credentials_cloned": False,
        "rollback_snapshot": tgt_snapshot["sections"],
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

    snap = rec.get("rollback_snapshot") or {}
    target_bot_id = rec["target_bot_id"]
    tgt = await services_bots.get_owned_workspace_bot(workspace_id, target_bot_id)
    restored_settings = dict(tgt.get("settings") or {})
    if "welcome" in snap:
        restored_settings["welcome_message"] = snap["welcome"].get("welcome_message", "")
    if "text_templates" in snap:
        restored_settings["custom_caption"] = snap["text_templates"].get("custom_caption", "")

    upd: dict[str, Any] = {
        "features": snap.get("features") or tgt.get("features") or {},
        "settings": restored_settings,
        "updated_ts": time.time(),
    }
    if "profile" in snap:
        upd.update(snap["profile"])
    await db_models.bots_col().update_one({"bot_id": target_bot_id}, {"$set": upd})
    if "filestore" in snap:
        await update_filestore_config(workspace_id, target_bot_id, actor_id, snap["filestore"])

    await db_models.clones_col().update_one(
        {"clone_id": str(clone_id)},
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


async def sync_disk_and_clone_files(workspace_id: str, user_id: int, bot_id: str = "default") -> None:
    """Import real files from user's workspace disk directory into `saas_files` metadata index idempotently."""
    ws = await db_models.workspaces_col().find_one({"workspace_id": workspace_id})
    if not ws or int(ws.get("owner_id", 0)) != int(user_id) or ws.get("is_demo"):
        return
    disk_listing = destinations.list_user_files(user_id)
    for f in disk_listing["files"]:
        fuid = f"disk_{user_id}_{f['path']}"
        exists = await db_models.files_col().find_one(
            {"$or": [{"file_uid": fuid}, {"workspace_id": workspace_id, "local_rel_path": f["path"]}]}
        )
        if exists:
            continue
        mime = mimetypes.guess_type(f["name"])[0] or "application/octet-stream"
        await db_models.files_col().insert_one({
            "file_uid": fuid,
            "workspace_id": workspace_id,
            "bot_id": str(bot_id or "default"),
            "owner_id": int(user_id),
            "filename": f["name"],
            "local_rel_path": f["path"],
            "size_bytes": int(f["size_bytes"]),
            "mime_type": mime,
            "category": f["category"],
            "tags": [],
            "description": "",
            "sha256": "",
            "destination": "local",
            "share_code": "",
            "access_count": 0,
            "deleted": False,
            "uploaded_at": db_models.utcnow_iso(),
            "uploaded_ts": float(f["modified_ts"]),
        })


async def register_filestore_entry(
    workspace_id: str,
    owner_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    fname = providers.sanitize_filename(str(payload.get("filename") or "file.bin"))
    bot_id = str(payload.get("bot_id") or "default")
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

    if act == "rename":
        new_name = providers.sanitize_filename(str(payload.get("new_name") or ""))
        if fdoc.get("local_rel_path"):
            try:
                res = destinations.rename_user_file(int(fdoc["owner_id"]), fdoc["local_rel_path"], new_name)
                await db_models.files_col().update_one(
                    {"file_uid": file_uid},
                    {"$set": {"filename": res["name"], "local_rel_path": res["path"], "updated_ts": now}},
                )
            except FileNotFoundError:
                await db_models.files_col().update_one({"file_uid": file_uid}, {"$set": {"filename": new_name, "updated_ts": now}})
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
        code = fdoc.get("share_code") or f"sh_{secrets.token_urlsafe(8)}"
        await db_models.files_col().update_one(
            {"file_uid": file_uid},
            {"$set": {"share_code": code, "updated_ts": now}},
        )

    elif act == "copy":
        target_bot = str(payload.get("target_bot_id") or fdoc.get("bot_id") or "default")
        cloned = dict(fdoc)
        cloned.pop("_id", None)
        cloned["file_uid"] = f"fsf_{secrets.token_hex(6)}"
        cloned["bot_id"] = target_bot
        cloned["filename"] = f"Copy of {fdoc['filename']}"[:190]
        cloned["uploaded_ts"] = now
        cloned["uploaded_at"] = db_models.utcnow_iso()
        await db_models.files_col().insert_one(cloned)
        cloned.pop("_id", None)
        return {"action": "copy", "file": cloned}

    elif act == "move":
        target_bot = str(payload.get("target_bot_id") or "default")
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
        if fdoc.get("local_rel_path"):
            try:
                destinations.delete_user_file(int(fdoc["owner_id"]), fdoc["local_rel_path"])
            except Exception:
                pass
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
    files = await db_models.files_col().find({"workspace_id": workspace_id, "deleted": False}).to_list(length=5000)
    for f in files:
        f.pop("_id", None)

    total_bytes = sum(int(f.get("size_bytes") or 0) for f in files)
    largest = sorted(files, key=lambda x: int(x.get("size_bytes") or 0), reverse=True)[:10]
    recent = sorted(files, key=lambda x: float(x.get("uploaded_ts") or 0), reverse=True)[:10]
    popular = sorted(files, key=lambda x: int(x.get("access_count") or 0), reverse=True)[:10]

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
    integrity_issues: list[dict[str, Any]] = []
    unused_files: list[dict[str, Any]] = []

    for f in files:
        cat = f.get("category") or "other"
        slot = by_category.setdefault(cat, {"count": 0, "bytes": 0})
        slot["count"] += 1
        slot["bytes"] += int(f.get("size_bytes") or 0)

        dest = f.get("destination") or "local"
        by_destination[dest] = by_destination.get(dest, 0) + 1

        if not f.get("description") and not f.get("tags"):
            missing_metadata.append({"file_uid": f["file_uid"], "filename": f["filename"]})
        if int(f.get("size_bytes") or 0) == 0:
            integrity_issues.append({"file_uid": f["file_uid"], "filename": f["filename"], "issue": "Zero-byte file"})
        if int(f.get("access_count") or 0) == 0:
            unused_files.append({"file_uid": f["file_uid"], "filename": f["filename"], "size_bytes": f.get("size_bytes", 0)})

    # Growth rate over the last 7 days (strictly observed uploads, never fabricated)
    cutoff_7d = time.time() - 7 * 86400
    bytes_last_7d = sum(int(f.get("size_bytes") or 0) for f in files if float(f.get("uploaded_ts") or 0) >= cutoff_7d)
    projected_30d_bytes = int((bytes_last_7d / 7.0) * 30.0) if bytes_last_7d > 0 else 0

    return {
        "total_files": len(files),
        "total_bytes": total_bytes,
        "largest_files": largest,
        "recent_files": recent,
        "popular_files": popular,
        "duplicate_candidates": duplicate_groups,
        "duplicate_reclaimable_bytes": reclaimable_bytes,
        "type_distribution": by_category,
        "destination_distribution": by_destination,
        "missing_metadata_count": len(missing_metadata),
        "missing_metadata": missing_metadata[:25],
        "integrity_issues": integrity_issues[:25],
        "unused_files_count": len(unused_files),
        "unused_files": unused_files[:25],
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
        file_q["bot_id"] = str(bot_id)
    files = await db_models.files_col().find(file_q).to_list(length=5000)

    dl_hist = await models_db.query_history("download", user_id=user_id, limit=200)
    ul_hist = await models_db.query_history("upload", user_id=user_id, limit=200)

    # Build daily buckets
    day_labels = [
        (s_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        for i in range(span_days)
    ]
    uploads_by_day = {d: 0 for d in day_labels}
    downloads_by_day = {d: 0 for d in day_labels}
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

    cur_dl_ok = cur_dl_fail = prev_dl_ok = 0
    for d in dl_hist["items"]:
        ts = float(d.get("created_ts") or 0)
        if s_ts <= ts <= e_ts:
            if d.get("state") == "completed":
                cur_dl_ok += 1
                dk = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                if dk in downloads_by_day:
                    downloads_by_day[dk] += 1
            elif d.get("state") == "failed":
                cur_dl_fail += 1
        elif prev_s_ts <= ts < s_ts and d.get("state") == "completed":
            prev_dl_ok += 1

    cur_ul_ok = sum(1 for u in ul_hist["items"] if s_ts <= float(u.get("created_ts") or 0) <= e_ts and u.get("state") == "completed")
    cur_ul_fail = sum(1 for u in ul_hist["items"] if s_ts <= float(u.get("created_ts") or 0) <= e_ts and u.get("state") == "failed")

    cmds: list[dict[str, Any]] = []
    if bot_id and bot_id != "all":
        try:
            cmds = await services_bots.list_bot_commands(workspace_id, bot_id)
        except Exception:
            cmds = []

    return {
        "range_days": span_days,
        "bot_id": bot_id or "all",
        "totals": {
            "stored_files": len(files),
            "storage_bytes": sum(int(f.get("size_bytes") or 0) for f in files),
            "period_uploads": cur_files + cur_ul_ok,
            "period_downloads": cur_dl_ok,
            "failed_uploads": cur_ul_fail,
            "failed_downloads": cur_dl_fail,
            "telegram_impressions": None,  # Explicitly None ("unavailable"), never fabricated as 0!
        },
        "comparison_previous_period": {
            "files_delta": cur_files - prev_files,
            "downloads_delta": cur_dl_ok - prev_dl_ok,
        },
        "series": {
            "days": day_labels,
            "uploads": [uploads_by_day[d] for d in day_labels],
            "downloads": [downloads_by_day[d] for d in day_labels],
            "storage_bytes_added": [bytes_by_day[d] for d in day_labels],
        },
        "command_usage": [
            {"command": c["command"], "usage_count": int(c.get("usage_count") or 0)}
            for c in sorted(cmds, key=lambda x: int(x.get("usage_count") or 0), reverse=True)[:10]
        ],
        "unavailable_metrics": ["telegram_impressions"],
        "data_availability": {
            "telegram_global_reach": "unavailable (Telegram Bot API does not expose passive channel impressions)",
            "workspace_operations": "measured",
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
        writer.writerow([d, ups[i] if i < len(ups) else 0, dls[i] if i < len(dls) else 0, bts[i] if i < len(bts) else 0])
    return buf.getvalue()

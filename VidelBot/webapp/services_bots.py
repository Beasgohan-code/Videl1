"""
Workspace, Team RBAC, Bot Management, Creation Wizard, Fleet, Config Revisions,
Environments & Command Studio Services (`VidelBot/webapp/services_bots.py`).
"""
from __future__ import annotations

import copy
import re
import secrets
import time
from typing import Any

from filestore.utils import security
from platform_core import models_db
from webapp import db_models

TOKEN_FORMAT_RE = re.compile(r"^(\d{5,15}):([A-Za-z0-9_-]{25,80})$")

BOT_TEMPLATES_PRESETS: dict[str, dict[str, Any]] = {
    "filestore": {
        "name": "File Store Bot",
        "category": "File Store",
        "description": "Dedicated file vault, batch share links, force-subscribe gates, and auto-delete timers.",
        "features": {
            "filestore_enabled": True,
            "batch_links": True,
            "permanent_link": False,
            "force_subscribe": True,
            "force_sub_request_mode": False,
            "auto_delete": True,
            "protect_content": False,
            "media_encoder": False,
            "auto_watermark": False,
            "group_moderation": False,
        },
        "settings": {
            "auto_delete_seconds": 600,
            "max_file_mb": 2000,
            "welcome_message": "Welcome to {bot_name}! Send or open a share link to access files.",
            "custom_caption": "{filename} · {filesize}",
            "notify_on_error": True,
            "notify_on_upload": True,
        },
    },
    "utility": {
        "name": "Multi-Utility Assistant",
        "category": "Utility",
        "description": "Fast everyday tools: QR generator, URL shortener, MediaInfo inspector, and checklists.",
        "features": {
            "filestore_enabled": False,
            "batch_links": False,
            "permanent_link": False,
            "force_subscribe": False,
            "force_sub_request_mode": False,
            "auto_delete": False,
            "protect_content": False,
            "media_encoder": False,
            "auto_watermark": False,
            "group_moderation": False,
        },
        "settings": {
            "auto_delete_seconds": 0,
            "max_file_mb": 512,
            "welcome_message": "Hello! Use /help to explore utility commands.",
            "custom_caption": "{filename}",
            "notify_on_error": True,
            "notify_on_upload": False,
        },
    },
    "group_management": {
        "name": "Group Moderation Bot",
        "category": "Group Management",
        "description": "Anti-raid protection, member tags, join-mode approvals, warnings, locks, and topic tools.",
        "features": {
            "filestore_enabled": False,
            "batch_links": False,
            "permanent_link": False,
            "force_subscribe": False,
            "force_sub_request_mode": False,
            "auto_delete": False,
            "protect_content": False,
            "media_encoder": False,
            "auto_watermark": False,
            "group_moderation": True,
        },
        "settings": {
            "auto_delete_seconds": 0,
            "max_file_mb": 256,
            "welcome_message": "Welcome {mention} to {chat}! Read /rules.",
            "custom_caption": "",
            "notify_on_error": True,
            "notify_on_upload": False,
        },
    },
    "media_suite": {
        "name": "Media & File Processing Bot",
        "category": "Media/File Management",
        "description": "Video compression, audio conversion, subtitle muxing, auto-rename, and cloud delivery.",
        "features": {
            "filestore_enabled": True,
            "batch_links": True,
            "permanent_link": False,
            "force_subscribe": False,
            "force_sub_request_mode": False,
            "auto_delete": False,
            "protect_content": False,
            "media_encoder": True,
            "auto_watermark": False,
            "group_moderation": False,
        },
        "settings": {
            "auto_delete_seconds": 0,
            "max_file_mb": 2048,
            "welcome_message": "Send media or direct links to process and deliver.",
            "custom_caption": "{filename}",
            "notify_on_error": True,
            "notify_on_upload": True,
        },
    },
    "custom": {
        "name": "Custom Blank Bot",
        "category": "Custom",
        "description": "Start with minimal defaults and enable only the studios and commands you need.",
        "features": {
            "filestore_enabled": False,
            "batch_links": False,
            "permanent_link": False,
            "force_subscribe": False,
            "force_sub_request_mode": False,
            "auto_delete": False,
            "protect_content": False,
            "media_encoder": False,
            "auto_watermark": False,
            "group_moderation": False,
        },
        "settings": {
            "auto_delete_seconds": 0,
            "max_file_mb": 1024,
            "welcome_message": "Hello from {bot_name}!",
            "custom_caption": "",
            "notify_on_error": True,
            "notify_on_upload": False,
        },
    },
}

# Feature dependency rules (Section 9)
FEATURE_DEPENDENCIES: dict[str, tuple[str, str]] = {
    "batch_links": ("filestore_enabled", "Batch Share Links requires File Store to be enabled first."),
    "permanent_link": ("filestore_enabled", "Permanent Cloud Links requires File Store to be enabled first."),
    "force_sub_request_mode": ("force_subscribe", "Join-Request Force-Sub requires Force Subscribe to be enabled first."),
    "auto_watermark": ("media_encoder", "Automatic Watermarking requires Media Encoder to be enabled first."),
}

COMMAND_CATEGORIES = (
    "General",
    "File Store",
    "Administration",
    "Media",
    "Group Management",
    "Utilities",
    "Owner Tools",
    "Custom",
)

COMMAND_PERMISSIONS = (
    "everyone",
    "owner",
    "administrators",
    "authorized_users",
    "custom_role",
)


class SaaSValidationError(ValueError):
    """Raised when input, dependency, or ownership checks fail."""


def validate_feature_dependencies(features: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for child, (parent, explanation) in FEATURE_DEPENDENCIES.items():
        if features.get(child) and not features.get(parent):
            errors.append(explanation)
    return errors


def compute_config_diff(left: dict[str, Any], right: dict[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    """Compute structured field-by-field diff between two sanitized configurations."""
    diffs: list[dict[str, Any]] = []
    keys = sorted(set(left.keys()) | set(right.keys()))
    for k in keys:
        path = f"{prefix}.{k}" if prefix else str(k)
        lv, rv = left.get(k), right.get(k)
        if isinstance(lv, dict) and isinstance(rv, dict):
            diffs.extend(compute_config_diff(lv, rv, path))
        elif lv != rv:
            diffs.append({
                "field": path,
                "before": lv,
                "after": rv,
                "conflict": lv is not None and rv is not None and lv != rv,
            })
    return diffs


# ─────────────────────────── Workspace & Team Operations (Sections 4 & 22) ───────────────────────────
async def create_workspace(
    owner_id: int,
    *,
    name: str,
    icon: str = "⚡",
    description: str = "",
    is_demo: bool = False,
) -> dict[str, Any]:
    clean_name = (name or "").strip()
    if len(clean_name) < 2 or len(clean_name) > 80:
        raise SaaSValidationError("Workspace name must be between 2 and 80 characters.")
    uid = int(owner_id)
    existing = await db_models.memberships_col().count_documents({"user_id": uid, "role": "owner"})
    if existing >= 20:
        raise SaaSValidationError("Maximum of 20 owned workspaces reached.")
    ws_id = f"ws_{secrets.token_hex(6)}"
    now = time.time()
    doc = {
        "workspace_id": ws_id,
        "name": clean_name,
        "icon": (icon or "⚡").strip()[:8],
        "description": (description or "").strip()[:240],
        "owner_id": uid,
        "is_demo": bool(is_demo),
        "archived": False,
        "deleted": False,
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
        "updated_ts": now,
    }
    await db_models.workspaces_col().insert_one(dict(doc))
    await db_models.memberships_col().insert_one({
        "workspace_id": ws_id,
        "user_id": uid,
        "role": "owner",
        "permissions": dict(db_models.DEFAULT_ROLE_PERMISSIONS["owner"]),
        "joined_at": db_models.utcnow_iso(),
        "joined_ts": now,
    })
    await models_db.log_audit(uid, "workspace_create", actor_role="owner", target=ws_id, detail=clean_name)
    return doc


async def update_workspace(
    workspace_id: str,
    actor_id: int,
    *,
    name: str | None = None,
    icon: str | None = None,
    description: str | None = None,
    archived: bool | None = None,
) -> dict[str, Any]:
    upd: dict[str, Any] = {"updated_ts": time.time()}
    if name is not None:
        clean = name.strip()
        if len(clean) < 2 or len(clean) > 80:
            raise SaaSValidationError("Workspace name must be 2–80 characters.")
        upd["name"] = clean
    if icon is not None:
        upd["icon"] = icon.strip()[:8] or "🚀"
    if description is not None:
        upd["description"] = description.strip()[:240]
    if archived is not None:
        upd["archived"] = bool(archived)
    await db_models.workspaces_col().update_one({"workspace_id": workspace_id}, {"$set": upd})
    ws = await db_models.workspaces_col().find_one({"workspace_id": workspace_id})
    if not ws:
        raise KeyError("Workspace not found.")
    ws.pop("_id", None)
    await models_db.log_audit(actor_id, "workspace_update", target=workspace_id)
    return ws


async def delete_workspace(workspace_id: str, actor_id: int, *, confirm_name: str) -> bool:
    ws = await db_models.workspaces_col().find_one({"workspace_id": workspace_id, "deleted": {"$ne": True}})
    if not ws:
        raise KeyError("Workspace not found.")
    if int(ws["owner_id"]) != int(actor_id):
        raise PermissionError("Only the workspace owner can delete a workspace.")
    if (confirm_name or "").strip() != ws["name"]:
        raise SaaSValidationError("Type the exact workspace name to confirm deletion.")
    owned_count = await db_models.workspaces_col().count_documents(
        {"owner_id": int(actor_id), "deleted": {"$ne": True}}
    )
    if owned_count <= 1:
        raise SaaSValidationError("Cannot delete your only remaining workspace.")
    await db_models.workspaces_col().update_one(
        {"workspace_id": workspace_id},
        {"$set": {"deleted": True, "archived": True, "updated_ts": time.time()}},
    )
    await models_db.log_audit(actor_id, "workspace_delete", actor_role="owner", target=workspace_id)
    return True


async def list_workspace_team(workspace_id: str) -> dict[str, Any]:
    mems = await db_models.memberships_col().find({"workspace_id": workspace_id}).to_list(length=200)
    invites = await db_models.invites_col().find(
        {"workspace_id": workspace_id, "status": "pending"}
    ).to_list(length=100)
    for m in mems:
        m.pop("_id", None)
    for inv in invites:
        inv.pop("_id", None)
    return {"members": mems, "pending_invites": invites}


async def _validate_delegated_permissions(workspace_id: str, actor_id: int, actor_role: str, permissions: dict[str, bool]) -> None:
    """A member cannot grant a capability they do not themselves possess."""
    if actor_role == "owner":
        return
    actor = await db_models.get_membership(workspace_id, actor_id)
    if not actor:
        raise PermissionError("Workspace membership required.")
    actor_permissions = actor.get("permissions") or {}
    if any(enabled and not actor_permissions.get(scope, False) for scope, enabled in permissions.items()):
        raise PermissionError("Cannot delegate permissions you do not possess.")


def _validated_member_permissions(role: str, permissions: dict[str, bool] | None) -> dict[str, bool]:
    result = dict(db_models.DEFAULT_ROLE_PERMISSIONS[role])
    if permissions is None:
        return result
    if not isinstance(permissions, dict):
        raise SaaSValidationError("Permissions must be an object of boolean values.")
    for scope, value in permissions.items():
        if scope not in db_models.PERMISSION_SCOPES or not isinstance(value, bool):
            raise SaaSValidationError("Permissions must contain known scopes with boolean values.")
        result[scope] = value
    return result


async def invite_team_member(
    workspace_id: str,
    actor_id: int,
    actor_role: str,
    *,
    target_user_id: int,
    role: str = "viewer",
    permissions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    role = (role or "viewer").strip().lower()
    if role not in db_models.WORKSPACE_ROLES or role == "owner":
        raise SaaSValidationError("Invited role must be administrator, manager, developer, analyst, or viewer.")
    if db_models.ROLE_RANK[role] >= db_models.ROLE_RANK.get(actor_role, 0) and actor_role != "owner":
        raise PermissionError("Privilege escalation blocked: cannot grant a role equal to or higher than your own.")
    t_uid = int(target_user_id)
    if t_uid <= 0:
        raise SaaSValidationError("Provide a valid numeric Telegram user ID.")

    # An invitation must never overwrite an existing membership, especially the
    # owner or a peer. Role changes use the separate, rank-checked operation.
    existing = await db_models.memberships_col().find_one({"workspace_id": workspace_id, "user_id": t_uid})
    if existing:
        raise SaaSValidationError("User is already a workspace member; use the role management operation.")
    base_perms = _validated_member_permissions(role, permissions)
    await _validate_delegated_permissions(workspace_id, actor_id, actor_role, base_perms)

    now = time.time()
    inv_doc = {
        "invite_id": f"inv_{secrets.token_hex(6)}",
        "workspace_id": workspace_id,
        "target_user_id": t_uid,
        "invited_by": int(actor_id),
        "role": role,
        "permissions": base_perms,
        "status": "accepted",  # Immediately grant workspace membership & record invite trail
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
    }
    # A pre-check alone is unsafe: another request may insert a membership
    # before this one writes. Enforce uniqueness and never update an existing
    # row in this path, even if it appears after the pre-check.
    members = db_models.memberships_col()
    await members.create_index([("workspace_id", 1), ("user_id", 1)], unique=True)
    from pymongo.errors import DuplicateKeyError
    try:
        result = await members.update_one(
            {"workspace_id": workspace_id, "user_id": t_uid},
            {"$setOnInsert": {
                "workspace_id": workspace_id,
                "user_id": t_uid,
                "role": role,
                "permissions": base_perms,
                "invited_by": int(actor_id),
                "joined_at": db_models.utcnow_iso(),
                "joined_ts": now,
            }},
            upsert=True,
        )
    except DuplicateKeyError:
        raise SaaSValidationError("User is already a workspace member; use the role management operation.") from None
    if getattr(result, "upserted_id", None) is None:
        raise SaaSValidationError("User is already a workspace member; use the role management operation.")
    await db_models.invites_col().insert_one(dict(inv_doc))
    await models_db.log_audit(actor_id, "team_member_add", actor_role=actor_role, target=f"{workspace_id}:{t_uid}", detail=role)
    await db_models.create_notification(
        workspace_id,
        category="permission_change",
        title="Team member added",
        message=f"User {t_uid} was granted {role} access.",
        severity="info",
    )
    inv_doc.pop("_id", None)
    return inv_doc


async def update_member_role(
    workspace_id: str,
    actor_id: int,
    actor_role: str,
    *,
    target_user_id: int,
    role: str,
    permissions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    existing = await db_models.memberships_col().find_one(
        {"workspace_id": workspace_id, "user_id": int(target_user_id)}
    )
    if not existing:
        raise KeyError("Member not found in this workspace.")
    if existing.get("role") == "owner":
        raise PermissionError("Workspace owner role cannot be downgraded or altered.")
    new_role = (role or "").strip().lower()
    if new_role not in db_models.WORKSPACE_ROLES or new_role == "owner":
        raise SaaSValidationError("Invalid target role.")
    if actor_role != "owner":
        if db_models.ROLE_RANK[new_role] >= db_models.ROLE_RANK.get(actor_role, 0):
            raise PermissionError("Cannot promote a member to or above your own role.")
        if db_models.ROLE_RANK.get(existing.get("role", "viewer"), 0) >= db_models.ROLE_RANK.get(actor_role, 0):
            raise PermissionError("Cannot modify a peer or higher-ranked member.")

    perms = _validated_member_permissions(new_role, permissions)
    await _validate_delegated_permissions(workspace_id, actor_id, actor_role, perms)
    result = await db_models.memberships_col().update_one(
        {"workspace_id": workspace_id, "user_id": int(target_user_id), "role": existing.get("role")},
        {"$set": {"role": new_role, "permissions": perms, "updated_ts": time.time()}},
    )
    if not result.matched_count:
        raise SaaSValidationError("Member role changed concurrently; reload and try again.")
    await models_db.log_audit(
        actor_id, "team_role_change", actor_role=actor_role, target=f"{workspace_id}:{target_user_id}", detail=new_role
    )
    updated = await db_models.memberships_col().find_one(
        {"workspace_id": workspace_id, "user_id": int(target_user_id)}
    )
    updated.pop("_id", None)
    return updated


async def remove_team_member(
    workspace_id: str, actor_id: int, actor_role: str, *, target_user_id: int
) -> bool:
    existing = await db_models.memberships_col().find_one(
        {"workspace_id": workspace_id, "user_id": int(target_user_id)}
    )
    if not existing:
        return False
    if existing.get("role") == "owner":
        raise PermissionError("Cannot remove the workspace owner.")
    if actor_role != "owner" and db_models.ROLE_RANK.get(existing.get("role", "viewer"), 0) >= db_models.ROLE_RANK.get(actor_role, 0):
        raise PermissionError("Insufficient rank to remove this member.")
    result = await db_models.memberships_col().delete_one(
        {"workspace_id": workspace_id, "user_id": int(target_user_id), "role": existing.get("role")}
    )
    if not result.deleted_count:
        raise SaaSValidationError("Member role changed concurrently; reload and try again.")
    await models_db.log_audit(
        actor_id, "team_member_revoke", actor_role=actor_role, target=f"{workspace_id}:{target_user_id}"
    )
    return True


# ─────────────────────────── Bot Sync, Creation, Management & Fleet ───────────────────────────
def _live_running_bot_ids() -> set[int]:
    running: set[int] = set()
    try:
        from filestore.worker_bot.engine import worker_engine
        running.update(int(k) for k in worker_engine.workers.keys())
    except Exception:
        pass
    try:
        import client as bot_client
        me = getattr(getattr(bot_client, "bot", None), "me", None)
        if me and getattr(me, "id", None):
            running.add(int(me.id))
    except Exception:
        pass
    return running


def _public_bot_doc(doc: dict[str, Any], live_ids: set[int] | None = None) -> dict[str, Any]:
    """Strip any encrypted token fields and enrich with live runtime vs credential status."""
    out = db_models.strip_sensitive_fields({
        k: v
        for k, v in doc.items()
        if k not in ("_id", "token_sealed", "bot_token_encrypted")
    })
    live = live_ids if live_ids is not None else _live_running_bot_ids()
    tg_id = int(out.get("telegram_bot_id") or 0)
    is_running = tg_id > 0 and tg_id in live
    has_creds = bool(doc.get("token_sealed") or doc.get("has_credentials"))
    out["has_credentials"] = has_creds
    out["runtime_running"] = is_running
    if out.get("archived"):
        out["connection_state"] = "archived"
    elif is_running:
        out["connection_state"] = "running"
    elif has_creds and out.get("connected", True):
        out["connection_state"] = "credentials_verified_idle"
    else:
        out["connection_state"] = "disconnected"
    return out


async def sync_legacy_registered_bots(owner_id: int, default_workspace_id: str) -> None:
    """Import any existing `/clone` bots from `MainDB.registered_bots` into `saas_bots` idempotently."""
    try:
        from filestore.database.main_db import MainDB
        legacy = await MainDB().get_user_bots(int(owner_id))
    except Exception:
        return

    for lb in legacy:
        tg_id = int(lb["_id"])
        bid = f"bot_{tg_id}"
        exists = await db_models.bots_col().find_one({"bot_id": bid})
        if exists:
            continue
        preset = copy.deepcopy(BOT_TEMPLATES_PRESETS["filestore"])
        lset = lb.get("settings") or {}
        preset["settings"]["auto_delete_seconds"] = int(lset.get("auto_delete_time") or 0)
        preset["settings"]["custom_caption"] = str(lset.get("custom_caption") or "")
        preset["features"]["protect_content"] = bool(lset.get("protect_content"))
        preset["features"]["permanent_link"] = bool(lset.get("permanent_link"))
        now = time.time()
        await db_models.bots_col().insert_one({
            "bot_id": bid,
            "telegram_bot_id": tg_id,
            "workspace_id": default_workspace_id,
            "owner_id": int(owner_id),
            "display_name": f"@{lb.get('bot_username', f'bot_{tg_id}')}",
            "username": str(lb.get("bot_username") or f"bot_{tg_id}"),
            "description": "Synced from Telegram FileStore Clone Engine",
            "avatar_emoji": "📦",
            "bot_type": "filestore",
            "category": "File Store",
            "tags": ["telegram-synced", "filestore"],
            "fleet_group": "Production Clones",
            "token_sealed": str(lb.get("bot_token_encrypted") or ""),
            "token_masked": "synced:****",
            "has_credentials": bool(lb.get("bot_token_encrypted")),
            "connected": bool(lb.get("is_active", True)),
            "archived": False,
            "deleted": False,
            "log_channel_id": int(lb.get("log_channel_id") or 0),
            "features": preset["features"],
            "settings": preset["settings"],
            "config_version": 1,
            "created_at": db_models.utcnow_iso(),
            "created_ts": now,
            "last_active_at": db_models.utcnow_iso(),
            "last_error": "",
        })
        await _seed_bot_defaults(bid, int(owner_id), preset["features"], preset["settings"])


async def _seed_bot_defaults(
    bot_id: str, actor_id: int, features: dict[str, Any], settings: dict[str, Any]
) -> None:
    now = time.time()
    # Initial v1 revision
    await db_models.bot_revs_col().update_one(
        {"bot_id": bot_id, "version": 1},
        {
            "$setOnInsert": {
                "bot_id": bot_id,
                "version": 1,
                "status": "applied",
                "author_id": int(actor_id),
                "note": "Initial configuration",
                "features": copy.deepcopy(features),
                "settings": copy.deepcopy(settings),
                "created_at": db_models.utcnow_iso(),
                "created_ts": now,
            }
        },
        upsert=True,
    )
    # Seed Development / Testing / Production environments
    for env_name in ("development", "testing", "production"):
        await db_models.bot_envs_col().update_one(
            {"bot_id": bot_id, "env": env_name},
            {
                "$setOnInsert": {
                    "bot_id": bot_id,
                    "env": env_name,
                    "label": env_name.title(),
                    "features": copy.deepcopy(features),
                    "settings": copy.deepcopy(settings),
                    "health": "healthy",
                    "updated_by": int(actor_id),
                    "updated_at": db_models.utcnow_iso(),
                    "updated_ts": now,
                }
            },
            upsert=True,
        )
    # Seed core commands from Videl1's real command definitions
    default_cmds = [
        ("start", "Open bot home screen", "General", "everyone", True),
        ("help", "View interactive help & guide", "General", "everyone", True),
        ("settings", "Configure personal preferences", "General", "everyone", True),
        ("id", "Inspect user & chat IDs", "Utilities", "everyone", True),
        ("ping", "Check bot latency", "Utilities", "everyone", True),
        ("batch", "Create multi-file share link", "File Store", "administrators", bool(features.get("filestore_enabled"))),
        ("flink", "Formatted batch link builder", "File Store", "administrators", bool(features.get("filestore_enabled"))),
        ("gsettings", "Group moderation control panel", "Group Management", "administrators", bool(features.get("group_moderation"))),
        ("compress", "Compress video resolution/CRF", "Media", "authorized_users", bool(features.get("media_encoder"))),
        ("stats", "Bot runtime & user statistics", "Administration", "administrators", True),
    ]
    for cmd_name, desc, cat, perm, enabled in default_cmds:
        await db_models.bot_commands_col().update_one(
            {"bot_id": bot_id, "command": cmd_name},
            {
                "$setOnInsert": {
                    "bot_id": bot_id,
                    "command": cmd_name,
                    "description": desc,
                    "category": cat,
                    "visibility": "public" if perm == "everyone" else "scoped",
                    "permission": perm,
                    "enabled": enabled,
                    "handler_bound": True,
                    "usage_count": 0,
                    "updated_at": db_models.utcnow_iso(),
                    "updated_ts": now,
                }
            },
            upsert=True,
        )


async def verify_telegram_token_diagnostic(raw_token: str) -> dict[str, Any]:
    """Validate token format and query Telegram Bot API `getMe` (honest diagnostics, zero token return)."""
    tok = (raw_token or "").strip()
    m = TOKEN_FORMAT_RE.match(tok)
    if not m:
        raise SaaSValidationError(
            "Invalid token format. Expected '<bot_id>:<secret>' from @BotFather (never paste API_HASH here)."
        )
    bot_num_id = int(m.group(1))
    masked = security.mask_token(tok)
    try:
        from filestore.utils.helpers import validate_bot_token
        info = await validate_bot_token(tok)
    except Exception:
        info = None
        # Network exception strings can contain Telegram URLs with bot tokens.
        err = "Telegram credential verification is unavailable."
    else:
        err = ""

    if info and info.get("id"):
        return {
            "verified_with_telegram": True,
            "telegram_bot_id": int(info["id"]),
            "first_name": str(info.get("first_name") or ""),
            "username": str(info.get("username") or ""),
            "can_join_groups": bool(info.get("can_join_groups", True)),
            "supports_inline_queries": bool(info.get("supports_inline_queries", False)),
            "token_masked": masked,
            "diagnostic": "Telegram Bot API getMe succeeded. Credentials verified.",
        }
    return {
        "verified_with_telegram": False,
        "telegram_bot_id": bot_num_id,
        "first_name": "",
        "username": "",
        "token_masked": masked,
        "diagnostic": err or "Telegram Bot API could not verify this token (invalid token or offline environment).",
    }


async def create_or_register_bot(
    workspace_id: str,
    owner_id: int,
    payload: dict[str, Any],
    *,
    allow_unverified_offline: bool = False,
) -> dict[str, Any]:
    """Guided Bot Creation Studio registration (validates template, dependencies, uniqueness & encrypts token)."""
    display_name = str(payload.get("display_name") or "").strip()
    if len(display_name) < 2 or len(display_name) > 80:
        raise SaaSValidationError("Bot display name must be between 2 and 80 characters.")

    tpl_key = str(payload.get("template") or "filestore").strip().lower()
    if tpl_key not in BOT_TEMPLATES_PRESETS:
        tpl_key = "custom"
    preset = copy.deepcopy(BOT_TEMPLATES_PRESETS[tpl_key])

    features = dict(preset["features"])
    if isinstance(payload.get("features"), dict):
        for k, v in payload["features"].items():
            if k in features:
                features[k] = bool(v)
    dep_errors = validate_feature_dependencies(features)
    if dep_errors:
        raise SaaSValidationError("; ".join(dep_errors))

    settings = dict(preset["settings"])
    if isinstance(payload.get("settings"), dict):
        for k, v in payload["settings"].items():
            if k in settings:
                settings[k] = v

    raw_token = str(payload.get("bot_token") or "").strip()
    tg_bot_id = 0
    username = str(payload.get("username") or "").strip().lstrip("@")
    token_sealed = ""
    token_masked = ""
    connected = False
    diag_note = "Registered as draft configuration (no bot token attached yet)."

    if raw_token:
        diag = await verify_telegram_token_diagnostic(raw_token)
        if not diag["verified_with_telegram"] and not allow_unverified_offline:
            raise SaaSValidationError(diag["diagnostic"])
        tg_bot_id = int(diag["telegram_bot_id"])
        # Prevent cross-owner hijack of an already registered Telegram bot ID
        clash = await db_models.bots_col().find_one({"telegram_bot_id": tg_bot_id})
        if clash:
            raise SaaSValidationError("This Telegram bot is already registered in a workspace.")
        if not security.is_encryption_enabled():
            raise SaaSValidationError("A valid ENCRYPTION_KEY is required before storing bot credentials.")
        if diag.get("username"):
            username = diag["username"]
        if diag.get("first_name") and not payload.get("display_name"):
            display_name = diag["first_name"]
        token_sealed = security.encrypt_token(raw_token)
        token_masked = diag["token_masked"]
        connected = bool(diag["verified_with_telegram"])
        diag_note = diag["diagnostic"]

    if not username:
        username = re.sub(r"[^a-zA-Z0-9_]", "_", display_name.lower()).strip("_")[:28] + "_bot"

    bid = f"bot_{tg_bot_id}" if tg_bot_id else f"bot_{secrets.token_hex(5)}"
    now = time.time()
    tags = [str(t).strip()[:24] for t in (payload.get("tags") or []) if str(t).strip()][:10]

    bot_doc = {
        "bot_id": bid,
        "telegram_bot_id": tg_bot_id,
        "workspace_id": workspace_id,
        "owner_id": int(owner_id),
        "display_name": display_name,
        "username": username,
        "description": str(payload.get("description") or preset["description"]).strip()[:300],
        "avatar_emoji": str(payload.get("avatar_emoji") or "🤖").strip()[:8],
        "bot_type": tpl_key,
        "category": str(payload.get("category") or preset["category"])[:40],
        "tags": tags,
        "fleet_group": str(payload.get("fleet_group") or "Default Fleet")[:48],
        "token_sealed": token_sealed,
        "token_masked": token_masked,
        "has_credentials": bool(token_sealed),
        "connected": connected,
        "archived": False,
        "deleted": False,
        "log_channel_id": int(payload.get("log_channel_id") or 0),
        "features": features,
        "settings": settings,
        "config_version": 1,
        "diagnostic": diag_note,
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
        "updated_ts": now,
        "last_active_at": db_models.utcnow_iso(),
        "last_error": "",
    }
    await db_models.bots_col().update_one({"bot_id": bid}, {"$set": bot_doc}, upsert=True)
    await _seed_bot_defaults(bid, int(owner_id), features, settings)
    await models_db.log_audit(owner_id, "bot_register", actor_role="owner", target=f"{workspace_id}:{bid}", detail=f"@{username}")
    await db_models.create_notification(
        workspace_id,
        bot_id=bid,
        category="bot_connected",
        title=f"Bot {display_name} created",
        message=f"@{username} added to workspace using '{preset['name']}' template.",
        severity="success",
    )
    return _public_bot_doc(bot_doc)


async def get_owned_workspace_bot(workspace_id: str, bot_id: str) -> dict[str, Any]:
    """Fetch a bot strictly scoped to `workspace_id` (prevents ID tampering across workspaces!)."""
    doc = await db_models.bots_col().find_one(
        {"bot_id": str(bot_id), "workspace_id": str(workspace_id), "deleted": {"$ne": True}}
    )
    if not doc:
        raise KeyError("Bot not found in this workspace.")
    return doc


async def list_workspace_bots(
    workspace_id: str,
    *,
    user_id: int,
    search: str = "",
    bot_type: str = "",
    status: str = "",
    tag: str = "",
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    # Legacy `/clone` bots have no workspace ID. Automatically importing them
    # into whichever workspace is viewed first misattributes private bot data.
    # Existing SaaS records remain available; legacy records require an explicit
    # ownership-aware migration instead of being guessed here.
    q: dict[str, Any] = {"workspace_id": workspace_id, "deleted": {"$ne": True}}
    if not include_archived:
        q["archived"] = False
    if bot_type and bot_type != "all":
        q["bot_type"] = bot_type

    docs = await db_models.bots_col().find(q).sort("created_ts", -1).to_list(length=300)
    live_ids = _live_running_bot_ids()
    needle = (search or "").strip().lower()
    st_filter = (status or "").strip().lower()
    tag_filter = (tag or "").strip().lower()

    out: list[dict[str, Any]] = []
    for raw in docs:
        pub = _public_bot_doc(raw, live_ids)
        if st_filter and st_filter != "all" and pub["connection_state"] != st_filter:
            continue
        if tag_filter and tag_filter not in [t.lower() for t in (pub.get("tags") or [])]:
            continue
        if needle:
            hay = f"{pub.get('display_name', '')} {pub.get('username', '')} {pub.get('description', '')} {' '.join(pub.get('tags') or [])}".lower()
            if needle not in hay:
                continue
        out.append(pub)
    return out


async def update_bot_profile_or_state(
    workspace_id: str,
    bot_id: str,
    actor_id: int,
    patch: dict[str, Any],
) -> dict[str, Any]:
    doc = await get_owned_workspace_bot(workspace_id, bot_id)
    upd: dict[str, Any] = {"updated_ts": time.time()}
    for str_key, max_len in (("display_name", 80), ("description", 300), ("category", 40), ("fleet_group", 48), ("avatar_emoji", 8)):
        if str_key in patch and isinstance(patch[str_key], str):
            upd[str_key] = patch[str_key].strip()[:max_len]
    if "tags" in patch and isinstance(patch["tags"], list):
        upd["tags"] = [str(t).strip()[:24] for t in patch["tags"] if str(t).strip()][:12]
    if "archived" in patch:
        upd["archived"] = bool(patch["archived"])
    if patch.get("disconnect"):
        upd["connected"] = False
        upd["token_sealed"] = ""
        upd["token_masked"] = ""
        upd["has_credentials"] = False
    if patch.get("rotate_token"):
        if not security.is_encryption_enabled():
            raise SaaSValidationError("A valid ENCRYPTION_KEY is required before storing bot credentials.")
        diag = await verify_telegram_token_diagnostic(str(patch["rotate_token"]))
        if not diag["verified_with_telegram"]:
            raise SaaSValidationError("Telegram could not verify the supplied credentials.")
        if int(diag["telegram_bot_id"]) != int(doc.get("telegram_bot_id") or 0):
            raise SaaSValidationError("The replacement token does not belong to this bot.")
        upd["token_sealed"] = security.encrypt_token(str(patch["rotate_token"]).strip())
        upd["token_masked"] = diag["token_masked"]
        upd["has_credentials"] = True
        upd["connected"] = bool(diag["verified_with_telegram"])
        await models_db.log_audit(actor_id, "bot_token_rotate", target=f"{workspace_id}:{bot_id}")

    await db_models.bots_col().update_one({"bot_id": doc["bot_id"]}, {"$set": upd})
    fresh = await get_owned_workspace_bot(workspace_id, bot_id)
    return _public_bot_doc(fresh)


async def delete_bot(workspace_id: str, bot_id: str, actor_id: int) -> bool:
    doc = await get_owned_workspace_bot(workspace_id, bot_id)
    await db_models.bots_col().update_one(
        {"bot_id": doc["bot_id"]},
        {"$set": {"deleted": True, "connected": False, "token_sealed": "", "updated_ts": time.time()}},
    )
    await models_db.log_audit(actor_id, "bot_delete", target=f"{workspace_id}:{bot_id}", detail=doc.get("username", ""))
    return True


# ─────────────────────────── Bot Fleet Management (Section 8) ───────────────────────────
async def fleet_summary_and_drift(workspace_id: str, user_id: int) -> dict[str, Any]:
    bots = await list_workspace_bots(workspace_id, user_id=user_id, include_archived=True)
    by_state = {"running": 0, "credentials_verified_idle": 0, "disconnected": 0, "archived": 0}
    groups: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    for b in bots:
        st = b.get("connection_state", "disconnected")
        by_state[st] = by_state.get(st, 0) + 1
        grp = b.get("fleet_group") or "Default Fleet"
        groups[grp] = groups.get(grp, 0) + 1
        if b.get("last_error"):
            errors.append({"bot_id": b["bot_id"], "username": b["username"], "error": b["last_error"]})

    # Configuration drift check across active non-archived bots
    active = [b for b in bots if not b.get("archived")]
    drift_fields: list[str] = []
    if len(active) >= 2:
        baseline = active[0].get("features") or {}
        for other in active[1:]:
            for d in compute_config_diff(baseline, other.get("features") or {}):
                if d["field"] not in drift_fields:
                    drift_fields.append(d["field"])

    return {
        "total_bots": len(bots),
        "state_counts": by_state,
        "fleet_groups": groups,
        "recent_errors": errors,
        "config_drift_fields": drift_fields,
        "bots": bots,
    }


async def fleet_bulk_operation(
    workspace_id: str,
    actor_id: int,
    *,
    bot_ids: list[str],
    operation: str,
    value: Any = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    """Preview affected bots first; execute ONLY when `confirmed=True` (Section 8)."""
    if not bot_ids:
        raise SaaSValidationError("Select at least one bot in this workspace.")
    affected: list[dict[str, Any]] = []
    for bid in bot_ids:
        doc = await get_owned_workspace_bot(workspace_id, bid)
        affected.append(_public_bot_doc(doc))

    if confirmed is not True:
        return {
            "preview": True,
            "operation": operation,
            "affected_count": len(affected),
            "affected_bots": [{"bot_id": b["bot_id"], "display_name": b["display_name"], "username": b["username"]} for b in affected],
        }

    now = time.time()
    for b in affected:
        bid = b["bot_id"]
        if operation == "add_label":
            lbl = str(value or "").strip()[:24]
            if lbl:
                tags = list(dict.fromkeys([*(b.get("tags") or []), lbl]))[:12]
                await db_models.bots_col().update_one({"bot_id": bid}, {"$set": {"tags": tags, "updated_ts": now}})
        elif operation == "set_fleet_group":
            grp = str(value or "Default Fleet").strip()[:48]
            await db_models.bots_col().update_one({"bot_id": bid}, {"$set": {"fleet_group": grp, "updated_ts": now}})
        elif operation == "notify_on_error":
            raw = await get_owned_workspace_bot(workspace_id, bid)
            st = dict(raw.get("settings") or {})
            st["notify_on_error"] = bool(value)
            await db_models.bots_col().update_one({"bot_id": bid}, {"$set": {"settings": st, "updated_ts": now}})
        elif operation == "archive":
            await db_models.bots_col().update_one({"bot_id": bid}, {"$set": {"archived": True, "updated_ts": now}})
        else:
            raise SaaSValidationError(f"Unsupported bulk fleet operation '{operation}'.")

    await models_db.log_audit(
        actor_id, f"fleet_bulk_{operation}", target=workspace_id, detail=f"{len(affected)} bots"
    )
    return {"preview": False, "executed": True, "operation": operation, "updated_count": len(affected)}


# ─────────────────────────── Bot Configuration Studio & Environments (Sections 9 & 16) ───────────────────────────
async def save_or_apply_bot_config(
    workspace_id: str,
    bot_id: str,
    actor_id: int,
    *,
    features: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    mode: str = "apply",  # draft | preview | apply | reset
    note: str = "",
) -> dict[str, Any]:
    bot = await get_owned_workspace_bot(workspace_id, bot_id)
    if mode == "reset":
        preset = copy.deepcopy(BOT_TEMPLATES_PRESETS.get(bot.get("bot_type", "custom"), BOT_TEMPLATES_PRESETS["custom"]))
        next_features = preset["features"]
        next_settings = preset["settings"]
    else:
        next_features = dict(bot.get("features") or {})
        if isinstance(features, dict):
            for k, v in features.items():
                next_features[str(k)] = bool(v)
        next_settings = dict(bot.get("settings") or {})
        if isinstance(settings, dict):
            for k, v in db_models.strip_sensitive_fields(settings).items():
                next_settings[str(k)] = v

    dep_errors = validate_feature_dependencies(next_features)
    if dep_errors:
        raise SaaSValidationError("; ".join(dep_errors))

    diffs = compute_config_diff(
        {"features": bot.get("features") or {}, "settings": bot.get("settings") or {}},
        {"features": next_features, "settings": next_settings},
    )
    if mode == "preview":
        return {"mode": "preview", "diffs": diffs, "dependencies": FEATURE_DEPENDENCIES}

    now = time.time()
    next_ver = int(bot.get("config_version") or 1) + 1
    rev_doc = {
        "bot_id": bot_id,
        "version": next_ver,
        "status": "draft" if mode == "draft" else "applied",
        "author_id": int(actor_id),
        "note": str(note or ("Reset to defaults" if mode == "reset" else "Configuration update"))[:160],
        "features": next_features,
        "settings": next_settings,
        "diffs": diffs,
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
    }
    await db_models.bot_revs_col().insert_one(dict(rev_doc))

    if mode in ("apply", "reset"):
        await db_models.bots_col().update_one(
            {"bot_id": bot_id},
            {
                "$set": {
                    "features": next_features,
                    "settings": next_settings,
                    "config_version": next_ver,
                    "updated_ts": now,
                }
            },
        )
        await models_db.log_audit(actor_id, "bot_config_apply", target=f"{workspace_id}:{bot_id}", detail=f"v{next_ver}")
    rev_doc.pop("_id", None)
    updated_bot = _public_bot_doc(await get_owned_workspace_bot(workspace_id, bot_id))
    return {"mode": mode, "revision": rev_doc, "diffs": diffs, "bot": updated_bot}


async def list_bot_revisions(workspace_id: str, bot_id: str) -> list[dict[str, Any]]:
    await get_owned_workspace_bot(workspace_id, bot_id)
    revs = await db_models.bot_revs_col().find({"bot_id": bot_id}).sort("version", -1).limit(50).to_list(length=50)
    for r in revs:
        r.pop("_id", None)
    return revs


async def restore_bot_revision(workspace_id: str, bot_id: str, actor_id: int, version: int) -> dict[str, Any]:
    await get_owned_workspace_bot(workspace_id, bot_id)
    rev = await db_models.bot_revs_col().find_one({"bot_id": bot_id, "version": int(version)})
    if not rev:
        raise KeyError(f"Revision v{version} not found.")
    return await save_or_apply_bot_config(
        workspace_id,
        bot_id,
        actor_id,
        features=rev.get("features") or {},
        settings=rev.get("settings") or {},
        mode="apply",
        note=f"Restored from v{version}",
    )


async def list_bot_environments(workspace_id: str, bot_id: str) -> list[dict[str, Any]]:
    bot = await get_owned_workspace_bot(workspace_id, bot_id)
    envs = await db_models.bot_envs_col().find({"bot_id": bot_id}).to_list(length=10)
    if not envs:
        await _seed_bot_defaults(bot_id, int(bot["owner_id"]), bot.get("features") or {}, bot.get("settings") or {})
        envs = await db_models.bot_envs_col().find({"bot_id": bot_id}).to_list(length=10)
    order = {"development": 0, "testing": 1, "production": 2}
    for e in envs:
        e.pop("_id", None)
    envs.sort(key=lambda x: order.get(x.get("env", ""), 9))
    return envs


async def update_or_promote_environment(
    workspace_id: str,
    bot_id: str,
    actor_id: int,
    *,
    source_env: str,
    target_env: str = "",
    features: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    confirmed: bool = False,
) -> dict[str, Any]:
    await get_owned_workspace_bot(workspace_id, bot_id)
    src_name = source_env.strip().lower()
    if src_name not in ("development", "testing", "production"):
        raise SaaSValidationError("Environment must be development, testing, or production.")

    # A direct production edit is also a production change, not just promotion.
    if not target_env:
        if src_name == "production" and confirmed is not True:
            raise SaaSValidationError("Editing production requires explicit confirmation.")
        cur = await db_models.bot_envs_col().find_one({"bot_id": bot_id, "env": src_name}) or {}
        nf = dict(cur.get("features") or {})
        if isinstance(features, dict):
            nf.update({k: bool(v) for k, v in features.items()})
        ns = dict(cur.get("settings") or {})
        if isinstance(settings, dict):
            ns.update(db_models.strip_sensitive_fields(settings))
        errs = validate_feature_dependencies(nf)
        if errs:
            raise SaaSValidationError("; ".join(errs))
        await db_models.bot_envs_col().update_one(
            {"bot_id": bot_id, "env": src_name},
            {"$set": {"features": nf, "settings": ns, "updated_by": int(actor_id), "updated_at": db_models.utcnow_iso(), "updated_ts": time.time()}},
            upsert=True,
        )
        return {"updated_env": src_name, "features": nf, "settings": ns}

    # Promotion workflow (`source_env` -> `target_env`) — NEVER overwrites production without `confirmed=True`
    tgt_name = target_env.strip().lower()
    if tgt_name not in ("development", "testing", "production") or tgt_name == src_name:
        raise SaaSValidationError("Select two distinct environments to compare or promote.")
    src_doc = await db_models.bot_envs_col().find_one({"bot_id": bot_id, "env": src_name}) or {}
    tgt_doc = await db_models.bot_envs_col().find_one({"bot_id": bot_id, "env": tgt_name}) or {}
    diffs = compute_config_diff(
        {"features": tgt_doc.get("features") or {}, "settings": tgt_doc.get("settings") or {}},
        {"features": src_doc.get("features") or {}, "settings": src_doc.get("settings") or {}},
    )
    if confirmed is not True:
        return {"preview": True, "requires_confirmation": True, "source_env": src_name, "target_env": tgt_name, "diffs": diffs}

    await db_models.bot_envs_col().update_one(
        {"bot_id": bot_id, "env": tgt_name},
        {
            "$set": {
                "features": copy.deepcopy(src_doc.get("features") or {}),
                "settings": copy.deepcopy(src_doc.get("settings") or {}),
                "promoted_from": src_name,
                "updated_by": int(actor_id),
                "updated_at": db_models.utcnow_iso(),
                "updated_ts": time.time(),
            }
        },
        upsert=True,
    )
    if tgt_name == "production":
        await save_or_apply_bot_config(
            workspace_id,
            bot_id,
            actor_id,
            features=src_doc.get("features") or {},
            settings=src_doc.get("settings") or {},
            mode="apply",
            note=f"Promoted {src_name} → production",
        )
    await models_db.log_audit(actor_id, "bot_env_promote", target=f"{workspace_id}:{bot_id}", detail=f"{src_name}->{tgt_name}")
    return {"preview": False, "promoted": True, "source_env": src_name, "target_env": tgt_name, "diffs": diffs}


# ─────────────────────────── Command Management Studio (Section 10) ───────────────────────────
CMD_NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")


async def list_bot_commands(
    workspace_id: str,
    bot_id: str,
    *,
    search: str = "",
    category: str = "",
) -> list[dict[str, Any]]:
    bot = await get_owned_workspace_bot(workspace_id, bot_id)
    cmds = await db_models.bot_commands_col().find({"bot_id": bot_id}).to_list(length=200)
    if not cmds:
        await _seed_bot_defaults(bot_id, int(bot["owner_id"]), bot.get("features") or {}, bot.get("settings") or {})
        cmds = await db_models.bot_commands_col().find({"bot_id": bot_id}).to_list(length=200)
    q = (search or "").strip().lower()
    cat = (category or "").strip()
    out: list[dict[str, Any]] = []
    for c in cmds:
        c.pop("_id", None)
        if cat and cat != "all" and c.get("category") != cat:
            continue
        if q and q not in c.get("command", "").lower() and q not in c.get("description", "").lower():
            continue
        out.append(c)
    out.sort(key=lambda x: (x.get("category", ""), x.get("command", "")))
    return out


async def upsert_bot_command(
    workspace_id: str,
    bot_id: str,
    actor_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    await get_owned_workspace_bot(workspace_id, bot_id)
    cmd = str(payload.get("command") or "").strip().lstrip("/").lower()
    if not CMD_NAME_RE.match(cmd):
        raise SaaSValidationError("Command must be 1–32 lowercase letters, digits, or underscores.")
    desc = str(payload.get("description") or "").strip()
    if len(desc) < 2 or len(desc) > 256:
        raise SaaSValidationError("Command description must be 2–256 characters (Telegram BotFather limit).")
    cat = str(payload.get("category") or "Custom")
    if cat not in COMMAND_CATEGORIES:
        cat = "Custom"
    perm = str(payload.get("permission") or "everyone")
    if perm not in COMMAND_PERMISSIONS:
        perm = "everyone"

    existing = await db_models.bot_commands_col().find_one({"bot_id": bot_id, "command": cmd})
    handler_bound = bool(existing.get("handler_bound")) if existing else False
    now = time.time()
    doc = {
        "bot_id": bot_id,
        "command": cmd,
        "description": desc,
        "category": cat,
        "visibility": str(payload.get("visibility") or ("public" if perm == "everyone" else "scoped")),
        "permission": perm,
        "enabled": bool(payload.get("enabled", True)),
        "custom_reply_text": str(payload.get("custom_reply_text") or "")[:1000] if not handler_bound else "",
        "handler_bound": handler_bound,
        "execution_note": (
            "Backed by built-in Videl runtime handler"
            if handler_bound
            else "Custom menu metadata / static text response (does not execute arbitrary Python)"
        ),
        "usage_count": int(existing.get("usage_count", 0)) if existing else 0,
        "updated_at": db_models.utcnow_iso(),
        "updated_ts": now,
    }
    await db_models.bot_commands_col().update_one({"bot_id": bot_id, "command": cmd}, {"$set": doc}, upsert=True)
    await models_db.log_audit(actor_id, "bot_command_upsert", target=f"{workspace_id}:{bot_id}:/{cmd}")
    return doc


async def delete_bot_command(workspace_id: str, bot_id: str, actor_id: int, command: str) -> bool:
    await get_owned_workspace_bot(workspace_id, bot_id)
    cmd = command.strip().lstrip("/").lower()
    res = await db_models.bot_commands_col().delete_one({"bot_id": bot_id, "command": cmd})
    if getattr(res, "deleted_count", 0):
        await models_db.log_audit(actor_id, "bot_command_delete", target=f"{workspace_id}:{bot_id}:/{cmd}")
        return True
    return False

"""
Operations, Automation Studio, Templates, Plugins, Developer Center,
Backups, Custom Dashboards, Global Search, Demo Seeder & Platform Admin
(`VidelBot/webapp/services_ops.py`).
"""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import time
from typing import Any


import config
from core import diagnostics
from platform_core import destinations, models_db
from webapp import db_models, safe_webhook, services_bots, services_clone_files

# ─────────────────────────── Supported Automation Catalog (Section 18) ───────────────────────────
SUPPORTED_TRIGGERS = (
    "file_uploaded",
    "download_completed",
    "storage_threshold",
    "scheduled_report",
    "bot_error",
    "bot_connected",
    "task_failed",
    "scheduled_cleanup_inspect",
)

SUPPORTED_ACTIONS = (
    "record_activity",
    "create_notification",
    "generate_report",
    "inspect_eligible_files",
)

TEMPLATE_CATEGORIES = (
    "bot_config",
    "filestore_config",
    "command_metadata",
    "permission_presets",
    "automation_workflow",
    "welcome_message",
    "notification_prefs",
)

OFFICIAL_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_id": "tpl_off_filestore_pro",
        "name": "High-Security File Store Vault",
        "category": "filestore_config",
        "official": True,
        "publisher": "Videl Official",
        "version": "1.2.0",
        "description": "Content-protected File Store preset with 10-minute auto-delete and duplicate SHA-256 blocking.",
        "payload": {
            "duplicate_policy": "block",
            "max_file_mb": 2000,
            "access_policy": "authorized_only",
            "caption_template": "📦 {filename} ({filesize})",
        },
    },
    {
        "template_id": "tpl_off_mod_shield",
        "name": "Anti-Raid Supergroup Shield",
        "category": "bot_config",
        "official": True,
        "publisher": "Videl Official",
        "version": "1.1.0",
        "description": "Group moderation bot preset with join-request notifications and error alerts.",
        "payload": {
            "features": {"group_moderation": True},
            "settings": {"welcome_message": "Welcome {mention}! Please review /rules.", "notify_on_error": True},
        },
    },
    {
        "template_id": "tpl_off_dl_notify",
        "name": "Download Completion Notifier",
        "category": "automation_workflow",
        "official": True,
        "publisher": "Videl Official",
        "version": "1.0.0",
        "description": "Creates an in-app workspace alert whenever a large file download completes.",
        "payload": {
            "trigger": "download_completed",
            "conditions": [{"field": "size_mb", "op": ">=", "value": 10}],
            "actions": [{"type": "create_notification", "title": "Large download finished"}],
        },
    },
]

_automation_seen_keys: set[str] = set()


# ─────────────────────────── 1. Bot Health & Webhook Diagnostics (Sections 15 & 17) ───────────────────────────
async def bot_operations_health(workspace_id: str, user_id: int) -> dict[str, Any]:
    bots = await services_bots.list_workspace_bots(workspace_id, user_id=user_id, include_archived=False)
    sys_diag = diagnostics.collect_diagnostics()

    alerts: list[dict[str, Any]] = []
    bot_health_rows: list[dict[str, Any]] = []

    for b in bots:
        state = b["connection_state"]
        if b["has_credentials"] and not b["runtime_running"]:
            alerts.append({
                "severity": "info",
                "code": "credentials_valid_worker_idle",
                "bot_id": b["bot_id"],
                "message": f"@{b['username']}: Token credentials verified, worker process currently idle.",
            })
        elif not b["has_credentials"]:
            alerts.append({
                "severity": "warning",
                "code": "bot_disconnected",
                "bot_id": b["bot_id"],
                "message": f"{b['display_name']} has no connected Telegram token.",
            })
        if b.get("last_error"):
            alerts.append({
                "severity": "error",
                "code": "bot_error",
                "bot_id": b["bot_id"],
                "message": f"@{b['username']}: {b['last_error']}",
            })
        bot_health_rows.append({
            "bot_id": b["bot_id"],
            "display_name": b["display_name"],
            "username": b["username"],
            "credentials_valid": bool(b["has_credentials"] and b.get("connected")),
            "runtime_process_running": bool(b["runtime_running"]),
            "connection_state": state,
            "last_active_at": b.get("last_active_at", ""),
            "last_error": b.get("last_error", ""),
        })

    # Legacy connections and scheduled jobs are user-scoped, not workspace-scoped.
    # Do not attribute their errors to this workspace.

    if sys_diag["resources"]["disk_percent"] >= 85.0:
        alerts.append({
            "severity": "warning",
            "code": "storage_warning",
            "message": f"Server disk usage is at {sys_diag['resources']['disk_percent']:.1f}%.",
        })

    return {
        "architecture_note": "Incoming Telegram updates are handled exclusively by WzGram/Pyrofork; aiogram acts as outgoing Bot API bridge.",
        "database_connected": models_db.is_mongodb_backend(),
        "database_mode": "mongodb" if models_db.is_mongodb_backend() else "standalone_memory",
        "bots": bot_health_rows,
        "alerts": alerts,
        "plugins": sys_diag["plugins"] if "plugins" in sys_diag else diagnostics.plugin_manager.health_report() if hasattr(diagnostics, "plugin_manager") else {},
        "resources": sys_diag["resources"],
    }


async def register_outbound_webhook(
    workspace_id: str, actor_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """Register an outbound endpoint; DNS is validated when a connection is made."""
    url = safe_webhook.validate_webhook_url(str(payload.get("url") or ""))
    events = [str(e).strip() for e in (payload.get("events") or ["download_completed", "bot_error"]) if str(e).strip()]
    now = time.time()
    doc = {
        "webhook_id": f"whk_{secrets.token_hex(6)}",
        "workspace_id": workspace_id,
        "name": str(payload.get("name") or "Integration Webhook")[:80],
        "url": url,
        "events": events[:12],
        "enabled": bool(payload.get("enabled", True)),
        "last_status": "never_fired",
        "last_http_code": 0,
        "failure_count": 0,
        "created_by": int(actor_id),
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
    }
    await db_models.webhooks_col().insert_one(dict(doc))
    await models_db.log_audit(actor_id, "webhook_register", target=f"{workspace_id}:{doc['webhook_id']}")
    doc.pop("_id", None)
    doc.pop("url", None)
    return doc


async def list_webhooks_and_events(workspace_id: str, *, event_type: str = "") -> dict[str, Any]:
    hooks = await db_models.webhooks_col().find({"workspace_id": workspace_id}).to_list(length=50)
    ev_q: dict[str, Any] = {"workspace_id": workspace_id}
    if event_type:
        ev_q["event_type"] = event_type
    events = await db_models.webhook_events_col().find(ev_q).sort("created_ts", -1).limit(50).to_list(length=50)
    for h in hooks:
        h.pop("_id", None)
        # Older records can contain secret-bearing URLs; never reveal them again.
        h.pop("url", None)
    for e in events:
        e.pop("_id", None)
        # Historical exceptions may contain endpoint URLs or credentials.
        if e.get("error"):
            e["error"] = "Delivery failed; details withheld."
    return {
        "incoming_telegram_mode": "MTProto Long-Polling / Authoritative WzGram Receiver (No competing Telegram webhook)",
        "webhooks": hooks,
        "recent_events": events,
    }


async def test_outbound_webhook(workspace_id: str, webhook_id: str, actor_id: int) -> dict[str, Any]:
    hook = await db_models.webhooks_col().find_one({"webhook_id": webhook_id, "workspace_id": workspace_id})
    if not hook:
        raise KeyError("Webhook not found in this workspace.")
    now = time.time()
    body = {"event": "webhook.ping", "workspace_id": workspace_id, "timestamp": db_models.utcnow_iso()}
    http_code = 0
    err = ""
    try:
        http_code = await safe_webhook.ping(hook["url"], body)
        ok = 200 <= http_code < 300
        if not ok:
            err = f"HTTP {http_code}"
    except Exception:
        ok = False
        # aiohttp exceptions can contain URLs, query strings and credentials.
        err = "Delivery failed; verify the public endpoint configuration."


    ev_doc = {
        "event_id": f"whe_{secrets.token_hex(6)}",
        "workspace_id": workspace_id,
        "webhook_id": webhook_id,
        "event_type": "webhook.ping",
        "status": "delivered" if ok else "failed",
        "http_code": http_code,
        "error": err,
        "retries": 0,
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
    }
    await db_models.webhook_events_col().insert_one(dict(ev_doc))
    await db_models.webhooks_col().update_one(
        {"webhook_id": webhook_id, "workspace_id": workspace_id},
        {
            "$set": {
                "last_status": "ok" if ok else "failed",
                "last_http_code": http_code,
                "last_event_at": db_models.utcnow_iso(),
            },
            "$inc": {"failure_count": 0 if ok else 1},
        },
    )
    ev_doc.pop("_id", None)
    return ev_doc


# ─────────────────────────── 2. Automation Studio (Section 18) ───────────────────────────
AUTOMATION_ACTION_SCOPES = {
    "record_activity": frozenset(),
    "create_notification": frozenset(),
    "generate_report": frozenset({"analytics"}),
    "inspect_eligible_files": frozenset({"analytics", "filestore"}),
}


def automation_action_scopes(actions: list[dict[str, Any]]) -> set[str]:
    """Fail closed on unsupported or malformed stored actions, before side effects."""
    if not isinstance(actions, list) or not actions:
        raise services_bots.SaaSValidationError("Workflow has no supported actions.")
    scopes: set[str] = set()
    for action in actions:
        if not isinstance(action, dict) or action.get("type") not in AUTOMATION_ACTION_SCOPES:
            raise services_bots.SaaSValidationError("Workflow contains an unsupported or unavailable action.")
        scopes.update(AUTOMATION_ACTION_SCOPES[action["type"]])
    return scopes


def validate_automation_workflow(payload: dict[str, Any]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    trigger = str(payload.get("trigger") or "").strip()
    if trigger not in SUPPORTED_TRIGGERS:
        raise services_bots.SaaSValidationError(
            f"Unsupported trigger '{trigger}'. Supported triggers: {', '.join(SUPPORTED_TRIGGERS)}"
        )
    raw_conds = payload.get("conditions") or []
    if not isinstance(raw_conds, list) or len(raw_conds) > 10:
        raise services_bots.SaaSValidationError("Conditions must be a list of at most 10 rules.")
    conds: list[dict[str, Any]] = []
    for c in raw_conds:
        if not isinstance(c, dict):
            continue
        op = str(c.get("op") or "==")
        if op not in ("==", "!=", ">", "<", ">=", "<=", "contains", "gt", "lt", "gte", "lte", "eq"):
            raise services_bots.SaaSValidationError(f"Unsupported condition operator '{op}'.")
        norm_op = {"gt": ">", "lt": "<", "gte": ">=", "lte": "<=", "eq": "=="}.get(op, op)
        conds.append({"field": str(c.get("field") or "")[:40], "op": norm_op, "value": c.get("value")})

    raw_actions = payload.get("actions") or []
    if not isinstance(raw_actions, list) or not (1 <= len(raw_actions) <= 6):
        raise services_bots.SaaSValidationError("Provide between 1 and 6 supported workflow actions.")
    actions: list[dict[str, Any]] = []
    for a in raw_actions:
        if not isinstance(a, dict):
            raise services_bots.SaaSValidationError("Each workflow action must be an object.")
        atype = str(a.get("type") or "").strip()
        if atype not in SUPPORTED_ACTIONS:
            raise services_bots.SaaSValidationError(
                f"Unsupported action '{atype}'. Supported actions: {', '.join(SUPPORTED_ACTIONS)}"
            )
        actions.append({
            "type": atype,
            "title": str(a.get("title") or atype.replace("_", " ").title())[:120],
            "params": db_models.strip_sensitive_fields(a.get("params") or {}),
        })
    return trigger, conds, actions


async def upsert_automation_workflow(
    workspace_id: str, actor_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if len(name) < 2 or len(name) > 80:
        raise services_bots.SaaSValidationError("Workflow name must be between 2 and 80 characters.")
    trigger, conds, actions = validate_automation_workflow(payload)
    bot_id = str(payload.get("bot_id") or "all")
    if bot_id != "all":
        await services_bots.get_owned_workspace_bot(workspace_id, bot_id)
    wf_id = str(payload.get("workflow_id") or f"wf_{secrets.token_hex(6)}")
    existing = await db_models.automations_col().find_one({"workflow_id": wf_id, "workspace_id": workspace_id})
    if payload.get("workflow_id") and not existing:
        raise KeyError("Workflow not found in this workspace.")
    version = (int(existing.get("version") or 1) + 1) if existing else 1
    now = time.time()
    doc = {
        "workflow_id": wf_id,
        "workspace_id": workspace_id,
        "bot_id": bot_id,
        "name": name,
        "trigger": trigger,
        "conditions": conds,
        "actions": actions,
        "enabled": bool(payload.get("enabled", True)),
        "version": version,
        "run_count": int(existing.get("run_count", 0)) if existing else 0,
        "updated_by": int(actor_id),
        "updated_at": db_models.utcnow_iso(),
        "created_ts": float(existing.get("created_ts", now)) if existing else now,
        "updated_ts": now,
    }
    await db_models.automations_col().update_one({"workflow_id": wf_id, "workspace_id": workspace_id}, {"$set": doc}, upsert=True)
    await models_db.log_audit(actor_id, "automation_upsert", target=f"{workspace_id}:{wf_id}", detail=f"v{version}")
    doc.pop("_id", None)
    return doc


def _evaluate_condition(cond: dict[str, Any], event_data: dict[str, Any]) -> bool:
    actual = event_data.get(cond.get("field", ""))
    target = cond.get("value")
    op = cond.get("op", "==")
    try:
        if op == "==":
            return actual == target
        if op == "!=":
            return actual != target
        if op == ">":
            return float(actual or 0) > float(target or 0)
        if op == "<":
            return float(actual or 0) < float(target or 0)
        if op == ">=":
            return float(actual or 0) >= float(target or 0)
        if op == "<=":
            return float(actual or 0) <= float(target or 0)
        if op == "contains":
            return str(target or "").lower() in str(actual or "").lower()
    except (TypeError, ValueError):
        return False
    return False


async def trigger_automation_run(
    workspace_id: str,
    workflow_id: str,
    actor_id: int,
    *,
    event_data: dict[str, Any] | None = None,
    idempotency_key: str = "",
    allowed_scopes: set[str] | None = None,
    _depth: int = 0,
) -> dict[str, Any]:
    """Execute a workflow with idempotency, rate cap, and recursion-loop prevention (`_depth <= 1`)."""
    effective_depth = max(int(_depth), int((event_data or {}).get("_depth") or 0))
    if effective_depth > 1:
        return {"workflow_id": workflow_id, "status": "blocked_loop_guard", "blocked": True}

    wf = await db_models.automations_col().find_one({"workflow_id": workflow_id, "workspace_id": workspace_id})
    if not wf:
        raise KeyError("Workflow not found in this workspace.")
    required = automation_action_scopes(wf.get("actions"))
    if not required.issubset(allowed_scopes or set()):
        raise PermissionError("Additional workspace permissions are required for this workflow's actions.")
    if wf.get("bot_id") and wf["bot_id"] != "all":
        await services_bots.get_owned_workspace_bot(workspace_id, wf["bot_id"])

    idem = (idempotency_key or "").strip()
    if idem:
        dedupe_token = f"{workspace_id}:{workflow_id}:{idem}"
        if dedupe_token in _automation_seen_keys:
            return {"workflow_id": workflow_id, "status": "skipped_duplicate", "skipped_idempotent": True}
        if len(_automation_seen_keys) > 5000:
            _automation_seen_keys.clear()
        _automation_seen_keys.add(dedupe_token)

    # Hourly execution cap (max 30 runs/hour per workflow)
    cutoff_hr = time.time() - 3600
    recent_runs = await db_models.automation_runs_col().count_documents(
        {"workspace_id": workspace_id, "workflow_id": workflow_id, "created_ts": {"$gte": cutoff_hr}}
    )
    if recent_runs >= 30:
        raise services_bots.SaaSValidationError("Workflow hourly execution cap (30/hr) reached.")

    ev = event_data or {}
    conds_ok = all(_evaluate_condition(c, ev) for c in (wf.get("conditions") or []))
    results: list[dict[str, Any]] = []
    status = "completed"
    err_txt = ""

    if not conds_ok:
        status = "conditions_not_met"
    else:
        for act in wf.get("actions") or []:
            atype = act["type"]
            if atype == "create_notification":
                ntf = await db_models.create_notification(
                    workspace_id,
                    bot_id=wf.get("bot_id", ""),
                    category="automation",
                    title=act.get("title") or wf["name"],
                    message=f"Workflow '{wf['name']}' triggered on '{wf['trigger']}'.",
                    severity="info",
                )
                results.append({"action": atype, "notification_id": ntf["notification_id"]})
            elif atype == "record_activity":
                await models_db.log_audit(actor_id, "automation_activity", target=f"{workspace_id}:{workflow_id}", detail=wf["trigger"])
                results.append({"action": atype, "recorded": True})
            elif atype == "generate_report":
                anl = await services_clone_files.compute_owner_and_bot_analytics(workspace_id, actor_id, days=7)
                results.append({"action": atype, "stored_files": anl["totals"]["stored_files"]})
            elif atype == "inspect_eligible_files":
                intel = await services_clone_files.compute_file_intelligence(workspace_id, actor_id)
                results.append({"action": atype, "duplicate_groups": len(intel["duplicate_candidates"]), "advisory_only": True})

    now = time.time()
    run_doc = {
        "run_id": f"wfr_{secrets.token_hex(6)}",
        "workflow_id": workflow_id,
        "workspace_id": workspace_id,
        "trigger": wf["trigger"],
        "status": status,
        "results": results,
        "error": err_txt,
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
    }
    await db_models.automation_runs_col().insert_one(dict(run_doc))
    await db_models.automations_col().update_one({"workspace_id": workspace_id, "workflow_id": workflow_id}, {"$inc": {"run_count": 1}})
    run_doc.pop("_id", None)
    return run_doc


# ─────────────────────────── 3. Template Library & Marketplace (Section 20) ───────────────────────────
async def list_templates(workspace_id: str, *, category: str = "") -> dict[str, Any]:
    custom = await db_models.templates_col().find(
        {"$or": [{"workspace_id": workspace_id}, {"marketplace_status": "approved"}]}
    ).to_list(length=200)
    for t in custom:
        t.pop("_id", None)
    official = copy.deepcopy(OFFICIAL_TEMPLATES)
    if category and category != "all":
        official = [t for t in official if t["category"] == category]
        custom = [t for t in custom if t.get("category") == category]
    return {"official": official, "workspace_templates": custom}


async def upsert_template(workspace_id: str, actor_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if len(name) < 2 or len(name) > 80:
        raise services_bots.SaaSValidationError("Template name must be 2–80 characters.")
    cat = str(payload.get("category") or "bot_config")
    if cat not in TEMPLATE_CATEGORIES:
        raise services_bots.SaaSValidationError(f"Unsupported template category '{cat}'.")
    raw_body = payload.get("payload")
    if not isinstance(raw_body, dict):
        raise services_bots.SaaSValidationError("Template payload must be a declarative JSON object (code is forbidden).")

    tid = str(payload.get("template_id") or f"tpl_{secrets.token_hex(6)}")
    now = time.time()
    doc = {
        "template_id": tid,
        "workspace_id": workspace_id,
        "owner_id": int(actor_id),
        "name": name,
        "category": cat,
        "official": False,
        "publisher": str(payload.get("publisher") or f"User {actor_id}")[:60],
        "version": str(payload.get("version") or "1.0.0")[:20],
        "description": str(payload.get("description") or "")[:280],
        "payload": db_models.strip_sensitive_fields(raw_body),
        "marketplace_status": "pending_review" if payload.get("submit_marketplace") else "private",
        "reports": 0,
        "updated_at": db_models.utcnow_iso(),
        "created_ts": now,
    }
    await db_models.templates_col().update_one({"template_id": tid, "workspace_id": workspace_id}, {"$set": doc}, upsert=True)
    doc.pop("_id", None)
    return doc


# ─────────────────────────── 4. Plugin & Extension Center (Section 21) ───────────────────────────
def list_plugins_status() -> dict[str, Any]:
    from core.plugin_manager import plugin_manager
    return plugin_manager.health_report()


async def toggle_or_configure_plugin(
    actor_id: int, plugin_name: str, *, enabled: bool | None = None, config_patch: dict[str, Any] | None = None
) -> dict[str, Any]:
    from core.plugin_manager import plugin_manager
    name = plugin_name.strip().lower()
    if enabled is not None:
        await plugin_manager.set_enabled(name, bool(enabled))
        await models_db.log_audit(actor_id, "plugin_toggle", actor_role="admin", target=name, detail=str(bool(enabled)))
    if isinstance(config_patch, dict) and config_patch:
        await plugin_manager.configure(name, config_patch)
        await models_db.log_audit(actor_id, "plugin_configure", actor_role="admin", target=name)
    return plugin_manager.health_report()


# ─────────────────────────── 5. Developer Center & Scoped API Keys (Section 23) ───────────────────────────
async def _validate_api_key_scopes(workspace_id: str, actor_id: int, scopes: list[str]) -> list[str]:
    if not isinstance(scopes, list) or not scopes or any(
        not isinstance(scope, str) or scope not in db_models.PERMISSION_SCOPES for scope in scopes
    ):
        raise services_bots.SaaSValidationError("Select valid permission scopes for the API key.")
    member = await db_models.get_membership(workspace_id, actor_id)
    if not member or db_models.ROLE_RANK.get(member.get("role"), 0) < db_models.ROLE_RANK["administrator"]:
        raise PermissionError("An administrator workspace membership is required to manage API keys.")
    if any(not member["permissions"].get(scope, False) for scope in scopes):
        raise PermissionError("Cannot grant API key permissions you do not possess.")
    return list(dict.fromkeys(scopes))


async def create_scoped_api_key(
    workspace_id: str,
    owner_id: int,
    *,
    name: str,
    scopes: list[str],
    expires_in_days: int = 90,
) -> dict[str, Any]:
    """Create a scoped API key. Returns `raw_key` ONCE; persists only `key_hash`!"""
    clean_name = (name or "").strip()
    if len(clean_name) < 2 or len(clean_name) > 64:
        raise services_bots.SaaSValidationError("API key name must be between 2 and 64 characters.")
    valid_scopes = await _validate_api_key_scopes(workspace_id, owner_id, scopes)

    raw_key = f"vdk_{secrets.token_urlsafe(30)}"
    key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    now = time.time()
    days = max(1, min(365, int(expires_in_days or 90)))
    key_id = f"key_{secrets.token_hex(6)}"

    doc = {
        "key_id": key_id,
        "workspace_id": workspace_id,
        "owner_id": int(owner_id),
        "name": clean_name,
        "key_prefix": raw_key[:10] + "…",
        "key_hash": key_hash,
        "scopes": valid_scopes,
        "usage_count": 0,
        "revoked": False,
        "created_at": db_models.utcnow_iso(),
        "created_ts": now,
        "expires_ts": now + days * 86400.0,
    }
    await db_models.api_keys_col().insert_one(dict(doc))
    await models_db.log_audit(owner_id, "api_key_create", target=f"{workspace_id}:{key_id}", detail=",".join(valid_scopes))
    clean_key = {k: v for k, v in doc.items() if k not in ("_id", "key_hash")}
    out = dict(clean_key)
    out["key"] = clean_key
    out["raw_key_once"] = raw_key
    out["notice"] = "Copy this key now — for security it is hashed at rest and will never be shown again."
    return out


async def list_api_keys(workspace_id: str) -> list[dict[str, Any]]:
    rows = await db_models.api_keys_col().find({"workspace_id": workspace_id}).sort("created_ts", -1).to_list(length=100)
    out = []
    for r in rows:
        r.pop("_id", None)
        r.pop("key_hash", None)
        out.append(r)
    return out


async def revoke_or_rotate_api_key(
    workspace_id: str, key_id: str, actor_id: int, *, rotate: bool = False
) -> dict[str, Any]:
    existing = await db_models.api_keys_col().find_one({"key_id": key_id, "workspace_id": workspace_id})
    if not existing:
        raise KeyError("API key not found in this workspace.")
    if rotate:
        # Validate before revocation so a caller cannot rotate a broader key
        # into their own identity or accidentally invalidate it on denial.
        await _validate_api_key_scopes(workspace_id, actor_id, existing.get("scopes") or [])
    await db_models.api_keys_col().update_one(
        {"key_id": key_id, "workspace_id": workspace_id},
        {"$set": {"revoked": True, "revoked_at": db_models.utcnow_iso()}},
    )
    await models_db.log_audit(actor_id, "api_key_rotate" if rotate else "api_key_revoke", target=f"{workspace_id}:{key_id}")
    if rotate:
        return await create_scoped_api_key(
            workspace_id,
            actor_id,
            name=f"{existing['name']} (Rotated)",
            scopes=existing.get("scopes") or ["analytics"],
        )
    return {"key_id": key_id, "revoked": True}


# ─────────────────────────── 6. Safe Export, Import & Config Backup (Section 28) ───────────────────────────
BUNDLE_FORMAT = "videl-saas-bundle/1"


async def export_workspace_bundle(workspace_id: str, user_id: int) -> dict[str, Any]:
    bots = await services_bots.list_workspace_bots(workspace_id, user_id=user_id, include_archived=True)
    clean_bots = []
    for b in bots:
        cmds = await services_bots.list_bot_commands(workspace_id, b["bot_id"])
        fs_cfg = await services_clone_files.get_filestore_config(workspace_id, b["bot_id"])
        clean_bots.append({
            "bot_id": b["bot_id"],
            "display_name": b["display_name"],
            "bot_type": b["bot_type"],
            "description": b.get("description", ""),
            "features": db_models.strip_sensitive_fields(b.get("features") or {}),
            "settings": db_models.strip_sensitive_fields(b.get("settings") or {}),
            "commands": cmds,
            "filestore_config": db_models.strip_sensitive_fields(fs_cfg),
        })
    tpls = (await list_templates(workspace_id))["workspace_templates"]
    autos = await db_models.automations_col().find({"workspace_id": workspace_id}).to_list(length=100)
    for a in autos:
        a.pop("_id", None)
    envelope = {
        "format": BUNDLE_FORMAT,
        "exported_at": db_models.utcnow_iso(),
        "secrets_included": False,
        "workspace_id": workspace_id,
        "bots": clean_bots,
        "templates": db_models.strip_sensitive_fields(tpls),
        "automations": db_models.strip_sensitive_fields(autos),
    }
    raw_bytes = json.dumps(envelope, sort_keys=True).encode("utf-8")
    envelope["checksum_sha256"] = hashlib.sha256(raw_bytes).hexdigest()

    await db_models.backups_meta_col().insert_one({
        "backup_id": f"bkp_{secrets.token_hex(6)}",
        "workspace_id": workspace_id,
        "actor_id": int(user_id),
        "bots_count": len(clean_bots),
        "templates_count": len(tpls),
        "automations_count": len(autos),
        "checksum_sha256": envelope["checksum_sha256"],
        "created_at": db_models.utcnow_iso(),
        "created_ts": time.time(),
    })
    return envelope


async def import_workspace_bundle(
    workspace_id: str, actor_id: int, bundle: dict[str, Any], *, confirmed: bool = False
) -> dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT:
        raise services_bots.SaaSValidationError(f"Unsupported bundle format (expected '{BUNDLE_FORMAT}').")
    clean = db_models.strip_sensitive_fields(bundle)
    bots_in = clean.get("bots") or []
    tpls_in = clean.get("templates") or []
    autos_in = clean.get("automations") or []

    existing_bots = {b["display_name"]: b for b in await services_bots.list_workspace_bots(workspace_id, user_id=actor_id, include_archived=True)}
    conflicts = [b.get("display_name") for b in bots_in if b.get("display_name") in existing_bots]

    if confirmed is not True:
        return {
            "preview": True,
            "requires_confirmation": True,
            "valid": True,
            "bots_count": len(bots_in),
            "templates_count": len(tpls_in),
            "automations_count": len(autos_in),
            "conflicts": conflicts,
        }

    for t in tpls_in:
        if isinstance(t, dict) and t.get("name") and isinstance(t.get("payload"), dict):
            await upsert_template(workspace_id, actor_id, t)
    for a in autos_in:
        if isinstance(a, dict) and a.get("name") and a.get("trigger"):
            a_copy = dict(a)
            a_copy.pop("workflow_id", None)
            await upsert_automation_workflow(workspace_id, actor_id, a_copy)
    await models_db.log_audit(actor_id, "workspace_bundle_import", target=workspace_id)
    return {
        "preview": False,
        "imported": True,
        "templates_imported": len(tpls_in),
        "automations_imported": len(autos_in),
    }


# ─────────────────────────── 7. Global Workspace Search (Section 27) ───────────────────────────
async def global_workspace_search(workspace_id: str, user_id: int, query: str) -> dict[str, Any]:
    """Permission-aware global search across bots, files, commands, templates, tasks, and notifications."""
    q = (query or "").strip().lower()
    if not q:
        return {"query": "", "results": []}

    membership = await db_models.get_membership(workspace_id, user_id)
    if not membership:
        raise PermissionError("Workspace membership required.")
    permissions = membership.get("permissions") or {}
    results: list[dict[str, Any]] = []
    bots = await services_bots.list_workspace_bots(workspace_id, user_id=user_id)
    for b in bots[:300]:
        if q in f"{b['display_name']} {b['username']} {b['category']}".lower():
            results.append({"type": "bot", "id": b["bot_id"], "title": b["display_name"], "subtitle": f"@{b['username']} · {b['category']}", "studio": "bots"})
        if permissions.get("commands"):
            for c in (await services_bots.list_bot_commands(workspace_id, b["bot_id"], search=q))[:5]:
                results.append({"type": "command", "id": c["command"], "title": f"/{c['command']}", "subtitle": f"{b['display_name']} · {c['description']}", "studio": "commands"})

    if permissions.get("filestore"):
        files = await services_clone_files.query_filestore_files(workspace_id, user_id=user_id, search=q, page_size=8)
        for f in files["items"]:
            results.append({"type": "file", "id": f["file_uid"], "title": f["filename"], "subtitle": f"{f['category']} · {f['size_bytes']} B", "studio": "filestore"})

    if permissions.get("templates"):
        tpls = await list_templates(workspace_id)
        for t in [*tpls["official"], *tpls["workspace_templates"]]:
            if q in t["name"].lower() or q in t.get("description", "").lower():
                results.append({"type": "template", "id": t["template_id"], "title": t["name"], "subtitle": t["category"], "studio": "templates"})

    # User-scoped download tasks have no workspace ownership field, so they
    # cannot safely be surfaced in a workspace search.
    return {"query": query, "results": results[:30]}


# ─────────────────────────── 8. Demo Workspace Seeder & Platform Admin (Sections 29 & 30) ───────────────────────────
async def seed_demo_workspace_if_empty(workspace_id: str, demo_user_id: int) -> None:
    """Populate clearly-labeled DEMO sample bots, files, and automations ONLY inside a demo workspace."""
    existing = await db_models.bots_col().count_documents({"workspace_id": workspace_id})
    if existing > 0:
        return
    b1 = await services_bots.create_or_register_bot(
        workspace_id,
        demo_user_id,
        {
            "display_name": "[DEMO] Cloud Vault Bot",
            "username": "demo_vault_bot",
            "template": "filestore",
            "tags": ["demo", "filestore"],
            "fleet_group": "Demo Fleet",
        },
        allow_unverified_offline=True,
    )
    await services_bots.create_or_register_bot(
        workspace_id,
        demo_user_id,
        {
            "display_name": "[DEMO] Community Guard Bot",
            "username": "demo_guard_bot",
            "template": "group_management",
            "tags": ["demo", "moderation"],
            "fleet_group": "Demo Fleet",
        },
        allow_unverified_offline=True,
    )
    await services_clone_files.register_filestore_entry(
        workspace_id,
        demo_user_id,
        {
            "bot_id": b1["bot_id"],
            "filename": "DEMO_Architecture_Guide.pdf",
            "size_bytes": 1_450_000,
            "mime_type": "application/pdf",
            "category": "pdf",
            "tags": ["demo", "guide"],
            "description": "Sample demo document (clearly labeled demo data).",
            "sha256": "demo_sha256_guide_001",
        },
    )
    await services_clone_files.register_filestore_entry(
        workspace_id,
        demo_user_id,
        {
            "bot_id": b1["bot_id"],
            "filename": "DEMO_Release_Bundle_v2.zip",
            "size_bytes": 8_240_000,
            "mime_type": "application/zip",
            "category": "archive",
            "tags": ["demo", "release"],
            "description": "Sample demo archive entry.",
            "sha256": "demo_sha256_bundle_002",
        },
    )
    await upsert_automation_workflow(
        workspace_id,
        demo_user_id,
        {
            "name": "[DEMO] Large Upload Alert",
            "bot_id": b1["bot_id"],
            "trigger": "file_uploaded",
            "conditions": [{"field": "size_mb", "op": ">=", "value": 5}],
            "actions": [{"type": "create_notification", "title": "Demo upload processed"}],
            "enabled": True,
        },
    )


async def platform_admin_overview() -> dict[str, Any]:
    """Platform-wide statistics & health strictly restricted to platform administrators (Section 29)."""
    ws_count = await db_models.workspaces_col().count_documents({"deleted": {"$ne": True}})
    bots_count = await db_models.bots_col().count_documents({"deleted": {"$ne": True}})
    files_count = await db_models.files_col().count_documents({"deleted": False})
    autos_count = await db_models.automations_col().count_documents({})
    audit_logs = await models_db.list_audit_logs(limit=60)
    diag = diagnostics.collect_diagnostics()

    users_total = 0
    banned_total = 0
    try:
        from core.db import vdb as core_vdb
        users_total = await core_vdb.users.count_documents({})
        banned_total = len(getattr(core_vdb, "_banned", set()))
    except Exception:
        pass

    return {
        "counts": {
            "platform_users": users_total,
            "banned_users": banned_total,
            "workspaces": ws_count,
            "registered_bots": bots_count,
            "indexed_files": files_count,
            "automations": autos_count,
        },
        "database_connected": models_db.is_mongodb_backend(),
        "maintenance_mode": bool(getattr(config, "MAINTENANCE", False)),
        "diagnostics": diag,
        "audit_logs": audit_logs,
    }

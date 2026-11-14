"""
MongoDB Schema, Indexes & Multi-Workspace Persistence Layer (`VidelBot/webapp/db_models.py`).

Uses Videl1's shared `vdb.db` Motor database (`core.db.vdb`) alongside existing `MainDB` /
`WorkerDB` / `CloneExtras` collections without replacing MongoDB or storing raw secrets.
When `MONGO_URI` is unset (standalone local preview), falls back transparently to bounded
in-memory collections while reporting `backend_mode = "standalone_memory"`.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import config
from core.db import vdb

# ─────────────────────────── RBAC Roles & Permission Scopes (Section 22) ───────────────────────────
WORKSPACE_ROLES = ("owner", "administrator", "manager", "developer", "analyst", "viewer")

ROLE_RANK: dict[str, int] = {
    "owner": 60,
    "administrator": 50,
    "manager": 40,
    "developer": 30,
    "analyst": 20,
    "viewer": 10,
}

PERMISSION_SCOPES = (
    "bot_settings",
    "commands",
    "filestore",
    "analytics",
    "integrations",
    "logs",
    "templates",
    "cloning",
    "automation",
    "team_management",
)

DEFAULT_ROLE_PERMISSIONS: dict[str, dict[str, bool]] = {
    "owner": {s: True for s in PERMISSION_SCOPES},
    "administrator": {s: True for s in PERMISSION_SCOPES},
    "manager": {
        "bot_settings": True,
        "commands": True,
        "filestore": True,
        "analytics": True,
        "integrations": False,
        "logs": True,
        "templates": True,
        "cloning": True,
        "automation": True,
        "team_management": False,
    },
    "developer": {
        "bot_settings": True,
        "commands": True,
        "filestore": False,
        "analytics": True,
        "integrations": True,
        "logs": True,
        "templates": True,
        "cloning": True,
        "automation": True,
        "team_management": False,
    },
    "analyst": {
        "bot_settings": False,
        "commands": False,
        "filestore": False,
        "analytics": True,
        "integrations": False,
        "logs": True,
        "templates": False,
        "cloning": False,
        "automation": False,
        "team_management": False,
    },
    "viewer": {**{s: False for s in PERMISSION_SCOPES}, "analytics": True},
}

NEVER_CLONE_OR_EXPORT_KEYS = frozenset({
    "bot_token",
    "bot_token_encrypted",
    "token",
    "token_sealed",
    "access_token",
    "api_key",
    "api_key_encrypted",
    "api_hash",
    "session",
    "session_string",
    "secret",
    "webhook_secret",
    "payment_secret",
    "provider_token",
    "encryption_key",
    "owner_id",
    "users",
    "purchases",
    "payments",
})


def strip_sensitive_fields(obj: Any) -> Any:
    """Recursively strip credentials, tokens, payment secrets, and private user lists."""
    if isinstance(obj, dict):
        cleaned: dict[str, Any] = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in NEVER_CLONE_OR_EXPORT_KEYS or any(
                s in kl for s in ("token", "secret", "password", "session", "api_key", "private_key")
            ):
                continue
            cleaned[k] = strip_sensitive_fields(v)
        return cleaned
    if isinstance(obj, list):
        return [strip_sensitive_fields(x) for x in obj]
    return obj


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ─────────────────────────── Standalone Memory Fallback (only when MONGO_URI is empty) ───────────────────────────
class _MemCursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def sort(self, field: str | list[tuple[str, int]], direction: int = 1) -> "_MemCursor":
        if isinstance(field, list):
            for k, d in reversed(field):
                self._docs.sort(key=lambda r: (r.get(k) is None, r.get(k, 0)), reverse=(d < 0))
        else:
            self._docs.sort(key=lambda r: (r.get(field) is None, r.get(field, 0)), reverse=(direction < 0))
        return self

    def skip(self, n: int) -> "_MemCursor":
        self._docs = self._docs[max(0, int(n)) :]
        return self

    def limit(self, n: int) -> "_MemCursor":
        self._docs = self._docs[: max(0, int(n))]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        if length is None:
            return [dict(d) for d in self._docs]
        return [dict(d) for d in self._docs[:length]]

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return dict(next(self._it))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _MemCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    @classmethod
    def _match(cls, doc: dict[str, Any], q: dict[str, Any]) -> bool:
        for k, v in (q or {}).items():
            if k == "$or":
                if not any(cls._match(doc, branch) for branch in v):
                    return False
                continue
            dv = doc.get(k)
            if isinstance(v, dict):
                if not any(str(key).startswith("$") for key in v):
                    if dv != v:
                        return False
                    continue
                if "$ne" in v and dv == v["$ne"]:
                    return False
                if "$in" in v and dv not in v["$in"]:
                    return False
                if "$nin" in v and dv in v["$nin"]:
                    return False
                if "$gte" in v and (dv is None or dv < v["$gte"]):
                    return False
                if "$lte" in v and (dv is None or dv > v["$lte"]):
                    return False
                if "$gt" in v and (dv is None or dv <= v["$gt"]):
                    return False
                if "$lt" in v and (dv is None or dv >= v["$lt"]):
                    return False
            else:
                if dv != v:
                    return False
        return True

    async def create_index(self, *_a, **_kw) -> str:
        return "mem_idx"

    async def count_documents(self, q: dict[str, Any] | None = None) -> int:
        return sum(1 for d in self.docs if self._match(d, q or {}))

    async def find_one(self, q: dict[str, Any] | None = None) -> dict[str, Any] | None:
        for d in self.docs:
            if self._match(d, q or {}):
                return dict(d)
        return None

    def find(self, q: dict[str, Any] | None = None) -> _MemCursor:
        return _MemCursor([dict(d) for d in self.docs if self._match(d, q or {})])

    async def insert_one(self, doc: dict[str, Any]):
        copy_doc = dict(doc)
        copy_doc.setdefault("_id", secrets.token_hex(8))
        self.docs.append(copy_doc)
        return type("Res", (), {"inserted_id": copy_doc["_id"]})()

    async def update_one(self, q: dict[str, Any], upd: dict[str, Any], upsert: bool = False):
        for d in self.docs:
            if self._match(d, q):
                self._apply_update(d, upd)
                return type("Res", (), {"modified_count": 1, "matched_count": 1})()
        if upsert:
            base = {k: v for k, v in q.items() if not k.startswith("$") and not isinstance(v, dict)}
            base.setdefault("_id", secrets.token_hex(8))
            self._apply_update(base, upd, inserting=True)
            self.docs.append(base)
            return type("Res", (), {"modified_count": 1, "matched_count": 0, "upserted_id": base["_id"]})()
        return type("Res", (), {"modified_count": 0, "matched_count": 0})()

    async def update_many(self, q: dict[str, Any], upd: dict[str, Any]):
        cnt = 0
        for d in self.docs:
            if self._match(d, q):
                self._apply_update(d, upd)
                cnt += 1
        return type("Res", (), {"modified_count": cnt})()

    @staticmethod
    def _apply_update(target: dict[str, Any], upd: dict[str, Any], inserting: bool = False) -> None:
        if "$set" in upd:
            target.update(upd["$set"])
        if inserting and "$setOnInsert" in upd:
            for k, v in upd["$setOnInsert"].items():
                target.setdefault(k, v)
        if "$unset" in upd:
            for k in upd["$unset"]:
                target.pop(k, None)
        if "$inc" in upd:
            for k, v in upd["$inc"].items():
                target[k] = (target.get(k) or 0) + v

    async def delete_one(self, q: dict[str, Any]):
        for i, d in enumerate(self.docs):
            if self._match(d, q):
                self.docs.pop(i)
                return type("Res", (), {"deleted_count": 1})()
        return type("Res", (), {"deleted_count": 0})()

    async def delete_many(self, q: dict[str, Any]):
        before = len(self.docs)
        self.docs = [d for d in self.docs if not self._match(d, q)]
        return type("Res", (), {"deleted_count": before - len(self.docs)})()


_mem_cols: dict[str, _MemCollection] = {}


def col(name: str):
    """Return Motor collection from `vdb.db` (or standalone preview collection when `MONGO_URI` is unset)."""
    try:
        return vdb.db[name]
    except Exception:
        if getattr(config, "MONGO_URI", ""):
            raise
        if name not in _mem_cols:
            _mem_cols[name] = _MemCollection()
        return _mem_cols[name]


def reset_memory_collections() -> None:
    _mem_cols.clear()


# Collection accessors
def workspaces_col():
    return col("saas_workspaces")


def memberships_col():
    return col("saas_memberships")


def invites_col():
    return col("saas_invites")


def bots_col():
    return col("saas_bots")


def bot_envs_col():
    return col("saas_bot_environments")


def bot_revs_col():
    return col("saas_bot_revisions")


def bot_commands_col():
    return col("saas_bot_commands")


def clones_col():
    return col("saas_clone_ops")


def migrations_col():
    return col("saas_migrations")


def files_col():
    return col("saas_files")


def filestore_cfg_col():
    return col("saas_filestore_configs")


def templates_col():
    return col("saas_templates")


def automations_col():
    return col("saas_automations")


def automation_runs_col():
    return col("saas_automation_runs")


def webhooks_col():
    return col("saas_webhooks")


def webhook_events_col():
    return col("saas_webhook_events")


def api_keys_col():
    return col("saas_api_keys")


def notifications_col():
    return col("saas_notifications")


def user_prefs_col():
    return col("saas_user_prefs")


def backups_meta_col():
    return col("saas_config_backups")


async def ensure_saas_indexes() -> None:
    """Create indexes and unique constraints across all SaaS collections idempotently."""
    specs = [
        (workspaces_col(), "workspace_id", {"unique": True}),
        (workspaces_col(), [("owner_id", 1), ("archived", 1)], {}),
        (memberships_col(), [("workspace_id", 1), ("user_id", 1)], {"unique": True}),
        (memberships_col(), "user_id", {}),
        (invites_col(), "invite_id", {"unique": True}),
        (invites_col(), [("workspace_id", 1), ("status", 1)], {}),
        (bots_col(), "bot_id", {"unique": True}),
        (bots_col(), [("workspace_id", 1), ("archived", 1)], {}),
        (bots_col(), "owner_id", {}),
        (bot_envs_col(), [("bot_id", 1), ("env", 1)], {"unique": True}),
        (bot_revs_col(), [("bot_id", 1), ("version", -1)], {}),
        (bot_commands_col(), [("bot_id", 1), ("command", 1)], {"unique": True}),
        (clones_col(), "clone_id", {"unique": True}),
        (clones_col(), [("workspace_id", 1), ("created_ts", -1)], {}),
        (migrations_col(), "migration_id", {"unique": True}),
        (files_col(), "file_uid", {"unique": True}),
        (files_col(), [("workspace_id", 1), ("bot_id", 1), ("deleted", 1)], {}),
        (files_col(), [("workspace_id", 1), ("sha256", 1)], {}),
        (filestore_cfg_col(), [("workspace_id", 1), ("bot_id", 1)], {"unique": True}),
        (templates_col(), "template_id", {"unique": True}),
        (templates_col(), [("workspace_id", 1), ("category", 1)], {}),
        (automations_col(), "workflow_id", {"unique": True}),
        (automations_col(), [("workspace_id", 1), ("enabled", 1)], {}),
        (automation_runs_col(), [("workflow_id", 1), ("created_ts", -1)], {}),
        (webhooks_col(), "webhook_id", {"unique": True}),
        (webhook_events_col(), [("workspace_id", 1), ("created_ts", -1)], {}),
        (api_keys_col(), "key_id", {"unique": True}),
        (api_keys_col(), "key_hash", {"unique": True}),
        (notifications_col(), [("workspace_id", 1), ("created_ts", -1)], {}),
        (user_prefs_col(), "user_id", {"unique": True}),
        (backups_meta_col(), "backup_id", {"unique": True}),
    ]
    for c, keys, kw in specs:
        try:
            await c.create_index(keys, **kw)
        except Exception:
            pass


# ─────────────────────────── Workspace & Team Membership Helpers ───────────────────────────
def is_demo_workspace(workspace: dict[str, Any]) -> bool:
    """Recognize labeled demos and legacy synthetic-owner workspaces."""
    try:
        owner_id = int(workspace.get("owner_id"))
    except (TypeError, ValueError, OverflowError):
        owner_id = None
    return bool(workspace.get("is_demo")) or (owner_id is not None and owner_id <= 0)


async def ensure_default_workspace(user_id: int, *, display_name: str = "", is_demo: bool = False) -> dict[str, Any]:
    """Provision an owner workspace without overwriting existing memberships.

    A collaborator's membership is not their personal workspace. Reuse an
    existing workspace only if both its owner record and membership agree.
    Otherwise a deterministic ID plus unique indexes lets concurrent requests
    recover a partially created workspace rather than creating several.
    """
    from pymongo.errors import DuplicateKeyError

    uid = int(user_id)
    members = memberships_col()
    workspaces = workspaces_col()
    # The preview-only memory adapter does not enforce Mongo unique indexes.
    # Automatic account provisioning must never pretend those guarantees exist.
    if isinstance(members, _MemCollection) or isinstance(workspaces, _MemCollection):
        raise RuntimeError("MongoDB is required for automatic workspace provisioning")
    owned_memberships = await members.find({"user_id": uid, "role": "owner"}).to_list(length=100)
    for membership in owned_memberships:
        ws = await workspaces.find_one({
            "workspace_id": membership["workspace_id"], "owner_id": uid, "deleted": {"$ne": True},
        })
        if ws and is_demo_workspace(ws) == bool(is_demo):
            ws.pop("_id", None)
            return ws

    # This ID is an idempotency key, not an authorization token; every access
    # still requires membership. Avoid placing the Telegram ID directly in it.
    digest = hashlib.sha256(f"videl-personal-workspace:{uid}".encode()).hexdigest()[:32]
    ws_id = f"ws_personal_{digest}"
    now = time.time()
    name = "Demo Sandbox Workspace" if is_demo else f"{display_name or 'Personal'} Workspace"
    ws_doc = {
        "workspace_id": ws_id,
        "name": name[:80],
        "icon": "🧪" if is_demo else "🚀",
        "description": "Isolated demonstration workspace with sample data" if is_demo else "Primary multi-bot workspace",
        "owner_id": uid,
        "is_demo": bool(is_demo),
        "archived": False,
        "deleted": False,
        "created_at": utcnow_iso(),
        "created_ts": now,
        "updated_ts": now,
    }
    # Fail closed if duplicates or database permissions prevent the necessary
    # constraints. Never change or resurrect a conflicting existing workspace.
    await workspaces.create_index("workspace_id", unique=True)
    await members.create_index([("workspace_id", 1), ("user_id", 1)], unique=True)
    try:
        await workspaces.insert_one(dict(ws_doc))
    except DuplicateKeyError:
        pass
    ws = await workspaces.find_one({"workspace_id": ws_id})
    if (not ws or ws.get("owner_id") != uid or ws.get("deleted")
            or bool(ws.get("is_demo")) != bool(is_demo)):
        raise RuntimeError("Personal workspace identity conflict; operator review required.")

    mem_doc = {
        "workspace_id": ws_id,
        "user_id": uid,
        "role": "owner",
        "permissions": dict(DEFAULT_ROLE_PERMISSIONS["owner"]),
        "joined_at": utcnow_iso(),
        "joined_ts": now,
    }
    try:
        await members.insert_one(mem_doc)
    except DuplicateKeyError:
        pass
    membership = await members.find_one({"workspace_id": ws_id, "user_id": uid})
    if not membership or membership.get("role") != "owner":
        raise RuntimeError("Personal workspace membership conflict; operator review required.")
    ws.pop("_id", None)
    return ws


async def list_user_workspaces(user_id: int) -> list[dict[str, Any]]:
    uid = int(user_id)
    await ensure_default_workspace(uid, is_demo=uid < 0)
    mems = await memberships_col().find({"user_id": uid}).to_list(length=100)
    out: list[dict[str, Any]] = []
    for m in mems:
        ws = await workspaces_col().find_one(
            {"workspace_id": m["workspace_id"], "deleted": {"$ne": True}}
        )
        if not ws:
            continue
        ws.pop("_id", None)
        ws["my_role"] = m.get("role", "viewer")
        ws["my_permissions"] = m.get("permissions") or dict(DEFAULT_ROLE_PERMISSIONS.get(ws["my_role"], {}))
        out.append(ws)
    out.sort(key=lambda w: (bool(w.get("archived")), -float(w.get("created_ts", 0))))
    return out


async def get_membership(workspace_id: str, user_id: int) -> dict[str, Any] | None:
    m = await memberships_col().find_one({"workspace_id": str(workspace_id), "user_id": int(user_id)})
    if not m:
        return None
    ws = await workspaces_col().find_one({"workspace_id": str(workspace_id), "deleted": {"$ne": True}})
    if not ws:
        return None
    m.pop("_id", None)
    role = m.get("role", "viewer")
    perms = dict(DEFAULT_ROLE_PERMISSIONS.get(role, DEFAULT_ROLE_PERMISSIONS["viewer"]))
    if isinstance(m.get("permissions"), dict):
        perms.update({k: bool(v) for k, v in m["permissions"].items() if k in PERMISSION_SCOPES})
    if role == "owner":
        perms = dict(DEFAULT_ROLE_PERMISSIONS["owner"])
    m["permissions"] = perms
    m["workspace"] = {k: v for k, v in ws.items() if k != "_id"}
    return m


async def create_notification(
    workspace_id: str,
    *,
    category: str,
    title: str,
    message: str,
    severity: str = "info",
    bot_id: str = "",
) -> dict[str, Any]:
    now = time.time()
    doc = {
        "notification_id": f"ntf_{int(now * 1000)}_{secrets.token_hex(3)}",
        "workspace_id": str(workspace_id),
        "bot_id": str(bot_id or ""),
        "category": str(category)[:40],
        "severity": severity if severity in ("info", "success", "warning", "error") else "info",
        "title": str(title)[:140],
        "message": str(message)[:500],
        "read": False,
        "created_at": utcnow_iso(),
        "created_ts": now,
    }
    await notifications_col().insert_one(dict(doc))
    return doc

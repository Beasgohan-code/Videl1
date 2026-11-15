"""
Authentication, CSRF, Rate-Limiting, Scoped API Keys & Workspace Isolation (`VidelBot/webapp/auth.py`).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

import config
from platform_core import models_db
from webapp import db_models

SESSION_MAX = 4000
LINK_TTL = 600.0
LINK_MAX = 1000
AUTH_FAIL_WINDOW = 600.0
AUTH_FAIL_MAX = 10
API_RATE_WINDOW = 60.0
API_RATE_MAX = 240

_magic_links: dict[str, tuple[float, int, str]] = {}  # sha256(key) -> (exp, user_id, name)
_sessions: dict[str, dict[str, Any]] = {}             # sha256(tok) -> session dict
_auth_fails: dict[str, list[float]] = {}              # ip -> [ts]
_api_hits: dict[str, list[float]] = {}                # subject -> [ts]


def _sha256(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def reset_auth_state() -> None:
    _magic_links.clear()
    _sessions.clear()
    _auth_fails.clear()
    _api_hits.clear()


def _prune(now: float) -> None:
    for k in [k for k, v in list(_magic_links.items()) if v[0] < now]:
        _magic_links.pop(k, None)
    for k in [k for k, v in list(_sessions.items()) if v["expires_ts"] < now]:
        _sessions.pop(k, None)


class LinkCapacityError(RuntimeError):
    """No room for a new link without invalidating existing links."""


def issue_saas_link(
    user_id: int,
    display_name: str = "",
    *,
    username: str = "",
    first_name: str = "",
) -> str:
    """Issue a 10-minute single-use sign-in token for `/app#l.<key>`."""
    resolved = display_name or first_name or (f"@{username}" if username else f"User {user_id}")
    now = time.time()
    _prune(now)
    if len(_magic_links) >= LINK_MAX:
        raise LinkCapacityError("Sign-in link capacity reached")
    key = secrets.token_urlsafe(24)
    _magic_links[_sha256(key)] = (now + LINK_TTL, int(user_id), str(resolved)[:64])
    return f"l.{key}"


def create_session(
    user_id: int,
    *,
    display_name: str = "",
    is_demo: bool = False,
    via_admin_token: bool = False,
    telegram_verified: bool = False,
) -> dict[str, Any]:
    now = time.time()
    _prune(now)
    if len(_sessions) >= SESSION_MAX:
        oldest = next(iter(_sessions))
        _sessions.pop(oldest, None)
    hours = max(1, int(getattr(config, "WEB_SESSION_HOURS", 12) or 12))
    raw_tok = "vs." + secrets.token_urlsafe(32)
    csrf_tok = secrets.token_urlsafe(24)
    uid = int(user_id)
    rec = {
        "user_id": uid,
        "display_name": str(display_name or f"User {uid}")[:64],
        "is_demo": bool(is_demo),
        "via_admin_token": bool(via_admin_token),
        "telegram_verified": bool(telegram_verified),
        "csrf_token": csrf_tok,
        "created_ts": now,
        "expires_ts": now + hours * 3600.0,
    }
    _sessions[_sha256(raw_tok)] = rec
    return {"session_token": raw_tok, "csrf_token": csrf_tok, **rec}


def revoke_session_token(raw_tok: str) -> bool:
    return _sessions.pop(_sha256(raw_tok or ""), None) is not None


@dataclass
class AuthContext:
    user_id: int
    display_name: str
    is_platform_admin: bool
    is_demo: bool = False
    csrf_token: str = ""
    auth_kind: str = "session"  # session | api_key | admin_token
    telegram_verified: bool = False
    api_key_workspace_id: str = ""
    api_key_scopes: set[str] = field(default_factory=set)


def _client_ip(request: web.Request) -> str:
    return request.remote or "unknown"


def check_login_rate_limit(request: web.Request) -> None:
    ip = _client_ip(request)
    now = time.time()
    recent = [t for t in _auth_fails.get(ip, []) if now - t < AUTH_FAIL_WINDOW]
    _auth_fails[ip] = recent
    if len(recent) >= AUTH_FAIL_MAX:
        raise web.HTTPTooManyRequests(
            text='{"error":"Too many failed sign-in attempts. Please wait 10 minutes."}',
            content_type="application/json",
        )


def record_login_failure(request: web.Request) -> None:
    ip = _client_ip(request)
    now = time.time()
    _auth_fails.setdefault(ip, []).append(now)
    if len(_auth_fails) > 5000:
        _auth_fails.clear()


def _check_api_burst(subject: str) -> None:
    now = time.time()
    recent = [t for t in _api_hits.get(subject, []) if now - t < API_RATE_WINDOW]
    recent.append(now)
    _api_hits[subject] = recent
    if len(_api_hits) > 5000:
        _api_hits.clear()
    if len(recent) > API_RATE_MAX:
        raise web.HTTPTooManyRequests(
            text='{"error":"Rate limit exceeded. Slow down API requests."}',
            content_type="application/json",
        )


def _reject_query_secrets(request: web.Request) -> None:
    """Strictly forbid credentials in URL query strings (Section 33)."""
    forbidden_q = {"token", "api_key", "session", "session_token", "bot_token", "secret", "admin_token"}
    if forbidden_q.intersection({k.lower() for k in request.query.keys()}):
        raise web.HTTPUnauthorized(
            text='{"error":"Credentials in URL query parameters are forbidden for security.","code":"query_secret_forbidden"}',
            content_type="application/json",
        )


async def authenticate_request(request: web.Request, *, require_csrf: bool | None = None) -> AuthContext:
    """Authenticate session Bearer token, `vdk_` developer API key, or `X-Admin-Token`."""
    _reject_query_secrets(request)
    now = time.time()
    _prune(now)

    must_csrf = (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        if require_csrf is None
        else bool(require_csrf)
    )

    auth_hdr = (request.headers.get("Authorization") or "").strip()
    bearer = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else ""
    admin_hdr = (request.headers.get("X-Admin-Token") or "").strip()

    # 1. Scoped Developer API Key (`Bearer vdk_...`)
    if bearer.startswith("vdk_"):
        key_hash = _sha256(bearer)
        kdoc = await db_models.api_keys_col().find_one({"key_hash": key_hash})
        if not kdoc or kdoc.get("revoked"):
            record_login_failure(request)
            raise web.HTTPUnauthorized(
                text='{"error":"Invalid or revoked API key."}', content_type="application/json"
            )
        exp = float(kdoc.get("expires_ts") or 0)
        if exp and exp < now:
            raise web.HTTPUnauthorized(
                text='{"error":"API key has expired."}', content_type="application/json"
            )
        _check_api_burst(f"key:{kdoc['key_id']}")
        await db_models.api_keys_col().update_one(
            {"key_id": kdoc["key_id"]},
            {"$inc": {"usage_count": 1}, "$set": {"last_used_at": db_models.utcnow_iso(), "last_used_ts": now}},
        )
        # API keys are capability tokens, not interchangeable user sessions.  Deny
        # account, administration, key-management, and unclassified routes; only
        # explicitly mapped workspace endpoints may be used by a scoped key.
        parts = request.path.strip("/").split("/")
        endpoint = parts[4] if len(parts) >= 5 and parts[:3] == ["api", "v1", "workspaces"] else ""
        scope_map = {
            "bots": "bot_settings", "fleet": "bot_settings", "clones": "cloning",
            "filestore": "filestore", "file-intelligence": "analytics",
            "downloads": "filestore", "studio": "filestore", "analytics": "analytics",
            "analytics.csv": "analytics", "health": "logs", "automations": "automation",
            "schedules": "automation", "templates": "templates", "integrations": "integrations",
            "bundle": "bot_settings", "notifications": "logs",
        }
        required = scope_map.get(endpoint)
        # Bot subresources have their own permission domains.
        if endpoint == "bots" and len(parts) >= 7 and parts[6] == "commands":
            required = "commands"
        if endpoint == "health" and request.method != "GET":
            required = "integrations"
        if not required or required not in set(kdoc.get("scopes") or []):
            raise web.HTTPForbidden(text='{"error":"API key is not permitted for this endpoint."}', content_type="application/json")
        uid = int(kdoc["owner_id"])
        return AuthContext(
            user_id=uid,
            display_name=f"API Key ({kdoc.get('name', 'dev')})",
            is_platform_admin=False,
            is_demo=False,
            auth_kind="api_key",
            api_key_workspace_id=str(kdoc["workspace_id"]),
            api_key_scopes=set(kdoc.get("scopes") or []),
        )

    # 2. SaaS Web Session (`Bearer vs....`)
    if bearer.startswith("vs."):
        sess = _sessions.get(_sha256(bearer))
        if not sess or sess["expires_ts"] < now:
            raise web.HTTPUnauthorized(
                text='{"error":"Session expired or invalid. Please sign in again."}',
                content_type="application/json",
            )
        if must_csrf:
            given_csrf = (request.headers.get("X-CSRF-Token") or "").strip()
            if not given_csrf or not hmac.compare_digest(given_csrf, sess["csrf_token"]):
                raise web.HTTPForbidden(
                    text='{"error":"Missing or invalid X-CSRF-Token header."}',
                    content_type="application/json",
                )
        uid = int(sess["user_id"])
        _check_api_burst(f"u:{uid}")
        is_demo = bool(sess.get("is_demo"))
        is_admin = (not is_demo) and (
            bool(sess.get("via_admin_token")) or uid in set(getattr(config, "ADMINS", []) or [])
        )
        return AuthContext(
            user_id=uid,
            display_name=sess["display_name"],
            is_platform_admin=is_admin,
            is_demo=is_demo,
            csrf_token=sess["csrf_token"],
            auth_kind="session",
            telegram_verified=bool(sess.get("telegram_verified")) and not is_demo and not bool(sess.get("via_admin_token")),
        )

    # 3. Direct Platform Admin Token (`X-Admin-Token`)
    if admin_hdr:
        check_login_rate_limit(request)
        cfg_tok = getattr(config, "ADMIN_WEB_TOKEN", "") or ""
        if cfg_tok and hmac.compare_digest(admin_hdr.encode(), cfg_tok.encode()):
            owners = list(getattr(config, "OWNERS", []) or getattr(config, "ADMINS", []) or [1])
            uid = int(owners[0]) if owners else 1
            _check_api_burst(f"adm:{uid}")
            return AuthContext(
                user_id=uid,
                display_name="Platform Owner",
                is_platform_admin=True,
                is_demo=False,
                auth_kind="admin_token",
            )
        record_login_failure(request)

    raise web.HTTPUnauthorized(
        text='{"error":"Authentication required."}', content_type="application/json"
    )


async def require_workspace_access(
    ctx: AuthContext,
    workspace_id: str,
    *,
    scope: str | None = None,
    min_role: str | None = None,
) -> dict[str, Any]:
    """Verify workspace membership, API-key workspace pin, and granular RBAC permission."""
    ws_id = (workspace_id or "").strip()
    if not ws_id:
        raise web.HTTPBadRequest(
            text='{"error":"workspace_id is required."}', content_type="application/json"
        )
    if ctx.auth_kind == "api_key":
        if ctx.api_key_workspace_id != ws_id:
            raise web.HTTPForbidden(
                text='{"error":"API key is scoped to a different workspace."}',
                content_type="application/json",
            )
        if scope and scope not in ctx.api_key_scopes:
            raise web.HTTPForbidden(
                text=f'{{"error":"API key lacks required scope: {scope}"}}',
                content_type="application/json",
            )

    mem = await db_models.get_membership(ws_id, ctx.user_id)
    if not mem:
        raise web.HTTPForbidden(
            text='{"error":"Access denied: you are not a member of this workspace."}',
            content_type="application/json",
        )
    if min_role:
        have = db_models.ROLE_RANK.get(mem.get("role", "viewer"), 0)
        need = db_models.ROLE_RANK.get(min_role, 100)
        if have < need:
            raise web.HTTPForbidden(
                text=f'{{"error":"Requires at least {min_role} role in this workspace."}}',
                content_type="application/json",
            )
    if scope and not (mem.get("permissions") or {}).get(scope, False):
        raise web.HTTPForbidden(
            text=f'{{"error":"Missing workspace permission: {scope}"}}',
            content_type="application/json",
        )
    return mem


def require_platform_admin(ctx: AuthContext) -> None:
    if not ctx.is_platform_admin or ctx.is_demo:
        raise web.HTTPForbidden(
            text='{"error":"Platform Administration Center requires platform admin privileges."}',
            content_type="application/json",
        )


async def _logged_session(uid: int, display_name: str, event: str, *, role: str, via_admin_token: bool = False) -> dict[str, Any]:
    session = create_session(uid, display_name=display_name, via_admin_token=via_admin_token,
                             telegram_verified=role == "user" and not via_admin_token)
    try:
        await models_db.log_audit(uid, event, actor_role=role, outcome="ok")
    except BaseException:
        revoke_session_token(session["session_token"])
        raise
    return session


async def exchange_login_payload(request: web.Request, body: dict[str, Any]) -> dict[str, Any]:
    """Exchange magic link (`link`), Telegram `initData`, `admin_token`, or `keep_alive` link for a SaaS session."""
    check_login_rate_limit(request)
    now = time.time()
    _prune(now)

    # 1. SaaS magic link (`l.<key>` or `<key>`)
    link = str(body.get("link") or "").strip()
    if link.startswith("l."):
        link = link[2:]
    if link:
        rec = _magic_links.pop(_sha256(link), None)
        if rec and rec[0] > now:
            _, uid, name = rec
            return await _logged_session(uid, name, "saas_login_link", role="user")
        # Also accept one-time `/dashboard` links issued by `keep_alive.issue_link()` for seamless admin transition
        try:
            import keep_alive
            exp = keep_alive._links.pop(keep_alive._h(link), 0)
            if exp > now:
                owners = list(getattr(config, "OWNERS", []) or getattr(config, "ADMINS", []) or [1])
                uid = int(owners[0]) if owners else 1
                return await _logged_session(uid, "Platform Admin", "saas_login_dashboard_link", role="admin", via_admin_token=True)
        except Exception:
            pass
        record_login_failure(request)
        raise web.HTTPUnauthorized(
            text='{"error":"Sign-in link expired or already used. Request a fresh link from the bot."}',
            content_type="application/json",
        )

    # 2. Telegram Mini App `initData`
    init_data = str(body.get("init_data") or body.get("init") or "").strip()
    if init_data:
        import keep_alive
        tg_user = keep_alive.check_init_data(init_data, getattr(config, "BOT_TOKEN", "") or "")
        if tg_user and tg_user.get("id"):
            from webapp.telegram_proof import consume_verified_init_data
            try:
                await consume_verified_init_data(init_data, keep_alive.INIT_MAX_AGE)
            except web.HTTPUnauthorized:
                record_login_failure(request)
                raise
            uid = int(tg_user["id"])
            name = str(tg_user.get("first_name") or tg_user.get("username") or f"User {uid}")
            return await _logged_session(uid, name, "saas_login_miniapp", role="user")
        record_login_failure(request)
        raise web.HTTPUnauthorized(
            text='{"error":"Invalid or expired Telegram Mini App launch signature."}',
            content_type="application/json",
        )

    # 3. Platform Admin Token (`admin_token`)
    adm_tok = str(body.get("admin_token") or body.get("token") or "").strip()
    cfg_tok = getattr(config, "ADMIN_WEB_TOKEN", "") or ""
    if adm_tok and cfg_tok and hmac.compare_digest(adm_tok.encode(), cfg_tok.encode()):
        owners = list(getattr(config, "OWNERS", []) or getattr(config, "ADMINS", []) or [1])
        uid = int(owners[0]) if owners else 1
        return await _logged_session(uid, "Platform Owner", "saas_login_admin_token", role="owner", via_admin_token=True)

    record_login_failure(request)
    raise web.HTTPUnauthorized(
        text='{"error":"Invalid credentials."}', content_type="application/json"
    )

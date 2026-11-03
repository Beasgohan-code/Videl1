"""
REST API Router & Static Asset Delivery for Videl1 Multi-Bot SaaS Platform
(`VidelBot/webapp/api_routes.py`).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Awaitable, Callable

from aiohttp import web

import config
from platform_core import destinations, download_engine, media_studio, models_db, providers
from webapp import auth, db_models, services_bots, services_clone_files, services_ops

log = logging.getLogger("videl.webapp.api")


async def _json_body(request: web.Request) -> dict[str, Any]:
    if not request.can_read_body:
        return {}
    raw = await request.read()
    if not raw:
        return {}
    if len(raw) > 2 * 1024 * 1024:
        raise services_bots.SaaSValidationError("JSON request payload exceeds 2 MiB limit.")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise services_bots.SaaSValidationError("Malformed JSON request body.") from exc
    if not isinstance(parsed, dict):
        raise services_bots.SaaSValidationError("Request body must be a JSON object.")
    return parsed


def _security_headers(*, content_type: str = "application/json") -> dict[str, str]:
    anc = getattr(config, "WEB_FRAME_ANCESTORS", "") or "*"
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": f"frame-ancestors 'self' {anc}".strip(),
    }


def _ok(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, headers=_security_headers())


def _can_read_scope(ctx: auth.AuthContext, membership: dict[str, Any], scope: str) -> bool:
    return bool((membership.get("permissions") or {}).get(scope)) and (
        ctx.auth_kind != "api_key" or scope in ctx.api_key_scopes
    )


def _bot_for_member(bot: dict[str, Any], ctx: auth.AuthContext, membership: dict[str, Any]) -> dict[str, Any]:
    result = dict(bot)
    if not _can_read_scope(ctx, membership, "bot_settings"):
        result.pop("settings", None)
        result.pop("features", None)
    if not _can_read_scope(ctx, membership, "logs"):
        result.pop("last_error", None)
    return result


def _api(fn: Callable[[web.Request], Awaitable[web.StreamResponse]]):
    async def wrapper(request: web.Request) -> web.StreamResponse:
        try:
            return await fn(request)
        except web.HTTPException:
            raise
        except (
            services_bots.SaaSValidationError,
            providers.ProviderError,
            media_studio.MediaStudioError,
            download_engine.DownloadEngineError,
            destinations.DestinationError,
            ValueError,
        ) as exc:
            return web.json_response({"error": str(exc)}, status=400, headers=_security_headers())
        except PermissionError as exc:
            return web.json_response({"error": str(exc)}, status=403, headers=_security_headers())
        except (KeyError, FileNotFoundError) as exc:
            msg = exc.args[0] if exc.args else str(exc)
            return web.json_response({"error": str(msg)}, status=404, headers=_security_headers())
        except Exception as exc:
            log.exception("Unhandled SaaS API error on %s %s: %s", request.method, request.path, exc)
            return web.json_response(
                {"error": "Internal server error while processing request."},
                status=500,
                headers=_security_headers(),
            )
    return wrapper


# ─────────────────────────── Auth & Session Endpoints ───────────────────────────
@_api
async def api_auth_login(request: web.Request) -> web.Response:
    body = await _json_body(request)
    sess = await auth.exchange_login_payload(request, body)
    uid = int(sess["user_id"])
    await db_models.ensure_default_workspace(uid, display_name=sess["display_name"])
    workspaces = await db_models.list_user_workspaces(uid)
    is_admin = uid in set(getattr(config, "ADMINS", []) or []) or bool(sess.get("via_admin_token"))
    return _ok({
        "session_token": sess["session_token"],
        "csrf_token": sess["csrf_token"],
        "user": {
            "user_id": uid,
            "display_name": sess["display_name"],
            "is_platform_admin": is_admin,
            "is_demo": False,
        },
        "workspaces": workspaces,
    })


@_api
async def api_auth_demo(request: web.Request) -> web.Response:
    demo_enabled = os.environ.get("WEB_DEMO_MODE", "true").strip().lower() not in ("0", "false", "no", "off")
    if not demo_enabled:
        raise PermissionError("Interactive Demo Workspace is disabled on this server.")
    auth.check_login_rate_limit(request)
    # Keep synthetic principals outside Telegram's positive user-id namespace.
    import secrets
    demo_uid = -secrets.randbits(62) - 1
    sess = auth.create_session(demo_uid, display_name="Demo Evaluator", is_demo=True)
    ws = await db_models.ensure_default_workspace(demo_uid, display_name="Demo Evaluator", is_demo=True)
    await services_ops.seed_demo_workspace_if_empty(ws["workspace_id"], demo_uid)
    workspaces = await db_models.list_user_workspaces(demo_uid)
    return _ok({
        "session_token": sess["session_token"],
        "csrf_token": sess["csrf_token"],
        "user": {
            "user_id": demo_uid,
            "display_name": "Demo Evaluator",
            "is_platform_admin": False,
            "is_demo": True,
        },
        "workspaces": workspaces,
    })


@_api
async def api_auth_logout(request: web.Request) -> web.Response:
    await auth.authenticate_request(request)
    hdr = (request.headers.get("Authorization") or "").strip()
    if hdr.lower().startswith("bearer "):
        auth.revoke_session_token(hdr[7:].strip())
    return _ok({"logged_out": True})


@_api
async def api_auth_me(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request, require_csrf=False)
    workspaces = await db_models.list_user_workspaces(ctx.user_id)
    uset = await models_db.get_webapp_settings(ctx.user_id)
    prefs = await db_models.user_prefs_col().find_one({"user_id": ctx.user_id}) or {}
    prefs.pop("_id", None)
    return _ok({
        "user": {
            "user_id": ctx.user_id,
            "display_name": ctx.display_name,
            "is_platform_admin": ctx.is_platform_admin,
            "is_demo": ctx.is_demo,
            "csrf_token": ctx.csrf_token,
        },
        "workspaces": workspaces,
        "settings": uset,
        "preferences": prefs,
        "catalogs": {
            "bot_templates": services_bots.BOT_TEMPLATES_PRESETS,
            "feature_dependencies": {k: {"requires": v[0], "reason": v[1]} for k, v in services_bots.FEATURE_DEPENDENCIES.items()},
            "command_categories": list(services_bots.COMMAND_CATEGORIES),
            "command_permissions": list(services_bots.COMMAND_PERMISSIONS),
            "cloneable_sections": list(services_clone_files.CLONEABLE_SECTIONS),
            "workspace_roles": list(db_models.WORKSPACE_ROLES),
            "permission_scopes": list(db_models.PERMISSION_SCOPES),
            "automation_triggers": list(services_ops.SUPPORTED_TRIGGERS),
            "automation_actions": list(services_ops.SUPPORTED_ACTIONS),
            "template_categories": list(services_ops.TEMPLATE_CATEGORIES),
            "providers": providers.PROVIDER_CATALOG,
            "destinations": destinations.DESTINATION_CATALOG,
            "media_studio": media_studio.STUDIO_CATALOG,
        },
    })


# ─────────────────────────── Workspaces & Team Management ───────────────────────────
@_api
async def api_workspaces_list_or_create(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    if request.method == "GET":
        return _ok({"workspaces": await db_models.list_user_workspaces(ctx.user_id)})
    body = await _json_body(request)
    ws = await services_bots.create_workspace(
        ctx.user_id,
        name=str(body.get("name") or ""),
        icon=str(body.get("icon") or "⚡"),
        description=str(body.get("description") or ""),
        is_demo=ctx.is_demo,
    )
    return _ok({"workspace": ws}, status=201)


@_api
async def api_workspace_detail(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    mem = await auth.require_workspace_access(ctx, ws_id)
    if request.method == "GET":
        bots = await services_bots.list_workspace_bots(ws_id, user_id=ctx.user_id, include_archived=True)
        files = await services_clone_files.query_filestore_files(ws_id, user_id=ctx.user_id, page_size=5)
        can_read_logs = (mem.get("permissions") or {}).get("logs", False)
        ntfs = (await db_models.notifications_col().find({"workspace_id": ws_id}).sort("created_ts", -1).limit(10).to_list(length=10)) if can_read_logs else []
        for n in ntfs:
            n.pop("_id", None)
        return _ok({
            "workspace": mem["workspace"],
            "my_role": mem["role"],
            "my_permissions": mem["permissions"],
            "usage": {
                "bots_count": len(bots),
                "files_count": files["total"],
                "storage_bytes": files["total_bytes"],
                "unread_notifications": sum(1 for n in ntfs if not n.get("read")) if can_read_logs else None,
            },
            "recent_notifications": ntfs,
        })
    if request.method in ("PATCH", "PUT"):
        await auth.require_workspace_access(ctx, ws_id, min_role="administrator")
        body = await _json_body(request)
        ws = await services_bots.update_workspace(
            ws_id,
            ctx.user_id,
            name=body.get("name"),
            icon=body.get("icon"),
            description=body.get("description"),
            archived=body.get("archived"),
        )
        return _ok({"workspace": ws})
    if request.method == "DELETE":
        await auth.require_workspace_access(ctx, ws_id, min_role="owner")
        body = await _json_body(request)
        await services_bots.delete_workspace(ws_id, ctx.user_id, confirm_name=str(body.get("confirm_name") or ""))
        return _ok({"deleted": True})
    raise web.HTTPMethodNotAllowed(request.method, ["GET", "PATCH", "PUT", "DELETE"])


@_api
async def api_workspace_team(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="team_management")
        return _ok(await services_bots.list_workspace_team(ws_id))
    mem = await auth.require_workspace_access(ctx, ws_id, scope="team_management")
    body = await _json_body(request)
    inv = await services_bots.invite_team_member(
        ws_id,
        ctx.user_id,
        mem["role"],
        target_user_id=int(body.get("user_id") or 0),
        role=str(body.get("role") or "viewer"),
        permissions=body.get("permissions"),
    )
    return _ok({"member": inv}, status=201)


@_api
async def api_workspace_team_member(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    target_uid = int(request.match_info["member_id"])
    mem = await auth.require_workspace_access(ctx, ws_id, scope="team_management")
    if request.method in ("PATCH", "PUT"):
        body = await _json_body(request)
        updated = await services_bots.update_member_role(
            ws_id,
            ctx.user_id,
            mem["role"],
            target_user_id=target_uid,
            role=str(body.get("role") or "viewer"),
            permissions=body.get("permissions"),
        )
        return _ok({"member": updated})
    if request.method == "DELETE":
        removed = await services_bots.remove_team_member(ws_id, ctx.user_id, mem["role"], target_user_id=target_uid)
        return _ok({"removed": removed})
    raise web.HTTPMethodNotAllowed(request.method, ["PATCH", "PUT", "DELETE"])


# ─────────────────────────── Bots, Creation Wizard & Fleet ───────────────────────────
@_api
async def api_bots_verify_token(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
    body = await _json_body(request)
    diag = await services_bots.verify_telegram_token_diagnostic(str(body.get("bot_token") or ""))
    return _ok(diag)


@_api
async def api_bots_collection(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        mem = await auth.require_workspace_access(ctx, ws_id)
        bots = await services_bots.list_workspace_bots(
            ws_id,
            user_id=ctx.user_id,
            search=request.query.get("search", ""),
            bot_type=request.query.get("bot_type", ""),
            status=request.query.get("status", ""),
            tag=request.query.get("tag", ""),
            include_archived=request.query.get("include_archived", "").lower() in ("1", "true"),
        )
        return _ok({"bots": [_bot_for_member(b, ctx, mem) for b in bots]})
    await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
    body = await _json_body(request)
    bot = await services_bots.create_or_register_bot(
        ws_id,
        ctx.user_id,
        body,
        allow_unverified_offline=False,
    )
    return _ok({"bot": bot}, status=201)


@_api
async def api_bot_item(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    bot_id = request.match_info["bot_id"]
    if request.method == "GET":
        mem = await auth.require_workspace_access(ctx, ws_id)
        raw = await services_bots.get_owned_workspace_bot(ws_id, bot_id)
        return _ok({"bot": _bot_for_member(services_bots._public_bot_doc(raw), ctx, mem)})
    await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
    if request.method in ("PATCH", "PUT"):
        body = await _json_body(request)
        updated = await services_bots.update_bot_profile_or_state(ws_id, bot_id, ctx.user_id, body)
        return _ok({"bot": updated})
    if request.method == "DELETE":
        await services_bots.delete_bot(ws_id, bot_id, ctx.user_id)
        return _ok({"deleted": True})
    raise web.HTTPMethodNotAllowed(request.method, ["GET", "PATCH", "PUT", "DELETE"])


@_api
async def api_fleet(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    mem = await auth.require_workspace_access(ctx, ws_id)
    if request.method == "GET":
        data = await services_bots.fleet_summary_and_drift(ws_id, ctx.user_id)
        data["bots"] = [_bot_for_member(b, ctx, mem) for b in data["bots"]]
        if not _can_read_scope(ctx, mem, "bot_settings"):
            data["config_drift_fields"] = None
        if not _can_read_scope(ctx, mem, "logs"):
            data["recent_errors"] = None
        return _ok(data)
    await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
    body = await _json_body(request)
    res = await services_bots.fleet_bulk_operation(
        ws_id,
        ctx.user_id,
        bot_ids=list(body.get("bot_ids") or []),
        operation=str(body.get("operation") or ""),
        value=body.get("value"),
        confirmed=bool(body.get("confirmed", False)),
    )
    return _ok(res)


# ─────────────────────────── Config Revisions, Environments & Commands ───────────────────────────
@_api
async def api_bot_config(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    bot_id = request.match_info["bot_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
        revs = await services_bots.list_bot_revisions(ws_id, bot_id)
        return _ok({"revisions": revs, "dependencies": services_bots.FEATURE_DEPENDENCIES})
    await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
    body = await _json_body(request)
    if body.get("restore_version") is not None:
        res = await services_bots.restore_bot_revision(ws_id, bot_id, ctx.user_id, int(body["restore_version"]))
        return _ok(res)
    res = await services_bots.save_or_apply_bot_config(
        ws_id,
        bot_id,
        ctx.user_id,
        features=body.get("features"),
        settings=body.get("settings"),
        mode=str(body.get("mode") or "apply"),
        note=str(body.get("note") or ""),
    )
    return _ok(res)


@_api
async def api_bot_environments(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    bot_id = request.match_info["bot_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
        return _ok({"environments": await services_bots.list_bot_environments(ws_id, bot_id)})
    await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
    body = await _json_body(request)
    res = await services_bots.update_or_promote_environment(
        ws_id,
        bot_id,
        ctx.user_id,
        source_env=str(body.get("source_env") or "development"),
        target_env=str(body.get("target_env") or ""),
        features=body.get("features"),
        settings=body.get("settings"),
        confirmed=bool(body.get("confirmed", False)),
    )
    return _ok(res)


@_api
async def api_bot_commands(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    bot_id = request.match_info["bot_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="commands")
        cmds = await services_bots.list_bot_commands(
            ws_id,
            bot_id,
            search=request.query.get("search", ""),
            category=request.query.get("category", ""),
        )
        return _ok({"commands": cmds})
    await auth.require_workspace_access(ctx, ws_id, scope="commands")
    body = await _json_body(request)
    if request.method == "DELETE":
        deleted = await services_bots.delete_bot_command(ws_id, bot_id, ctx.user_id, str(body.get("command") or ""))
        return _ok({"deleted": deleted})
    cmd = await services_bots.upsert_bot_command(ws_id, bot_id, ctx.user_id, body)
    return _ok({"command": cmd, "commands": await services_bots.list_bot_commands(ws_id, bot_id)})


# ─────────────────────────── Clone Studio & Migration Center ───────────────────────────
async def _require_clone_section_access(ctx: auth.AuthContext, workspace_id: str, sections: list[str]) -> None:
    domains = set()
    for section in sections:
        if section == "commands":
            domains.add("commands")
        elif section == "filestore":
            domains.add("filestore")
        elif section == "automations":
            domains.add("automation")
        else:
            domains.add("bot_settings")
    for domain in sorted(domains):
        await auth.require_workspace_access(ctx, workspace_id, scope=domain)


@_api
async def api_clones(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="cloning")
        hist = await services_clone_files.list_clone_history(ws_id, kind=request.query.get("kind", ""))
        return _ok({"history": hist})
    await auth.require_workspace_access(ctx, ws_id, scope="cloning")
    body = await _json_body(request)
    action = str(body.get("action") or "preview")
    if action == "inspect":
        await _require_clone_section_access(ctx, ws_id, list(services_clone_files.CLONEABLE_SECTIONS))
        bundle = await services_clone_files.extract_cloneable_bundle(ws_id, str(body.get("bot_id") or ""))
        return _ok({"bundle": bundle})
    if action in ("preview", "execute", "migrate"):
        sections = services_clone_files.validate_clone_sections(body.get("sections"))
        await _require_clone_section_access(ctx, ws_id, sections)
    if action == "preview":
        prev = await services_clone_files.preview_clone_operation(
            ws_id,
            source_bot_id=str(body.get("source_bot_id") or ""),
            target_bot_id=str(body.get("target_bot_id") or ""),
            sections=body.get("sections"),
            excluded_fields=body.get("excluded_fields"),
        )
        return _ok(prev)
    if action in ("execute", "migrate"):
        if body.get("confirmed") is not True:
            raise services_bots.SaaSValidationError("Clone execution requires explicit confirmation.")
        # Every execution currently writes the destination bot record/version.
        await auth.require_workspace_access(ctx, ws_id, scope="bot_settings")
        rep = await services_clone_files.execute_clone_operation(
            ws_id,
            ctx.user_id,
            source_bot_id=str(body.get("source_bot_id") or ""),
            target_bot_id=str(body.get("target_bot_id") or ""),
            sections=body.get("sections"),
            excluded_fields=body.get("excluded_fields"),
            kind="migration" if action == "migrate" else "clone",
        )
        return _ok({"report": rep, **rep})
    if action == "rollback":
        if body.get("confirmed") is not True:
            raise services_bots.SaaSValidationError("Rollback requires explicit confirmation.")
        record = await db_models.clones_col().find_one({"workspace_id": ws_id, "clone_id": str(body.get("clone_id") or "")})
        if not record:
            raise KeyError("Clone/migration record not found in this workspace.")
        await _require_clone_section_access(ctx, ws_id, services_clone_files.validate_clone_sections(record.get("sections")))
        rb = await services_clone_files.rollback_clone_operation(ws_id, str(body.get("clone_id") or ""), ctx.user_id)
        return _ok(rb)
    raise services_bots.SaaSValidationError(f"Unsupported clone action '{action}'.")


# ─────────────────────────── File Store, Intelligence, Downloads & Media Studio ───────────────────────────
@_api
async def api_filestore_list_or_create(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="filestore")
        q = request.query
        res = await services_clone_files.query_filestore_files(
            ws_id,
            user_id=ctx.user_id,
            bot_id=q.get("bot_id", ""),
            search=q.get("search", ""),
            category=q.get("category", ""),
            mime_filter=q.get("mime", ""),
            tag=q.get("tag", ""),
            min_bytes=int(q.get("min_bytes") or 0),
            max_bytes=int(q.get("max_bytes") or 0),
            include_deleted=q.get("include_deleted", "").lower() in ("1", "true"),
            sort_by=q.get("sort_by", "uploaded_ts"),
            sort_dir=q.get("sort_dir", "desc"),
            page=int(q.get("page") or 1),
            page_size=int(q.get("page_size") or 25),
        )
        return _ok(res)

    await auth.require_workspace_access(ctx, ws_id, scope="filestore")
    ct = (request.headers.get("Content-Type") or "").lower()
    if ct.startswith("multipart/form-data"):
        import hashlib
        import secrets
        import shutil

        reader = await request.multipart()
        saved_entries = []
        bot_id = request.query.get("bot_id", "default")
        if bot_id != "default":
            await services_bots.get_owned_workspace_bot(ws_id, bot_id)
        while True:
            part = await reader.next()
            if part is None:
                break
            if not part.filename:
                continue
            fname = providers.sanitize_filename(part.filename)
            relative_dir = services_clone_files.workspace_file_prefix(ws_id) + secrets.token_hex(16)
            relative_path = relative_dir + "/" + fname
            out_path = services_clone_files.resolve_workspace_file(ws_id, ctx.user_id, relative_path, must_exist=False)
            file_dir = os.path.dirname(out_path)
            os.makedirs(file_dir, exist_ok=False)
            size = 0
            sha = hashlib.sha256()
            try:
                with open(out_path, "xb") as fp:
                    while True:
                        chunk = await part.read_chunk(65536)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > 512 * 1024 * 1024:
                            raise services_bots.SaaSValidationError("Direct browser upload capped at 512 MiB.")
                        sha.update(chunk)
                        fp.write(chunk)
                entry = await services_clone_files.register_filestore_entry(
                    ws_id, ctx.user_id,
                    {"bot_id": bot_id, "filename": fname, "local_rel_path": relative_path,
                     "size_bytes": size, "sha256": sha.hexdigest()},
                )
            except BaseException:
                # Includes cancellation and database errors; never leave an unindexed upload.
                shutil.rmtree(file_dir, ignore_errors=True)
                raise
            saved_entries.append(entry)
        return _ok({"uploaded": saved_entries}, status=201)

    # A caller-supplied local path could reference another workspace's files and
    # fabricated metadata would pollute analytics. Only real uploads are accepted.
    raise services_bots.SaaSValidationError("Use multipart upload to add a file.")


@_api
async def api_filestore_item(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    file_uid = request.match_info["file_uid"]
    await auth.require_workspace_access(ctx, ws_id, scope="filestore")
    body = await _json_body(request)
    res = await services_clone_files.operate_filestore_file(
        ws_id, file_uid, ctx.user_id, action=str(body.get("action") or ""), payload=body
    )
    return _ok(res)


@_api
async def api_filestore_config(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    bot_id = request.query.get("bot_id", "default")
    await auth.require_workspace_access(ctx, ws_id, scope="filestore")
    if bot_id != "default":
        await services_bots.get_owned_workspace_bot(ws_id, bot_id)
    if request.method == "GET":
        return _ok(await services_clone_files.get_filestore_config(ws_id, bot_id))
    body = await _json_body(request)
    target_bot = str(body.get("bot_id") or bot_id)
    if target_bot != "default":
        await services_bots.get_owned_workspace_bot(ws_id, target_bot)
    return _ok(await services_clone_files.update_filestore_config(ws_id, target_bot, ctx.user_id, body))


@_api
async def api_file_intelligence(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    await auth.require_workspace_access(ctx, ws_id, scope="analytics")
    await auth.require_workspace_access(ctx, ws_id, scope="filestore")
    return _ok(await services_clone_files.compute_file_intelligence(ws_id, ctx.user_id))


@_api
async def api_downloads(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="filestore")
        tasks = download_engine.engine.list_tasks(
            user_id=ctx.user_id,
            status=request.query.get("status", ""),
            search=request.query.get("search", ""),
        )
        hist = await models_db.query_history("download", user_id=ctx.user_id, limit=30)
        return _ok({"tasks": tasks, "history": hist["items"], "providers": providers.PROVIDER_CATALOG})

    await auth.require_workspace_access(ctx, ws_id, scope="filestore")
    body = await _json_body(request)
    op = str(body.get("action") or "enqueue")
    if op == "inspect":
        mf = await providers.inspect_source(str(body.get("url") or ""), user_id=ctx.user_id)
        sess = providers.selection_store.create(ctx.user_id, mf, destination=str(body.get("destination") or "local"))
        return _ok({"manifest": mf.to_dict(), "selection": sess})
    if op == "selection_update":
        view = providers.selection_store.update_selection(
            str(body.get("session_id") or ""),
            ctx.user_id,
            toggle_id=str(body.get("toggle_id") or ""),
            select_all=body.get("select_all"),
            selected_ids=body.get("selected_ids"),
            exclude_extensions=body.get("exclude_extensions"),
            destination=str(body.get("destination") or ""),
        )
        return _ok({"selection": view})
    if op == "selection_confirm":
        created = await download_engine.engine.enqueue_confirmed_selection(
            session_id=str(body.get("session_id") or ""),
            user_id=ctx.user_id,
            priority=str(body.get("priority") or "normal"),
            destination=str(body.get("destination") or ""),
        )
        return _ok({"tasks": created}, status=201)
    if op == "enqueue":
        res = await download_engine.engine.enqueue_url(
            user_id=ctx.user_id,
            url=str(body.get("url") or ""),
            destination=str(body.get("destination") or "local"),
            priority=str(body.get("priority") or "normal"),
            expected_sha256=str(body.get("expected_sha256") or ""),
            auto_select_all=bool(body.get("auto_select_all", False)),
        )
        return _ok(res, status=201)
    if op in ("pause", "resume", "cancel", "retry", "priority_high", "delete"):
        tk = await download_engine.engine.control_task(str(body.get("task_id") or ""), op, user_id=ctx.user_id)
        return _ok({"task": tk})
    if op == "clear_finished":
        removed = await download_engine.engine.clear_finished(user_id=ctx.user_id)
        return _ok({"cleared": removed})
    raise services_bots.SaaSValidationError(f"Unsupported download action '{op}'.")


@_api
async def api_media_studio(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="filestore")
        return _ok({
            "catalog": media_studio.STUDIO_CATALOG,
            "workspace_files": destinations.list_user_files(ctx.user_id)["files"],
        })
    await auth.require_workspace_access(ctx, ws_id, scope="filestore")
    body = await _json_body(request)
    res = await media_studio.run_studio_operation(ctx.user_id, str(body.get("op") or ""), body)
    return _ok(res)


# ─────────────────────────── Analytics, Operations, Automation, Templates, Dev & Admin ───────────────────────────
@_api
async def api_analytics(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    await auth.require_workspace_access(ctx, ws_id, scope="analytics")
    q = request.query
    data = await services_clone_files.compute_owner_and_bot_analytics(
        ws_id,
        ctx.user_id,
        bot_id=q.get("bot_id", ""),
        days=int(q.get("days") or 30),
        start_date=q.get("start_date", ""),
        end_date=q.get("end_date", ""),
    )
    if request.path.endswith(".csv"):
        csv_text = services_clone_files.export_analytics_csv(data)
        return web.Response(
            text=csv_text,
            content_type="text/csv",
            headers={**_security_headers(content_type="text/csv"), "Content-Disposition": 'attachment; filename="videl_analytics.csv"'},
        )
    return _ok(data)


@_api
async def api_health_and_webhooks(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    mem = await auth.require_workspace_access(ctx, ws_id, scope="logs")
    if request.method == "GET":
        health = await services_ops.bot_operations_health(ws_id, ctx.user_id)
        can_integrate = (mem.get("permissions") or {}).get("integrations") and (ctx.auth_kind != "api_key" or "integrations" in ctx.api_key_scopes)
        hooks = (await services_ops.list_webhooks_and_events(ws_id, event_type=request.query.get("event_type", ""))) if can_integrate else {"webhooks": [], "recent_events": [], "availability": "permission required"}
        return _ok({"health": health, "webhooks": hooks})
    await auth.require_workspace_access(ctx, ws_id, scope="integrations")
    body = await _json_body(request)
    if body.get("action") == "test":
        ev = await services_ops.test_outbound_webhook(ws_id, str(body.get("webhook_id") or ""), ctx.user_id)
        return _ok({"event": ev})
    if body.get("action") == "delete":
        await db_models.webhooks_col().delete_one({"webhook_id": str(body.get("webhook_id") or ""), "workspace_id": ws_id})
        return _ok({"deleted": True})
    wh = await services_ops.register_outbound_webhook(ws_id, ctx.user_id, body)
    return _ok({"webhook": wh}, status=201)


@_api
async def api_automations(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="automation")
        wfs = await db_models.automations_col().find({"workspace_id": ws_id}).sort("created_ts", -1).to_list(length=100)
        runs = await db_models.automation_runs_col().find({"workspace_id": ws_id}).sort("created_ts", -1).limit(50).to_list(length=50)
        for w in wfs:
            w.pop("_id", None)
        for r in runs:
            r.pop("_id", None)
        return _ok({"workflows": wfs, "runs": runs})
    await auth.require_workspace_access(ctx, ws_id, scope="automation")
    body = await _json_body(request)
    act = str(body.get("action") or "upsert")
    if act == "run":
        run_res = await services_ops.trigger_automation_run(
            ws_id,
            str(body.get("workflow_id") or ""),
            ctx.user_id,
            event_data=body.get("event_data") or {},
            idempotency_key=str(body.get("idempotency_key") or ""),
        )
        return _ok({"run": run_res})
    if act == "delete":
        await db_models.automations_col().delete_one({"workflow_id": str(body.get("workflow_id") or ""), "workspace_id": ws_id})
        return _ok({"deleted": True})
    wf = await services_ops.upsert_automation_workflow(ws_id, ctx.user_id, body)
    return _ok({"workflow": wf}, status=201 if not body.get("workflow_id") else 200)


@_api
async def api_schedules(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="automation")
        return _ok({"schedules": await models_db.list_scheduled_jobs(user_id=ctx.user_id)})
    await auth.require_workspace_access(ctx, ws_id, scope="automation")
    body = await _json_body(request)
    act = str(body.get("action") or "create")
    if act == "toggle":
        ok = await models_db.toggle_scheduled_job(str(body.get("job_id") or ""), bool(body.get("enabled")), user_id=ctx.user_id)
        return _ok({"updated": ok})
    if act == "delete":
        ok = await models_db.delete_scheduled_job(str(body.get("job_id") or ""), user_id=ctx.user_id)
        return _ok({"deleted": ok})
    if act == "run_due":
        raise services_bots.SaaSValidationError("Manually running all users’ due jobs from a workspace is not supported.")
    delay_min = max(0, int(body.get("delay_minutes") or 60))
    job = await models_db.create_scheduled_job(
        user_id=ctx.user_id,
        kind=str(body.get("kind") or "download"),
        target=str(body.get("target") or ""),
        run_at_ts=float(body.get("run_at_ts") or (time.time() + delay_min * 60)),
        interval_minutes=int(body.get("interval_minutes") or 0),
        destination=str(body.get("destination") or "local"),
        title=str(body.get("title") or ""),
        options=body.get("options") or {},
    )
    job.pop("_id", None)
    return _ok({"schedule": job}, status=201)


@_api
async def api_templates(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="templates")
        return _ok(await services_ops.list_templates(ws_id, category=request.query.get("category", "")))
    await auth.require_workspace_access(ctx, ws_id, scope="templates")
    body = await _json_body(request)
    if body.get("action") == "delete":
        await db_models.templates_col().delete_one({"template_id": str(body.get("template_id") or ""), "workspace_id": ws_id})
        return _ok({"deleted": True})
    if body.get("action") == "report":
        result = await db_models.templates_col().update_one(
            {"template_id": str(body.get("template_id") or ""), "workspace_id": ws_id},
            {"$inc": {"reports": 1}},
        )
        return _ok({"reported": bool(result.matched_count)})
    tpl = await services_ops.upsert_template(ws_id, ctx.user_id, body)
    return _ok({"template": tpl})


@_api
async def api_integrations(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="integrations")
        conns = await models_db.list_user_connections(ctx.user_id)
        return _ok({"connections": conns, "destinations": destinations.DESTINATION_CATALOG})
    await auth.require_workspace_access(ctx, ws_id, scope="integrations")
    body = await _json_body(request)
    if body.get("action") == "disconnect":
        ok = await models_db.remove_storage_connection(ctx.user_id, str(body.get("provider") or ""))
        return _ok({"disconnected": ok})
    from filestore.utils import security
    if body.get("access_token") and not security.is_encryption_enabled():
        raise services_bots.SaaSValidationError("A valid ENCRYPTION_KEY is required before storing integration credentials.")
    saved = await models_db.save_storage_connection(
        ctx.user_id,
        str(body.get("provider") or ""),
        access_token=str(body.get("access_token") or ""),
        root_folder=str(body.get("root_folder") or ""),
        label=str(body.get("label") or ""),
    )
    await models_db.log_audit(ctx.user_id, "integration_connect", target=f"{ws_id}:{body.get('provider')}")
    return _ok({"connection": saved})


@_api
async def api_developer_keys(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    if request.method == "GET":
        await auth.require_workspace_access(ctx, ws_id, scope="integrations")
        return _ok({"keys": await services_ops.list_api_keys(ws_id)})
    await auth.require_workspace_access(ctx, ws_id, min_role="administrator")
    await auth.require_workspace_access(ctx, ws_id, scope="integrations")
    body = await _json_body(request)
    act = str(body.get("action") or "create")
    if act in ("revoke", "rotate"):
        res = await services_ops.revoke_or_rotate_api_key(
            ws_id, str(body.get("key_id") or ""), ctx.user_id, rotate=(act == "rotate")
        )
        return _ok(res)
    created = await services_ops.create_scoped_api_key(
        ws_id,
        ctx.user_id,
        name=str(body.get("name") or ""),
        scopes=body.get("scopes") or [],
        expires_in_days=int(body.get("expires_in_days") or 90),
    )
    return _ok(created, status=201)


@_api
async def api_bundle_backup(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    for scope in ("bot_settings", "commands", "filestore", "templates", "automation"):
        await auth.require_workspace_access(ctx, ws_id, scope=scope)
    if request.method == "GET":
        bundle = await services_ops.export_workspace_bundle(ws_id, ctx.user_id)
        hist = await db_models.backups_meta_col().find({"workspace_id": ws_id}).sort("created_ts", -1).limit(20).to_list(length=20)
        for h in hist:
            h.pop("_id", None)
        return _ok({"bundle": bundle, "history": hist})
    body = await _json_body(request)
    res = await services_ops.import_workspace_bundle(
        ws_id, ctx.user_id, body.get("bundle") or {}, confirmed=bool(body.get("confirmed", False))
    )
    return _ok(res)


@_api
async def api_dashboard_layout(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    await auth.require_workspace_access(ctx, ws_id)
    if request.method == "GET":
        return _ok(await services_ops.get_dashboard_layout(ctx.user_id, ws_id))
    body = await _json_body(request)
    if body.get("reset"):
        return _ok(await services_ops.save_dashboard_layout(ctx.user_id, ws_id, list(services_ops.DEFAULT_WIDGET_ORDER)))
    return _ok(await services_ops.save_dashboard_layout(
        ctx.user_id, ws_id, list(body.get("widgets") or []), preset_name=str(body.get("preset_name") or "Custom")
    ))


@_api
async def api_notifications_and_activity(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    await auth.require_workspace_access(ctx, ws_id, scope="logs")
    if request.method == "GET":
        q: dict[str, Any] = {"workspace_id": ws_id}
        if request.query.get("unread_only") in ("1", "true"):
            q["read"] = False
        ntfs = await db_models.notifications_col().find(q).sort("created_ts", -1).limit(80).to_list(length=80)
        for n in ntfs:
            n.pop("_id", None)
        return _ok({"notifications": ntfs, "activity_logs": [],
                    "activity_logs_availability": "unavailable: legacy audit records are not workspace-scoped"})
    body = await _json_body(request)
    if body.get("mark_all_read"):
        await db_models.notifications_col().update_many({"workspace_id": ws_id}, {"$set": {"read": True}})
    elif body.get("notification_id"):
        await db_models.notifications_col().update_one(
            {"notification_id": str(body["notification_id"]), "workspace_id": ws_id},
            {"$set": {"read": bool(body.get("read", True))}},
        )
    return _ok({"updated": True})


@_api
async def api_global_search(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    ws_id = request.match_info["ws_id"]
    await auth.require_workspace_access(ctx, ws_id)
    return _ok(await services_ops.global_workspace_search(ws_id, ctx.user_id, request.query.get("q", "")))


@_api
async def api_user_settings(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    if request.method == "GET":
        return _ok({"settings": await models_db.get_webapp_settings(ctx.user_id)})
    body = await _json_body(request)
    if body.get("reset"):
        return _ok({"settings": await models_db.reset_webapp_settings(ctx.user_id)})
    if "preferences" in body and isinstance(body["preferences"], dict):
        prefs = {
            k: body["preferences"][k]
            for k in ("onboarding_completed", "favorites", "recent_bots", "saved_filters", "selected_workspace_id", "selected_bot_id")
            if k in body["preferences"]
        }
        await db_models.user_prefs_col().update_one({"user_id": ctx.user_id}, {"$set": prefs}, upsert=True)
        return _ok({"preferences": prefs})
    return _ok({"settings": await models_db.update_webapp_settings(ctx.user_id, body)})


# ─────────────────────────── Platform Administration Center (Section 29) ───────────────────────────
@_api
async def api_platform_admin_overview(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    auth.require_platform_admin(ctx)
    return _ok(await services_ops.platform_admin_overview())


@_api
async def api_platform_admin_plugins(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    auth.require_platform_admin(ctx)
    if request.method == "GET":
        return _ok(services_ops.list_plugins_status())
    body = await _json_body(request)
    res = await services_ops.toggle_or_configure_plugin(
        ctx.user_id,
        str(body.get("plugin") or ""),
        enabled=body.get("enabled"),
        config_patch=body.get("config"),
    )
    return _ok(res)


@_api
async def api_platform_admin_control(request: web.Request) -> web.Response:
    ctx = await auth.authenticate_request(request)
    auth.require_platform_admin(ctx)
    body = await _json_body(request)
    act = str(body.get("action") or "")
    if act == "maintenance":
        config.MAINTENANCE = bool(body.get("enabled"))
        await models_db.log_audit(ctx.user_id, "admin_maintenance_toggle", actor_role="admin", detail=str(config.MAINTENANCE))
        return _ok({"maintenance": config.MAINTENANCE})
    if act in ("ban_user", "unban_user"):
        target_uid = int(body.get("user_id") or 0)
        if target_uid in set(getattr(config, "ADMINS", []) or []):
            raise PermissionError("Cannot ban a platform administrator.")
        from core.db import vdb as core_vdb
        if act == "ban_user":
            await core_vdb.ban(target_uid, str(body.get("reason") or "Platform admin action"))
        else:
            await core_vdb.unban(target_uid)
        await models_db.log_audit(ctx.user_id, act, actor_role="admin", target=str(target_uid))
        return _ok({"action": act, "user_id": target_uid, "ok": True})
    if act == "approve_template":
        tid = str(body.get("template_id") or "")
        await db_models.templates_col().update_one({"template_id": tid}, {"$set": {"marketplace_status": "approved"}})
        await models_db.log_audit(ctx.user_id, "admin_template_approve", actor_role="admin", target=tid)
        return _ok({"approved": True, "template_id": tid})
    raise services_bots.SaaSValidationError(f"Unsupported platform admin action '{act}'.")


# ─────────────────────────── SPA Shell & Assets Delivery ───────────────────────────
async def serve_app_html(_request: web.Request) -> web.Response:
    from webapp.ui_shell import render_app_html
    return web.Response(
        text=render_app_html(),
        content_type="text/html",
        headers=_security_headers(content_type="text/html"),
    )


async def serve_app_css(_request: web.Request) -> web.Response:
    from webapp.ui_styles import STYLES_CSS
    return web.Response(
        text=STYLES_CSS,
        content_type="text/css",
        headers=_security_headers(content_type="text/css"),
    )


async def serve_app_js(_request: web.Request) -> web.Response:
    from webapp.ui_client import CLIENT_JS
    return web.Response(
        text=CLIENT_JS,
        content_type="application/javascript",
        headers=_security_headers(content_type="application/javascript"),
    )


def mount_webapp(app: web.Application) -> None:
    """Mount Videl1 SaaS Platform (`/app` + `/api/v1/*`) onto the aiohttp application."""
    r = app.router
    r.add_get("/app", serve_app_html)
    r.add_get("/app/", serve_app_html)
    r.add_get("/app/styles.css", serve_app_css)
    r.add_get("/app/app.js", serve_app_js)

    # Auth
    r.add_post("/api/v1/auth/login", api_auth_login)
    r.add_post("/api/v1/auth/demo", api_auth_demo)
    r.add_post("/api/v1/auth/logout", api_auth_logout)
    r.add_get("/api/v1/auth/me", api_auth_me)
    r.add_route("*", "/api/v1/settings", api_user_settings)

    # Workspaces & Team
    r.add_route("*", "/api/v1/workspaces", api_workspaces_list_or_create)
    r.add_route("*", "/api/v1/workspaces/{ws_id}", api_workspace_detail)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/team", api_workspace_team)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/team/{member_id}", api_workspace_team_member)

    # Bots, Creation, Config, Envs, Commands & Fleet
    r.add_post("/api/v1/workspaces/{ws_id}/bots/verify-token", api_bots_verify_token)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/bots", api_bots_collection)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/bots/{bot_id}", api_bot_item)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/bots/{bot_id}/config", api_bot_config)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/bots/{bot_id}/environments", api_bot_environments)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/bots/{bot_id}/commands", api_bot_commands)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/fleet", api_fleet)

    # Clone Studio & Migration
    r.add_route("*", "/api/v1/workspaces/{ws_id}/clones", api_clones)

    # File Store, Intelligence, Downloads & Media Studio
    r.add_route("*", "/api/v1/workspaces/{ws_id}/filestore", api_filestore_list_or_create)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/filestore/config", api_filestore_config)
    r.add_post("/api/v1/workspaces/{ws_id}/filestore/{file_uid}", api_filestore_item)
    r.add_get("/api/v1/workspaces/{ws_id}/file-intelligence", api_file_intelligence)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/downloads", api_downloads)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/studio", api_media_studio)

    # Analytics, Health/Webhooks, Automations, Schedules, Templates, Integrations, Dev Keys, Bundles, Layout, Search
    r.add_get("/api/v1/workspaces/{ws_id}/analytics", api_analytics)
    r.add_get("/api/v1/workspaces/{ws_id}/analytics.csv", api_analytics)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/health", api_health_and_webhooks)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/automations", api_automations)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/schedules", api_schedules)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/templates", api_templates)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/integrations", api_integrations)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/api-keys", api_developer_keys)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/bundle", api_bundle_backup)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/dashboard-layout", api_dashboard_layout)
    r.add_route("*", "/api/v1/workspaces/{ws_id}/notifications", api_notifications_and_activity)
    r.add_get("/api/v1/workspaces/{ws_id}/search", api_global_search)

    # Platform Admin Center
    r.add_get("/api/v1/admin/overview", api_platform_admin_overview)
    r.add_route("*", "/api/v1/admin/plugins", api_platform_admin_plugins)
    r.add_post("/api/v1/admin/control", api_platform_admin_control)

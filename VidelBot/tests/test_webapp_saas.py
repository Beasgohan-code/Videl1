"""
Comprehensive Integration, Security, RBAC, IDOR, Clone, File Store, Analytics &
Automation Tests for Videl1 Multi-Bot SaaS Platform (`VidelBot/webapp/`).
"""
from __future__ import annotations

import json
import shutil
import pytest
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer

from tests.harness import load_all, no_botapi, reset_db, run

load_all()

import config  # noqa: E402
import keep_alive  # noqa: E402
from platform_core import destinations  # noqa: E402
from webapp import auth as saas_auth  # noqa: E402
from webapp import db_models, services_bots, services_ops  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402


@pytest.fixture(autouse=True)
def reset_saas_state(monkeypatch):
    """Ensure clean isolated DB + in-memory SaaS state and test Admin Token per test."""
    run(reset_db())
    no_botapi()
    db_models.reset_memory_collections()
    saas_auth.reset_auth_state()
    services_ops._automation_seen_keys.clear()
    monkeypatch.setattr(config, "ADMIN_WEB_TOKEN", "test_super_admin_secret_token_999")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

    async def verified_token(token):
        bot_id = int(token.split(":", 1)[0])
        return {"verified_with_telegram": True, "telegram_bot_id": bot_id,
                "username": "", "first_name": "", "token_masked": "masked",
                "diagnostic": "Verified by test stub."}

    monkeypatch.setattr(services_bots, "verify_telegram_token_diagnostic", verified_token)
    yield


async def _login_link(cli: TestClient, uid: int, name: str = "User") -> tuple[dict[str, str], str]:
    link = saas_auth.issue_saas_link(uid, username=f"user{uid}", first_name=name)
    resp = await cli.post("/api/v1/auth/login", json={"link": link})
    assert resp.status == 200
    data = await resp.json()
    headers = {
        "Authorization": f"Bearer {data['session_token']}",
        "X-CSRF-Token": data["csrf_token"],
    }
    me_resp = await cli.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status == 200
    me = await me_resp.json()
    ws_id = me["workspaces"][0]["workspace_id"]
    return headers, ws_id


def test_spa_assets_security_headers_and_query_secret_rejection():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            r_html = await cli.get("/app")
            assert r_html.status == 200
            assert "Videl" in await r_html.text()
            assert r_html.headers.get("X-Content-Type-Options") == "nosniff"
            assert r_html.headers.get("Referrer-Policy") == "no-referrer"

            r_css = await cli.get("/app/styles.css")
            assert r_css.status == 200
            assert "--bg-app" in await r_css.text()

            r_js = await cli.get("/app/app.js")
            assert r_js.status == 200
            assert "STUDIOS" in await r_js.text()

            # Secrets in URL query parameters MUST be rejected with 401
            r_q = await cli.get("/api/v1/auth/me?token=secret123")
            assert r_q.status == 401
            q_json = await r_q.json()
            assert q_json["code"] == "query_secret_forbidden"

    run(go())


def test_csrf_protection_and_demo_sandbox_login():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            r_demo = await cli.post("/api/v1/auth/demo", json={})
            assert r_demo.status == 200
            demo = await r_demo.json()
            assert demo["user"]["is_demo"] is True

            # Mutating call WITHOUT X-CSRF-Token must fail with 403
            no_csrf = {"Authorization": f"Bearer {demo['session_token']}"}
            r_fail = await cli.post("/api/v1/workspaces", headers=no_csrf, json={"name": "Blocked"})
            assert r_fail.status == 403

            # With valid X-CSRF-Token succeeds
            with_csrf = {
                "Authorization": f"Bearer {demo['session_token']}",
                "X-CSRF-Token": demo["csrf_token"],
            }
            r_ok = await cli.post("/api/v1/workspaces", headers=with_csrf, json={"name": "Allowed WS"})
            assert r_ok.status == 201

    run(go())


def test_strict_workspace_isolation_idor_and_secret_redaction():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr_a, ws_a = await _login_link(cli, 1001, "Alice")
            hdr_b, ws_b = await _login_link(cli, 2002, "Bob")
            assert ws_a != ws_b

            raw_tg_token = "123456789:AAH_SuperSecretBotFatherTokenValue12345"
            r_bot = await cli.post(
                f"/api/v1/workspaces/{ws_a}/bots",
                headers=hdr_a,
                json={
                    "display_name": "Alice Private Bot",
                    "username": "alice_priv_bot",
                    "template": "filestore",
                    "bot_token": raw_tg_token,
                    "allow_unverified_offline": True,
                },
            )
            assert r_bot.status == 201
            bot_a = (await r_bot.json())["bot"]
            bot_a_id = bot_a["bot_id"]

            # Secret token MUST NEVER appear in response JSON
            serialized = json.dumps(bot_a)
            assert raw_tg_token not in serialized
            assert "bot_token_encrypted" not in bot_a
            assert bot_a["has_credentials"] is True

            # Bob attempting IDOR against Alice's workspace or bot MUST get 403
            assert (await cli.get(f"/api/v1/workspaces/{ws_a}", headers=hdr_b)).status == 403
            assert (await cli.get(f"/api/v1/workspaces/{ws_a}/bots/{bot_a_id}", headers=hdr_b)).status == 403
            assert (await cli.delete(f"/api/v1/workspaces/{ws_a}/bots/{bot_a_id}", headers=hdr_b)).status == 403
            # Even if Bob passes his own workspace URL with Alice's bot_id, must get 404
            assert (await cli.get(f"/api/v1/workspaces/{ws_b}/bots/{bot_a_id}", headers=hdr_b)).status == 404

    run(go())


def test_team_rbac_and_anti_escalation():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr_owner, ws_id = await _login_link(cli, 3001, "Owner")
            hdr_viewer, _ = await _login_link(cli, 3002, "ViewerUser")

            r_inv = await cli.post(f"/api/v1/workspaces/{ws_id}/team", headers=hdr_owner, json={"user_id": 3002, "role": "viewer"})
            assert r_inv.status == 201

            # Viewer CAN read analytics & bots
            assert (await cli.get(f"/api/v1/workspaces/{ws_id}/bots", headers=hdr_viewer)).status == 200
            assert (await cli.get(f"/api/v1/workspaces/{ws_id}/analytics", headers=hdr_viewer)).status == 200

            # Viewer CANNOT create bots or escalate own role to administrator
            assert (await cli.post(f"/api/v1/workspaces/{ws_id}/bots", headers=hdr_viewer, json={"display_name": "HackBot"})).status == 403
            assert (await cli.post(f"/api/v1/workspaces/{ws_id}/team", headers=hdr_viewer, json={"user_id": 3002, "role": "administrator"})).status == 403

    run(go())


def test_config_dependencies_revisions_environments_and_commands():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, ws_id = await _login_link(cli, 4001, "DevOwner")
            r_bot = await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots",
                headers=hdr,
                json={"display_name": "Config Bot", "username": "cfg_test_bot", "template": "minimal"},
            )
            bot_id = (await r_bot.json())["bot"]["bot_id"]

            # Enabling force_sub_request_mode WITHOUT force_subscribe must fail dependency validation (400)
            r_dep = await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots/{bot_id}/config",
                headers=hdr,
                json={"mode": "apply", "features": {"force_sub_request_mode": True, "force_subscribe": False}},
            )
            assert r_dep.status == 400

            # Apply valid v2 config
            r_v2 = await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots/{bot_id}/config",
                headers=hdr,
                json={"mode": "apply", "settings": {"welcome_message": "Welcome v2!"}, "note": "v2 release"},
            )
            assert r_v2.status == 200
            assert (await r_v2.json())["bot"]["config_version"] == 2

            # Restore v1
            r_rest = await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots/{bot_id}/config",
                headers=hdr,
                json={"restore_version": 1},
            )
            assert r_rest.status == 200
            assert (await r_rest.json())["bot"]["settings"]["welcome_message"] != "Welcome v2!"

            # Environment promotion requires confirmation
            r_env_prev = await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots/{bot_id}/environments",
                headers=hdr,
                json={"source_env": "development", "target_env": "production", "confirmed": False},
            )
            assert (await r_env_prev.json())["requires_confirmation"] is True

            r_env_ok = await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots/{bot_id}/environments",
                headers=hdr,
                json={"source_env": "development", "target_env": "production", "confirmed": True},
            )
            assert (await r_env_ok.json())["promoted"] is True

            # Command Studio distinguishes built-in runtime handler from custom metadata command
            r_cmd = await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots/{bot_id}/commands",
                headers=hdr,
                json={"command": "mycustomfaq", "description": "Custom FAQ entry", "category": "Custom", "permission": "everyone"},
            )
            assert r_cmd.status == 200
            cmds = {c["command"]: c for c in (await r_cmd.json())["commands"]}
            assert cmds["start"]["handler_bound"] is True
            assert cmds["mycustomfaq"]["handler_bound"] is False

    run(go())


def test_clone_studio_excludes_secrets_and_supports_rollback():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, ws_id = await _login_link(cli, 5001, "CloneOwner")
            b1 = (await (await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots",
                headers=hdr,
                json={
                    "display_name": "Source Bot",
                    "username": "src_clone_bot",
                    "template": "filestore",
                    "bot_token": "999888777:AAH_SourceSecretTokenDoNotClone999",
                    "allow_unverified_offline": True,
                },
            )).json())["bot"]
            b2 = (await (await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots",
                headers=hdr,
                json={"display_name": "Target Bot", "username": "tgt_clone_bot", "template": "minimal"},
            )).json())["bot"]

            await cli.post(
                f"/api/v1/workspaces/{ws_id}/bots/{b1['bot_id']}/config",
                headers=hdr,
                json={"mode": "apply", "settings": {"welcome_message": "Cloned Greeting!"}},
            )

            r_prev = await cli.post(
                f"/api/v1/workspaces/{ws_id}/clones",
                headers=hdr,
                json={"action": "preview", "source_bot_id": b1["bot_id"], "target_bot_id": b2["bot_id"], "sections": ["welcome", "features"]},
            )
            assert r_prev.status == 200
            assert (await r_prev.json())["sensitive_fields_filtered"] is True

            r_exec = await cli.post(
                f"/api/v1/workspaces/{ws_id}/clones",
                headers=hdr,
                json={"action": "execute", "source_bot_id": b1["bot_id"], "target_bot_id": b2["bot_id"], "sections": ["welcome", "features"], "confirmed": True},
            )
            assert r_exec.status == 200
            exec_data = await r_exec.json()
            clone_id = exec_data["clone"]["clone_id"]
            tgt_after = exec_data["target_bot"]
            assert tgt_after["settings"]["welcome_message"] == "Cloned Greeting!"
            assert tgt_after["has_credentials"] is False

            # Rollback restores target bot's previous welcome_message
            r_rb = await cli.post(
                f"/api/v1/workspaces/{ws_id}/clones",
                headers=hdr,
                json={"action": "rollback", "clone_id": clone_id, "confirmed": True},
            )
            assert r_rb.status == 200
            tgt_restored = (await r_rb.json())["target_bot"]
            assert tgt_restored["settings"]["welcome_message"] != "Cloned Greeting!"

    run(go())


def test_filestore_upload_duplicate_intelligence_and_honest_analytics():
    async def go():
        shutil.rmtree(destinations.user_workspace_dir(6001), ignore_errors=True)
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, ws_id = await _login_link(cli, 6001, "VaultOwner")

            for fname in ("report_copy1.pdf", "report_copy2.pdf"):
                fd = FormData()
                fd.add_field("file", b"%PDF-1.4 identical payload bytes for dedupe check", filename=fname, content_type="application/pdf")
                r_up = await cli.post(f"/api/v1/workspaces/{ws_id}/filestore", headers=hdr, data=fd)
                assert r_up.status == 201

            r_intel = await cli.get(f"/api/v1/workspaces/{ws_id}/file-intelligence", headers=hdr)
            assert r_intel.status == 200
            intel = await r_intel.json()
            assert intel["total_files"] == 2
            assert intel["popular_files"] is None and intel["unused_files_count"] is None
            assert intel["integrity_issues"] is None
            assert len(intel["duplicate_candidates"]) == 1
            assert intel["duplicate_reclaimable_bytes"] > 0

            r_anl = await cli.get(f"/api/v1/workspaces/{ws_id}/analytics?days=7", headers=hdr)
            assert r_anl.status == 200
            anl = await r_anl.json()
            assert anl["totals"]["stored_files"] == 2
            assert anl["totals"]["telegram_impressions"] is None
            assert "telegram_impressions" in anl["unavailable_metrics"]

            r_csv = await cli.get(f"/api/v1/workspaces/{ws_id}/analytics.csv?days=7", headers=hdr)
            assert r_csv.status == 200
            assert "date,uploads,downloads" in await r_csv.text()
        shutil.rmtree(destinations.user_workspace_dir(6001), ignore_errors=True)

    run(go())


def test_automation_idempotency_loop_guard_api_keys_bundle_and_admin():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, ws_id = await _login_link(cli, 7001, "AutoOwner")

            r_wf = await cli.post(
                f"/api/v1/workspaces/{ws_id}/automations",
                headers=hdr,
                json={
                    "name": "Alert on upload",
                    "trigger": "file_uploaded",
                    "conditions": [{"field": "size_mb", "op": "gt", "value": 10}],
                    "actions": [{"type": "create_notification", "title": "Large Upload", "message": "File > 10MB"}],
                },
            )
            assert r_wf.status == 201
            wf_id = (await r_wf.json())["workflow"]["workflow_id"]

            r_run1 = await cli.post(
                f"/api/v1/workspaces/{ws_id}/automations",
                headers=hdr,
                json={"action": "run", "workflow_id": wf_id, "event_data": {"size_mb": 50}, "idempotency_key": "idem-1"},
            )
            assert (await r_run1.json())["run"]["status"] in ("completed", "executed")

            r_run2 = await cli.post(
                f"/api/v1/workspaces/{ws_id}/automations",
                headers=hdr,
                json={"action": "run", "workflow_id": wf_id, "event_data": {"size_mb": 50}, "idempotency_key": "idem-1"},
            )
            assert (await r_run2.json())["run"]["status"] == "skipped_duplicate"

            r_loop = await cli.post(
                f"/api/v1/workspaces/{ws_id}/automations",
                headers=hdr,
                json={"action": "run", "workflow_id": wf_id, "event_data": {"size_mb": 50, "_depth": 2}},
            )
            assert (await r_loop.json())["run"]["status"] == "blocked_loop_guard"

            r_key = await cli.post(
                f"/api/v1/workspaces/{ws_id}/api-keys",
                headers=hdr,
                json={"name": "ReadOnly Analytics Key", "scopes": ["analytics"]},
            )
            assert r_key.status == 201
            key_payload = await r_key.json()
            raw_key = key_payload["raw_key_once"]
            key_id = key_payload["key"]["key_id"]
            assert raw_key.startswith("vdk_")

            r_keys_list = await cli.get(f"/api/v1/workspaces/{ws_id}/api-keys", headers=hdr)
            listed_json = json.dumps(await r_keys_list.json())
            assert raw_key not in listed_json
            assert "key_hash" not in listed_json

            api_hdr = {"Authorization": f"Bearer {raw_key}"}
            assert (await cli.get(f"/api/v1/workspaces/{ws_id}/analytics", headers=api_hdr)).status == 200
            assert (await cli.post(f"/api/v1/workspaces/{ws_id}/bots", headers=api_hdr, json={"display_name": "Nope"})).status == 403

            await cli.post(f"/api/v1/workspaces/{ws_id}/api-keys", headers=hdr, json={"action": "revoke", "key_id": key_id})
            assert (await cli.get(f"/api/v1/workspaces/{ws_id}/analytics", headers=api_hdr)).status == 401

            r_exp = await cli.get(f"/api/v1/workspaces/{ws_id}/bundle", headers=hdr)
            bundle = (await r_exp.json())["bundle"]
            assert bundle["format"] == "videl-saas-bundle/1"

            r_imp_prev = await cli.post(f"/api/v1/workspaces/{ws_id}/bundle", headers=hdr, json={"bundle": bundle, "confirmed": False})
            assert (await r_imp_prev.json())["requires_confirmation"] is True
            r_imp_ok = await cli.post(f"/api/v1/workspaces/{ws_id}/bundle", headers=hdr, json={"bundle": bundle, "confirmed": True})
            assert (await r_imp_ok.json())["imported"] is True

            assert (await cli.get("/api/v1/admin/overview", headers=hdr)).status == 403
            r_adm_login = await cli.post("/api/v1/auth/login", json={"admin_token": "test_super_admin_secret_token_999"})
            adm_tok = (await r_adm_login.json())["session_token"]
            r_adm = await cli.get("/api/v1/admin/overview", headers={"Authorization": f"Bearer {adm_tok}"})
            assert r_adm.status == 200

    run(go())


def test_scoped_keys_cannot_escalate_or_read_other_domains():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, ws = await _login_link(cli, 81001, "Owner")
            key_res = await cli.post(f"/api/v1/workspaces/{ws}/api-keys", headers=hdr,
                                     json={"name": "Analytics", "scopes": ["analytics"]})
            key = (await key_res.json())["raw_key_once"]
            kh = {"Authorization": f"Bearer {key}"}
            assert (await cli.get(f"/api/v1/workspaces/{ws}/analytics", headers=kh)).status == 200
            for path in (f"/api/v1/workspaces/{ws}/bots", f"/api/v1/workspaces/{ws}/filestore",
                         f"/api/v1/workspaces/{ws}/api-keys", "/api/v1/auth/me", "/api/v1/workspaces"):
                assert (await cli.get(path, headers=kh)).status == 403
            assert (await cli.post(f"/api/v1/workspaces/{ws}/api-keys", headers=kh,
                                   json={"name": "Escalation", "scopes": ["team_management"]})).status == 403
    run(go())


def test_invite_cannot_overwrite_owner_or_delegate_unheld_permissions():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 82001, "Owner")
            admin, _ = await _login_link(cli, 82002, "Admin")
            r = await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                               json={"user_id": 82002, "role": "administrator", "permissions": {"bot_settings": False}})
            assert r.status == 201
            r = await cli.post(f"/api/v1/workspaces/{ws}/team", headers=admin,
                               json={"user_id": 82001, "role": "viewer"})
            assert r.status == 400
            membership = await db_models.get_membership(ws, 82001)
            assert membership["role"] == "owner"
            r = await cli.post(f"/api/v1/workspaces/{ws}/team", headers=admin,
                               json={"user_id": 82003, "role": "developer"})
            assert r.status == 403
    run(go())


def test_workflow_id_cannot_move_another_workspaces_record():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            ha, wa = await _login_link(cli, 83001, "Alice")
            hb, wb = await _login_link(cli, 83002, "Bob")
            body = {"name": "private workflow", "trigger": "bot_error", "actions": [{"type": "create_notification"}]}
            r = await cli.post(f"/api/v1/workspaces/{wa}/automations", headers=ha, json=body)
            workflow = (await r.json())["workflow"]
            body["workflow_id"] = workflow["workflow_id"]
            r = await cli.post(f"/api/v1/workspaces/{wb}/automations", headers=hb, json=body)
            assert r.status == 404
            assert await db_models.automations_col().find_one({"workspace_id": wa, "workflow_id": workflow["workflow_id"]})
    run(go())


def test_secret_storage_requires_encryption_and_token_verification(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, ws = await _login_link(cli, 84001, "Owner")
            monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
            bot_body = {"display_name": "Test bot", "bot_token": "123456:xxxxxxxxxxxxxxxxxxxxxxxxx"}
            r = await cli.post(f"/api/v1/workspaces/{ws}/bots", headers=hdr, json=bot_body)
            assert r.status == 400
            assert (await cli.post(f"/api/v1/workspaces/{ws}/integrations", headers=hdr,
                                   json={"provider": "dropbox", "access_token": "secret"})).status == 400
            monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

            async def unverified(_token):
                return {"verified_with_telegram": False, "diagnostic": "offline", "telegram_bot_id": 123456}
            monkeypatch.setattr(services_bots, "verify_telegram_token_diagnostic", unverified)
            bot_body["allow_unverified_offline"] = True
            r = await cli.post(f"/api/v1/workspaces/{ws}/bots", headers=hdr, json=bot_body)
            assert r.status == 400
    run(go())


def test_filestore_workspace_storage_rejects_unsafe_paths_and_bot_ids(tmp_path, monkeypatch):
    from webapp import services_clone_files as fs
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path))

    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, a = await _login_link(cli, 15001, "Owner")
            b = (await services_bots.create_workspace(15001, name="Second"))["workspace_id"]
            old = tmp_path / "platform_files" / "u_15001" / "legacy.txt"
            old.parent.mkdir(parents=True, exist_ok=True)
            old.write_text("private legacy content")
            assert (await (await cli.get(f"/api/v1/workspaces/{b}/filestore", headers=hdr)).json())["items"] == []
            form = FormData()
            form.add_field("file", b"workspace A only", filename="secret.txt", content_type="text/plain")
            up = await cli.post(f"/api/v1/workspaces/{a}/filestore", headers=hdr, data=form)
            assert up.status == 201
            entry = (await up.json())["uploaded"][0]
            physical = fs.resolve_workspace_file(a, 15001, entry["local_rel_path"])
            assert open(physical, "rb").read() == b"workspace A only"
            assert destinations.list_user_files(15001)["files"][0]["name"] == "legacy.txt"
            assert (await (await cli.get(f"/api/v1/workspaces/{b}/filestore", headers=hdr)).json())["items"] == []
            assert (await cli.post(f"/api/v1/workspaces/{b}/filestore", headers=hdr,
                                   json={"filename": "stolen.txt", "local_rel_path": entry["local_rel_path"]})).status == 400
            assert (await cli.post(f"/api/v1/workspaces/{b}/filestore/{entry['file_uid']}", headers=hdr,
                                   json={"action": "rename", "new_name": "stolen.txt"})).status == 404
            bot = await services_bots.create_or_register_bot(a, 15001, {"display_name": "A bot"}, allow_unverified_offline=True)
            bot_id = bot["bot_id"]
            invalid = FormData()
            invalid.add_field("file", b"no", filename="no.txt", content_type="text/plain")
            assert (await cli.post(f"/api/v1/workspaces/{b}/filestore?bot_id={bot_id}", headers=hdr, data=invalid)).status == 404
            assert (await cli.post(f"/api/v1/workspaces/{a}/filestore/{entry['file_uid']}", headers=hdr,
                                   json={"action": "move", "target_bot_id": "not_a_bot"})).status == 404
            assert (await cli.post(f"/api/v1/workspaces/{a}/filestore/{entry['file_uid']}", headers=hdr,
                                   json={"action": "copy"})).status == 400
            assert (await cli.post(f"/api/v1/workspaces/{a}/filestore/{entry['file_uid']}", headers=hdr,
                                   json={"action": "share_link"})).status == 400

            alias = dict(entry, file_uid="legacy_alias")
            await db_models.files_col().insert_one(alias)
            assert (await cli.post(f"/api/v1/workspaces/{a}/filestore/{entry['file_uid']}", headers=hdr,
                                   json={"action": "rename", "new_name": "renamed.txt"})).status == 400
            assert (await cli.post(f"/api/v1/workspaces/{a}/filestore/{entry['file_uid']}", headers=hdr,
                                   json={"action": "soft_delete", "confirmed": True})).status == 200
            assert (await cli.post(f"/api/v1/workspaces/{a}/filestore/{entry['file_uid']}", headers=hdr,
                                   json={"action": "purge", "confirmed": True})).status == 400
            assert open(physical, "rb").read() == b"workspace A only"
            await db_models.files_col().delete_one({"file_uid": "legacy_alias"})
            assert (await cli.post(f"/api/v1/workspaces/{a}/filestore/{entry['file_uid']}", headers=hdr,
                                   json={"action": "purge", "confirmed": True})).status == 200
            assert not __import__("os").path.exists(physical)
            assert old.read_text() == "private legacy content"

    run(go())


def test_filestore_rejected_upload_cleanup_and_demo_upload(tmp_path, monkeypatch):
    from webapp import services_clone_files as fs
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path))

    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, ws = await _login_link(cli, 15002)
            await fs.update_filestore_config(ws, "default", 15002, {"allowed_types": ["video"]})
            form = FormData()
            form.add_field("file", b"not allowed", filename="blocked.txt", content_type="text/plain")
            assert (await cli.post(f"/api/v1/workspaces/{ws}/filestore", headers=hdr, data=form)).status == 400
            assert not [p for p in tmp_path.rglob("*") if p.is_file()]
            demo = await (await cli.post("/api/v1/auth/demo", json={})).json()
            uid = demo["user"]["user_id"]
            assert uid < 0
            dh = {"Authorization": f"Bearer {demo['session_token']}", "X-CSRF-Token": demo["csrf_token"]}
            dws = demo["workspaces"][0]["workspace_id"]
            form = FormData()
            form.add_field("file", b"demo", filename="demo.txt", content_type="text/plain")
            response = await cli.post(f"/api/v1/workspaces/{dws}/filestore", headers=dh, data=form)
            assert response.status == 201
            entry = (await response.json())["uploaded"][0]
            assert open(fs.resolve_workspace_file(dws, uid, entry["local_rel_path"]), "rb").read() == b"demo"
            assert destinations.user_workspace_dir(uid).endswith(f"u_{uid}")

    run(go())


def test_filestore_permissions_search_and_path_escape(tmp_path, monkeypatch):
    from webapp import services_clone_files as fs
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path))

    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 15003)
            viewer, _ = await _login_link(cli, 15004)
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                   json={"user_id": 15004, "role": "viewer"})).status == 201
            form = FormData()
            form.add_field("file", b"private", filename="uniquefilename.txt", content_type="text/plain")
            assert (await cli.post(f"/api/v1/workspaces/{ws}/filestore", headers=owner, data=form)).status == 201
            assert (await cli.get(f"/api/v1/workspaces/{ws}/filestore", headers=viewer)).status == 403
            assert (await cli.get(f"/api/v1/workspaces/{ws}/file-intelligence", headers=viewer)).status == 403
            search = await services_ops.global_workspace_search(ws, 15004, "uniquefilename")
            assert not any(r["type"] == "file" for r in search["results"])
            with pytest.raises(services_bots.SaaSValidationError):
                fs.resolve_workspace_file(ws, 15003, fs.workspace_file_prefix(ws) + "../escape", must_exist=False)
            base = __import__("pathlib").Path(fs.workspace_storage_user_dir(15003))
            root = base / fs.workspace_file_prefix(ws)
            outside = tmp_path / "outside"
            outside.mkdir()
            (root / "linked").symlink_to(outside, target_is_directory=True)
            with pytest.raises(PermissionError):
                fs.resolve_workspace_file(ws, 15003, fs.workspace_file_prefix(ws) + "linked/evil", must_exist=False)

    run(go())


def test_filestore_failed_database_write_cleans_upload(tmp_path, monkeypatch):
    from webapp import services_clone_files as fs
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path))

    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, ws = await _login_link(cli, 15005)
            async def broken_insert(*args, **kwargs):
                raise RuntimeError("simulated database failure")
            monkeypatch.setattr(fs, "register_filestore_entry", broken_insert)
            form = FormData()
            form.add_field("file", b"never leave this behind", filename="error.txt", content_type="text/plain")
            assert (await cli.post(f"/api/v1/workspaces/{ws}/filestore", headers=hdr, data=form)).status == 500
            assert not [p for p in tmp_path.rglob("*") if p.is_file()]
    run(go())


def test_domain_permissions_apply_to_reads_and_multi_domain_bundles():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 16001, "Owner")
            viewer, _ = await _login_link(cli, 16002, "Viewer")
            restricted, _ = await _login_link(cli, 16003, "Limited")
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                   json={"user_id": 16002, "role": "viewer"})).status == 201
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                   json={"user_id": 16003, "role": "developer", "permissions": {"templates": False}})).status == 201
            bot = await services_bots.create_or_register_bot(ws, 16001, {"display_name": "Sample"}, allow_unverified_offline=True)
            bid = bot["bot_id"]
            for path in ("team", "clones", "downloads", "studio", "automations", "schedules", "templates", "integrations", "notifications",
                         f"bots/{bid}/config", f"bots/{bid}/environments", f"bots/{bid}/commands"):
                assert (await cli.get(f"/api/v1/workspaces/{ws}/{path}", headers=viewer)).status == 403, path
            assert (await cli.get(f"/api/v1/workspaces/{ws}/bots/{bid}/commands", headers=owner)).status == 200
            viewer_bot = (await (await cli.get(f"/api/v1/workspaces/{ws}/bots/{bid}", headers=viewer)).json())["bot"]
            assert "settings" not in viewer_bot and "features" not in viewer_bot and "last_error" not in viewer_bot
            owner_bot = (await (await cli.get(f"/api/v1/workspaces/{ws}/bots/{bid}", headers=owner)).json())["bot"]
            assert "settings" in owner_bot and "features" in owner_bot
            fleet = await (await cli.get(f"/api/v1/workspaces/{ws}/fleet", headers=viewer)).json()
            assert fleet["config_drift_fields"] is None and fleet["recent_errors"] is None
            assert "settings" not in fleet["bots"][0]
            analyst, _ = await _login_link(cli, 16006, "Analyst")
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                   json={"user_id": 16006, "role": "analyst"})).status == 201
            await db_models.webhooks_col().insert_one({"workspace_id": ws, "webhook_id": "private_hook", "url": "https://example.com/private"})
            health = await (await cli.get(f"/api/v1/workspaces/{ws}/health", headers=analyst)).json()
            assert health["webhooks"]["availability"] == "permission required"
            assert "private_hook" not in json.dumps(health)
            for method in (cli.get, cli.post):
                assert (await method(f"/api/v1/workspaces/{ws}/bundle", headers=restricted)).status == 403
            assert (await cli.post(f"/api/v1/workspaces/{ws}/schedules", headers=owner, json={"action": "run_due"})).status == 400
            info = await (await cli.get(f"/api/v1/workspaces/{ws}", headers=viewer)).json()
            assert info["recent_notifications"] == [] and info["usage"]["unread_notifications"] is None
    run(go())


def test_webhook_blocks_private_dns_redirects_and_url_disclosure(monkeypatch):
    import socket
    import aiohttp
    from aiohttp import web
    from webapp import safe_webhook
    from platform_core import providers

    async def go():
        monkeypatch.setenv("LEECH_ALLOW_PRIVATE", "true")
        for url in ("http://127.0.0.1/", "http://[::1]/", "https://user:secret@example.com/",
                    "https://example.com/path?token=secret", "https://example.com/path#secret"):
            with pytest.raises(providers.ProviderError):
                safe_webhook.validate_webhook_url(url)
        resolver = safe_webhook.PublicResolver()
        async def mixed(host, port=0, family=socket.AF_UNSPEC):
            return [{"hostname": host, "host": "8.8.8.8", "port": port, "family": socket.AF_INET, "proto": 0, "flags": 0},
                    {"hostname": host, "host": "127.0.0.1", "port": port, "family": socket.AF_INET, "proto": 0, "flags": 0}]
        monkeypatch.setattr(resolver._resolver, "resolve", mixed)
        with pytest.raises(OSError):
            await resolver.resolve("rebind.example", 80)
        await resolver.close()

        async def rebound(self, host, port=0, family=socket.AF_UNSPEC):
            return [{"hostname": host, "host": "127.0.0.1", "port": port, "family": socket.AF_INET, "proto": 0, "flags": 0}]
        with monkeypatch.context() as dns_patch:
            dns_patch.setattr(safe_webhook.aiohttp.resolver.DefaultResolver, "resolve", rebound)
            with pytest.raises(aiohttp.ClientError):
                await safe_webhook.ping("http://rebind.invalid/hook", {})

        calls = []
        async def redirect(request):
            calls.append("redirect")
            raise web.HTTPFound(location="/private")
        async def private(request):
            calls.append("private")
            return web.Response(text="must not be reached")
        app = web.Application()
        app.router.add_post("/hook", redirect)
        app.router.add_get("/private", private)
        async with TestServer(app) as server:
            async def test_only_resolve(self, host, port=0, family=socket.AF_UNSPEC):
                return [{"hostname": host, "host": "127.0.0.1", "port": port, "family": socket.AF_INET, "proto": 0, "flags": 0}]
            # A local fake public host exercises redirect behavior without network access.
            monkeypatch.setattr(safe_webhook.PublicResolver, "resolve", test_only_resolve)
            assert await safe_webhook.ping(f"http://public.test:{server.port}/hook", {}) == 302
            assert calls == ["redirect"]

        hook = await services_ops.register_outbound_webhook("ws_test", 1, {"url": "https://example.com/secret-path"})
        assert "url" not in hook
        await db_models.webhook_events_col().insert_one({"workspace_id": "ws_test", "created_ts": 1,
            "error": "https://example.com/?token=secret"})
        listing = await services_ops.list_webhooks_and_events("ws_test")
        assert "secret" not in json.dumps(listing)
        async def failing_ping(url, body):
            raise OSError("https://example.com/?token=secret")
        monkeypatch.setattr(safe_webhook, "ping", failing_ping)
        result = await services_ops.test_outbound_webhook("ws_test", hook["webhook_id"], 1)
        assert result["status"] == "failed" and "secret" not in json.dumps(result)
    run(go())


def test_analytics_does_not_attribute_userwide_history_or_foreign_bots(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, a = await _login_link(cli, 16004, "Owner")
            b = (await services_bots.create_workspace(16004, name="Second"))["workspace_id"]
            bot = await services_bots.create_or_register_bot(a, 16004, {"display_name": "One"}, allow_unverified_offline=True)
            async def forbidden_history(*args, **kwargs):
                raise AssertionError("user-wide history must not be used for workspace analytics")
            from platform_core import models_db
            monkeypatch.setattr(models_db, "query_history", forbidden_history)
            response = await cli.get(f"/api/v1/workspaces/{b}/analytics", headers=hdr)
            assert response.status == 200
            data = await response.json()
            assert data["totals"]["period_downloads"] is None
            assert data["totals"]["failed_uploads"] is None
            assert data["series"]["downloads"] is None
            assert data["command_usage"] is None
            assert (await cli.get(f"/api/v1/workspaces/{b}/analytics?bot_id={bot['bot_id']}", headers=hdr)).status == 404
            csv = await (await cli.get(f"/api/v1/workspaces/{b}/analytics.csv?days=1", headers=hdr)).text()
            assert ",," in csv  # Missing download values are blank, not false zeroes.
    run(go())


def test_workspace_bot_listing_does_not_automatically_assign_legacy_bots(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            hdr, a = await _login_link(cli, 16005)
            b = (await services_bots.create_workspace(16005, name="Other"))["workspace_id"]
            async def unsafe_import(*args, **kwargs):
                raise AssertionError("legacy bots must not be assigned to an arbitrary workspace")
            monkeypatch.setattr(services_bots, "sync_legacy_registered_bots", unsafe_import)
            assert (await cli.get(f"/api/v1/workspaces/{a}/bots", headers=hdr)).status == 200
            assert (await cli.get(f"/api/v1/workspaces/{b}/bots", headers=hdr)).status == 200
    run(go())


def test_api_key_delegation_and_rotation_do_not_exceed_actor_scopes():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 17001)
            admin, _ = await _login_link(cli, 17002)
            denied, _ = await _login_link(cli, 17003)
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner, json={
                "user_id": 17002, "role": "administrator", "permissions": {"bot_settings": False}})).status == 201
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner, json={
                "user_id": 17003, "role": "administrator", "permissions": {"integrations": False}})).status == 201
            uri = f"/api/v1/workspaces/{ws}/api-keys"
            result = await cli.post(uri, headers=owner, json={"name": "Owner bot key", "scopes": ["bot_settings"]})
            assert result.status == 201
            key = await result.json()
            token = {"Authorization": f"Bearer {key['raw_key_once']}"}
            assert (await cli.post(uri, headers=admin, json={"name": "Escalation", "scopes": ["bot_settings"]})).status == 403
            assert (await cli.post(uri, headers=admin, json={"action": "rotate", "key_id": key["key_id"]})).status == 403
            assert (await cli.get(f"/api/v1/workspaces/{ws}/bots", headers=token)).status == 200
            assert (await cli.post(uri, headers=denied, json={"name": "No access", "scopes": ["analytics"]})).status == 403
            assert (await cli.post(uri, headers=owner, json={"name": "Invalid", "scopes": "analytics"})).status == 400
            allowed = await cli.post(uri, headers=admin, json={"name": "Analyst key", "scopes": ["analytics"]})
            assert allowed.status == 201
            assert "raw_key_once" in await allowed.json()
    run(go())


def test_invitation_rechecks_membership_atomically_and_validates_permission_types(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 17004)
            uri = f"/api/v1/workspaces/{ws}/team"
            invalid = await cli.post(uri, headers=owner, json={"user_id": 17005, "role": "viewer", "permissions": {"bot_settings": "false"}})
            assert invalid.status == 400
            assert not await db_models.memberships_col().find_one({"workspace_id": ws, "user_id": 17005})
            real = db_models.memberships_col()
            class RaceCollection:
                def __getattr__(self, name):
                    return getattr(real, name)
                async def update_one(self, query, update, **kwargs):
                    await real.insert_one({"workspace_id": ws, "user_id": 17005, "role": "owner", "permissions": {}})
                    return await real.update_one(query, update, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(db_models, "memberships_col", lambda: RaceCollection())
                response = await cli.post(uri, headers=owner, json={"user_id": 17005, "role": "viewer"})
            assert response.status == 400
            assert (await real.find_one({"workspace_id": ws, "user_id": 17005}))["role"] == "owner"
            assert await db_models.invites_col().count_documents({"workspace_id": ws, "target_user_id": 17005}) == 0
    run(go())


def test_member_role_races_do_not_modify_or_revoke_newly_promoted_peer(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 17006)
            admin, _ = await _login_link(cli, 17007)
            uri = f"/api/v1/workspaces/{ws}/team"
            assert (await cli.post(uri, headers=owner, json={"user_id": 17007, "role": "administrator"})).status == 201
            assert (await cli.post(uri, headers=owner, json={"user_id": 17008, "role": "viewer"})).status == 201
            real = db_models.memberships_col()
            class RaceCollection:
                def __getattr__(self, name):
                    return getattr(real, name)
                async def update_one(self, query, update, **kwargs):
                    await real.update_one({"workspace_id": ws, "user_id": 17008}, {"$set": {"role": "administrator"}})
                    return await real.update_one(query, update, **kwargs)
                async def delete_one(self, query):
                    await real.update_one({"workspace_id": ws, "user_id": 17008}, {"$set": {"role": "administrator"}})
                    return await real.delete_one(query)
            with monkeypatch.context() as patch:
                patch.setattr(db_models, "memberships_col", lambda: RaceCollection())
                result = await cli.patch(f"{uri}/17008", headers=admin, json={"role": "developer"})
            assert result.status == 400
            assert (await real.find_one({"workspace_id": ws, "user_id": 17008}))["role"] == "administrator"
            await real.update_one({"workspace_id": ws, "user_id": 17008}, {"$set": {"role": "viewer"}})
            with monkeypatch.context() as patch:
                patch.setattr(db_models, "memberships_col", lambda: RaceCollection())
                result = await cli.delete(f"{uri}/17008", headers=admin)
            assert result.status == 400
            assert (await real.find_one({"workspace_id": ws, "user_id": 17008}))["role"] == "administrator"
    run(go())


def test_invite_fails_closed_if_unique_index_cannot_be_established(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 17009)
            real = db_models.memberships_col()
            class BrokenIndex:
                def __getattr__(self, name):
                    return getattr(real, name)
                async def create_index(self, *args, **kwargs):
                    raise RuntimeError("index creation failure")
            with monkeypatch.context() as patch:
                patch.setattr(db_models, "memberships_col", lambda: BrokenIndex())
                response = await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                          json={"user_id": 17010, "role": "viewer"})
            assert response.status == 500
            assert await real.find_one({"workspace_id": ws, "user_id": 17010}) is None
            assert await db_models.invites_col().count_documents({"workspace_id": ws, "target_user_id": 17010}) == 0
    run(go())


def test_clone_section_permissions_confirmation_and_unsupported_sections():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 18001)
            actor, _ = await _login_link(cli, 18002)
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                   json={"user_id": 18002, "role": "manager", "permissions": {"commands": False, "filestore": False, "automation": False}})).status == 201
            a = await services_bots.create_or_register_bot(ws, 18001, {"display_name": "Source"}, allow_unverified_offline=True)
            b = await services_bots.create_or_register_bot(ws, 18001, {"display_name": "Target"}, allow_unverified_offline=True)
            url = f"/api/v1/workspaces/{ws}/clones"
            payload = {"action": "preview", "source_bot_id": a["bot_id"], "target_bot_id": b["bot_id"]}
            for section in ("commands", "filestore", "automations"):
                assert (await cli.post(url, headers=actor, json={**payload, "sections": [section]})).status == 403
            assert (await cli.post(url, headers=actor, json={"action": "inspect", "bot_id": a["bot_id"]})).status == 403
            assert (await cli.post(url, headers=owner, json={**payload, "sections": []})).status == 400
            assert (await cli.post(url, headers=owner, json={**payload, "sections": ["permissions_template"]})).status == 400
            assert (await cli.post(url, headers=actor, json={**payload, "action": "execute", "sections": ["welcome"]})).status == 400
            assert (await cli.post(url, headers=actor, json={**payload, "action": "preview", "sections": ["welcome"]})).status == 200
            commands_user, _ = await _login_link(cli, 18005)
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                   json={"user_id": 18005, "role": "developer", "permissions": {"bot_settings": False}})).status == 201
            assert (await cli.post(url, headers=commands_user, json={**payload, "sections": ["commands"]})).status == 200
            assert (await cli.post(url, headers=commands_user, json={**payload, "action": "execute", "sections": ["commands"], "confirmed": True})).status == 403
            result = await cli.post(url, headers=owner, json={**payload, "action": "execute", "sections": ["commands"], "confirmed": True})
            assert result.status == 200
            clone = (await result.json())["clone"]
            assert clone["rollback_supported"] is False
            assert (await cli.post(url, headers=owner, json={"action": "rollback", "clone_id": clone["clone_id"], "confirmed": True})).status == 400
    run(go())


def test_clone_rollback_preserves_unselected_fields_and_blocks_conflicts():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 18003)
            a = await services_bots.create_or_register_bot(ws, 18003, {"display_name": "Source"}, allow_unverified_offline=True)
            b = await services_bots.create_or_register_bot(ws, 18003, {"display_name": "Target"}, allow_unverified_offline=True)
            source, target = a["bot_id"], b["bot_id"]
            await db_models.bots_col().update_one({"bot_id": source}, {"$set": {"settings.welcome_message": "New welcome"}})
            await db_models.bots_col().update_one({"bot_id": target}, {"$set": {"settings.welcome_message": "Original", "settings.api_token": "retain-target-secret"}})
            url = f"/api/v1/workspaces/{ws}/clones"
            body = {"action": "execute", "source_bot_id": source, "target_bot_id": target, "sections": ["welcome"], "confirmed": True}
            response = await cli.post(url, headers=owner, json=body)
            assert response.status == 200
            clone = (await response.json())["clone"]
            assert clone["rollback_supported"]
            raw = await db_models.bots_col().find_one({"bot_id": target})
            assert raw["settings"]["api_token"] == "retain-target-secret"
            public = await (await cli.get(f"/api/v1/workspaces/{ws}/bots/{target}", headers=owner)).json()
            assert "retain-target-secret" not in json.dumps(public)
            await db_models.bots_col().update_one({"bot_id": target}, {"$set": {"category": "Unrelated edit", "settings.custom_caption": "Keep this"}})
            assert (await cli.post(url, headers=owner, json={"action": "rollback", "clone_id": clone["clone_id"]})).status == 400
            response = await cli.post(url, headers=owner, json={"action": "rollback", "clone_id": clone["clone_id"], "confirmed": True})
            assert response.status == 200
            raw = await db_models.bots_col().find_one({"bot_id": target})
            assert raw["category"] == "Unrelated edit" and raw["settings"]["custom_caption"] == "Keep this"
            assert raw["settings"]["welcome_message"] == "Original" and raw["settings"]["api_token"] == "retain-target-secret"
            response = await cli.post(url, headers=owner, json=body)
            clone2 = (await response.json())["clone"]
            await db_models.bots_col().update_one({"bot_id": target}, {"$set": {"settings.welcome_message": "Later change"}})
            response = await cli.post(url, headers=owner, json={"action": "rollback", "clone_id": clone2["clone_id"], "confirmed": True})
            assert response.status == 400
            assert (await db_models.bots_col().find_one({"bot_id": target}))["settings"]["welcome_message"] == "Later change"
    run(go())


def test_clone_rollback_compare_and_set_does_not_overwrite_concurrent_change(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 18004)
            a = await services_bots.create_or_register_bot(ws, 18004, {"display_name": "From"}, allow_unverified_offline=True)
            b = await services_bots.create_or_register_bot(ws, 18004, {"display_name": "To"}, allow_unverified_offline=True)
            url = f"/api/v1/workspaces/{ws}/clones"
            response = await cli.post(url, headers=owner, json={"action": "execute", "source_bot_id": a["bot_id"],
                "target_bot_id": b["bot_id"], "sections": ["welcome"], "confirmed": True})
            assert response.status == 200
            clone_id = (await response.json())["clone"]["clone_id"]
            real = db_models.bots_col()
            class ConcurrentBotWrite:
                def __getattr__(self, name):
                    return getattr(real, name)
                async def update_one(self, query, update, **kwargs):
                    await real.update_one({"workspace_id": ws, "bot_id": b["bot_id"]}, {"$set": {"settings.welcome_message": "Concurrent edit"}})
                    return await real.update_one(query, update, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(db_models, "bots_col", lambda: ConcurrentBotWrite())
                result = await cli.post(url, headers=owner, json={"action": "rollback", "clone_id": clone_id, "confirmed": True})
            assert result.status == 400
            assert (await real.find_one({"bot_id": b["bot_id"]}))["settings"]["welcome_message"] == "Concurrent edit"
            assert not (await db_models.clones_col().find_one({"clone_id": clone_id}))["rolled_back"]
    run(go())


def test_workspace_overview_respects_domains_and_counts_all_unread(tmp_path, monkeypatch):
    from webapp import services_clone_files as fs
    monkeypatch.setattr(config, "DOWNLOAD_DIR", str(tmp_path))

    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 19001, "Owner")
            viewer, _ = await _login_link(cli, 19002, "Viewer")
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                   json={"user_id": 19002, "role": "viewer"})).status == 201
            form = FormData()
            form.add_field("file", b"confidential file bytes", filename="private.txt", content_type="text/plain")
            assert (await cli.post(f"/api/v1/workspaces/{ws}/filestore", headers=owner, data=form)).status == 201
            await db_models.notifications_col().update_many({"workspace_id": ws}, {"$set": {"read": True}})
            for i in range(12):
                await db_models.create_notification(ws, category="test", title=f"Alert {i}", message="private")
            await db_models.create_notification("another-workspace", category="test", title="Other", message="private")
            owner_response = await cli.get(f"/api/v1/workspaces/{ws}", headers=owner)
            assert owner_response.status == 200
            data = await owner_response.json()
            assert data["usage"]["files_count"] == 1
            assert data["usage"]["storage_bytes"] == len(b"confidential file bytes")
            assert data["usage"]["unread_notifications"] == 12
            assert len(data["recent_notifications"]) == 10

            async def forbidden_query(*args, **kwargs):
                raise AssertionError("unauthorized file query")
            monkeypatch.setattr(fs, "query_filestore_files", forbidden_query)
            viewer_response = await cli.get(f"/api/v1/workspaces/{ws}", headers=viewer)
            assert viewer_response.status == 200
            data = await viewer_response.json()
            assert data["usage"]["files_count"] is None
            assert data["usage"]["storage_bytes"] is None
            assert data["usage"]["filestore_sample_limit"] is None
            assert data["usage"]["unread_notifications"] is None
            assert data["recent_notifications"] == []

    run(go())


def test_production_environment_requires_explicit_boolean_confirmation():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 19003, "Owner")
            bot = await services_bots.create_or_register_bot(ws, 19003, {"display_name": "Env Bot"}, allow_unverified_offline=True)
            url = f"/api/v1/workspaces/{ws}/bots/{bot['bot_id']}/environments"
            assert (await cli.get(url, headers=owner)).status == 200
            before = await db_models.bot_envs_col().find_one({"bot_id": bot["bot_id"], "env": "production"})
            for value in (None, False, "false", 1):
                payload = {"source_env": "production", "settings": {"welcome_message": "Changed"}, "confirmed": value}
                assert (await cli.post(url, headers=owner, json=payload)).status == 400
                after = await db_models.bot_envs_col().find_one({"bot_id": bot["bot_id"], "env": "production"})
                assert after == before
                response = await cli.post(url, headers=owner, json={"source_env": "development", "target_env": "production", "confirmed": value})
                assert response.status == 200 and (await response.json())["preview"] is True
                assert await db_models.bot_envs_col().find_one({"bot_id": bot["bot_id"], "env": "production"}) == before
            assert (await cli.post(url, headers=owner, json={"source_env": "production", "settings": {"welcome_message": "Changed"}, "confirmed": True})).status == 200
            assert (await db_models.bot_envs_col().find_one({"bot_id": bot["bot_id"], "env": "production"}))["settings"]["welcome_message"] == "Changed"
    run(go())


def test_bulk_and_bundle_require_true_boolean_confirmation():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 19004, "Owner")
            bot = await services_bots.create_or_register_bot(ws, 19004, {"display_name": "Fleet Bot"}, allow_unverified_offline=True)
            fleet_url = f"/api/v1/workspaces/{ws}/fleet"
            bundle_url = f"/api/v1/workspaces/{ws}/bundle"
            bundle = {"format": services_ops.BUNDLE_FORMAT, "templates": [{"name": "Example", "category": "bot_config", "payload": {"setting": "value"}}]}
            for confirmation in (None, False, "false", 1):
                fleet = await cli.post(fleet_url, headers=owner, json={"bot_ids": [bot["bot_id"]], "operation": "archive", "confirmed": confirmation})
                assert fleet.status == 200 and (await fleet.json())["preview"] is True
                assert not (await services_bots.get_owned_workspace_bot(ws, bot["bot_id"])).get("archived")
                imported = await cli.post(bundle_url, headers=owner, json={"bundle": bundle, "confirmed": confirmation})
                assert imported.status == 200 and (await imported.json())["preview"] is True
                assert await db_models.templates_col().count_documents({"workspace_id": ws}) == 0
            assert (await (await cli.post(fleet_url, headers=owner, json={"bot_ids": [bot["bot_id"]], "operation": "archive", "confirmed": True})).json())["executed"] is True
            assert (await services_bots.get_owned_workspace_bot(ws, bot["bot_id"]))["archived"] is True
            assert (await (await cli.post(bundle_url, headers=owner, json={"bundle": bundle, "confirmed": True})).json())["imported"] is True
            assert await db_models.templates_col().count_documents({"workspace_id": ws}) == 1
    run(go())


def test_automation_actions_enforce_cross_domain_permissions_and_isolation():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 20001, "Owner")
            limited, _ = await _login_link(cli, 20002, "Limited")
            other, other_ws = await _login_link(cli, 20003, "Other")
            assert (await cli.post(f"/api/v1/workspaces/{ws}/team", headers=owner,
                                   json={"user_id": 20002, "role": "manager", "permissions": {"analytics": False, "filestore": False}})).status == 201
            url = f"/api/v1/workspaces/{ws}/automations"
            def payload(action):
                return {"name": "Test workflow", "trigger": "file_uploaded", "actions": [{"type": action}]}
            for action in ("generate_report", "inspect_eligible_files"):
                assert (await cli.post(url, headers=limited, json=payload(action))).status == 403
            for action in ("retry_failed_task", "apply_template_defaults"):
                assert (await cli.post(url, headers=owner, json=payload(action))).status == 400
            other_bot = await services_bots.create_or_register_bot(other_ws, 20003, {"display_name": "Other Bot"}, allow_unverified_offline=True)
            assert (await cli.post(url, headers=owner, json={**payload("record_activity"), "bot_id": other_bot["bot_id"]})).status == 404
            good = await cli.post(url, headers=limited, json=payload("create_notification"))
            assert good.status == 201
            assert (await cli.post(url, headers=limited, json={"action": "run", "workflow_id": (await good.json())["workflow"]["workflow_id"]})).status == 200
            r = await cli.post(url, headers=owner, json=payload("inspect_eligible_files"))
            assert r.status == 201
            wf_id = (await r.json())["workflow"]["workflow_id"]
            run_request = {"action": "run", "workflow_id": wf_id}
            assert (await cli.post(url, headers=limited, json=run_request)).status == 403
            assert (await cli.post(url, headers=owner, json=run_request)).status == 200
            rows = (await (await cli.get(url, headers=limited)).json())["runs"]
            report = next(row for row in rows if row["workflow_id"] == wf_id)
            assert report["results"] == [{"redacted": True}]
            owner_rows = (await (await cli.get(url, headers=owner)).json())["runs"]
            assert next(row for row in owner_rows if row["workflow_id"] == wf_id)["results"][0]["action"] == "inspect_eligible_files"
            key_response = await cli.post(f"/api/v1/workspaces/{ws}/api-keys", headers=owner, json={"name": "Automation only", "scopes": ["automation"]})
            key = (await key_response.json())["raw_key_once"]
            key_headers = {"Authorization": f"Bearer {key}"}
            assert (await cli.post(url, headers=key_headers, json=run_request)).status == 403
            key_rows = (await (await cli.get(url, headers=key_headers)).json())["runs"]
            assert next(row for row in key_rows if row["workflow_id"] == wf_id)["results"] == [{"redacted": True}]
            assert (await cli.post(f"/api/v1/workspaces/{other_ws}/automations", headers=other, json=run_request)).status == 404
    run(go())


def test_legacy_unsupported_automation_fails_before_any_side_effects():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await _login_link(cli, 20004, "Owner")
            wf_id = "legacy_workflow"
            await db_models.automations_col().insert_one({"workspace_id": ws, "workflow_id": wf_id, "bot_id": "all", "name": "Legacy", "trigger": "task_failed", "actions": [{"type": "create_notification"}, {"type": "retry_failed_task"}]})
            before = await db_models.notifications_col().count_documents({"workspace_id": ws})
            response = await cli.post(f"/api/v1/workspaces/{ws}/automations", headers=owner, json={"action": "run", "workflow_id": wf_id})
            assert response.status == 400
            assert await db_models.notifications_col().count_documents({"workspace_id": ws}) == before
            assert await db_models.automation_runs_col().count_documents({"workspace_id": ws}) == 0
    run(go())


def test_automation_run_count_and_hourly_cap_are_workspace_scoped():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner_a, ws_a = await _login_link(cli, 20005, "A")
            _, ws_b = await _login_link(cli, 20006, "B")
            common = {"workflow_id": "same_id", "name": "Scoped", "bot_id": "all", "trigger": "file_uploaded", "actions": [{"type": "record_activity"}], "conditions": [], "run_count": 0}
            await db_models.automations_col().insert_one({**common, "workspace_id": ws_b})
            await db_models.automations_col().insert_one({**common, "workspace_id": ws_a})
            import time
            for i in range(30):
                await db_models.automation_runs_col().insert_one({"workspace_id": ws_b, "workflow_id": "same_id", "created_ts": time.time(), "run_id": str(i)})
            response = await cli.post(f"/api/v1/workspaces/{ws_a}/automations", headers=owner_a, json={"action": "run", "workflow_id": "same_id"})
            assert response.status == 200 and (await response.json())["run"]["status"] == "completed"
            assert (await db_models.automations_col().find_one({"workspace_id": ws_a, "workflow_id": "same_id"}))["run_count"] == 1
            assert (await db_models.automations_col().find_one({"workspace_id": ws_b, "workflow_id": "same_id"}))["run_count"] == 0
    run(go())

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
            assert (await r_prev.json())["credentials_stripped"] is True

            r_exec = await cli.post(
                f"/api/v1/workspaces/{ws_id}/clones",
                headers=hdr,
                json={"action": "execute", "source_bot_id": b1["bot_id"], "target_bot_id": b2["bot_id"], "sections": ["welcome", "features"]},
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
                json={"action": "rollback", "clone_id": clone_id},
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

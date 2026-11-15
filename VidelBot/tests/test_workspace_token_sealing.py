"""Workspace credentials must fail closed even after asynchronous verification."""
import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from tests.harness import load_all, no_botapi, reset_db, run

load_all()
import keep_alive
from filestore.utils import security
from webapp import auth, db_models, services_bots
from tests.test_command_create_contract import login


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    run(reset_db()); no_botapi(); db_models.reset_memory_collections(); auth.reset_auth_state()
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())


def diagnostic():
    return {"verified_with_telegram": True, "telegram_bot_id": 987654321,
            "username": "verified_bot", "first_name": "", "token_masked": "****",
            "diagnostic": "Verified by test stub"}


@pytest.mark.parametrize("failure", ["removed", "changed", "encrypt_error"])
@pytest.mark.parametrize("operation", ["create", "rotate"])
def test_workspace_token_failures_do_not_persist_credentials(monkeypatch, failure, operation):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 79001)
            col = db_models.bots_col()
            path = f"/api/v1/workspaces/{ws}/bots"
            if operation == "rotate":
                async def initial(_): return diagnostic()
                monkeypatch.setattr(services_bots, "verify_telegram_token_diagnostic", initial)
                created = await client.post(path, headers=owner, json={"display_name": "Original", "bot_token": "987654321:original-secret"})
                assert created.status == 201
                before = await col.find_one({"telegram_bot_id": 987654321})
                assert security.decrypt_token(before["token_sealed"]) == "987654321:original-secret"
            async def verify(_):
                if failure == "removed": monkeypatch.delenv("ENCRYPTION_KEY")
                elif failure == "changed": monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
                else:
                    cipher = security._get_fernet()
                    def fail(_value): raise RuntimeError("private synthetic-secret")
                    monkeypatch.setattr(cipher, "encrypt", fail)
                return diagnostic()
            monkeypatch.setattr(services_bots, "verify_telegram_token_diagnostic", verify)
            secret = "987654321:synthetic-secret"
            if operation == "create":
                response = await client.post(path, headers=owner, json={"display_name": "Not saved", "bot_token": secret})
            else:
                response = await client.patch(f"{path}/{before['bot_id']}", headers=owner, json={"rotate_token": secret})
            text = await response.text()
            assert response.status == 400, text
            assert secret not in text and "private synthetic-secret" not in text
            assert "Unable to encrypt credentials" in text
            if operation == "create": assert await col.count_documents({"workspace_id": ws}) == 0
            else: assert await col.find_one({"telegram_bot_id": 987654321}) == before
    run(go())


def test_saved_workspace_token_remains_compatible_with_existing_decryptor(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 79002)
            async def verify(_): return diagnostic()
            monkeypatch.setattr(services_bots, "verify_telegram_token_diagnostic", verify)
            response = await client.post(f"/api/v1/workspaces/{ws}/bots", headers=owner,
                                         json={"display_name": "Test", "bot_token": "987654321:synthetic-secret"})
            assert response.status == 201
            assert "synthetic-secret" not in await response.text()
            row = await db_models.bots_col().find_one({"telegram_bot_id": 987654321})
            assert row["token_sealed"].startswith("enc:")
            assert security.decrypt_token(row["token_sealed"]) == "987654321:synthetic-secret"
    run(go())


def test_rotation_saves_compatible_encrypted_token(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 79003)
            async def verify(_): return diagnostic()
            monkeypatch.setattr(services_bots, "verify_telegram_token_diagnostic", verify)
            path = f"/api/v1/workspaces/{ws}/bots"
            created = await client.post(path, headers=owner, json={"display_name": "Test", "bot_token": "987654321:old-secret"})
            assert created.status == 201
            col = db_models.bots_col()
            before = await col.find_one({"telegram_bot_id": 987654321})
            response = await client.patch(f"{path}/{before['bot_id']}", headers=owner,
                                          json={"rotate_token": "987654321:new-secret"})
            assert response.status == 200
            assert "new-secret" not in await response.text()
            after = await col.find_one({"telegram_bot_id": 987654321})
            assert after["token_sealed"].startswith("enc:")
            assert security.decrypt_token(after["token_sealed"]) == "987654321:new-secret"
    run(go())

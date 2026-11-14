"""Automatic first-visit provisioning; mock Mongo and synthetic Telegram only."""
import json
import pytest
from aiohttp.test_utils import TestClient, TestServer
from tests.harness import load_all, no_botapi, reset_db, run
load_all()
import config
import keep_alive
from webapp import auth as saas_auth, db_models, services_bots

@pytest.fixture(autouse=True)
def reset_state():
    run(reset_db()); no_botapi(); db_models.reset_memory_collections(); saas_auth.reset_auth_state()
    yield

async def _login_link(cli, uid, name="User"):
    link = saas_auth.issue_saas_link(uid, first_name=name)
    resp = await cli.post("/api/v1/auth/login", json={"link": link})
    assert resp.status == 200
    body = await resp.json()
    return {"Authorization": f"Bearer {body['session_token']}", "X-CSRF-Token": body['csrf_token']}, body['workspaces'][0]['workspace_id']

def test_non_admin_collaborator_gets_personal_workspace_on_verified_login():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            _, team_ws = await _login_link(cli, 32001, "Team Owner")
            await services_bots.invite_team_member(
                team_ws, 32001, "owner", target_user_id=32002, role="viewer"
            )
            link = saas_auth.issue_saas_link(32002, first_name="New Member")
            resp = await cli.post("/api/v1/auth/login", json={"link": link})
            assert resp.status == 200
            result = await resp.json()
            assert result["user"]["is_platform_admin"] is False
            workspaces = {w["workspace_id"]: w for w in result["workspaces"]}
            assert workspaces[team_ws]["my_role"] == "viewer"
            personal = [w for w in workspaces.values() if w["owner_id"] == 32002]
            assert len(personal) == 1 and personal[0]["my_role"] == "owner"
            assert "32002" not in personal[0]["workspace_id"]
            headers = {"Authorization": f"Bearer {result['session_token']}"}
            assert (await cli.get("/api/v1/admin/overview", headers=headers)).status == 403
            assert (await cli.get(f"/api/v1/workspaces/{team_ws}", headers=headers)).status == 200
            assert (await cli.get(f"/api/v1/workspaces/{personal[0]['workspace_id']}", headers=headers)).status == 200
            assert (await cli.post("/api/v1/auth/login", json={"user_id": 32002})).status == 401

    run(go())


def test_signed_miniapp_login_creates_non_admin_personal_workspace():
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode

    async def go():
        user = {"id": 32003, "first_name": "Telegram User"}
        fields = {"auth_date": str(int(time.time())), "user": json.dumps(user, separators=(",", ":"))}
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            response = await cli.post("/api/v1/auth/login", json={"init_data": urlencode(fields)})
            assert response.status == 200
            result = await response.json()
            assert result["user"]["is_platform_admin"] is False
            assert result["workspaces"][0]["owner_id"] == 32003
            assert result["workspaces"][0]["my_role"] == "owner"
            fields["user"] = json.dumps({"id": 32004, "first_name": "Imposter"})
            invalid = await cli.post("/api/v1/auth/login", json={"init_data": urlencode(fields)})
            assert invalid.status == 401
            assert await db_models.workspaces_col().count_documents({"owner_id": 32004}) == 0

    run(go())


def test_concurrent_personal_workspace_provisioning_is_idempotent(monkeypatch):
    import asyncio

    async def go():
        await db_models.ensure_saas_indexes()
        collection = db_models.memberships_col()
        both_checked = asyncio.Event()

        class RacingCursor:
            def __init__(self, cursor):
                self.cursor = cursor

            async def to_list(self, *args, **kwargs):
                result = await self.cursor.to_list(*args, **kwargs)
                racing.reads += 1
                if racing.reads == 2:
                    both_checked.set()
                await asyncio.wait_for(both_checked.wait(), timeout=3)
                return result

        class RacingMemberships:
            def __init__(self):
                self.reads = 0

            def __getattr__(self, name):
                return getattr(collection, name)

            def find(self, query):
                cursor = collection.find(query)
                if query == {"user_id": 32005, "role": "owner"}:
                    return RacingCursor(cursor)
                return cursor

        racing = RacingMemberships()
        monkeypatch.setattr(db_models, "memberships_col", lambda: racing)
        one, two = await asyncio.gather(
            db_models.ensure_default_workspace(32005, display_name="One"),
            db_models.ensure_default_workspace(32005, display_name="Two"),
        )
        assert racing.reads == 2
        assert one["workspace_id"] == two["workspace_id"]
        assert await db_models.workspaces_col().count_documents({"owner_id": 32005}) == 1
        assert await collection.count_documents({"user_id": 32005, "role": "owner"}) == 1

    run(go())


def test_personal_workspace_recovers_from_failed_membership_insert(monkeypatch):
    async def go():
        original = db_models.memberships_col()

        class FailingMemberships:
            def __getattr__(self, name):
                return getattr(original, name)

            async def insert_one(self, document):
                raise RuntimeError("simulated write outage")

        monkeypatch.setattr(db_models, "memberships_col", lambda: FailingMemberships())
        with pytest.raises(RuntimeError, match="simulated write outage"):
            await db_models.ensure_default_workspace(32006, display_name="Original")
        assert await db_models.workspaces_col().count_documents({"owner_id": 32006}) == 1
        monkeypatch.setattr(db_models, "memberships_col", lambda: original)
        recovered = await db_models.ensure_default_workspace(32006, display_name="Changed")
        assert recovered["name"] == "Original Workspace"
        assert await db_models.workspaces_col().count_documents({"owner_id": 32006}) == 1
        assert (await original.find_one({"user_id": 32006}))["role"] == "owner"

    run(go())


def test_personal_workspace_identity_and_role_conflicts_fail_closed():
    import hashlib

    async def go():
        for uid, conflict in ((32007, "owner"), (32008, "role")):
            digest = hashlib.sha256(f"videl-personal-workspace:{uid}".encode()).hexdigest()[:32]
            ws_id = f"ws_personal_{digest}"
            await db_models.workspaces_col().insert_one({
                "workspace_id": ws_id, "owner_id": 999 if conflict == "owner" else uid,
                "is_demo": False, "deleted": False, "name": "Existing",
            })
            if conflict == "role":
                await db_models.memberships_col().insert_one({
                    "workspace_id": ws_id, "user_id": uid, "role": "viewer", "permissions": {},
                })
            with pytest.raises(RuntimeError, match="conflict"):
                await db_models.ensure_default_workspace(uid)
            ws = await db_models.workspaces_col().find_one({"workspace_id": ws_id})
            assert ws["name"] == "Existing"
            if conflict == "role":
                assert (await db_models.memberships_col().find_one({"user_id": uid}))["role"] == "viewer"
            else:
                assert ws["owner_id"] == 999
                assert await db_models.memberships_col().count_documents({"user_id": uid}) == 0

    run(go())


def test_personal_workspace_reuses_existing_owned_workspace_without_modifying_it():
    async def go():
        ws = await services_bots.create_workspace(32009, name="Existing Owner Workspace")
        before = await db_models.workspaces_col().find_one({"workspace_id": ws["workspace_id"]})
        result = await db_models.ensure_default_workspace(32009, display_name="New Name")
        assert result["workspace_id"] == ws["workspace_id"]
        assert await db_models.workspaces_col().find_one({"workspace_id": ws["workspace_id"]}) == before
        assert await db_models.workspaces_col().count_documents({"owner_id": 32009}) == 1

    run(go())


def test_personal_workspace_fails_closed_when_membership_index_unavailable(monkeypatch):
    async def go():
        original = db_models.memberships_col()

        class BrokenMembershipIndex:
            def __getattr__(self, name):
                return getattr(original, name)

            async def create_index(self, *args, **kwargs):
                raise RuntimeError("simulated index failure")

        monkeypatch.setattr(db_models, "memberships_col", lambda: BrokenMembershipIndex())
        with pytest.raises(RuntimeError, match="simulated index failure"):
            await db_models.ensure_default_workspace(32010)
        assert await db_models.workspaces_col().count_documents({"owner_id": 32010}) == 0
        assert await original.count_documents({"user_id": 32010}) == 0

    run(go())




def test_parallel_distinct_telegram_launches_create_one_account_workspace():
    import asyncio, hashlib, hmac, time
    from urllib.parse import urlencode
    def signed(query_id):
        fields = {'auth_date':str(int(time.time())), 'query_id':query_id,
                  'user':json.dumps({'id':32011,'first_name':'New User'},separators=(',',':'))}
        check = '\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
        secret = hmac.new(b'WebAppData', config.BOT_TOKEN.encode(), hashlib.sha256).digest()
        fields['hash'] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        return urlencode(fields)
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            a,b = await asyncio.gather(*(cli.post('/api/v1/auth/login',json={'init_data':signed(q)}) for q in ('one','two')))
            assert a.status == b.status == 200
            first,second = await a.json(), await b.json()
            assert first['user']['user_id'] == second['user']['user_id'] == 32011
            assert first['workspaces'][0]['workspace_id'] == second['workspaces'][0]['workspace_id']
            assert await db_models.workspaces_col().count_documents({'owner_id':32011}) == 1
            assert await db_models.memberships_col().count_documents({'user_id':32011,'role':'owner'}) == 1
    run(go())


def test_failed_first_signup_never_delivers_or_leaves_session(monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError('secret database error')
    monkeypatch.setattr(db_models, 'ensure_default_workspace', fail)
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            link = saas_auth.issue_saas_link(32012, first_name='Test')
            response = await cli.post('/api/v1/auth/login', json={'link':link})
            assert response.status == 500
            assert 'secret database error' not in await response.text()
            assert not saas_auth._sessions
    run(go())


def test_preview_memory_adapter_is_not_a_signup_database(monkeypatch):
    monkeypatch.setattr(db_models, 'workspaces_col', lambda: db_models._MemCollection())
    with pytest.raises(RuntimeError, match='MongoDB is required'):
        run(db_models.ensure_default_workspace(32013))


def test_failed_login_audit_does_not_leave_an_undelivered_session(monkeypatch):
    from platform_core import models_db
    async def fail(*args, **kwargs):
        raise RuntimeError('sensitive audit failure')
    monkeypatch.setattr(models_db, 'log_audit', fail)
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            link = saas_auth.issue_saas_link(32014, first_name='Test')
            response = await cli.post('/api/v1/auth/login', json={'link':link})
            assert response.status == 500
            assert 'sensitive audit failure' not in await response.text()
            assert not saas_auth._sessions
    run(go())

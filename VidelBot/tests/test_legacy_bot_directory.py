"""Synthetic account isolation and secret-projection tests, not live Mongo/Telegram."""
import pytest
from aiohttp.test_utils import TestClient, TestServer
from tests.harness import load_all, no_botapi, reset_db, run
load_all()
import config
import keep_alive
from filestore.database.main_db import MainDB
from webapp import auth, db_models
from tests.test_command_create_contract import login

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    run(reset_db()); no_botapi(); db_models.reset_memory_collections(); auth.reset_auth_state()
    monkeypatch.setattr(config, 'ADMIN_WEB_TOKEN', 'synthetic-admin-token')
    yield


def test_only_telegram_owner_can_read_own_registration_without_secrets(monkeypatch):
    async def go():
        from mongomock_motor import AsyncMongoMockClient
        col = AsyncMongoMockClient().db.registered_bots
        monkeypatch.setattr(MainDB, '__init__', lambda self: setattr(self, 'bots', col))
        await col.insert_many([
            {'_id': 100, 'owner_id': 111, 'bot_username':'alpha_bot', 'is_active':True, 'bot_token_encrypted':'secret-token', 'settings':{'secret':'another-secret'}},
            {'_id': 101, 'owner_id': 111, 'bot_username':'bad:secret-token', 'is_active':False},
            {'_id': 102, 'owner_id': 111, 'bot_username':'deleted_bot','is_deleted':True},
            {'_id': 103, 'owner_id': 222, 'bot_username':'other_bot'},
        ])
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, _ = await login(cli, 111); other, _ = await login(cli, 222)
            path = '/api/v1/account/legacy-bots'
            assert (await cli.get(path)).status == 401
            r = await cli.get(path, headers=owner); assert r.status == 200
            assert r.headers['Cache-Control'] == 'no-store'
            assert await r.json() == {'bots':[{'telegram_bot_id':'100','username':'alpha_bot','enabled':True},{'telegram_bot_id':'101','username':'','enabled':False}], 'truncated':False}
            assert 'secret' not in await r.text()
            assert [b['username'] for b in (await (await cli.get(path,headers=other)).json())['bots']] == ['other_bot']
            admin = await (await cli.post('/api/v1/auth/login',json={'admin_token':'synthetic-admin-token'})).json()
            assert (await cli.get(path,headers={'Authorization':'Bearer '+admin['session_token']})).status == 403
            demo_response = await cli.post('/api/v1/auth/demo',json={})
            if demo_response.status == 200:
                demo = await demo_response.json()
                assert (await cli.get(path,headers={'Authorization':'Bearer '+demo['session_token']})).status == 403
            else:
                assert demo_response.status == 503  # hardened worktrees may fail demo closed
            unverified = auth.create_session(111, display_name='No identity proof')
            assert (await cli.get(path,headers={'Authorization':'Bearer '+unverified['session_token']})).status == 403
            assert (await cli.get(path,headers={'X-Admin-Token':'synthetic-admin-token'})).status == 403
        assert await col.count_documents({}) == 4
    run(go())


def test_bounded_read_and_database_error_not_silenced(monkeypatch):
    async def go():
        from mongomock_motor import AsyncMongoMockClient
        col = AsyncMongoMockClient().db.registered_bots
        monkeypatch.setattr(MainDB, '__init__', lambda self: setattr(self, 'bots', col))
        await col.insert_many([{'_id':i,'owner_id':111,'bot_username':'test_bot'} for i in range(1,103)])
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, _ = await login(cli,111)
            r = await cli.get('/api/v1/account/legacy-bots',headers=owner)
            data=await r.json(); assert len(data['bots']) == 100 and data['truncated'] is True
            def failed(self): raise RuntimeError('secret database credentials')
            monkeypatch.setattr(MainDB, '__init__', failed)
            r = await cli.get('/api/v1/account/legacy-bots',headers=owner)
            assert r.status == 500 and 'secret' not in await r.text()
    run(go())


def test_missing_encryption_is_reported_before_contacting_telegram(monkeypatch):
    async def go():
        from filestore.utils import security
        from webapp import services_bots
        monkeypatch.setattr(security, 'is_encryption_enabled', lambda: False)
        async def forbidden_contact(_token):
            raise AssertionError('Should not contact Telegram when encryption is unavailable')
        monkeypatch.setattr(services_bots, 'verify_telegram_token_diagnostic', forbidden_contact)
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await login(cli,111)
            path = f'/api/v1/workspaces/{ws}/bots'
            r = await cli.post(path,headers=owner,json={'display_name':'My bot','bot_token':'123:secret'})
            assert r.status == 400 and (await r.json())['error'] == 'A valid ENCRYPTION_KEY is required before storing bot credentials.'
            r = await cli.post(path,headers=owner,json={'display_name':'My draft','template':'custom'})
            assert r.status == 201
    run(go())

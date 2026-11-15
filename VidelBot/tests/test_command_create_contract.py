"""Create-only command API checks with mock Mongo; not real multi-worker evidence."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer
from pymongo.errors import OperationFailure
from tests.harness import load_all, no_botapi, reset_db, run

load_all()
import keep_alive
from webapp import auth, db_models


@pytest.fixture(autouse=True)
def clean():
    run(reset_db())
    no_botapi()
    db_models.reset_memory_collections()
    auth.reset_auth_state()
    yield


async def login(client, uid):
    key = auth.issue_saas_link(uid, username=f'user{uid}', first_name='Fixture')
    response = await client.post('/api/v1/auth/login', json={'link': key})
    data = await response.json()
    return {'Authorization':f"Bearer {data['session_token']}", 'X-CSRF-Token':data['csrf_token']}, data['workspaces'][0]['workspace_id']


def payload(name='welcome'):
    return {'command':name, 'description':'Welcome record', 'category':'Custom', 'permission':'everyone', 'visibility':'public', 'enabled':True}


def test_create_only_permission_conflicts_validation_and_concurrency():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 67001)
            outsider, other_ws = await login(client, 67002)
            viewer, _ = await login(client, 67003)
            created = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=owner, json={'display_name':'Record','template':'custom'})
            assert created.status == 201
            bot = (await created.json())['bot']['bot_id']
            root = f'/api/v1/workspaces/{ws}/bots/{bot}/commands'
            listing = await (await client.get(root, headers=owner)).json()
            assert listing['capabilities']['command_create_only'] is True
            uri = root + '/create'
            assert (await client.post(uri, headers={'Authorization':owner['Authorization']}, json=payload())).status == 403
            assert (await client.post(uri, headers=outsider, json=payload())).status == 403
            assert (await client.post(f'/api/v1/workspaces/{other_ws}/bots/{bot}/commands/create', headers=outsider, json=payload())).status == 404
            assert (await client.post(f'/api/v1/workspaces/{ws}/team',headers=owner,json={'user_id':67003,'role':'viewer'})).status == 201
            assert (await client.post(uri,headers=viewer,json=payload())).status == 403
            invalid = [
                {**payload(),'command':'UPPER'}, {**payload(),'command':'x'*33},
                {**payload(),'description':'x'}, {**payload(),'enabled':'false'},
                {**payload(),'category':'bad'}, {**payload(),'permission':'bad'},
                {**payload(),'visibility':'scoped'}, {**payload(),'custom_reply_text':'x'},
                {**payload(),'handler_bound':True}, {**payload(),'create_only':True},
                {**payload(),'command':{'$ne':'welcome'}},
            ]
            for body in invalid:
                assert (await client.post(uri,headers=owner,json=body)).status == 400
            # The seeded built-in cannot be overwritten by a create request.
            before = await db_models.bot_commands_col().find_one({'bot_id':bot,'command':'start'})
            assert (await client.post(uri,headers=owner,json=payload('start'))).status == 409
            assert await db_models.bot_commands_col().find_one({'bot_id':bot,'command':'start'}) == before
            one, two = await asyncio.gather(client.post(uri,headers=owner,json=payload()),client.post(uri,headers=owner,json={**payload(),'description':'Other'}))
            assert sorted([one.status,two.status]) == [201,409]
            stored = await db_models.bot_commands_col().find_one({'bot_id':bot,'command':'welcome'})
            assert stored['description'] in ('Welcome record','Other') and stored['handler_bound'] is False
            assert await db_models.bot_commands_col().count_documents({'bot_id':bot,'command':'welcome'}) == 1
            # Normalized casing is a UI concern, never a hidden overwrite in the API.
            assert (await client.post(uri,headers=owner,json=payload('welcome'))).status == 409
            assert await db_models.bot_commands_col().find_one({'bot_id':bot,'command':'welcome'}) == stored
            indexes = await db_models.bot_commands_col().index_information()
            assert indexes['bot_id_1_command_1']['unique'] is True
    run(go())


def test_index_failure_returns_error_without_writing(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 67010)
            created = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=owner, json={'display_name':'Record','template':'custom'})
            bot = (await created.json())['bot']['bot_id']
            collection = db_models.bot_commands_col()
            async def failed_index(*args, **kwargs):
                raise OperationFailure('private database diagnostic')
            monkeypatch.setattr(collection, 'create_index', failed_index)
            monkeypatch.setattr(db_models, 'bot_commands_col', lambda: collection)
            uri = f'/api/v1/workspaces/{ws}/bots/{bot}/commands/create'
            response = await client.post(uri,headers=owner,json=payload())
            assert response.status == 500
            assert 'private database diagnostic' not in await response.text()
            assert await collection.find_one({'bot_id':bot,'command':'welcome'}) is None
    run(go())


def test_legacy_duplicate_commands_block_index_and_leave_data_untouched():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 67020)
            created = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=owner, json={'display_name':'Record','template':'custom'})
            bot = (await created.json())['bot']['bot_id']
            col = db_models.bot_commands_col()
            indexes = await col.index_information()
            if 'bot_id_1_command_1' in indexes:
                await col.drop_index('bot_id_1_command_1')
            await col.insert_one({'bot_id':bot,'command':'legacy_duplicate','description':'one'})
            await col.insert_one({'bot_id':bot,'command':'legacy_duplicate','description':'two'})
            before = await col.count_documents({'bot_id':bot})
            response = await client.post(f'/api/v1/workspaces/{ws}/bots/{bot}/commands/create',headers=owner,json=payload())
            assert response.status == 500
            assert await col.count_documents({'bot_id':bot}) == before
            assert await col.find_one({'bot_id':bot,'command':'welcome'}) is None
            assert await col.count_documents({'bot_id':bot,'command':'legacy_duplicate'}) == 2
    run(go())


def test_legacy_duplicate_bot_ids_block_creation_without_deleting_records():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 67030)
            created = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=owner, json={'display_name':'Record','template':'custom'})
            bot = (await created.json())['bot']['bot_id']
            bots = db_models.bots_col()
            if 'bot_id_1' in await bots.index_information():
                await bots.drop_index('bot_id_1')
            await bots.insert_one({'bot_id':bot,'workspace_id':'other_workspace','owner_id':999,'deleted':False})
            response = await client.post(f'/api/v1/workspaces/{ws}/bots/{bot}/commands/create',headers=owner,json=payload())
            assert response.status == 500
            assert await bots.count_documents({'bot_id':bot}) == 2
            assert await db_models.bot_commands_col().find_one({'bot_id':bot,'command':'welcome'}) is None
    run(go())


def test_audit_failure_after_insert_returns_uncertain_error_and_retry_conflicts(monkeypatch):
    from platform_core import models_db
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 67040)
            created = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=owner, json={'display_name':'Record','template':'custom'})
            bot = (await created.json())['bot']['bot_id']
            async def fail_audit(*args, **kwargs):
                raise RuntimeError('audit unavailable')
            monkeypatch.setattr(models_db, 'log_audit', fail_audit)
            uri = f'/api/v1/workspaces/{ws}/bots/{bot}/commands/create'
            response = await client.post(uri,headers=owner,json=payload())
            assert response.status == 500
            record = await db_models.bot_commands_col().find_one({'bot_id':bot,'command':'welcome'})
            assert record and record['description']=='Welcome record'
            assert (await client.post(uri,headers=owner,json={**payload(),'description':'Overwrite'})).status == 409
            assert await db_models.bot_commands_col().find_one({'bot_id':bot,'command':'welcome'}) == record
    run(go())

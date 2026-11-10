"""Contract checks for the Next bot-record editor; not real Mongo or Telegram."""
import pytest
from aiohttp.test_utils import TestClient, TestServer

from tests.harness import load_all, no_botapi, reset_db, run

load_all()

import keep_alive
from webapp import auth, db_models


@pytest.fixture(autouse=True)
def clean_state():
    run(reset_db())
    no_botapi()
    db_models.reset_memory_collections()
    auth.reset_auth_state()
    yield


async def login(client, user_id):
    link = auth.issue_saas_link(user_id, username=f'user{user_id}', first_name='Fixture')
    response = await client.post('/api/v1/auth/login', json={'link': link})
    assert response.status == 200
    body = await response.json()
    return {'Authorization': f"Bearer {body['session_token']}", 'X-CSRF-Token': body['csrf_token']}, body['workspaces'][0]['workspace_id']


def test_bot_record_profile_api_scopes_and_patch_contract():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 65001)
            outsider, other_ws = await login(client, 65002)
            viewer, _ = await login(client, 65003)
            created = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=owner,
                                        json={'display_name': 'Original', 'description': 'Description', 'template': 'custom'})
            assert created.status == 201
            bot = (await created.json())['bot']
            uri = f"/api/v1/workspaces/{ws}/bots/{bot['bot_id']}"
            get = await client.get(uri, headers=owner)
            assert get.status == 200 and (await get.json())['bot']['display_name'] == 'Original'
            updated = await client.patch(uri, headers=owner, json={'display_name': 'Renamed'})
            assert updated.status == 200
            record = (await updated.json())['bot']
            assert record['display_name'] == 'Renamed' and record['description'] == 'Description'
            assert record['archived'] is False and 'token_sealed' not in record
            assert (await client.patch(uri, headers={'Authorization': owner['Authorization']}, json={'description': 'no csrf'})).status == 403
            assert (await client.patch(uri, headers=outsider, json={'display_name': 'No'})).status == 403
            assert (await client.get(f"/api/v1/workspaces/{other_ws}/bots/{bot['bot_id']}", headers=outsider)).status == 404
            granted = await client.post(f'/api/v1/workspaces/{ws}/team', headers=owner,
                                        json={'user_id': 65003, 'role': 'viewer'})
            assert granted.status == 201
            assert (await client.get(uri, headers=viewer)).status == 200
            assert (await client.patch(uri, headers=viewer, json={'description': 'No'})).status == 403
            final = await client.get(uri, headers=owner)
            assert (await final.json())['bot']['description'] == 'Description'
    run(go())


def test_conditional_profile_scope_validation_and_conflicts():
    import asyncio
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 65101)
            outsider, other_ws = await login(client, 65102)
            viewer, _ = await login(client, 65103)
            editor, _ = await login(client, 65104)
            created = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=owner,
                                        json={'display_name':'Original', 'description':'Description', 'template':'custom'})
            bot_id = (await created.json())['bot']['bot_id']
            root = f'/api/v1/workspaces/{ws}/bots/{bot_id}'
            read = await (await client.get(root, headers=owner)).json()
            assert read['capabilities']['conditional_profile_update'] is True
            uri = root + '/profile'
            body = {'display_name':'First', 'expected':{'display_name':'Original'}}
            assert (await client.patch(uri, headers={'Authorization': owner['Authorization']}, json=body)).status == 403
            assert (await client.patch(uri, headers=outsider, json=body)).status == 403
            assert (await client.patch(f'/api/v1/workspaces/{other_ws}/bots/{bot_id}/profile', headers=outsider, json=body)).status == 409
            granted = await client.post(f'/api/v1/workspaces/{ws}/team', headers=owner, json={'user_id':65103, 'role':'viewer'})
            assert granted.status == 201 and (await client.patch(uri, headers=viewer, json=body)).status == 403
            invalid = [
                {'display_name':'Bad'},
                {'display_name':'Bad', 'expected':{}},
                {'display_name':'Bad', 'expected':{'display_name':{'$ne':'Original'}}},
                {'display_name':'Bad', 'archived':True, 'expected':{'display_name':'Original','archived':False}},
                {'display_name':'Bad', 'expected':{'display_name':'Original','description':'Description'}},
                {'display_name':'x', 'expected':{'display_name':'Original'}},
            ]
            for payload in invalid:
                assert (await client.patch(uri, headers=owner, json=payload)).status == 400
            # Concurrent same-field edits cannot both succeed against the same original.
            one, two = await asyncio.gather(
                client.patch(uri, headers=owner, json={'display_name':'First','expected':{'display_name':'Original'}}),
                client.patch(uri, headers=owner, json={'display_name':'Second','expected':{'display_name':'Original'}}),
            )
            assert sorted([one.status, two.status]) == [200, 409]
            winner = (await (await client.get(root, headers=owner)).json())['bot']['display_name']
            # Independent field changes can coexist; a bot-settings editor without logs must not receive errors.
            granted = await client.post(f'/api/v1/workspaces/{ws}/team', headers=owner,
                                        json={'user_id':65104, 'role':'manager', 'permissions':{'logs':False}})
            assert granted.status == 201
            await db_models.bots_col().update_one({'bot_id':bot_id}, {'$set':{'last_error':'private diagnostic'}})
            ok = await client.patch(uri, headers=editor, json={'description':'New description','expected':{'description':'Description'}})
            result = await ok.json()
            assert ok.status == 200 and result['bot']['display_name'] == winner and 'last_error' not in result['bot']
            old = await client.patch(root, headers=owner, json={'display_name':'Legacy'})
            assert old.status == 200
            stale = await client.patch(uri, headers=owner, json={'display_name':'Stale','expected':{'display_name':winner}})
            assert stale.status == 409
            # A leading dollar sign is data, not a Mongo expression.
            assert (await client.patch(root, headers=owner, json={'display_name':'$display_name'})).status == 200
            literal = await client.patch(uri, headers=owner, json={'display_name':'Literal','expected':{'display_name':'$display_name'}})
            assert literal.status == 200
            # Missing or null optional description compares as empty, malformed arrays do not.
            coll = db_models.bots_col()
            await coll.update_one({'bot_id':bot_id}, {'$unset':{'description':''}})
            assert (await client.patch(uri, headers=owner, json={'description':'Added','expected':{'description':''}})).status == 200
            await coll.update_one({'bot_id':bot_id}, {'$set':{'description':['Added']}})
            assert (await client.patch(uri, headers=owner, json={'description':'Bad','expected':{'description':'Added'}})).status == 409
            await coll.update_one({'bot_id':bot_id}, {'$set':{'deleted':True}})
            assert (await client.patch(uri, headers=owner, json={'display_name':'No','expected':{'display_name':'Literal'}})).status == 409
    run(go())

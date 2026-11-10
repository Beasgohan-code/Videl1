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

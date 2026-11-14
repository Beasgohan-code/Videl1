"""Synthetic permission, tenant and secret boundaries for saved feature summaries."""
import pytest
from aiohttp.test_utils import TestClient, TestServer
from tests.harness import load_all, no_botapi, reset_db, run
load_all()
import keep_alive
from webapp import auth, db_models
from webapp.bot_feature_summary import FEATURE_KEYS
from tests.test_command_create_contract import login


@pytest.fixture(autouse=True)
def clean():
    run(reset_db()); no_botapi(); db_models.reset_memory_collections(); auth.reset_auth_state()
    yield


def test_owner_tenant_scope_no_secrets_and_no_mutation():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await login(cli, 75801)
            outsider, other_ws = await login(cli, 75802)
            viewer, _ = await login(cli, 75803)
            created = await cli.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json={'display_name':'Fixture bot','template':'custom'})
            assert created.status == 201
            bot=(await created.json())['bot']['bot_id']; uri=f'/api/v1/workspaces/{ws}/bots/{bot}/feature-summary'
            col=db_models.bots_col()
            await col.update_one({'bot_id':bot},{'$set':{'features':{'filestore_enabled':True,'batch_links':False,'media_encoder':'SECRET_BOOL','unknown_secret':'HIDDEN_FLAG'},'settings':{'password':'HIDDEN_SETTING'},'bot_token_encrypted':'HIDDEN_TOKEN'}})
            before=await col.find_one({'bot_id':bot})
            assert (await cli.get(uri)).status == 401
            assert (await cli.get(uri,headers=outsider)).status == 403
            assert (await cli.get(f'/api/v1/workspaces/{other_ws}/bots/{bot}/feature-summary',headers=outsider)).status == 404
            assert (await cli.post(f'/api/v1/workspaces/{ws}/team',headers=owner,json={'user_id':75803,'role':'viewer'})).status == 201
            assert (await cli.get(uri,headers=viewer)).status == 403
            response=await cli.get(uri,headers={'Authorization':owner['Authorization']})
            assert response.status == 200 and response.headers['Cache-Control']=='no-store'
            text=await response.text(); assert all(secret not in text for secret in ['SECRET_BOOL','HIDDEN_FLAG','HIDDEN_SETTING','HIDDEN_TOKEN','password','settings'])
            data=await response.json(); assert set(data)=={'summary','bot_id','features'} and data['summary']=='features-v1' and data['bot_id']==bot
            assert set(data['features'])==set(FEATURE_KEYS)
            assert data['features']['filestore_enabled'] is True and data['features']['batch_links'] is False
            assert data['features']['media_encoder'] is None and data['features']['auto_delete'] is None
            assert await col.find_one({'bot_id':bot})==before
            assert (await cli.post(uri,headers=owner,json={})).status==405
            await col.update_one({'bot_id':bot},{'$set':{'deleted':True}})
            assert (await cli.get(uri,headers=owner)).status==404
    run(go())


def test_api_key_scopes_and_malformed_features():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws = await login(cli, 75811)
            created=await cli.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json={'display_name':'Fixture bot','template':'custom'})
            bot=(await created.json())['bot']['bot_id']; uri=f'/api/v1/workspaces/{ws}/bots/{bot}/feature-summary'
            keys=f'/api/v1/workspaces/{ws}/api-keys'
            async def key(scope):
                result=await cli.post(keys,headers=owner,json={'name':'Fixture key','scopes':[scope]})
                assert result.status==201
                return {'Authorization':'Bearer '+(await result.json())['raw_key_once']}
            assert (await cli.get(uri,headers=await key('analytics'))).status==403
            allowed=await key('bot_settings')
            col=db_models.bots_col(); await col.update_one({'bot_id':bot},{'$set':{'features':'PRIVATE_BAD_VALUE'}})
            response=await cli.get(uri,headers=allowed); assert response.status==200
            text=await response.text(); assert 'PRIVATE_BAD_VALUE' not in text
            assert all(value is None for value in (await response.json())['features'].values())
    run(go())


def test_backend_error_is_not_a_fake_empty_summary(monkeypatch):
    from webapp import services_bots
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws=await login(cli,75821)
            async def fail(*args): raise RuntimeError('HIDDEN_DATABASE_ERROR')
            monkeypatch.setattr(services_bots,'get_owned_workspace_bot',fail)
            response=await cli.get(f'/api/v1/workspaces/{ws}/bots/fixture/feature-summary',headers=owner)
            assert response.status==500 and 'HIDDEN_DATABASE_ERROR' not in await response.text()
    run(go())


def test_membership_revocation_blocks_subsequent_reads():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner, ws=await login(cli,75831); manager,_=await login(cli,75832)
            created=await cli.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json={'display_name':'Fixture bot','template':'custom'})
            bot=(await created.json())['bot']['bot_id']; uri=f'/api/v1/workspaces/{ws}/bots/{bot}/feature-summary'
            assert (await cli.post(f'/api/v1/workspaces/{ws}/team',headers=owner,json={'user_id':75832,'role':'manager'})).status==201
            assert (await cli.get(uri,headers=manager)).status==200
            await db_models.memberships_col().delete_one({'workspace_id':ws,'user_id':75832})
            assert (await cli.get(uri,headers=manager)).status==403
    run(go())

"""Create-only bot registration contracts with synthetic sessions and mock Mongo."""
import asyncio
import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.fernet import Fernet
from pymongo.errors import OperationFailure
from tests.harness import load_all, no_botapi, reset_db, run
load_all()
import keep_alive
from platform_core import models_db
from webapp import auth, db_models, services_bots
from tests.test_command_create_contract import login

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    run(reset_db()); no_botapi(); db_models.reset_memory_collections(); auth.reset_auth_state()
    monkeypatch.setenv('ENCRYPTION_KEY', Fernet.generate_key().decode())
    yield


def test_concurrent_cross_workspace_registration_cannot_replace_winner(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 71001)
            other, other_ws = await login(client, 71002)
            collection = db_models.bots_col()
            monkeypatch.setattr(db_models, 'bots_col', lambda: collection)
            orig = collection.find_one; both = asyncio.Event(); count = 0
            async def lookup(query, *args, **kwargs):
                nonlocal count
                result = await orig(query, *args, **kwargs)
                if query == {'telegram_bot_id':987654321}:
                    count += 1
                    if count == 2: both.set()
                    await asyncio.wait_for(both.wait(), 5)
                return result
            monkeypatch.setattr(collection, 'find_one', lookup)
            async def verified(_):
                return {'verified_with_telegram':True,'telegram_bot_id':987654321,'username':'verified_bot','first_name':'', 'token_masked':'****','diagnostic':'Verified by test stub'}
            monkeypatch.setattr(services_bots,'verify_telegram_token_diagnostic', verified)
            body = {'display_name':'A record','template':'custom','bot_token':'987654321:synthetic-secret'}
            first, second = await asyncio.gather(
                client.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json=body),
                client.post(f'/api/v1/workspaces/{other_ws}/bots',headers=other,json={**body,'display_name':'Other record'}))
            assert sorted([first.status,second.status]) == [201,400]
            failed=first if first.status == 400 else second
            assert (await failed.json())['error'] == 'This bot is already registered in a workspace.'
            rows = await collection.find({'bot_id':'bot_987654321'}).to_list(length=10)
            assert len(rows)==1
            row=rows[0]
            assert (row['workspace_id'],row['display_name']) in [(ws,'A record'),(other_ws,'Other record')]
            assert row['token_sealed'].startswith('enc:') and 'synthetic-secret' not in row['token_sealed']
            assert 'synthetic-secret' not in await failed.text()
            assert (await collection.index_information())['bot_id_1']['unique'] is True
    run(go())


def test_draft_collision_and_deleted_record_are_never_replaced(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client,71010)
            monkeypatch.setattr(services_bots.secrets,'token_hex',lambda _: 'deadbeef00')
            path=f'/api/v1/workspaces/{ws}/bots'
            response=await client.post(path,headers=owner,json={'display_name':'Original','template':'custom'})
            assert response.status==201
            col=db_models.bots_col(); original=await col.find_one({'bot_id':'bot_deadbeef00'})
            failed=await client.post(path,headers=owner,json={'display_name':'Replacement','template':'custom'})
            assert failed.status==400 and await col.find_one({'bot_id':'bot_deadbeef00'})==original
            await col.update_one({'bot_id':'bot_deadbeef00'},{'$set':{'deleted':True,'token_sealed':'preserved'}})
            before=await col.find_one({'bot_id':'bot_deadbeef00'})
            failed=await client.post(path,headers=owner,json={'display_name':'Recreated','template':'custom'})
            assert failed.status==400 and await col.find_one({'bot_id':'bot_deadbeef00'})==before
            assert (await client.post(path,headers={'Authorization':owner['Authorization']},json={'display_name':'Blocked'})).status==403
    run(go())


def test_missing_index_or_legacy_duplicates_fail_closed_without_secrets(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws=await login(client,71020)
            col=db_models.bots_col();monkeypatch.setattr(db_models,'bots_col',lambda: col)
            async def fail(*args, **kwargs): raise OperationFailure('private database diagnostic')
            monkeypatch.setattr(col,'create_index',fail)
            response=await client.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json={'display_name':'No write'})
            assert response.status==503 and 'private database diagnostic' not in await response.text()
            assert 'check database indexes' in (await response.json())['error']
            assert await col.count_documents({'workspace_id':ws})==0
    run(go())


def test_audit_failure_after_insert_is_uncertain_and_retry_does_not_replace(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws=await login(client,71030)
            monkeypatch.setattr(services_bots.secrets,'token_hex',lambda _: 'aabbccdde0')
            async def fail(*args, **kwargs): raise RuntimeError('private audit failure')
            monkeypatch.setattr(models_db,'log_audit',fail)
            path=f'/api/v1/workspaces/{ws}/bots'
            response=await client.post(path,headers=owner,json={'display_name':'Saved despite error'})
            assert response.status==500 and 'private audit failure' not in await response.text()
            col=db_models.bots_col(); row=await col.find_one({'bot_id':'bot_aabbccdde0'})
            assert row and row['display_name']=='Saved despite error'
            retry=await client.post(path,headers=owner,json={'display_name':'Must not overwrite'})
            assert retry.status==400 and await col.find_one({'bot_id':'bot_aabbccdde0'})==row
    run(go())


def test_duplicate_legacy_ids_and_nonunique_indexes_fail_closed(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws=await login(client,71040)
            col=db_models.bots_col(); monkeypatch.setattr(db_models,'bots_col',lambda: col)
            if 'bot_id_1' in await col.index_information(): await col.drop_index('bot_id_1')
            await col.insert_many([{'bot_id':'legacy','workspace_id':'one'}, {'bot_id':'legacy','workspace_id':'two'}])
            before=await col.find({}).to_list(length=10)
            response=await client.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json={'display_name':'Blocked'})
            assert response.status==503 and await col.find({}).to_list(length=10)==before
            await col.delete_many({'bot_id':'legacy'})  # test fixture only
            async def pretend_index(*args,**kwargs): return 'bot_id_1'
            async def index_info(): return {'bot_id_1':{'key':[('bot_id',1)]}}
            monkeypatch.setattr(col,'create_index',pretend_index)
            monkeypatch.setattr(col,'index_information',index_info)
            response=await client.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json={'display_name':'Still blocked'})
            assert response.status==503 and await col.count_documents({})==0
    run(go())

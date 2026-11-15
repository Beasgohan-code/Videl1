"""Synthetic API contracts for reviewed bot lifecycle writes; not real MongoDB."""
import asyncio
import pytest
from aiohttp.test_utils import TestClient, TestServer
from tests.harness import load_all, no_botapi, reset_db, run
load_all()
import keep_alive
from webapp import auth, db_models
from tests.test_bot_profile_contract import login


@pytest.fixture(autouse=True)
def clean():
    run(reset_db()); no_botapi(); db_models.reset_memory_collections(); auth.reset_auth_state()


async def make(client, headers, ws):
    response = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=headers, json={'display_name':'Record'})
    assert response.status == 201
    return (await response.json())['bot']


def payload(bot, op):
    return {'operation': op, 'expected_updated_ts': bot['updated_ts'], 'expected_archived': bot['archived']}


def test_scope_csrf_validation_and_stale_record():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 88001); outsider, other_ws = await login(client, 88002); viewer, _ = await login(client, 88003)
            bot = await make(client, owner, ws); bid=bot['bot_id']; path=f'/api/v1/workspaces/{ws}/bots/{bid}/lifecycle'
            body=payload(bot,'archive')
            assert (await client.post(path, headers={'Authorization':owner['Authorization']}, json=body)).status == 403
            assert (await client.post(path, headers=outsider, json=body)).status == 403
            assert (await client.post(f'/api/v1/workspaces/{other_ws}/bots/{bid}/lifecycle', headers=outsider, json=body)).status == 409
            assert (await client.post(f'/api/v1/workspaces/{ws}/team',headers=owner,json={'user_id':88003,'role':'viewer'})).status == 201
            assert (await client.post(path,headers=viewer,json=body)).status == 403
            invalid=[{}, {**body,'operation':'oops'}, {**body,'operation':{'$ne':'x'}}, {**body,'expected_updated_ts':True},
                     {**body,'expected_updated_ts':{'$gt':0}}, {**body,'expected_updated_ts':10**300},
                     {**body,'expected_archived':0}, {**body,'unexpected':True}, {**body,'operation':'unarchive'}]
            for bad in invalid: assert (await client.post(path,headers=owner,json=bad)).status == 400
            await db_models.bots_col().update_one({'bot_id':bid},{'$set':{'updated_ts':bot['updated_ts']+1}})
            response=await client.post(path,headers=owner,json=body)
            assert response.status==409 and (await response.json())['code']=='lifecycle_conflict'
            assert (await db_models.bots_col().find_one({'bot_id':bid}))['archived'] is False
    run(go())


def test_concurrent_archive_and_delete_preserves_single_winner():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner,ws=await login(client,88004); bot=await make(client,owner,ws)
            col=db_models.bots_col(); bid=bot['bot_id']; path=f'/api/v1/workspaces/{ws}/bots/{bid}/lifecycle'
            await col.update_one({'bot_id':bid},{'$set':{'token_sealed':'enc:synthetic-secret','token_masked':'masked','has_credentials':True,'connected':True,'other_data':'retained'}})
            one,two=await asyncio.gather(client.post(path,headers=owner,json=payload(bot,'archive')), client.post(path,headers=owner,json=payload(bot,'delete')))
            assert sorted([one.status,two.status])==[200,409]
            text=await one.text()+await two.text(); assert 'synthetic-secret' not in text
            record=await col.find_one({'bot_id':bid}); assert record['other_data']=='retained'
            if record['deleted']:
                assert record['token_sealed']==record['token_masked']=='' and not record['has_credentials'] and not record['connected']
            else:
                assert record['archived'] and record['token_sealed']=='enc:synthetic-secret'
                updated={**bot,'updated_ts':record['updated_ts'],'archived':True}
                result=await client.post(path,headers=owner,json=payload(updated,'unarchive'))
                assert result.status==200 and (await result.json())['bot']['archived'] is False
    run(go())


def test_delete_rechecks_token_after_read_without_leaking_it(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner,ws=await login(client,88005);bot=await make(client,owner,ws);col=db_models.bots_col();bid=bot['bot_id']
            monkeypatch.setattr(db_models,'bots_col',lambda:col)
            await col.update_one({'bot_id':bid},{'$set':{'token_sealed':'enc:old-secret'}})
            orig=col.find_one_and_update
            async def race(query,*args,**kwargs):
                await col.update_one({'bot_id':bid},{'$set':{'token_sealed':'enc:new-secret'}})
                return await orig(query,*args,**kwargs)
            monkeypatch.setattr(col,'find_one_and_update',race)
            path=f'/api/v1/workspaces/{ws}/bots/{bid}/lifecycle'
            response=await client.post(path,headers=owner,json=payload(bot,'delete'))
            assert response.status==409 and 'secret' not in await response.text()
            record=await col.find_one({'bot_id':bid});assert record['token_sealed']=='enc:new-secret' and not record['deleted']
    run(go())


def test_deleted_or_missing_state_is_never_mutated():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner,ws=await login(client,88006);bot=await make(client,owner,ws);col=db_models.bots_col();bid=bot['bot_id']
            path=f'/api/v1/workspaces/{ws}/bots/{bid}/lifecycle';body=payload(bot,'delete')
            await col.update_one({'bot_id':bid},{'$unset':{'updated_ts':''}})
            assert (await client.post(path,headers=owner,json=body)).status==409
            await col.update_one({'bot_id':bid},{'$set':{'updated_ts':bot['updated_ts'],'deleted':True,'token_sealed':'retained'}})
            assert (await client.post(path,headers=owner,json=body)).status==409
            assert (await col.find_one({'bot_id':bid}))['token_sealed']=='retained'
    run(go())


def test_delete_audit_failure_is_uncertain_not_rolled_back(monkeypatch):
    from platform_core import models_db
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner,ws=await login(client,88007);bot=await make(client,owner,ws);col=db_models.bots_col()
            async def fail(*args,**kwargs): raise RuntimeError('private audit details')
            monkeypatch.setattr(models_db,'log_audit',fail)
            path=f"/api/v1/workspaces/{ws}/bots/{bot['bot_id']}/lifecycle"
            response=await client.post(path,headers=owner,json=payload(bot,'delete'))
            assert response.status==500 and 'private audit details' not in await response.text()
            assert (await col.find_one({'bot_id':bot['bot_id']}))['deleted'] is True
            assert (await client.post(path,headers=owner,json=payload(bot,'delete'))).status==409
    run(go())


def test_malformed_record_state_and_revoked_membership_never_write():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner,ws=await login(client,88008);member,_=await login(client,88009);bot=await make(client,owner,ws);col=db_models.bots_col();bid=bot['bot_id']
            grant=await client.post(f'/api/v1/workspaces/{ws}/team',headers=owner,json={'user_id':88009,'role':'manager'})
            assert grant.status==201
            path=f'/api/v1/workspaces/{ws}/bots/{bid}/lifecycle';body=payload(bot,'archive')
            await col.update_one({'bot_id':bid},{'$set':{'archived':[False]}})
            assert (await client.post(path,headers=owner,json=body)).status==409
            await col.update_one({'bot_id':bid},{'$set':{'archived':False,'updated_ts':[bot['updated_ts']]}})
            assert (await client.post(path,headers=owner,json=body)).status==409
            await col.update_one({'bot_id':bid},{'$set':{'updated_ts':bot['updated_ts']}})
            member_col=db_models.memberships_col()
            await member_col.delete_one({'workspace_id':ws,'user_id':88009})
            assert (await client.post(path,headers=member,json=body)).status==403
            assert (await col.find_one({'bot_id':bid}))['archived'] is False
    run(go())

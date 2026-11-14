"""Command CAS contract; mock Mongo and synthetic sessions, not a real cluster."""
import asyncio
import pytest
from aiohttp.test_utils import TestClient, TestServer
from tests.harness import load_all, no_botapi, reset_db, run
load_all()
import keep_alive
from webapp import auth, db_models
from tests.test_command_create_contract import login, payload


@pytest.fixture(autouse=True)
def clean():
    run(reset_db())
    no_botapi()
    db_models.reset_memory_collections()
    auth.reset_auth_state()
    yield


def expected(doc):
    return {k:doc.get(k, False if k in ('enabled','handler_bound') else '') for k in
            ('description','category','permission','visibility','enabled','custom_reply_text','handler_bound')}


def test_conditional_command_scope_validation_conflicts_and_delete():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client, 68001)
            outsider, other_ws = await login(client, 68002)
            viewer, _ = await login(client, 68003)
            created = await client.post(f'/api/v1/workspaces/{ws}/bots', headers=owner, json={'display_name':'Record','template':'custom'})
            bot = (await created.json())['bot']['bot_id']
            root = f'/api/v1/workspaces/{ws}/bots/{bot}/commands'
            listing = await (await client.get(root,headers=owner)).json()
            assert listing['capabilities']['command_conditional_mutations'] is True
            record = (await (await client.post(root+'/create',headers=owner,json=payload())).json())['command']
            uri = root+'/conditional'
            base = {'command':'welcome','expected':expected(record)}
            change = {**base,'changes':{'description':'New'}}
            assert (await client.patch(uri,headers={'Authorization':owner['Authorization']},json=change)).status == 403
            assert (await client.patch(uri,headers=outsider,json=change)).status == 403
            assert (await client.patch(f'/api/v1/workspaces/{other_ws}/bots/{bot}/commands/conditional',headers=outsider,json=change)).status == 404
            assert (await client.post(f'/api/v1/workspaces/{ws}/team',headers=owner,json={'user_id':68003,'role':'viewer'})).status == 201
            assert (await client.patch(uri,headers=viewer,json=change)).status == 403
            invalid = [
                {**base,'changes':{}}, {**base,'changes':{'handler_bound':True}},
                {**base,'changes':{'custom_reply_text':'overwrite'}},
                {**base,'changes':{'permission':'owner'}},
                {**base,'changes':{'permission':'owner','visibility':'public'}},
                {**base,'changes':{'enabled':1}},
                {**base,'changes':{'description':'x'}},
                {**base,'expected':{**base['expected'],'enabled':{'$ne':False}},'changes':{'enabled':False}},
                {**base,'expected':{'description':'Welcome record'},'changes':{'enabled':False}},
                {**base,'changes':{'category':'Invalid'}},
            ]
            for body in invalid: assert (await client.patch(uri,headers=owner,json=body)).status == 400
            # Competing edits to the same snapshot cannot both succeed.
            one,two = await asyncio.gather(
                client.patch(uri,headers=owner,json={**base,'changes':{'description':'One'}}),
                client.patch(uri,headers=owner,json={**base,'changes':{'description':'Two'}}))
            assert sorted([one.status,two.status]) == [200,409]
            col = db_models.bot_commands_col()
            current = await col.find_one({'bot_id':bot,'command':'welcome'})
            assert current['description'] in ('One','Two') and current['custom_reply_text']=='' and current['handler_bound'] is False
            # Changed reply text is preserved and blocks stale deletion, rather than being silently cleared.
            stale = {'command':'welcome','expected':expected(current)}
            await col.update_one({'bot_id':bot,'command':'welcome'},{'$set':{'custom_reply_text':'$enabled'}})
            assert (await client.delete(uri,headers=owner,json=stale)).status == 409
            assert (await col.find_one({'bot_id':bot,'command':'welcome'}))['custom_reply_text']=='$enabled'
            now = await col.find_one({'bot_id':bot,'command':'welcome'})
            body = {'command':'welcome','expected':expected(now),'changes':{'permission':'owner','visibility':'scoped'}}
            result = await client.patch(uri,headers=owner,json=body)
            assert result.status==200 and (await result.json())['command']['permission']=='owner'
            updated = await col.find_one({'bot_id':bot,'command':'welcome'})
            assert updated['custom_reply_text']=='$enabled'
            assert (await client.delete(uri,headers=owner,json={'command':'welcome','expected':expected(updated)})).status==200
            assert await col.find_one({'bot_id':bot,'command':'welcome'}) is None
            assert (await client.delete(uri,headers=owner,json={'command':'welcome','expected':expected(updated)})).status==409
            # Missing optional built-in fields compare as empty/false without touching handler metadata.
            builtin = await col.find_one({'bot_id':bot,'command':'start'})
            assert builtin['handler_bound'] is True and 'custom_reply_text' not in builtin
            result = await client.patch(uri,headers=owner,json={'command':'start','expected':expected(builtin),'changes':{'enabled':False}})
            assert result.status==200
            after = await col.find_one({'bot_id':bot,'command':'start'})
            assert after['handler_bound'] is True and 'custom_reply_text' not in after
    run(go())


def test_conditional_audit_failure_after_write_is_uncertain(monkeypatch):
    from platform_core import models_db
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client,68010)
            bot = (await (await client.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json={'display_name':'Record','template':'custom'})).json())['bot']['bot_id']
            root=f'/api/v1/workspaces/{ws}/bots/{bot}/commands'
            rec=(await (await client.post(root+'/create',headers=owner,json=payload())).json())['command']
            async def fail(*args,**kwargs): raise RuntimeError('private audit detail')
            monkeypatch.setattr(models_db,'log_audit',fail)
            body={'command':'welcome','expected':expected(rec),'changes':{'description':'Saved'}}
            response=await client.patch(root+'/conditional',headers=owner,json=body)
            assert response.status==500 and 'private audit detail' not in await response.text()
            assert (await db_models.bot_commands_col().find_one({'bot_id':bot,'command':'welcome'}))['description']=='Saved'
            assert (await client.patch(root+'/conditional',headers=owner,json=body)).status==409
    run(go())


def test_conditional_operations_request_binary_collation(monkeypatch):
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            owner, ws = await login(client,68020)
            bot=(await (await client.post(f'/api/v1/workspaces/{ws}/bots',headers=owner,json={'display_name':'Record','template':'custom'})).json())['bot']['bot_id']
            root=f'/api/v1/workspaces/{ws}/bots/{bot}/commands'
            rec=(await (await client.post(root+'/create',headers=owner,json=payload())).json())['command']
            col=db_models.bot_commands_col()
            monkeypatch.setattr(db_models,'bot_commands_col',lambda:col)
            original_update,original_delete=col.find_one_and_update,col.find_one_and_delete
            seen=[]
            async def update(*args,**kwargs):
                seen.append(kwargs.get('collation'))
                return await original_update(*args,**kwargs)
            async def delete(*args,**kwargs):
                seen.append(kwargs.get('collation'))
                return await original_delete(*args,**kwargs)
            monkeypatch.setattr(col,'find_one_and_update',update)
            monkeypatch.setattr(col,'find_one_and_delete',delete)
            result=await client.patch(root+'/conditional',headers=owner,json={'command':'welcome','expected':expected(rec),'changes':{'description':'New'}})
            assert result.status==200
            current=(await result.json())['command']
            assert (await client.delete(root+'/conditional',headers=owner,json={'command':'welcome','expected':expected(current)})).status==200
            assert seen==[{'locale':'simple'},{'locale':'simple'}]
    run(go())

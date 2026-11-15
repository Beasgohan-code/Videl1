"""Read-only owner-scoped keyset pagination: synthetic auth and mock Mongo."""
import pytest
from bson.int64 import Int64
from aiohttp.test_utils import TestClient, TestServer
from tests.harness import load_all, no_botapi, reset_db, run
load_all()
import config, keep_alive
from filestore.database.main_db import MainDB
from webapp import auth, db_models
from tests.test_command_create_contract import login

@pytest.fixture(autouse=True)
def clean(monkeypatch):
    run(reset_db()); no_botapi(); db_models.reset_memory_collections(); auth.reset_auth_state()
    monkeypatch.setattr(config,'ADMIN_WEB_TOKEN','synthetic-admin-token')
    yield


def test_owner_scoping_paging_types_secrecy_and_compatibility(monkeypatch):
    async def go():
        from mongomock_motor import AsyncMongoMockClient
        col=AsyncMongoMockClient().db.registered_bots
        monkeypatch.setattr(MainDB,'__init__',lambda self:setattr(self,'bots',col))
        rows=[{'_id':i,'owner_id':111,'bot_username':f'bot_{i}','is_active':i%2==0,'bot_token_encrypted':'SECRET','settings':{'secret':'HIDDEN'}} for i in range(1,206)]
        rows += [{'_id':Int64(2**40),'owner_id':111,'bot_username':'long_id'}, {'_id':206,'owner_id':111,'is_deleted':True}, {'_id':207,'owner_id':222,'bot_username':'other'}, {'_id':1.5,'owner_id':111,'bot_username':'float_bad'}, {'_id':'not-int','owner_id':111,'bot_username':'string_bad'}]
        await col.insert_many(rows)
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner,_=await login(cli,111); other,_=await login(cli,222)
            path='/api/v1/account/legacy-bots/pages'
            assert (await cli.get(path)).status==401
            all_ids=[]; after=None
            for expected in (100,100,6):
                r=await cli.get(path+('' if after is None else '?after='+after),headers=owner)
                assert r.status==200 and r.headers['Cache-Control']=='no-store'
                raw=await r.text(); assert 'SECRET' not in raw and 'HIDDEN' not in raw and 'other' not in raw
                data=await r.json();assert data['pagination']=='id-v1' and len(data['bots'])==expected
                all_ids.extend(b['telegram_bot_id'] for b in data['bots']);after=data['next_after']
            assert after is None and all_ids==[str(i) for i in range(1,206)]+[str(2**40)]
            outsider=await (await cli.get(path+'?after=100',headers=other)).json()
            assert [x['telegram_bot_id'] for x in outsider['bots']]==['207']
            assert (await cli.get(path+'?after=100&owner_id=222',headers=owner)).status==200
            check=await (await cli.get(path+'?after=100&owner_id=222',headers=owner)).json()
            assert all(x['telegram_bot_id']!='207' for x in check['bots'])
            legacy=await (await cli.get('/api/v1/account/legacy-bots',headers=owner)).json()
            assert set(legacy)=={'bots','truncated'} and legacy['truncated'] is True
        assert await col.count_documents({})==len(rows)
    run(go())


def test_rejects_bad_cursors_and_non_telegram_auth(monkeypatch):
    async def go():
        from mongomock_motor import AsyncMongoMockClient
        col=AsyncMongoMockClient().db.registered_bots
        monkeypatch.setattr(MainDB,'__init__',lambda self:setattr(self,'bots',col))
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner,_=await login(cli,111); path='/api/v1/account/legacy-bots/pages'
            for value in ('','0','-1','01','1.2','true','abc','9223372036854775808','1%26after%3D2'):
                r=await cli.get(path+'?after='+value,headers=owner)
                assert r.status==400, value
                assert (await r.json())['error']=='Invalid clone page cursor.'
            assert (await cli.get(path+'?after=1&after=2',headers=owner)).status==400
            admin=await (await cli.post('/api/v1/auth/login',json={'admin_token':'synthetic-admin-token'})).json()
            assert (await cli.get(path,headers={'Authorization':'Bearer '+admin['session_token']})).status==403
            plain=auth.create_session(111,display_name='unverified')
            assert (await cli.get(path,headers={'Authorization':'Bearer '+plain['session_token']})).status==403
            assert (await cli.get(path,headers={'X-Admin-Token':'synthetic-admin-token'})).status==403
    run(go())


def test_backend_failure_is_not_a_false_empty_page(monkeypatch):
    async def go():
        def fail(self): raise RuntimeError('private database secret')
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner,_=await login(cli,111);monkeypatch.setattr(MainDB,'__init__',fail)
            r=await cli.get('/api/v1/account/legacy-bots/pages',headers=owner)
            assert r.status==500 and 'private database secret' not in await r.text()
    run(go())


def test_page_boundaries_and_live_mutations(monkeypatch):
    async def go():
        from mongomock_motor import AsyncMongoMockClient
        col=AsyncMongoMockClient().db.registered_bots
        monkeypatch.setattr(MainDB,'__init__',lambda self:setattr(self,'bots',col))
        async with TestClient(TestServer(keep_alive.make_app())) as cli:
            owner,_=await login(cli,111);path='/api/v1/account/legacy-bots/pages'
            async def page(suffix=''):
                response=await cli.get(path+suffix,headers=owner)
                assert response.status==200
                return await response.json()
            assert (await page())['bots']==[]
            await col.insert_many([{'_id':i,'owner_id':111} for i in range(1,101)])
            first=await page(); assert len(first['bots'])==100 and first['next_after'] is None
            await col.insert_one({'_id':101,'owner_id':111})
            first=await page(); assert first['next_after']=='100'
            await col.delete_one({'_id':101}); await col.insert_one({'_id':102,'owner_id':111})
            second=await page('?after=100');assert [b['telegram_bot_id'] for b in second['bots']]==['102']
            assert (await page('?after=9223372036854775807'))['bots']==[]
    run(go())

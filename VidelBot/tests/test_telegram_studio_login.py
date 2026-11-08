"""Telegram Mini App login regressions, using mock Mongo and no real Telegram."""
import asyncio
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest
from aiohttp.test_utils import TestClient, TestServer
from tests.harness import FakeClient, FakeMsg, load_all, no_botapi, reset_db, run

load_all()
import config
import keep_alive
from core import menus
from webapp import auth, db_models


@pytest.fixture(autouse=True)
def clean_state():
    run(reset_db()); no_botapi(); auth.reset_auth_state()
    yield


def proof(uid=501, *, timestamp=None, token=None):
    fields = {'auth_date':str(int(time.time()) if timestamp is None else timestamp),
              'query_id':'synthetic-launch', 'user':json.dumps({'id':uid,'first_name':'Test'},separators=(',',':'))}
    data = '\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    secret = hmac.new(b'WebAppData',(token or config.BOT_TOKEN).encode(),hashlib.sha256).digest()
    fields['hash'] = hmac.new(secret,data.encode(),hashlib.sha256).hexdigest()
    return urlencode(fields)


def test_verified_non_admin_mini_app_login_and_replay_rejection():
    async def go():
        signed=proof(56789)
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            result=await client.post('/api/v1/auth/login',json={'init_data':signed})
            assert result.status==200
            body=await result.json()
            assert body['user']['user_id']==56789 and not body['user']['is_platform_admin']
            assert any(w['owner_id']==56789 for w in body['workspaces'])
            replay=await client.post('/api/v1/auth/login',json={'init_data':signed})
            assert replay.status==401 and 'already used' in (await replay.json())['error']
            records=await db_models.col('saas_telegram_login_proofs').find({}).to_list(None)
            assert len(records)==1 and signed not in str(records)
            assert 'expires_at' in records[0]
    run(go())


def test_simultaneous_replay_has_only_one_winner():
    async def go():
        signed=proof(56790)
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            responses=await asyncio.gather(*(client.post('/api/v1/auth/login',json={'init_data':signed}) for _ in range(2)))
            assert sorted(r.status for r in responses)==[200,401]
    run(go())


def test_tampered_expired_future_or_ambiguous_proofs_fail():
    async def go():
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            for item in (proof(token='1:incorrect'),proof(timestamp=int(time.time())-7200),
                         proof(timestamp=int(time.time())+3600),proof()+'&query_id=duplicate',
                         'hash=%C3%A9',proof(uid=-1)):
                result=await client.post('/api/v1/auth/login',json={'init_data':item})
                assert result.status==401
            assert await db_models.col('saas_telegram_login_proofs').count_documents({})==0
    run(go())


def test_replay_store_failure_is_closed_and_does_not_expose_error(monkeypatch):
    async def go():
        original=db_models.col
        class Broken:
            async def create_index(self,*args,**kwargs): raise RuntimeError('sensitive-test-error')
        monkeypatch.setattr(db_models,'col',lambda name: Broken() if name=='saas_telegram_login_proofs' else original(name))
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            response=await client.post('/api/v1/auth/login',json={'init_data':proof(56791)})
            assert response.status==503 and 'sensitive-test-error' not in await response.text()
            assert not auth._sessions
    run(go())


def test_web_command_offers_telegram_miniapp_without_secret_url(monkeypatch):
    class Capture(FakeMsg):
        async def reply_text(self,text,*args,**kwargs):
            self.kwargs=kwargs
            return await super().reply_text(text,*args,**kwargs)
    async def go():
        monkeypatch.setattr(config,'KEEP_ALIVE_URL','https://example.org')
        msg=Capture(text='/web',uid=98765)
        await menus.web_studio_cmd(FakeClient(),msg)
        rows=msg.kwargs['reply_markup'].inline_keyboard
        assert rows[0][0].web_app.url=='https://example.org/app'
        assert rows[0][0].url is None
        assert rows[1][0].url.startswith('https://example.org/app#l.')
        assert 'automatically' in msg.replies[0]
    run(go())


def test_web_command_rejects_unsafe_base_url(monkeypatch):
    class Capture(FakeMsg):
        async def reply_text(self,text,*args,**kwargs):
            self.kwargs=kwargs
            return await super().reply_text(text,*args,**kwargs)
    async def go():
        for base in ('https://user:password@example.org', 'https://example.org/path',
                     'https://example.org/?token=secret', 'http://example.org', 'https://example.org:bad'):
            monkeypatch.setattr(config,'KEEP_ALIVE_URL',base)
            msg=Capture(text='/web',uid=98765)
            await menus.web_studio_cmd(FakeClient(),msg)
            assert msg.kwargs['reply_markup'] is None
            assert base not in msg.replies[0]
    run(go())


def test_proof_claim_requests_majority_and_stores_only_digest(monkeypatch):
    from datetime import datetime
    from webapp.telegram_proof import consume_verified_init_data
    class ProofCollection:
        def __init__(self): self.index = None; self.document = None
        async def create_index(self, field, **kwargs): self.index = (field, kwargs)
        async def insert_one(self, document): self.document = document
    proof_collection = ProofCollection()
    class Database:
        def get_collection(self, name, *, write_concern):
            assert name == 'saas_telegram_login_proofs'
            assert write_concern.document == {'w': 'majority'}
            return proof_collection
    class Handle:
        database = Database()
    monkeypatch.setattr(db_models, 'col', lambda name: Handle())
    signed = proof(56792)
    run(consume_verified_init_data(signed, keep_alive.INIT_MAX_AGE))
    assert proof_collection.index == ('expires_at', {'expireAfterSeconds': 0})
    doc = proof_collection.document
    assert set(doc) == {'_id', 'expires_at'} and len(doc['_id']) == 64
    assert isinstance(doc['expires_at'], datetime) and signed not in str(doc)


def test_replayed_launches_are_rate_limited():
    async def go():
        signed = proof(56793)
        async with TestClient(TestServer(keep_alive.make_app())) as client:
            assert (await client.post('/api/v1/auth/login', json={'init_data': signed})).status == 200
            for _ in range(auth.AUTH_FAIL_MAX):
                assert (await client.post('/api/v1/auth/login', json={'init_data': signed})).status == 401
            assert (await client.post('/api/v1/auth/login', json={'init_data': signed})).status == 429
    run(go())


def test_web_command_keeps_telegram_available_at_link_capacity(monkeypatch):
    class Capture(FakeMsg):
        async def reply_text(self, text, *args, **kwargs):
            self.kwargs = kwargs
            return await super().reply_text(text, *args, **kwargs)
    async def go():
        monkeypatch.setattr(config, 'KEEP_ALIVE_URL', 'https://example.org')
        monkeypatch.setattr(auth, 'LINK_MAX', 0)
        msg = Capture(text='/web', uid=98765)
        await menus.web_studio_cmd(FakeClient(), msg)
        assert msg.kwargs['reply_markup'].inline_keyboard[0][0].web_app.url == 'https://example.org/app'
        assert 'temporarily unavailable' in msg.replies[0]
    run(go())

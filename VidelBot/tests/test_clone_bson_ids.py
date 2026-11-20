"""Regression for actual PyMongo BSON int64 decoding in clone registry records."""
from types import SimpleNamespace
from tests.harness import FakeClient, FakeMsg, FakeQuery, run
from bson import BSON, Int64
from core import ui
from filestore.utils.registry_ids import valid_registry_id
from filestore.main_bot.plugins import clone_lifecycle, my_bots, owner_cmds
from filestore.worker_bot import engine


BOT=2**33+10
OWNER=2**33+5


def decoded():
    row={'_id':BOT, 'owner_id':OWNER, 'is_active':False, 'is_deleted':False,
         'bot_token_encrypted':'secret', 'bot_username':'group_bot', 'clone_profile':'group'}
    record=BSON(BSON.encode(row)).decode()
    assert type(record['_id']) is Int64 and type(record['owner_id']) is Int64
    return record


def test_mongo_bson_id_validation():
    row=decoded();assert valid_registry_id(row['_id']) and valid_registry_id(row['owner_id'])
    for value in [True,False,None,'100',1.0,0,-1,2**63,Int64(-1)]:
        assert not valid_registry_id(value)


def test_group_dashboard_accepts_decoded_bson_ids(monkeypatch):
    async def go():
        async def load(bot_id): return decoded()
        edits=[]
        async def edit(message,text,markup): edits.append(text)
        monkeypatch.setattr(my_bots.main_db,'get_bot',load)
        monkeypatch.setattr(ui,'smart_edit',edit)
        class Worker:
            def get_worker(self,bid): return None
        monkeypatch.setattr(engine,'worker_engine',Worker())
        q=FakeQuery(f'dashboard_{BOT}',uid=OWNER);await my_bots.dashboard_callback(FakeClient(),q)
        assert edits and f'<code>{BOT}</code>' in edits[-1] and '/gcheck' in edits[-1]
    run(go())


def test_reactivation_accepts_decoded_bson_ids(monkeypatch):
    async def go():
        run_id=[];record=decoded()
        async def load(bid): return record
        async def mark(bot): run_id.append((bot['_id'],bot['owner_id']));return True
        class Worker:
            def __init__(self): self.client=None
            def get_worker(self,bid): return self.client
            async def start_worker(self,bot): self.client=object()
        monkeypatch.setattr(clone_lifecycle.main_db,'get_bot',load)
        monkeypatch.setattr(clone_lifecycle.main_db,'reactivate_inactive_if_unchanged',mark)
        monkeypatch.setattr(engine,'worker_engine',Worker())
        q=FakeQuery(f'cl_on_{BOT}',uid=OWNER);await clone_lifecycle.reactivate_cb(FakeClient(),q)
        assert run_id==[(BOT,OWNER)] and 'local client was found' in q.message.edits[-1]
    run(go())


def test_owner_directory_and_health_accept_decoded_bson_ids(monkeypatch):
    record=decoded();record['is_active']=True
    async def load(): return [record]
    async def users(): return 1
    async def get_page(page, page_size): return [record]
    async def count(): return 1
    async def count_invalid(): return 0
    async def profile_counts():
        return {'filestore': 0, 'group': 1, 'music': 0, 'unknown': 0, 'total': 1}
    async def iter_ids():
        yield record['_id']
    monkeypatch.setattr(owner_cmds.main_db,'get_clone_directory_records',load)
    monkeypatch.setattr(owner_cmds.main_db,'get_clone_directory_page',get_page)
    monkeypatch.setattr(owner_cmds.main_db,'count_clone_directory_records',count)
    monkeypatch.setattr(owner_cmds.main_db,'count_invalid_clone_directory_records',count_invalid)
    monkeypatch.setattr(owner_cmds.main_db,'clone_directory_profile_counts',profile_counts)
    monkeypatch.setattr(owner_cmds.main_db,'iter_clone_directory_ids',iter_ids)
    monkeypatch.setattr(owner_cmds.main_db,'get_all_bots',load)
    monkeypatch.setattr(owner_cmds.main_db,'total_users',users)
    class Worker:
        async def get_me(self): return SimpleNamespace(id=BOT)
    class Engine:
        def get_worker(self,bid): return client
    client=Worker();monkeypatch.setattr(engine,'worker_engine',Engine())
    output=[]
    async def capture(message,doc,**kw): output.append(doc)
    monkeypatch.setattr(owner_cmds.rich,'reply',capture)
    run(owner_cmds.list_all_bots(FakeClient(),FakeMsg('/bots',uid=111)))
    assert str(BOT) in output[-1].classic() and str(OWNER) in output[-1].classic() and 'invalid excluded' in output[-1].classic()
    run(owner_cmds.platform_stats(FakeClient(),FakeMsg('/clonestats',uid=111)))
    assert dict(next(block[1] for block in output[-1].blocks if block[0]=='table'))['🛡 Group Management records']=='1'
    class Status:
        async def finish(self,text): self.text=text
    status=Status();run(owner_cmds._check_bots(FakeClient(),FakeMsg('/check',uid=111),status))
    assert 'matching identity: <code>1</code>' in status.text
    assert 'Unknown or changed: <code>0</code>' in status.text


def test_reactivation_compare_and_set_with_decoded_int64_values():
    from mongomock_motor import AsyncMongoMockClient
    from filestore.database.main_db import MainDB
    async def go():
        db=MainDB.__new__(MainDB);db.bots=AsyncMongoMockClient().diagnostics.bots
        row=decoded();await db.bots.insert_one(row)
        assert await db.reactivate_inactive_if_unchanged(row) is True
        saved=await db.bots.find_one({'_id':BOT});assert saved['is_active'] is True
        assert await db.reactivate_inactive_if_unchanged(row) is False
    run(go())

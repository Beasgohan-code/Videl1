"""Synthetic owner-only clone registry summaries; no live worker or MongoDB."""
import copy
from types import SimpleNamespace

import pytest
from tests.harness import FakeClient, FakeMsg, run
from filestore.main_bot.plugins import owner_cmds as commands
from filestore.worker_bot import engine


@pytest.fixture
def capture(monkeypatch):
    docs = []
    async def reply(message, doc, **kwargs): docs.append(doc)
    monkeypatch.setattr(commands.rich, 'reply', reply)
    return docs


def setup(monkeypatch, records, *, workers=None, users=3):
    async def get_all(): return records
    async def get_users():
        if isinstance(users, Exception): raise users
        return users
    monkeypatch.setattr(commands.main_db, 'get_clone_directory_records', get_all)
    monkeypatch.setattr(commands.main_db, 'total_users', get_users)
    class Engine:
        def get_worker(self, bid):
            if workers == 'fail': raise RuntimeError('sensitive-token')
            return object() if bid in (workers or ()) else None
    monkeypatch.setattr(engine, 'worker_engine', Engine())


def rows(doc): return dict(next(block[1] for block in doc.blocks if block[0] == 'table'))


def test_stats_break_down_registry_modes_without_claiming_live_state(monkeypatch, capture):
    docs = [
        {'_id':10,'owner_id':1,'bot_username':'filestore_bot','is_active':True},
        {'_id':11,'owner_id':1,'clone_profile':'group','is_active':True},
        {'_id':12,'owner_id':1,'clone_profile':'music','is_active':False},
        {'_id':13,'owner_id':1,'clone_profile':None,'is_active':False},
        {'_id':14,'owner_id':1,'clone_profile':'filestore','is_deleted':True},
        {'_id':'SECRET-TOKEN','clone_profile':'group'},
        {'_id':15,'is_deleted':None},
    ]
    original=copy.deepcopy(docs);setup(monkeypatch,docs,workers={10,11},users=9)
    run(commands.platform_stats(FakeClient(),FakeMsg('/clonestats',uid=111)))
    values=rows(capture[-1]);assert values['👥 Registered users']=='9'
    assert values['🤖 Non-deleted clone records']=='4'
    assert values['📁 File Store records']=='1' and values['🛡 Group Management records']=='1'
    assert values['🎵 Music records (unsupported)']=='1' and values['❔ Unknown profiles']=='1'
    assert values['⚠️ Invalid records excluded']=='2' and values['👷 Local clients found']=='2'
    output=capture[-1].classic();assert 'SECRET-TOKEN' not in output and 'not runtime health' in output
    assert 'Music playback is unavailable' in output and docs==original


def test_stats_unavailable_data_never_shows_false_zero_or_secrets(monkeypatch, capture):
    setup(monkeypatch,[{'_id':1,'is_active':True}],workers='fail',users=RuntimeError('sensitive-token'))
    run(commands.platform_stats(FakeClient(),FakeMsg('/clonestats',uid=111)))
    values=rows(capture[-1]);assert values['👥 Registered users']=='Unavailable'
    assert values['👷 Local clients found']=='Unavailable' and 'sensitive-token' not in capture[-1].classic()


def test_directory_pagination_includes_inactive_and_is_read_only(monkeypatch, capture):
    docs=[{'_id':n,'owner_id':4,'bot_username':f'botname_{n}','is_active':n%2==0,
           'clone_profile':'group' if n==16 else 'filestore'} for n in range(31,0,-1)]
    original=copy.deepcopy(docs);setup(monkeypatch,docs,workers={16})
    run(commands.list_all_bots(FakeClient(),FakeMsg('/bots 2',uid=111)))
    doc=capture[-1]; entries=rows(doc);assert doc.sub=='Page 2 of 3' and len(entries)==15
    assert 'botname_16' in str(entries) and 'Group Management' in str(entries)
    assert 'Present here' in str(entries) and 'Marked inactive' in str(entries)
    assert 'not health' in doc.classic() and len(doc.classic())<4096 and docs==original
    run(commands.list_all_bots(FakeClient(),FakeMsg('/bots 999',uid=111)))
    assert capture[-1].sub=='Page 3 of 3' and 'adjusted' in capture[-1].classic()


def test_directory_safe_unknown_values_and_music_never_claims_playback(monkeypatch, capture):
    token='123456:SECRET-TOKEN';docs=[{'_id':1,'owner_id':token,'bot_username':'<script>bad</script>','clone_profile':token,'is_active':'yes'},
                                     {'_id':2,'owner_id':5,'bot_username':'music_bot','clone_profile':'music','is_active':False}]
    setup(monkeypatch,docs,workers='fail')
    run(commands.list_all_bots(FakeClient(),FakeMsg('/bots',uid=111)))
    output=capture[-1].classic(); assert token not in output and '<script>' not in output
    assert 'Unknown / unsupported' in output and 'State unknown' in output and 'Unavailable' in output
    assert 'Music (unsupported)' in output and 'Music playback is unavailable' in output


def test_directory_empty_does_not_claim_bad_records_absent(monkeypatch,capture):
    setup(monkeypatch,[{'_id':'secret-token'},{'_id':99,'is_deleted':True}])
    run(commands.list_all_bots(FakeClient(),FakeMsg('/bots',uid=111)))
    text=capture[-1].classic();assert '1 invalid record' in text and 'secret-token' not in text


@pytest.mark.parametrize('arg', ['0','-1','1001','1.5','01','9'*5000,'<bad>'])
def test_invalid_page_never_loads_registry(monkeypatch,arg):
    async def fail(): pytest.fail('Invalid pages must not read registry')
    monkeypatch.setattr(commands.main_db,'get_clone_directory_records',fail)
    msg=FakeMsg('/bots '+arg,uid=111);run(commands.list_all_bots(FakeClient(),msg))
    assert msg.replies[-1] == 'Use a page number from 1 to 1000: <code>/bots [page]</code>'


def test_page_cap_is_disclosed(monkeypatch,capture):
    monkeypatch.setattr(commands,'_CLONE_PAGE_SIZE',1)
    setup(monkeypatch,[{'_id':n} for n in range(1,1002)])
    run(commands.list_all_bots(FakeClient(),FakeMsg('/bots 1000',uid=111)))
    assert capture[-1].sub=='Page 1000 of 1001'
    assert 'Pages after 1000 are not available here' in capture[-1].classic()


def test_registry_failures_do_not_leak_or_look_empty(monkeypatch,capture,caplog):
    async def fail(): raise RuntimeError('sensitive-token')
    monkeypatch.setattr(commands.main_db,'get_clone_directory_records',fail)
    for callback,text in ((commands.platform_stats,'/clonestats'),(commands.list_all_bots,'/bots')):
        msg=FakeMsg(text,uid=111);run(callback(FakeClient(),msg))
        assert 'unavailable' in msg.replies[-1] and 'sensitive-token' not in msg.replies[-1]
    assert not capture and 'sensitive-token' not in caplog.text


def test_directory_projection_omits_credentials_and_other_fields():
    from filestore.database.main_db import MainDB
    from mongomock_motor import AsyncMongoMockClient
    db=MainDB.__new__(MainDB);db.bots=AsyncMongoMockClient().diagnostics.bots
    record={'_id':123,'owner_id':5,'bot_username':'valid_bot','is_active':True,
            'clone_profile':'group','bot_token_encrypted':'secret-token','session':'secret-session',
            'settings':{'hidden':'secret'}}
    run(db.bots.insert_one(record));result=run(db.get_clone_directory_records())
    assert result == [{key:record[key] for key in ('_id','owner_id','bot_username','is_active','clone_profile')}]
    assert run(db.bots.find_one({'_id':123}))['bot_token_encrypted']=='secret-token'


def test_worst_case_page_remains_bounded_and_complete(monkeypatch,capture):
    docs=[{'_id':(2**63)-n-1,'owner_id':(2**63)-1,'bot_username':'a'*32,
           'clone_profile':'music','is_active':None} for n in range(15)]
    setup(monkeypatch,docs,workers='fail');run(commands.list_all_bots(FakeClient(),FakeMsg('/bots',uid=111)))
    doc=capture[-1];output=doc.classic()
    assert len(output)<4096 and output.count('Music (unsupported)')==15
    assert 'Music playback is unavailable' in output


@pytest.mark.parametrize('callback,text', [(commands.platform_stats, '/clonestats'),
                                         (commands.platform_stats, '/fsstats'),
                                         (commands.list_all_bots, '/bots')])
def test_handler_filter_requires_owner_and_private_chat(callback, text):
    from pyrogram.enums import ChatType
    handler = callback.handlers[0][0]
    client=FakeClient()
    assert run(handler.filters(client, FakeMsg(text, uid=111)))
    assert not run(handler.filters(client, FakeMsg(text, uid=222)))
    assert not run(handler.filters(client, FakeMsg(text, uid=111, chat_type=ChatType.SUPERGROUP)))

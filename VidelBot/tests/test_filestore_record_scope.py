"""Offline workspace/record scoping checks; real Mongo and filesystem races remain open."""
from types import SimpleNamespace

import pytest

from tests.harness import reset_db, run
from webapp import db_models, services_clone_files as fs


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    db_models.reset_memory_collections()


@pytest.mark.parametrize('action,payload,changes',[
    ('rename',{'new_name':'changed.txt'},{'filename':'changed.txt'}),
    ('update_metadata',{'description':'changed'},{'description':'changed'}),
    ('move',{'target_bot_id':'default'},{'bot_id':'default'}),
    ('soft_delete',{'confirmed':True},{'deleted':True}),
    ('restore',{}, {'deleted':False}),
    ('purge',{'confirmed':True},None),
])
def test_colliding_uids_cannot_mutate_or_return_other_workspace(monkeypatch,action,payload,changes):
    async def go():
        coll=db_models.files_col()
        foreign={'_id':'foreign','file_uid':'same','workspace_id':'A','owner_id':1,'filename':'foreign.txt','deleted':True,'bot_id':'x','local_rel_path':''}
        selected={'_id':'selected','file_uid':'same','workspace_id':'B','owner_id':2,'filename':'selected.txt','deleted':True,'bot_id':'y','local_rel_path':''}
        await coll.insert_one(foreign);await coll.insert_one(selected)
        async def audit(*a,**kw):pass
        monkeypatch.setattr(fs.models_db,'log_audit',audit)
        result=await fs.operate_filestore_file('B','same',2,action=action,payload=payload)
        assert await coll.find_one({'_id':'foreign'})==foreign
        if changes is None:
            assert await coll.find_one({'_id':'selected'}) is None
            assert result=={'action':'purge','deleted':True,'file_uid':'same'}
        else:
            row=await coll.find_one({'_id':'selected'})
            assert all(row[k]==v for k,v in changes.items())
            assert result['file']['workspace_id']=='B' and result['file']['filename']==row['filename']
    run(go())


def test_same_uid_alias_blocks_physical_rename_and_purge(tmp_path,monkeypatch):
    import config
    monkeypatch.setattr(config,'DOWNLOAD_DIR',str(tmp_path))
    async def go():
        coll=db_models.files_col();ws='B';path=fs.workspace_file_prefix(ws)+'original.txt'
        physical=fs.resolve_workspace_file(ws,2,path,must_exist=False)
        from pathlib import Path
        Path(physical).parent.mkdir(parents=True,exist_ok=True);Path(physical).write_text('valuable')
        original={'_id':'original','file_uid':'same','workspace_id':ws,'owner_id':2,'filename':'original.txt','deleted':True,'local_rel_path':path}
        alias={**original,'_id':'alias'}
        await coll.insert_one(original);await coll.insert_one(alias)
        for action,payload in [('rename',{'new_name':'new.txt'}),('purge',{'confirmed':True})]:
            with pytest.raises(fs.services_bots.SaaSValidationError,match='Shared physical paths'):
                await fs.operate_filestore_file(ws,'same',2,action=action,payload=payload)
        assert Path(physical).read_text()=='valuable' and await coll.count_documents({})==2
    run(go())


@pytest.mark.parametrize('result',[SimpleNamespace(acknowledged=False),SimpleNamespace(acknowledged=True,matched_count=0)])
def test_unconfirmed_write_never_returns_success(monkeypatch,result):
    async def go():
        coll=db_models.files_col();doc={'_id':'one','file_uid':'x','workspace_id':'A','owner_id':1,'filename':'old','local_rel_path':''}
        await coll.insert_one(doc)
        async def no_write(*args,**kwargs):return result
        monkeypatch.setattr(coll,'update_one',no_write);monkeypatch.setattr(db_models,'files_col',lambda:coll)
        with pytest.raises(RuntimeError,match='could not be confirmed'):
            await fs.operate_filestore_file('A','x',1,action='rename',payload={'new_name':'new'})
        assert (await coll.find_one({'_id':'one'}))['filename']=='old'
    run(go())


def test_post_write_missing_record_is_reported_as_uncertain(monkeypatch):
    async def go():
        coll=db_models.files_col();await coll.insert_one({'_id':'one','file_uid':'x','workspace_id':'A','owner_id':1,'filename':'old','local_rel_path':''})
        find=coll.find_one;calls=[]
        async def vanishes(query):
            calls.append(query)
            return await find(query) if len(calls)==1 else None
        monkeypatch.setattr(coll,'find_one',vanishes);monkeypatch.setattr(db_models,'files_col',lambda:coll)
        with pytest.raises(RuntimeError,match='could not be confirmed'):
            await fs.operate_filestore_file('A','x',1,action='rename',payload={'new_name':'new'})
        assert (await find({'_id':'one'}))['filename']=='new'
    run(go())


def test_standalone_memory_results_support_acknowledged_scoped_actions(monkeypatch):
    async def go():
        coll=db_models._MemCollection();monkeypatch.setattr(db_models,'files_col',lambda:coll)
        await coll.insert_one({'_id':'x','file_uid':'x','workspace_id':'A','owner_id':1,'filename':'old','deleted':False,'local_rel_path':''})
        result=await fs.operate_filestore_file('A','x',1,action='rename',payload={'new_name':'new'})
        assert result['file']['filename']=='new'
        await fs.operate_filestore_file('A','x',1,action='soft_delete',payload={'confirmed':True})
        async def audit(*args,**kwargs):pass
        monkeypatch.setattr(fs.models_db,'log_audit',audit)
        assert (await fs.operate_filestore_file('A','x',1,action='purge',payload={'confirmed':True}))['deleted'] is True
    run(go())


@pytest.mark.parametrize('result',[SimpleNamespace(acknowledged=False),SimpleNamespace(acknowledged=True,deleted_count=0)])
def test_unconfirmed_purge_never_reports_success(monkeypatch,result):
    async def go():
        coll=db_models.files_col();doc={'_id':'one','file_uid':'x','workspace_id':'A','owner_id':1,'deleted':True,'local_rel_path':''}
        await coll.insert_one(doc)
        async def no_delete(*args,**kwargs):return result
        monkeypatch.setattr(coll,'delete_one',no_delete);monkeypatch.setattr(db_models,'files_col',lambda:coll)
        with pytest.raises(RuntimeError,match='could not be confirmed'):
            await fs.operate_filestore_file('A','x',1,action='purge',payload={'confirmed':True})
        assert await coll.find_one({'_id':'one'})==doc
    run(go())


def test_missing_record_identity_fails_closed(monkeypatch):
    async def go():
        coll=db_models.files_col()
        async def no_identity(query):return {'file_uid':'x','workspace_id':'A','local_rel_path':''}
        monkeypatch.setattr(coll,'find_one',no_identity);monkeypatch.setattr(db_models,'files_col',lambda:coll)
        with pytest.raises(RuntimeError,match='identity'):
            await fs.operate_filestore_file('A','x',1,action='rename',payload={'new_name':'new'})
    run(go())

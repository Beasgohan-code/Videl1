"""Offline mode dashboard tests. No live Telegram or MongoDB."""
import asyncio

import pytest
from tests.harness import FakeClient, FakeQuery, no_botapi, reset_db, run
from pyrogram import enums
import config
from filestore.main_bot.plugins import create_bot, my_bots
from filestore.worker_bot import engine
from core import ui


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db()); no_botapi(); create_bot._creation_state.clear()
    yield
    create_bot._creation_state.clear(); no_botapi()


async def add(**fields):
    row={'_id':42, 'owner_id':5, 'is_active':False, 'is_deleted':False,
         'bot_username':'group_bot', 'bot_token_encrypted':'secret-token',
         'clone_profile':'group', 'settings':{'custom_caption':'preserve'}}
    row.update(fields); await my_bots.main_db.bots.insert_one(row)


class Worker:
    def __init__(self, present=False, fail=False): self.present=present;self.fail=fail
    def get_worker(self, bid):
        if self.fail: raise RuntimeError('sensitive-token')
        return object() if self.present else None


def buttons(markup): return [b for row in markup.inline_keyboard for b in row]


def capture_edits(monkeypatch):
    edits=[]
    async def edit(message,text,markup): edits.append((text,markup))
    monkeypatch.setattr(ui,'smart_edit',edit)
    return edits


@pytest.mark.parametrize('gate,present',[(True,True),(True,False),(False,True),(False,False)])
def test_group_dashboard_is_actionable_and_truthful(monkeypatch,gate,present):
    async def go():
        monkeypatch.setattr(config,'CLONE_PROFILES_SINGLE_INSTANCE',gate)
        monkeypatch.setattr(engine,'worker_engine',Worker(present));edits=capture_edits(monkeypatch)
        await add();before=await my_bots.main_db.get_bot(42)
        q=FakeQuery('dashboard_42',uid=5);await my_bots.dashboard_callback(FakeClient(),q)
        text,markup=edits[-1]; items=buttons(markup); data=[b.callback_data for b in items]
        assert '/gcheck' in text and '/help' in text and 'Local client presence is not a health check' in text
        assert ('Local client:</b> Present' if present else 'Local client:</b> Not found') in text
        assert ('enabled on this process' if gate else 'Starting group profiles is disabled') in text
        assert 'File Store commands are not served' in text and 'data is retained' in text
        assert 'Music voice-chat playback is unavailable' in text and 'secret-token' not in text
        assert {b.url for b in items if b.url}=={'https://t.me/group_bot', 'https://t.me/group_bot?startgroup'}
        assert {'clprofile_42','transfer_42','confirm_delete_42','my_bots'} <= set(data)
        assert ('toggle_bot_42' in data) is (gate or present)
        assert not set(data).intersection({'auto_delete_42','shortener_42','toggle_protect_42'})
        assert await my_bots.main_db.get_bot(42)==before and q.answers
    run(go())


def test_failed_worker_observation_hides_toggle_and_error(monkeypatch):
    async def go():
        monkeypatch.setattr(config,'CLONE_PROFILES_SINGLE_INSTANCE',True)
        monkeypatch.setattr(engine,'worker_engine',Worker(fail=True));edits=capture_edits(monkeypatch)
        await add();q=FakeQuery('dashboard_42',uid=5);await my_bots.dashboard_callback(FakeClient(),q)
        text,markup=edits[-1];assert 'Local client:</b> Unavailable' in text
        assert 'sensitive-token' not in text and 'toggle_bot_42' not in [b.callback_data for b in buttons(markup)]
    run(go())


def test_pending_music_dashboard_only_offers_safe_session_onboarding(monkeypatch):
    async def go():
        monkeypatch.setattr(engine,'worker_engine',Worker(present=True));edits=capture_edits(monkeypatch)
        await add(clone_profile='filestore',music_setup_pending=True,is_active=False)
        q=FakeQuery('dashboard_42',uid=5);await my_bots.dashboard_callback(FakeClient(),q)
        text,markup=edits[-1];data=[b.callback_data for b in buttons(markup)]
        assert 'Music setup pending' in text and 'safely paused' in text
        assert data==['clmusic_42','my_bots']
        assert 'toggle_bot_42' not in data and 'secret-token' not in text
    run(go())


@pytest.mark.parametrize('profile',[None,'unknown-mode',{'secret':'sensitive-token'}])
def test_unsupported_modes_never_imply_running_or_expose_fields(monkeypatch,profile):
    async def go():
        monkeypatch.setattr(engine,'worker_engine',Worker(present=True));edits=capture_edits(monkeypatch)
        await add(clone_profile=profile);q=FakeQuery('dashboard_42',uid=5)
        await my_bots.dashboard_callback(FakeClient(),q)
        text,markup=edits[-1];data=[b.callback_data for b in buttons(markup)]
        assert 'This profile cannot be started here' in text
        assert 'secret-token' not in text and 'sensitive-token' not in text
        assert data==['clprofile_42','my_bots']
    run(go())


def test_music_dashboard_offers_session_setup_without_file_store_worker_controls(monkeypatch):
    async def go():
        monkeypatch.setattr(engine,'worker_engine',Worker(present=True));edits=capture_edits(monkeypatch)
        await add(clone_profile='music');q=FakeQuery('dashboard_42',uid=5)
        await my_bots.dashboard_callback(FakeClient(),q)
        text,markup=edits[-1];data=[b.callback_data for b in buttons(markup)]
        assert 'Music' in text and 'standalone music program' in text
        assert 'clmusic_42' in data and 'toggle_bot_42' not in data
        assert 'sensitive-token' not in text
    run(go())


@pytest.mark.parametrize('name',[None,'<script>token</script>','bad?start=secret','x','abc/def'])
def test_invalid_username_is_never_shown_or_linked(monkeypatch,name):
    async def go():
        monkeypatch.setattr(engine,'worker_engine',Worker());edits=capture_edits(monkeypatch)
        await add(bot_username=name);q=FakeQuery('dashboard_42',uid=5)
        await my_bots.dashboard_callback(FakeClient(),q)
        text,markup=edits[-1];assert 'Username unavailable' in text
        assert not any(b.url for b in buttons(markup)) and '<script>' not in text and 'secret' not in text
    run(go())


def test_dashboard_is_private_owned_and_non_deleted(monkeypatch):
    async def go():
        edits=capture_edits(monkeypatch);await add()
        qs=[FakeQuery('dashboard_42',uid=6), FakeQuery('dashboard_42',uid=5),
            FakeQuery('dashboard_42',uid=5), FakeQuery('dashboard_42',uid=5)]
        qs[1].message.chat.type=enums.ChatType.SUPERGROUP
        qs[2].message.chat.id=6
        qs[3].from_user.is_bot=True
        for q in qs:
            await my_bots.dashboard_callback(FakeClient(),q)
            assert q.answers[-1][1] and not edits
        await my_bots.main_db.bots.update_one({'_id':42},{'$set':{'is_deleted':True}})
        q=FakeQuery('dashboard_42',uid=5);await my_bots.dashboard_callback(FakeClient(),q)
        assert q.answers[-1][1] and not edits
    run(go())


def test_navigation_guard_and_delayed_lookup_are_preserved(monkeypatch):
    async def go():
        edits=capture_edits(monkeypatch);entered,release=asyncio.Event(),asyncio.Event()
        async def load(bot_id):
            entered.set();await release.wait()
            return {'_id':42,'owner_id':5,'clone_profile':'group'}
        monkeypatch.setattr(my_bots.main_db,'get_bot',load)
        q=FakeQuery('dashboard_42',uid=5);task=asyncio.create_task(my_bots.dashboard_callback(FakeClient(),q))
        await entered.wait();state={'step':'awaiting_token','data':{'registration_token':'private-secret'}}
        create_bot._creation_state[5]=state;release.set();await task
        assert not edits and create_bot._creation_state[5] is state and 'in progress' in q.answers[-1][0]
        assert 'private-secret' not in str(q.answers)
    run(go())


def test_legacy_filestore_dashboard_keeps_existing_layout(monkeypatch):
    async def go():
        monkeypatch.setattr(engine,'worker_engine',Worker());edits=capture_edits(monkeypatch)
        await add(clone_profile='filestore');q=FakeQuery('dashboard_42',uid=5)
        await my_bots.dashboard_callback(FakeClient(),q)
        text,markup=edits[-1];data=[b.callback_data for b in buttons(markup)]
        assert '𝗕𝗢𝗧 𝗗𝗔𝗦𝗛𝗕𝗢𝗔𝗥𝗗' in text and {'auto_delete_42','shortener_42','clprofile_42'} <= set(data)
        from core import rich
        assert rich.markup_ok(markup)
        assert 'Local client status is an observation' in text
        assert '/gcheck' not in text
    run(go())


def test_missing_profile_keeps_legacy_filestore_dashboard(monkeypatch):
    async def go():
        monkeypatch.setattr(engine,'worker_engine',Worker());edits=capture_edits(monkeypatch)
        await add();await my_bots.main_db.bots.update_one({'_id':42},{'$unset':{'clone_profile':''}})
        q=FakeQuery('dashboard_42',uid=5);await my_bots.dashboard_callback(FakeClient(),q)
        text,markup=edits[-1];assert '𝗕𝗢𝗧 𝗗𝗔𝗦𝗛𝗕𝗢𝗔𝗥𝗗' in text
        assert 'auto_delete_42' in [b.callback_data for b in buttons(markup)]
    run(go())

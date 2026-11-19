"""Managed-token rotation boundaries using fake Telegram and mock Mongo only."""
import asyncio
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pyrogram import enums
from tests.harness import FakeClient, FakeQuery, no_botapi, reset_db, run, use_botapi
from filestore.database.main_db import MainDB
from filestore.main_bot.plugins import managed_bots
from filestore.utils import security
from core import botapi
import config


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    run(reset_db()); no_botapi()
    managed_bots._rotation_pending.clear(); managed_bots._rotation_in_flight.clear(); managed_bots._rotation_uncertain.clear()
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(security, '_get_fernet', lambda: cipher)
    monkeypatch.setattr(config, 'CLONE_PROFILES_SINGLE_INSTANCE', True)
    monkeypatch.setattr(config, "MANAGED_ROTATION_ENABLED", True)
    run(MainDB().add_bot(424242, 5, 'enc:' + cipher.encrypt(b'424242:old').decode(), 'safe_bot', -100, managed=True))
    real_update = managed_bots.main_db.bots.update_one
    async def mock_update(*args, **kwargs):
        assert kwargs.pop('collation').document == {'locale': 'simple'}
        return await real_update(*args, **kwargs)
    monkeypatch.setattr(managed_bots.main_db.bots, 'update_one', mock_update)
    yield cipher
    no_botapi()
    managed_bots._rotation_pending.clear(); managed_bots._rotation_in_flight.clear(); managed_bots._rotation_uncertain.clear()


def preview(uid=5):
    q = FakeQuery('rotate_tok_424242', uid=uid)
    run(managed_bots.rotate_token(FakeClient(), q))
    return q


def confirmation():
    nonce = managed_bots._rotation_pending[424242]['nonce']
    return FakeQuery(f'rotate_tok_do_424242_{nonce}', uid=5)


def test_save_is_encrypted_one_use_and_does_not_claim_restart(setup):
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    preview(); q = confirmation(); run(managed_bots.rotate_token(FakeClient(), q))
    row = run(MainDB().get_bot(424242))
    assert setup.decrypt(row['bot_token_encrypted'][4:].encode()) == b'424242:newtoken'
    assert session.names().count('ReplaceManagedBotToken') == 1
    assert 'New token saved' in q.message.text and 'stop and start' in q.message.text
    assert 'newtoken' not in q.message.text
    run(managed_bots.rotate_token(FakeClient(), q))
    assert 'expired' in q.answers[-1][0] and session.names().count('ReplaceManagedBotToken') == 1


def test_legacy_confirmation_and_unauthorized_or_group_requests_cannot_rotate():
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    old = FakeQuery('rotate_tok_do_424242', uid=5); run(managed_bots.rotate_token(FakeClient(), old))
    assert 'expired' in old.answers[-1][0]
    outsider = preview(uid=6); assert 'denied' in outsider.answers[-1][0]
    group = FakeQuery('rotate_tok_424242', uid=5); group.message.chat.type = enums.ChatType.SUPERGROUP
    run(managed_bots.rotate_token(FakeClient(), group)); assert 'denied' in group.answers[-1][0]
    assert 'ReplaceManagedBotToken' not in session.names()


def test_disabled_gate_or_unavailable_key_fail_before_telegram(monkeypatch):
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    monkeypatch.setattr(config, 'CLONE_PROFILES_SINGLE_INSTANCE', False)
    assert 'single current worker' in preview().answers[-1][0]
    monkeypatch.setattr(config, 'CLONE_PROFILES_SINGLE_INSTANCE', True)
    monkeypatch.setattr(security, '_get_fernet', lambda: None)
    assert 'encryption key' in preview().answers[-1][0]
    assert 'ReplaceManagedBotToken' not in session.names()


def test_changed_record_prevents_rotation_and_retains_data():
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    preview(); q = confirmation()
    run(MainDB().bots.update_one({'_id': 424242}, {'$set': {'bot_token_encrypted': 'changed', 'settings.custom_caption': 'kept'}}))
    run(managed_bots.rotate_token(FakeClient(), q))
    assert 'not attempted' in q.message.text and 'ReplaceManagedBotToken' not in session.names()
    assert run(MainDB().get_bot(424242))['settings']['custom_caption'] == 'kept'


def test_api_failure_is_uncertain_no_retry_no_secret():
    session = use_botapi(fail={'ReplaceManagedBotToken': RuntimeError('synthetic-secret')})
    preview(); q = confirmation(); run(managed_bots.rotate_token(FakeClient(), q))
    assert 'uncertain' in q.message.text and 'synthetic-secret' not in q.message.text
    assert 424242 in managed_bots._rotation_uncertain
    assert 'do not retry' in preview().answers[-1][0].lower()
    assert session.names().count('ReplaceManagedBotToken') == 1


def test_database_failure_after_rotation_is_uncertain(monkeypatch):
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    preview(); q = confirmation()
    async def fail(*a, **kw): raise RuntimeError('synthetic-secret')
    monkeypatch.setattr(managed_bots.main_db.bots, 'update_one', fail)
    run(managed_bots.rotate_token(FakeClient(), q))
    assert 'uncertain' in q.message.text and 'synthetic-secret' not in q.message.text
    assert session.names().count('ReplaceManagedBotToken') == 1 and 424242 in managed_bots._rotation_uncertain


def test_duplicate_clicks_cannot_rotate_twice(monkeypatch):
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Bot:
            async def replace_managed_bot_token(self, **kw):
                calls.append(1); entered.set(); await release.wait(); return '424242:newtoken'
        monkeypatch.setattr(botapi, 'enabled', lambda: True)
        monkeypatch.setattr(botapi, 'bot', lambda: Bot())
        p = FakeQuery('rotate_tok_424242', uid=5); await managed_bots.rotate_token(FakeClient(), p)
        q1 = confirmation(); q2 = confirmation()
        task = asyncio.create_task(managed_bots.rotate_token(FakeClient(), q1)); await entered.wait()
        await managed_bots.rotate_token(FakeClient(), q2)
        release.set(); await task
        assert calls == [1] and 'already being processed' in q2.answers[-1][0]
    run(go())


def test_capability_flags_do_not_claim_unknown_is_disabled():
    from core.extras import _mode_flag
    assert _mode_flag(SimpleNamespace(supports_guest_queries=True), 'supports_guest_queries') == 'Enabled'
    assert _mode_flag(SimpleNamespace(supports_guest_queries=False), 'supports_guest_queries') == 'Disabled'
    assert _mode_flag(SimpleNamespace(), 'supports_guest_queries') == 'Unknown'


def test_foreign_encryption_key_and_uncertain_worker_stop_block_rotation(monkeypatch):
    import filestore.worker_bot.engine as engine
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    monkeypatch.setattr(security, '_get_fernet', lambda: Fernet(Fernet.generate_key()))
    assert 'cannot be decrypted' in preview().answers[-1][0]
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(security, '_get_fernet', lambda: cipher)
    run(MainDB().bots.update_one({'_id': 424242}, {'$set': {'bot_token_encrypted': 'enc:' + cipher.encrypt(b'424242:old').decode()}}))
    preview(); q = confirmation()
    engine.worker_engine._uncertain_stops[424242] = object()
    try:
        run(managed_bots.rotate_token(FakeClient(), q))
        assert 'not attempted' in q.message.text and 'ReplaceManagedBotToken' not in session.names()
    finally:
        engine.worker_engine._uncertain_stops.pop(424242, None)


def test_cancel_after_rotation_attempt_blocks_retry(monkeypatch):
    async def go():
        entered = asyncio.Event()
        class Bot:
            async def replace_managed_bot_token(self, **kw):
                entered.set(); await asyncio.Event().wait()
        monkeypatch.setattr(botapi, 'enabled', lambda: True)
        monkeypatch.setattr(botapi, 'bot', lambda: Bot())
        await managed_bots.rotate_token(FakeClient(), FakeQuery('rotate_tok_424242', uid=5))
        q = confirmation(); task = asyncio.create_task(managed_bots.rotate_token(FakeClient(), q))
        await entered.wait(); task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert 424242 in managed_bots._rotation_uncertain
    run(go())


def test_rotation_requires_explicit_opt_in_and_no_mtproto_fallback(monkeypatch):
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    monkeypatch.setattr(config, 'MANAGED_ROTATION_ENABLED', False)
    assert 'disabled' in preview().answers[-1][0]
    assert 'ReplaceManagedBotToken' not in session.names()
    monkeypatch.setattr(config, 'MANAGED_ROTATION_ENABLED', True)
    preview(); q = confirmation()
    monkeypatch.setattr(botapi, 'enabled', lambda: False)
    c = FakeClient(); run(managed_bots.rotate_token(c, q))
    assert 'not attempted' in q.message.text and not c.called('replace_managed_bot_token')


def test_mismatched_saved_credential_is_not_rotated():
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    run(MainDB().bots.update_one({'_id': 424242}, {'$set': {'bot_token_encrypted': '111:wrong'}}))
    assert 'does not match' in preview().answers[-1][0]
    assert 'ReplaceManagedBotToken' not in session.names()


def test_legacy_plaintext_matching_credential_is_sealed_on_rotation(setup):
    session = use_botapi(responses={'ReplaceManagedBotToken': '424242:newtoken'})
    run(MainDB().bots.update_one({'_id': 424242}, {'$set': {'bot_token_encrypted': '424242:old'}}))
    preview(); q = confirmation(); run(managed_bots.rotate_token(FakeClient(), q))
    saved = run(MainDB().get_bot(424242))['bot_token_encrypted']
    assert saved.startswith('enc:') and setup.decrypt(saved[4:].encode()) == b'424242:newtoken'
    assert session.names().count('ReplaceManagedBotToken') == 1

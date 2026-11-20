"""Offline Telegram profile snapshots; no live Telegram or private profile lookups."""
from types import SimpleNamespace

import pytest
from tests.harness import FakeClient, FakeMsg, run
from pyrogram import enums
from core import tools


def test_classic_info_reports_available_flags_without_private_fields_or_html_injection():
    user = SimpleNamespace(id=501, first_name='<script>Alice</script>', last_name='& Bob',
        username='alice', status=SimpleNamespace(name='RECENTLY'), dc_id=4,
        is_bot=False, is_premium=True, is_verified=True, is_scam=None,
        is_fake=False, is_restricted=None, is_deleted=False, is_support=None,
        mention='<a href="https://evil.example">unsafe</a>', phone_number='PRIVATE-PHONE')
    snapshot = tools._user_info_doc(user)
    doc = snapshot.classic()
    rows = dict(snapshot.blocks[0][1])
    assert 'tg://user?id=501' in doc and '&lt;script&gt;Alice&lt;/script&gt;' in doc
    assert '<script>' not in doc and 'evil.example' not in doc and 'PRIVATE-PHONE' not in doc
    assert rows['Premium'] == 'Yes' and rows['Verified'] == 'Yes'
    assert rows['Scam'] == 'Unavailable' and rows['Restricted'] == 'Unavailable'
    assert rows['Fake'] == 'No' and rows['Deleted'] == 'No' and rows['Support account'] == 'Unavailable'
    assert rows['Last seen'] == 'Recently' and 'Unavailable' in doc


def test_missing_flags_and_data_are_unavailable_and_long_names_bounded():
    doc = tools._user_info_doc(SimpleNamespace(id=3, first_name='X'*500, is_bot=None)).classic()
    assert 'X'*121 not in doc and '…' in doc and 'Hidden or unavailable' in doc
    assert doc.count('Unavailable') >= 9


@pytest.mark.parametrize('bad_id', [None, True, 0, -1, '123'])
def test_invalid_resolved_user_id_rejected(bad_id):
    with pytest.raises(ValueError):
        tools._user_info_doc(SimpleNamespace(id=bad_id))


def test_lookup_errors_and_malformed_user_do_not_leak_exception(monkeypatch):
    class Client(FakeClient):
        async def get_users(self, target):
            if target == '@bad': raise ValueError('secret-token-private-details')
            return SimpleNamespace(id='malformed', mention='secret-token')
    for target in ('@bad', '@malformed'):
        message = FakeMsg('/info ' + target, uid=5)
        run(tools.info_cmd(Client(), message))
        assert 'could not be resolved' in message.replies[-1]
        assert 'secret-token' not in message.replies[-1]


def test_user_info_works_for_replies_and_group_without_widget(monkeypatch):
    calls, docs = [], []
    class Client(FakeClient):
        async def get_users(self, target):
            calls.append(target)
            return SimpleNamespace(id=target, first_name='Test', is_bot=False)
    async def reply(message, doc): docs.append(doc.classic())
    monkeypatch.setattr(tools.rich, 'reply', reply)
    message = FakeMsg('/info', uid=5, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
    message.reply_to_message = FakeMsg('/hello', uid=45, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
    run(tools.info_cmd(Client(), message))
    assert calls == [45] and 'tg://user?id=45' in docs[0]


def test_missing_actor_and_target_does_not_resolve():
    class Client(FakeClient):
        async def get_users(self, target): pytest.fail('No user should be resolved')
    message = FakeMsg('/info', uid=0, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
    run(tools.info_cmd(Client(), message))
    assert 'Reply to a user' in message.replies[-1]


def test_wzgram_nested_verification_flags_used_by_text_and_widget():
    from pyrogram import types
    from plugins.widget.details import user_payload
    user = types.User(id=77, first_name='Example', is_bot=False,
        verification_status=types.VerificationStatus(is_verified=True, is_scam=False, is_fake=None))
    rows = dict(tools._user_info_doc(user).blocks[0][1])
    assert (rows['Verified'], rows['Scam'], rows['Fake']) == ('Yes', 'No', 'Unavailable')
    widget = {r['label']: r['value'] for section in user_payload(user)['sections'] for r in section['rows']}
    assert (widget['Verified'], widget['Scam'], widget['Fake']) == ('Yes', 'No', 'Unavailable')


def test_verification_flag_supports_legacy_and_prefers_explicit_nested_boolean():
    from core.telegram_profile import profile_flag
    user = SimpleNamespace(is_verified=True, is_scam=False, is_fake='false',
                           verification_status=SimpleNamespace(is_verified=False, is_scam=None))
    assert profile_flag(user, 'is_verified') is False
    assert profile_flag(user, 'is_scam') is False
    assert profile_flag(user, 'is_fake') is None

"""Independent Rose-inspired clone moderation, exercised without Telegram."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pyrogram import Client, enums
from pyrogram.types import ChatPermissions

from tests.harness import FakeMsg, run
from tests.test_audit import _dispatch
from filestore.worker_bot.group_profile import setup_group_profile, _duration


def admin(**permissions):
    values = dict(can_restrict_members=True, can_delete_messages=True, can_pin_messages=True)
    values.update(permissions)
    return SimpleNamespace(status=enums.ChatMemberStatus.ADMINISTRATOR, privileges=SimpleNamespace(**values))


def status(value):
    return SimpleNamespace(status=value)


class RecordingClient:
    def __init__(self):
        self.me = SimpleNamespace(id=4242, username='CloneBot')
        self.members = {5: admin(), 4242: admin(), 9: status(enums.ChatMemberStatus.MEMBER)}
        self.calls = []
        self.fail = set()
        self.permissions = ChatPermissions(can_send_messages=True, can_send_photos=False)

    async def get_chat_member(self, chat_id, user_id):
        self.calls.append(('member', chat_id, user_id))
        value = self.members[user_id]
        if isinstance(value, Exception):
            raise value
        return value

    async def get_users(self, name):
        self.calls.append(('resolve', name))
        if 'resolve' in self.fail:
            raise RuntimeError('secret')
        return SimpleNamespace(id=9)

    async def get_chat(self, chat_id):
        self.calls.append(('chat', chat_id))
        return SimpleNamespace(permissions=self.permissions)

    async def send_message(self, chat_id, text):
        self.calls.append(('send', chat_id, text))
        if 'send' in self.fail:
            raise RuntimeError('secret')

    async def ban_chat_member(self, chat_id, user_id, **kwargs):
        self.calls.append(('ban', chat_id, user_id, kwargs))
        if 'ban' in self.fail:
            raise RuntimeError('secret')

    async def unban_chat_member(self, chat_id, user_id):
        self.calls.append(('unban', chat_id, user_id))
        if 'unban' in self.fail:
            raise RuntimeError('secret')

    async def restrict_chat_member(self, chat_id, user_id, permissions, **kwargs):
        self.calls.append(('restrict', chat_id, user_id, permissions, kwargs))
        if 'restrict' in self.fail:
            raise RuntimeError('secret')

    async def pin_chat_message(self, chat_id, message_id, **kwargs):
        self.calls.append(('pin', chat_id, message_id, kwargs))
        if 'pin' in self.fail:
            raise RuntimeError('secret')

    async def unpin_chat_message(self, chat_id, message_id):
        self.calls.append(('unpin', chat_id, message_id))
        if 'unpin' in self.fail:
            raise RuntimeError('secret')


def group(text, uid=5):
    return FakeMsg(text, uid=uid, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)


def reply(uid=9):
    return group('message', uid=uid)


@pytest.fixture
def application():
    # Registration uses the real WzGram filters, but no client is connected.
    async def go():
        app = Client('test_group_profile', api_id=1, api_hash='test', bot_token='4242:test', in_memory=True)
        setup_group_profile(app)
        await asyncio.sleep(.05)
        return app
    return run(go())


@pytest.mark.parametrize('command', ['/ban 9', '.ban 9', '/ban@CloneBot 9', '.ban@CloneBot 9', '/gban 9'])
def test_ban_dispatch_accepts_slash_dot_and_addressed_clone(application, command):
    async def go():
        c = RecordingClient(); msg = group(command)
        await _dispatch(application, c, msg)
        assert ('ban', -100, 9, {}) in c.calls
        assert 'Banned' in msg.replies[-1]
    run(go())


def test_addressed_to_other_bot_does_not_run(application):
    async def go():
        c=RecordingClient();msg=group('/ban@OtherBot 9')
        await _dispatch(application,c,msg)
        assert not c.calls and not msg.replies
    run(go())


def test_username_and_reply_reasons_are_escaped(application):
    async def go():
        c=RecordingClient();msg=group('/ban @someone <b>spam</b>')
        await _dispatch(application,c,msg)
        assert ('resolve','@someone') in c.calls and ('ban',-100,9,{}) in c.calls
        assert '&lt;b&gt;spam&lt;/b&gt;' in msg.replies[-1]
        c=RecordingClient();msg=group('.ban <script>');msg.reply_to_message=reply()
        await _dispatch(application,c,msg)
        assert ('ban',-100,9,{}) in c.calls and '&lt;script&gt;' in msg.replies[-1]
    run(go())


@pytest.mark.parametrize('action', ['ban','tban','dban','sban','mute','tmute','dmute','smute','kick','dkick','skick','unban','unmute'])
def test_non_admin_cannot_perform_or_resolve_actions(application, action):
    async def go():
        c=RecordingClient();c.members[5]=status(enums.ChatMemberStatus.MEMBER)
        text=f'/{action} @someone' + (' 3d' if action.startswith('t') else '')
        msg=group(text)
        if action.startswith('d'):msg.reply_to_message=reply()
        await _dispatch(application,c,msg)
        assert not any(call[0] in ('resolve','ban','unban','restrict') for call in c.calls)
        assert msg.replies and not msg.deleted
    run(go())


@pytest.mark.parametrize('variant', ['dban','sban','dmute','smute','dkick','skick'])
def test_delete_variants_require_both_delete_rights(application,variant):
    async def go():
        for uid in (5,4242):
            c=RecordingClient();c.members[uid]=admin(can_delete_messages=False)
            msg=group(f'/{variant}');msg.reply_to_message=reply()
            await _dispatch(application,c,msg)
            assert 'delete-messages' in msg.replies[-1]
            assert not any(x[0] in ('ban','restrict','unban') for x in c.calls)
            assert not msg.deleted and not msg.reply_to_message.deleted
    run(go())


@pytest.mark.parametrize('variant,api', [('sban','ban'),('smute','restrict'),('skick','ban')])
def test_silent_variants_delete_reply_and_command_after_confirmed_action(application, variant, api):
    async def go():
        c=RecordingClient();msg=group(f'.{variant} reason');msg.reply_to_message=reply()
        await _dispatch(application,c,msg)
        assert any(x[0]==api for x in c.calls)
        assert msg.deleted and msg.reply_to_message.deleted and not msg.replies
    run(go())


def test_sban_by_id_deletes_only_command_and_no_success_reply(application):
    async def go():
        c=RecordingClient();msg=group('/sban 9')
        await _dispatch(application,c,msg)
        assert msg.deleted and not msg.replies and ('ban',-100,9,{}) in c.calls
    run(go())


def test_dban_requires_reply_and_does_not_delete_command(application):
    async def go():
        c=RecordingClient();msg=group('/dban 9')
        await _dispatch(application,c,msg);assert msg.replies and not c.calls
        c=RecordingClient();msg=group('/dban spam');msg.reply_to_message=reply()
        await _dispatch(application,c,msg)
        assert msg.reply_to_message.deleted and not msg.deleted and 'Banned' in msg.replies[-1]
    run(go())


def test_silent_action_uncertainty_never_deletes_and_no_secret_leak(application,caplog):
    async def go():
        c=RecordingClient();c.fail.add('ban');msg=group('/sban');msg.reply_to_message=reply()
        await _dispatch(application,c,msg)
        assert not msg.deleted and not msg.reply_to_message.deleted
        assert 'did not confirm' in msg.replies[-1] and 'secret' not in msg.replies[-1]+caplog.text
        assert len([x for x in c.calls if x[0]=='ban'])==1
    run(go())


def test_failed_delete_after_confirmed_ban_reports_partial_outcome(application):
    async def go():
        c=RecordingClient();msg=group('/sban');target=reply();msg.reply_to_message=target
        async def bad_delete():raise RuntimeError('secret')
        target.delete=bad_delete
        await _dispatch(application,c,msg)
        assert msg.deleted and not msg.replies
        assert any(x[0]=='send' and 'deletions' in x[2] for x in c.calls)
        assert len([x for x in c.calls if x[0]=='ban'])==1
    run(go())


@pytest.mark.parametrize('command', ['/tban 9 5m reason','/tmute 9 2h reason','/tban 9 365d'])
def test_timed_actions_apply_bounded_datetime(application,command):
    async def go():
        c=RecordingClient();msg=group(command);start=datetime.now(timezone.utc)
        await _dispatch(application,c,msg)
        action=next(x for x in c.calls if x[0] in ('ban','restrict'))
        until=action[-1]['until_date'];assert until.tzinfo is not None and until>start
        assert 'Duration:' in msg.replies[-1]
    run(go())


@pytest.mark.parametrize('duration', ['1m','29s','0h','366d','53w','9999999w','-1d','1D2h','０１m'])
def test_unsafe_durations_are_rejected_before_action(application,duration):
    async def go():
        c=RecordingClient();msg=group('/tban 9 '+duration)
        await _dispatch(application,c,msg)
        assert not c.calls and msg.replies
    run(go())


def test_duration_boundaries():
    assert _duration('5m')==300 and _duration('365D')==365*86400
    assert _duration('1m') is None and _duration('366d') is None


def test_unmute_uses_verified_group_defaults_and_unknown_defaults_fail_closed(application):
    async def go():
        c=RecordingClient();c.members[9]=status(enums.ChatMemberStatus.RESTRICTED)
        msg=group('/unmute 9');await _dispatch(application,c,msg)
        call=next(x for x in c.calls if x[0]=='restrict')
        assert call[3] is c.permissions and call[3].can_send_photos is False
        assert 'group defaults' in msg.replies[-1]
        c=RecordingClient();c.members[9]=status(enums.ChatMemberStatus.RESTRICTED);c.permissions=None
        msg=group('/unmute 9');await _dispatch(application,c,msg)
        assert not any(x[0]=='restrict' for x in c.calls) and 'No action' in msg.replies[-1]
    run(go())


def test_kick_partial_failure_warns_member_may_remain_banned(application):
    async def go():
        c=RecordingClient();c.fail.add('unban');msg=group('/skick');msg.reply_to_message=reply()
        await _dispatch(application,c,msg)
        assert [x[0] for x in c.calls if x[0] in ('ban','unban')]==['ban','unban']
        assert 'may remain banned' in msg.replies[-1] and not msg.deleted and not msg.reply_to_message.deleted
    run(go())


@pytest.mark.parametrize('target_status', [enums.ChatMemberStatus.ADMINISTRATOR,enums.ChatMemberStatus.OWNER,enums.ChatMemberStatus.BANNED])
def test_ban_protects_admins_owners_and_unexpected_status(application,target_status):
    async def go():
        c=RecordingClient();c.members[9]=status(target_status);msg=group('/ban 9')
        await _dispatch(application,c,msg)
        assert not any(x[0]=='ban' for x in c.calls) and 'eligible' in msg.replies[-1]
    run(go())


def test_basic_group_does_not_claim_to_ban_or_mute(application):
    async def go():
        for action in ('ban','tban','mute','kick','unban'):
            c=RecordingClient();msg=group(f'/{action} 9'+(' 5m' if action=='tban' else ''));msg.chat.type=enums.ChatType.GROUP
            await _dispatch(application,c,msg)
            assert not any(x[0] in ('ban','unban','restrict') for x in c.calls)
            assert 'supergroup' in msg.replies[-1]
    run(go())


def test_pin_and_delete_require_permissions_and_report_uncertainty(application):
    async def go():
        c=RecordingClient();msg=group('/pin loud');msg.reply_to_message=reply()
        await _dispatch(application,c,msg)
        assert ('pin',-100,msg.reply_to_message.id,{'disable_notification':False}) in c.calls
        c=RecordingClient();msg=group('.pin');msg.reply_to_message=reply()
        await _dispatch(application,c,msg)
        assert ('pin',-100,msg.reply_to_message.id,{'disable_notification':True}) in c.calls
        c=RecordingClient();c.members[5]=admin(can_pin_messages=False);msg=group('/unpin')
        await _dispatch(application,c,msg);assert not any(x[0]=='unpin' for x in c.calls)
        c=RecordingClient();msg=group('/del');msg.reply_to_message=reply()
        await _dispatch(application,c,msg);assert msg.deleted and msg.reply_to_message.deleted and not msg.replies
        c=RecordingClient();c.fail.add('pin');msg=group('/pin');msg.reply_to_message=reply()
        await _dispatch(application,c,msg);assert 'did not confirm' in msg.replies[-1] and 'secret' not in msg.replies[-1]
    run(go())


def test_unban_only_banned_targets_and_alias(application):
    async def go():
        c=RecordingClient();c.members[9]=status(enums.ChatMemberStatus.BANNED)
        msg=group('.unban 9');await _dispatch(application,c,msg)
        assert ('unban',-100,9) in c.calls and 'Unbanned' in msg.replies[-1]
        c=RecordingClient();msg=group('/unban 9');await _dispatch(application,c,msg)
        assert not any(x[0]=='unban' for x in c.calls)
    run(go())


def test_other_chat_reply_and_invalid_target_fail_closed(application):
    async def go():
        for text in ('/ban', '/sban', '/del', '/pin'):
            c=RecordingClient();msg=group(text);msg.reply_to_message=reply();msg.reply_to_message.chat.id=-999
            await _dispatch(application,c,msg)
            assert not c.calls and not msg.deleted and not msg.reply_to_message.deleted
        for text in ('/ban 0', '/ban -2', '/ban 9223372036854775808', '/ban @bad!', '/ban 4242'):
            c=RecordingClient();msg=group(text);await _dispatch(application,c,msg)
            assert not any(x[0]=='ban' for x in c.calls)
    run(go())


def test_silent_command_delete_failure_reports_without_replying_to_deleted_message(application):
    async def go():
        c=RecordingClient();msg=group('/sban 9')
        async def bad_delete():raise RuntimeError('secret')
        msg.delete=bad_delete
        await _dispatch(application,c,msg)
        assert any(x[0]=='send' and 'deletions' in x[2] and 'secret' not in x[2] for x in c.calls)
        assert not msg.replies
    run(go())


def test_false_delete_result_is_not_claimed_as_success(application):
    async def go():
        c=RecordingClient();msg=group('/sban');msg.reply_to_message=reply()
        async def no_delete():return False
        msg.reply_to_message.delete=no_delete
        await _dispatch(application,c,msg)
        assert msg.deleted and any(x[0]=='send' and 'deletions' in x[2] for x in c.calls)
        c=RecordingClient();msg=group('/del');msg.reply_to_message=reply();msg.reply_to_message.delete=no_delete
        await _dispatch(application,c,msg)
        assert not msg.deleted and 'did not confirm' in msg.replies[-1]
    run(go())


def test_false_action_result_fails_closed_without_deletion(application):
    async def go():
        c=RecordingClient();msg=group('/sban');msg.reply_to_message=reply()
        async def unconfirmed(*args,**kwargs):return False
        c.ban_chat_member=unconfirmed
        await _dispatch(application,c,msg)
        assert not msg.deleted and not msg.reply_to_message.deleted and 'did not confirm' in msg.replies[-1]
    run(go())

"""Offline tests for user, group and owner system snapshot widgets."""
from datetime import timedelta
from types import SimpleNamespace
import json
import zlib

import pytest
from tests.harness import FakeClient, FakeMsg, run
from pyrogram import enums

import config
from core import tools
from plugins.widget import details


def fake_user():
    return SimpleNamespace(id=501, first_name='Alice </script><script>oops()', last_name='Example',
        username='alice', mention='Alice', status=SimpleNamespace(name='RECENTLY'),
        dc_id=4, is_bot=False, is_premium=True, is_verified=False, is_scam=None,
        is_fake=False, is_restricted=None, phone_number='PRIVATE-PHONE-NOT-EXPORTED')


def test_user_widget_fields_bounded_escaped_and_no_private_fields():
    payload = details.user_payload(fake_user())
    html = details.detail_html(payload)
    assert 'PRIVATE-PHONE-NOT-EXPORTED' not in html
    assert '</script><script>oops' not in html and '\\u003c/script' in html
    assert len(html.encode()) <= 32768 and len(zlib.compress(html.encode(), 9)) <= 4096
    assert 'TelegramWidget.request' not in html and 'eval(' not in html
    rows = {r['label']: r['value'] for section in payload['sections'] for r in section['rows']}
    assert rows['User ID'] == '501' and rows['Premium'] == 'Yes'
    assert rows['Scam'] == 'Unavailable' and rows['Last seen'] == 'Recently'
    assert rows['Data center'] == '4'


def test_system_widget_uses_actual_inputs_and_bounds():
    mem = SimpleNamespace(percent=37.5, used=1048576*40, total=1048576*100)
    disk = SimpleNamespace(percent=68.0, used=1048576*68, total=1048576*100)
    payload = details.system_payload(12.4, mem, disk, timedelta(hours=2), 7)
    rows = {r['label']: r for s in payload['sections'] for r in s['rows']}
    assert rows['CPU']['percent'] == 12.4 and rows['RAM']['percent'] == 37.5
    assert rows['RAM used']['value'] == '40 MiB' and rows['Active clone workers']['value'] == '7'
    with pytest.raises(ValueError): details.system_payload(float('nan'), mem, disk, timedelta(), 0)
    assert len(zlib.compress(details.detail_html(payload).encode(), 9)) <= 4096


def test_info_existing_user_resolution_and_private_only_widget(monkeypatch):
    user = fake_user(); calls = []; docs = []
    class Client(FakeClient):
        async def get_users(self, target): calls.append(('get_users', target)); return user
    async def rich_reply(msg, doc): docs.append(doc.classic())
    async def send(client, message, data): calls.append(('widget', data)); return True
    monkeypatch.setattr(tools.rich, 'reply', rich_reply); monkeypatch.setattr(details, 'send_detail', send)
    client = Client(); msg = FakeMsg('/info @alice', uid=5)
    run(tools.info_cmd(client, msg))
    assert calls[0] == ('get_users', '@alice') and calls[1][0] == 'widget'
    assert calls[1][1]['sections'][0]['rows'][0]['value'] == '501' and docs
    calls.clear(); group = FakeMsg('/info @alice', uid=5, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
    run(tools.info_cmd(client, group))
    assert calls == [('get_users', '@alice')]


class GroupClient(FakeClient):
    def __init__(self, statuses=None):
        super().__init__(); self.statuses = statuses or {}; self.group_calls = []
    async def get_chat(self, target):
        self.group_calls.append(('chat', target))
        return SimpleNamespace(id=-10055, type=enums.ChatType.SUPERGROUP, title='Example group',
            username='example', description='A group description', is_forum=True,
            has_protected_content=False, is_verified=None)
    async def get_chat_member(self, chat, uid):
        self.group_calls.append(('member', chat, uid))
        value = self.statuses.get(uid, enums.ChatMemberStatus.LEFT)
        if isinstance(value, Exception): raise value
        return SimpleNamespace(status=value, is_member=value != enums.ChatMemberStatus.LEFT)
    async def get_chat_members_count(self, chat): self.group_calls.append(('count', chat)); return 42


def test_group_info_requires_requester_and_bot_membership_and_returns_group_data(monkeypatch):
    doc = []; sent = []
    async def reply(msg, payload): doc.append(payload.classic())
    async def widget(client, msg, payload): sent.append(payload)
    monkeypatch.setattr(tools.rich, 'reply', reply); monkeypatch.setattr(details, 'send_detail', widget)
    client = GroupClient({5: enums.ChatMemberStatus.MEMBER, 123456: enums.ChatMemberStatus.ADMINISTRATOR})
    msg = FakeMsg('/info group @example', uid=5)
    run(tools.info_cmd(client, msg))
    assert client.group_calls == [('chat', '@example'), ('member', -10055, 5),
                                  ('member', -10055, 123456), ('count', -10055)]
    assert sent and sent[0]['title'] == 'Example group' and doc
    rows = {r['label']: r['value'] for s in sent[0]['sections'] for r in s['rows']}
    assert rows['Group ID'] == '-10055' and rows['Members'] == '42' and rows['Your role'] == 'Member'
    assert rows['Description'] == 'A group description' and rows['Forum'] == 'Yes'


@pytest.mark.parametrize('statuses', [
    {}, {5: enums.ChatMemberStatus.LEFT, 123456: enums.ChatMemberStatus.MEMBER},
    {5: enums.ChatMemberStatus.MEMBER, 123456: enums.ChatMemberStatus.LEFT},
    {5: RuntimeError('secret'), 123456: enums.ChatMemberStatus.MEMBER},
])
def test_group_info_fails_closed_without_membership_or_on_error(monkeypatch, statuses):
    client = GroupClient(statuses)
    async def no_widget(*args): pytest.fail('not authorized to send')
    monkeypatch.setattr(details, 'send_detail', no_widget)
    msg = FakeMsg('/info group -10055', uid=5)
    run(tools.info_cmd(client, msg))
    assert 'membership could not be verified' in msg.replies[-1]
    assert 'secret' not in msg.replies[-1] and all(c[0] != 'count' for c in client.group_calls)


def test_group_request_in_group_never_resolves_or_dms():
    client = GroupClient()
    msg = FakeMsg('/info group -10055', uid=5, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)
    run(tools.info_cmd(client, msg))
    assert not client.group_calls and 'private chat' in msg.replies[-1]


def test_unknown_and_restricted_memberships_fail_closed():
    assert not tools._verified_member(SimpleNamespace(status=None))
    assert not tools._verified_member(SimpleNamespace(status=enums.ChatMemberStatus.RESTRICTED, is_member=False))
    assert tools._verified_member(SimpleNamespace(status=enums.ChatMemberStatus.RESTRICTED, is_member=True))


def test_send_requires_private_identity_and_owner_for_sys(monkeypatch):
    from plugins.widget import detail_sender
    monkeypatch.setattr(config, 'OWNERS', [111]); monkeypatch.setattr(details, 'plugin_enabled', lambda _: True)
    monkeypatch.setattr(details.botapi, 'enabled', lambda: True)
    monkeypatch.setattr(details.botapi, 'bot', lambda: SimpleNamespace(id=123456, token='secret-token'))
    monkeypatch.setenv('WIDGET_API_BETA', 'true'); monkeypatch.setenv('WIDGET_API_MODE', 'beta')
    calls = []
    async def send(*args): calls.append(args)
    monkeypatch.setattr(detail_sender, 'send', send)
    payload = details.user_payload(fake_user()); client = FakeClient()
    assert not run(details.send_detail(client, FakeMsg('/sys', uid=5), payload, owner_only=True))
    assert not run(details.send_detail(client, FakeMsg('/info', uid=5, chat_type=enums.ChatType.SUPERGROUP), payload))
    assert run(details.send_detail(client, FakeMsg('/sys', uid=111), payload, owner_only=True))
    assert len(calls) == 1 and calls[0][1] == 111 and calls[0][3] == 'beta'
    method = detail_sender.SendDetailWidget(chat_id=111, html=calls[0][2], caption=calls[0][4]).model_dump(exclude_none=True)
    assert method['is_shared'] is False and method['allow_queries'] is False
    assert 'public_data' not in method and 'chat_data' not in method
    monkeypatch.setattr(details.botapi, 'bot', lambda: SimpleNamespace(id=999, token='secret-token'))
    assert not run(details.send_detail(client, FakeMsg('/info', uid=5), payload)) and len(calls) == 1


def test_failed_widget_send_is_not_retried_or_logged_with_secrets(monkeypatch, caplog):
    from plugins.widget import detail_sender
    monkeypatch.setattr(details, 'plugin_enabled', lambda _: True)
    monkeypatch.setattr(details.botapi, 'enabled', lambda: True)
    monkeypatch.setattr(details.botapi, 'bot', lambda: SimpleNamespace(id=123456, token='secret-token'))
    monkeypatch.setenv('WIDGET_API_BETA', 'true'); monkeypatch.setenv('WIDGET_API_MODE', 'beta')
    calls = []
    async def fail(*args): calls.append(1); raise TimeoutError('secret-token')
    monkeypatch.setattr(detail_sender, 'send', fail)
    msg = FakeMsg('/info', uid=5)
    assert not run(details.send_detail(FakeClient(), msg, details.user_payload(fake_user())))
    assert calls == [1] and 'check this chat before retrying' in msg.replies[-1].lower()
    assert 'secret-token' not in caplog.text and 'secret-token' not in msg.replies[-1]


def test_system_handler_keeps_reply_and_adds_owner_snapshot(monkeypatch):
    from filestore.main_bot.plugins import owner_cmds
    from filestore.worker_bot import engine
    mem = SimpleNamespace(percent=37.5, used=1048576 * 40, total=1048576 * 100)
    disk = SimpleNamespace(percent=68.0, used=1048576 * 68, total=1048576 * 100)
    monkeypatch.setattr(owner_cmds.psutil, 'cpu_percent', lambda interval: 12.4)
    monkeypatch.setattr(owner_cmds.psutil, 'virtual_memory', lambda: mem)
    monkeypatch.setattr(owner_cmds.psutil, 'disk_usage', lambda path: disk)
    monkeypatch.setattr(owner_cmds.psutil, 'boot_time', lambda: 1_600_000_000)
    monkeypatch.setattr(engine, 'worker_engine', SimpleNamespace(active_count=7))
    output = []; widgets = []
    async def reply(msg, doc): output.append(doc.classic())
    async def widget(client, msg, payload, *, owner_only=False): widgets.append((payload, owner_only))
    monkeypatch.setattr(owner_cmds.rich, 'reply', reply); monkeypatch.setattr(details, 'send_detail', widget)
    run(owner_cmds.system_stats(FakeClient(), FakeMsg('/sys', uid=111)))
    assert output and 'CPU' in output[0] and len(widgets) == 1 and widgets[0][1] is True
    rows = {r['label']: r for s in widgets[0][0]['sections'] for r in s['rows']}
    assert rows['CPU']['value'] == '12.4%' and rows['RAM']['value'] == '37.5%'
    assert rows['Active clone workers']['value'] == '7'


def test_info_group_widget_text_sanitizes_user_controlled_content():
    chat = SimpleNamespace(id=-100, title='</script><script>alert(1)', type=enums.ChatType.GROUP,
        username=None, description='<img src=x>', is_forum=None)
    html = details.detail_html(details.group_payload(chat, 'Member', None))
    assert '</script><script>alert' not in html and '<img src=x>' not in html
    assert 'Unavailable' in html


def test_sys_command_filter_is_owner_only_and_private():
    from filestore.main_bot.plugins import owner_cmds
    filt = owner_cmds.system_stats.handlers[0][0].filters
    owners = filt.other; original = set(owners); owners.clear(); owners.add(111)
    try:
        client = FakeClient()
        assert run(filt(client, FakeMsg('/sys', uid=111)))
        assert not run(filt(client, FakeMsg('/sys', uid=5)))
        assert not run(filt(client, FakeMsg('/sys', uid=111, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)))
    finally:
        owners.clear(); owners.update(original)


def test_detail_sender_serialization_modes_and_session_cleanup(monkeypatch):
    from plugins.widget import detail_sender as sender
    calls = []; sessions = []
    class Outgoing:
        token = '123:token'
        async def __call__(self, method): calls.append(('configured', method)); return 'accepted'
    class Session:
        def __init__(self, **kwargs): self.kwargs, self.closed = kwargs, False
        async def close(self): self.closed = True
    def new_session(**kwargs):
        session = Session(**kwargs); sessions.append(session); return session
    class BetaBot:
        def __init__(self, token, session): assert token == '123:token'; self.session = session
        async def __call__(self, method): calls.append(('beta', method)); return 'accepted'
    monkeypatch.setattr(sender, 'AiohttpSession', new_session); monkeypatch.setattr(sender, 'Bot', BetaBot)
    assert run(sender.send(Outgoing(), 5, 'html', 'configured', 'caption')) == 'accepted'
    assert run(sender.send(Outgoing(), 5, 'html', 'beta', 'caption')) == 'accepted'
    assert [name for name, _ in calls] == ['configured', 'beta']
    assert calls[0][1].allow_queries is False and calls[1][1].is_shared is False
    assert sessions[0].kwargs['api'].base.startswith(sender.BETA_BASE) and sessions[0].closed
    with pytest.raises(ValueError): run(sender.send(Outgoing(), 5, 'html', 'wrong', 'caption'))


def test_long_values_are_clipped_and_marked_as_previews():
    chat = SimpleNamespace(id=-100, title='T' * 100, type=enums.ChatType.GROUP,
        username='u' * 32, description='D' * 200)
    payload = details.group_payload(chat, 'Member', 123)
    rows = {r['label']: r['value'] for s in payload['sections'] for r in s['rows']}
    assert len(payload['title']) == 28 and payload['title'].endswith('…')
    assert len(rows['Description']) == 48 and rows['Description'].endswith('…')


def test_group_info_rejects_channels_and_missing_bot_identity():
    class ChannelClient(GroupClient):
        async def get_chat(self, target):
            return SimpleNamespace(id=-100, type=enums.ChatType.CHANNEL, title='channel')
    for client in (ChannelClient(), GroupClient()):
        client.me = None
        msg = FakeMsg('/info group -100', uid=5)
        run(tools.info_cmd(client, msg))
        assert 'membership could not be verified' in msg.replies[-1]
        assert all(call[0] != 'count' for call in client.group_calls)


def test_group_regular_reply_escapes_and_shows_more_description(monkeypatch):
    class LongGroup(GroupClient):
        async def get_chat(self, target):
            return SimpleNamespace(id=-10055, type=enums.ChatType.SUPERGROUP, title='Group <name>',
                username=None, description='<hello>' + 'x' * 100, is_forum=True)
    async def unavailable_count(chat): raise RuntimeError('not available')
    client = LongGroup({5: enums.ChatMemberStatus.MEMBER, 123456: enums.ChatMemberStatus.MEMBER})
    client.get_chat_members_count = unavailable_count
    replies = []; widgets = []
    async def reply(msg, doc): replies.append(doc.classic())
    async def send(client, msg, payload): widgets.append(payload)
    monkeypatch.setattr(tools.rich, 'reply', reply); monkeypatch.setattr(details, 'send_detail', send)
    run(tools.info_cmd(client, FakeMsg('/info group -10055', uid=5)))
    assert 'Group &lt;name&gt;' in replies[0] and '&lt;hello&gt;' in replies[0]
    assert 'x' * 80 in replies[0]
    rows = {r['label']: r['value'] for s in widgets[0]['sections'] for r in s['rows']}
    assert rows['Members'] == 'Unavailable' and len(rows['Description']) == 48


def test_template_markers_inside_untrusted_profile_stay_data():
    user = fake_user(); user.first_name = '/*JS*/'; user.last_name = '/*CSS*/'
    html = details.detail_html(details.user_payload(user))
    assert '/*JS*/ /*CSS*/' in html
    assert html.count("(()=>{'use strict'") == 1
    assert html.index('/*JS*/ /*CSS*/') < html.index("(()=>{'use strict'")


def test_private_chat_identity_mismatch_never_resolves_or_sends(monkeypatch):
    client = GroupClient({5: enums.ChatMemberStatus.MEMBER, 123456: enums.ChatMemberStatus.MEMBER})
    msg = FakeMsg('/info group -10055', uid=5, chat_id=999)
    run(tools.info_cmd(client, msg))
    assert not client.group_calls and not msg.replies
    monkeypatch.setattr(details, 'plugin_enabled', lambda _: pytest.fail('identity check must run first'))
    assert not run(details.send_detail(client, msg, details.user_payload(fake_user())))

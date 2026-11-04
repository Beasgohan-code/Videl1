"""Offline tests for the real group diagnostics handlers and live guard parity."""
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import FakeMsg, load_all, no_botapi, reset_db, run
from tests.test_group_mod_and_security import GroupFakeClient, _full_privs, _grp_msg

load_all()


@pytest.fixture(autouse=True)
def setup():
    from core.plugin_manager import plugin_manager
    from plugins.group_mod import plugin as gmod
    run(reset_db())
    no_botapi()
    gmod._CFG_CACHE.clear()
    gmod._FLOOD_BUCKETS.clear()
    plugin_manager._enabled.clear()
    plugin_manager.discover()
    yield
    no_botapi()
    plugin_manager._enabled.clear()


def owner_client(bot_priv=None):
    if bot_priv is None:
        bot_priv = _full_privs()
    return GroupFakeClient({10: (enums.ChatMemberStatus.OWNER, None),
                            999: (enums.ChatMemberStatus.ADMINISTRATOR, bot_priv)})


def test_plugin_discovery_menu_and_anonymous_admin_gate():
    from core.commands import GROUP_COMMANDS, _cmds
    from core.middleware import senderless_gate
    from core.plugin_manager import plugin_manager
    manifests = plugin_manager.discover()
    assert {x['name'] for x in manifests['group_insights'].commands} == {'policysim', 'ghealth'}
    assert {'policysim', 'ghealth'} <= {x.command for x in _cmds(GROUP_COMMANDS)}
    assert len(_cmds(GROUP_COMMANDS)) <= 100
    c = owner_client()
    msg = _grp_msg('/ghealth', uid=None, client=c)
    msg.sender_chat = SimpleNamespace(id=msg.chat.id)
    run(senderless_gate(c, msg))
    from plugins.group_insights.plugin import ghealth_cmd
    run(ghealth_cmd(c, msg))
    assert msg.replies


def test_simulation_matches_live_lock_and_is_read_only():
    from core.db import vdb
    from plugins.group_mod import plugin as gmod
    from plugins.group_insights.plugin import policysim_cmd
    c = owner_client()
    run(gmod.update_group_cfg(-100888, {'locks': ['links'], 'filters': {'sale': 'secret response'}}))
    before = run(vdb.db['group_settings'].find_one({'_id': -100888}))
    command = _grp_msg('/policysim sale https://example.com/<script>', uid=10, client=c)
    run(policysim_cmd(c, command))
    result = command.replies[-1].lower()
    assert 'lock matched: links' in result
    assert 'keyword filter matched' not in result
    assert '<script>' not in result and 'example.com' not in result
    assert 'flood' in result and 'hypothetical non-admin' in result
    assert run(vdb.db['group_settings'].find_one({'_id': -100888})) == before
    assert not c.called('delete_messages') and not c.called('restrict_chat_member')
    sample = _grp_msg('sale https://example.com/<script>', uid=77, client=c)
    run(gmod.group_passive_guard(c, sample))
    assert sample.deleted and not sample.replies


def test_simulation_reply_media_forward_bots_and_keyword_precedence():
    from plugins.group_mod import plugin as gmod
    from plugins.group_insights.plugin import policysim_cmd
    c = owner_client()
    for kind, attr in [('media', 'photo'), ('forwards', 'forward_origin'), ('bots', 'via_bot')]:
        run(gmod.update_group_cfg(-100888, {'locks': [kind]}))
        cmd = _grp_msg('/policysim', uid=10, client=c, reply_uid=77)
        setattr(cmd.reply_to_message, attr, object())
        run(policysim_cmd(c, cmd))
        assert f'lock matched: {kind}' in cmd.replies[-1].lower()
        run(gmod.group_passive_guard(c, cmd.reply_to_message))
        assert cmd.reply_to_message.deleted
    run(gmod.update_group_cfg(-100888, {'locks': [], 'filters': {'hello': 'private configured reply'}}))
    msg = _grp_msg('/policysim hello everyone', uid=10, client=c)
    run(policysim_cmd(c, msg))
    assert 'keyword filter matched' in msg.replies[-1].lower()
    assert 'private configured reply' not in msg.replies[-1]
    live = _grp_msg('hello everyone', uid=77, client=c)
    run(gmod.group_passive_guard(c, live))
    assert 'private configured reply' in live.replies[-1]
    command_sample = _grp_msg('/policysim /hello', uid=10, client=c)
    run(policysim_cmd(c, command_sample))
    assert 'no supported content lock' in command_sample.replies[-1].lower()


def test_simulation_is_isolated_and_denies_nonadmins_and_disabled_group_mod():
    from core.plugin_manager import plugin_manager
    from plugins.group_mod import plugin as gmod
    from plugins.group_insights.plugin import policysim_cmd
    c = owner_client()
    run(gmod.update_group_cfg(-100888, {'locks': ['all']}))
    msg = _grp_msg('/policysim hello', uid=10, chat_id=-100999, client=c)
    run(policysim_cmd(c, msg))
    assert 'no supported content lock' in msg.replies[-1].lower()
    denied = _grp_msg('/policysim hello', uid=20, client=c)
    run(policysim_cmd(c, denied))
    assert 'permission check failed' in denied.replies[-1].lower()
    plugin_manager._enabled['group_mod'] = False
    disabled = _grp_msg('/policysim hello', uid=10, client=c)
    run(policysim_cmd(c, disabled))
    assert 'enable group_mod' in disabled.replies[-1].lower()


def test_health_rights_partial_unknown_and_network_failure():
    from plugins.group_insights.plugin import ghealth_cmd
    c = owner_client(_full_privs(can_delete_messages=False, can_invite_users=True))
    msg = _grp_msg('/ghealth', uid=10, client=c)
    run(ghealth_cmd(c, msg))
    assert 'Not granted' in msg.replies[-1] and 'Granted' in msg.replies[-1]
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, None)
    msg = _grp_msg('/ghealth', uid=10, client=c)
    run(ghealth_cmd(c, msg))
    assert msg.replies[-1].count('Unknown') == 6
    class Broken(GroupFakeClient):
        async def get_chat_member(self, chat_id, user_id):
            if user_id == 999:
                raise RuntimeError('sensitive connection error')
            return await super().get_chat_member(chat_id, user_id)
    broken = Broken({10: (enums.ChatMemberStatus.OWNER, None)})
    msg = _grp_msg('/ghealth', uid=10, client=broken)
    run(ghealth_cmd(broken, msg))
    assert 'cannot verify' in msg.replies[-1].lower()
    assert 'sensitive' not in msg.replies[-1]
    assert not broken.called('ban_chat_member')


def test_handlers_registered_at_boot_and_plugin_filter_disables_dispatch():
    import run as bot_run
    from core.plugin_manager import plugin_manager
    from plugins.group_insights import plugin as insights
    class Registry:
        def __init__(self):
            self.handlers = []
        def add_handler(self, handler, group):
            self.handlers.append((handler, group))
    app = Registry()
    bot_run.load_plugins(app)
    functions = [getattr(handler, 'callback', None) for handler, _ in app.handlers]
    assert insights.policysim_cmd in functions
    assert insights.ghealth_cmd in functions
    assert not bot_run.boot_import_errors.get('plugins.group_insights.plugin')
    c = owner_client()
    msg = _grp_msg('/policysim hello', uid=10, client=c)
    handler = insights.policysim_cmd.handlers[0][0]
    assert run(handler.filters(c, msg))
    plugin_manager._enabled['group_insights'] = False
    assert not run(handler.filters(c, msg))


def test_draft_policy_simulation_does_not_change_current_settings():
    from plugins.group_insights.plugin import policysim_cmd
    from plugins.group_mod import plugin as gmod
    c = owner_client()
    run(gmod.update_group_cfg(-100888, {'locks': [], 'filters': {}}))
    msg = _grp_msg('/policysim --locks=links hello https://example.com', uid=10, client=c)
    run(policysim_cmd(c, msg))
    assert 'draft locks' in msg.replies[-1].lower()
    assert 'lock matched: links' in msg.replies[-1].lower()
    assert run(gmod.get_group_cfg(-100888))['locks'] == []
    msg = _grp_msg('/policysim --locks=links', uid=10, client=c, reply_uid=77)
    msg.reply_to_message.text = 'https://example.com'
    run(policysim_cmd(c, msg))
    assert 'lock matched: links' in msg.replies[-1].lower()
    invalid = _grp_msg('/policysim --locks=unknown abc', uid=10, client=c)
    run(policysim_cmd(c, invalid))
    assert 'invalid draft locks' in invalid.replies[-1].lower()
    assert run(gmod.get_group_cfg(-100888))['locks'] == []
    run(gmod.update_group_cfg(-100888, {'locks': ['all']}))
    no_locks = _grp_msg('/policysim --locks=none abc', uid=10, client=c)
    run(policysim_cmd(c, no_locks))
    assert 'no supported content lock' in no_locks.replies[-1].lower()
    assert run(gmod.get_group_cfg(-100888))['locks'] == ['all']

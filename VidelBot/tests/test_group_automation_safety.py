"""Offline checks for explicit automation consent and fail-closed execution."""
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import FakeQuery, load_all, no_botapi, reset_db, run
from tests.test_group_mod_and_security import GroupFakeClient, _full_privs, _grp_msg

load_all()


@pytest.fixture(autouse=True)
def clean():
    from core.plugin_manager import plugin_manager
    from plugins.group_mod import plugin as gmod
    run(reset_db())
    no_botapi()
    plugin_manager._enabled.clear()
    plugin_manager.discover()
    gmod._CFG_CACHE.clear()
    gmod._FLOOD_BUCKETS.clear()
    yield
    plugin_manager._enabled.clear()
    no_botapi()


def client(bot_priv=None, caller_priv=None):
    if bot_priv is None:
        bot_priv = _full_privs()
    if caller_priv is None:
        caller_priv = _full_privs()
    return GroupFakeClient({10: (enums.ChatMemberStatus.ADMINISTRATOR, caller_priv),
                            999: (enums.ChatMemberStatus.ADMINISTRATOR, bot_priv)})


def command(text, c, uid=10, chat=-100888):
    return _grp_msg(text, uid=uid, chat_id=chat, client=c)


def request(uid=77, chat=-100888):
    return SimpleNamespace(chat=SimpleNamespace(id=chat, title='Group', type=enums.ChatType.SUPERGROUP),
                           from_user=SimpleNamespace(id=uid, first_name='Person', username=None))


def joined(uid=77, before=enums.ChatMemberStatus.LEFT, chat=-100888):
    u = SimpleNamespace(id=uid, first_name='Member', is_bot=False)
    return SimpleNamespace(chat=SimpleNamespace(id=chat, title='Group', type=enums.ChatType.SUPERGROUP),
                           old_chat_member=SimpleNamespace(status=before, user=u),
                           new_chat_member=SimpleNamespace(status=enums.ChatMemberStatus.MEMBER, user=u))


def query(data, uid=10, chat=-100888):
    q = FakeQuery(data, uid=uid)
    q.message.chat = SimpleNamespace(id=chat, title='Group', type=enums.ChatType.SUPERGROUP)
    return q


def test_joinmode_requires_confirmation_and_permissions_to_enable():
    from plugins.group_mod import plugin as g
    c = client(bot_priv=_full_privs(can_invite_users=False))
    no_confirm = command('/joinmode auto', c)
    run(g.joinmode_cmd(c, no_confirm))
    assert 'confirm' in no_confirm.replies[-1].lower()
    assert run(g.get_group_cfg(-100888))['joinmode'] == 'off'
    denied = command('/joinmode auto confirm', c)
    run(g.joinmode_cmd(c, denied))
    assert 'permission' in denied.replies[-1].lower()
    assert run(g.get_group_cfg(-100888))['joinmode'] == 'off'
    run(g.joinmode_cmd(c, command('/joinmode notify', c)))
    assert run(g.get_group_cfg(-100888))['joinmode'] == 'off'
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    run(g.joinmode_cmd(c, command('/joinmode auto confirm', c)))
    assert g._join_mode(run(g.get_group_cfg(-100888))) == 'auto'
    # Disabling does not require the bot to retain invite permission.
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_invite_users=False))
    run(g.joinmode_cmd(c, command('/joinmode off', c)))
    assert g._join_mode(run(g.get_group_cfg(-100888))) == 'off'
    denied = command('/joinmode auto confirm', c, uid=77)
    run(g.joinmode_cmd(c, denied))
    assert 'permission' in denied.replies[-1].lower()


def test_legacy_auto_is_inactive_and_runtime_requires_live_invite_rights():
    from plugins.group_mod import plugin as g
    c = client()
    run(g.update_group_cfg(-100888, {'joinmode': 'auto'}))
    run(g.group_join_request_handler(c, request()))
    assert not c.called('approve_chat_join_request')
    status = command('/joinmode', c)
    run(g.joinmode_cmd(c, status))
    assert 'inactive' in status.replies[-1].lower()
    run(g.joinmode_cmd(c, command('/joinmode auto confirm', c)))
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_invite_users=False))
    run(g.group_join_request_handler(c, request()))
    assert not c.called('approve_chat_join_request')
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    run(g.group_join_request_handler(c, request()))
    assert len(c.called('approve_chat_join_request')) == 1
    run(g.group_join_request_handler(c, request(chat=-100999)))
    assert len(c.called('approve_chat_join_request')) == 1


def test_raid_confirmation_checks_both_rights_and_off_works_after_revocation():
    from plugins.group_mod import plugin as g
    c = client(caller_priv=_full_privs(can_invite_users=False))
    for text in ['/antiraid 30', '/antiraid 4 confirm', '/antiraid 99999 confirm', '/antiraid nonsense confirm']:
        m = command(text, c)
        run(g.antiraid_cmd(c, m))
        assert not g._raid_active(run(g.get_group_cfg(-100888)))
    run(g.antiraid_cmd(c, command('/antiraid 30 confirm', c)))
    assert not g._raid_active(run(g.get_group_cfg(-100888)))
    c._members[10] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_invite_users=False))
    run(g.antiraid_cmd(c, command('/antiraid 30 confirm', c)))
    assert not g._raid_active(run(g.get_group_cfg(-100888)))
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    m = command('/antiraid 30 confirm', c)
    run(g.antiraid_cmd(c, m))
    assert g._raid_active(run(g.get_group_cfg(-100888)))
    assert 'all join requests' in m.replies[-1].lower()
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_restrict_members=False, can_invite_users=False))
    run(g.antiraid_cmd(c, command('/antiraid off', c)))
    assert not g._raid_active(run(g.get_group_cfg(-100888)))


def test_raid_runtime_checks_current_member_and_bot_status():
    import time
    from plugins.group_mod import plugin as g
    c = client()
    # An old unconfirmed config must not act after upgrade.
    run(g.update_group_cfg(-100888, {'antiraid_until': time.time() + 300}))
    run(g.group_member_greeting_handler(c, joined()))
    run(g.group_join_request_handler(c, request()))
    assert not c.called('ban_chat_member') and not c.called('decline_chat_join_request')
    run(g.antiraid_cmd(c, command('/antiraid 30 confirm', c)))
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_restrict_members=False, can_invite_users=False))
    run(g.group_member_greeting_handler(c, joined()))
    run(g.group_join_request_handler(c, request()))
    assert not c.called('ban_chat_member') and not c.called('decline_chat_join_request')
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    c._members[77] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    run(g.group_member_greeting_handler(c, joined()))
    assert not c.called('ban_chat_member')
    c._members[77] = (enums.ChatMemberStatus.MEMBER, None)
    run(g.group_member_greeting_handler(c, joined(before=None)))
    assert not c.called('ban_chat_member')
    run(g.group_member_greeting_handler(c, joined()))
    assert len(c.called('ban_chat_member')) == 1
    run(g.group_join_request_handler(c, request()))
    assert len(c.called('decline_chat_join_request')) == 1
    assert not c.called('approve_chat_join_request')


def test_dashboard_requires_confirmation_and_does_not_assume_bot_rights():
    from plugins.group_mod import plugin as g
    c = client(bot_priv=_full_privs(can_restrict_members=False, can_invite_users=False))
    q = query('plg_gmod:tog:antiflood')
    run(g.group_mod_cb(c, q))
    assert q.answers[-1][1] and not run(g.get_group_cfg(-100888))['antiflood']
    q = query('plg_gmod:tog:joinmode')
    run(g.group_mod_cb(c, q))
    assert q.answers[-1][1] and run(g.get_group_cfg(-100888))['joinmode'] == 'off'
    q = query('plg_gmod:tog:antiraid')
    run(g.group_mod_cb(c, q))
    assert 'confirm' in q.answers[-1][0].lower() and not g._raid_active(run(g.get_group_cfg(-100888)))
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    run(g.joinmode_cmd(c, command('/joinmode notify', c)))
    q = query('plg_gmod:tog:joinmode')
    run(g.group_mod_cb(c, q))
    assert run(g.get_group_cfg(-100888))['joinmode'] == 'off'
    assert not run(g.get_group_cfg(-100888))['joinmode_auto_confirmed']


def test_antiflood_requires_current_bot_permission_on_action():
    from plugins.group_mod import plugin as g
    c = client(bot_priv=_full_privs(can_restrict_members=False))
    run(g.update_group_cfg(-100888, {'antiflood': True}))
    for i in range(7):
        run(g.group_passive_guard(c, command(f'message {i}', c, uid=77)))
    assert not c.called('restrict_chat_member')
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())
    for i in range(7):
        run(g.group_passive_guard(c, command(f'message {i}', c, uid=77)))
    assert len(c.called('restrict_chat_member')) == 1


def test_legacy_raid_never_falls_through_to_confirmed_autoapproval():
    import time
    from plugins.group_mod import plugin as g
    c = client()
    run(g.joinmode_cmd(c, command('/joinmode auto confirm', c)))
    run(g.update_group_cfg(-100888, {'antiraid_until': time.time() + 300, 'antiraid_confirmed': False}))
    run(g.group_join_request_handler(c, request()))
    assert not c.called('approve_chat_join_request') and not c.called('decline_chat_join_request')


def test_unconfirmed_flood_and_join_results_do_not_claim_success():
    from plugins.group_mod import plugin as g
    class Unconfirmed(GroupFakeClient):
        async def restrict_chat_member(self, *args, **kwargs):
            return None
        async def approve_chat_join_request(self, *args, **kwargs):
            return False
    c = Unconfirmed({10: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
                     999: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())})
    run(g.update_group_cfg(-100888, {'antiflood': True}))
    m = None
    for i in range(7):
        m = command(f'message {i}', c, uid=77)
        run(g.group_passive_guard(c, m))
    assert not m.replies
    q = query('plg_gmod:jr:ok:77')
    run(g.group_mod_cb(c, q))
    assert 'not confirmed' in q.answers[-1][0].lower() and q.answers[-1][1]
    assert not q.message.edits


def test_unknown_membership_and_missing_bot_identity_never_enforce(caplog):
    from plugins.group_mod import plugin as g
    class Unknown(GroupFakeClient):
        async def get_chat_member(self, chat_id, user_id):
            if user_id == 77:
                raise RuntimeError('sensitive connection detail')
            return await super().get_chat_member(chat_id, user_id)
    c = Unknown({10: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs()),
                 999: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())})
    run(g.antiraid_cmd(c, command('/antiraid 30 confirm', c)))
    run(g.group_member_greeting_handler(c, joined()))
    assert not c.called('ban_chat_member')
    assert 'sensitive connection detail' not in caplog.text
    c.me = None
    run(g.group_join_request_handler(c, request()))
    assert not c.called('decline_chat_join_request')


def test_join_notify_and_dashboard_can_disable_after_permission_revocation():
    from plugins.group_mod import plugin as g
    c = client()
    run(g.joinmode_cmd(c, command('/joinmode notify', c)))
    run(g.group_join_request_handler(c, request()))
    assert len(c.called('send_message')) == 1
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_invite_users=False, can_restrict_members=False))
    run(g.group_join_request_handler(c, request()))
    assert len(c.called('send_message')) == 1
    # A previous enabled flood rule may be switched off despite missing bot rights.
    run(g.update_group_cfg(-100888, {'antiflood': True}))
    q = query('plg_gmod:tog:antiflood')
    run(g.group_mod_cb(c, q))
    assert not run(g.get_group_cfg(-100888))['antiflood']
    # Safety controls remain available when the bot loses permissions.
    run(g.joinmode_cmd(c, command('/joinmode off', c)))
    assert g._join_mode(run(g.get_group_cfg(-100888))) == 'off'


def test_raid_disable_dashboard_and_legacy_config_visibility():
    import time
    from plugins.group_mod import plugin as g
    c = client()
    run(g.update_group_cfg(-100888, {'antiraid_until': time.time() + 600, 'joinmode': 'auto'}))
    doc, _ = g.build_gsettings_doc('Group', run(g.get_group_cfg(-100888)))
    text = doc.classic().lower()
    assert 'inactive' in text and 'confirm again' in text
    c._members[999] = (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs(can_restrict_members=False, can_invite_users=False))
    q = query('plg_gmod:tog:antiraid')
    run(g.group_mod_cb(c, q))
    assert run(g.get_group_cfg(-100888))['antiraid_until'] == 0
    assert not run(g.get_group_cfg(-100888))['antiraid_confirmed']


def test_legacy_member_update_unknown_and_disabled_plugin_do_not_enforce():
    from core.plugin_manager import plugin_manager
    from plugins.group_mod import plugin as g
    c = client()
    run(g.antiraid_cmd(c, command('/antiraid 30 confirm', c)))
    plugin_manager._enabled['group_mod'] = False
    run(g.group_join_request_handler(c, request()))
    run(g.group_member_greeting_handler(c, joined()))
    assert not c.called('ban_chat_member') and not c.called('decline_chat_join_request')


def test_automation_warning_is_rate_limited_per_event(monkeypatch, caplog):
    from plugins.group_mod import plugin as g
    now = [100.0]
    g._AUTOMATION_WARNINGS.clear()
    monkeypatch.setattr(g.time, 'monotonic', lambda: now[0])
    with caplog.at_level('WARNING', logger='videl.plugin.group_mod'):
        g._automation_warn('test_event', 'safe warning')
        g._automation_warn('test_event', 'safe warning')
        now[0] += 61
        g._automation_warn('test_event', 'safe warning')
    assert caplog.text.count('safe warning') == 2
    g._AUTOMATION_WARNINGS.clear()


def test_existing_raid_disable_aliases_remain_available():
    import time
    from plugins.group_mod import plugin as g
    c = client(bot_priv=_full_privs(can_restrict_members=False, can_invite_users=False))
    for alias in ('disable', '0'):
        run(g.update_group_cfg(-100888, {'antiraid_until': time.time() + 300, 'antiraid_confirmed': True}))
        run(g.antiraid_cmd(c, command('/antiraid ' + alias, c)))
        assert not g._raid_active(run(g.get_group_cfg(-100888)))


def test_autoapproval_and_raid_shield_cannot_be_newly_enabled_together():
    from plugins.group_mod import plugin as g
    c = client()
    run(g.joinmode_cmd(c, command('/joinmode auto confirm', c)))
    attempt = command('/antiraid 30 confirm', c)
    run(g.antiraid_cmd(c, attempt))
    assert 'auto approval is enabled' in attempt.replies[-1].lower()
    assert not g._raid_active(run(g.get_group_cfg(-100888)))
    run(g.joinmode_cmd(c, command('/joinmode off', c)))
    run(g.antiraid_cmd(c, command('/antiraid 30 confirm', c)))
    attempt = command('/joinmode auto confirm', c)
    run(g.joinmode_cmd(c, attempt))
    assert 'turn off' in attempt.replies[-1].lower()
    assert g._join_mode(run(g.get_group_cfg(-100888))) == 'off'


def test_raid_unknown_is_bot_flag_fails_closed():
    from plugins.group_mod import plugin as g
    c = client()
    run(g.antiraid_cmd(c, command('/antiraid 30 confirm', c)))
    update = joined()
    update.new_chat_member.user.is_bot = None
    run(g.group_member_greeting_handler(c, update))
    assert not c.called('ban_chat_member')

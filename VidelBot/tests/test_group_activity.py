"""Offline group activity tests: real handlers, fake Telegram and Mongo."""
import asyncio
from types import SimpleNamespace

import pytest
from pyrogram import enums

from tests.harness import load_all, no_botapi, reset_db, run
from tests.test_group_mod_and_security import GroupFakeClient, _full_privs, _grp_msg

load_all()


@pytest.fixture(autouse=True)
def setup():
    from core.plugin_manager import plugin_manager
    run(reset_db())
    no_botapi()
    plugin_manager._enabled.clear()
    plugin_manager.discover()
    yield
    plugin_manager._enabled.clear()
    no_botapi()


def client(bot=999):
    c = GroupFakeClient({10: (enums.ChatMemberStatus.OWNER, None),
                         bot: (enums.ChatMemberStatus.ADMINISTRATOR, _full_privs())})
    c.me.id = bot
    return c


def msg(text, uid=77, chat=-100888, c=None):
    return _grp_msg(text, uid=uid, chat_id=chat, client=c)


def test_enable_authorization_and_default_disabled():
    from core.db import vdb
    from plugins.group_activity import plugin as a
    c = client()
    run(a.activity_counter(c, msg('hello', c=c)))
    assert run(vdb.db[a._MEMBERS].count_documents({})) == 0
    denied = msg('/activity on', uid=20, c=c)
    run(a.activity_cmd(c, denied))
    assert 'permission check failed' in denied.replies[-1].lower()
    assert run(vdb.db[a._SETTINGS].count_documents({})) == 0
    anon = msg('/activity on', uid=None, c=c)
    anon.sender_chat = SimpleNamespace(id=anon.chat.id)
    from core.middleware import senderless_gate
    run(senderless_gate(c, anon))
    run(a.activity_cmd(c, anon))
    assert 'counting on' in anon.replies[-1].lower()
    assert '/activityprivacy off' in anon.replies[-1]
    run(a.activity_counter(c, msg('hello', c=c)))
    assert run(vdb.db[a._MEMBERS].find_one({'_id': '999:-100888:77'}))['messages'] == 1
    run(a.activity_cmd(c, msg('/activity off', uid=10, c=c)))
    run(a.activity_counter(c, msg('after off', c=c)))
    assert run(vdb.db[a._MEMBERS].find_one({'_id': '999:-100888:77'}))['messages'] == 1


def test_ranking_member_stats_totals_privacy_and_no_content_storage():
    from core.db import vdb
    from plugins.group_activity import plugin as a
    c = client()
    run(a.activity_cmd(c, msg('/activity on', uid=10, c=c)))
    for uid in (77, 88, 77, 88, 77):
        event = msg('private message content', uid=uid, c=c)
        event.from_user.first_name = 'Alice <script>' if uid == 77 else 'Bob & friends'
        run(a.activity_counter(c, event))
    for text in ('/topmembers', '/start abc'):
        run(a.activity_counter(c, msg(text, uid=77, c=c)))
    top = msg('/topmembers', uid=88, c=c)
    run(a.topmembers_cmd(c, top))
    text = top.replies[-1]
    assert 'Alice &lt;script&gt;' in text and 'Bob &amp; friends' in text
    assert text.index('Alice') < text.index('Bob') and '<script>' not in text
    my = msg('/myactivity', uid=88, c=c)
    run(a.myactivity_cmd(c, my))
    assert '#2' in my.replies[-1] and '2' in my.replies[-1]
    other = msg('/memberactivity', uid=88, c=c)
    other.reply_to_message = msg('x', uid=77, c=c)
    run(a.memberactivity_cmd(c, other))
    assert '#1' in other.replies[-1] and '3' in other.replies[-1]
    total = msg('/groupactivity', uid=88, c=c)
    run(a.groupactivity_cmd(c, total))
    assert '5' in total.replies[-1] and '2' in total.replies[-1]
    docs = run(vdb.db[a._MEMBERS].find({}).to_list(10))
    assert 'private message content' not in repr(docs)
    off = msg('/activityprivacy off', uid=77, c=c)
    run(a.activityprivacy_cmd(c, off))
    saved = run(vdb.db[a._MEMBERS].find_one({'_id': '999:-100888:77'}))
    assert saved['opted_out'] is True and all(key not in saved for key in ('messages', 'display_name'))
    run(a.activity_counter(c, msg('still private', uid=77, c=c)))
    assert 'messages' not in run(vdb.db[a._MEMBERS].find_one({'_id': saved['_id']}))
    run(a.memberactivity_cmd(c, other))
    assert 'no recorded count' in other.replies[-1].lower()
    run(a.activityprivacy_cmd(c, msg('/activityprivacy on', uid=77, c=c)))
    run(a.activity_counter(c, msg('return', uid=77, c=c)))
    assert run(vdb.db[a._MEMBERS].find_one({'_id': saved['_id']}))['messages'] == 1


def test_scope_isolated_by_bot_and_group_disabled_plugin_and_exclusions():
    from core.db import vdb
    from core.plugin_manager import plugin_manager
    from plugins.group_activity import plugin as a
    c1, c2 = client(999), client(1000)
    run(a.activity_cmd(c1, msg('/activity on', uid=10, c=c1)))
    run(a.activity_counter(c1, msg('one', c=c1)))
    run(a.activity_counter(c2, msg('no other bot enable', c=c2)))
    run(a.activity_counter(c1, msg('no other group enable', chat=-100999, c=c1)))
    assert run(vdb.db[a._MEMBERS].count_documents({})) == 1
    event = msg('bot', c=c1)
    event.from_user.is_bot = True
    run(a.activity_counter(c1, event))
    event = msg('anonymous', c=c1)
    event.sender_chat = SimpleNamespace(id=-100888)
    run(a.activity_counter(c1, event))
    plugin_manager._enabled['group_activity'] = False
    run(a.activity_counter(c1, msg('disabled plugin', c=c1)))
    assert run(vdb.db[a._MEMBERS].find_one({'_id': '999:-100888:77'}))['messages'] == 1
    plugin_manager._enabled['group_activity'] = True
    run(a.activity_cmd(c2, msg('/activity on', uid=10, c=c2)))
    run(a.activity_counter(c2, msg('second bot', c=c2)))
    assert run(vdb.db[a._MEMBERS].count_documents({})) == 2
    run(a.activityprivacy_cmd(c1, msg('/activityprivacy off', c=c1)))
    assert run(vdb.db[a._MEMBERS].find_one({'_id': '1000:-100888:77'}))['messages'] == 1


def test_privacy_optout_wins_over_an_inflight_first_message():
    from core.db import vdb
    from plugins.group_activity import plugin as a
    c = client()
    run(a.activity_cmd(c, msg('/activity on', uid=10, c=c)))
    real = vdb.db[a._MEMBERS]
    class Barrier:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.first = True
        async def update_one(self, *args, **kwargs):
            if self.first:
                self.first = False
                self.started.set()
                await self.release.wait()
            return await real.update_one(*args, **kwargs)
        async def find_one(self, *args, **kwargs):
            return await real.find_one(*args, **kwargs)
    barrier = Barrier()
    original = a.vdb
    class DB:
        def __getitem__(self, name):
            return barrier if name == a._MEMBERS else original.db[name]
    a.vdb = SimpleNamespace(db=DB())
    async def exercise():
        event = asyncio.create_task(a.activity_counter(c, msg('hello', c=c)))
        await barrier.started.wait()
        await a.activityprivacy_cmd(c, msg('/activityprivacy off', c=c))
        barrier.release.set()
        await event
    try:
        run(exercise())
    finally:
        a.vdb = original
    record = run(real.find_one({'_id': '999:-100888:77'}))
    assert record['opted_out'] is True and 'messages' not in record and 'display_name' not in record


def test_discovery_boot_handlers_menu_limit_and_plugin_filter():
    import run as bot_run
    from core.commands import GROUP_COMMANDS, _cmds
    from core.plugin_manager import plugin_manager
    from plugins.group_activity import plugin as a
    assert not plugin_manager.command_conflicts(plugin_manager.discover()['group_activity'])
    assert {'activity', 'topmembers', 'myactivity', 'memberactivity', 'groupactivity', 'activityprivacy'} <= {x.command for x in _cmds(GROUP_COMMANDS)}
    assert len(_cmds(GROUP_COMMANDS)) <= 100
    class Registry:
        def __init__(self):
            self.handlers = []
        def add_handler(self, handler, group):
            self.handlers.append((handler, group))
    app = Registry()
    bot_run.load_plugins(app)
    callbacks = [getattr(handler, 'callback', None) for handler, _ in app.handlers]
    assert a.topmembers_cmd in callbacks and a.activity_counter in callbacks
    assert not bot_run.boot_import_errors.get('plugins.group_activity.plugin')
    handler = a.topmembers_cmd.handlers[0][0]
    assert run(handler.filters(client(), msg('/topmembers')))
    plugin_manager._enabled['group_activity'] = False
    assert not run(handler.filters(client(), msg('/topmembers')))


def test_equal_counts_rank_by_id_and_views_do_not_cross_groups():
    from plugins.group_activity import plugin as a
    c = client()
    run(a.activity_cmd(c, msg('/activity on', uid=10, c=c)))
    run(a.activity_counter(c, msg('hello', uid=88, c=c)))
    run(a.activity_counter(c, msg('hello', uid=77, c=c)))
    first = msg('/myactivity', uid=77, c=c)
    second = msg('/myactivity', uid=88, c=c)
    run(a.myactivity_cmd(c, first))
    run(a.myactivity_cmd(c, second))
    assert '#1' in first.replies[-1] and '#2' in second.replies[-1]
    other_group = msg('/myactivity', uid=77, chat=-100999, c=c)
    run(a.myactivity_cmd(c, other_group))
    assert 'no recorded count' in other_group.replies[-1].lower()


def test_time_based_rankings_rolling_dates_totals_and_privacy(monkeypatch):
    from core.db import vdb
    from plugins.group_activity import plugin as a
    c = client()
    day = [20000]
    monkeypatch.setattr(a, '_utc_day', lambda: day[0])
    run(a.activity_cmd(c, msg('/activity on', uid=10, c=c)))
    # Old aggregate records must not be backfilled into a period.
    run(vdb.db[a._MEMBERS].insert_one({'_id': '999:-100888:99', 'bot_id': 999, 'chat_id': -100888, 'user_id': 99, 'messages': 50, 'display_name': 'Legacy'}))
    def send(uid, name):
        m = msg('hello', uid=uid, c=c)
        m.from_user.first_name = name
        run(a.activity_counter(c, m))
    for _ in range(3):
        send(77, 'Alice')
    send(88, 'Bob')
    day[0] += 1
    for _ in range(3):
        send(88, 'Bob')
    today = msg('/topmembers today', uid=10, c=c)
    run(a.topmembers_cmd(c, today))
    assert 'Bob' in today.replies[-1] and 'Alice' not in today.replies[-1] and 'Legacy' not in today.replies[-1]
    week = msg('/topmembers week', uid=10, c=c)
    run(a.topmembers_cmd(c, week))
    assert week.replies[-1].index('Bob') < week.replies[-1].index('Alice') and 'Legacy' not in week.replies[-1]
    all_time = msg('/topmembers all', uid=10, c=c)
    run(a.topmembers_cmd(c, all_time))
    assert all_time.replies[-1].index('Legacy') < all_time.replies[-1].index('Bob')
    mine = msg('/myactivity', uid=88, c=c)
    run(a.myactivity_cmd(c, mine))
    assert '4' in mine.replies[-1] and 'not backfilled' in mine.replies[-1]
    group = msg('/groupactivity', uid=10, c=c)
    run(a.groupactivity_cmd(c, group))
    assert '57' in group.replies[-1] and '7' in group.replies[-1]
    day[0] = 20007
    send(77, 'Alice')
    week = msg('/topmembers week', uid=10, c=c)
    run(a.topmembers_cmd(c, week))
    assert week.replies[-1].index('Bob') < week.replies[-1].index('Alice')
    rec = run(vdb.db[a._MEMBERS].find_one({'_id': '999:-100888:77'}))
    assert rec['messages'] == 4 and a._period_totals(rec, day[0]) == (1, 1)
    run(a.activityprivacy_cmd(c, msg('/activityprivacy off', uid=77, c=c)))
    rec = run(vdb.db[a._MEMBERS].find_one({'_id': '999:-100888:77'}))
    assert rec['opted_out'] and 'daily' not in rec and 'messages' not in rec
    run(a.topmembers_cmd(c, week))
    assert 'Alice' not in week.replies[-1]


def test_utc_slots_are_bounded_and_late_events_cannot_reset_newer_day(monkeypatch):
    from core.db import vdb
    from plugins.group_activity import plugin as a
    c = client()
    day = [1000]
    monkeypatch.setattr(a, '_utc_day', lambda: day[0])
    run(a.activity_cmd(c, msg('/activity on', uid=10, c=c)))
    user = msg('x', uid=77, c=c).from_user
    for i in range(30):
        day[0] = 1000 + i
        run(a.activity_counter(c, msg('hello', uid=77, c=c)))
    rec = run(vdb.db[a._MEMBERS].find_one({'_id': '999:-100888:77'}))
    assert rec['messages'] == 30 and len(rec['daily']) == 7
    assert a._period_totals(rec, day[0]) == (1, 7)
    run(a._count_message(999, -100888, user, 1001))  # stale date shares slot with 1029
    rec = run(vdb.db[a._MEMBERS].find_one({'_id': '999:-100888:77'}))
    assert rec['messages'] == 31 and a._period_totals(rec, 1029) == (1, 7)
    assert rec['daily'][f's{1029 % 7}']['day'] == 1029


def test_concurrent_first_day_updates_do_not_lose_messages(monkeypatch):
    from core.db import vdb
    from plugins.group_activity import plugin as a
    c = client()
    monkeypatch.setattr(a, '_utc_day', lambda: 20000)
    run(a.activity_cmd(c, msg('/activity on', uid=10, c=c)))
    real = vdb.db[a._MEMBERS]
    class Barrier:
        def __init__(self):
            self.rotations = 0
            self.ready = asyncio.Event()
        async def update_one(self, query, *args, **kwargs):
            if '$or' in query and self.rotations < 2:
                self.rotations += 1
                if self.rotations == 2:
                    self.ready.set()
                await self.ready.wait()
            return await real.update_one(query, *args, **kwargs)
        async def find_one(self, *args, **kwargs):
            return await real.find_one(*args, **kwargs)
    barrier = Barrier()
    original = a.vdb
    class DB:
        def __getitem__(self, name):
            return barrier if name == a._MEMBERS else original.db[name]
    a.vdb = SimpleNamespace(db=DB())
    async def exercise():
        await asyncio.gather(a.activity_counter(c, msg('one', uid=77, c=c)), a.activity_counter(c, msg('two', uid=77, c=c)))
    try:
        run(exercise())
    finally:
        a.vdb = original
    rec = run(real.find_one({'_id': '999:-100888:77'}))
    assert rec['messages'] == 2 and a._period_totals(rec, 20000) == (2, 2)


def test_topmembers_period_usage_and_utc_day(monkeypatch):
    from plugins.group_activity import plugin as a
    c = client()
    bad = msg('/topmembers monthly', uid=10, c=c)
    run(a.topmembers_cmd(c, bad))
    assert '/topmembers [all|today|week]' in bad.replies[-1]
    monkeypatch.setattr(a.time, 'time', lambda: 86400 * 20000 + 86399)
    assert a._utc_day() == 20000
    monkeypatch.setattr(a.time, 'time', lambda: 86400 * 20001)
    assert a._utc_day() == 20001


def test_period_leaderboards_respect_bot_group_and_pause(monkeypatch):
    from plugins.group_activity import plugin as a
    c1, c2 = client(999), client(1000)
    day = [30000]
    monkeypatch.setattr(a, '_utc_day', lambda: day[0])
    run(a.activity_cmd(c1, msg('/activity on', uid=10, c=c1)))
    run(a.activity_cmd(c2, msg('/activity on', uid=10, c=c2)))
    run(a.activity_cmd(c1, msg('/activity on', uid=10, chat=-100999, c=c1)))
    for c, group, name in [(c1, -100888, 'Alpha'), (c2, -100888, 'Beta'), (c1, -100999, 'Gamma')]:
        event = msg('hello', uid=77, chat=group, c=c)
        event.from_user.first_name = name
        run(a.activity_counter(c, event))
    a_top = msg('/topmembers week', uid=10, c=c1)
    run(a.topmembers_cmd(c1, a_top))
    assert 'Alpha' in a_top.replies[-1] and 'Beta' not in a_top.replies[-1] and 'Gamma' not in a_top.replies[-1]
    b_top = msg('/topmembers week', uid=10, c=c2)
    run(a.topmembers_cmd(c2, b_top))
    assert 'Beta' in b_top.replies[-1] and 'Alpha' not in b_top.replies[-1]
    run(a.activity_cmd(c1, msg('/activity off', uid=10, c=c1)))
    run(a.activity_counter(c1, msg('ignored', uid=77, c=c1)))
    day[0] += 7
    run(a.topmembers_cmd(c1, a_top))
    assert 'no messages were recorded for this page and period' in a_top.replies[-1].lower()
    all_time = msg('/topmembers all', uid=10, c=c1)
    run(a.topmembers_cmd(c1, all_time))
    assert 'Alpha' in all_time.replies[-1] and 'counting is off' in all_time.replies[-1].lower()


def test_leaderboard_pages_and_period_switches_are_group_scoped(monkeypatch):
    from core.db import vdb
    from plugins.group_activity import plugin as a
    c = client()
    monkeypatch.setattr(a, '_utc_day', lambda: 20000)
    run(a.activity_cmd(c, msg('/activity on', uid=10, c=c)))
    col = vdb.db[a._MEMBERS]
    for i in range(13):
        user_id = i + 100
        run(col.insert_one({'_id': f'999:-100888:{user_id}', 'bot_id': 999, 'chat_id': -100888,
                            'user_id': user_id, 'display_name': f'User{user_id}', 'messages': 100 - i,
                            'daily': {f's{20000 % 7}': {'day': 20000, 'count': 100 - i}}}))
    run(col.insert_one({'_id': '999:-100999:555', 'bot_id': 999, 'chat_id': -100999,
                        'user_id': 555, 'display_name': 'OtherGroup', 'messages': 999}))
    base = msg('/topmembers', uid=10, c=c)
    doc, markup = run(a._leaderboard(c, base, 'all', 1))
    assert 'User100' in doc.classic() and 'User110' not in doc.classic() and 'OtherGroup' not in doc.classic()
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert 'plg_act:board:all:2' in buttons and 'plg_act:board:today:1' in buttons
    doc, markup = run(a._leaderboard(c, base, 'all', 2))
    assert 'User110' in doc.classic() and 'User100' not in doc.classic()
    buttons = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert 'plg_act:board:all:1' in buttons and 'plg_act:board:all:3' not in buttons
    doc, _ = run(a._leaderboard(c, base, 'week', 2))
    assert 'User110' in doc.classic() and 'User100' not in doc.classic()
    cmd = msg('/topmembers week 2', uid=10, c=c)
    run(a.topmembers_cmd(c, cmd))
    assert 'User110' in cmd.replies[-1]
    for text in ['/topmembers week 0', '/topmembers all 101', '/topmembers １２', '/topmembers week 1 extra']:
        bad = msg(text, uid=10, c=c)
        run(a.topmembers_cmd(c, bad))
        assert 'usage' in bad.replies[-1].lower()


def test_leaderboard_callback_validates_scope_and_enabled_state(monkeypatch):
    from tests.harness import FakeQuery
    from core.plugin_manager import plugin_manager
    from plugins.group_activity import plugin as a
    c = client()
    run(a.activity_cmd(c, msg('/activity on', uid=10, c=c)))
    event = msg('hello', uid=77, c=c)
    event.from_user.first_name = 'Participant'
    run(a.activity_counter(c, event))
    board = msg('leaderboard', uid=10, c=c)
    q = FakeQuery('plg_act:board:today:1', uid=20, message=board)
    run(a.leaderboard_callback(c, q))
    assert q.answers[-1] == (None, False)
    assert board.edits and 'Participant' in board.edits[-1]
    bad = FakeQuery('plg_act:board:today:999', uid=20, message=msg('x', c=c))
    run(a.leaderboard_callback(c, bad))
    assert 'invalid' in bad.answers[-1][0].lower() and bad.answers[-1][1]
    plugin_manager._enabled['group_activity'] = False
    disabled = FakeQuery('plg_act:board:all:1', uid=20, message=msg('x', c=c))
    run(a.leaderboard_callback(c, disabled))
    assert 'disabled' in disabled.answers[-1][0].lower()
    plugin_manager._enabled['group_activity'] = True
    private = msg('x', c=c)
    private.chat.type = enums.ChatType.PRIVATE
    q = FakeQuery('plg_act:board:all:1', uid=20, message=private)
    run(a.leaderboard_callback(c, q))
    assert 'group' in q.answers[-1][0].lower()


def test_same_leaderboard_tab_acknowledges_unchanged_message():
    from pyrogram.errors import MessageNotModified
    from tests.harness import FakeQuery
    from plugins.group_activity import plugin as a
    c = client()
    board = msg('x', c=c)
    async def unchanged(*args, **kwargs):
        raise MessageNotModified('MESSAGE_NOT_MODIFIED')
    board.edit_text = unchanged
    q = FakeQuery('plg_act:board:all:1', uid=10, message=board)
    run(a.leaderboard_callback(c, q))
    assert q.answers[-1] == (None, False)

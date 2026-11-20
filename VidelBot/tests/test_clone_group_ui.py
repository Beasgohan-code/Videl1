"""Offline navigation and read-only permission diagnostics for clone group UI."""
import inspect
from types import SimpleNamespace

import pytest
from pyrogram import enums
from pyrogram.errors import MessageNotModified
from pyrogram.handlers import CallbackQueryHandler

from tests.harness import FakeMsg, FakeQuery, run
from tests.test_audit import _dispatch
from tests.test_clone_group_moderation import application, RecordingClient, admin, group, status
from filestore.worker_bot import group_ui


class CaptureMsg(FakeMsg):
    def __init__(self, text='/start', uid=5, chat_type=enums.ChatType.PRIVATE, chat_id=None):
        super().__init__(text, uid=uid, chat_type=chat_type, chat_id=chat_id)
        self.reply_options = []
        self.edit_options = []

    async def reply_text(self, text, *args, **kwargs):
        self.reply_options.append(kwargs)
        return await super().reply_text(text, *args, **kwargs)

    async def edit_text(self, text, *args, **kwargs):
        self.edit_options.append(kwargs)
        return await super().edit_text(text, *args, **kwargs)


async def dispatch_callback(app, client, query):
    for handlers in app.dispatcher.groups.values():
        for handler in handlers:
            if not isinstance(handler, CallbackQueryHandler):
                continue
            result = handler.filters(client, query) if handler.filters else True
            if await result if inspect.isawaitable(result) else result:
                await (getattr(handler, 'original_callback', None) or handler.callback)(client, query)
                return True
    return False


def query_for(page='home', *, bot_id=4242, chat_type=enums.ChatType.PRIVATE, uid=5):
    chat_id = uid if chat_type == enums.ChatType.PRIVATE else -100
    msg = CaptureMsg('guide', uid=uid, chat_type=chat_type, chat_id=chat_id)
    q = FakeQuery('cgh:' + page, uid=uid, message=msg)
    q.message.from_user.id = bot_id
    return q


def test_start_and_group_help_show_navigable_ui(application):
    async def go():
        c=RecordingClient()
        for m in (CaptureMsg('/start'), CaptureMsg('/help', chat_type=enums.ChatType.SUPERGROUP, chat_id=-100)):
            await _dispatch(application, c, m)
            assert 'Group Management' in m.replies[-1] and 'File Store commands' in m.replies[-1]
            kb = m.reply_options[-1]['reply_markup']
            assert [b.callback_data for row in kb.inline_keyboard for b in row] == ['cgh:home', 'cgh:moderate', 'cgh:messages', 'cgh:welcome', 'cgh:notes', 'cgh:setup']
            assert m.reply_options[-1]['parse_mode'] == enums.ParseMode.HTML
        assert not c.calls
    run(go())


@pytest.mark.parametrize('page', list(group_ui.PAGES))
def test_real_callback_filters_navigate_static_pages(application, page):
    async def go():
        c=RecordingClient();q=query_for(page, chat_type=enums.ChatType.SUPERGROUP)
        assert await dispatch_callback(application, c, q)
        assert q.message.edits == [group_ui.PAGES[page]]
        assert q.answers == [(None, False)]
        kb=q.message.edit_options[-1]['reply_markup']
        assert sum(b.text.startswith('• ') for row in kb.inline_keyboard for b in row) == 1
        assert not c.calls
    run(go())


def test_callback_rejects_wrong_author_chat_actor_or_data_without_edit():
    async def go():
        c=RecordingClient()
        for q in (query_for('home', bot_id=999), query_for('home', chat_type=enums.ChatType.CHANNEL),
                  query_for('home', uid=0), query_for('home:unexpected')):
            await group_ui.guide_callback(c, q)
            assert not q.message.edits and q.answers[-1][1] is True
        q=query_for();q.message.chat.id=123
        await group_ui.guide_callback(c,q)
        assert not q.message.edits and q.answers[-1][1] is True
        q=query_for();q.from_user.is_bot=True
        await group_ui.guide_callback(c,q)
        assert not q.message.edits and q.answers[-1][1] is True
    run(go())


def test_callback_not_modified_and_failed_edit_are_acknowledged_without_error_leak():
    async def go():
        c=RecordingClient()
        for error, alert in ((MessageNotModified(), False), (RuntimeError('token secret'), True)):
            q=query_for()
            async def fail(*args, **kwargs): raise error
            q.message.edit_text=fail
            await group_ui.guide_callback(c,q)
            assert q.answers[-1][1] is alert and 'secret' not in str(q.answers)
    run(go())


def test_group_check_rights_and_no_mutating_calls(application):
    async def go():
        c=RecordingClient();c.members[4242]=admin(can_restrict_members=True, can_delete_messages=False,
                                                    can_pin_messages=None, can_change_info=True)
        m=group('/gcheck');await _dispatch(application,c,m)
        out=m.replies[-1]
        assert 'Administrator' in out and 'Restrict members: ✅ Granted' in out
        assert 'Delete messages: ❌ Missing' in out and 'Pin messages: ❔ Unknown' in out
        assert 'actor and target rights' in out and 'No settings were changed' in out
        assert c.calls == [('member', -100, 4242)]
        c.calls.clear();m=group('.gcheck');m.chat.type=enums.ChatType.GROUP
        c.members[4242]=status(enums.ChatMemberStatus.MEMBER)
        await _dispatch(application,c,m)
        assert 'Basic group' in m.replies[-1] and 'require a supergroup' in m.replies[-1]
        assert m.replies[-1].count('❌ Missing')==4
        c.calls.clear();c.members[4242]=status(enums.ChatMemberStatus.OWNER);m=group('/gcheck')
        await _dispatch(application,c,m);assert m.replies[-1].count('✅ Granted')==4
    run(go())


def test_group_check_unknown_failures_and_command_filter(application):
    async def go():
        c=RecordingClient();c.members[4242]=SimpleNamespace(status=['unexpected'], privileges=None)
        m=group('/gcheck');await _dispatch(application,c,m)
        assert 'Bot role: <b>Unknown</b>' in m.replies[-1] and m.replies[-1].count('❔ Unknown')==4
        c.members[4242]=RuntimeError('db secret');m=group('/gcheck');await _dispatch(application,c,m)
        assert 'could not confirm' in m.replies[-1] and 'secret' not in m.replies[-1]
        c.calls.clear();m=group('/gcheck extra');await _dispatch(application,c,m)
        assert 'Usage' in m.replies[-1] and not c.calls
        m=CaptureMsg('/gcheck');await _dispatch(application,c,m)
        assert not m.replies
        c.me.id=None;m=group('/gcheck');await _dispatch(application,c,m)
        assert 'identity' in m.replies[-1] and not c.calls
    run(go())

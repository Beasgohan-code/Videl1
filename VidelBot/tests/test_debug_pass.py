"""Debug pass: /clear with parallel workers, stale queue buttons, bare-domain KEEP_ALIVE_URL."""
import os
import subprocess
import sys

import pytest

from tests.harness import FakeMsg, FakeQuery, load_all, no_botapi, reset_db, run

MODULES = load_all()

import config  # noqa: E402
from VideoEncoder import data as queue  # noqa: E402
from VideoEncoder.utils import scheduler  # noqa: E402
import VideoEncoder.plugins.queue as Q  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    run(reset_db())
    no_botapi()
    saved = list(queue)
    queue.clear()
    scheduler._running.clear()
    monkeypatch.setattr(config, "ENCODER_WORKERS", 2)
    monkeypatch.setattr(config, "QUEUE_PERSIST", True)
    yield
    queue[:] = saved
    scheduler._running.clear()
    for d in (scheduler.MODES, scheduler.EXTRA, scheduler.NOTES, scheduler.SRC):
        d.clear()
    scheduler.PRIO.clear()


def _queued(n):
    msgs = []
    for i in range(n):
        m = FakeMsg(text="/dl", uid=10 + i)
        m.video = m.document = m.audio = None
        scheduler.add(m, "tg")
        run(scheduler.persist(m, "tg"))
        msgs.append(m)
    return msgs


def _stored_keys():
    async def go():
        return {d["_id"] async for d in scheduler._col().find({})}
    return run(go())


def test_clear_keeps_every_running_task_and_forgets_the_purged_ones():
    a, b, c, d = _queued(4)
    scheduler.mark_running(a)
    scheduler.mark_running(b)             # two workers busy
    note = FakeMsg(text="Added to the queue #3", uid=0)
    scheduler.NOTES[id(c)] = note

    removed = run(Q.purge_waiting())

    assert removed == 2
    assert queue == [a, b]                               # the 2nd running task used to be dropped
    assert scheduler.running() == [a, b] and scheduler.slots_free() == 0
    assert _stored_keys() == {scheduler.task_key(a), scheduler.task_key(b)}   # no resurrection after restart
    assert id(c) not in scheduler.MODES and id(d) not in scheduler.SRC
    assert note.edits and "Removed from the queue" in note.edits[-1]


def test_clear_command_replies():
    a, b = _queued(2)
    scheduler.mark_running(a)
    scheduler.mark_running(b)
    msg = FakeMsg(text="/clear", uid=111)
    run(Q.clear(None, msg))
    assert "Nothing is waiting" in msg.replies[-1] and queue == [a, b]
    c = _queued(1)[0]
    run(Q.clear(None, msg))
    assert "Purged 1 waiting task " in msg.replies[-1] and c not in queue


def test_queue_doc_marks_all_running_tasks():
    a, b, c = _queued(3)
    scheduler.mark_running(a)
    scheduler.mark_running(b)
    html = Q.queue_doc().classic()
    assert html.count("▶️") == 2 and "⏳ #3" in html and "#1" not in html and "#2" not in html


def test_stale_queue_button_shows_current_queue():
    _queued(1)
    q = FakeQuery("queue+3")
    run(Q.queue_answer(None, q))
    assert q.answers and "queue changed" in q.answers[-1][0]
    assert q.message.edits and "📋" in q.message.edits[-1] and "Tester" in q.message.edits[-1]


@pytest.mark.parametrize("raw,want", [
    ("videl.onrender.com", "https://videl.onrender.com"),
    ("https://videl.onrender.com/", "https://videl.onrender.com"),
    ("http://1.2.3.4:8080", "http://1.2.3.4:8080"),
    ("", ""),
])
def test_keep_alive_url_gets_a_scheme(raw, want):
    assert config._https(raw) == want


def test_log_channel_username_warns_with_a_hint():
    env = {"PATH": os.environ["PATH"], "HOME": "/tmp", "BOT_TOKEN": "1:x", "API_ID": "1", "API_HASH": "x",
           "DB_URI": "mongodb://x", "OWNER_ID": "111", "LOG_CHANNEL": "@mylogs"}
    r = subprocess.run([sys.executable, "-c", "import config; print(config.LOG_CHANNEL)"], env=env,
                       capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert r.stdout.strip() == "0" and "numeric id" in r.stderr


class _Recorder:
    def __init__(self):
        self.msg, self.cb = [], []

    def on_message(self, *a, **k):
        def deco(fn):
            self.msg.append(fn)
            return fn
        return deco

    def on_callback_query(self, *a, **k):
        def deco(fn):
            self.cb.append(fn)
            return fn
        return deco


def test_flink_started_twice_keeps_the_newer_flow_listening(monkeypatch):
    import asyncio
    import filestore.worker_bot.flink_logic as F
    from pyrogram import StopPropagation

    seen = []

    async def fake_id(client, msg, chan):
        seen.append(msg)
        return None                      # stop right after the first answer
    monkeypatch.setattr(F, "get_message_id", fake_id)

    async def is_admin(uid):
        return True

    app = _Recorder()
    F.setup_flink(app, None, -100, is_admin)
    catcher, handle = app.msg[0], [f for f in app.msg if f.__name__ == "handle_flink"][0]

    async def go():
        m1, m2 = FakeMsg("/flink", uid=111), FakeMsg("/flink", uid=111)
        t1 = asyncio.ensure_future(handle(None, m1))
        await asyncio.sleep(0.01)
        t2 = asyncio.ensure_future(handle(None, m2))
        await asyncio.sleep(0.01)
        await asyncio.wait_for(t1, 1)                       # the older flow ends right away
        assert "Cᴀɴᴄᴇʟʟᴇᴅ" in m1.replies[-1]
        post = FakeMsg("forwarded post", uid=111)
        try:
            await catcher(None, post)                       # the newer flow still receives input
        except StopPropagation:
            pass
        await asyncio.wait_for(t2, 1)
        assert seen == [post]
    run(go())


# ───────────────────────── saver login ─────────────────────────
from pyrogram.types import ReplyKeyboardRemove  # noqa: E402


class StrictMsg(FakeMsg):
    """Like Telegram: an edit may only carry an inline keyboard."""
    async def edit_text(self, text, *a, **k):
        if isinstance(k.get("reply_markup"), ReplyKeyboardRemove):
            raise ValueError("REPLY_MARKUP_INVALID")
        return await super().edit_text(text, *a, **k)

    edit = edit_text

    async def reply_text(self, text, *a, **k):
        self.replies.append((text, type(k.get("reply_markup")).__name__))
        sent = StrictMsg(text=text, uid=0, chat_id=self.chat.id)
        self.sent.append(sent)
        return sent

    reply = reply_text


class TempClient:
    fail_connect = False

    def __init__(self, *a, **k):
        self.connected = False

    async def connect(self):
        if TempClient.fail_connect:
            raise OSError("network down")
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def send_code(self, phone):
        from types import SimpleNamespace
        return SimpleNamespace(phone_code_hash="h")

    async def sign_in(self, *a):
        return True

    async def export_session_string(self):
        return "SESSION"

    async def get_me(self):
        return None


def _login_msg(text, uid=77):
    m = StrictMsg(text=text, uid=uid)
    return m


def test_login_filter_lets_other_commands_through():
    from saver import session as S
    from types import SimpleNamespace
    S.LOGIN_STATE[77] = {"step": "WAITING_PHONE", "data": {}}
    try:
        f = S.check_login_state
        assert run(f(None, None, _login_msg("+919876543210")))
        assert not run(f(None, None, _login_msg("/help")))
        S.LOGIN_STATE[77]["step"] = "WAITING_PASSWORD"
        assert run(f(None, None, _login_msg("/my-pass")))          # a password may start with /
        assert not run(f(None, None, SimpleNamespace(from_user=None, text="x")))
    finally:
        S.LOGIN_STATE.clear()


def test_login_success_is_shown_and_keyboard_removed(monkeypatch):
    from saver import session as S
    from database.db import db
    monkeypatch.setattr(S, "Client", TempClient)
    monkeypatch.setattr(S, "animate_loading", lambda *a, **k: asyncio_sleep0())
    S.LOGIN_STATE[77] = {"step": "WAITING_PHONE", "data": {}}
    phone = _login_msg("+91 98765 43210")
    run(S.login_handler(None, phone))
    assert S.LOGIN_STATE[77]["step"] == "WAITING_CODE"
    code = _login_msg("12 345")
    run(S.login_handler(None, code))
    assert 77 not in S.LOGIN_STATE
    assert run(db.get_session(77)) == "SESSION"          # saved even without a saver document
    status = code.sent[0]                                  # "Verifying code…"
    assert "Verifying" in status.text or status.edits
    assert any("Login Successful" in e for e in status.edits if isinstance(e, str))   # the edit used to raise
    assert ("✅", "ReplyKeyboardRemove") in status.replies  # keyboard taken away by a new message
    assert status.sent and status.sent[-1].deleted


def test_login_connect_failure_ends_cleanly(monkeypatch):
    from saver import session as S
    monkeypatch.setattr(S, "Client", TempClient)
    monkeypatch.setattr(S, "animate_loading", lambda *a, **k: asyncio_sleep0())
    monkeypatch.setattr(TempClient, "fail_connect", True)
    S.LOGIN_STATE[77] = {"step": "WAITING_PHONE", "data": {}}
    m = _login_msg("+919876543210")
    run(S.login_handler(None, m))                      # used to raise out of the handler
    assert 77 not in S.LOGIN_STATE


async def asyncio_sleep0():
    import asyncio
    await asyncio.sleep(0)


def test_saver_setters_work_without_a_saver_document(monkeypatch):
    from database.db import db
    import database.db as D
    monkeypatch.setattr(D, "FREE_LIMIT_DAILY", 2)
    uid = 9090                                    # known to Videl, never added to the saver users collection

    async def go():
        await db.set_session(uid, "S")
        await db.ban_user(uid)
        await db.set_dump_chat(uid, -1005)
        for _ in range(2):
            assert not await db.check_limit(uid)
            await db.add_traffic(uid)
        assert await db.check_limit(uid)          # the daily limit now applies (it used to be unlimited)
        await db.add_user(uid, "Late")            # idempotent – no duplicate document
        await db.add_user(uid, "Late")
        return await db.get_session(uid), await db.is_banned(uid), await db.get_dump_chat(uid), \
            await db.col.count_documents({"id": uid})
    assert run(go()) == ("S", True, -1005, 1)


# ───────────────────────── file hint routing ─────────────────────────
def _video_msg(uid=321, caption=None, size_mb=50):
    from types import SimpleNamespace
    from pyrogram import enums
    m = FakeMsg(text=None, uid=uid)
    m.text, m.caption, m.command = None, caption, None
    for k in ("document", "audio", "photo", "animation", "via_bot", "media_group_id", "voice", "sticker"):
        try:
            setattr(m, k, None)
        except AttributeError:
            pass
    m.entities = m.caption_entities = None
    m.video = SimpleNamespace(file_name="clip.mp4", file_size=size_mb * 2**20, duration=60, width=1280, height=720,
                              mime_type="video/mp4", file_unique_id=f"u{uid}", file_id="f", thumbs=None)
    m.media = enums.MessageMediaType.VIDEO
    return m


def _group0_winner(message):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from pyrogram.handlers import MessageHandler
    from tests.harness import FakeClient
    import run as runner

    class Rec:
        def __init__(self):
            self.h = []

        def add_handler(self, handler, group=0):
            self.h.append((group, handler))
    app = Rec()
    runner.load_plugins(app)
    client = FakeClient()
    client.executor = ThreadPoolExecutor(1)
    client.me.usernames = None

    async def go():
        client.loop = asyncio.get_running_loop()
        for g, h in app.h:
            if g == 0 and isinstance(h, MessageHandler) and await h.filters(client, message):
                cb = getattr(h, "original_callback", None) or h.callback
                return cb.__name__
    return run(go())


def test_unclaimed_private_file_gets_the_hint_and_real_flows_still_win(monkeypatch):
    from renamer import store
    import VideoEncoder.plugins.compress as P
    assert _group0_winner(_video_msg()) == "file_hint"                       # used to be silence
    assert _group0_winner(_video_msg(caption="/compress 480")) not in (None, "file_hint")   # the command wins
    run(store.update(321, template="{title} [{quality}]"))
    assert _group0_winner(_video_msg()) == "incoming_file"                   # auto-rename keeps its files

    async def on(uid):
        return True
    monkeypatch.setattr(P, "_auto_on", on)
    assert _group0_winner(_video_msg()) == "auto_compress"                   # auto-compress keeps its videos


def test_file_hint_is_rate_limited_and_once_per_album():
    from core import filehint
    filehint._last.clear()
    filehint._albums.clear()
    a = _video_msg(uid=654)
    run(filehint.file_hint(None, a))
    assert a.replies and "compress" in a.replies[-1]
    b = _video_msg(uid=654)
    run(filehint.file_hint(None, b))
    assert not b.replies                                                   # cooldown
    c, d = _video_msg(uid=655), _video_msg(uid=656)
    c.media_group_id = d.media_group_id = "album1"
    run(filehint.file_hint(None, c))
    run(filehint.file_hint(None, d))
    assert c.replies and not d.replies

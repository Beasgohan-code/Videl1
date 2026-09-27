"""Full-audit regressions: clone worker delivery (broken / crafted links, FloodWait), custom batches,
sender-less updates, /vset parsing, permanent-link toggle, deploy files."""
import asyncio
import inspect
import json
import os
from types import SimpleNamespace

import pytest
from pyrogram import Client, ContinuePropagation, StopPropagation, enums
from pyrogram.errors import FloodWait
from pyrogram.handlers import MessageHandler

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run

MODULES = load_all()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
LOG_CH = -1001234567890


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    yield


class Msg(FakeMsg):
    """Unset Message fields read as None, like a real pyrogram Message."""
    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return None


# ───────────────────────── clone worker ─────────────────────────
class Stored:
    def __init__(self, mid, flood=0):
        self.id = mid
        self.empty = False
        self.caption = None
        self.media = None
        self.flood = flood
        self.copies = []

    async def copy(self, chat_id, **k):
        if self.flood:
            self.flood -= 1
            raise FloodWait(value=0)
        self.copies.append(chat_id)
        return FakeMsg(text="copy", uid=0)


class WorkerClient(FakeClient):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self.me = SimpleNamespace(id=4242, username="CloneBot", first_name="Clone", mention="Clone", usernames=None)
        self.fetched = []

    async def get_messages(self, chat_id=None, message_ids=None, **k):
        self.fetched.extend(message_ids)
        return [self.store.setdefault(i, Stored(i)) for i in message_ids]


def _start_worker(monkeypatch):
    from filestore.database.main_db import MainDB
    from filestore.worker_bot.engine import worker_engine
    import filestore.worker_bot.engine as eng

    async def noop(self, *a, **k):
        return None

    async def get_me(self):
        return SimpleNamespace(id=4242, username="CloneBot")
    monkeypatch.setattr(Client, "start", noop)
    monkeypatch.setattr(Client, "set_bot_commands", noop)
    monkeypatch.setattr(Client, "get_me", get_me)
    monkeypatch.setattr(eng, "decrypt_token", lambda t: "4242:TOKEN")
    run(MainDB().add_bot(4242, 5, "enc", "CloneBot", LOG_CH))
    run(worker_engine.start_worker(run(MainDB().get_bot(4242))))
    app = worker_engine.get_worker(4242)
    run(asyncio.sleep(0.05))          # pyrogram registers handlers in a task
    return app


async def _dispatch(app, client, msg):
    for grp in sorted(app.dispatcher.groups):
        for hd in app.dispatcher.groups[grp]:
            if not isinstance(hd, MessageHandler) or not hasattr(hd, "filters"):
                continue
            r = hd.filters(client, msg) if hd.filters else True
            if not (await r if inspect.isawaitable(r) else bool(r)):
                continue
            cb = getattr(hd, "original_callback", None) or hd.callback
            try:
                await cb(client, msg)
            except StopPropagation:
                return
            except ContinuePropagation:
                pass
            break


def _stop(app):
    from filestore.worker_bot.engine import worker_engine
    worker_engine.workers.pop(4242, None)


def test_worker_delivers_file_link(monkeypatch):
    from filestore.utils.helpers import encode
    app = _start_worker(monkeypatch)
    try:
        store = {}
        c = WorkerClient(store)
        code = run(encode(f"get-{77 * abs(LOG_CH)}"))
        m = Msg(f"/start {code}", uid=9, client=c)
        run(_dispatch(app, c, m))
        assert c.fetched == [77] and store[77].copies == [9]
    finally:
        _stop(app)


def test_worker_rejects_broken_and_giant_links(monkeypatch):
    from filestore.utils.helpers import encode
    app = _start_worker(monkeypatch)
    try:
        c = WorkerClient({})
        broken = Msg("/start %%%not-base64%%%", uid=9, client=c)
        run(_dispatch(app, c, broken))
        assert "broken" in broken.replies[-1]
        giant = Msg("/start " + run(encode(f"get-{1 * abs(LOG_CH)}-{10 ** 9 * abs(LOG_CH)}")), uid=9, client=c)
        run(_dispatch(app, c, giant))
        assert "too many files" in giant.replies[-1] and c.fetched == []
    finally:
        _stop(app)


def test_worker_retries_floodwait_instead_of_dropping(monkeypatch):
    from filestore.utils.helpers import encode
    app = _start_worker(monkeypatch)
    try:
        store = {i: Stored(i, flood=1 if i == 11 else 0) for i in range(10, 13)}
        c = WorkerClient(store)
        m = Msg("/start " + run(encode(f"get-{10 * abs(LOG_CH)}-{12 * abs(LOG_CH)}")), uid=9, client=c)
        run(_dispatch(app, c, m))
        assert [len(store[i].copies) for i in (10, 11, 12)] == [1, 1, 1]
    finally:
        _stop(app)


def test_custom_batch_makes_scattered_posts_contiguous(monkeypatch):
    """Posts 5 and 20 picked → link must not deliver 6..19."""
    import filestore.worker_bot.link_gen as lg
    ids = iter([5, 20])

    async def fake_get_id(client, message, log_channel_id):
        return next(ids)
    monkeypatch.setattr(lg, "get_message_id", fake_get_id)
    app = _start_worker(monkeypatch)
    try:
        copies = []

        class C(WorkerClient):
            async def copy_message(self, chat_id, from_chat_id, message_id, **k):
                copies.append(message_id)
                return SimpleNamespace(id=100 + len(copies))
        c = C({})
        handlers = [getattr(h, "original_callback", h.callback) for g in app.dispatcher.groups.values() for h in g]
        custom = next(h for h in handlers if h.__name__ == "handle_custom_batch")
        catcher = next(h for h in handlers if h.__name__ == "link_gen_input_catcher")
        cmd = Msg("/custom_batch", uid=5, client=c)

        async def flow():
            task = asyncio.ensure_future(custom(c, cmd))
            for text in ("file-a", "file-b", "/done"):
                await asyncio.sleep(0.01)
                with pytest.raises(StopPropagation):
                    await catcher(c, Msg(text, uid=5, client=c))
            await asyncio.wait_for(task, 2)
        run(flow())
        assert copies == [5, 20]
        from filestore.utils.helpers import decode
        link = cmd.replies[-1]
        code = link.split("start=")[1].split("<")[0]
        assert run(decode(code)) == f"get-{101 * abs(LOG_CH)}-{102 * abs(LOG_CH)}" and "2 ꜰɪʟᴇs" in link
    finally:
        _stop(app)


# ───────────────────────── main bot fixes ─────────────────────────
def test_senderless_messages_stop_early_except_id():
    from core.middleware import senderless_gate
    c = FakeClient()
    post = Msg("/stats", uid=None, chat_type=enums.ChatType.CHANNEL, chat_id=-100999, client=c)
    with pytest.raises(StopPropagation):
        run(senderless_gate(c, post))
    run(senderless_gate(c, Msg("/id", uid=None, chat_type=enums.ChatType.CHANNEL, chat_id=-100999, client=c)))
    anon = Msg("/help", uid=None, chat_type=enums.ChatType.SUPERGROUP, chat_id=-100555, client=c)
    anon.sender_chat = SimpleNamespace(id=-100555)
    with pytest.raises(StopPropagation):
        run(senderless_gate(c, anon))
    assert "anonymous" in anon.replies[-1]
    run(senderless_gate(c, Msg("/help", uid=5, client=c)))     # normal users pass


def test_vset_accepts_username_and_rejects_garbage(monkeypatch):
    import VideoEncoder.plugins.settings as st

    async def ok_chat(event, chat="Both"):
        return True
    monkeypatch.setattr(st, "check_chat", ok_chat)

    class C(FakeClient):
        async def get_users(self, u):
            raise ValueError("nope")
    m = Msg("/vset notauser", uid=111, client=C())
    m.command = ["vset", "notauser"]
    run(st.settings_viewer(m._client, m))
    assert "user ID" in m.replies[-1]


def test_permanent_link_toggle_needs_backend(monkeypatch):
    from filestore.database.main_db import MainDB
    import filestore.main_bot.plugins.my_bots as mb
    monkeypatch.setattr(mb, "BACKEND_API_URL", "")
    run(MainDB().add_bot(77, 5, "enc", "B", LOG_CH))
    q = FakeQuery("toggle_permanent_link_77", uid=5)
    run(mb.toggle_permanent_link_callback(FakeClient(), q))
    assert "BACKEND_API_URL" in q.answers[-1][0]
    assert run(MainDB().get_bot(77))["settings"]["permanent_link"] is False


def test_login_handler_survives_expired_state():
    from saver import session
    session.LOGIN_STATE.pop(5, None)
    run(session.login_handler(FakeClient(), Msg("12345", uid=5)))      # no KeyError


# ───────────────────────── deploy files ─────────────────────────
@pytest.mark.skipif(not __import__("importlib").util.find_spec("yaml"), reason="PyYAML not installed")
def test_render_blueprint_and_compose_parse():
    import yaml
    render = yaml.safe_load(open(os.path.join(REPO, "render.yaml")))
    svc = render["services"][0]
    assert svc["runtime"] == "docker" and svc["healthCheckPath"] == "/health"
    keys = {e["key"] for e in svc["envVars"]}
    assert {"BOT_TOKEN", "API_ID", "API_HASH", "DB_URI", "OWNER_ID"} <= keys
    compose = yaml.safe_load(open(os.path.join(REPO, "docker-compose.yml")))
    assert "videl" in compose["services"]


def test_railway_and_app_json_parse():
    import tomllib
    rw = tomllib.load(open(os.path.join(REPO, "railway.toml"), "rb"))
    assert rw["build"]["builder"] == "DOCKERFILE" and rw["deploy"]["healthcheckPath"] == "/health"
    assert "startCommand" not in rw["deploy"]          # keep the image's tini ENTRYPOINT
    app = json.load(open(os.path.join(REPO, "app.json")))
    assert all(app["env"][k]["required"] for k in ("BOT_TOKEN", "API_ID", "API_HASH", "DB_URI", "OWNER_ID"))


def test_dockerfiles_run_videl():
    root = open(os.path.join(REPO, "Dockerfile")).read()
    inner = open(os.path.join(ROOT, "Dockerfile")).read()
    for df in (root, inner):
        assert "python:3.11" in df and "ffmpeg" in df and 'CMD ["python3", "run.py"]' in df
    assert "COPY VidelBot/" in root


# ───────────────────────── encoder ─────────────────────────
import VideoEncoder.plugins.encode as enc_plugin  # noqa: E402
import VideoEncoder.utils.encoding as encoding  # noqa: E402
import VideoEncoder.utils.helper as enc_helper  # noqa: E402
import VideoEncoder.utils.tasks as enc_tasks  # noqa: E402
from VideoEncoder import data as enc_queue, download_dir  # noqa: E402


@pytest.fixture
def encoder_stubs(monkeypatch):
    calls = []

    async def yes(*a, **k):
        return True

    async def record(message, mode):
        calls.append((message, mode))

    monkeypatch.setattr(enc_plugin, "check_chat", yes)
    monkeypatch.setattr(enc_plugin, "AddUserToDatabase", yes)
    monkeypatch.setattr(enc_plugin, "handle_tasks", record)
    monkeypatch.setattr(enc_plugin.asyncio, "sleep", yes)
    saved = list(enc_queue)
    enc_queue.clear()
    yield calls
    enc_queue[:] = saved


def test_ddl_and_batch_reject_bad_links_without_touching_queue(encoder_stubs):
    running = object()
    enc_queue.append(running)                      # a job is already being processed
    for cmd, fn in (("/ddl", enc_plugin.url_encode), ("/batch", enc_plugin.batch_encode)):
        for text in (cmd, f"{cmd} ftp://x/y.mkv", f"{cmd} javascript:alert(1)"):
            m = Msg(text, uid=5)
            run(fn(None, m))
            assert "must start with http" in m.replies[-1]
    assert enc_queue == [running] and encoder_stubs == []   # the running job was not removed


def test_ddl_valid_link_starts_or_queues(encoder_stubs):
    first, second = Msg("/ddl https://example.com/a.mkv", uid=5), Msg("/ddl HTTP://example.com/b.mkv", uid=6)
    run(enc_plugin.url_encode(None, first))
    run(enc_plugin.url_encode(None, second))
    assert encoder_stubs == [(first, "url")]
    assert enc_queue == [first, second] and "Waiting for queue" in second.replies[-1]


def test_batch_accepts_reply_to_archive(encoder_stubs):
    m = Msg("/batch", uid=5)
    m.reply_to_message = SimpleNamespace(document=SimpleNamespace(file_name="eps.zip"))
    run(enc_plugin.batch_encode(None, m))
    assert encoder_stubs == [(m, "batch")]


@pytest.mark.parametrize("text,expected", [
    ("/ddl https://x.io/video.mkv", "video.mkv"),
    ("/ddl https://x.io/v.mkv | ../../etc/passwd", "passwd"),
    ("/ddl https://x.io/v.mkv | ..\\..\\boot.ini", "boot.ini"),
    ("/ddl https://x.io/v.mkv | .bashrc", "bashrc"),
    ("/ddl https://x.io/v.mkv | a<b>:c?.mkv", "abc.mkv"),
    ("/ddl https://x.io/v.mkv | ..", "downloaded_file"),
    ("/ddl https://x.io/%2E%2E%2Fsecret", "secret"),
    ("/ddl https://x.io/v.mkv | " + "n" * 500, "n" * 200),
])
def test_download_filename_cannot_escape_download_dir(monkeypatch, text, expected):
    seen = {}

    async def fake_url(url, filepath, msg):
        seen["url"], seen["path"] = url, filepath

    monkeypatch.setattr(enc_tasks, "direct_link_generator", lambda url: None)
    monkeypatch.setattr(enc_tasks, "handle_url", fake_url)
    path = run(enc_tasks.handle_download_url(Msg(text, uid=5), Msg("s", uid=5), False))
    assert path == seen["path"] == os.path.join(download_dir, expected)
    assert os.path.dirname(os.path.abspath(path)) == os.path.abspath(download_dir)
    assert seen["url"].startswith("https://x.io/")


def test_failed_download_raises_and_queue_moves_on(monkeypatch):
    class DeadDL:
        def __init__(self, *a, **k): pass
        def start(self, blocking=True): pass
        def isFinished(self): return True
        def isSuccessful(self): return False
        def get_errors(self): return [OSError("HTTP 404")]

    monkeypatch.setattr(enc_helper, "SmartDL", DeadDL)
    with pytest.raises(RuntimeError, match="404"):
        run(enc_helper.handle_url("https://x.io/a.mkv", "/tmp/a.mkv", Msg("s", uid=5)))

    # the error is reported and the next queued job is started
    started = []

    async def boom(message, msg):
        raise RuntimeError("Download failed: HTTP 404")

    real_handle = enc_tasks.handle_tasks

    async def spy(message, mode):
        started.append((message, mode))

    monkeypatch.setattr(enc_tasks, "url_task", boom)
    monkeypatch.setattr(enc_tasks, "delete_downloads", lambda: None)
    first, nxt = Msg("/ddl https://x.io/a", uid=5), Msg("/ddl https://x.io/b", uid=6)
    saved = list(enc_queue)
    enc_queue[:] = [first, nxt]
    try:
        monkeypatch.setattr(enc_tasks, "handle_tasks", spy)   # recursion target inside on_task_complete
        run(real_handle(first, "url"))
    finally:
        enc_queue[:] = saved
    assert any("404" in r for r in first.replies)
    assert started == [(nxt, "url")]


def test_lk21_patch_does_not_break_urljoin_or_aiogram():
    import urllib.parse
    import VideoEncoder.utils.lk21_patch as patch
    assert urllib.parse.urlparse is patch._ORIGINAL_URLPARSE
    assert urllib.parse.urljoin("http://a/b/", "c") == "http://a/b/c"
    assert patch.safe_urlparse("http://[bad", "", True).netloc == "invalid"
    import aiogram.client.session.aiohttp  # noqa: F401  (crashed before the fix)


def test_encode_progress_survives_missing_duration_and_zero_speed(monkeypatch):
    real_sleep = asyncio.sleep

    async def fast(_=0):
        await real_sleep(0)

    class Proc:
        pid, ticks = 1, 0

        @property
        def returncode(self):
            self.ticks += 1
            return None if self.ticks < 4 else 0

    monkeypatch.setattr(encoding.asyncio, "sleep", fast)
    progress = download_dir + "process.txt"
    cases = (("out_time_ms=5000000\nspeed=0\nprogress=continue\n", None, "Done: 5s"),
             ("out_time_ms=20000000\nspeed=2.0x\nprogress=continue\n", 40.0, "50%"),
             ("out_time_ms=90000000\nspeed=N/A\nprogress=continue\n", 40.0, "100%"),
             (None, 40.0, None))
    try:
        for content, duration, expect in cases:
            if os.path.exists(progress):
                os.remove(progress)
            if content:
                with open(progress, "w") as f:
                    f.write(content)

            async def info(_fp, d=duration):
                return d, None
            monkeypatch.setattr(encoding, "media_info", info)
            status = Msg("status", uid=0)
            run(encoding.handle_progress(Proc(), status, Msg("/dl", uid=5), "x.mkv"))
            if expect is None:
                assert status.edits == []
            else:
                assert expect in status.edits[-1]
    finally:
        if os.path.exists(progress):
            os.remove(progress)


def test_encode_keeps_audio_when_probe_fails(monkeypatch, tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"\0" * 64)
    captured = {}

    class Stop(Exception):
        pass

    async def fake_exec(*cmd, **k):
        captured["cmd"] = cmd
        raise Stop

    monkeypatch.setattr(encoding, "get_codec", lambda *a, **k: [])   # ffprobe unavailable / failed
    monkeypatch.setattr(encoding.asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(Stop):
        run(encoding.encode(str(src), Msg("/dl", uid=5), Msg("s", uid=5)))
    cmd = list(captured["cmd"])
    assert "0:a?" in cmd and cmd[cmd.index("0:a?") - 1] == "-map"
    assert "-c:a" in cmd


def test_env_inline_comments_and_quotes_are_cleaned(monkeypatch):
    import config
    for k, v in {"V_A": "240   # seconds", "V_B": "   # only a comment", "V_C": '"Videl #1"',
                 "V_D": "mongodb+srv://u:p@h/db?retryWrites=true#frag", "V_E": "'x y'"}.items():
        monkeypatch.setenv(k, v)
    config._clean_environ()
    assert [os.environ[k] for k in ("V_A", "V_B", "V_C", "V_D", "V_E")] == \
        ["240", "", "Videl #1", "mongodb+srv://u:p@h/db?retryWrites=true#frag", "x y"]


def test_malformed_numeric_env_falls_back_instead_of_crashing(monkeypatch, capsys):
    import config
    monkeypatch.setenv("V_NUM", "4m")
    assert config.env_int("V_NUM", 240) == 240
    assert "V_NUM" in capsys.readouterr().err
    monkeypatch.setenv("V_NUM", "")
    assert config.env_int("V_NUM", 90, empty=0) == 0 and config.env_int("V_NUM", 90) == 90
    monkeypatch.setenv("V_NUM", " 2.5 ")
    assert config.env_float("V_NUM", 1.0) == 2.5
    monkeypatch.delenv("V_NUM")
    assert config.env_int("V_NUM", 7) == 7

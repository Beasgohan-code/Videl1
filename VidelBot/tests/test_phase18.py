"""Phase 18 – audit / debug pass.

• cloud.mail.ru shell injection closed · default HTTP timeouts in the link generators · racaty used the raw text
• background tasks can't be garbage-collected mid-run and their crashes are logged (core/bg.spawn)
• bounded per-user caches (core/bg.trim) · no bare `except:` (it swallowed task cancellation)
• exception text / user names are HTML-escaped before they go into an HTML reply
• timeouts on ffprobe, shorteners and other HTTP sessions · /vupload no longer blocks the loop
"""
import ast
import asyncio
import logging
import pathlib
import re
import types

import pytest

from tests.harness import FakeMsg, load_all, no_botapi, reset_db, run

MODULES = load_all()

from core import bg  # noqa: E402
import VideoEncoder.utils.direct_link_generator as dlg  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = [p for p in ROOT.rglob("*.py") if "tests" not in p.parts and "__pycache__" not in p.parts]


@pytest.fixture(autouse=True)
def fresh():
    run(reset_db())
    no_botapi()
    yield


# ─────────────────────────── core/bg ───────────────────────────
def test_spawn_keeps_a_strong_reference_until_done():
    async def go():
        gate = asyncio.Event()

        async def job():
            await gate.wait()
            return 7

        t = bg.spawn(job(), name="unit-job")
        assert t in bg._TASKS and bg.running() >= 1 and t.get_name() == "unit-job"
        gate.set()
        assert await t == 7
        await asyncio.sleep(0)
        assert t not in bg._TASKS
    run(go())


def test_spawn_logs_crashes_with_the_task_name(caplog):
    async def go():
        async def boom():
            raise RuntimeError("kaboom")
        t = bg.spawn(boom(), name="crashy")
        with pytest.raises(RuntimeError):
            await t
        await asyncio.sleep(0)
    with caplog.at_level(logging.ERROR, logger="videl.bg"):
        run(go())
    assert any("crashy" in r.getMessage() and "kaboom" in r.getMessage() for r in caplog.records)


def test_spawn_ignores_cancellation():
    async def go():
        t = bg.spawn(asyncio.sleep(30), name="sleeper")
        await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        await asyncio.sleep(0)
        assert t not in bg._TASKS
    run(go())


def test_trim_bounds_a_dict_and_respects_the_veto():
    d = {i: i for i in range(100)}
    assert bg.trim(d, 200) == 0 and len(d) == 100
    dropped = bg.trim(d, 50)
    assert dropped == 75 and len(d) == 25 and min(d) == 75          # oldest-inserted go first
    d = {i: (i % 2 == 0) for i in range(100)}                       # only even keys may be removed
    bg.trim(d, 50, lambda k, keep_ok: keep_ok)
    assert all(k % 2 for k in d if k < 99) and all(k in d for k in range(1, 100, 2))


def test_renamer_never_drops_a_held_lock():
    from renamer import engine

    async def go():
        engine._locks.clear()
        held = asyncio.Lock()
        await held.acquire()
        engine._locks[1] = held
        for uid in range(2, 5200):
            engine._locks[uid] = asyncio.Lock()
        bg.trim(engine._locks, 5000, lambda k, lk: not lk.locked() and not getattr(lk, "_waiters", None))
        assert engine._locks.get(1) is held and len(engine._locks) <= 2600
        held.release()
        engine._locks.clear()
    run(go())


def test_no_unreferenced_create_task_left():
    """A bare `asyncio.create_task(x)` statement can be garbage-collected mid-run – use core.bg.spawn."""
    bad = []
    for p in SOURCES:
        for node in ast.walk(ast.parse(p.read_text())):
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                f = node.value.func
                if isinstance(f, ast.Attribute) and f.attr in ("create_task", "ensure_future"):
                    bad.append(f"{p.relative_to(ROOT)}:{node.lineno}")
    assert not bad, bad


# ─────────────────────────── robustness (static) ───────────────────────────
def test_no_bare_except_anywhere():
    bad = [f"{p.relative_to(ROOT)}:{n.lineno}" for p in SOURCES
           for n in ast.walk(ast.parse(p.read_text())) if isinstance(n, ast.ExceptHandler) and n.type is None]
    assert not bad, bad


def test_every_aiohttp_session_has_a_timeout():
    bad = []
    for p in SOURCES:
        for n in ast.walk(ast.parse(p.read_text())):
            if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("ClientSession"):
                if not any(k.arg == "timeout" for k in n.keywords):
                    bad.append(f"{p.relative_to(ROOT)}:{n.lineno}")
    assert not bad, bad


def test_ffprobe_calls_in_encoding_have_timeouts():
    src = (ROOT / "VideoEncoder/utils/encoding.py").read_text()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Call) and ast.unparse(n.func) in ("subprocess.check_output", "subprocess.call",
                                                               "subprocess.run"):
            if "shell" in {k.arg for k in n.keywords}:
                continue                                 # constant font-install strings
            assert any(k.arg == "timeout" for k in n.keywords), f"encoding.py:{n.lineno}"


def test_exception_text_is_escaped_in_html_replies():
    """`<code>{e}</code>` broke the whole reply when the error said e.g. `<Response [403]>`."""
    pat = re.compile(r"""f["'][^"'\n]*<[a-z]+>[^"'\n]*\{e\}""")
    bad = [f"{p.relative_to(ROOT)}:{i}" for p in SOURCES
           for i, line in enumerate(p.read_text().splitlines(), 1)
           if pat.search(line) and "log" not in line.split("(")[0]]
    assert not bad, bad


def test_user_names_are_escaped():
    q = (ROOT / "VideoEncoder/plugins/queue.py").read_text()
    assert "html.escape(str(tasktitle" in q
    assert "html.escape(c.title" in (ROOT / "core/fsub.py").read_text()
    assert "html.escape(str(bot_name))" in (ROOT / "filestore/main_bot/plugins/create_bot.py").read_text()


# ─────────────────────────── link generators ───────────────────────────
def test_cm_ru_never_uses_a_shell(monkeypatch, tmp_path):
    calls = []
    monkeypatch.chdir(tmp_path)
    tool = tmp_path / "vendor" / "cmrudl.py" / "cmrudl"
    tool.parent.mkdir(parents=True)
    tool.write_text("")

    def fake_run(args, **kw):
        calls.append((args, kw))
        return types.SimpleNamespace(stdout='{"download": "https://dl.example/f"}\n')
    monkeypatch.setattr(dlg.subprocess, "run", fake_run)
    evil = "https://cloud.mail.ru/public/abc;touch${IFS}/tmp/pwned"
    assert dlg.cm_ru(evil) == "https://dl.example/f"
    args, kw = calls[0]
    assert isinstance(args, list) and not kw.get("shell") and kw.get("timeout")
    assert args[-1] == "https://cloud.mail.ru/public/abc"           # metacharacters never reach the tool
    assert "--" in args
    assert "os.popen(" not in (ROOT / "VideoEncoder/utils/direct_link_generator.py").read_text()


def test_cm_ru_without_the_tool_says_so(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(dlg.DirectDownloadLinkException, match="aren't supported"):
        dlg.cm_ru("https://cloud.mail.ru/public/abc")


def test_requests_get_a_default_timeout(monkeypatch):
    seen = {}

    def fake_get(*a, **k):
        seen.update(k)
        return "ok"
    monkeypatch.setattr(dlg._requests, "get", fake_get)
    assert dlg.requests.get("https://x.example") == "ok" and seen["timeout"] == dlg.HTTP_TIMEOUT
    dlg.requests.get("https://x.example", timeout=3)
    assert seen["timeout"] == 3                                    # an explicit value wins
    assert dlg.requests.Session is dlg._requests.Session           # everything else passes through


def test_racaty_scrapes_the_extracted_link_and_reports_layout_changes(monkeypatch):
    urls = []

    class Scraper:
        def get(self, url, **k):
            urls.append(url)
            assert k.get("timeout")
            return types.SimpleNamespace(text="<html>no form here</html>")
    monkeypatch.setattr(dlg.cloudscraper, "create_scraper", lambda: Scraper())
    with pytest.raises(dlg.DirectDownloadLinkException, match="layout"):
        dlg.racaty("grab this https://racaty.net/abc123 please")
    assert urls == ["https://racaty.net/abc123"]


# ─────────────────────────── /vupload ───────────────────────────
def test_vupload_missing_file_answers_instead_of_crashing(monkeypatch):
    import VideoEncoder.plugins.upload as up

    async def ok(*a, **k):
        return True
    monkeypatch.setattr(up, "check_chat", ok)
    m = FakeMsg("/vupload /definitely/not/here.mkv", uid=111)
    m.command = ["vupload", "/definitely/not/here.mkv"]
    run(up.videoupload(None, m))
    assert "File not found." in " ".join(str(r) for r in m.replies)


def test_vupload_probes_run_off_the_event_loop():
    src = (ROOT / "VideoEncoder/plugins/upload.py").read_text()
    body = src.split("async def videoupload", 1)[1].split("\n@", 1)[0]
    assert "asyncio.to_thread(get_duration" in body and "asyncio.to_thread(get_thumbnail" in body
    assert "os.path.isfile(file)" in body

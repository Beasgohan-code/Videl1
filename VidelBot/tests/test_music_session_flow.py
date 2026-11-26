"""Music clone assistant-session setup: paste + guided-login flows."""
import re
import time

import pytest
from cryptography.fernet import Fernet
from pyrogram import enums

from tests.harness import FakeClient, FakeMsg, FakeQuery, load_all, reset_db, run

load_all()
from filestore.database.main_db import MainDB
from filestore.main_bot.plugins import music_session


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    run(reset_db())
    music_session.PENDING.clear()
    music_session.EXPIRED_FLOW_MARKERS.clear()
    music_session.UNCERTAIN_LOGIN_CLIENTS.clear()
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    yield
    music_session.PENDING.clear()
    music_session.EXPIRED_FLOW_MARKERS.clear()
    music_session.UNCERTAIN_LOGIN_CLIENTS.clear()


async def _bot(bot_id=4242, owner=5):
    db = MainDB()
    await db.add_bot(bot_id, owner, "enc", "CloneBot", -1001)
    await db.set_bot_active(bot_id, False)
    return db


def _query(data, uid=5):
    q = FakeQuery(data, uid=uid)
    pattern = {
        "clmusic_": r"^clmusic_(\d+)$",
        "clmsess_": r"^clmsess_(\w+)_(\d+)$",
    }
    for prefix, pat in pattern.items():
        if data.startswith(prefix):
            q.matches = [re.match(pat, data)]
    return q


def test_session_helpers():
    from filestore.main_bot.plugins.music_session import looks_like_session, mask_session

    assert looks_like_session("A" * 60)
    assert not looks_like_session("A" * 4097)
    assert not looks_like_session("short")
    assert not looks_like_session("has spaces " + "A" * 60)
    assert mask_session("1234567890abcdef") == "123456…cdef"


def test_prompt_offers_paste_and_create():
    async def go():
        await _bot()
        q = _query("clmusic_4242")
        await music_session.music_prompt(FakeClient(), q)
        shown = " ".join(str(e) for e in q.message.edits + q.message.replies)
        assert "assistant account" in shown and "Paste" in shown
        assert 5 not in music_session.PENDING  # prompt alone starts no flow

    run(go())


def test_music_profile_cannot_be_selected_before_encrypted_session_exists():
    async def go():
        db = await _bot()
        result = await music_session.select_music_profile(4242, 5)
        doc = await db.get_bot(4242)
        assert result == "no_session"
        assert doc.get("clone_profile", "filestore") == "filestore"
        assert doc["is_active"] is False
    run(go())


def test_paste_flow_saves_encrypted_session_and_selects_music():
    async def go():
        db = await _bot()
        q = _query("clmsess_paste_4242")
        await music_session.music_session_action(FakeClient(), q)
        assert music_session.PENDING[5]["step"] == "paste"

        session = "A" * 80 + "sessionstring"
        msg = FakeMsg(session, uid=5)
        msg.chat = type("C", (), {"id": 5, "type": enums.ChatType.PRIVATE})()
        await music_session.music_session_message(FakeClient(), msg)
        doc = await db.get_bot(4242)
        assert doc["clone_profile"] == "music"
        assert doc["is_active"] is True
        assert "music_setup_pending" not in doc
        assert doc["music_session_encrypted"].startswith("enc:")
        assert session not in doc["music_session_encrypted"]
        assert 5 not in music_session.PENDING
        assert msg.deleted is True
        return msg

    msg = run(go())
    assert any("Music mode is ready" in t for t in msg.replies)


def test_guided_login_flow_with_fake_client(monkeypatch):
    from types import SimpleNamespace

    class FakeLogin:
        def __init__(self):
            self.disconnected = False

        async def connect(self):
            pass

        async def send_code(self, phone):
            return SimpleNamespace(phone_code_hash="hash123")

        async def sign_in(self, phone, code_hash, code):
            assert code == "12345"

        async def export_session_string(self):
            return "B" * 90

        async def disconnect(self):
            self.disconnected = True

    login = FakeLogin()
    monkeypatch.setattr(music_session, "_make_login_client", lambda: login)

    async def go():
        db = await _bot()
        q = _query("clmsess_create_4242")
        await music_session.music_session_action(FakeClient(), q)
        assert music_session.PENDING[5]["step"] == "phone"

        m1 = FakeMsg("+919876543210", uid=5)
        await music_session.music_session_message(FakeClient(), m1)
        assert music_session.PENDING[5]["step"] == "code"
        assert any("Code sent" in t for t in m1.replies)
        assert m1.deleted is True

        m2 = FakeMsg("12345", uid=5)
        await music_session.music_session_message(FakeClient(), m2)
        doc = await db.get_bot(4242)
        assert doc["clone_profile"] == "music"
        assert doc["is_active"] is True
        assert "music_setup_pending" not in doc
        assert doc["music_session_encrypted"].startswith("enc:")
        assert login.disconnected is True
        assert m2.deleted is True
        assert any("Music mode is ready" in t for t in m2.replies)

        duplicate_code = FakeMsg("12345", uid=5)
        await music_session.music_session_message(FakeClient(), duplicate_code)
        assert duplicate_code.deleted is True
        assert any("not processed" in text.lower() for text in duplicate_code.replies)

    run(go())


def test_other_users_and_strangers_cannot_use_the_flow():
    async def go():
        db = await _bot()
        q = _query("clmusic_4242", uid=999)
        await music_session.music_prompt(FakeClient(), q)
        assert any("denied" in (t or "").lower() for t, _ in q.answers)
        assert 999 not in music_session.PENDING

    run(go())


def test_session_write_is_owner_scoped_and_transfer_during_flow_does_not_leak(monkeypatch):
    async def go():
        db = await _bot()
        assert not await db.set_music_session(4242, 999, "enc:attacker-session")
        assert "music_session_encrypted" not in await db.get_bot(4242)

        q = _query("clmsess_paste_4242")
        await music_session.music_session_action(FakeClient(), q)
        await db.transfer_owner(4242, 6)
        msg = FakeMsg("S" * 80, uid=5)
        msg.chat = type("C", (), {"id": 5, "type": enums.ChatType.PRIVATE})()
        await music_session.music_session_message(FakeClient(), msg)
        current = await db.get_bot(4242)
        assert msg.deleted is True
        assert "music_session_encrypted" not in current
        assert current["owner_id"] == 6 and current.get("clone_profile", "filestore") == "filestore"
        assert any("denied" in text.lower() for text in msg.replies)
    run(go())


def test_setup_cancels_if_secret_message_cannot_be_deleted():
    async def go():
        db = await _bot()
        q = _query("clmsess_paste_4242")
        await music_session.music_session_action(FakeClient(), q)
        msg = FakeMsg("T" * 90, uid=5)
        async def fail_delete():
            raise RuntimeError("private delete failure")
        msg.delete = fail_delete
        await music_session.music_session_message(FakeClient(), msg)
        doc = await db.get_bot(4242)
        assert doc.get("clone_profile", "filestore") == "filestore"
        assert "music_session_encrypted" not in doc
        assert 5 not in music_session.PENDING
        assert any("login was cancelled" in text.lower() for text in msg.replies)
    run(go())


def test_expired_login_flow_disconnects_its_temporary_client():
    import time

    class Login:
        def __init__(self):
            self.disconnected = False
        async def disconnect(self):
            self.disconnected = True

    async def go():
        login = Login()
        music_session.PENDING[5] = {
            "bot_id": 4242, "step": "code", "ts": time.time() - music_session.FLOW_TTL - 1,
            "login_client": login,
        }
        assert await music_session._state(5) is None
        assert login.disconnected is True
        assert 5 not in music_session.PENDING
    run(go())


def test_expired_paste_flow_deletes_late_session_without_processing():
    import time

    async def go():
        db = await _bot()
        music_session.PENDING[5] = {
            "bot_id": 4242, "step": "paste", "ts": time.time() - music_session.FLOW_TTL - 1,
        }
        session = "L" * 85
        msg = FakeMsg(session, uid=5)
        await music_session.music_session_message(FakeClient(), msg)
        doc = await db.get_bot(4242)
        assert msg.deleted is True
        assert "music_session_encrypted" not in doc
        assert "clone_profile" not in doc
        assert 5 not in music_session.PENDING and 5 not in music_session.EXPIRED_FLOW_MARKERS
        assert any("not processed" in text.lower() for text in msg.replies)
    run(go())


def test_failed_login_client_cleanup_blocks_session_save_and_retries():
    class FlakyLogin:
        def __init__(self):
            self.calls = 0
        async def disconnect(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("uncertain disconnect")

    async def go():
        db = await _bot()
        login = FlakyLogin()
        state = {"user_id": 5, "bot_id": 4242, "step": "password", "login_client": login}
        music_session.PENDING[5] = state
        msg = FakeMsg("", uid=5)
        await music_session._finish_flow(FakeClient(), msg, state, "A" * 80)
        assert "music_session_encrypted" not in await db.get_bot(4242)
        assert music_session.UNCERTAIN_LOGIN_CLIENTS[5] is login
        assert any("not saved" in text.lower() for text in msg.replies)
        await music_session._prune_expired_flows()
        assert 5 not in music_session.UNCERTAIN_LOGIN_CLIENTS
        assert login.calls == 2
    run(go())


def test_simultaneous_login_inputs_are_serialized_per_owner(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    class Login:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.sign_ins = 0
        async def sign_in(self, *_args):
            self.sign_ins += 1
            self.entered.set()
            await self.release.wait()
        async def export_session_string(self): return "R" * 90
        async def disconnect(self): pass

    async def go():
        db = await _bot()
        login = Login()
        music_session.PENDING[5] = {
            "user_id": 5, "bot_id": 4242, "step": "code", "ts": time.time(),
            "login_client": login, "phone": "+919876543210", "phone_code_hash": "hash",
        }
        first, second = FakeMsg("12345", uid=5), FakeMsg("67890", uid=5)
        task1 = asyncio.create_task(music_session.music_session_message(FakeClient(), first))
        await login.entered.wait()
        task2 = asyncio.create_task(music_session.music_session_message(FakeClient(), second))
        await asyncio.sleep(0)
        login.release.set()
        await asyncio.gather(task1, task2)
        saved = await db.get_bot(4242)
        assert login.sign_ins == 1
        assert first.deleted is True and second.deleted is True
        assert saved["clone_profile"] == "music"
        assert any("not processed" in text.lower() for text in second.replies)
    run(go())

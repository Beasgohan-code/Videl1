"""Music clone assistant-session setup: paste + guided-login flows."""
import re

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
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    yield
    music_session.PENDING.clear()


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
        assert doc["music_session_encrypted"].startswith("enc:")
        assert session not in doc["music_session_encrypted"]
        assert 5 not in music_session.PENDING
        return msg

    msg = run(go())
    assert any("Music mode is on" in t for t in msg.replies)


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

        m2 = FakeMsg("12345", uid=5)
        await music_session.music_session_message(FakeClient(), m2)
        doc = await db.get_bot(4242)
        assert doc["clone_profile"] == "music"
        assert doc["music_session_encrypted"].startswith("enc:")
        assert login.disconnected is True
        assert any("Music mode is on" in t for t in m2.replies)

    run(go())


def test_other_users_and_strangers_cannot_use_the_flow():
    async def go():
        db = await _bot()
        q = _query("clmusic_4242", uid=999)
        await music_session.music_prompt(FakeClient(), q)
        assert any("denied" in (t or "").lower() for t, _ in q.answers)
        assert 999 not in music_session.PENDING

    run(go())

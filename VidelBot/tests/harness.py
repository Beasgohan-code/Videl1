"""
Offline test harness: in-memory MongoDB (mongomock-motor) + a fake Telegram client
and fake messages / callback queries, so real handlers run end-to-end without network.

    pip install -r requirements-dev.txt
    python -m pytest tests -q
"""
import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

os.environ.update(
    BOT_TOKEN="123456:TEST", API_ID="1", API_HASH="x", DB_URI="mongodb://localhost:27017",
    OWNER_ID="111", ADMINS="222", LOG_CHANNEL="-100123", START_PIC="https://example.com/pic.jpg",
    ENCRYPTION_KEY="", KEEP_ALIVE_URL="", STARS_PLANS="30:100 90:250 0:500",
    AIOGRAM_ENABLED="False",   # bridge tests switch it on with a recording session
)

import motor.motor_asyncio  # noqa: E402
from mongomock_motor import AsyncMongoMockClient  # noqa: E402

motor.motor_asyncio.AsyncIOMotorClient = AsyncMongoMockClient

from pyrogram import enums, raw  # noqa: E402
from pyrogram.parser import Parser  # noqa: E402
from pyrogram.types import CallbackQuery, Message, User  # noqa: E402

LOOP = asyncio.new_event_loop()
asyncio.set_event_loop(LOOP)


def run(coro):
    return LOOP.run_until_complete(coro)


class FakeUser(User):
    def __init__(self, uid=5, first_name="Tester", username="tester"):
        self.id = uid
        self.first_name = first_name
        self.last_name = None
        self.username = username
        self.is_bot = False
        self.is_premium = False
        self.usernames = None   # real pyrogram Users have it (filters.user reads it)
        self.language_code = "en"

    @property
    def mention(self):
        return f'<a href="tg://user?id={self.id}">{self.first_name}</a>'


class FakeMsg(Message):
    _next_id = 100

    def __init__(self, text="/start", uid=5, chat_type=enums.ChatType.PRIVATE, chat_id=None, client=None):
        FakeMsg._next_id += 1
        self.id = FakeMsg._next_id
        self.from_user = FakeUser(uid) if uid else None
        self.chat = SimpleNamespace(id=chat_id if chat_id is not None else uid, type=chat_type,
                                    title="Chat", username=None)
        self.text = text
        self.caption = None
        self.media = None
        self.command = text[1:].split() if text and text.startswith("/") else None
        self.reply_to_message = None
        self.successful_payment = None
        self.sender_chat = None
        self.outgoing = False
        self.replies = []      # texts sent in reply / edits
        self.sent = []         # FakeMsg objects returned by reply_text (for later edits)
        self.edits = []
        self.reactions = []
        self.deleted = False
        self._client = client

    async def reply_text(self, text, *a, **k):
        self.replies.append(text)
        sent = FakeMsg(text=text, uid=0, chat_id=self.chat.id)
        self.sent.append(sent)
        return sent

    reply = reply_text

    async def reply_document(self, doc, *a, **k):
        self.replies.append(("document", k.get("caption")))
        return FakeMsg(text=None, uid=0, chat_id=self.chat.id)

    async def reply_photo(self, photo, *a, **k):
        self.replies.append(("photo", k.get("caption")))
        return FakeMsg(text=None, uid=0, chat_id=self.chat.id)

    async def edit_text(self, text, *a, **k):
        self.edits.append(text)
        self.text = text
        return self

    edit = edit_text

    async def edit_caption(self, text, *a, **k):
        self.edits.append(text)
        return self

    async def edit_reply_markup(self, markup=None, *a, **k):
        self.edits.append(("markup", markup))
        return self

    async def react(self, emoji=None, big=False, **k):
        self.reactions.append(emoji)

    async def delete(self, *a, **k):
        self.deleted = True


class FakeQuery(CallbackQuery):
    def __init__(self, data, uid=5, message=None):
        self.data = data
        self.id = "q1"
        self.from_user = FakeUser(uid)
        self.message = message or FakeMsg(text="menu", uid=uid)
        self.message.from_user = FakeUser(999, "Videl", "VidelBot")
        self.answers = []
        self.matches = []

    async def answer(self, text=None, show_alert=False, **k):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, *a, **k):
        self.message.edits.append(text)

    async def edit_message_reply_markup(self, markup=None, *a, **k):
        self.message.edits.append(("markup", markup))


class FakeClient:
    """Records every API call. Unknown methods are accepted and return a FakeMsg."""

    def __init__(self):
        self.calls = []
        self.parse_mode = enums.ParseMode.DEFAULT
        self.parser = Parser(self)
        self.me = SimpleNamespace(id=123456, username="VidelBot", first_name="Videl", mention="Videl",
                                  is_bot=True)
        self.members = {}        # (chat, uid) -> ChatMemberStatus | Exception
        self.fail = set()        # method names that should raise
        self.is_connected = True

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        async def _call(*a, **k):
            self.calls.append((name, a, k))
            if name in self.fail:
                raise RuntimeError(f"{name} failed (test)")
            return FakeMsg(text=k.get("text") or (a[1] if len(a) > 1 and isinstance(a[1], str) else ""),
                           uid=0)
        return _call

    def called(self, name):
        return [c for c in self.calls if c[0] == name]

    async def get_me(self):
        self.calls.append(("get_me", (), {}))
        if "get_me" in self.fail:
            raise ConnectionError("offline (test)")
        return self.me

    async def get_chat_member(self, chat, uid):
        self.calls.append(("get_chat_member", (chat, uid), {}))
        v = self.members.get((chat, uid), enums.ChatMemberStatus.LEFT)
        if isinstance(v, Exception):
            raise v
        return SimpleNamespace(status=v, is_member=v != enums.ChatMemberStatus.LEFT)

    async def get_chat(self, chat):
        self.calls.append(("get_chat", (chat,), {}))
        return SimpleNamespace(id=chat if isinstance(chat, int) else -1009, title=f"Chan {chat}", username=None)

    async def create_chat_invite_link(self, chat, **k):
        self.calls.append(("create_chat_invite_link", (chat,), k))
        return SimpleNamespace(invite_link=f"https://t.me/+invite{abs(hash(chat)) % 1000}")

    async def resolve_peer(self, peer):
        return raw.types.InputPeerUser(user_id=int(peer) if str(peer).lstrip("-").isdigit() else 1, access_hash=0)

    async def invoke(self, query, *a, **k):
        self.calls.append(("invoke", (query,), {}))
        return raw.types.Updates(updates=[], users=[], chats=[], date=0, seq=0)


async def reset_db():
    """Wipe every in-memory database between tests (clients are kept so module-level DB objects stay valid)."""
    import filestore.database.mongo as mongo
    from core.db import vdb
    from database.db import db as saver_db
    from VideoEncoder.utils.database.access_db import db as enc_db
    for client in {id(c): c for c in (mongo.get_motor_client(), saver_db._client, enc_db._client)}.values():
        for name in await client.list_database_names():
            await client.drop_database(name)
    vdb._banned.clear()
    vdb._known.clear()
    vdb._settings = {}
    vdb._settings_ts = 0.0


def enable_blocking_logs():
    from core import botlog
    botlog.BLOCKING = True
    botlog._start_seen.clear()


def load_all():
    """Import every plugin module the way run.py does (fails loudly on import errors)."""
    import importlib
    from pathlib import Path

    import run
    mods = []
    for root in run.PLUGIN_ROOTS:
        for path in sorted(Path(root).rglob("*.py")):
            if path.stem in run.SKIP or "__pycache__" in path.parts:
                continue
            mods.append(importlib.import_module(".".join(path.with_suffix("").parts)))
    enable_blocking_logs()
    return mods


# ───────────────────────── aiogram (Bot API) test session ─────────────────────────
from aiogram.client.session.base import BaseSession  # noqa: E402


class RecordingSession(BaseSession):
    """Records every Bot API request (method object + the exact form payload the
    real aiohttp session would send) and returns canned results.

    responses: {"SendMessage": value | callable(method)}; fail: {"GetMe": Exception}
    """

    def __init__(self, responses=None, fail=None):
        super().__init__()
        self.requests = []
        self.payloads = []
        self.responses = responses or {}
        self.fail = fail or {}

    async def make_request(self, bot, method, timeout=None):
        files = {}
        payload = {}
        for key, value in method.model_dump(warnings=False).items():
            value = self.prepare_value(value, bot=bot, files=files)
            if value:
                payload[key] = _maybe_json(value)
        payload.update({k: f"<file {k}>" for k in files})
        name = type(method).__name__
        self.requests.append(method)
        self.payloads.append((name, payload))
        if name in self.fail:
            raise self.fail[name]
        r = self.responses.get(name, _default_result(name))
        return r(method) if callable(r) else r

    def names(self):
        return [n for n, _ in self.payloads]

    def last(self, name):
        for n, p in reversed(self.payloads):
            if n == name:
                return p
        return None

    async def close(self):
        pass

    async def stream_content(self, *a, **k):  # pragma: no cover
        yield b""


def _maybe_json(v):
    """Form values are strings on the wire; decode JSON ones for easy asserts."""
    import json
    try:
        return json.loads(v)
    except Exception:
        return v


def _default_result(name):
    from aiogram import types as at
    msg = at.Message(message_id=1, date=0, chat=at.Chat(id=1, type="private"))
    return {"SendMessage": msg, "SendRichMessage": msg, "EditMessageText": msg,
            "GetMe": at.User(id=123456, is_bot=True, first_name="Videl", username="VidelBot", can_manage_bots=True),
            "CreateInvoiceLink": "https://t.me/$invoice", "GetMyStarBalance": at.StarAmount(amount=4321),
            "GetStarTransactions": at.StarTransactions(transactions=[])}.get(name, True)


def use_botapi(responses=None, fail=None) -> RecordingSession:
    from core import botapi
    s = RecordingSession(responses, fail)
    botapi.install(s, enable=True)
    return s


def no_botapi():
    from core import botapi
    botapi.install(None, enable=False)

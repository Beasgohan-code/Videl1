"""
Live reply streaming — Bot API 9.5+ ``sendMessageDraft`` + WzGram Layer 232 ``send_rich_message_draft``
with native **Stop Generating** (``MessageGenerationStopped``) support.

Telegram lets a bot show an animated *draft* of the message it is about to
send (private chats only). Updates that reuse the same ``draft_id`` animate
smoothly, empty text renders Telegram's native *“Thinking…”* placeholder, and
WzGram's ``can_stop=True`` offers Telegram's native **Stop** button so users
can cancel long tasks mid-stream!
"""
import asyncio
import contextlib
import logging
import random
import re

from pyrogram import Client, enums, raw, utils
from pyrogram.types import InputRichMessage

from config import STREAM_REPLIES

log = logging.getLogger("videl.stream")

DRAFT_TTL = 30          # seconds a draft stays visible (Telegram limit)
REFRESH_EVERY = 20      # re-send the placeholder before it expires
_disabled = False       # flipped when Telegram says drafts are not allowed

# Active stoppable draft sessions: (chat_id, draft_id) -> asyncio.Event
_STOP_EVENTS: dict[tuple[int, int], asyncio.Event] = {}
_CHAT_LATEST_DRAFT: dict[int, int] = {}


def enabled() -> bool:
    return STREAM_REPLIES and not _disabled


def _private(chat_id) -> bool:
    try:
        return int(chat_id) > 0
    except (TypeError, ValueError):
        return False


def new_draft_id() -> int:
    return random.randint(1, 2**31 - 2)


def register_stop_event(chat_id: int, draft_id: int) -> asyncio.Event:
    ev = _STOP_EVENTS.get((int(chat_id), int(draft_id)))
    if ev is None:
        ev = asyncio.Event()
        _STOP_EVENTS[(int(chat_id), int(draft_id))] = ev
    _CHAT_LATEST_DRAFT[int(chat_id)] = int(draft_id)
    if len(_STOP_EVENTS) > 1000:
        oldest = next(iter(_STOP_EVENTS))
        _STOP_EVENTS.pop(oldest, None)
    return ev


def unregister_stop_event(chat_id: int, draft_id: int) -> None:
    _STOP_EVENTS.pop((int(chat_id), int(draft_id)), None)
    if _CHAT_LATEST_DRAFT.get(int(chat_id)) == int(draft_id):
        _CHAT_LATEST_DRAFT.pop(int(chat_id), None)


def is_stopped(chat_id: int, draft_id: int | None = None) -> bool:
    cid = int(chat_id)
    did = int(draft_id) if draft_id is not None else _CHAT_LATEST_DRAFT.get(cid)
    if did is None:
        return False
    ev = _STOP_EVENTS.get((cid, did))
    return bool(ev and ev.is_set())


@Client.on_message_generation_stopped()
async def on_generation_stopped(client: Client, update):
    """Handle Telegram's native 'Stop Generating' button on a streaming draft."""
    chat = getattr(update, "chat", None)
    cid = getattr(chat, "id", None)
    did = getattr(update, "draft_id", None)
    if cid is None:
        return
    if did is not None and (int(cid), int(did)) in _STOP_EVENTS:
        _STOP_EVENTS[(int(cid), int(did))].set()
    latest = _CHAT_LATEST_DRAFT.get(int(cid))
    if latest is not None and (int(cid), latest) in _STOP_EVENTS:
        _STOP_EVENTS[(int(cid), latest)].set()
    log.info(f"⏹ User stopped draft generation in chat {cid} (draft_id={did})")


async def draft(client, chat_id, text: str = "", draft_id: int = None,
                parse_mode=enums.ParseMode.HTML, can_stop: bool = False) -> int | None:
    """Show/update a message draft. Returns the draft id (or None if skipped/stopped)."""
    global _disabled
    if not enabled() or not _private(chat_id):
        return None
    draft_id = draft_id or new_draft_id()
    if is_stopped(chat_id, draft_id):
        return None

    # 1) documented Bot API method through aiogram (sendMessageDraft)
    from core import botapi
    if botapi.enabled() and botapi.is_main(client) and await botapi.draft(chat_id, draft_id % 2**31 or 1, text or ""):
        return draft_id

    # 2) WzGram Layer 232 native send_rich_message_draft (supports Rich HTML + can_stop button)
    from core.ui import is_wzgram_client
    if is_wzgram_client(client) and hasattr(client, "send_rich_message_draft"):
        try:
            irm = InputRichMessage(html=f"<p>{text}</p>" if text and not text.startswith("<") else (text or None)) if text else None
            ok = await client.send_rich_message_draft(
                chat_id,
                draft_id,
                rich_message=irm,
                can_stop=can_stop or None,
            )
            if ok:
                return draft_id
        except Exception as e:
            log.debug(f"send_rich_message_draft fallback ({type(e).__name__}): {e}")

    # 3) MTProto SetTyping + SendMessageTextDraftAction fallback
    try:
        message, entities = text or "", []
        if message:
            parsed = await utils.parse_text_entities(client, message, parse_mode, None)
            message, entities = parsed["message"], parsed["entities"] or []
        await client.invoke(raw.functions.messages.SetTyping(
            peer=await client.resolve_peer(chat_id),
            action=raw.types.SendMessageTextDraftAction(
                random_id=draft_id,
                text=raw.types.TextWithEntities(text=message, entities=entities),
            ),
        ))
        return draft_id
    except Exception as e:  # noqa: BLE001 – never break a reply because of a preview
        sig = f"{getattr(e, 'ID', '')} {type(e).__name__} {e}".upper()
        if any(k in sig for k in _FATAL):
            _disabled = True
            log.info(f"Message drafts not supported here ({type(e).__name__}) – streaming disabled.")
        else:
            log.debug(f"draft failed ({type(e).__name__}): {e}")
        return None


_FATAL = ("BOT_METHOD_INVALID", "BOTMETHODINVALID", "CONSTRUCTOR_INVALID", "CONSTRUCTORINVALID")


def _visible_len(html: str) -> int:
    return len(re.sub(r"<[^>]+>", "", html))


def chunks(text: str, steps: int = 4) -> list[str]:
    """Split *text* into growing prefixes on line/word boundaries without
    cutting through an HTML tag or entity (so every prefix still parses)."""
    text = text or ""
    if steps <= 1 or len(text) < 40:
        return [text]
    cuts, in_tag, in_ent = [], False, False
    for i, ch in enumerate(text):
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif ch == "&":
            in_ent = True
        elif ch == ";" and in_ent:
            in_ent = False
        elif ch in "\n " and not in_tag and not in_ent:
            cuts.append(i)
    if not cuts:
        return [text]
    out = []
    for k in range(1, steps):
        target = len(text) * k // steps
        cut = min(cuts, key=lambda c: abs(c - target))
        prefix = _close_tags(text[:cut])
        if prefix and (not out or out[-1] != prefix):
            out.append(prefix)
    out.append(text)
    return out


_TAG = re.compile(r"<(/?)([a-zA-Z0-9-]+)[^>]*?(/?)>")


def _close_tags(fragment: str) -> str:
    """Append closing tags for any tags left open in an HTML fragment."""
    stack = []
    for m in _TAG.finditer(fragment):
        closing, name, selfclose = m.group(1), m.group(2).lower(), m.group(3)
        if selfclose or name == "br":
            continue
        if closing:
            if name in stack:
                while stack and stack.pop() != name:
                    pass
        else:
            stack.append(name)
    return fragment + "".join(f"</{t}>" for t in reversed(stack))


async def typewriter(client, chat_id, text: str, steps: int = 3, delay: float = 0.25) -> None:
    """Animate *text* growing in the chat's draft area. Honors Stop Generating."""
    if not enabled() or not _private(chat_id):
        return
    did = new_draft_id()
    ev = register_stop_event(chat_id, did)
    try:
        for part in chunks(text, steps)[:-1]:
            if ev.is_set() or await draft(client, chat_id, part, did, can_stop=True) is None:
                return
            await asyncio.sleep(delay)
    finally:
        unregister_stop_event(chat_id, did)


@contextlib.asynccontextmanager
async def thinking(client, chat_id, label: str = "", can_stop: bool = False):
    """Show the native “Thinking…” placeholder (empty draft) – or *label* –
    while the body runs; refreshed so it never expires on slow work."""
    if not enabled() or not _private(chat_id):
        yield None
        return
    did = new_draft_id()
    ev = register_stop_event(chat_id, did)
    await draft(client, chat_id, label, did, can_stop=can_stop)

    async def _keep():
        try:
            while not ev.is_set():
                await asyncio.sleep(REFRESH_EVERY)
                if ev.is_set() or await draft(client, chat_id, label, did, can_stop=can_stop) is None:
                    return
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_keep())
    try:
        yield ev
    finally:
        task.cancel()
        unregister_stop_event(chat_id, did)


async def reply(message, text: str, **kwargs):
    """``message.reply_text`` with a short typewriter preview first."""
    await typewriter(message._client, message.chat.id, text)
    return await message.reply_text(text, **kwargs)


class Progress:
    """Feedback for a slow command with multi-step draft updates & Stop support."""

    def __init__(self, message, placeholder: str, can_stop: bool = False):
        self.message = message
        self.placeholder = placeholder
        self.can_stop = can_stop
        self.msg = None
        self._cm = None
        self.draft_id: int | None = None
        self.stop_event: asyncio.Event | None = None

    @property
    def stopped(self) -> bool:
        return bool(self.stop_event and self.stop_event.is_set())

    async def __aenter__(self):
        if enabled() and _private(self.message.chat.id):
            self.draft_id = new_draft_id()
            self.stop_event = register_stop_event(self.message.chat.id, self.draft_id)
            await draft(self.message._client, self.message.chat.id, "", self.draft_id, can_stop=self.can_stop)
        else:
            self.msg = await self.message.reply_text(self.placeholder)
        return self

    async def step(self, html_label: str):
        """Update the live streaming draft (or edit the placeholder message in groups)."""
        if self.stopped:
            return
        if self.draft_id is not None:
            await draft(self.message._client, self.message.chat.id, html_label, self.draft_id, can_stop=self.can_stop)
        elif self.msg is not None:
            try:
                await self.msg.edit_text(html_label)
            except Exception:
                pass

    async def finish(self, text: str, **kwargs):
        if self.draft_id is not None:
            unregister_stop_event(self.message.chat.id, self.draft_id)
            self.draft_id = None
        if self.msg is not None:
            return await self.msg.edit_text(text, **kwargs)
        return await self.message.reply_text(text, **kwargs)

    async def finish_doc(self, doc, reply_markup=None):
        """Finish a streaming Progress by sending/editing a rich ``Doc``."""
        from core import rich
        if self.draft_id is not None:
            unregister_stop_event(self.message.chat.id, self.draft_id)
            self.draft_id = None
        if self.msg is not None:
            return await rich.edit(self.msg, doc, reply_markup=reply_markup)
        return await rich.reply(self.message, doc, reply_markup=reply_markup)

    async def __aexit__(self, *exc):
        if self.draft_id is not None:
            unregister_stop_event(self.message.chat.id, self.draft_id)
            self.draft_id = None
        return False


def progress(message, placeholder: str = "⏳ <i>Working…</i>", can_stop: bool = False) -> Progress:
    return Progress(message, placeholder, can_stop=can_stop)

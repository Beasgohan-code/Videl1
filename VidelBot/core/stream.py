"""
Live reply streaming — Bot API 9.5+ ``sendMessageDraft``.

Telegram lets a bot show an animated *draft* of the message it is about to
send (private chats only). The draft is a temporary preview that lives for
≈30 seconds; the real message must still be sent afterwards. Updates that
reuse the same ``draft_id`` are animated as a smooth "typing" effect, and a
draft with **empty text** renders Telegram's native *“Thinking…”* placeholder.

Videl runs on MTProto (pyrofork), where the same feature is exposed as
``messages.setTyping`` with a ``sendMessageTextDraftAction``. Everything in
this module is best-effort: it never raises, it silently disables itself if
the server rejects drafts, and it is a no-op in groups/channels or when
``STREAM_REPLIES`` is off.

    await stream.typewriter(client, chat_id, text)          # animated preview
    async with stream.thinking(client, chat_id):            # "Thinking…"
        text = await build_slow_report()
    await message.reply_text(text)
"""
import asyncio
import contextlib
import logging
import random
import re

from pyrogram import enums, raw, utils

from config import STREAM_REPLIES

log = logging.getLogger("videl.stream")

DRAFT_TTL = 30          # seconds a draft stays visible (Telegram limit)
REFRESH_EVERY = 20      # re-send the placeholder before it expires
_disabled = False       # flipped when Telegram says drafts are not allowed


def enabled() -> bool:
    return STREAM_REPLIES and not _disabled


def _private(chat_id) -> bool:
    try:
        return int(chat_id) > 0
    except (TypeError, ValueError):
        return False


def new_draft_id() -> int:
    return random.randint(1, 2**62)


async def draft(client, chat_id, text: str = "", draft_id: int = None,
                parse_mode=enums.ParseMode.HTML) -> int | None:
    """Show/update a message draft. Returns the draft id (or None if skipped)."""
    global _disabled
    if not enabled() or not _private(chat_id):
        return None
    draft_id = draft_id or new_draft_id()
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
    # candidate cut points: after a newline or a space, outside tags/entities
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
    """Animate *text* growing in the chat's draft area. The caller sends the
    final message right after, which replaces the draft."""
    if not enabled() or not _private(chat_id):
        return
    did = new_draft_id()
    for part in chunks(text, steps)[:-1]:
        if await draft(client, chat_id, part, did) is None:
            return
        await asyncio.sleep(delay)


@contextlib.asynccontextmanager
async def thinking(client, chat_id, label: str = ""):
    """Show the native “Thinking…” placeholder (empty draft) – or *label* –
    while the body runs; refreshed so it never expires on slow work."""
    if not enabled() or not _private(chat_id):
        yield
        return
    did = new_draft_id()
    await draft(client, chat_id, label, did)

    async def _keep():
        try:
            while True:
                await asyncio.sleep(REFRESH_EVERY)
                if await draft(client, chat_id, label, did) is None:
                    return
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(_keep())
    try:
        yield
    finally:
        task.cancel()


async def reply(message, text: str, **kwargs):
    """``message.reply_text`` with a short typewriter preview first."""
    await typewriter(message._client, message.chat.id, text)
    return await message.reply_text(text, **kwargs)


class Progress:
    """Feedback for a slow command.

    Private chat + streaming on → the native “Thinking…” draft (no extra
    message; the final answer is sent as a normal reply).
    Otherwise → the classic placeholder message that is edited at the end.
    """

    def __init__(self, message, placeholder: str):
        self.message = message
        self.placeholder = placeholder
        self.msg = None
        self._cm = None

    async def __aenter__(self):
        if enabled() and _private(self.message.chat.id):
            self._cm = thinking(self.message._client, self.message.chat.id)
            await self._cm.__aenter__()
        else:
            self.msg = await self.message.reply_text(self.placeholder)
        return self

    async def finish(self, text: str, **kwargs):
        if self._cm is not None:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
        if self.msg is not None:
            return await self.msg.edit_text(text, **kwargs)
        return await self.message.reply_text(text, **kwargs)

    async def __aexit__(self, *exc):
        if self._cm is not None:
            await self._cm.__aexit__(*exc)
            self._cm = None
        return False


def progress(message, placeholder: str = "⏳ <i>Working…</i>") -> Progress:
    return Progress(message, placeholder)

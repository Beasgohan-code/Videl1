"""
Live transfer progress – one async pyrogram `progress=` callback that edits a status message in place.

Replaces the old "write a *status.txt file, poll it every 5 s" pattern: no disk I/O, no background
tasks that can leak, no edits when nothing changed, per-chat FloodWait coalescing, deleted-message
circuit breaker, pause/resume support, and live telemetry into `VideoEncoder.utils.jobs`.
"""
import time
from collections import OrderedDict

from pyrogram import StopTransmission
from pyrogram.errors import FloodWait, MessageDeleteForbidden, MessageIdInvalid, MessageNotModified

from core.ui import humanbytes, readable_time

# Shared per-chat FloodWait cooldown & deleted-message circuit breaker across concurrent transfers
_CHAT_COOLDOWN: dict[int, float] = {}
_DEAD_MESSAGES: OrderedDict[tuple[int, int], float] = OrderedDict()
_MAX_DEAD_TRACKED = 1024


def _msg_ident(msg) -> tuple[int, int] | None:
    if msg is None:
        return None
    chat_id = getattr(getattr(msg, "chat", None), "id", None)
    mid = getattr(msg, "id", None)
    if not chat_id or not mid:
        return None
    return int(chat_id), int(mid)


def mark_message_dead(msg) -> None:
    ident = _msg_ident(msg)
    if ident is None:
        return
    _DEAD_MESSAGES[ident] = time.time()
    while len(_DEAD_MESSAGES) > _MAX_DEAD_TRACKED:
        _DEAD_MESSAGES.popitem(last=False)


def is_message_dead(msg) -> bool:
    ident = _msg_ident(msg)
    return bool(ident and ident in _DEAD_MESSAGES)


def bar(pct: float, width: int = 20, full: str = "█", empty: str = "░") -> str:
    filled = max(0, min(width, int(pct * width / 100)))
    return full * filled + empty * (width - filled)


class Cancelled(StopTransmission):
    """Raised from the callback to abort a transfer. pyrogram handles StopTransmission cleanly (download →
    None, upload → None); any other exception is swallowed there and can leave a half-written file."""

    def __init__(self):
        super().__init__("Cancelled")


class LiveProgress:
    """
    live = LiveProgress(status_msg, "⬇️ Downloading…", template=..., cancel=lambda: flag)
    await client.download_media(msg, progress=live.update)

    Pass the bound method `live.update` – pyrogram only awaits real coroutine functions
    (inspect.iscoroutinefunction), anything else is run in a thread and never awaited.

    template placeholders: {title} {bar} {percentage} {current} {total} {speed} {elapsed} {eta} {name}
    """

    DEFAULT = ("<b>{title}</b>\n<code>[{bar}]</code> <b>{percentage:.1f}%</b>\n\n"
               "💾 {current} / {total}\n⚡ {speed}/s · ⏳ {eta}")

    def __init__(
        self,
        status,
        title: str,
        *,
        template: str = None,
        every: float = 5.0,
        cancel=None,
        reply_markup=None,
        name: str = "",
        job_key=None,
        engine: str = "WzGram",
    ):
        self.status = status
        self.title = title
        self.template = template or self.DEFAULT
        self.every = every
        self.cancel = cancel
        self.reply_markup = reply_markup
        self.name = name
        self.job_key = job_key
        self.engine = engine
        self.start = time.time()
        self._next = 0.0
        self._last_text = None
        self._message_dead = is_message_dead(status)

    def metrics(self, current: int, total: int) -> dict:
        now = time.time()
        elapsed = max(now - self.start, 0.001)
        speed = current / elapsed
        pct = min(100.0, max(0.0, current * 100 / total)) if total else 0.0
        eta = max(0.0, (total - current) / speed) if speed > 0 and total else 0.0
        return {"elapsed": elapsed, "speed": speed, "pct": pct, "eta": eta}

    def render(self, current: int, total: int) -> str:
        m = self.metrics(current, total)
        return self.template.format(
            title=self.title,
            bar=bar(m["pct"]),
            percentage=m["pct"],
            current=humanbytes(current),
            total=humanbytes(total),
            speed=humanbytes(m["speed"]),
            elapsed=readable_time(m["elapsed"]),
            eta=readable_time(m["eta"]) if m["eta"] else "—",
            name=self.name,
        )

    async def update(self, current: int, total: int, *args):
        if self.cancel and self.cancel():
            raise Cancelled()
        if self.job_key is not None:
            try:
                from VideoEncoder.utils import jobs

                if jobs.is_cancelled(self.job_key):
                    raise Cancelled()
                if jobs.is_paused(self.job_key):
                    ok = await jobs.wait_if_paused(self.job_key)
                    if not ok or (self.cancel and self.cancel()):
                        raise Cancelled()
                m = self.metrics(current, total)
                jobs.update_progress(
                    self.job_key,
                    current=current,
                    total=total,
                    pct=m["pct"],
                    speed=m["speed"],
                    eta=m["eta"],
                    stage=self.title,
                    engine=self.engine,
                    name=self.name or None,
                )
            except Cancelled:
                raise
            except Exception:
                pass
        if self._message_dead or self.status is None or is_message_dead(self.status):
            self._message_dead = True
            return
        now = time.time()
        ident = _msg_ident(self.status)
        if ident is not None and now < _CHAT_COOLDOWN.get(ident[0], 0.0):
            return
        if now < self._next and current != total:
            return
        if current == total and getattr(self, "_done", False):
            return                      # completion already shown (speed would differ → a pointless edit)
        self._next = now + self.every
        text = self.render(current, total)
        if text == self._last_text:
            return
        try:
            await self.status.edit_text(text, reply_markup=self.reply_markup)
            self._last_text = text
            if current == total:
                self._done = True
        except FloodWait as e:
            wait_s = int(getattr(e, "value", 5) or 5) + 1
            until = time.time() + wait_s
            self._next = until
            if ident is not None:
                _CHAT_COOLDOWN[ident[0]] = max(_CHAT_COOLDOWN.get(ident[0], 0.0), until)
                if len(_CHAT_COOLDOWN) > 1024:
                    _CHAT_COOLDOWN.clear()
        except (MessageIdInvalid, MessageDeleteForbidden):
            self._message_dead = True
            mark_message_dead(self.status)
        except MessageNotModified:
            self._last_text = text
        except Exception as exc:
            if "MESSAGE_ID_INVALID" in str(exc):
                self._message_dead = True
                mark_message_dead(self.status)

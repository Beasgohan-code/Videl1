"""
Live transfer progress – one async pyrogram `progress=` callback that edits a status message in place.

Replaces the old "write a *status.txt file, poll it every 5 s" pattern: no disk I/O, no background
tasks that can leak, no edits when nothing changed, and FloodWait-aware.
"""
import time

from pyrogram import StopTransmission
from pyrogram.errors import FloodWait, MessageNotModified

from core.ui import humanbytes, readable_time


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

    def __init__(self, status, title: str, *, template: str = None, every: float = 5.0, cancel=None,
                 reply_markup=None, name: str = ""):
        self.status = status
        self.title = title
        self.template = template or self.DEFAULT
        self.every = every
        self.cancel = cancel
        self.reply_markup = reply_markup
        self.name = name
        self.start = time.time()
        self._next = 0.0
        self._last_text = None

    def render(self, current: int, total: int) -> str:
        now = time.time()
        elapsed = max(now - self.start, 0.001)
        speed = current / elapsed
        pct = (current * 100 / total) if total else 0.0
        eta = (total - current) / speed if speed > 0 and total else 0
        return self.template.format(
            title=self.title, bar=bar(pct), percentage=pct, current=humanbytes(current), total=humanbytes(total),
            speed=humanbytes(speed), elapsed=readable_time(elapsed), eta=readable_time(eta) if eta else "—",
            name=self.name)

    async def update(self, current: int, total: int, *args):
        if self.cancel and self.cancel():
            raise Cancelled()
        now = time.time()
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
            self._next = time.time() + e.value + 1
        except MessageNotModified:
            self._last_text = text
        except Exception:
            pass

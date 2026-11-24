"""Voice-chat streaming for the clone Music worker (stage 2).

Verified by probe (Nov 2026): PyTgCalls 3.0.1 imports and constructs
on WzGram 3.1.7 once two legacy error names are aliased —
pytgcalls still imports ``GroupcallForbidden``/``GroupcallInvalid``
(old Pyrogram spelling) while WzGram ships ``GroupCallInvalid``.
Aliasing the names is safe: both refer to group-call RPC failures,
and any genuinely unknown RPC error still surfaces as a generic
RPCError at call time (reported honestly by the handlers).

Live-call behaviour (actually joining a real voice chat) can only be
proven against real Telegram with a real clone; everything here is
kept thin and injectable so the queue logic stays testable without it.

pytgcalls is an optional dependency of this standalone worker (see
this package's requirements.txt); it is intentionally NOT in the main
bot's requirements. Without it, the worker runs in queue-only mode.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def ensure_compat() -> bool:
    """Alias legacy Pyrogram error names pytgcalls expects. Idempotent."""
    try:
        import pyrogram.errors as errors
    except Exception:
        return False
    target = getattr(errors, "GroupCallInvalid", None)
    if target is None:
        return False
    for legacy in ("GroupcallForbidden", "GroupcallInvalid"):
        if not hasattr(errors, legacy):
            setattr(errors, legacy, target)
    return True


def pytgcalls_available() -> bool:
    try:
        ensure_compat()
        import pytgcalls  # noqa: F401
        return True
    except Exception:
        return False


class VoicePlayer:
    """Thin async wrapper over a PyTgCalls instance (injectable for tests)."""

    def __init__(self, calls, stream_factory=None):
        self._calls = calls
        self._stream_factory = stream_factory
        self.ready = False

    async def start(self) -> bool:
        try:
            await self._calls.start()
            self.ready = True
        except Exception:
            log.warning("voice player failed to start; queue-only mode", exc_info=True)
            self.ready = False
        return self.ready

    def _stream(self, source: str):
        if self._stream_factory is not None:
            return self._stream_factory(source)
        from pytgcalls.types import MediaStream
        return MediaStream(source)

    async def play(self, chat_id: int, source: str) -> None:
        await self._calls.play(int(chat_id), self._stream(source))

    async def pause(self, chat_id: int) -> bool:
        return bool(await self._calls.pause(int(chat_id)))

    async def resume(self, chat_id: int) -> bool:
        return bool(await self._calls.resume(int(chat_id)))

    async def stop(self, chat_id: int) -> None:
        await self._calls.leave_call(int(chat_id))

    async def set_volume(self, chat_id: int, volume: int) -> None:
        await self._calls.change_volume_call(int(chat_id), int(volume))


def build_player(client) -> VoicePlayer | None:
    """Create a player for one clone client, or None in queue-only mode."""
    if not pytgcalls_available():
        return None
    try:
        from pytgcalls import PyTgCalls
        return VoicePlayer(PyTgCalls(client))
    except Exception:
        log.warning("could not create voice player; queue-only mode", exc_info=True)
        return None

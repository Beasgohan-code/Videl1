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

import asyncio
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
        # Some installed Pyrogram-compatible builds still export these names,
        # but with distinct legacy classes. PyTgCalls expects the single
        # GroupCallInvalid type used by the supported Videl/WzGram runtime.
        if getattr(errors, legacy, None) is not target:
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
        self._advance_callback = None
        self._advance_bound = False

    def bind_auto_advance(self, callback) -> None:
        """Register an async callback(chat_id) fired when a stream ends."""
        self._advance_callback = callback
        if self.ready:
            self._register_advance()

    def _register_advance(self) -> None:
        if self._advance_bound or self._advance_callback is None:
            return
        try:
            try:
                from pytgcalls import filters as call_filters
                end_filter = call_filters.stream_end
            except Exception:
                end_filter = None

            @self._calls.on_update(end_filter)
            async def _on_stream_end(_, update):
                try:
                    await self._advance_callback(int(update.chat_id))
                except Exception:
                    log.warning("auto-advance failed", exc_info=True)

            self._advance_bound = True
        except Exception:
            log.warning("could not bind stream-end events; no auto-advance", exc_info=True)

    async def start(self) -> bool:
        try:
            await self._calls.start()
            self.ready = True
        except asyncio.CancelledError:
            # The remote/library start may have succeeded before cancellation.
            # Mark it uncertain so engine cleanup still attempts a real stop.
            self.ready = True
            raise
        except Exception:
            log.warning("voice player failed to start; queue-only mode", exc_info=True)
            self.ready = False
            return False
        self._register_advance()
        return True

    async def shutdown(self) -> bool:
        """Stop the PyTgCalls process; report uncertainty instead of hiding it."""
        if not self.ready:
            return True
        try:
            await self._calls.stop()
        except Exception:
            log.warning("voice player shutdown is uncertain", exc_info=True)
            return False
        self.ready = False
        return True

    def _stream(self, source: str, ffmpeg_parameters: str | None = None):
        if self._stream_factory is not None:
            try:
                return self._stream_factory(source, ffmpeg_parameters)
            except TypeError:
                return self._stream_factory(source)
        from pytgcalls.types import MediaStream
        if ffmpeg_parameters:
            return MediaStream(source, ffmpeg_parameters=ffmpeg_parameters)
        return MediaStream(source)

    async def play(self, chat_id: int, source: str,
                   ffmpeg_parameters: str | None = None) -> None:
        await self._calls.play(int(chat_id), self._stream(source, ffmpeg_parameters))

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

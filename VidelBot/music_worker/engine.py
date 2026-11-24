"""Process-side engine for Music clones (stage 1: queue & UI).

Starts only clones whose saved profile is ``music``, using this
package's own handlers/store. It is not imported by ``run.py`` yet:
profile selection for music, the assistant-session lifecycle, and the
voice-chat streaming engine are separate approved steps. Starting both
this engine and another worker for the same clone would double-handle
it, so wiring must be exclusive.
"""
from __future__ import annotations

import asyncio
import logging
import weakref

from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import BotCommand, BotCommandScopeAllGroupChats

from filestore.database.main_db import MainDB
from filestore.database.mongo import get_db
from filestore.fs_config import API_ID, API_HASH
from filestore.utils.security import decrypt_token

from .handlers import MUSIC_WORKER_COMMANDS, attach_music_handlers
from .store import MusicWorkerStore

log = logging.getLogger(__name__)
main_db = MainDB()


def _client_still_running(app) -> bool:
    return (getattr(app, "is_connected", False) is True
            or getattr(app, "is_initialized", False) is True)


class MusicWorkerEngine:
    """Runs Music clones; one Pyrogram client per clone."""

    def __init__(self, store: MusicWorkerStore | None = None):
        self.workers: dict[int, Client] = {}
        self._lock = asyncio.Lock()
        self._operation_locks = weakref.WeakValueDictionary()
        self._uncertain_stops: dict[int, Client] = {}
        self.store = store or MusicWorkerStore(get_db())

    @property
    def active_count(self) -> int:
        return len(self.workers)

    def _operation_lock(self, bot_id: int) -> asyncio.Lock:
        lock = self._operation_locks.get(bot_id)
        if lock is None:
            lock = asyncio.Lock()
            self._operation_locks[bot_id] = lock
        return lock

    async def start_all(self) -> None:
        bots = [b for b in await main_db.get_all_active_bots()
                if b.get("clone_profile", "filestore") == "music"]
        log.info("Starting %d music clones...", len(bots))
        for bot_doc in bots:
            try:
                await self.start_worker(bot_doc)
            except Exception:
                log.error("Failed to start music clone %s.", bot_doc.get("_id", "?"))

    async def start_worker(self, bot_doc: dict) -> None:
        bot_id = int(bot_doc["_id"])
        async with self._operation_lock(bot_id):
            current = await main_db.get_bot(bot_id)
            if not current or current.get("is_deleted"):
                raise RuntimeError("Clone record is unavailable")
            if current.get("clone_profile", "filestore") != "music":
                raise RuntimeError("Clone is not a Music clone")
            if bot_id in self._uncertain_stops:
                raise RuntimeError("Previous clone stop could not be confirmed")
            await self._start_locked(current)

    async def _start_locked(self, bot_doc: dict) -> None:
        bot_id = int(bot_doc["_id"])
        async with self._lock:
            if bot_id in self.workers:
                return
        try:
            token = decrypt_token(bot_doc["bot_token_encrypted"])
        except Exception:
            raise RuntimeError("Clone credential unavailable") from None
        app = Client(
            name=f"music_worker_{bot_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=token,
            in_memory=True,
            workers=4,
        )
        attach_music_handlers(app, bot_id=bot_id,
                              owner_id=int(bot_doc["owner_id"]), store=self.store)
        try:
            await app.start()
            app.set_parse_mode(ParseMode.HTML)
            commands = [BotCommand(name, desc) for name, desc in MUSIC_WORKER_COMMANDS]
            await app.set_bot_commands(commands)
            try:
                await app.set_bot_commands(commands, scope=BotCommandScopeAllGroupChats())
            except Exception:
                log.warning("Group-scope commands unavailable for clone %s.", bot_id)
            async with self._lock:
                self.workers[bot_id] = app
        except Exception:
            if _client_still_running(app):
                try:
                    await asyncio.wait_for(app.stop(block=True), timeout=3.0)
                except Exception:
                    self._uncertain_stops[bot_id] = app
                if _client_still_running(app):
                    self._uncertain_stops[bot_id] = app
            raise

    async def stop_worker(self, bot_id: int) -> bool:
        bot_id = int(bot_id)
        async with self._operation_lock(bot_id):
            async with self._lock:
                app = self.workers.pop(bot_id, None)
            if app is None:
                return bot_id not in self._uncertain_stops
            try:
                await asyncio.wait_for(app.stop(block=True), timeout=5.0)
            except Exception:
                pass
            if _client_still_running(app):
                self._uncertain_stops[bot_id] = app
                return False
            self._uncertain_stops.pop(bot_id, None)
            return True

    async def stop_all(self) -> int:
        """Stop every running clone; returns how many were running."""
        bot_ids = list(self.workers) + [b for b in self._uncertain_stops if b not in self.workers]
        for bot_id in bot_ids:
            await self.stop_worker(bot_id)
        return len(bot_ids)


music_worker_engine = MusicWorkerEngine()

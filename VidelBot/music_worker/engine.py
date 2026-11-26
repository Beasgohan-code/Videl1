"""Process-side engine for active Music clones.

Starts only active clones whose saved profile is ``music``, using this
package's handlers/store and per-clone assistant-session playback. It runs
as a separate, single Music worker process; the main controller persists
owner desired state while this process reconciles it. Never run another
client against the same clone token. The player engine is stopped before
the assistant and bot clients; uncertain local resources are retained for
retry, but local reports are not distributed fencing.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import weakref

from pyrogram import Client, ContinuePropagation
from pyrogram.enums import ParseMode
from pyrogram.handlers import CallbackQueryHandler, MessageHandler
from pyrogram.types import BotCommand, BotCommandScopeAllGroupChats

from filestore.database.main_db import MainDB
from filestore.database.mongo import get_db
from filestore.fs_config import API_ID, API_HASH
from filestore.utils.security import decrypt_token

from .handlers import MUSIC_WORKER_COMMANDS, attach_music_handlers
from .store import MusicWorkerStore

log = logging.getLogger(__name__)
main_db = MainDB()
_PLAYER_STOP_TIMEOUT = 5.0


def _attach_activity_middleware(app, bot_id: int) -> None:
    """Keep Videl's idle lifecycle accurate for activity handled by Music clones."""
    async def update_activity(client, update):
        if time.monotonic() - main_db._last_active_written.get(bot_id, -1e9) >= 60:
            from core.bg import spawn
            spawn(main_db.update_last_active(bot_id), name="music-clone-last-active")
        raise ContinuePropagation

    app.add_handler(MessageHandler(update_activity), group=-1)
    app.add_handler(CallbackQueryHandler(update_activity), group=-1)


def _client_still_running(app) -> bool:
    return (getattr(app, "is_connected", False) is True
            or getattr(app, "is_initialized", False) is True)


class MusicWorkerEngine:
    """Runs Music clones; one Pyrogram client per clone."""

    def __init__(self, store: MusicWorkerStore | None = None):
        self.workers: dict[int, Client] = {}
        self._assistants: dict[int, Client] = {}
        self._players: dict[int, object] = {}
        self._uncertain_players: dict[int, object] = {}
        self._lock = asyncio.Lock()
        self._reconcile_lock = asyncio.Lock()
        self._operation_locks = weakref.WeakValueDictionary()
        # A Music clone can own two Telegram clients (bot + assistant). Keep
        # every client whose stop could not be confirmed so later shutdowns
        # can retry and startup remains fenced until all are down.
        self._uncertain_stops: dict[int, tuple[Client, ...]] = {}
        # Fingerprints let the sole runtime refresh changed bot/assistant
        # credentials without retaining the secret strings in another map.
        self._loaded_configs: dict[int, str] = {}
        self._start_failures: dict[int, tuple[int, float]] = {}
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

    @staticmethod
    def _config_fingerprint(bot_doc: dict) -> str:
        """Non-reversible change detector for credentials and command ownership."""
        bot_token = bot_doc.get("bot_token_encrypted")
        session = bot_doc.get("music_session_encrypted")
        owner_id = str(bot_doc.get("owner_id", ""))
        material = "\\x1f".join(value if isinstance(value, str) else ""
                                  for value in (bot_token, session, owner_id))
        return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()

    async def start_all(self) -> None:
        result = await self.reconcile()
        log.info("Music worker reconciliation: %d started, %d stopped, %d failed",
                 len(result["started"]), len(result["stopped"]), len(result["failed"]))

    async def start_worker(self, bot_doc: dict) -> None:
        bot_id = int(bot_doc["_id"])
        async with self._operation_lock(bot_id):
            current = await main_db.get_bot(bot_id)
            if not current or current.get("is_deleted"):
                raise RuntimeError("Clone record is unavailable")
            if current.get("is_active") is not True:
                raise RuntimeError("Music clone is no longer desired active")
            if current.get("clone_profile", "filestore") != "music":
                raise RuntimeError("Clone is not a Music clone")
            if bot_id in self.workers:
                if self._loaded_configs.get(bot_id) == self._config_fingerprint(current):
                    await self._publish_runtime_state(bot_id, "running")
                    return
                raise RuntimeError("Clone credentials changed; reconciliation must stop it before refresh")
            if (bot_id in self._uncertain_stops or bot_id in self._uncertain_players
                    or bot_id in self._assistants or bot_id in self._players):
                raise RuntimeError("Previous Music resources require cleanup")
            await self._publish_runtime_state(bot_id, "starting")
            try:
                await self._start_locked(current)
            except BaseException:
                uncertain = bot_id in self._uncertain_stops or bot_id in self._uncertain_players
                await self._publish_runtime_state(bot_id, "uncertain" if uncertain else "stopped")
                raise
            self._loaded_configs[bot_id] = self._config_fingerprint(current)
            await self._publish_runtime_state(bot_id, "running")

    async def _publish_runtime_state(self, bot_id: int, state: str) -> None:
        try:
            await main_db.set_music_runtime_state(int(bot_id), state)
        except Exception:
            log.warning("Could not publish Music runtime state for clone %s.", bot_id)

    async def reconcile(self) -> dict[str, list[int]]:
        """Serialize one reconciliation pass inside this process only."""
        async with self._reconcile_lock:
            return await self._reconcile_once()

    async def _reconcile_once(self) -> dict[str, list[int]]:
        """Converge local clients to the active Music-profile records.

        The registry is desired state, not a distributed lease: run exactly one
        Music clone worker and never overlap it with another token client.
        """
        desired = await main_db.get_active_bots_for_profile("music")
        desired_by_id = {int(doc["_id"]): doc for doc in desired}
        # A stop request cancels any pending startup backoff; a later owner
        # restart is a new desired-state transition and should retry promptly.
        for bot_id in list(self._start_failures):
            if bot_id not in desired_by_id:
                self._start_failures.pop(bot_id, None)
        running_ids = (set(self.workers) | set(self._assistants) | set(self._players)
                       | set(self._uncertain_stops) | set(self._uncertain_players))
        stopped, started, failed = [], [], []
        for bot_id in sorted(running_ids - set(desired_by_id)):
            if await self.stop_worker(bot_id):
                stopped.append(bot_id)
            else:
                failed.append(bot_id)
        now = time.monotonic()
        for bot_id, doc in desired_by_id.items():
            orphaned_resources = (bot_id not in self.workers
                                  and (bot_id in self._assistants or bot_id in self._players))
            if (bot_id in self._uncertain_stops or bot_id in self._uncertain_players
                    or orphaned_resources):
                if not await self.stop_worker(bot_id):
                    failed.append(bot_id)
                    continue
                stopped.append(bot_id)
            if bot_id in self.workers:
                if self._loaded_configs.get(bot_id) == self._config_fingerprint(doc):
                    await self._publish_runtime_state(bot_id, "running")
                    self._start_failures.pop(bot_id, None)
                    continue
                # Owner changed a bot token or assistant session. Recycle only
                # inside this sole process, and never start until stop is sure.
                if not await self.stop_worker(bot_id):
                    failed.append(bot_id)
                    continue
                stopped.append(bot_id)
            attempts, retry_at = self._start_failures.get(bot_id, (0, 0.0))
            if retry_at > now:
                continue
            try:
                await self.start_worker(doc)
                self._start_failures.pop(bot_id, None)
                started.append(bot_id)
            except Exception:
                attempts += 1
                self._start_failures[bot_id] = (attempts, now + min(300.0, 5.0 * (2 ** min(attempts - 1, 6))))
                failed.append(bot_id)
                log.warning("Music profile reconciliation failed for clone %s (attempt %d).",
                            bot_id, attempts)
        return {"started": started, "stopped": stopped, "failed": failed}

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
        _attach_activity_middleware(app, bot_id)
        from .player import build_player
        # Playback runs on the assistant user account when the clone has
        # one (collected by the main bot's Music setup); the clone bot
        # itself only takes commands. Without a session we fall back to
        # the bot client (queue-only or bot-joined playback).
        assistant = None
        encrypted = bot_doc.get("music_session_encrypted")
        if encrypted:
            try:
                session = decrypt_token(encrypted)
                assistant = Client(
                    name=f"music_asst_{bot_id}",
                    api_id=API_ID,
                    api_hash=API_HASH,
                    session_string=session,
                    in_memory=True,
                )
                self._assistants[bot_id] = assistant
            except Exception:
                log.warning("music clone %s: assistant session unusable; bot-client playback", bot_id)
                assistant = None
        player = build_player(assistant or app)
        attach_music_handlers(app, bot_id=bot_id,
                              owner_id=int(bot_doc["owner_id"]), store=self.store,
                              player=player)
        try:
            await app.start()
            if assistant is not None:
                try:
                    await assistant.start()
                except Exception:
                    log.warning("music clone %s: assistant login failed; queue-only mode", bot_id)
                    self._assistants.pop(bot_id, None)
                    if not await self._stop_client(assistant, timeout=3.0):
                        raise RuntimeError("Assistant client stop could not be confirmed")
                    assistant = None
            if player is not None and (assistant is not None or bot_doc.get("music_session_encrypted") is None):
                await player.start()  # failure degrades to queue-only mode
                if player.ready:
                    self._players[bot_id] = player
            app.set_parse_mode(ParseMode.HTML)
            commands = [BotCommand(name, desc) for name, desc in MUSIC_WORKER_COMMANDS]
            await app.set_bot_commands(commands)
            try:
                await app.set_bot_commands(commands, scope=BotCommandScopeAllGroupChats())
            except Exception:
                log.warning("Group-scope commands unavailable for clone %s.", bot_id)
            async with self._lock:
                self.workers[bot_id] = app
        except asyncio.CancelledError:
            self._assistants.pop(bot_id, None)
            self._players.pop(bot_id, None)
            if player is not None and not await self._shutdown_player(player):
                self._uncertain_players[bot_id] = player
            uncertain = []
            for resource in (assistant, app):
                if resource is not None and not await self._stop_client(resource, timeout=3.0):
                    uncertain.append(resource)
            if uncertain:
                self._uncertain_stops[bot_id] = tuple(uncertain)
            raise
        except Exception:
            self._assistants.pop(bot_id, None)
            self._players.pop(bot_id, None)
            if player is not None and not await self._shutdown_player(player):
                self._uncertain_players[bot_id] = player
            uncertain = []
            # Stop the assistant first when both clients partially started.
            for resource in (assistant, app):
                if resource is not None and not await self._stop_client(resource, timeout=3.0):
                    uncertain.append(resource)
            if uncertain:
                self._uncertain_stops[bot_id] = tuple(uncertain)
            raise

    async def _shutdown_player(self, player) -> bool:
        """Stop the voice-call engine before its Telegram client dependencies."""
        if player is None:
            return True
        try:
            result = await asyncio.wait_for(player.shutdown(), timeout=_PLAYER_STOP_TIMEOUT)
        except Exception:
            log.warning("music player stop failed", exc_info=True)
            return False
        return result is not False and not getattr(player, "ready", False)

    async def _stop_client(self, app, *, timeout: float = 5.0) -> bool:
        """Stop a Telegram client and verify its local lifecycle flags cleared."""
        try:
            await asyncio.wait_for(app.stop(block=True), timeout=timeout)
        except Exception:
            pass
        return not _client_still_running(app)

    async def stop_worker(self, bot_id: int) -> bool:
        """Stop player, assistant and bot; retry any uncertain resource later."""
        bot_id = int(bot_id)
        async with self._operation_lock(bot_id):
            async with self._lock:
                app = self.workers.pop(bot_id, None)
            assistant = self._assistants.pop(bot_id, None)
            player = self._players.pop(bot_id, None)
            if player is None:
                player = self._uncertain_players.get(bot_id)
            candidates = []
            for resource in (assistant, app, *self._uncertain_stops.get(bot_id, ())):
                if resource is not None and all(resource is not old for old in candidates):
                    candidates.append(resource)
            if player is None and not candidates:
                self._start_failures.pop(bot_id, None)
                self._loaded_configs.pop(bot_id, None)
                await self._publish_runtime_state(bot_id, "stopped")
                return True

            player_stopped = True
            uncertain = []
            await self._publish_runtime_state(bot_id, "stopping")
            try:
                # PyTgCalls owns voice-call tasks and must stop before its clients.
                if player is not None:
                    player_stopped = await self._shutdown_player(player)
                for resource in candidates:
                    if not await self._stop_client(resource):
                        uncertain.append(resource)
            except asyncio.CancelledError:
                # Cancellation makes even completed remote stops ambiguous.
                if player is not None:
                    self._uncertain_players[bot_id] = player
                if candidates:
                    self._uncertain_stops[bot_id] = tuple(candidates)
                await self._publish_runtime_state(bot_id, "uncertain")
                raise
            if not player_stopped:
                self._uncertain_players[bot_id] = player
            else:
                self._uncertain_players.pop(bot_id, None)
            if uncertain:
                self._uncertain_stops[bot_id] = tuple(uncertain)
            else:
                self._uncertain_stops.pop(bot_id, None)
            confirmed = player_stopped and not uncertain
            if confirmed:
                self._start_failures.pop(bot_id, None)
                self._loaded_configs.pop(bot_id, None)
            await self._publish_runtime_state(bot_id, "stopped" if confirmed else "uncertain")
            return confirmed

    async def stop_all(self) -> int:
        """Stop every running clone; returns how many were running."""
        bot_ids = list(dict.fromkeys(
            [*self.workers, *self._assistants, *self._players,
             *self._uncertain_stops, *self._uncertain_players]
        ))
        for bot_id in bot_ids:
            await self.stop_worker(bot_id)
        return len(bot_ids)


music_worker_engine = MusicWorkerEngine()

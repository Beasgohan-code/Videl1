"""Mongo-backed settings for the clone Music worker, scoped per clone+chat."""
from __future__ import annotations

from .logic import default_settings


_TRACK_FIELDS = ("title", "duration", "requested_by", "source")


class MusicWorkerStore:
    def __init__(self, db):
        self._settings = db["music_worker_settings"]
        self._queues = db["music_worker_queues"]

    @staticmethod
    def _key(bot_id: int, chat_id: int) -> dict:
        return {"bot_id": int(bot_id), "chat_id": int(chat_id)}

    async def get_settings(self, bot_id: int, chat_id: int) -> dict:
        doc = await self._settings.find_one(self._key(bot_id, chat_id))
        settings = default_settings()
        if doc:
            settings.update({k: v for k, v in doc.items() if k in settings})
        return settings

    async def update_settings(self, bot_id: int, chat_id: int, updates: dict) -> dict:
        allowed = {k: v for k, v in (updates or {}).items() if k in default_settings()}
        if allowed:
            await self._settings.update_one(
                self._key(bot_id, chat_id), {"$set": allowed}, upsert=True)
        return await self.get_settings(bot_id, chat_id)

    # ── persisted queues (survive worker restarts) ──────────────
    async def save_queue(self, bot_id: int, chat_id: int, tracks: list[dict]) -> None:
        clean = [{k: t.get(k) for k in _TRACK_FIELDS} for t in (tracks or [])[:100]]
        if clean:
            await self._queues.update_one(
                self._key(bot_id, chat_id), {"$set": {"tracks": clean}}, upsert=True)
        else:
            await self._queues.delete_one(self._key(bot_id, chat_id))

    async def load_queue(self, bot_id: int, chat_id: int) -> list[dict]:
        doc = await self._queues.find_one(self._key(bot_id, chat_id))
        if not doc:
            return []
        return [{k: t.get(k) for k in _TRACK_FIELDS}
                for t in doc.get("tracks") or [] if isinstance(t, dict)]

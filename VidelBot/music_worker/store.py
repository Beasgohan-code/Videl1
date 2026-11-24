"""Mongo-backed settings for the clone Music worker, scoped per clone+chat."""
from __future__ import annotations

from .logic import default_settings


class MusicWorkerStore:
    def __init__(self, db):
        self._settings = db["music_worker_settings"]

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

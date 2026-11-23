"""Mongo-backed storage for the clone Group Management worker.

Settings and warn counts are scoped by (clone bot, chat) so one clone
can never read or change another clone's group configuration. The
database handle is injectable for tests.
"""
from __future__ import annotations

from .logic import default_settings


class GroupWorkerStore:
    def __init__(self, db):
        self._settings = db["group_worker_settings"]
        self._warns = db["group_worker_warns"]
        self._modlog = db["group_worker_modlog"]

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
                self._key(bot_id, chat_id), {"$set": allowed}, upsert=True,
            )
        return await self.get_settings(bot_id, chat_id)

    async def add_warn(self, bot_id: int, chat_id: int, user_id: int, limit: int) -> tuple[int, bool]:
        """Atomically add a warn; returns (new count, limit reached)."""
        key = {**self._key(bot_id, chat_id), "user_id": int(user_id)}
        doc = await self._warns.find_one_and_update(
            key, {"$inc": {"count": 1}}, upsert=True, return_document=True,
        )
        count = int((doc or {}).get("count", 1))
        if count >= max(1, int(limit)):
            await self._warns.delete_one(key)
            return count, True
        return count, False

    async def warn_count(self, bot_id: int, chat_id: int, user_id: int) -> int:
        doc = await self._warns.find_one(
            {**self._key(bot_id, chat_id), "user_id": int(user_id)}
        )
        return int((doc or {}).get("count", 0))

    async def reset_warns(self, bot_id: int, chat_id: int, user_id: int) -> None:
        await self._warns.delete_one(
            {**self._key(bot_id, chat_id), "user_id": int(user_id)}
        )


    async def log_action(self, bot_id: int, chat_id: int, action: str,
                         target_id: int | None, actor_id: int | None,
                         reason: str = "") -> None:
        """Append a moderation log entry, keeping the newest 200 per chat."""
        key = self._key(bot_id, chat_id)
        await self._modlog.insert_one({
            **key, "action": action, "target_id": target_id,
            "actor_id": actor_id, "reason": (reason or "")[:200],
        })
        old = await self._modlog.find(key, {"_id": 1}).sort("_id", -1).skip(200).to_list(length=200)
        if old:
            await self._modlog.delete_many({"_id": {"$in": [d["_id"] for d in old]}})

    async def recent_actions(self, bot_id: int, chat_id: int, limit: int = 10) -> list[dict]:
        cursor = self._modlog.find(self._key(bot_id, chat_id)).sort("_id", -1).limit(limit)
        return [doc async for doc in cursor]

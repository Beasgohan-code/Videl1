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
        self._afk = db["group_worker_afk"]
        self._feds = db["group_worker_feds"]
        self._fedbans = db["group_worker_fed_bans"]

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


    async def set_afk(self, bot_id: int, chat_id: int, user_id: int,
                      name: str, reason: str = "") -> None:
        await self._afk.update_one(
            {**self._key(bot_id, chat_id), "user_id": int(user_id)},
            {"$set": {"name": (name or "")[:60], "reason": (reason or "")[:200]}},
            upsert=True)

    async def get_afk(self, bot_id: int, chat_id: int, user_id: int) -> dict | None:
        return await self._afk.find_one(
            {**self._key(bot_id, chat_id), "user_id": int(user_id)})

    async def clear_afk(self, bot_id: int, chat_id: int, user_id: int) -> dict | None:
        doc = await self.get_afk(bot_id, chat_id, user_id)
        if doc:
            await self._afk.delete_one(
                {**self._key(bot_id, chat_id), "user_id": int(user_id)})
        return doc


    # ── federations (shared bans across one clone's groups) ─────────
    async def create_fed(self, bot_id: int, owner_id: int, name: str, fed_id: str) -> dict:
        doc = {"bot_id": int(bot_id), "fed_id": fed_id, "owner_id": int(owner_id),
               "name": (name or "Federation")[:60], "chats": []}
        await self._feds.insert_one(doc)
        return doc

    async def get_fed(self, bot_id: int, fed_id: str) -> dict | None:
        return await self._feds.find_one({"bot_id": int(bot_id), "fed_id": fed_id})

    async def set_fed_chats(self, bot_id: int, fed_id: str, chats: list[int]) -> None:
        await self._feds.update_one({"bot_id": int(bot_id), "fed_id": fed_id},
                                    {"$set": {"chats": [int(c) for c in chats]}})

    async def fed_ban(self, bot_id: int, fed_id: str, user_id: int, reason: str = "") -> None:
        await self._fedbans.update_one(
            {"bot_id": int(bot_id), "fed_id": fed_id, "user_id": int(user_id)},
            {"$set": {"reason": (reason or "")[:200]}}, upsert=True)

    async def fed_unban(self, bot_id: int, fed_id: str, user_id: int) -> None:
        await self._fedbans.delete_one(
            {"bot_id": int(bot_id), "fed_id": fed_id, "user_id": int(user_id)})

    async def get_fed_ban(self, bot_id: int, fed_id: str, user_id: int) -> dict | None:
        if not fed_id:
            return None
        return await self._fedbans.find_one(
            {"bot_id": int(bot_id), "fed_id": fed_id, "user_id": int(user_id)})

    async def fed_bans(self, bot_id: int, fed_id: str) -> list[dict]:
        cursor = self._fedbans.find({"bot_id": int(bot_id), "fed_id": fed_id})
        return [doc async for doc in cursor]

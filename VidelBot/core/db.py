"""
Core Videl database: global user registry, bans and bot-wide settings.
Lives in its own Mongo database (VIDEL_DB_NAME) next to the module DBs.
"""
import time
from datetime import datetime, timezone

from config import VIDEL_DB_NAME
from filestore.database.mongo import get_motor_client


class VidelDB:
    def __init__(self):
        self._db = None
        self._banned: set[int] = set()
        self._known: set[int] = set()
        self._settings: dict = {}
        self._settings_ts = 0.0

    @property
    def db(self):
        if self._db is None:
            self._db = get_motor_client()[VIDEL_DB_NAME]
        return self._db

    @property
    def users(self):
        return self.db["videl_users"]

    @property
    def settings(self):
        return self.db["videl_settings"]

    # ---------------- lifecycle ----------------
    async def warm_up(self):
        await self.users.create_index("id", unique=True)
        self._banned = {d["id"] async for d in self.users.find({"banned": True}, {"id": 1})}
        await self._reload_settings()

    # ---------------- users ----------------
    async def track(self, user) -> bool:
        """Insert user if unseen. Returns True when the user is brand new."""
        if user.id in self._known:
            return False
        self._known.add(user.id)
        res = await self.users.update_one(
            {"id": user.id},
            {
                "$setOnInsert": {"id": user.id, "joined": datetime.now(timezone.utc), "banned": False},
                "$set": {"name": user.first_name or "", "username": user.username or ""},
            },
            upsert=True,
        )
        return res.upserted_id is not None

    async def total_users(self) -> int:
        return await self.users.count_documents({})

    async def active_user_ids(self):
        async for d in self.users.find({"banned": {"$ne": True}, "blocked": {"$ne": True}}, {"id": 1}):
            yield d["id"]

    async def mark_blocked(self, user_id: int):
        await self.users.update_one({"id": user_id}, {"$set": {"blocked": True}})

    async def unmark_blocked(self, user_id: int):
        await self.users.update_one({"id": user_id}, {"$unset": {"blocked": ""}})

    async def get_user(self, user_id: int):
        return await self.users.find_one({"id": user_id})

    # ---------------- bans ----------------
    def is_banned(self, user_id: int) -> bool:
        return user_id in self._banned

    async def ban(self, user_id: int, reason: str = ""):
        self._banned.add(user_id)
        await self.users.update_one(
            {"id": user_id},
            {"$set": {"banned": True, "ban_reason": reason, "banned_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def unban(self, user_id: int):
        self._banned.discard(user_id)
        await self.users.update_one({"id": user_id}, {"$set": {"banned": False}, "$unset": {"ban_reason": ""}})

    async def banned_users(self) -> list:
        return [d async for d in self.users.find({"banned": True})]

    # ---------------- settings ----------------
    async def _reload_settings(self):
        doc = await self.settings.find_one({"_id": "global"}) or {}
        self._settings = doc
        self._settings_ts = time.time()

    async def get_setting(self, key: str, default=None):
        if time.time() - self._settings_ts > 60:
            await self._reload_settings()
        return self._settings.get(key, default)

    def cached_setting(self, key: str, default=None):
        return self._settings.get(key, default)

    async def set_setting(self, key: str, value):
        await self.settings.update_one({"_id": "global"}, {"$set": {key: value}}, upsert=True)
        self._settings[key] = value


vdb = VidelDB()

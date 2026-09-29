"""
main_db.py — Database operations for the main controller bot.

Manages the registry of user-created bots and main bot users.
"""

import time
from datetime import datetime, timezone
from filestore.database.mongo import get_db
from filestore.fs_config import LOGGER
log = LOGGER(__name__)

class MainDB:
    """Database operations for the main controller bot."""

    def __init__(self):
        db = get_db()
        self.bots = db["registered_bots"]
        self.users = db["main_users"]
        self.cooldowns = db["creation_cooldowns"]

    async def ensure_indexes(self):
        """My Bots / worker start-up / cooldown look-ups – cheap to create, idempotent."""
        try:
            await self.bots.create_index([("owner_id", 1), ("is_deleted", 1)])
            await self.bots.create_index("is_active")
            await self.bots.create_index("bot_token_encrypted")
        except Exception as e:
            log.warning(f"filestore index creation skipped: {e}")

    # =========================================================================
    # USER MANAGEMENT
    # =========================================================================

    async def add_user(self, user_id: int) -> bool:
        """Add a user to the main bot's user list. Returns True if new."""
        existing = await self.users.find_one({"_id": user_id})
        if existing:
            return False
        await self.users.insert_one({
            "_id": user_id,
            "created_at": datetime.now(timezone.utc),
        })
        return True

    async def user_exists(self, user_id: int) -> bool:
        """Check if a user exists in the main bot's user list."""
        return bool(await self.users.find_one({"_id": user_id}))

    async def get_all_users(self) -> list:
        """Return all user IDs."""
        docs = await self.users.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    async def total_users(self) -> int:
        """Return total user count."""
        return await self.users.count_documents({})

    async def ban_user_exist(self, user_id: int) -> bool:
        doc = await self.users.find_one({"_id": user_id})
        return doc.get("banned", False) if doc else False

    async def add_ban_user(self, user_id: int):
        await self.users.update_one({"_id": user_id}, {"$set": {"banned": True}}, upsert=True)

    async def del_ban_user(self, user_id: int):
        await self.users.update_one({"_id": user_id}, {"$unset": {"banned": ""}})

    # =========================================================================
    # BOT REGISTRATION
    # =========================================================================

    async def add_bot(
        self,
        bot_id: int,
        owner_id: int,
        bot_token_encrypted: str,
        bot_username: str,
        log_channel_id: int,
    ) -> dict:
        """Register a new user-created bot."""
        doc = {
            "_id": bot_id,
            "owner_id": owner_id,
            "bot_token_encrypted": bot_token_encrypted,
            "bot_username": bot_username,
            "log_channel_id": log_channel_id,
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
            "settings": {
                "auto_delete_time": 0,
                "protect_content": False,
                "custom_caption": "",
                "start_pic": "",
                "start_message": "",
                "force_pic": "",
                "permanent_link": False,
            },
            "shortener": {
                "enabled": False,
                "api_key_encrypted": "",
                "domain": "",
            },
        }
        await self.bots.replace_one({"_id": bot_id}, doc, upsert=True)
        log.info(f"Bot @{bot_username} (ID:{bot_id}) registered by user {owner_id}")
        return doc

    async def get_bot(self, bot_id: int) -> dict | None:
        """Get a registered bot by its bot user ID."""
        return await self.bots.find_one({"_id": bot_id})

    async def get_bot_by_token(self, token_encrypted: str) -> dict | None:
        """Get a bot by its encrypted token."""
        return await self.bots.find_one({"bot_token_encrypted": token_encrypted})

    async def get_user_bots(self, owner_id: int) -> list:
        """Get all active (non-deleted) bots owned by a specific user."""
        cursor = self.bots.find({"owner_id": owner_id, "is_deleted": {"$ne": True}})
        return await cursor.to_list(length=None)

    async def count_user_bots(self, owner_id: int) -> int:
        """Count active (non-deleted) bots owned by a specific user."""
        return await self.bots.count_documents({"owner_id": owner_id, "is_deleted": {"$ne": True}})

    async def get_deleted_user_bots(self, owner_id: int) -> list:
        """Get all soft-deleted bots owned by a user (for data transfer)."""
        cursor = self.bots.find({"owner_id": owner_id, "is_deleted": True})
        return await cursor.to_list(length=None)

    
    async def get_all_bots(self) -> list:
        """Return all bots (active + inactive)."""
        cursor = self.bots.find({})
        return await cursor.to_list(length=None)

    async def get_all_active_bots(self) -> list:
        """Get all active bots for worker engine startup."""
        cursor = self.bots.find({"is_active": True})
        return await cursor.to_list(length=None)

    async def delete_bot(self, bot_id: int) -> bool:
        """Soft-delete a bot (keeps data for transfer)."""
        result = await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"is_deleted": True, "is_active": False}},
        )
        if result.modified_count > 0:
            log.info(f"Bot ID:{bot_id} soft-deleted (data preserved)")
            return True
        return False

    async def purge_bot(self, bot_id: int) -> bool:
        """Permanently delete a bot record from the registry."""
        result = await self.bots.delete_one({"_id": bot_id})
        if result.deleted_count > 0:
            log.info(f"Bot ID:{bot_id} permanently purged")
            return True
        return False

    async def transfer_owner(self, bot_id: int, new_owner: int):
        """Give a clone to another user (keeps users, files, links and settings)."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"owner_id": new_owner, "transferred_at": datetime.now(timezone.utc)}},
        )

    async def set_bot_active(self, bot_id: int, active: bool):
        """Enable or disable a bot. Turning it on also restarts the inactivity clock – otherwise a clone
        woken after a long sleep would be switched off again by the very next sweep."""
        if active:
            update = {"$set": {"is_active": True, "last_active": datetime.now(timezone.utc)},
                      "$unset": {"deactivated_at": "", "deactivated_reason": "", "inactive_warned": ""}}
            self._last_active_written[bot_id] = time.monotonic()
        else:
            update = {"$set": {"is_active": False}}
        await self.bots.update_one({"_id": bot_id}, update)

    async def deactivate_inactive(self, bot_id: int):
        """Switch a clone off because nobody used it for CLONE_INACTIVE_DAYS."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"is_active": False, "deactivated_at": datetime.now(timezone.utc),
                      "deactivated_reason": "inactive"},
             "$unset": {"inactive_warned": ""}},
        )

    async def mark_inactive_warned(self, bot_id: int):
        await self.bots.update_one({"_id": bot_id}, {"$set": {"inactive_warned": datetime.now(timezone.utc)}})

    async def keep_alive(self, bot_id: int):
        """Owner tapped “keep it running” – counts as activity."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"last_active": datetime.now(timezone.utc)}, "$unset": {"inactive_warned": ""}},
        )

    async def update_log_channel(self, bot_id: int, channel_id: int):
        """Update a bot's log channel."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"log_channel_id": channel_id}},
        )

    _last_active_written: dict = {}

    async def update_last_active(self, bot_id: int):
        """Update the last_active timestamp of a bot (at most once a minute – it runs on every update)."""
        import time
        now = time.monotonic()
        if now - self._last_active_written.get(bot_id, -1e9) < 60:
            return
        self._last_active_written[bot_id] = now
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {"last_active": datetime.now(timezone.utc)}, "$unset": {"inactive_warned": ""}},
        )

    # =========================================================================
    # BOT SETTINGS
    # =========================================================================

    async def update_setting(self, bot_id: int, key: str, value):
        """Update a single setting for a bot."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {f"settings.{key}": value}},
        )

    async def get_settings(self, bot_id: int) -> dict:
        """Get all settings for a bot."""
        doc = await self.get_bot(bot_id)
        return doc.get("settings", {}) if doc else {}

    # =========================================================================
    # SHORTENER CONFIG
    # =========================================================================

    async def update_shortener(self, bot_id: int, key: str, value):
        """Update a shortener setting for a bot."""
        await self.bots.update_one(
            {"_id": bot_id},
            {"$set": {f"shortener.{key}": value}},
        )

    async def get_shortener(self, bot_id: int) -> dict:
        """Get shortener config for a bot."""
        doc = await self.get_bot(bot_id)
        return doc.get("shortener", {}) if doc else {}

    # =========================================================================
    # RATE LIMITING / COOLDOWN
    # =========================================================================

    async def set_cooldown(self, user_id: int):
        """Set a bot creation cooldown timestamp for a user."""
        await self.cooldowns.replace_one(
            {"_id": user_id},
            {"_id": user_id, "timestamp": datetime.now(timezone.utc)},
            upsert=True,
        )

    async def get_cooldown(self, user_id: int) -> datetime | None:
        """Get the last bot creation timestamp for a user."""
        doc = await self.cooldowns.find_one({"_id": user_id})
        return doc["timestamp"] if doc else None

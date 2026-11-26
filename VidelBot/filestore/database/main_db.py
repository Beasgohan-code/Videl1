"""
main_db.py — Database operations for the main controller bot.

Manages the registry of user-created bots and main bot users.
"""

import time
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument
from pymongo.collation import Collation
from pymongo.errors import DuplicateKeyError
from filestore.database.mongo import get_db
from filestore.fs_config import LOGGER
log = LOGGER(__name__)

class BotRegistrationConflict(Exception):
    """The ID is already claimed or the requested restoration is no longer valid."""


_PROFILE_NOT_CHECKED = object()
MUSIC_RUNTIME_STALE_SECONDS = 120
_MUSIC_ACTIVE_RUNTIME_STATES = ("starting", "running", "stopping", "uncertain")


def music_runtime_report_is_fresh(bot: dict, *, now=None) -> bool:
    """Whether an active Music observation is recent enough to block recovery.

    A missing/malformed timestamp is treated conservatively as fresh. A stale
    observation can only permit owner-confirmed recovery; it is not proof that a
    separate worker has stopped.
    """
    if not isinstance(bot, dict) or bot.get("music_runtime_state") not in _MUSIC_ACTIVE_RUNTIME_STATES:
        return False
    observed_at = bot.get("music_runtime_updated_at")
    if not isinstance(observed_at, datetime):
        return True
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age = (now - observed_at).total_seconds()
    return age <= MUSIC_RUNTIME_STALE_SECONDS


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
        *,
        managed: bool = False,
        restore_deleted: bool = False,
        expected_old_token: str | None = None,
        initial_profile: str | None = None,
        initial_active: bool = True,
        music_setup_pending: bool = False,
        expected_old_profile=_PROFILE_NOT_CHECKED,
    ) -> dict:
        """Insert a clone, or explicitly restore an owner's soft-deleted record.

        Music onboarding may create a paused File Store-default record marked
        ``music_setup_pending``; it must not save the Music profile until an
        encrypted assistant session is available. MongoDB's unique _id prevents
        check-then-write races. Restoration uses a conditional update.
        """
        if (initial_profile not in (None, "filestore", "group")
                or (restore_deleted and (initial_profile is not None or music_setup_pending))
                or type(initial_active) is not bool
                or (music_setup_pending and (initial_active or initial_profile not in (None, "filestore")))):
            raise ValueError("Invalid initial clone profile or state")
        doc = {
            "_id": bot_id,
            "owner_id": owner_id,
            "bot_token_encrypted": bot_token_encrypted,
            "bot_username": bot_username,
            "log_channel_id": log_channel_id,
            "is_active": initial_active,
            "is_deleted": False,
            "managed": bool(managed),
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
        if initial_profile is not None:
            doc["clone_profile"] = initial_profile
        if music_setup_pending:
            doc["music_setup_pending"] = True
        if restore_deleted:
            if not isinstance(expected_old_token, str):
                raise BotRegistrationConflict
            match = {"_id": bot_id, "owner_id": owner_id, "is_deleted": True,
                     "bot_token_encrypted": expected_old_token}
            if expected_old_profile is not _PROFILE_NOT_CHECKED:
                match["clone_profile"] = ({"$exists": False} if expected_old_profile is None else expected_old_profile)
            restored = await self.bots.find_one_and_update(
                match,
                {"$set": {"bot_token_encrypted": bot_token_encrypted,
                          "bot_username": bot_username, "log_channel_id": log_channel_id,
                          "is_active": True, "is_deleted": False, "managed": bool(managed)}},
                return_document=ReturnDocument.AFTER,
                collation=Collation("simple"),
            )
            if restored is None:
                raise BotRegistrationConflict
            return restored
        try:
            result = await self.bots.insert_one(dict(doc))
            if not result.acknowledged:
                raise RuntimeError("Clone registration was not acknowledged")
        except DuplicateKeyError:
            raise BotRegistrationConflict from None
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

    async def get_clone_directory_records(self) -> list:
        """Read only fields needed for owner directory views; never fetch credentials."""
        projection = {"_id": 1, "owner_id": 1, "bot_username": 1,
                      "clone_profile": 1, "is_active": 1, "is_deleted": 1}
        cursor = self.bots.find({}, projection)
        return await cursor.to_list(length=None)

    @staticmethod
    def _clone_directory_projection() -> dict:
        return {"_id": 1, "owner_id": 1, "bot_username": 1,
                "clone_profile": 1, "is_active": 1, "is_deleted": 1}

    @staticmethod
    def _clone_directory_valid_filter() -> dict:
        """DB-side filter for valid, non-deleted clone directory records.

        Mirrors `valid_registry_id` + the owner directory's deletion rules:
        numeric `_id` in Telegram range (doubles excluded; bools/strings fail
        the range comparison), and `is_deleted` missing or exactly False.
        Callers still re-validate in Python before display.
        """
        return {
            "_id": {"$gt": 0, "$lt": 9223372036854775807, "$not": {"$type": "double"}},
            "$or": [{"is_deleted": {"$exists": False}}, {"is_deleted": False}],
        }

    async def get_clone_directory_page(self, page: int, page_size: int) -> list:
        """Return one bounded page of valid directory records, sorted by `_id`."""
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        cursor = (self.bots.find(self._clone_directory_valid_filter(),
                                 self._clone_directory_projection())
                  .sort("_id", 1).skip((page - 1) * page_size).limit(page_size))
        return await cursor.to_list(length=page_size)

    async def count_clone_directory_records(self) -> int:
        """Count valid, non-deleted directory records without loading them."""
        return await self.bots.count_documents(self._clone_directory_valid_filter())

    async def count_invalid_clone_directory_records(self) -> int:
        """Count non-deleted records excluded as invalid (never fetched)."""
        non_deleted = await self.bots.count_documents({"is_deleted": {"$ne": True}})
        valid = await self.bots.count_documents(self._clone_directory_valid_filter())
        return max(0, non_deleted - valid)

    async def clone_directory_profile_counts(self) -> dict:
        """Bounded profile aggregation for `/clonestats` (no record loading).

        Missing `clone_profile` means the legacy File Store profile; any
        other present value outside filestore/group/music counts as unknown.
        """
        filt = self._clone_directory_valid_filter()
        total = await self.bots.count_documents(filt)
        missing = await self.bots.count_documents(
            {**filt, "clone_profile": {"$exists": False}})
        groups: dict = {}
        async for row in self.bots.aggregate([
                {"$match": filt},
                {"$group": {"_id": "$clone_profile", "count": {"$sum": 1}}},
        ]):
            groups[row.get("_id")] = row.get("count", 0)
        filestore = missing + int(groups.get("filestore", 0) or 0)
        group = int(groups.get("group", 0) or 0)
        music = int(groups.get("music", 0) or 0)
        unknown = max(0, total - filestore - group - music)
        return {"filestore": filestore, "group": group, "music": music,
                "unknown": unknown, "total": total}

    async def iter_clone_directory_ids(self):
        """Stream valid directory `_id`s one at a time (bounded memory)."""
        cursor = self.bots.find(self._clone_directory_valid_filter(),
                                {"_id": 1}).sort("_id", 1)
        async for doc in cursor:
            yield doc["_id"]

    async def get_all_active_bots(self) -> list:
        """Get active File Store bots for the in-process File Store worker.

        Legacy records without ``clone_profile`` remain File Store clones. Group
        and Music runtimes are separate processes and must not be started by the
        File Store engine/watchdog.
        """
        cursor = self.bots.find({
            "is_active": True,
            "is_deleted": {"$ne": True},
            "$or": [{"clone_profile": {"$exists": False}},
                    {"clone_profile": "filestore"}],
        })
        return await cursor.to_list(length=None)

    async def get_active_bots_for_profile(self, profile: str) -> list:
        """Get active clones owned by a profile-specific worker runtime."""
        if profile not in ("group", "music"):
            raise ValueError("Unsupported clone worker profile")
        cursor = self.bots.find({"is_active": True, "clone_profile": profile,
                                 "is_deleted": {"$ne": True}})
        return await cursor.to_list(length=None)

    async def set_profile_active(self, bot_id: int, owner_id: int,
                                 profile: str, active: bool) -> bool:
        """Change a profile clone's desired state using owner/profile CAS."""
        if profile not in ("group", "music") or type(active) is not bool:
            return False
        now = datetime.now(timezone.utc)
        update = {"$set": {"is_active": active}}
        if active:
            update["$set"]["last_active"] = now
            update["$unset"] = {"deactivated_at": "", "deactivated_reason": "",
                                "inactive_warned": ""}
        result = await self.bots.update_one(
            {"_id": bot_id, "owner_id": owner_id, "is_deleted": {"$ne": True},
             "clone_profile": profile, "is_active": {"$ne": active}},
            update,
        )
        return result.matched_count == 1

    async def set_music_runtime_state(self, bot_id: int, state: str) -> bool:
        """Publish a Music worker observation; not a distributed lease."""
        if state not in ("starting", "running", "stopping", "stopped", "uncertain"):
            return False
        result = await self.bots.update_one(
            {"_id": bot_id, "clone_profile": "music", "is_deleted": {"$ne": True}},
            {"$set": {"music_runtime_state": state,
                      "music_runtime_updated_at": datetime.now(timezone.utc)}},
        )
        return result.matched_count == 1

    async def recover_music_profile(self, bot_id: int, owner_id: int,
                                    target_profile: str = "filestore") -> bool:
        """Owner-confirmed fallback when Music is inactive and no fresh active report exists.

        Missing report timestamps fail closed. Stale reports pass only with the
        owner's explicit confirmation in the calling UI; this is not a lease.
        """
        if target_profile not in ("filestore", "group"):
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=MUSIC_RUNTIME_STALE_SECONDS)
        result = await self.bots.update_one(
            {"_id": bot_id, "owner_id": owner_id, "is_deleted": {"$ne": True},
             "clone_profile": "music", "is_active": False,
             "$or": [
                 {"music_runtime_state": {"$nin": list(_MUSIC_ACTIVE_RUNTIME_STATES)}},
                 {"music_runtime_state": {"$in": list(_MUSIC_ACTIVE_RUNTIME_STATES)},
                  "music_runtime_updated_at": {"$lt": cutoff}},
             ]},
            {"$set": {"clone_profile": target_profile},
             "$unset": {"music_runtime_state": "", "music_runtime_updated_at": ""}},
        )
        return result.matched_count == 1

    async def get_active_clone_sweep_records(self) -> list:
        """Projected active records for the idle sweep (no unrelated fields)."""
        projection = {"_id": 1, "owner_id": 1, "is_active": 1, "is_deleted": 1,
                      "bot_token_encrypted": 1, "clone_profile": 1,
                      "last_active": 1, "created_at": 1, "bot_username": 1,
                      "inactive_warned": 1}
        cursor = self.bots.find({"is_active": True}, projection)
        return await cursor.to_list(length=None)

    async def count_all_clone_records(self) -> int:
        """Count every registry record (including deleted/invalid) without loading."""
        return await self.bots.count_documents({})

    async def count_active_clone_records(self) -> int:
        """Count records marked active without loading full bot records."""
        return await self.bots.count_documents({"is_active": True})

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

    async def update_music_settings_defaults(self, bot_id: int, owner_id: int,
                                             updates: dict) -> bool:
        """Owner/profile-checked atomic update for Music-wide defaults."""
        if not isinstance(updates, dict) or not updates:
            return False
        allowed = {"dj_mode", "autoplay", "loop_mode", "volume", "speed"}
        if not set(updates).issubset(allowed):
            return False
        if any(key in updates and type(updates[key]) is not bool
               for key in ("dj_mode", "autoplay")):
            return False
        if "loop_mode" in updates and updates["loop_mode"] not in ("off", "one", "all"):
            return False
        if "volume" in updates and (type(updates["volume"]) is not int
                                     or not 10 <= updates["volume"] <= 200):
            return False
        if "speed" in updates:
            value = updates["speed"]
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not 0.25 <= value <= 4.0):
                return False
        result = await self.bots.update_one(
            {"_id": bot_id, "owner_id": owner_id, "is_deleted": {"$ne": True},
             "clone_profile": "music"},
            {"$set": {f"music_settings_defaults.{key}": value
                      for key, value in updates.items()}},
        )
        return result.matched_count == 1

    async def set_music_session(self, bot_id: int, owner_id: int, session_encrypted: str) -> bool:
        """Store an encrypted assistant session only for its current live owner."""
        if not isinstance(session_encrypted, str) or not session_encrypted.startswith("enc:"):
            return False
        result = await self.bots.update_one(
            {"_id": bot_id, "owner_id": owner_id, "is_deleted": {"$ne": True}},
            {"$set": {"music_session_encrypted": session_encrypted}},
        )
        return result.matched_count == 1

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

    async def reactivate_inactive_if_unchanged(self, bot: dict) -> bool:
        """Mark a previously read inactive clone active, only if key state still matches.

        A failed acknowledgement is uncertain and must not be retried blindly.
        This is not a lifecycle lease or transaction with the Telegram client.
        """
        match = {"_id": bot["_id"], "owner_id": bot["owner_id"], "is_active": False}
        for field in ("is_deleted", "bot_token_encrypted", "clone_profile", "last_active"):
            match[field] = ({"$exists": True, "$eq": bot[field]} if field in bot
                            else {"$exists": False})
        update = {"$set": {"is_active": True, "last_active": datetime.now(timezone.utc)},
                  "$unset": {"deactivated_at": "", "deactivated_reason": "", "inactive_warned": ""}}
        result = await self.bots.update_one(match, update)
        if not result.acknowledged:
            raise RuntimeError("Reactivation write was not acknowledged")
        return result.matched_count == 1

    async def deactivate_inactive_if_unchanged(self, bot: dict) -> bool:
        """Conditionally mark a previously inspected active clone inactive.

        This write is not atomic with stopping a Telegram client, other writers,
        or in-memory activity. Failed acknowledgements remain uncertain.
        """
        match = {"_id": bot["_id"], "owner_id": bot["owner_id"], "is_active": True}
        for field in ("is_deleted", "bot_token_encrypted", "clone_profile", "last_active", "created_at"):
            match[field] = ({"$exists": True, "$eq": bot[field]} if field in bot
                            else {"$exists": False})
        result = await self.bots.update_one(
            match,
            {"$set": {"is_active": False, "deactivated_at": datetime.now(timezone.utc),
                      "deactivated_reason": "inactive"}, "$unset": {"inactive_warned": ""}},
        )
        if not result.acknowledged:
            raise RuntimeError("Inactivity update was not acknowledged")
        return result.matched_count == 1

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

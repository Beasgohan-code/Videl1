
from filestore.database.mongo import get_db
from filestore.fs_config import LOGGER

log = LOGGER(__name__)


class WorkerDB:
    """
    Database operations scoped to a single worker bot.

    All collections are prefixed with the bot_id to ensure
    complete isolation between different user bots.
    """

    def __init__(self, bot_id: int):
        self.bot_id = bot_id
        db = get_db()
        prefix = f"bot_{bot_id}"
        self.users = db[f"{prefix}_users"]
        self.admins = db[f"{prefix}_admins"]
        self.channels = db[f"{prefix}_channels"]
        self.banned = db[f"{prefix}_banned"]
        self.request_fsub = db[f"{prefix}_request_fsub"]
        self.del_timer = db[f"{prefix}_del_timer"]
        self.verify = db[f"{prefix}_verify"]

    # =========================================================================
    # USER TRACKING
    # =========================================================================

    async def present_user(self, user_id: int) -> bool:
        """Check if a user exists."""
        return bool(await self.users.find_one({"_id": user_id}))

    async def add_user(self, user_id: int):
        """Add a user to the bot's user list."""
        if not await self.present_user(user_id):
            await self.users.insert_one({"_id": user_id})

    async def full_userbase(self) -> list:
        """Return all user IDs."""
        docs = await self.users.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    async def del_user(self, user_id: int):
        """Remove a user from the bot's user list."""
        await self.users.delete_one({"_id": user_id})

    async def total_users(self) -> int:
        """Count total users."""
        return await self.users.count_documents({})

    # =========================================================================
    # ADMIN MANAGEMENT
    # =========================================================================

    async def admin_exist(self, admin_id: int) -> bool:
        """Check if a user is an admin."""
        return bool(await self.admins.find_one({"_id": admin_id}))

    async def add_admin(self, admin_id: int):
        """Add an admin."""
        if not await self.admin_exist(admin_id):
            await self.admins.insert_one({"_id": admin_id})

    async def del_admin(self, admin_id: int):
        """Remove an admin."""
        await self.admins.delete_one({"_id": admin_id})

    async def get_all_admins(self) -> list:
        """Return all admin IDs."""
        docs = await self.admins.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    # =========================================================================
    # BAN MANAGEMENT
    # =========================================================================

    async def ban_user_exist(self, user_id: int) -> bool:
        """Check if a user is banned."""
        return bool(await self.banned.find_one({"_id": user_id}))

    async def add_ban_user(self, user_id: int):
        """Ban a user."""
        if not await self.ban_user_exist(user_id):
            await self.banned.insert_one({"_id": user_id})

    async def del_ban_user(self, user_id: int):
        """Unban a user."""
        await self.banned.delete_one({"_id": user_id})

    async def get_ban_users(self) -> list:
        """Return all banned user IDs."""
        docs = await self.banned.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    # =========================================================================
    # AUTO-DELETE TIMER
    # =========================================================================

    async def set_del_timer(self, value: int):
        """Set the auto-delete timer value (seconds)."""
        existing = await self.del_timer.find_one({})
        if existing:
            await self.del_timer.update_one({}, {"$set": {"value": value}})
        else:
            await self.del_timer.insert_one({"value": value})

    async def get_del_timer(self) -> int:
        """Get the auto-delete timer value. Returns 0 if not set."""
        data = await self.del_timer.find_one({})
        return data.get("value", 0) if data else 0

    # =========================================================================
    # FORCE-SUBSCRIBE CHANNEL MANAGEMENT
    # =========================================================================

    async def channel_exist(self, channel_id: int) -> bool:
        """Check if a force-sub channel exists."""
        return bool(await self.channels.find_one({"_id": channel_id}))

    async def add_channel(self, channel_id: int, mode: str = "off"):
        """Add a force-sub channel. Mode: 'off' = force join, 'on' = request-based."""
        if not await self.channel_exist(channel_id):
            await self.channels.insert_one({"_id": channel_id, "mode": mode})

    async def rem_channel(self, channel_id: int):
        """Remove a force-sub channel."""
        await self.channels.delete_one({"_id": channel_id})
        # Also clean up request data for this channel
        await self.request_fsub.delete_one({"_id": channel_id})

    async def show_channels(self) -> list:
        """Return all force-sub channel IDs."""
        docs = await self.channels.find().to_list(length=None)
        return [doc["_id"] for doc in docs]

    async def get_channel_mode(self, channel_id: int) -> str:
        """Get a channel's force-sub mode ('on' = request-based, 'off' = force join)."""
        data = await self.channels.find_one({"_id": channel_id})
        return data.get("mode", "off") if data else "off"

    async def set_channel_mode(self, channel_id: int, mode: str):
        """Set a channel's force-sub mode."""
        await self.channels.update_one(
            {"_id": channel_id},
            {"$set": {"mode": mode}},
            upsert=True,
        )

    # =========================================================================
    # REQUEST-BASED FORCE-SUB USER TRACKING
    # =========================================================================

    async def req_user(self, channel_id: int, user_id: int):
        """Record that a user sent a join request for a channel."""
        try:
            await self.request_fsub.update_one(
                {"_id": int(channel_id)},
                {"$addToSet": {"user_ids": int(user_id)}},
                upsert=True,
            )
        except Exception as e:
            log.error(f"Failed to add user to request list: {e}")

    async def del_req_user(self, channel_id: int, user_id: int):
        """Remove a user from the join request list for a channel."""
        await self.request_fsub.update_one(
            {"_id": channel_id},
            {"$pull": {"user_ids": user_id}},
        )

    async def req_user_exist(self, channel_id: int, user_id: int) -> bool:
        """Check if a user has a pending join request for a channel."""
        try:
            found = await self.request_fsub.find_one(
                {"_id": int(channel_id), "user_ids": int(user_id)}
            )
            return bool(found)
        except Exception as e:
            log.error(f"Failed to check request list: {e}")
            return False

    # =========================================================================
    # SHORTENER VERIFICATION TRACKING
    # =========================================================================

    async def set_verified(self, user_id: int):
        """Mark a user as shortener-verified with current timestamp."""
        from datetime import datetime, timezone
        await self.verify.update_one(
            {"_id": user_id},
            {"$set": {"verified_at": datetime.now(timezone.utc)}},
            upsert=True,
        )

    async def is_verified(self, user_id: int, expire_seconds: int) -> bool:
        """Check if a user's verification is still valid."""
        from datetime import datetime, timezone
        doc = await self.verify.find_one({"_id": user_id})
        if not doc:
            return False
        verified_at = doc.get("verified_at")
        if not verified_at:
            return False
        if verified_at.tzinfo is None:  # Mongo returns naive UTC datetimes
            verified_at = verified_at.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - verified_at).total_seconds()
        return elapsed < expire_seconds

    async def new_verify_token(self, user_id: int) -> str:
        """One-time token for the shortener link (``/start verify_<token>``).
        Typing ``/start verify`` by hand no longer verifies anyone."""
        import secrets
        from datetime import datetime, timezone
        token = secrets.token_urlsafe(9)
        await self.verify.update_one(
            {"_id": user_id},
            {"$set": {"token": token, "token_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return token

    async def consume_verify_token(self, user_id: int, token: str, max_age: int = 86400) -> bool:
        """Mark the user verified if *token* is theirs and fresh (single use)."""
        import hmac
        from datetime import datetime, timezone
        doc = await self.verify.find_one({"_id": user_id})
        if not doc or not doc.get("token") or not hmac.compare_digest(str(doc["token"]), str(token)):
            return False
        made = doc.get("token_at")
        if made is not None:
            if made.tzinfo is None:
                made = made.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - made).total_seconds() > max_age:
                return False
        await self.verify.update_one(
            {"_id": user_id},
            {"$set": {"verified_at": datetime.now(timezone.utc)}, "$unset": {"token": "", "token_at": ""}},
        )
        return True

    # =========================================================================
    # DATA TRANSFER — Copy data from another bot
    # =========================================================================

    async def copy_data_from(self, source_bot_id: int) -> dict:
        """Copy all collections from source_bot_id to this bot. Returns stats."""
        db = get_db()
        source_prefix = f"bot_{source_bot_id}"
        dest_prefix = f"bot_{self.bot_id}"
        stats = {}

        collections = await db.list_collection_names()
        source_collections = [c for c in collections if c.startswith(source_prefix)]

        for src_col_name in source_collections:
            suffix = src_col_name[len(source_prefix):]  # e.g. "_users"
            dest_col_name = f"{dest_prefix}{suffix}"

            src_col = db[src_col_name]
            dest_col = db[dest_col_name]

            docs = await src_col.find().to_list(length=None)
            if docs:
                # Clear destination first to avoid duplicates
                await dest_col.delete_many({})
                await dest_col.insert_many(docs)
                stats[suffix.strip("_")] = len(docs)
                log.info(f"Transferred {len(docs)} docs: {src_col_name} → {dest_col_name}")
            else:
                stats[suffix.strip("_")] = 0

        return stats

    # =========================================================================
    # CLEANUP — Used when deleting a bot
    # =========================================================================

    async def drop_all_collections(self):
        """Drop all collections for this bot. Use when permanently purging a bot."""
        db = get_db()
        prefix = f"bot_{self.bot_id}"
        collections = await db.list_collection_names()
        for col_name in collections:
            if col_name.startswith(prefix):
                await db.drop_collection(col_name)
                log.info(f"Dropped collection: {col_name}")

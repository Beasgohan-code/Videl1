import motor.motor_asyncio
import datetime
from config import DB_NAME, DB_URI, FREE_LIMIT_DAILY
from logger import LOGGER
logger = LOGGER(__name__)
class Database:
    # Every per-user setter upserts: users already known to Videl before the saver document was created
    # (or whose add_user failed) used to have /login sessions, bans, dump chats, captions … silently dropped,
    # and their saves were never counted against the daily limit.
   
    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri)
        self.db = self._client[database_name]
        self.col = self.db.users
    def new_user(self, id, name):
        return dict(
            id = id,
            name = name,
            session = None,
            daily_usage = 0, # Added: Track saves
            limit_reset_time = None # Added: Track 24h reset time
        )
   
    async def add_user(self, id, name):
        # idempotent: two handlers seeing a new user at once can't create duplicate documents
        user = self.new_user(id, name)
        user.pop("id")
        await self.col.update_one({'id': int(id)}, {'$setOnInsert': user}, upsert=True)
        logger.info(f"New user added to DB: {id} - {name}")
   
    async def is_user_exist(self, id):
        user = await self.col.find_one({'id':int(id)})
        return bool(user)
   
    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count
    async def get_all_users(self):
        return self.col.find({})
    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})
        logger.info(f"User deleted from DB: {user_id}")
    async def set_session(self, id, session):
        await self.col.update_one({'id': int(id)}, {'$set': {'session': session}}, upsert=True)
    async def get_session(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('session')
    # Caption Support
    async def set_caption(self, id, caption):
        await self.col.update_one({'id': int(id)}, {'$set': {'caption': caption}}, upsert=True)
    async def get_caption(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('caption', None)
    async def del_caption(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'caption': ""}})
    # Thumbnail Support
    async def set_thumbnail(self, id, thumbnail):
        await self.col.update_one({'id': int(id)}, {'$set': {'thumbnail': thumbnail}}, upsert=True)
    async def get_thumbnail(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('thumbnail', None)
    async def del_thumbnail(self, id):
        await self.col.update_one({'id': int(id)}, {'$unset': {'thumbnail': ""}})
    # Premium Support
    async def add_premium(self, id, expiry_date):
        # When user buys premium, we also reset their limits just in case
        await self.col.update_one({'id': int(id)}, {
            '$set': {
                'is_premium': True,
                'premium_expiry': expiry_date,
                'daily_usage': 0,
                'limit_reset_time': None
            }
        }, upsert=True)
        logger.info(f"User {id} granted premium until {expiry_date}")
    async def remove_premium(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': {'is_premium': False, 'premium_expiry': None}}, upsert=True)
        logger.info(f"User {id} removed from premium")
    async def _expire_if_needed(self, user: dict) -> bool:
        """Downgrade users whose premium_expiry date has passed. Returns True if still premium."""
        if not user.get('is_premium'):
            return False
        exp = user.get('premium_expiry')
        if not exp:
            return True  # permanent
        try:
            exp_date = exp if isinstance(exp, datetime.date) else datetime.date.fromisoformat(str(exp)[:10])
            if isinstance(exp_date, datetime.datetime):
                exp_date = exp_date.date()
            if exp_date < datetime.date.today():
                await self.remove_premium(user['id'])
                return False
        except Exception:
            pass
        return True

    async def check_premium(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        if user and await self._expire_if_needed(user):
            return user.get('premium_expiry') or "Permanent"
        return None
    async def get_premium_users(self):
        return self.col.find({'is_premium': True})
    # Ban Support
    async def ban_user(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': {'is_banned': True}}, upsert=True)
        logger.warning(f"User banned: {id}")
    async def unban_user(self, id):
        await self.col.update_one({'id': int(id)}, {'$set': {'is_banned': False}}, upsert=True)
        logger.info(f"User unbanned: {id}")
    async def is_banned(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return bool(user and user.get('is_banned', False))
    # Dump Chat Support
    async def set_dump_chat(self, id, chat_id):
        await self.col.update_one({'id': int(id)}, {'$set': {'dump_chat': int(chat_id)}}, upsert=True)
    async def get_dump_chat(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('dump_chat', None)
    # Delete/Replace Words Support
    async def set_delete_words(self, id, words):
        await self.col.update_one({'id': int(id)}, {'$addToSet': {'delete_words': {'$each': words}}}, upsert=True)
    async def get_delete_words(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('delete_words', [])
    async def remove_delete_words(self, id, words):
        await self.col.update_one({'id': int(id)}, {'$pull': {'delete_words': {'$in': words}}})
    async def set_replace_words(self, id, repl_dict):
        user = await self.col.find_one({'id': int(id)}) or {}
        current_repl = user.get('replace_words', {})
        current_repl.update(repl_dict)
        await self.col.update_one({'id': int(id)}, {'$set': {'replace_words': current_repl}}, upsert=True)
    async def get_replace_words(self, id):
        user = await self.col.find_one({'id': int(id)}) or {}
        return user.get('replace_words', {})
    async def remove_replace_words(self, id, words):
        user = await self.col.find_one({'id': int(id)}) or {}
        current_repl = user.get('replace_words', {})
        for w in words:
            current_repl.pop(w, None)
        await self.col.update_one({'id': int(id)}, {'$set': {'replace_words': current_repl}}, upsert=True)
    # --------------------------------------------------------
    # NEW FEATURES: Daily Limits (Free User Restriction)
    # --------------------------------------------------------
    async def check_limit(self, id):
        """
        Checks if a user has hit their daily limit.
        Returns: True if BLOCKED (limit reached), False if ALLOWED.
        """
        user = await self.col.find_one({'id': int(id)}) or {}
        if not user:
            return False # Should be added via add_user, but safe fallback
       
        # 1. Premium Check: Always allowed
        if await self._expire_if_needed(user):
            return False
        # 2. Check Time Reset
        now = datetime.datetime.now()
        reset_time = user.get('limit_reset_time')
       
        # If reset time has passed or was never set, reset count to 0
        if reset_time is None or now >= reset_time:
            await self.col.update_one(
                {'id': int(id)},
                {'$set': {'daily_usage': 0, 'limit_reset_time': None}}
            )
            return False # Allowed (count is 0)
        # 3. Check Count
        usage = user.get('daily_usage', 0)
        if usage >= FREE_LIMIT_DAILY:
            return True # Blocked
       
        return False # Allowed
    async def add_traffic(self, id):
        """
        Increments usage count.
        If it's the first save of the cycle, sets the 24h timer.
        """
        user = await self.col.find_one({'id': int(id)}) or {}
        await self.col.update_one({'id': int(id)}, {'$inc': {'total_saves': 1}}, upsert=True)
        try:
            from core.analytics import bump_later
            bump_later("save")
        except Exception:
            pass
        if user.get('is_premium'):
            return
        now = datetime.datetime.now()
        reset_time = user.get('limit_reset_time')
        # Logic: If timer is not running (None), start it for 24 hours from NOW.
        if reset_time is None:
            new_reset_time = now + datetime.timedelta(hours=24)
            await self.col.update_one(
                {'id': int(id)},
                {'$set': {'daily_usage': 1, 'limit_reset_time': new_reset_time}}, upsert=True
            )
        else:
            # Just increment
            await self.col.update_one(
                {'id': int(id)},
                {'$inc': {'daily_usage': 1}}
            )
db = Database(DB_URI, DB_NAME)

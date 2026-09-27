
import motor.motor_asyncio
from filestore.fs_config import MONGO_URI, MONGO_DB_NAME, LOGGER

log = LOGGER(__name__)

_client: motor.motor_asyncio.AsyncIOMotorClient = None
_db: motor.motor_asyncio.AsyncIOMotorDatabase = None


def get_motor_client() -> motor.motor_asyncio.AsyncIOMotorClient:
    """Return the singleton Motor client, creating it if needed."""
    global _client
    if _client is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        log.info("MongoDB Motor client initialized")
    return _client


def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    """Return the default database instance."""
    global _db
    if _db is None:
        _db = get_motor_client()[MONGO_DB_NAME]
        log.info(f"Using database: {MONGO_DB_NAME}")
    return _db

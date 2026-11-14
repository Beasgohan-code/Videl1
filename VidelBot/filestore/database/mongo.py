import motor.motor_asyncio
from filestore.fs_config import MONGO_URI, MONGO_DB_NAME, LOGGER

log = LOGGER(__name__)

_client: motor.motor_asyncio.AsyncIOMotorClient | None = None
_db: motor.motor_asyncio.AsyncIOMotorDatabase | None = None

CLIENT_TIMEOUT_KW = {
    "serverSelectionTimeoutMS": 10_000,
    "connectTimeoutMS": 10_000,
    "socketTimeoutMS": 30_000,
}


def get_motor_client() -> motor.motor_asyncio.AsyncIOMotorClient:
    """Return the singleton Motor client, creating it with bounded timeouts if needed."""
    global _client
    if _client is None:
        _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI, **CLIENT_TIMEOUT_KW)
        log.info("MongoDB Motor client initialized")
    return _client


def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    """Return the default database instance."""
    global _db
    if _db is None:
        _db = get_motor_client()[MONGO_DB_NAME]
        log.info(f"Using database: {MONGO_DB_NAME}")
    return _db


def close_motor_client() -> None:
    """Close the shared Motor connection pool on process shutdown."""
    global _client, _db
    if _client is not None:
        try:
            _client.close()
        except Exception as exc:
            log.debug(f"MongoDB client close warning: {exc}")
        _client = None
        _db = None

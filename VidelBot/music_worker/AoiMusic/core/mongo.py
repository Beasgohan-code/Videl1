import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient as _mongo_client_
from pymongo import MongoClient

import config

from ..logging import LOGGER

# Shared fallback cluster (used only when MONGO_DB_URI is not set).
# Kept in an env var so no credentials live in the source.
TEMP_MONGODB = os.getenv("TEMP_MONGO_DB_URI", None)

DB_NAME = "queen"

if config.MONGO_DB_URI:
    _mongo_async_ = _mongo_client_(
        config.MONGO_DB_URI,
        maxPoolSize=50,
        minPoolSize=5,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=10000,
    )
    _mongo_sync_ = MongoClient(
        config.MONGO_DB_URI,
        maxPoolSize=10,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    mongodb = _mongo_async_[DB_NAME]
    pymongodb = _mongo_sync_[DB_NAME]
else:
    LOGGER(__name__).warning(
        "No MONGO DB URI found; using the configured fallback database"
    )
    if not TEMP_MONGODB:
        LOGGER(__name__).critical(
            "Neither MONGO_DB_URI nor TEMP_MONGO_DB_URI is set. "
            "Cannot connect to any database."
        )
        sys.exit(1)
    # Namespace the shared cluster per bot instead of starting a throwaway
    # Telegram client at import time.
    bot_id = (config.BOT_TOKEN or "").split(":")[0]
    _namespace_ = f"aoimusic_{bot_id}" if bot_id else "aoimusic_bot"
    _mongo_async_ = _mongo_client_(
        TEMP_MONGODB,
        maxPoolSize=50,
        minPoolSize=5,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        socketTimeoutMS=10000,
    )
    _mongo_sync_ = MongoClient(
        TEMP_MONGODB,
        maxPoolSize=10,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
    )
    mongodb = _mongo_async_[_namespace_]
    pymongodb = _mongo_sync_[_namespace_]

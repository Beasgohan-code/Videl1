"""
Config bridge for the Clone / Worker FileStore module.
All real values come from the unified `config.py`.
"""
import logging

from config import (  # noqa: F401
    API_ID, API_HASH, BOT_TOKEN, OWNER_ID, OWNERS, MONGO_URI,
    MAIN_LOG_CHANNEL, MAX_BOTS_PER_USER, BOT_CREATION_COOLDOWN,
    HIBERNATION_HOURS, DEFAULT_AUTO_DELETE, ENCRYPTION_KEY,
    BACKEND_API_URL, BACKEND_API_SECRET, PORT, TG_BOT_WORKERS,
    FREEIMAGE_API_KEY, BOT_NAME, CLONE_ENABLED,
)
from config import FS_DB_NAME as MONGO_DB_NAME  # noqa: F401

APP_ID = API_ID
TG_BOT_TOKEN = BOT_TOKEN
DB_URI = MONGO_URI
DB_NAME = MONGO_DB_NAME


def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)

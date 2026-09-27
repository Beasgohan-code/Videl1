"""Logging setup shared by every Videl module."""
import logging
import os
from logging.handlers import RotatingFileHandler

SHORT_LOG_FORMAT = "[%(asctime)s - %(levelname)s] - %(name)s - %(message)s"

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format=SHORT_LOG_FORMAT,
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        RotatingFileHandler("logs/videl.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)

for noisy in ("pyrogram", "urllib3", "pymongo", "motor", "httpx", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)

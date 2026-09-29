"""
Videl • Video Encoder module.

The encoder no longer creates its own Client – it runs inside the single
Videl client (see client.py). Settings come from the unified config.py.
"""
import logging
import os
import time
from io import BytesIO, StringIO

import config as _cfg
from client import app  # the shared Videl client  # noqa: F401

botStartTime = time.time()

api_id = _cfg.API_ID
api_hash = _cfg.API_HASH
bot_token = _cfg.BOT_TOKEN

database = _cfg.DB_URI
session = _cfg.ENCODER_DB_NAME

drive_dir = _cfg.DRIVE_DIR
index = _cfg.INDEX_URL

download_dir = _cfg.DOWNLOAD_DIR
encode_dir = _cfg.ENCODE_DIR

owner = list(_cfg.OWNERS)
sudo_users = list(_cfg.SUDO_USERS)
everyone = list(_cfg.EVERYONE_CHATS)
all = everyone + sudo_users + owner  # noqa: A001  (name kept for compatibility)
PUBLIC = _cfg.ENCODER_PUBLIC

log = _cfg.LOG_CHANNEL or (owner[0] if owner else None)

data = []

PROGRESS = """
• {0} of {1}
• Speed: {2}
• ETA: {3}
"""

video_mimetype = [
    "video/x-flv", "video/mp4", "application/x-mpegURL", "video/MP2T", "video/3gpp",
    "video/quicktime", "video/x-msvideo", "video/x-ms-wmv", "video/x-matroska",
    "video/webm", "video/x-m4v", "video/mpeg",
]


def memory_file(name=None, contents=None, *, bytes=True):
    if isinstance(contents, str) and bytes:
        contents = contents.encode()
    file = BytesIO() if bytes else StringIO()
    if name:
        file.name = name
    if contents:
        file.write(contents)
        file.seek(0)
    return file


for _d in (download_dir, encode_dir, "VideoEncoder/utils/extras"):
    os.makedirs(_d, exist_ok=True)

LOGGER = logging.getLogger("VideoEncoder")

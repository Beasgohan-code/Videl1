"""The single Pyrogram client shared by every Videl module."""
from pyrogram import Client

import config

app = Client(
    name="Videl",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workers=config.TG_BOT_WORKERS,
    sleep_threshold=30,
    max_concurrent_transmissions=5,
    in_memory=False,
)

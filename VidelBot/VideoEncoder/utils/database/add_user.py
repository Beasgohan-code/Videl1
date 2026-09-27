

from pyrogram import Client
from pyrogram.types import Message

from ... import log
from .access_db import db


async def AddUserToDatabase(bot: Client, cmd: Message):
    # New users are reported once, globally, as #NewUser by core/middleware.py (core/botlog.py).
    if cmd.from_user and not await db.is_user_exist(cmd.from_user.id):
        await db.add_user(cmd.from_user.id)


#

from pyrogram import filters
from pyrogram.types import Message

from strings import command

from config import BANNED_USERS
from AoiMusic import app
from AoiMusic.core.call import AoiCall
from AoiMusic.utils.database import is_muted, mute_off, mute_on
from AoiMusic.utils.decorators import AdminRightsCheck
from AoiMusic.utils.rich_msg import reply_note, status_card


@app.on_message(command("MUTE_COMMAND") & filters.group & ~BANNED_USERS)
@AdminRightsCheck
async def mute_admin(cli, message: Message, _, chat_id):
    if not len(message.command) == 1 or message.reply_to_message:
        return
    if await is_muted(chat_id):
        return await reply_note(message, _["admin_5"])
    await mute_on(chat_id)
    await AoiCall.mute_stream(chat_id)
    await reply_note(message,
        _["admin_6"].format(message.from_user.mention)
    )


@app.on_message(command("UNMUTE_COMMAND") & filters.group & ~BANNED_USERS)
@AdminRightsCheck
async def unmute_admin(Client, message: Message, _, chat_id):
    if not len(message.command) == 1 or message.reply_to_message:
        return
    if not await is_muted(chat_id):
        return await reply_note(message, _["admin_7"])
    await mute_off(chat_id)
    await AoiCall.unmute_stream(chat_id)
    await reply_note(message,
        _["admin_8"].format(message.from_user.mention)
    )

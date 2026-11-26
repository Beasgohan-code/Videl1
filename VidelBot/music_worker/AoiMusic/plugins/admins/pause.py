
#
#

from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from strings import command
from AoiMusic import app
from AoiMusic.core.call import AoiCall
from AoiMusic.utils.database import is_music_playing, music_off
from AoiMusic.utils.decorators import AdminRightsCheck
from AoiMusic.utils.rich_msg import reply_note, status_card


@app.on_message(command("PAUSE_COMMAND") & filters.group & ~BANNED_USERS)
@AdminRightsCheck
async def pause_admin(cli, message: Message, _, chat_id):
    if not len(message.command) == 1:
        return await reply_note(message, _["general_2"])
    if not await is_music_playing(chat_id):
        return await reply_note(message, _["admin_1"])
    await music_off(chat_id)
    await AoiCall.pause_stream(chat_id)
    await reply_note(message, _["admin_2"].format(message.from_user.mention))

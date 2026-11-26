
#

from pyrogram.types import Message

import config
from strings import command
from AoiMusic import app
from AoiMusic.misc import SUDOERS
from AoiMusic.utils.database import add_off, add_on
from AoiMusic.utils.decorators.language import language
from AoiMusic.utils.rich_msg import reply_note, status_card


@app.on_message(command("VIDEOMODE_COMMAND") & SUDOERS)
@language
async def videoloaymode(client, message: Message, _):
    usage = _["vidmode_1"]
    if len(message.command) != 2:
        return await reply_note(message, usage)
    state = message.text.split(None, 1)[1].strip()
    state = state.lower()
    if state == "download":
        await add_on(config.YTDOWNLOADER)
        await reply_note(message, _["vidmode_2"])
    elif state == "m3u8":
        await add_off(config.YTDOWNLOADER)
        await reply_note(message, _["vidmode_3"])
    else:
        await reply_note(message, usage)

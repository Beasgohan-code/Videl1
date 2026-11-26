#
from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from strings import command
from AoiMusic import app
from AoiMusic.utils.database import get_instant_play
from AoiMusic.utils.decorators import language
from AoiMusic.utils.inline.settings import instantplay_markup
from AoiMusic.utils.rich_msg import rich_esc, send_rich


@app.on_message(command("INSTANT_PLAY_COMMAND") & filters.group & ~BANNED_USERS)
@language
async def instantplay_mar(client, message: Message, _):
    instant = await get_instant_play(message.chat.id)
    buttons = instantplay_markup(_, instant)
    status = "✅ Enabled" if instant else "❌ Disabled"
    await send_rich(
        message.chat.id,
        f"<p>{rich_esc(_['instantplay_1'].format(message.chat.title, status))}</p>{buttons}",
    )

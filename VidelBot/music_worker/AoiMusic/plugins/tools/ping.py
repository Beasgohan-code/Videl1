
#
# Ping module — rich-message reply card (Bot API 10.3) + AoiMusic branding.
#

from datetime import datetime

from config import BANNED_USERS, PING_IMG_URL
from strings import command
from AoiMusic import app
from AoiMusic.core.call import AoiCall
from AoiMusic.utils import bot_sys_stats
from AoiMusic.utils.decorators.language import language
from AoiMusic.utils.inline import support_group_markup
from AoiMusic.utils.rich_msg import (
    edit_rich,
    pemoji,
    rich_esc,
    rich_heading,
    rich_img,
    send_rich,
)


@app.on_message(command("PING_COMMAND") & ~BANNED_USERS)
@language
async def ping_com(client, message, _):
    mid = await send_rich(
        message.chat.id,
        rich_img(PING_IMG_URL)
        + rich_heading(f'{pemoji("🏓")} Ping Check', level=4)
        + f"<p>{rich_esc(_['ping_1'].format(app.mention))}</p>"
        + support_group_markup(_),
    )
    start = datetime.now()
    pytgping = await AoiCall.ping()
    UP, CPU, RAM, DISK = await bot_sys_stats()
    resp = (datetime.now() - start).microseconds / 1000
    body = (
        rich_img(PING_IMG_URL)
        + rich_heading(f'{pemoji("🏓")} System Status', level=4)
        + "<p>"
        + f"{pemoji('⚡')} <b>ᴘɪɴɢ</b>: {resp} ms\n\n"
        + f"{pemoji('🕒')} <b>ᴜᴘᴛɪᴍᴇ</b>: {UP}\n"
        + f"{pemoji('💾')} <b>ʀᴀᴍ</b>: {RAM}\n"
        + f"{pemoji('🖥')} <b>ᴄᴘᴜ</b>: {CPU}\n"
        + f"{pemoji('🌩')} <b>ᴅɪsᴋ</b>: {DISK}\n"
        + f"{pemoji('📞')} <b>ᴄʙ ʟᴀᴛᴇɴᴄʏ</b>: {pytgping}"
        + "</p>"
        + support_group_markup(_)
    )
    if mid:
        await edit_rich(message.chat.id, mid, body)
    else:
        # The rich card never went out (e.g. an unfetchable header image):
        # answer plainly instead of leaving the user with silence.
        try:
            await message.reply_text(
                "🏓 System Status\n"
                f"⚡ ᴘɪɴɢ: {resp} ms\n"
                f"🖥 ᴜᴘᴛɪᴍᴇ: {UP}\n"
                f"💾 ʀᴀᴍ: {RAM}\n"
                f"🛑 ᴄᴘᴜ: {CPU}\n"
                f"🌩 ᴅɪsᴋ: {DISK}\n"
                f"📞 ᴄʙ ʟᴀᴛᴇɴᴄʏ: {pytgping}",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except Exception:
            pass

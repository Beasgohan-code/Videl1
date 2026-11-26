
#

import asyncio
import time
from pyrogram.types import Message

from config import PRIVATE_BOT_MODE, PLAYLIST_IMG_URL
from config import adminlist
from strings import get_string
from AoiMusic import Platform, app
from AoiMusic.misc import SUDOERS
from AoiMusic.utils.database import (
    get_cmode,
    get_lang,
    get_playmode,
    get_playtype,
    is_active_chat,
    is_commanddelete_on,
    is_maintenance,
    is_served_private_chat,
)
from AoiMusic.utils.inline import botplaylist_markup
from AoiMusic.utils.rich_msg import (
    pemoji,
    rich_button,
    rich_button_row,
    rich_esc,
    rich_heading,
    rich_img,
    reply_note,
    send_rich,
)

links = {}
_CALL_ACTIVE_CACHE: dict[int, float] = {}
_CALL_ACTIVE_TTL = 20.0


def _bg_delete(msg) -> None:
    async def _del():
        try:
            await msg.delete()
        except Exception:
            pass
    asyncio.create_task(_del())


def PlayWrapper(command):
    async def wrapper(client, message):
        language, maint_ok, playmode, playty = await asyncio.gather(
            get_lang(message.chat.id),
            is_maintenance(),
            get_playmode(message.chat.id),
            get_playtype(message.chat.id),
        )
        _ = get_string(language)
        if message.sender_chat:
            return await send_rich(
                message.chat.id,
                rich_heading(f'{pemoji("⚠")} ᴀᴄᴄᴇꜱꜱ ɴᴏᴛᴇ', level=4)
                + f"<p>{rich_esc(_['general_4'])}</p>"
                + rich_button_row(
                    rich_button("❓ ᴋᴀɪꜱᴇ ꜰɪx ᴋᴀʀᴇ ꜱɪʀ", callback_data="AnonymousAdmin", style="danger"),
                ),
            )

        if maint_ok is False:
            if message.from_user.id not in SUDOERS:
                return

        if PRIVATE_BOT_MODE == str(True):
            if not await is_served_private_chat(message.chat.id):
                await reply_note(message,
                    "**PRIVATE MUSIC BOT**\n\nOnly For Authorized chats from the owner ask my owner to allow your chat first."
                )
                return await app.leave_chat(message.chat.id)
        if await is_commanddelete_on(message.chat.id):
            _bg_delete(message)

        audio_telegram = (
            (message.reply_to_message.audio or message.reply_to_message.voice)
            if message.reply_to_message
            else None
        )
        video_telegram = (
            (message.reply_to_message.video or message.reply_to_message.document)
            if message.reply_to_message
            else None
        )
        url = await Platform.youtube.url(message)
        if audio_telegram is None and video_telegram is None and url is None:
            if len(message.command) < 2:
                if "stream" in message.command:
                    return await reply_note(message, _["str_1"])
                buttons = botplaylist_markup(_)
                return await send_rich(
                    message.chat.id,
                    rich_img(PLAYLIST_IMG_URL)
                    + rich_heading(f'{pemoji("🎵")} Playlist', level=4)
                    + f"<p>{rich_esc(_['playlist_1'])}</p>"
                    + buttons,
                )
        if message.command[0][0] == "c":
            chat_id = await get_cmode(message.chat.id)
            if chat_id is None:
                return await reply_note(message, _["setting_12"])
            try:
                chat = await app.get_chat(chat_id)
            except Exception:
                return await reply_note(message, _["cplay_4"])
            channel = chat.title
        else:
            chat_id = message.chat.id
            channel = None
        already_active = await is_active_chat(chat_id)
        now = time.monotonic()
        if not already_active:
            # Start warming Telegram peer + group call handshake in background
            # while search / API fetch runs.
            try:
                from AoiMusic.core.call import AoiCall
                asyncio.create_task(AoiCall.warm_join(chat_id))
            except Exception:
                pass
            if _CALL_ACTIVE_CACHE.get(chat_id, 0.0) <= now:
                try:
                    is_call_active = (await app.get_chat(chat_id)).is_call_active
                    if not is_call_active:
                        return await reply_note(message,
                            "**No active video chat found **\n\nPlease make sure you started the voicechat."
                        )
                    _CALL_ACTIVE_CACHE[chat_id] = now + _CALL_ACTIVE_TTL
                except Exception:
                    pass
        else:
            _CALL_ACTIVE_CACHE[chat_id] = now + _CALL_ACTIVE_TTL
        if playty != "Everyone":
            if message.from_user.id not in SUDOERS:
                admins = adminlist.get(message.chat.id)
                if not admins:
                    return await reply_note(message, _["admin_18"])
                else:
                    if message.from_user.id not in admins:
                        return await reply_note(message, _["play_4"])
        if message.command[0][0] == "v":
            video = True
        else:
            if "-v" in message.text:
                video = True
            else:
                video = True if message.command[0][1] == "v" else None
        if message.command[0][-1] == "e":
            if not await is_active_chat(chat_id):
                return await reply_note(message, _["play_18"])
            fplay = True
        else:
            fplay = None

        return await command(
            client,
            message,
            _,
            chat_id,
            video,
            channel,
            playmode,
            url,
            fplay,
        )

    return wrapper

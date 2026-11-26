
#
import asyncio
from pyrogram.enums import ChatType

from strings import get_string
from AoiMusic.misc import SUDOERS
from AoiMusic.utils.database import get_lang, is_commanddelete_on, is_maintenance
from AoiMusic.utils.rich_msg import forget_anchor, reply_note, status_card


def language(mystic):
    async def wrapper(_, message, **kwargs):
        try:
            language = await get_lang(message.chat.id)
            language = get_string(language)
        except Exception:
            language = get_string("en")
        if not await is_maintenance():
            if not message.from_user or message.from_user.id not in SUDOERS:
                if message.chat.type == ChatType.PRIVATE:
                    return await reply_note(message, language["maint_4"])
                return
        if await is_commanddelete_on(message.chat.id):
            # The command message is what the anchor hook registered; if it is
            # deleted here without forgetting, the next rich card replies to a
            # dead message and Telegram strips its premium emoji.
            try:
                forget_anchor(message.chat.id, message.id)
            except Exception:
                pass
            async def _del():
                try:
                    await message.delete()
                except Exception:
                    pass
            asyncio.create_task(_del())
        return await mystic(_, message, language)

    return wrapper


def languageCB(mystic):
    async def wrapper(_, CallbackQuery, **kwargs):
        try:
            language = await get_lang(CallbackQuery.message.chat.id)
            language = get_string(language)
        except Exception:
            language = get_string("en")
        if not await is_maintenance():
            if CallbackQuery.from_user.id not in SUDOERS:
                if CallbackQuery.message.chat.type == ChatType.PRIVATE:
                    return await CallbackQuery.answer(
                        language["maint_4"],
                        show_alert=True,
                    )
                return

        return await mystic(_, CallbackQuery, language)

    return wrapper


def LanguageStart(mystic):
    async def wrapper(_, message, **kwargs):
        try:
            language = await get_lang(message.chat.id)
            language = get_string(language)
        except Exception:
            language = get_string("en")
        return await mystic(_, message, language)

    return wrapper

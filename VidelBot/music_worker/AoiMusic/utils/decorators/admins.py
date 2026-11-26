
#

import asyncio
import time
from pyrogram.enums import ChatMemberStatus, ChatType

from config import adminlist
from strings import get_string
from AoiMusic import app
from AoiMusic.misc import SUDOERS
from AoiMusic.utils.database import (
    get_authuser_names,
    get_cmode,
    get_lang,
    is_active_chat,
    is_commanddelete_on,
    is_maintenance,
    is_nonadmin_chat,
)

from ..formatters import int_to_alpha
from AoiMusic.utils.rich_msg import (
    pemoji,
    rich_button,
    rich_button_row,
    rich_esc,
    rich_heading,
    reply_note,
    send_rich,
)


_ADMIN_PRIV_CACHE: dict[tuple[int, int], tuple[bool, float]] = {}
_ADMIN_PRIV_TTL = 30.0


def _bg_delete(msg) -> None:
    async def _del():
        try:
            await msg.delete()
        except Exception:
            pass
    asyncio.create_task(_del())


async def _has_vc_manage_priv(client, chat_id: int, user_id: int) -> bool:
    key = (chat_id, user_id)
    now = time.monotonic()
    cached = _ADMIN_PRIV_CACHE.get(key)
    if cached and cached[1] > now:
        return cached[0]
    member = await client.get_chat_member(chat_id, user_id)
    ok = bool(
        member
        and member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
        and member.privileges is not None
        and member.privileges.can_manage_video_chats
    )
    if len(_ADMIN_PRIV_CACHE) > 2048:
        _ADMIN_PRIV_CACHE.clear()
    _ADMIN_PRIV_CACHE[key] = (ok, now + _ADMIN_PRIV_TTL)
    return ok


def _anon_note(_):
    return (
        rich_heading(f'{pemoji("⚠")} ᴀɴᴏɴʏᴍᴏᴜꜱ ᴀᴅᴍɪɴ ᴅᴇᴛᴇᴄᴛᴇᴅ', level=4)
        + f"<p>{rich_esc(_['general_4'])}</p>"
        + rich_button_row(
            rich_button("❓ ʜᴏᴡ ᴛᴏ ꜰɪx ᴛʜɪꜱ?", callback_data="AnonymousAdmin", style="danger"),
        )
    )


def AdminRightsCheck(mystic):
    async def wrapper(client, message):
        if not await is_maintenance():
            if message.from_user.id not in SUDOERS:
                return
        if await is_commanddelete_on(message.chat.id):
            _bg_delete(message)
        try:
            language = await get_lang(message.chat.id)
            _ = get_string(language)
        except Exception:
            _ = get_string("en")
        if message.sender_chat:
            return await send_rich(message.chat.id, _anon_note(_))
        if message.command[0][0] == "c":
            chat_id = await get_cmode(message.chat.id)
            if chat_id is None:
                return await reply_note(message, _["setting_12"])
            try:
                await app.get_chat(chat_id)
            except Exception:
                return await reply_note(message, _["cplay_4"])
        else:
            chat_id = message.chat.id
        if not await is_active_chat(chat_id):
            return await reply_note(message, _["general_6"])
        is_non_admin = await is_nonadmin_chat(message.chat.id)
        if not is_non_admin:
            if message.from_user.id not in SUDOERS:
                admins = adminlist.get(message.chat.id)
                if not admins:
                    return await reply_note(message, _["admin_18"])
                else:
                    if message.from_user.id not in admins:
                        return await reply_note(message, _["admin_19"])
        return await mystic(client, message, _, chat_id)

    return wrapper


def AdminActual(mystic):
    async def wrapper(client, message):
        if not await is_maintenance():
            if message.from_user.id not in SUDOERS:
                return

        if await is_commanddelete_on(message.chat.id):
            _bg_delete(message)

        try:
            language = await get_lang(message.chat.id)
            _ = get_string(language)
        except Exception:
            _ = get_string("en")

        if message.sender_chat:
            return await send_rich(message.chat.id, _anon_note(_))

        if message.from_user.id not in SUDOERS:
            try:
                if not await _has_vc_manage_priv(client, message.chat.id, message.from_user.id):
                    return await message.reply(_["general_5"])
            except Exception as e:
                return await message.reply(f"Error: {str(e)}")

        return await mystic(client, message, _)

    return wrapper


def ActualAdminCB(mystic):
    async def wrapper(client, CallbackQuery):
        try:
            language = await get_lang(CallbackQuery.message.chat.id)
            _ = get_string(language)
        except Exception:
            _ = get_string("en")

        if not await is_maintenance():
            if CallbackQuery.from_user.id not in SUDOERS:
                return await CallbackQuery.answer(
                    _["maint_4"],
                    show_alert=True,
                )

        if CallbackQuery.message.chat.type == ChatType.PRIVATE:
            return await mystic(client, CallbackQuery, _)

        is_non_admin = await is_nonadmin_chat(CallbackQuery.message.chat.id)
        if not is_non_admin and CallbackQuery.from_user.id not in SUDOERS:
            try:
                if not await _has_vc_manage_priv(
                    app, CallbackQuery.message.chat.id, CallbackQuery.from_user.id
                ):
                    token = await int_to_alpha(CallbackQuery.from_user.id)
                    _check = await get_authuser_names(CallbackQuery.from_user.id)
                    if token not in _check:
                        return await CallbackQuery.answer(
                            _["general_5"],
                            show_alert=True,
                        )
            except Exception as e:
                return await CallbackQuery.answer(f"Error: {str(e)}")

        return await mystic(client, CallbackQuery, _)

    return wrapper

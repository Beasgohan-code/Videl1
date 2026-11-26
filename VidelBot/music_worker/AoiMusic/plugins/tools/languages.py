#
# Language settings — clean flag-only language buttons (no extra premium emojis on language buttons).
#

from pyrogram import filters
from pyrogram.types import Message

from config import BANNED_USERS
from strings import command, get_string, languages_present
from AoiMusic import app
from AoiMusic.utils.database import get_lang, set_lang
from AoiMusic.utils.decorators import ActualAdminCB, language, languageCB
from AoiMusic.utils.premium_emoji import pe
from AoiMusic.utils.rich_msg import (
    edit_rich,
    rich_button,
    rich_button_row,
    rich_esc,
    rich_heading,
    send_rich,
)


def lanuages_keyboard(_):
    rows = []
    row = []
    for lang_key in languages_present:
        row.append(
            rich_button(
                rich_esc(languages_present[lang_key]),
                callback_data=f"languages:{lang_key}",
                style="primary",
            )
        )
        if len(row) == 2:
            rows.append(rich_button_row(*row))
            row = []
    if row:
        rows.append(rich_button_row(*row))
    rows.append(
        rich_button_row(
            rich_button(
                rich_esc(_["BACK_BUTTON"]),
                callback_data="settingsback_helper",
                style="primary",
                emoji="BACK_ARROW",
            ),
            rich_button(
                rich_esc(_["CLOSE_BUTTON"]),
                callback_data="close",
                style="danger",
                emoji="CLOSE",
            ),
        )
    )
    return "".join(rows)


def languages_panel(_, title, chat_id):
    body = rich_heading(f"{pe('GLOBE_LANG')} Language Settings", level=4)
    body += f"<p>{rich_esc(_['setting_1'].format(title, chat_id))}</p>"
    body += lanuages_keyboard(_)
    return body


@app.on_message(command("LANGUAGE_COMMAND") & filters.group & ~BANNED_USERS)
@language
async def langs_command(client, message: Message, _):
    await send_rich(
        message.chat.id,
        languages_panel(_, message.chat.title, message.chat.id),
    )


@app.on_callback_query(filters.regex("LG") & ~BANNED_USERS)
@languageCB
async def lanuagecb(client, CallbackQuery, _):
    try:
        await CallbackQuery.answer()
    except Exception:
        pass
    return await edit_rich(
        CallbackQuery.message.chat.id,
        CallbackQuery.message.id,
        languages_panel(
            _, CallbackQuery.message.chat.title, CallbackQuery.message.chat.id
        ),
    )


@app.on_callback_query(filters.regex(r"languages:(.*?)") & ~BANNED_USERS)
@ActualAdminCB
async def language_markup(client, CallbackQuery, _):
    langauge = (CallbackQuery.data).split(":")[1]
    old = await get_lang(CallbackQuery.message.chat.id)
    if str(old) == str(langauge):
        return await CallbackQuery.answer(
            "You are already using same language", show_alert=True
        )
    try:
        _ = get_string(langauge)
        await CallbackQuery.answer(
            "Your language changed successfully..", show_alert=True
        )
    except Exception:
        return await CallbackQuery.answer(
            "Failed to change language or language is under update",
            show_alert=True,
        )
    await set_lang(CallbackQuery.message.chat.id, langauge)
    return await edit_rich(
        CallbackQuery.message.chat.id,
        CallbackQuery.message.id,
        languages_panel(
            _, CallbackQuery.message.chat.title, CallbackQuery.message.chat.id
        ),
    )

#
# Helper module — unified Music + Management help menu with compact small-caps buttons & Musiciconis icons.
#

import logging
import re
from math import ceil
from typing import Union

from pyrogram import filters, types
from pyrogram.types import Message

from config import BANNED_USERS, START_IMG_URL
from strings import get_string, command, helpers, get_command
from AoiMusic import HELPABLE, app
from AoiMusic.utils.database import get_lang, is_commanddelete_on
from AoiMusic.utils.decorators.language import LanguageStart
from AoiMusic.utils.inline.start import private_help_panel
from AoiMusic.utils.premium_emoji import pe
from AoiMusic.utils.rich_msg import (
    edit_rich,
    rich_button,
    rich_button_row,
    rich_esc,
    rich_heading,
    rich_img,
    rich_md,
    send_rich,
)

COLUMN_SIZE = 4  # button rows per page
NUM_COLUMNS = 3  # buttons per row

_SMALL_CAPS = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ",
)

# Short, mobile-friendly button labels (keeps 3-column rows crisp without wrapping)
_SHORT_LABELS = {
    "notes & filters": "notes",
    "group tools": "tools",
    "blacklists": "blist",
    "security": "secure",
    "muting": "mute",
    "welcome": "welcome",
    "admins": "admins",
    "bans": "bans",
    "warns": "warns",
    "locks": "locks",
    "purge": "purge",
    "afk": "afk",
}

# Dedicated Musiciconis custom emoji key for each Music & Management category
_CATEGORY_EMOJI_MAP = {
    # Music help categories
    "admin": "HEADPHONES",
    "auth": "KEY_ACCESS",
    "active": "BROADCAST_LIVE",
    "play": "PLAY",
    "g-cast": "MEGAPHONE",
    "bot": "BOT",
    "p-list": "QUEUE_LIST",
    "b-list": "BAN_BLOCK",
    "dev": "TERMINAL_DEV",
    # Management modules
    "admins": "SHIELD_ADMIN",
    "bans": "BAN_BLOCK",
    "muting": "VOLUME_MUTE",
    "warns": "WARNING",
    "locks": "LOCK",
    "purge": "TRASH_DELETE",
    "blacklists": "BAN_BLOCK",
    "security": "SHIELD_CHECK",
    "welcome": "USERS_GROUP",
    "notes & filters": "DOCUMENT_RULES",
    "group tools": "SETTINGS",
    "afk": "CLOCK_DURATION",
}

_FALLBACK_KEYS = [
    "MUSIC_NOTE",
    "DOUBLE_MUSIC_NOTES",
    "SPARKLES_AI",
    "STAR",
    "BOLT_FAST",
    "GRID",
    "SETTINGS",
    "SHIELD_ADMIN",
]


def _fmt_btn_label(raw: str) -> str:
    short = _SHORT_LABELS.get(raw.lower(), raw)
    return short.translate(_SMALL_CAPS)


async def format_helper_text(lng, helper_key: str, text: str) -> str:
    if not text:
        return ""
    _ = get_command(lng)

    def _cmd(key):
        return " ".join(f"/{cmd}" for cmd in _[key])

    if helper_key == "Auth":
        return text.format(
            _cmd("AUTH_COMMAND"), _cmd("UNAUTH_COMMAND"), _cmd("AUTHUSERS_COMMAND")
        )
    elif helper_key == "Admin":
        return text.format(
            _cmd("PAUSE_COMMAND"),
            _cmd("RESUME_COMMAND"),
            _cmd("MUTE_COMMAND"),
            _cmd("UNMUTE_COMMAND"),
            _cmd("SKIP_COMMAND"),
            _cmd("STOP_COMMAND"),
            _cmd("SHUFFLE_COMMAND"),
            _cmd("SEEK_COMMAND"),
            _cmd("SEEK_BACK_COMMAND"),
            _cmd("REBOOT_COMMAND"),
            _cmd("LOOP_COMMAND"),
        )
    elif helper_key == "Active":
        return text.format(
            _cmd("ACTIVEVC_COMMAND"),
            _cmd("ACTIVEVIDEO_COMMAND"),
            _cmd("AC_COMMAND"),
            _cmd("STATS_COMMAND"),
        )
    elif helper_key == "Play":
        return text.format(
            _cmd("PLAY_COMMAND"),
            _cmd("PLAYMODE_COMMAND"),
            _cmd("CHANNELPLAY_COMMAND"),
            _cmd("STREAM_COMMAND"),
        )
    elif helper_key == "G-cast":
        return text.format(_cmd("BROADCAST_COMMAND"))
    elif helper_key == "Bot":
        return text.format(
            _cmd("GSTATS_COMMAND"),
            _cmd("SUDOUSERS_COMMAND"),
            _cmd("LYRICS_COMMAND"),
            _cmd("SONG_COMMAND"),
            _cmd("QUEUE_COMMAND"),
            _cmd("AUTHORIZE_COMMAND"),
            _cmd("UNAUTHORIZE_COMMAND"),
            _cmd("AUTHORIZED_COMMAND"),
        )
    elif helper_key == "P-List":
        return text.format(
            _cmd("PLAYLIST_COMMAND"),
            _cmd("DELETE_PLAYLIST_COMMAND"),
            _cmd("PLAY_PLAYLIST_COMMAND"),
            _cmd("PLAY_PLAYLIST_COMMAND"),
        )
    elif helper_key == "B-list":
        return text.format(
            _cmd("BLACKLISTCHAT_COMMAND"),
            _cmd("WHITELISTCHAT_COMMAND"),
            _cmd("BLACKLISTEDCHAT_COMMAND"),
            _cmd("BLOCK_COMMAND"),
            _cmd("UNBLOCK_COMMAND"),
            _cmd("BLOCKED_COMMAND"),
            _cmd("GBAN_COMMAND"),
            _cmd("UNGBAN_COMMAND"),
            _cmd("GBANNED_COMMAND"),
        )
    elif helper_key == "Dev":
        return text.format(
            _cmd("ADDSUDO_COMMAND"),
            _cmd("DELSUDO_COMMAND"),
            _cmd("SUDOUSERS_COMMAND"),
            _cmd("USAGE_COMMAND"),
            _cmd("GETVAR_COMMAND"),
            _cmd("DELVAR_COMMAND"),
            _cmd("SETVAR_COMMAND"),
            _cmd("RESTART_COMMAND"),
            _cmd("UPDATE_COMMAND"),
            _cmd("SPEEDTEST_COMMAND"),
            _cmd("MAINTENANCE_COMMAND"),
            _cmd("LOGGER_COMMAND"),
            _cmd("GETLOG_COMMAND"),
            _cmd("AUTOEND_COMMAND"),
        )
    else:
        return text


def _emoji_for_label(label: str, idx: int) -> str:
    return _CATEGORY_EMOJI_MAP.get(label.lower(), _FALLBACK_KEYS[idx % len(_FALLBACK_KEYS)])


def _cells(helpers_dict, helpable_dict, close, page_n):
    out = []
    offset = len(helpers_dict)
    for i, helper_key in enumerate(helpers_dict):
        out.append(
            (
                _fmt_btn_label(helper_key),
                f"help_helper({helper_key.replace(',', '%44')},{page_n},{int(close)})",
                _emoji_for_label(helper_key, i),
            )
        )
    for i, mod in enumerate(sorted(helpable_dict.values(), key=lambda m: m.__MODULE__.lower())):
        out.append(
            (
                _fmt_btn_label(mod.__MODULE__),
                f"help_module({mod.__MODULE__.lower()},{page_n},{int(close)})",
                _emoji_for_label(mod.__MODULE__, offset + i),
            )
        )
    return out


async def paginate_modules(page_n, chat_id: int, close: bool = False):
    """Unified Music + Management help menu with compact small-caps buttons & Musiciconis icons."""
    language = await get_lang(chat_id)
    helpers_dict = helpers.get(language, helpers.get("en", {}))

    cell = [
        rich_button(
            rich_esc(text),
            callback_data=cb,
            style="primary",
            emoji=emoji_key,
        )
        for text, cb, emoji_key in _cells(helpers_dict, HELPABLE, close, page_n)
    ]

    rows = [
        rich_button_row(*cell[i : i + NUM_COLUMNS])
        for i in range(0, len(cell), NUM_COLUMNS)
    ]

    max_num_pages = ceil(len(rows) / COLUMN_SIZE) if rows else 1
    modulo_page = max(0, min(int(page_n), max_num_pages - 1))

    nav_btns = []
    if modulo_page > 0:
        nav_btns.append(
            rich_button(
                "ᴘʀᴇᴠ",
                style="primary",
                emoji="LEFT_CHEVRON",
                callback_data=f"help_prev({modulo_page - 1},{int(close)})",
            )
        )
    nav_btns.append(
        rich_button(
            "ᴄʟᴏsᴇ" if close else "ʙᴀᴄᴋ",
            style="danger" if close else "primary",
            emoji="CLOSE" if close else "BACK_ARROW",
            callback_data="close" if close else "settingsback_helper",
        )
    )
    if modulo_page < max_num_pages - 1:
        nav_btns.append(
            rich_button(
                "ɴᴇxᴛ",
                style="primary",
                emoji="RIGHT_CHEVRON",
                callback_data=f"help_next({modulo_page + 1},{int(close)})",
            )
        )
    nav = rich_button_row(*nav_btns)

    page_rows = rows[modulo_page * COLUMN_SIZE : COLUMN_SIZE * (modulo_page + 1)] + [
        nav
    ]

    body = rich_heading(f"{pe('GRID')} ᴄᴏᴍᴍᴀɴᴅs &amp; ʜᴇʟᴘ ({modulo_page + 1}/{max_num_pages})", level=4)
    body += "".join(page_rows)
    return body


async def build_help_view(page_n: int, chat_id: int, _, close: bool = False) -> str:
    """Single source of truth for /help, Back, and Prev/Next pagination views."""
    body = rich_img(START_IMG_URL) if START_IMG_URL else ""
    body += f"<p>{rich_esc(_['help_1'])}</p>"
    body += await paginate_modules(page_n, chat_id, close=close)
    return body


@app.on_message(command("HELP_COMMAND") & filters.private & ~BANNED_USERS)
@app.on_callback_query(filters.regex("settings_back_helper") & ~BANNED_USERS)
async def helper_private(
    client: app, update: Union[types.Message, types.CallbackQuery]
):
    is_callback = isinstance(update, types.CallbackQuery)
    chat_id = update.message.chat.id if is_callback else update.chat.id
    language = await get_lang(chat_id)
    _ = get_string(language)
    full_body = await build_help_view(0, chat_id, _, close=not is_callback)
    if is_callback:
        try:
            await update.answer()
        except Exception:
            pass
        await edit_rich(chat_id, update.message.id, full_body)
    else:
        if await is_commanddelete_on(chat_id):
            try:
                await update.delete()
            except Exception:
                pass
        await send_rich(chat_id, full_body)


@app.on_message(command("HELP_COMMAND") & filters.group & ~BANNED_USERS)
@LanguageStart
async def help_com_group(client, message: Message, _):
    await send_rich(
        message.chat.id,
        rich_heading(f"{pe('HELP_SUPPORT')} ʜᴇʟᴘ ᴍᴇɴᴜ", level=4)
        + f"<p>{rich_esc(_['help_2'])}</p>"
        + private_help_panel(_),
    )


@app.on_callback_query(filters.regex(r"help_(.*?)"))
async def help_button(client, query):
    mod_match = re.match(r"help_module\((.+?),(.+?),(\d+)\)", query.data)
    prev_match = re.match(r"help_prev\((.+?),(\d+)\)", query.data)
    next_match = re.match(r"help_next\((.+?),(\d+)\)", query.data)
    helper_match = re.match(r"help_helper\((.+?),(.+?),(\d+)\)", query.data)

    try:
        language = await get_lang(query.message.chat.id)
        _ = get_string(language)
        helpers_dict = helpers.get(language, helpers.get("en"))
    except Exception:
        language = "en"
        _ = get_string("en")
        helpers_dict = helpers.get("en", {})

    chat_id = query.message.chat.id
    mid = query.message.id

    if mod_match:
        module = mod_match.group(1)
        prev_page_num = int(mod_match.group(2))
        close = bool(int(mod_match.group(3)))
        mod_obj = HELPABLE.get(module)
        if not mod_obj:
            try:
                await client.answer_callback_query(query.id, "Module not found.", show_alert=True)
            except Exception:
                pass
            return
        icon = pe(_emoji_for_label(mod_obj.__MODULE__, 0))
        key = rich_button_row(
            rich_button("ʙᴀᴄᴋ", callback_data=f"help_prev({prev_page_num},{int(close)})", style="primary", emoji="BACK_ARROW"),
            rich_button("ᴄʟᴏsᴇ", callback_data="close", style="danger", emoji="CLOSE"),
        )
        body = rich_heading(f"{icon} {rich_esc(mod_obj.__MODULE__.translate(_SMALL_CAPS))}", level=4)
        body += f"<blockquote>{mod_obj.__HELP__}</blockquote>"
        body += key
        await edit_rich(chat_id, mid, body)
    elif prev_match:
        curr_page = int(prev_match.group(1))
        close = bool(int(prev_match.group(2)))
        body = await build_help_view(curr_page, chat_id, _, close=close)
        await edit_rich(chat_id, mid, body)
    elif next_match:
        next_page = int(next_match.group(1))
        close = bool(int(next_match.group(2)))
        body = await build_help_view(next_page, chat_id, _, close=close)
        await edit_rich(chat_id, mid, body)
    elif helper_match:
        helper_key = helper_match.group(1).replace("%44", ",")
        page_n = int(helper_match.group(2))
        close = bool(int(helper_match.group(3)))
        raw_text = helpers_dict.get(helper_key, None)
        formatted_text = await format_helper_text(language, helper_key, raw_text)
        icon = pe(_emoji_for_label(helper_key, 0))
        key = rich_button_row(
            rich_button("ʙᴀᴄᴋ", callback_data=f"help_prev({page_n},{int(close)})", style="primary", emoji="BACK_ARROW"),
            rich_button("ᴄʟᴏsᴇ", callback_data="close", style="danger", emoji="CLOSE"),
        )
        body = rich_heading(f"{icon} {rich_esc(helper_key.translate(_SMALL_CAPS))}", level=4)
        body += f"<p>{rich_md(formatted_text)}</p>"
        body += key
        try:
            await edit_rich(chat_id, mid, body)
        except Exception as e:
            logging.exception(e)

    try:
        await client.answer_callback_query(query.id)
    except Exception:
        pass

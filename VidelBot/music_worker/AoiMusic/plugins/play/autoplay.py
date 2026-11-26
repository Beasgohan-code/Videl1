#
# Autoplay & Smart Relative Suggestions plugin (ported from XENOBOT2 into AoiMusic).
#

import asyncio
from typing import Tuple

import aiohttp
from pyrogram import filters
from pyrogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from config import BANNED_USERS
from AoiMusic import LOGGER, Platform, app
from AoiMusic.misc import db
from AoiMusic.utils.autoplay import (
    get_relative_suggestions,
    record_played_track,
    relative_suggestions_cache,
)
from AoiMusic.utils.database import (
    autoplay_off,
    autoplay_on,
    get_cmode,
    is_active_chat,
    is_autoplay,
)
from AoiMusic.utils.decorators.language import language, languageCB
from AoiMusic.utils.premium_emoji import pbtn, pe
from AoiMusic.utils.rich_msg import edit_rich, rich_esc, send_rich
from AoiMusic.utils.rich_panels import refresh_panel
from AoiMusic.utils.stream.stream import stream


def _build_autoplay_view(chat_id: int, enabled: bool) -> Tuple[str, InlineKeyboardMarkup]:
    """Single source of truth for /autoplay command and AutoplayToggle callback."""
    status_txt = (
        f"{pe('CHECK_CIRCLE')} <b>ᴏɴ (ᴇɴᴀʙʟᴇᴅ)</b>"
        if enabled
        else f"{pe('BAN_BLOCK')} <b>ᴏғғ (ᴅɪsᴀʙʟᴇᴅ)</b>"
    )
    desc = (
        "ᴡʜᴇɴ ᴛʜᴇ ᴄᴜʀʀᴇɴᴛ sᴏɴɢ ᴇɴᴅs ᴏʀ ᴛʜᴇ ʟᴀsᴛ sᴏɴɢ ɪs sᴋɪᴘᴘᴇᴅ, "
        "ᴛʜᴇ ɴᴇxᴛ ʀᴇʟᴀᴛᴇᴅ sᴏɴɢ ᴡɪʟʟ ᴘʟᴀʏ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ."
        if enabled
        else "ᴡʜᴇɴ ᴛʜᴇ ǫᴜᴇᴜᴇ ᴇɴᴅs, ᴛʜᴇ ʙᴏᴛ ᴡɪʟʟ sᴛᴏᴘ ᴀɴᴅ ʟᴇᴀᴠᴇ ᴛʜᴇ ᴠᴏɪᴄᴇ ᴄʜᴀᴛ."
    )
    html = (
        "<blockquote>"
        f"{pe('REFRESH')} <b>ᴀᴜᴛᴏᴘʟᴀʏ sᴇᴛᴛɪɴɢs</b><br><br>"
        f"<b>sᴛᴀᴛᴜs :</b> {status_txt}<br>"
        f"<b>ᴜsᴀɢᴇ :</b> <code>/autoplay on</code> ᴏʀ <code>/autoplay off</code><br><br>"
        f"{desc}"
        "</blockquote>"
    )
    kb = InlineKeyboardMarkup(
        [
            [
                pbtn(
                    text="ᴛᴜʀɴ ᴏғғ ᴀᴜᴛᴏᴘʟᴀʏ" if enabled else "ᴛᴜʀɴ ᴏɴ ᴀᴜᴛᴏᴘʟᴀʏ",
                    emoji="EQ_OFF" if enabled else "REFRESH",
                    style="DANGER" if enabled else "SUCCESS",
                    callback_data=f"AutoplayToggle|{chat_id}",
                ),
                pbtn(
                    text="ᴄʟᴏsᴇ",
                    emoji="CLOSE",
                    style="DANGER",
                    callback_data="close",
                ),
            ]
        ]
    )
    return html, kb


async def _resolve_track_details(vidid: str) -> dict:
    cached = relative_suggestions_cache.get(vidid)
    if cached and cached.get("title"):
        return dict(cached)
    try:
        details, _ = await Platform.youtube.track(vidid, True)
        if details and details.get("title"):
            relative_suggestions_cache[vidid] = details
            return details
    except Exception:
        pass

    title = "YouTube Track"
    thumb = f"https://i.ytimg.com/vi/{vidid}/hqdefault.jpg"
    oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vidid}&format=json"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async with session.get(oembed_url) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    title = data.get("title") or title
                    thumb = data.get("thumbnail_url") or thumb
    except Exception:
        pass

    fallback = {
        "title": title,
        "duration_min": "04:00",
        "duration_sec": 240,
        "thumb": thumb,
        "vidid": vidid,
        "link": f"https://www.youtube.com/watch?v={vidid}",
    }
    relative_suggestions_cache[vidid] = fallback
    return fallback


@app.on_message(
    filters.command(["autoplay", "cautoplay"]) & filters.group & ~BANNED_USERS
)
@language
async def autoplay_command(client, message: Message, _):
    if message.command[0][0].lower() == "c":
        chat_id = await get_cmode(message.chat.id)
        if chat_id is None:
            return await send_rich(message.chat.id, f"<p>{rich_esc(_['setting_12'])}</p>")
    else:
        chat_id = message.chat.id

    if len(message.command) > 1:
        arg = message.command[1].strip().lower()
        if arg in ("on", "enable", "enabled", "yes", "true", "1"):
            await autoplay_on(chat_id)
        elif arg in ("off", "disable", "disabled", "no", "false", "0"):
            await autoplay_off(chat_id)

    enabled = await is_autoplay(chat_id)
    html, kb = _build_autoplay_view(chat_id, enabled)
    await send_rich(message.chat.id, html, reply_markup=kb)


@app.on_callback_query(filters.regex(r"^AutoplayToggle\|") & ~BANNED_USERS)
@languageCB
async def autoplay_toggle_cb(client, CallbackQuery: CallbackQuery, _):
    try:
        chat_id = int(CallbackQuery.data.split("|", 1)[1])
    except Exception:
        chat_id = CallbackQuery.message.chat.id

    new_state = not await is_autoplay(chat_id)
    if new_state:
        await autoplay_on(chat_id)
        await CallbackQuery.answer(
            "🔄 Autoplay ON! Next related song will play automatically when the queue ends.",
            show_alert=True,
        )
    else:
        await autoplay_off(chat_id)
        await CallbackQuery.answer(
            "⏹ Autoplay OFF! Playback will stop when the queue ends.",
            show_alert=True,
        )

    msg = CallbackQuery.message
    if not msg:
        return

    # If triggered from the live Now Playing panel, re-render the panel so its button updates cleanly.
    playing = db.get(chat_id)
    if playing and playing[0].get("rich_mid") == msg.id:
        await refresh_panel(chat_id, msg.id)
        return

    # Otherwise update the /autoplay settings card using the shared view builder.
    html, kb = _build_autoplay_view(chat_id, new_state)
    await edit_rich(msg.chat.id, msg.id, html, reply_markup=kb)


@app.on_callback_query(filters.regex(r"^GetSuggestions\|") & ~BANNED_USERS)
@languageCB
async def get_suggestions_cb(client, CallbackQuery: CallbackQuery, _):
    try:
        chat_id = int(CallbackQuery.data.split("|", 1)[1])
    except Exception:
        chat_id = CallbackQuery.message.chat.id if CallbackQuery.message else 0

    playing = db.get(chat_id)
    if not playing or not await is_active_chat(chat_id):
        return await CallbackQuery.answer(_["general_6"], show_alert=True)

    try:
        await CallbackQuery.answer("✨ Finding related songs...")
    except Exception:
        pass

    current = playing[0]
    vidid = str(current.get("vidid") or "")
    title = str(current.get("title") or "")
    suggestions = await get_relative_suggestions(chat_id, title, vidid, count=5)
    target_chat = CallbackQuery.message.chat.id if CallbackQuery.message else chat_id

    if not suggestions:
        await send_rich(
            target_chat,
            f"<blockquote>{pe('WARNING')} <b>ɴᴏ ʀᴇʟᴀᴛᴇᴅ sᴜɢɢᴇsᴛɪᴏɴs ғᴏᴜɴᴅ ʀɪɢʜᴛ ɴᴏᴡ.</b></blockquote>",
            reply_markup=InlineKeyboardMarkup(
                [[pbtn(text="ᴄʟᴏsᴇ", emoji="CLOSE", style="DANGER", callback_data="close")]]
            ),
        )
        return

    lines = [
        f"{pe('SPARKLES_AI')} <b>ʀᴇʟᴀᴛᴇᴅ sᴏɴɢ sᴜɢɢᴇsᴛɪᴏɴs</b>",
        "",
    ]
    num_btns = []
    for idx, item in enumerate(suggestions[:5], start=1):
        s_title = rich_esc(str(item.get("title") or "Unknown")[:45])
        s_dur = rich_esc(str(item.get("duration_min") or "--:--"))
        lines.append(f"<b>{idx}.</b> {s_title} <code>[{s_dur}]</code>")
        if item.get("vidid"):
            num_btns.append(
                pbtn(
                    text=str(idx),
                    emoji="MUSIC_SINGLE",
                    style="PRIMARY",
                    callback_data=f"play_{item['vidid']}",
                )
            )
    lines.append("")
    lines.append(f"{pe('INFO')} ᴛᴀᴘ ᴀ ɴᴜᴍʙᴇʀ ʙᴇʟᴏᴡ ᴛᴏ ǫᴜᴇᴜᴇ &amp; ᴘʟᴀʏ.")

    rows = []
    if len(num_btns) > 3:
        rows.append(num_btns[:3])
        rows.append(num_btns[3:])
    elif num_btns:
        rows.append(num_btns)
    rows.append([pbtn(text="ᴄʟᴏsᴇ", emoji="CLOSE", style="DANGER", callback_data="close")])

    await send_rich(
        target_chat,
        "<blockquote>" + "<br>".join(lines) + "</blockquote>",
        reply_markup=InlineKeyboardMarkup(rows),
    )


@app.on_callback_query(filters.regex(r"^play_") & ~BANNED_USERS)
@languageCB
async def play_suggested_song_cb(client, CallbackQuery: CallbackQuery, _):
    vidid = (CallbackQuery.data or "")[len("play_"):].strip()
    if not vidid:
        return await CallbackQuery.answer("Invalid song ID.", show_alert=True)

    original_chat_id = CallbackQuery.message.chat.id if CallbackQuery.message else 0
    chat_id = original_chat_id
    try:
        cmode = await get_cmode(original_chat_id)
        if cmode:
            chat_id = cmode
    except Exception:
        pass

    try:
        await CallbackQuery.answer("🎵 Adding suggested song...")
    except Exception:
        pass

    details = await _resolve_track_details(vidid)
    record_played_track(chat_id, vidid, details.get("title"))

    user_id = CallbackQuery.from_user.id
    user_name = CallbackQuery.from_user.first_name or "User"

    from AoiMusic.utils.rich_msg import status_card

    mystic = await status_card(original_chat_id, _["play_1"])
    try:
        await stream(
            _,
            mystic,
            user_id,
            details,
            chat_id,
            user_name,
            original_chat_id,
            video=None,
            streamtype="youtube",
            forceplay=None,
        )
    except Exception as exc:
        LOGGER(__name__).warning("play_suggested_song_cb failed for %s: %s", vidid, exc)
        try:
            await mystic.edit_text(f"⚠️ Could not play suggested track: {exc}")
        except Exception:
            pass
    else:
        try:
            await mystic.delete()
        except Exception:
            pass

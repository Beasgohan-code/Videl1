#
# Song download module — direct Audio/Video download via Platform.youtube.download()
# (remote media API primary + yt-dlp automatic fallback) with rich-message panels.
#

import logging
import os
import re

from pyrogram import enums, filters
from pyrogram.types import Message

from config import (
    BANNED_USERS,
    SONG_DOWNLOAD_DURATION,
    SONG_DOWNLOAD_DURATION_LIMIT,
)
from strings import command
from AoiMusic import Platform, app
from AoiMusic.utils.decorators.language import language, languageCB
from AoiMusic.utils.inline.song import song_markup
from AoiMusic.utils.rich_msg import (
    delete_msg,
    edit_rich,
    pemoji,
    rich_button_row,
    rich_esc,
    rich_heading,
    rich_img,
    send_audio,
    send_rich,
    send_video,
)

LOGGER = logging.getLogger(__name__)


def song_panel_html(_, thumbnail, title, vidid):
    body = rich_img(thumbnail)
    body += rich_heading(f'{pemoji("🎧")} Song Downloader', level=4)
    body += f"<p>{rich_esc(_['song_4'].format(title))}</p>"
    body += song_markup(_, vidid)
    return body


@app.on_message(command("SONG_COMMAND") & filters.group & ~BANNED_USERS)
@language
async def song_commad_group(client, message: Message, _):
    body = (
        f"<p>{rich_esc(_['song_1'])}</p>"
        + rich_button_row(
            f'<tg-button type="url" style="primary" emoji="LINK" '
            f'url="https://t.me/{app.username}?start=song">'
            f'ᴏᴘᴇɴ Dᴍ</tg-button>'
        )
    )
    await send_rich(message.chat.id, body)


@app.on_message(command("SONG_COMMAND") & filters.private & ~BANNED_USERS)
@language
async def song_commad_private(client, message: Message, _):
    try:
        await message.delete()
    except Exception:
        pass

    url = await Platform.youtube.url(message)

    if url:
        if not await Platform.youtube.exists(url):
            return await send_rich(
                message.chat.id, f"<p>{rich_esc(_['song_5'])}</p>"
            )

        (
            title,
            duration_min,
            duration_sec,
            thumbnail,
            vidid,
        ) = await Platform.youtube.details(url)

        if str(duration_min) == "None":
            return await send_rich(
                message.chat.id, f"<p>{rich_esc(_['song_3'])}</p>"
            )

        if int(duration_sec) > SONG_DOWNLOAD_DURATION_LIMIT:
            return await send_rich(
                message.chat.id,
                f"<p>{rich_esc(_['play_4'].format(SONG_DOWNLOAD_DURATION, duration_min))}</p>",
            )

        return await send_rich(
            message.chat.id, song_panel_html(_, thumbnail, title, vidid)
        )

    if len(message.command) < 2:
        return await send_rich(
            message.chat.id, f"<p>{rich_esc(_['song_2'])}</p>"
        )

    query = " ".join(message.command[1:]).strip()

    try:
        (
            title,
            duration_min,
            duration_sec,
            thumbnail,
            vidid,
        ) = await Platform.youtube.details(query)
    except Exception:
        return await send_rich(
            message.chat.id, f"<p>{rich_esc(_['play_3'])}</p>"
        )

    if str(duration_min) == "None":
        return await send_rich(
            message.chat.id, f"<p>{rich_esc(_['song_3'])}</p>"
        )

    if int(duration_sec) > SONG_DOWNLOAD_DURATION_LIMIT:
        return await send_rich(
            message.chat.id,
            f"<p>{rich_esc(_['play_6'].format(SONG_DOWNLOAD_DURATION, duration_min))}</p>",
        )

    return await send_rich(
        message.chat.id, song_panel_html(_, thumbnail, title, vidid)
    )


async def _download_and_send_song(CallbackQuery, _, stype: str, vidid: str) -> None:
    """Download audio or video directly via Platform.youtube.download() (API -> yt-dlp fallback) and upload."""
    chat_id = CallbackQuery.message.chat.id
    mid = CallbackQuery.message.id
    is_video = stype == "video"
    yturl = f"https://www.youtube.com/watch?v={vidid}"

    await edit_rich(chat_id, mid, f"<p>{pemoji('📥')} {rich_esc(_['song_8'])}</p>")

    title = vidid
    duration_sec = 0
    uploader = "Videl Music"
    try:
        det_title, _dur_min, det_sec, _thumb, _vid = await Platform.youtube.details(yturl)
        if det_title:
            title = det_title
        if det_sec:
            duration_sec = int(det_sec)
    except Exception as exc:
        LOGGER.debug("song details lookup failed for %s: %s", vidid, exc)

    clean_title = re.sub(r"\s+", " ", re.sub(r"[^\w\s\-()&.+]", " ", str(title))).strip()[:80] or vidid

    file_path = None
    try:
        await edit_rich(chat_id, mid, f"<p>{pemoji('📥')} {rich_esc(_['song_11'])}</p>")
        try:
            await app.send_chat_action(
                chat_id=chat_id,
                action=enums.ChatAction.UPLOAD_VIDEO if is_video else enums.ChatAction.UPLOAD_AUDIO,
            )
        except Exception:
            pass

        result = await Platform.youtube.download(
            yturl,
            None,
            video=is_video,
        )
        if isinstance(result, tuple):
            file_path = result[0]
        else:
            file_path = result

        if not file_path or not os.path.exists(file_path):
            raise RuntimeError("Downloaded file not found on disk.")
    except Exception as e:
        LOGGER.warning("Direct /song download failed (%s, %s): %s", stype, vidid, e)
        await edit_rich(chat_id, mid, f"<p>{pemoji('⚠')} {rich_esc(_['song_9'].format(e))}</p>")
        return

    try:
        if is_video:
            await send_video(
                chat_id,
                file_path,
                caption=clean_title,
                duration=duration_sec,
                streaming=True,
            )
        else:
            await send_audio(
                chat_id,
                file_path,
                caption=clean_title,
                title=clean_title,
                performer=uploader,
                duration=duration_sec,
            )
        await delete_msg(chat_id, mid)
    except Exception as exc:
        LOGGER.warning("Failed uploading /song media (%s, %s): %s", stype, vidid, exc)
        await edit_rich(chat_id, mid, f"<p>{pemoji('⚠')} {rich_esc(_['song_10'])}</p>")
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass


@app.on_callback_query(filters.regex(pattern=r"^song_helper\s+") & ~BANNED_USERS)
@languageCB
async def song_helper_cb(client, CallbackQuery, _):
    try:
        await CallbackQuery.answer("ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ...")
    except Exception:
        pass

    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    stype, vidid = callback_request.split("|", 1)
    await _download_and_send_song(CallbackQuery, _, stype, vidid)


@app.on_callback_query(filters.regex(pattern=r"^song_download\s+") & ~BANNED_USERS)
@languageCB
async def song_download_cb(client, CallbackQuery, _):
    try:
        await CallbackQuery.answer("ᴅᴏᴡɴʟᴏᴀᴅɪɴɢ...")
    except Exception:
        pass

    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    parts = callback_request.split("|")
    stype = parts[0]
    vidid = parts[-1]
    await _download_and_send_song(CallbackQuery, _, stype, vidid)


@app.on_callback_query(filters.regex(pattern=r"^song_back\s+") & ~BANNED_USERS)
@languageCB
async def songs_back_helper(client, CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    _stype, vidid = callback_request.split("|", 1)
    try:
        await CallbackQuery.answer()
    except Exception:
        pass
    chat_id = CallbackQuery.message.chat.id
    mid = CallbackQuery.message.id
    title = ""
    thumbnail = "https://i.ibb.co/7zYzRJw/a8800c683dee8d9919d24dbdde5eed6a-1.jpg"
    try:
        t, _, _, th, _ = await Platform.youtube.details(f"https://www.youtube.com/watch?v={vidid}")
        title = t or ""
        thumbnail = th or thumbnail
    except Exception:
        pass
    await edit_rich(chat_id, mid, song_panel_html(_, thumbnail, title, vidid))

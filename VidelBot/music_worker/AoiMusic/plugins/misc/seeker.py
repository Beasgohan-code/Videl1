
#
import asyncio
import time
from datetime import datetime, timedelta

from pyrogram.types import InlineKeyboardMarkup

from AoiMusic import LOGGER
from strings import get_string
from AoiMusic.core.call import AoiCall, _chat_has_live_call
from AoiMusic.misc import db
from AoiMusic.utils.database import (
    get_active_chats,
    get_assistant,
    get_lang,
    is_music_playing,
    set_loop,
)
from AoiMusic.utils.formatters import seconds_to_min
from AoiMusic.utils.inline import stream_markup_timer, telegram_markup_timer

from ..admins.callback import wrong
from .autoleave import autoend

checker = {}
muted = {}
_call_presence_failures = {}


async def timer():
    while not await asyncio.sleep(1):
        active_chats = await get_active_chats()
        for chat_id in active_chats:
            if not await is_music_playing(chat_id):
                continue
            playing = db.get(chat_id)
            if not playing:
                continue
            file_path = playing[0]["file"]
            if "index_" in file_path or "live_" in file_path:
                continue
            duration = int(playing[0]["seconds"])
            if duration == 0:
                continue
            db[chat_id][0]["played"] += 1


async def leave_if_muted():
    while True:
        await asyncio.sleep(10)
        for chat_id, details in list(muted.items()):
            if time.time() - details["timestamp"] >= 60:
                _ = details["_"]
                try:
                    userbot = await get_assistant(chat_id)
                    members = []
                    try:
                        async for member in userbot.get_call_members(
                            chat_id
                        ):
                            if member is None:
                                continue
                            members.append(member)
                    except ValueError:
                        try:
                            await AoiCall.stop_stream(chat_id)
                        except Exception:
                            pass
                        continue

                    m = next(
                        (m for m in members if m.chat.id == userbot.id), None
                    )
                    if m is None:
                        continue
                    is_muted = bool(m.is_muted and not m.can_self_unmute)

                    if is_muted:
                        await AoiCall.stop_stream(chat_id)
                        await set_loop(chat_id, 0)

                    del muted[chat_id]
                except Exception:
                    del muted[chat_id]


async def markup_timer():
    while True:
        # Poll every 15s (not 2s!) so get_call_members never floods the assistant userbot.
        await asyncio.sleep(15)
        active_chats = list(await get_active_chats())
        for chat_id in active_chats:
            if AoiCall.is_join_pending(chat_id):
                continue

            # Check the VC on every active chat, paused or not. An assistant
            # that disappeared from the call used to be ignored (`m is None`
            # just continued), so the queue kept growing with nobody in VC.
            userbot = None
            members = []
            m = None
            try:
                userbot = await get_assistant(chat_id)
                try:
                    async for member in userbot.get_call_members(chat_id):
                        if member is not None:
                            members.append(member)
                except ValueError:
                    members = []
                if userbot is not None:
                    m = next((mm for mm in members if mm.chat.id == userbot.id), None)
            except Exception:
                pass

            if m is None and await _chat_has_live_call(chat_id, AoiCall.calls) is False:
                misses = _call_presence_failures.get(chat_id, 0) + 1
                _call_presence_failures[chat_id] = misses
                if misses >= 2:
                    LOGGER(__name__).info(
                        "[Seek] assistant absent from chat %s; stopping stream", chat_id
                    )
                    await AoiCall.stop_stream(chat_id)
                    await set_loop(chat_id, 0)
                    _call_presence_failures.pop(chat_id, None)
                    continue
            else:
                _call_presence_failures.pop(chat_id, None)

            if m is not None and len(members) <= 1 and chat_id not in autoend:
                autoend[chat_id] = datetime.now() + timedelta(seconds=30)

            if not await is_music_playing(chat_id):
                continue

            playing = db.get(chat_id)
            if not playing:
                continue

            duration_seconds = int(playing[0]["seconds"])

            try:
                language = await get_lang(chat_id)
                _ = get_string(language)
            except Exception:
                _ = get_string("en")

            is_muted = False
            try:
                is_muted = bool(m.is_muted and not m.can_self_unmute)
                if is_muted and chat_id not in muted:
                    muted[chat_id] = {
                        "timestamp": time.time(),
                        "_": _,
                    }
            except Exception:
                pass

            if duration_seconds == 0:
                continue

            try:
                mystic = playing[0]["mystic"]
                markup = playing[0]["markup"]
            except Exception:
                continue

            try:
                check = wrong[chat_id][mystic.id]
                if check is False:
                    continue
            except Exception:
                pass

            if markup == "rich":
                continue

            try:
                buttons = (
                    stream_markup_timer(
                        _,
                        playing[0]["vidid"],
                        chat_id,
                        seconds_to_min(playing[0]["played"]),
                        playing[0]["dur"],
                    )
                    if markup == "stream"
                    else telegram_markup_timer(
                        _,
                        chat_id,
                        seconds_to_min(playing[0]["played"]),
                        playing[0]["dur"],
                    )
                )

                await mystic.edit_reply_markup(
                    reply_markup=InlineKeyboardMarkup(buttons)
                )

            except Exception:
                continue


asyncio.create_task(timer(), name="timer")
asyncio.create_task(markup_timer(), name="markup_timer")
asyncio.create_task(leave_if_muted(), name="leave_if_muted")

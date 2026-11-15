import math
import time

from pyrogram import StopTransmission
from pyrogram.errors import FloodWait, MessageDeleteForbidden, MessageIdInvalid, MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .. import PROGRESS
from . import jobs


_last_edit: dict = {}          # (chat, message id) -> next allowed edit timestamp


async def progress_for_pyrogram(current, total, ud_type, message, start):
    msg_id = getattr(message, "id", None)
    if jobs.is_cancelled(msg_id):
        raise StopTransmission                  # ❌ Cancel pressed – pyrogram aborts the transfer

    now = time.time()
    diff = max(now - start, 0.001)
    safe_total = max(int(total or 0), 1)
    percentage = min(100.0, max(0.0, current * 100.0 / safe_total))
    speed = current / diff
    time_to_completion = round((safe_total - current) / speed) if speed > 0 else 0

    if msg_id is not None:
        jobs.update_progress(
            msg_id,
            current=int(current or 0),
            total=int(total or 0),
            pct=percentage,
            speed=speed,
            eta=time_to_completion,
            stage=str(ud_type or "Transfer")[:40],
            engine="WzGram",
        )

    key = (getattr(getattr(message, "chat", None), "id", None), msg_id)
    if current != total and now < _last_edit.get(key, 0):
        return
    _last_edit[key] = now + 5.0
    if current == total or len(_last_edit) > 500:
        _last_edit.pop(key, None)
        if len(_last_edit) > 500:
            _last_edit.clear()

    try:
        elapsed_sec = round(diff)
        estimated_total_time = TimeFormatter(seconds=elapsed_sec + time_to_completion)
        filled = max(0, min(10, math.floor(percentage / 10)))
        progress = ("█" * filled) + ("░" * (10 - filled))
        tmp = progress + PROGRESS.format(
            humanbytes(current) or "0 B",
            humanbytes(total) or "0 B",
            (humanbytes(speed) or "0 B") + "/s",
            estimated_total_time if estimated_total_time != "..." else "Calculating",
        )
        job = jobs.get(msg_id)
        markup = (
            InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"enc_cancel:{message.id}")]])
            if job
            else None
        )
        await message.edit(text=f"{ud_type}\n{tmp}", reply_markup=markup)
    except FloodWait as fw:
        _last_edit[key] = time.time() + int(getattr(fw, "value", 5) or 5) + 1
    except (MessageIdInvalid, MessageDeleteForbidden):
        # Status message was deleted by user/admin — suppress repeated edit attempts for 60s
        _last_edit[key] = time.time() + 60.0
    except MessageNotModified:
        pass
    except Exception:
        pass


async def progress_for_url(downloader, msg):
    msg_id = getattr(msg, "id", None)
    total_length = int(downloader.filesize or 0)
    downloaded = int(downloader.get_dl_size() or 0)
    speed = downloader.get_speed(human=True)
    estimated_total_time = downloader.get_eta(human=True)
    percentage = min(100.0, max(0.0, float(downloader.get_progress() or 0.0) * 100.0))
    if msg_id is not None:
        jobs.update_progress(
            msg_id,
            current=downloaded,
            total=total_length,
            pct=percentage,
            speed_label=str(speed or ""),
            stage="⬇️ Direct Download",
            engine="HTTP",
        )
    filled = max(0, min(10, math.floor(percentage / 10)))
    progress = ("█" * filled) + ("░" * (10 - filled))
    progress_str = "Downloading\n" + progress + PROGRESS.format(
        humanbytes(downloaded) or "0 B",
        humanbytes(total_length) or "0 B",
        speed,
        estimated_total_time,
    )
    try:
        await msg.edit_text(progress_str)
    except Exception:
        pass


def humanbytes(size):
    """ humanize size """
    if not size:
        return ""
    power = 1024
    t_n = 0
    power_dict = {0: ' ', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power and t_n < 4:
        size /= power
        t_n += 1
    return "{:.2f} {}B".format(size, power_dict[t_n])


def TimeFormatter(seconds: float) -> str:
    """ humanize time """
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = ((str(days) + "d, ") if days else "") + \
        ((str(hours) + "h, ") if hours else "") + \
        ((str(minutes) + "m, ") if minutes else "") + \
        ((str(seconds) + "s, ") if seconds else "")
    return tmp[:-2] or "0s"

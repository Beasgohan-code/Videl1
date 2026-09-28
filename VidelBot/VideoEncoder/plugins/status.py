from pyrogram import Client
from asyncio import gather
from psutil import cpu_percent, virtual_memory, disk_usage, net_io_counters
from pyrogram import filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from time import time
import os

from .. import app, botStartTime, download_dir, data, sudo_users, owner
from ..utils.display_progress import humanbytes, TimeFormatter
from ..utils.helper import check_chat

# Helper function for readable time
def get_readable_time(seconds):
    return TimeFormatter(seconds)

def get_readable_file_size(size):
    return humanbytes(size)

def get_task_info(task_msg):
    user = task_msg.from_user.first_name if task_msg.from_user else "Unknown User"
    user_id = task_msg.from_user.id if task_msg.from_user else "No ID"

    task_type = "Unknown Task"
    filename = "Unknown File"

    text_content = task_msg.text or task_msg.caption
    if text_content:
        parts = text_content.split(None, 1)
        cmd = parts[0].lower()
        if '/dl' in cmd:
            task_type = "Telegram Download"
        elif '/af' in cmd:
            task_type = "Audio Processing"
        elif '/ddl' in cmd:
            task_type = "Direct Download"
        elif '/batch' in cmd:
            task_type = "Batch Process"

    if task_msg.document:
        filename = task_msg.document.file_name or "Document"
        if task_type == "Unknown Task": task_type = "Telegram Download"
    elif task_msg.video:
        filename = task_msg.video.file_name or "Video"
        if task_type == "Unknown Task": task_type = "Telegram Download"
    elif text_content and ('/ddl' in text_content or '/batch' in text_content):
        # Attempt to extract filename or url
        filename = "URL Task"

    return f"{task_type}: {filename}\n   └ User: <a href='tg://user?id={user_id}'>{user}</a>"

def status_doc():
    """System status + active encodes as a rich screen."""
    from core.rich import Doc, Raw
    count = len(data)
    net = net_io_counters()
    doc = Doc("📈", "Server status")
    doc.table([
        ("⚙️ CPU", f"{cpu_percent()} %"),
        ("🧠 RAM", f"{virtual_memory().percent} %"),
        ("💽 Free disk", get_readable_file_size(disk_usage(download_dir).free)),
        ("📤 Up / 📥 Down", f"{humanbytes(net.bytes_sent)} / {humanbytes(net.bytes_recv)}"),
        ("⏱ Uptime", get_readable_time(time() - botStartTime)),
    ], header=("System", "Value"))
    doc.h("🎬", f"Active tasks ({count})")
    if count:
        rows = []
        for i, task_msg in enumerate(data, 1):
            kind, name, user = task_parts(task_msg)
            rows.append((i, kind, name, Raw(user)))
        doc.table(rows, header=("#", "Task", "File", "User"), compact=True)
    else:
        doc.text("<i>🥱 No active encodes.</i>")
    return doc


def task_parts(task_msg):
    """(task type, file name, user link html) for one queued message."""
    import html as _h
    info = get_task_info(task_msg)
    head, _, user_line = info.partition("\n")
    kind, _, name = head.partition(": ")
    user = user_line.replace("└ User:", "").strip()
    return kind, name, user or _h.escape("Unknown User")


def _status_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Refresh", callback_data="status ref")]])


@Client.on_message(filters.command("status"))
async def mirror_status(client, message: Message):
    c = await check_chat(message, chat='Both')
    if not c:
        return
    if message.chat.id < 0:     # groups: a normal public message, so everyone can use 🔄 Refresh
        return await message.reply_text(status_doc().classic(), reply_markup=_status_kb())
    from core import rich
    await rich.reply(message, status_doc(), reply_markup=_status_kb())


@Client.on_callback_query(filters.regex('^status'))
async def status_pages(client, query: CallbackQuery):
    data_split = query.data.split()
    cmd = data_split[1] if len(data_split) > 1 else ""
    if cmd == 'ref':
        from core import rich
        try:
            await rich.edit(query.message, status_doc(), reply_markup=_status_kb())
            await query.answer("Refreshed!")
        except Exception as e:
            await query.answer(f"Error: {e}"[:190])
    else:
        await query.answer("Unknown command")

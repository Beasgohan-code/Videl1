

import os
from urllib.parse import unquote_plus

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .. import data
from ..utils.database.add_user import AddUserToDatabase
from ..utils.helper import check_chat

queue_callback_filter = filters.create(
    lambda _, __, query: query.data.startswith('queue+'))


async def get_title(i):
    try:
        if data[i].video:
            return data[i].video.file_name
        elif data[i].document:
            return data[i].document.file_name
        else:
            url = data[i].command[1]
            return str(unquote_plus(os.path.basename(url)))
    except:
        return None


def map(pos):
    if pos == 0:
        if len(data) > 1:
            button = [[InlineKeyboardButton(
                text='Next', callback_data="queue+1")]]
        else:
            button = [[InlineKeyboardButton(
                text='No Other Task', callback_data="queue+-1")]]
    else:
        try:
            if data[pos+1]:
                button = [
                    [
                        InlineKeyboardButton(
                            text='Previous', callback_data=f"queue+{pos-1}"),
                        InlineKeyboardButton(
                            text='Next', callback_data=f"queue+{pos+1}")
                    ],
                ]
        except Exception as e:
            button = [
                [
                    InlineKeyboardButton(
                        text='Previous', callback_data=f"queue+{pos-1}")
                ],
            ]
    return button


async def queue_answer(app, callback_query):
    chatid = callback_query.from_user.id
    messageid = callback_query.message.id
    pos = int(callback_query.data.split('+')[1])
    if pos == -1:
        await callback_query.answer("no task", show_alert=True)
        return
    taskpos = pos+1
    size = len(data)
    tasktitle = await get_title(pos)
    await callback_query.edit_message_text(f"<b>{taskpos} of {size}</b>:\n\n{tasktitle}", reply_markup=InlineKeyboardMarkup(map(pos)))


def queue_doc():
    """The whole queue on one screen (the old /queue edited one message per task in a loop)."""
    from core.rich import Doc, Raw
    from .status import task_parts
    from ..utils import jobs
    doc = Doc("📋", "Encoder queue", f"{len(data)} task{'s' if len(data) != 1 else ''}")
    if not data:
        return doc.text("<i>🥱 No active encodes – send /dl to a video to start one.</i>")
    running = jobs.active()
    stage = running[0].stage if running else "starting"
    rows = []
    for i, task_msg in enumerate(data[:25]):
        kind, name, user = task_parts(task_msg)
        state = f"▶️ {stage}" if i == 0 else f"⏳ #{i + 1}"
        rows.append((state, kind, name[:40], Raw(user)))
    doc.table(rows, header=("State", "Task", "File", "User"), compact=True)
    if len(data) > 25:
        doc.text(f"<i>…and {len(data) - 25} more.</i>")
    return doc.footer("Admins can purge waiting tasks with /clear.")


@Client.on_message(filters.command(['queue']))
async def queue_message(app, message):
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)
    from core import rich
    await rich.reply(message, queue_doc())


@Client.on_message(filters.command('clear'))
async def clear(app, message):
    c = await check_chat(message, chat='Sudo')
    if not c:
        return
    await AddUserToDatabase(app, message)
    if len(data) >= 1:
        current = data[0]
        removed = len(data) - 1
        data.clear()
        data.append(current)
        await message.reply(f'🧹 Purged {removed} waiting task{"s" if removed != 1 else ""} – the running one continues.')
    else:
        await message.reply("🥱 No Active Encodes.")

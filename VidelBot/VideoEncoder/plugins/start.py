

import os
import shutil
from time import time

from psutil import (boot_time, cpu_count, cpu_percent, disk_usage,
                    net_io_counters, swap_memory, virtual_memory)
from pyrogram import Client, filters
from pyrogram.types import Message

from .. import botStartTime, download_dir, encode_dir
from ..utils.database.access_db import db
from ..utils.database.add_user import AddUserToDatabase
from ..utils.display_progress import TimeFormatter, humanbytes
from ..utils.helper import check_chat, delete_downloads, start_but

SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']


def uptime():
    """ returns uptime """
    return TimeFormatter(time() - botStartTime)


async def start_message(app, message):
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)
    text = f"Hi {message.from_user.mention()}! Send /dl as a reply to a video to encode it."
    await message.reply(text=text, reply_markup=start_but)


async def help_message(app, message):
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)
    msg = """<b>📕 Commands List</b>:

- Autodetect Telegram File.
- /ddl - encode through DDL
- /batch - encode in batch
- /queue - check queue
- /settings - settings
- /vset - view settings
- /reset - reset settings
- /stats - cpu stats

For Sudo:
- /exec - Execute Python
- /sh - Execute Shell
- /vupload - video upload
- /dupload - doc upload
- /gupload - drive upload
- /update - git pull
- /restart - restart bot
- /clean - clean junk
- /clear - clean queue
- /logs - view logs

For Owner:
- /addchat and /addsudo
- /rmsudo and /rmchat
"""
    await message.reply(text=msg, disable_web_page_preview=True, reply_markup=start_but)


@Client.on_message(filters.command(["stats", "botstats"]))
async def show_status_count(_, event: Message):
    # Public server stats (admins get the full Videl /stats from core/admin.py first).
    if event.from_user:
        await AddUserToDatabase(_, event)
    from core import rich
    await rich.reply(event, await stats_doc(_))


async def stats_doc(_):
    """Public server statistics as a rich screen (tables for uptime, CPU, RAM, disk, network)."""
    from core.rich import Doc
    total, used, free, disk = disk_usage('/')
    net = net_io_counters()
    swap = swap_memory()
    memory = virtual_memory()
    total_users = await db.total_users_count()
    doc = Doc("📊", "Bot statistics", "live server numbers")
    doc.table([
        ("🤖 Bot uptime", TimeFormatter(time() - botStartTime)),
        ("🖥 OS uptime", TimeFormatter(time() - boot_time())),
        ("👥 Users", total_users),
    ], header=("Overview", "Value"))
    doc.h("⚙️", "CPU & memory")
    doc.table([
        ("CPU", f"{cpu_percent(interval=0.5)} %", f"{cpu_count(logical=False)} physical · {cpu_count(logical=True)} total cores"),
        ("RAM", f"{memory.percent} %", f"{humanbytes(memory.used)} used · {humanbytes(memory.available)} free of {humanbytes(memory.total)}"),
        ("Swap", f"{swap.percent} %", humanbytes(swap.total) if swap.total else "—"),
    ], header=("Resource", "Load", "Details"), align=("left", "right", "left"))
    doc.h("💾", "Disk & network")
    doc.table([
        ("💽 Disk", f"{disk} %", f"{humanbytes(used)} used · {humanbytes(free)} free of {humanbytes(total)}"),
        ("📤 Uploaded", humanbytes(net.bytes_sent), "since boot"),
        ("📥 Downloaded", humanbytes(net.bytes_recv), "since boot"),
    ], header=("Item", "Amount", "Details"), align=("left", "right", "left"))
    return doc


async def show_status(_):
    return (await stats_doc(_)).classic()


async def showw_status(_):
    currentTime = TimeFormatter(time() - botStartTime)
    total, used, free, disk = disk_usage('/')
    total = humanbytes(total)
    used = humanbytes(used)
    free = humanbytes(free)
    cpuUsage = cpu_percent(interval=0.5)
    total_users = await db.total_users_count()

    text = f"""Uptime of Bot: {currentTime}

Disk:
- Total: {total}
- Used: {used}
- Free: {free}
CPU: {cpuUsage}%

Users: {total_users}"""
    return text


@Client.on_message(filters.command('clean'))
async def delete_files(_, message):
    c = await check_chat(message, chat='Sudo')
    if not c:
        return
    delete_downloads()
    await message.reply_text('Deleted all junk files!')

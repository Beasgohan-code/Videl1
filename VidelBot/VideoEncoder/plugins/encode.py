import asyncio
import re

from pyrogram import Client, filters

from .. import data, video_mimetype
from ..utils.database.add_user import AddUserToDatabase
from ..utils.helper import check_chat
from ..utils.tasks import handle_tasks


@Client.on_message(filters.command('dl'))
async def encode_video(app, message):
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)

    # Check if replying to a file or file is attached
    if not (message.reply_to_message and (message.reply_to_message.video or message.reply_to_message.document)) and \
       not (message.video or message.document):
           await message.reply("Please reply to a video or document, or attach one with the command.")
           return

    data.append(message)
    if len(data) == 1:
        await handle_tasks(message, 'tg')
    else:
        await message.reply("📔 Waiting for queue...")
    await asyncio.sleep(1)

@Client.on_message(filters.command('af'))
async def audio_features(app, message):
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)

    # Check if replying to a file or file is attached
    if not (message.reply_to_message and (message.reply_to_message.video or message.reply_to_message.document)) and \
       not (message.video or message.document):
           await message.reply("Please reply to a video or document, or attach one with the command.")
           return

    data.append(message)
    if len(data) == 1:
        await handle_tasks(message, 'af')
    else:
        await message.reply("📔 Waiting for queue...")
    await asyncio.sleep(1)

@Client.on_message(filters.command('ddl'))
async def url_encode(app, message):
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)
    parts = message.text.split()
    if len(parts) == 1 or not re.match(r"^https?://", parts[1], re.I):
        await message.reply_text("Usage: /ddl [url] | [filename]\n<i>The link must start with http:// or https://</i>")
        return
    data.append(message)
    if len(data) == 1:
        await handle_tasks(message, 'url')
    else:
        await message.reply("📔 Waiting for queue...")
    await asyncio.sleep(1)


@Client.on_message(filters.command('batch'))
async def batch_encode(app, message):
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)
    parts = message.text.split()
    replied_zip = bool(message.reply_to_message and message.reply_to_message.document)
    if not replied_zip and (len(parts) == 1 or not re.match(r"^https?://", parts[1], re.I)):
        await message.reply_text("Usage: /batch [url] – or reply /batch to a .zip / archive\n"
                                 "<i>The link must start with http:// or https://</i>")
        return
    data.append(message)
    if len(data) == 1:
        await handle_tasks(message, 'batch')
    else:
        await message.reply("📔 Waiting for queue...")
    await asyncio.sleep(1)

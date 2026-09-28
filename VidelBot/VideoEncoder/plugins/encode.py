import asyncio
import re

from pyrogram import Client, filters

from .. import data, video_mimetype
from ..utils.database.add_user import AddUserToDatabase
from ..utils.helper import check_chat
from ..utils.tasks import handle_tasks, parse_trim_args


async def _enqueue(message, mode):
    """Run now if the encoder is idle, otherwise queue it and tell the user where they are."""
    data.append(message)
    if len(data) == 1:
        await handle_tasks(message, mode)
    else:
        ahead = len(data) - 1
        await message.reply(f"⏳ <b>Added to the queue</b> – position <b>#{len(data)}</b> "
                            f"({ahead} task{'s' if ahead != 1 else ''} ahead)\n<i>See it with /queue.</i>")
    await asyncio.sleep(1)


def _has_media(message) -> bool:
    return bool((message.reply_to_message and (message.reply_to_message.video or message.reply_to_message.document))
                or message.video or message.document)


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

    await _enqueue(message, 'tg')

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

    await _enqueue(message, 'af')

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
    await _enqueue(message, 'url')


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
    await _enqueue(message, 'batch')


@Client.on_message(filters.command('sample'))
async def sample_encode(app, message):
    """/sample [seconds] – quick test encode of a clip from the middle (default 30 s)."""
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)
    if not _has_media(message):
        await message.reply("🧪 <b>Sample encode</b>\nReply to a video with <code>/sample</code> (30 s) or "
                            "<code>/sample 60</code> to test your settings before encoding the whole file.")
        return
    await _enqueue(message, 'sample')


@Client.on_message(filters.command('trim'))
async def trim_video(app, message):
    """/trim start [end] – lossless cut (no re-encode)."""
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)
    if not _has_media(message) or not parse_trim_args(message.text or message.caption):
        await message.reply("✂️ <b>Trim</b>\nReply to a video with:\n<code>/trim 00:01:00 00:02:30</code> – cut a part\n"
                            "<code>/trim 90</code> – from 1:30 to the end\n<i>Lossless and fast (cuts at keyframes).</i>")
        return
    await _enqueue(message, 'trim')


@Client.on_message(filters.command('screens'))
async def screens_video(app, message):
    """/screens [n] – n evenly spread screenshots (1-10, default 6)."""
    c = await check_chat(message, chat='Both')
    if not c:
        return
    await AddUserToDatabase(app, message)
    if not _has_media(message):
        await message.reply("📸 <b>Screenshots</b>\nReply to a video with <code>/screens</code> (6 shots) or "
                            "<code>/screens 10</code>.")
        return
    await _enqueue(message, 'screens')

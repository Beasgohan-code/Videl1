import asyncio  # noqa: F401  (tests patch enc_plugin.asyncio.sleep)
import re

from pyrogram import Client, filters

import config

from ..utils.database.add_user import AddUserToDatabase
from ..utils.helper import check_chat
from ..utils.tasks import handle_tasks, parse_sample_args, parse_screens_args, parse_trim_args


async def _is_pro(uid: int) -> bool:
    try:
        from core.plans import is_encoder_pro
        return await is_encoder_pro(uid)
    except Exception:
        return False


async def _enqueue(message, mode, extra=None, card=None) -> bool:
    """Run now if a worker is free, otherwise queue it (Encoder Pro / Premium first) and say where it is.

    card: a message of ours (e.g. the /compress panel) to reuse as the status card instead of new replies."""
    async def say(text):
        if card is not None:
            try:
                await card.edit(text, reply_markup=None)
                return card
            except Exception:
                pass
        return await message.reply(text)
    from ..utils import scheduler
    uid = message.from_user.id if message.from_user else 0
    pro = await _is_pro(uid)
    # No await between the duplicate/limit checks and add: concurrent handlers on
    # this event loop cannot both admit the same job or exceed the user's cap.
    pos, same = scheduler.find_duplicate(message, mode, extra)
    if pos:                                   # already queued / running – never encode the same thing twice
        if not same:                          # (same message = Telegram re-delivered the update: stay quiet)
            state = "running now" if scheduler.is_running(scheduler.data[pos - 1]) else f"position <b>#{pos}</b>"
            await say(f"♻️ <b>Already in your queue</b> – {state}.\n"
                      "<i>Same file with the same settings, so it won't be encoded twice. See /queue.</i>")
        return False
    limit = config.ENC_MAX_TASKS_PRO if pro else config.ENC_MAX_TASKS_FREE
    have = scheduler.user_tasks(uid) if uid else 0
    if uid and uid not in config.ADMINS and have >= limit:
        await say(f"⏳ <b>You already have {have} task{'s' if have != 1 else ''} in the queue</b> "
                            f"(limit {limit}).\n<i>Wait for one to finish"
                            + ("" if pro else f", or get 🎬 Encoder Pro for up to {config.ENC_MAX_TASKS_PRO} + "
                                              "priority – /plans") + ".</i>")
        return False
    if scheduler.queue_full():
        await say(f"🚦 <b>Encoder queue is full</b> – {len(scheduler.data)} tasks waiting/running. "
                  "<i>Please try again in a bit.</i>")
        return False
    if scheduler.data and scheduler.memory_overloaded():
        await say("🔥 <b>Bot is overloaded</b> – memory is high right now. "
                  "<i>Please try again when a task finishes.</i>")
        return False
    if scheduler.disk_low():
        await say("💽 <b>Low disk space</b> – new encodes are paused until cleanup frees space. "
                  "<i>Please try again in a bit.</i>")
        return False
    pos = scheduler.add(message, mode, priority=pro, extra=extra)
    await scheduler.persist(message, mode, extra, pro)
    if scheduler.can_start(message):
        if card is not None:
            scheduler.NOTES[id(message)] = card       # handle_tasks turns it into the live status card
        scheduler.mark_running(message)
        await handle_tasks(message, mode)
    else:
        ahead = pos - 1
        note = await say(f"⏳ <b>Added to the queue</b> – position <b>#{pos}</b> "
                                   f"({ahead} task{'s' if ahead != 1 else ''} ahead)"
                                   + (" · ⚡ <b>priority</b>" if pro else "") + "\n<i>See it with /queue.</i>")
        if note is not None and scheduler.position(message):
            scheduler.NOTES[id(message)] = note   # becomes the live status card when the task starts
    return True


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
    if not _has_media(message) or parse_sample_args(message.text or message.caption) is None:
        await message.reply("🧪 <b>Sample encode</b>\nReply to a video with <code>/sample</code> (30 s) or "
                            "<code>/sample 60</code> to test your settings before encoding the whole file. "
                            "Lengths are limited to 5–120 seconds.")
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
    if not _has_media(message) or parse_screens_args(message.text or message.caption) is None:
        await message.reply("📸 <b>Screenshots</b>\nReply to a video with <code>/screens</code> (6 shots) or "
                            "<code>/screens 10</code>. Counts must be between 1 and 10.")
        return
    await _enqueue(message, 'screens')

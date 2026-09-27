"""
Videl • Content Saver engine.

Send any Telegram post link (public or private) and Videl copies / re-uploads it.
  • Public links  → copied directly by the bot (no login needed)
  • Private links (t.me/c/…) → downloaded with the user's own session (/login)
  • Ranges        → https://t.me/channel/100-120 saves posts 100…120

Extras applied to every saved file:
  • custom caption  (/set_caption, placeholders {filename} {size})
  • delete / replace words (/set_del_word, /set_repl_word)
  • custom thumbnail (/set_thumb)
  • auto-forward copy to the user's dump chat (/setchat)
"""

import asyncio
import os
import re
import shutil
import time

from pyrogram import Client, enums, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import (
    API_HASH, API_ID, FREE_LIMIT_DAILY, FREE_LIMIT_SIZE_GB, QR_CODE, SUBSCRIPTION, UPI_ID, PREMIUM_PRICES,
)
from core.ui import contact_row, rows
from database.db import db
from logger import LOGGER

logger = LOGGER(__name__)

FREE_LIMIT_SIZE = int(FREE_LIMIT_SIZE_GB * 1024 * 1024 * 1024)


class script(object):
    PREMIUM_TEXT = """<b>💎 Premium Membership Plans</b>
<b>Unlock Unlimited Access & Advanced Features!</b>
<blockquote><b>✨ Key Benefits:</b>
<b>♾️ Unlimited Daily Saves</b>
<b>📂 Support for 4GB+ File Sizes</b>
<b>⚡ Instant Processing (Zero Delay)</b>
<b>🖼 Customizable Thumbnails</b>
<b>📝 Personalized Captions</b>
<b>🛂 Priority Support</b></blockquote>
<blockquote><b>💳 Pricing Options:</b></blockquote>
{prices}
{payment}
<i>After payment, send the screenshot to the admin for activation.</i>
"""
    PROGRESS_BAR = """\
<b>⚡ Processing Task...</b>
<blockquote>
<b>Progress: {bar} {percentage:.1f}%</b>
<b>🚀 Speed:</b> <code>{speed}/s</code>
<b>💾 Size:</b> <code>{current} of {total}</code>
<b>⏱ Elapsed:</b> <code>{elapsed}</code>
<b>⏳ ETA:</b> <code>{eta}</code>
</blockquote>
"""
    LIMIT_REACHED = f"""<b>🚫 Daily Limit Exceeded</b>
<b>Your {FREE_LIMIT_DAILY} free saves for today have been used.</b>
<i>Quota resets automatically 24 hours after your first save.</i>
<blockquote><b>🔓 Upgrade to Premium for Unlimited Access!</b></blockquote>
"""
    SIZE_LIMIT = f"""<b>⚠️ File Size Exceeded</b>
<b>Free tier is limited to {FREE_LIMIT_SIZE_GB:g}GB per file.</b>
<blockquote><b>🔓 Upgrade to Premium</b></blockquote>
Save files up to 4GB and beyond with no limits!
"""


def premium_text() -> str:
    prices = "\n".join(f"• <b>{p.strip()}</b>" for p in PREMIUM_PRICES.split("|") if p.strip())
    pay = []
    if UPI_ID:
        pay.append(f"<b>💸 UPI ID:</b> <code>{UPI_ID}</code>")
    if QR_CODE:
        pay.append(f"<b>📸 QR Code:</b> <a href='{QR_CODE}'>Scan to Pay</a>")
    return script.PREMIUM_TEXT.format(prices=prices, payment="\n".join(pay))


async def send_banner(client: Client, chat_id: int, text: str, markup=None):
    """Send `text` with the SUBSCRIPTION banner if one is configured, else as plain text."""
    if SUBSCRIPTION:
        try:
            return await client.send_photo(chat_id, SUBSCRIPTION, caption=text[:1024],
                                           reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        except Exception as e:
            logger.warning(f"banner photo failed: {e}")
    return await client.send_message(chat_id, text, reply_markup=markup,
                                     parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


def premium_markup(back_cb: str = None):
    from config import STARS_PLANS
    stars = [InlineKeyboardButton(f"⭐ {price} · {'Lifetime' if days == 0 else f'{days} days'}",
                                  callback_data=f"stars_buy:{days}") for days, price in STARS_PLANS[:4]]
    kb = rows(
        *[stars[i:i + 2] for i in range(0, len(stars), 2)],
        contact_row("📸 Send Payment Proof"),
        [InlineKeyboardButton("⬅️ Back", callback_data=back_cb)] if back_cb else [],
        [InlineKeyboardButton("❌ Close", callback_data="close_btn")],
    )
    return InlineKeyboardMarkup(kb)


def humanbytes(size):
    if not size:
        return "0B"
    power = 2 ** 10
    n = 0
    units = {0: " ", 1: "K", 2: "M", 3: "G", 4: "T"}
    while size > power and n < 4:
        size /= power
        n += 1
    return str(round(size, 2)) + " " + units[n] + "B"


def TimeFormatter(milliseconds: int) -> str:
    seconds, milliseconds = divmod(int(milliseconds), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    tmp = ((str(days) + "d, ") if days else "") + \
        ((str(hours) + "h, ") if hours else "") + \
        ((str(minutes) + "m, ") if minutes else "") + \
        ((str(seconds) + "s, ") if seconds else "")
    return tmp[:-2] if tmp else "0s"


class batch_temp(object):
    # user_id -> True (idle / cancelled) | False (task running)
    IS_BATCH = {}


def get_message_type(msg):
    if getattr(msg, "document", None):
        return "Document"
    if getattr(msg, "video", None):
        return "Video"
    if getattr(msg, "photo", None):
        return "Photo"
    if getattr(msg, "audio", None):
        return "Audio"
    if getattr(msg, "voice", None):
        return "Voice"
    if getattr(msg, "animation", None):
        return "Animation"
    if getattr(msg, "sticker", None):
        return "Sticker"
    if getattr(msg, "text", None):
        return "Text"
    return None


async def _status_updater(client, statusfile, message, chat):
    while not os.path.exists(statusfile):
        await asyncio.sleep(3)
    while os.path.exists(statusfile):
        try:
            with open(statusfile, "r", encoding="utf-8") as f:
                txt = f.read()
            await client.edit_message_text(chat, message.id, txt)
        except Exception:
            pass
        await asyncio.sleep(5)


downstatus = _status_updater
upstatus = _status_updater


def progress(current, total, message, type):
    if batch_temp.IS_BATCH.get(message.from_user.id):
        raise Exception("Cancelled")
    if not hasattr(progress, "cache"):
        progress.cache = {}
        progress.start_time = {}

    now = time.time()
    task_id = f"{message.id}{type}"
    last_time = progress.cache.get(task_id, 0)
    progress.start_time.setdefault(task_id, now)

    if (now - last_time) > 5 or current == total:
        try:
            elapsed = now - progress.start_time[task_id]
            percentage = current * 100 / total if total else 0
            speed = current / elapsed if elapsed > 0 else 0
            eta = (total - current) / speed if speed > 0 else 0
            filled = int(percentage / 5)
            bar = "█" * filled + "░" * (20 - filled)
            status = script.PROGRESS_BAR.format(
                bar=bar, percentage=percentage,
                current=humanbytes(current), total=humanbytes(total),
                speed=humanbytes(speed), elapsed=TimeFormatter(elapsed * 1000),
                eta=TimeFormatter(eta * 1000),
            )
            with open(f"{message.id}{type}status.txt", "w", encoding="utf-8") as f:
                f.write(status)
            progress.cache[task_id] = now
            if current == total:
                progress.start_time.pop(task_id, None)
                progress.cache.pop(task_id, None)
        except Exception:
            pass


@Client.on_message(filters.command(["plan"]) & filters.private)
async def send_plan(client: Client, message: Message):
    await send_banner(client, message.chat.id, premium_text(), premium_markup())


# ──────────────────────────────────────────────────────────────
# Word filters + caption helpers
# ──────────────────────────────────────────────────────────────
async def apply_word_filters(user_id: int, text: str) -> str:
    if not text:
        return text
    try:
        for w in await db.get_delete_words(user_id) or []:
            text = re.sub(re.escape(w), "", text, flags=re.IGNORECASE)
        for target, repl in (await db.get_replace_words(user_id) or {}).items():
            text = re.sub(re.escape(target), repl, text, flags=re.IGNORECASE)
    except Exception as e:
        logger.warning(f"word filter failed for {user_id}: {e}")
    return re.sub(r"[ \t]{2,}", " ", text).strip()


async def build_caption(user_id: int, filename: str, size: int, original: str) -> str:
    custom = await db.get_caption(user_id)
    if custom:
        try:
            cap = custom.format(filename=filename, size=humanbytes(size))
        except (KeyError, IndexError, ValueError):
            cap = custom
    else:
        cap = original or ""
    return (await apply_word_filters(user_id, cap))[:1024]


async def forward_to_dump(client: Client, user_id: int, sent: Message):
    try:
        dump = await db.get_dump_chat(user_id)
        if dump and sent:
            await sent.copy(int(dump))
    except Exception as e:
        logger.warning(f"dump chat copy failed for {user_id}: {e}")


# ──────────────────────────────────────────────────────────────
# Link handler (only fires for messages containing a t.me link)
# ──────────────────────────────────────────────────────────────
@Client.on_message(filters.text & filters.private & ~filters.regex(r"^/") & filters.regex(r"https?://t\.me/"))
async def save(client: Client, message: Message):
    user_id = message.from_user.id
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, message.from_user.first_name)

    if await db.check_limit(user_id):
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade to Premium", callback_data="buy_premium")]])
        return await send_banner(client, message.chat.id, script.LIMIT_REACHED, btn)

    if batch_temp.IS_BATCH.get(user_id) is False:
        return await message.reply_text(
            "<b>⚠️ A task is currently running.</b>\n<i>Wait for it to finish or use /cancel.</i>",
            parse_mode=enums.ParseMode.HTML,
        )

    link = re.search(r"https?://t\.me/\S+", message.text).group(0)
    datas = link.split("/")
    temp = datas[-1].replace("?single", "").split("?")[0].split("-")
    try:
        fromID = int(temp[0].strip())
    except ValueError:
        return await message.reply_text("❌ <b>Invalid post link.</b>", parse_mode=enums.ParseMode.HTML)
    try:
        toID = int(temp[1].strip())
    except (IndexError, ValueError):
        toID = fromID

    is_premium = await db.check_premium(user_id)
    if not is_premium and toID - fromID + 1 > 5:
        await message.reply_text(
            "<i>ℹ️ Free users can save up to 5 posts per batch – saving the first 5.</i>",
            parse_mode=enums.ParseMode.HTML,
        )
        toID = fromID + 4

    batch_temp.IS_BATCH[user_id] = False
    is_private_link = "t.me/c/" in link
    is_batch = "t.me/b/" in link
    is_public_link = not is_private_link and not is_batch
    acc = None

    try:
        for msgid in range(fromID, toID + 1):
            if batch_temp.IS_BATCH.get(user_id):
                break

            if is_public_link:
                username = datas[3]
                try:
                    sent = await client.copy_message(
                        chat_id=message.chat.id, from_chat_id=username,
                        message_id=msgid, reply_to_message_id=message.id,
                    )
                    await db.add_traffic(user_id)
                    await forward_to_dump(client, user_id, sent)
                    await asyncio.sleep(1)
                    continue
                except Exception:
                    pass  # restricted → fall back to the user session

            if acc is None:
                session = await db.get_session(user_id)
                if session is None:
                    await message.reply(
                        "<b>🔒 Login Required</b>\n\n"
                        "<i>This content is restricted. Use /login to connect your account.</i>",
                        parse_mode=enums.ParseMode.HTML,
                    )
                    return
                try:
                    acc = Client(
                        f"saver_{user_id}", session_string=session, api_hash=API_HASH,
                        api_id=API_ID, in_memory=True, no_updates=True,
                        max_concurrent_transmissions=10,
                    )
                    await acc.connect()
                except Exception as e:
                    acc = None
                    return await message.reply(
                        "<b>❌ Authentication Failed</b>\n\n"
                        "<i>Your session may have expired. Please /logout and /login again.</i>\n"
                        f"<code>{e}</code>",
                        parse_mode=enums.ParseMode.HTML,
                    )

            if is_private_link:
                chat_target = int("-100" + datas[4])
            elif is_batch:
                chat_target = datas[4]
            else:
                chat_target = datas[3]
            await handle_restricted_content(client, acc, message, chat_target, msgid)
            await asyncio.sleep(2)
    finally:
        batch_temp.IS_BATCH[user_id] = True
        if acc is not None:
            try:
                await acc.disconnect()
            except Exception:
                pass


async def handle_restricted_content(client: Client, acc, message: Message, chat_target, msgid):
    user_id = message.from_user.id
    try:
        msg: Message = await acc.get_messages(chat_target, msgid)
    except Exception as e:
        logger.error(f"Error fetching message: {e}")
        return
    if not msg or msg.empty:
        return

    msg_type = get_message_type(msg)
    if not msg_type:
        return

    file_size = 0
    media = getattr(msg, msg_type.lower(), None) if msg_type not in ("Text", "Photo") else None
    if media is not None:
        file_size = getattr(media, "file_size", 0) or 0

    if file_size > FREE_LIMIT_SIZE and not await db.check_premium(user_id):
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("💎 Upgrade to Premium", callback_data="buy_premium")]])
        await client.send_message(message.chat.id, script.SIZE_LIMIT, reply_markup=btn,
                                  parse_mode=enums.ParseMode.HTML)
        return

    if msg_type == "Text":
        try:
            text = await apply_word_filters(user_id, msg.text.html if hasattr(msg.text, "html") else msg.text)
            sent = await client.send_message(message.chat.id, text, parse_mode=enums.ParseMode.HTML)
            await forward_to_dump(client, user_id, sent)
        except Exception:
            pass
        return

    await db.add_traffic(user_id)
    smsg = await client.send_message(message.chat.id, "<b>⬇️ Starting Download...</b>",
                                     reply_to_message_id=message.id, parse_mode=enums.ParseMode.HTML)
    temp_dir = f"downloads/{message.id}_{msgid}"
    os.makedirs(temp_dir, exist_ok=True)
    down_status = f"{message.id}downstatus.txt"
    up_status = f"{message.id}upstatus.txt"

    try:
        asyncio.create_task(downstatus(client, down_status, smsg, message.chat.id))
        file = await acc.download_media(msg, file_name=f"{temp_dir}/", progress=progress,
                                        progress_args=[message, "down"])
        if os.path.exists(down_status):
            os.remove(down_status)
        if not file:
            raise Exception("download returned nothing")
    except Exception as e:
        if os.path.exists(down_status):
            os.remove(down_status)
        shutil.rmtree(temp_dir, ignore_errors=True)
        if batch_temp.IS_BATCH.get(user_id) or "Cancelled" in str(e):
            return await smsg.edit("❌ <b>Task Cancelled</b>", parse_mode=enums.ParseMode.HTML)
        logger.error(f"download failed: {e}")
        return await smsg.edit(f"❌ <b>Download failed:</b> <code>{e}</code>", parse_mode=enums.ParseMode.HTML)

    sent = None
    try:
        asyncio.create_task(upstatus(client, up_status, smsg, message.chat.id))
        ph_path = None
        thumb_id = await db.get_thumbnail(user_id)
        if thumb_id:
            try:
                ph_path = await client.download_media(thumb_id, file_name=f"{temp_dir}/custom_thumb.jpg")
            except Exception as e:
                logger.error(f"Failed to download custom thumb: {e}")
        if not ph_path:
            try:
                thumbs = getattr(media, "thumbs", None) if media is not None else None
                if thumbs:
                    ph_path = await acc.download_media(thumbs[0].file_id, file_name=f"{temp_dir}/thumb.jpg")
            except Exception:
                pass

        filename = os.path.basename(file)
        caption = await build_caption(user_id, filename, file_size,
                                      msg.caption.html if msg.caption and hasattr(msg.caption, "html") else (msg.caption or ""))
        common = dict(caption=caption, parse_mode=enums.ParseMode.HTML)

        if msg_type == "Document":
            sent = await client.send_document(message.chat.id, file, thumb=ph_path, progress=progress,
                                              progress_args=[message, "up"], **common)
        elif msg_type == "Video":
            sent = await client.send_video(message.chat.id, file, duration=msg.video.duration,
                                           width=msg.video.width, height=msg.video.height, thumb=ph_path,
                                           supports_streaming=True, progress=progress,
                                           progress_args=[message, "up"], **common)
        elif msg_type == "Animation":
            sent = await client.send_animation(message.chat.id, file, **common)
        elif msg_type in ("Audio", "Voice"):
            sent = await client.send_audio(message.chat.id, file, thumb=ph_path, progress=progress,
                                           progress_args=[message, "up"], **common)
        elif msg_type == "Photo":
            sent = await client.send_photo(message.chat.id, file, **common)
        elif msg_type == "Sticker":
            sent = await client.send_sticker(message.chat.id, file)
        await forward_to_dump(client, user_id, sent)
    except Exception as e:
        await smsg.edit(f"Upload Failed: {e}")
    finally:
        if os.path.exists(up_status):
            os.remove(up_status)
        shutil.rmtree(temp_dir, ignore_errors=True)
    try:
        await client.delete_messages(message.chat.id, [smsg.id])
    except Exception:
        pass

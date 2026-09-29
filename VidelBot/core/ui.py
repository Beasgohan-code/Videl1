"""Shared UI helpers for every Videl module (no handlers in here)."""
import asyncio
import logging
import random

import aiohttp
from pyrogram import enums, raw, utils
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton

from config import (CONTACT_URL, MESSAGE_EFFECTS, RANDOM_START_PIC, START_PICS,
                    START_REACTIONS, SUPPORT_URL, UPDATES_URL)

log = logging.getLogger("videl.ui")
HTML = enums.ParseMode.HTML

# Reactions every bot may use (the "standard" free reaction set).
REACTIONS = ["👍", "❤", "🔥", "🥰", "👏", "😁", "🎉", "🤩", "🙏", "👌", "😍", "🐳", "❤‍🔥",
             "💯", "⚡", "🏆", "🍾", "😎", "👀", "🤗", "🫡", "🆒", "🦄", "😇", "🤝", "✍"]

# Animated message effects (private chats only).
EFFECTS = {
    "fire": 5104841245755180586,
    "like": 5107584321108051014,
    "heart": 5159385139981059251,
    "party": 5046509860389126442,
}

_PIC_APIS = ["https://api.waifu.pics/sfw/waifu", "https://nekos.life/api/v2/img/waifu"]
_FALLBACK_PICS = ["https://i.postimg.cc/kX9tjGXP/16.png", "https://i.postimg.cc/cC7txyhz/15.png"]


# ─────────────────────────── keyboards ───────────────────────────
def contact_row(label: str = "📞 Contact Admin"):
    """A one-button row linking to CONTACT_URL, or [] when it isn't configured."""
    return [InlineKeyboardButton(label, url=CONTACT_URL)] if CONTACT_URL else []


def links_row():
    """Optional Updates / Support buttons (only the ones that are configured)."""
    row = []
    if UPDATES_URL:
        row.append(InlineKeyboardButton("📢 Updates", url=UPDATES_URL))
    if SUPPORT_URL:
        row.append(InlineKeyboardButton("💬 Support", url=SUPPORT_URL))
    return row


def rows(*maybe_rows):
    """Build a keyboard, silently dropping empty rows."""
    return [r for r in maybe_rows if r]


def copy_button(label: str, text: str) -> InlineKeyboardButton:
    """Native 'copy to clipboard' button (Bot API 7.11 / layer 193+)."""
    return InlineKeyboardButton(label, copy_text=text[:256])


# ─────────────────────────── formatting ───────────────────────────
def humanbytes(size) -> str:
    if not size:
        return "0 B"
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def readable_time(seconds: float) -> str:
    seconds = int(seconds)
    parts = []
    for name, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size or (name == "s" and not parts):
            val, seconds = divmod(seconds, size)
            parts.append(f"{val}{name}")
    return " ".join(parts)


# ─────────────────────────── pictures / effects ───────────────────────────
_pic_pool: list = []          # pre-fetched random pic URLs → /start never waits for the pic API
_pic_refill = None
PIC_POOL_SIZE = 12


async def _fetch_pic(session) -> str:
    try:
        async with session.get(random.choice(_PIC_APIS)) as r:
            if r.status == 200:
                return (await r.json(content_type=None)).get("url") or ""
    except Exception as e:
        log.debug(f"pic api failed: {e}")
    return ""


async def fill_pic_pool(n: int = PIC_POOL_SIZE):
    """Top the pool up (concurrently). Safe to call any time; failures just leave it smaller."""
    need = n - len(_pic_pool)
    if need <= 0 or START_PICS or not RANDOM_START_PIC:
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            urls = await asyncio.gather(*(_fetch_pic(s) for _ in range(need)))
        _pic_pool.extend(u for u in urls if u and u not in _pic_pool)
    except Exception as e:
        log.debug(f"pic pool refill failed: {e}")


def _schedule_refill():
    global _pic_refill
    if _pic_refill and not _pic_refill.done():
        return
    try:
        _pic_refill = asyncio.get_running_loop().create_task(fill_pic_pool())
    except RuntimeError:
        pass


async def random_start_pic() -> str:
    """START_PIC (random if several) or – like the original saver – a random SFW anime pic.
    Pics come from a pool filled in the background, so the menu is never held up by the pic API."""
    if START_PICS:
        return random.choice(START_PICS)
    if not RANDOM_START_PIC:
        return ""
    if len(_pic_pool) < PIC_POOL_SIZE // 2:
        _schedule_refill()
    if _pic_pool:
        return _pic_pool.pop(random.randrange(len(_pic_pool)))
    return random.choice(_FALLBACK_PICS)


def effect(name: str = "fire"):
    return EFFECTS.get(name) if MESSAGE_EFFECTS else None


_bg_tasks: set = set()


def background(coro):
    """Fire-and-forget: run *coro* without delaying the reply (errors are logged, never raised)."""
    import asyncio

    async def _run():
        try:
            await coro
        except Exception as e:  # noqa: BLE001
            logging.getLogger("videl.ui").debug(f"background task failed: {e}")

    task = asyncio.get_event_loop().create_task(_run())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


async def react(message, big: bool = True):
    if not START_REACTIONS:
        return
    try:
        await message.react(emoji=random.choice(REACTIONS), big=big)
    except Exception:
        pass


# ─────────────────────────── sending / editing ───────────────────────────
async def send_with_preview(client, chat_id: int, text: str, reply_markup=None, pic: str = "",
                            reply_to: int = None, effect_id: int = None):
    """
    Send `text` with `pic` rendered as a LARGE link preview shown ABOVE the text
    (link_preview_options: prefer_large_media + show_above_text). Unlike a photo,
    the result is a text message → up to 4096 chars and every module can edit it.

    With the aiogram bridge on, the message goes out through the Bot API so the
    buttons get colours (Bot API 9.4 ``style``); otherwise / on error → MTProto.
    """
    from core import botapi
    if botapi.is_main(client) and await botapi.send_text(chat_id, text, reply_markup, pic=pic, reply_to=reply_to,
                                                         effect_id=effect_id):
        return True
    if pic:
        try:
            return await client.send_web_page(
                chat_id, url=pic, text=text, parse_mode=HTML, large_media=True, invert_media=True,
                reply_to_message_id=reply_to, message_effect_id=effect_id, reply_markup=reply_markup,
            )
        except Exception as e:
            log.debug(f"send_web_page failed ({e}), falling back to plain text")
    try:
        return await client.send_message(
            chat_id, text, parse_mode=HTML, disable_web_page_preview=True, reply_to_message_id=reply_to,
            message_effect_id=effect_id, reply_markup=reply_markup,
        )
    except Exception:
        # effects are rejected in groups → retry without
        return await client.send_message(chat_id, text, parse_mode=HTML, disable_web_page_preview=True,
                                         reply_to_message_id=reply_to, reply_markup=reply_markup)


async def edit_with_preview(client, message, text: str, reply_markup=None, pic: str = ""):
    """Edit a text message and attach `pic` as a large preview above the text."""
    from core import botapi, rich
    if botapi.is_main(client) and await botapi.edit_text(message.chat.id, message.id, text, reply_markup, pic=pic):
        rich.mark(message.chat.id, message.id, False)
        return
    if rich.is_rich(message.chat.id, message.id):
        # Telegram refused to turn our rich screen back into a text one → replace the message
        rich.mark(message.chat.id, message.id, False)
        await send_with_preview(client, message.chat.id, text, reply_markup, pic=pic)
        try:
            await message.delete()
        except Exception:
            pass
        return
    if not pic:
        return await smart_edit(message, text, reply_markup)
    try:
        parsed = await utils.parse_text_entities(client, text, HTML, None)
        await client.invoke(raw.functions.messages.EditMessage(
            peer=await client.resolve_peer(message.chat.id),
            id=message.id,
            message=parsed["message"],
            entities=parsed["entities"],
            media=raw.types.InputMediaWebPage(url=pic, force_large_media=True, optional=True),
            invert_media=True,
            reply_markup=await reply_markup.write(client) if reply_markup else None,
        ))
    except MessageNotModified:
        pass
    except Exception as e:
        log.debug(f"preview edit failed ({e}), falling back")
        await smart_edit(message, text, reply_markup)


async def smart_edit(message, text: str, reply_markup=None, preview: bool = False):
    """
    Edit any bot message in place: text messages → edit text; media messages →
    edit caption when it fits, otherwise replace the message with a text one.
    """
    try:
        if message.text is not None or not message.media:
            from core import botapi, rich
            if botapi.is_main(getattr(message, "_client", None)) and \
                    await botapi.edit_text(message.chat.id, message.id, text, reply_markup, preview=preview):
                rich.mark(message.chat.id, message.id, False)
                return message
            if rich.is_rich(message.chat.id, message.id):
                rich.mark(message.chat.id, message.id, False)
                new = await message.reply_text(text, reply_markup=reply_markup, parse_mode=HTML,
                                               disable_web_page_preview=not preview, quote=False)
                try:
                    await message.delete()
                except Exception:
                    pass
                return new
            return await message.edit_text(text, reply_markup=reply_markup, parse_mode=HTML,
                                           disable_web_page_preview=not preview)
        if len(text) <= 1024:
            return await message.edit_caption(text, reply_markup=reply_markup, parse_mode=HTML)
        new = await message.reply_text(text, reply_markup=reply_markup, parse_mode=HTML,
                                       disable_web_page_preview=not preview, quote=False)
        try:
            await message.delete()
        except Exception:
            pass
        return new
    except MessageNotModified:
        return message
    except Exception as e:
        log.warning(f"smart_edit failed: {e}")


async def group_reply(message, text: str, reply_markup=None):
    """Reply to a command. In groups the answer is an *ephemeral* message (Bot API
    10.3) that only the caller sees – no chat spam; elsewhere / on failure a normal reply."""
    from pyrogram.enums import ChatType
    u = message.from_user
    if u and message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        from core import botapi
        if botapi.is_main(getattr(message, "_client", None)) and await botapi.ephemeral(message.chat.id, u.id, text, reply_markup):
            return True
    return await message.reply_text(text, reply_markup=reply_markup, parse_mode=HTML, disable_web_page_preview=True)


# ─────────────────────────── uploads ───────────────────────────
async def upload_to_host(path: str):
    """
    Upload a local file and return a public URL (or None).
    freeimage.host when FREEIMAGE_API_KEY is set (images only), else catbox.moe (≤ 200 MB).
    """
    import os

    from config import FREEIMAGE_API_KEY

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
            if FREEIMAGE_API_KEY and path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                with open(path, "rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("key", FREEIMAGE_API_KEY)
                    form.add_field("source", f, filename=os.path.basename(path))
                    async with s.post("https://freeimage.host/api/1/upload", data=form) as r:
                        if r.status == 200:
                            j = await r.json(content_type=None)
                            url = (j.get("image") or {}).get("url")
                            if url:
                                return url
            with open(path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("reqtype", "fileupload")
                form.add_field("fileToUpload", f, filename=os.path.basename(path))
                async with s.post("https://catbox.moe/user/api.php", data=form) as r:
                    text = (await r.text()).strip()
                    if r.status == 200 and text.startswith("http"):
                        return text
                    log.warning(f"catbox upload failed: {r.status} {text[:100]}")
    except Exception as e:
        log.warning(f"upload_to_host failed: {e}")
    return None

"""Shared UI helpers for every Videl module (WzGram Layer 232 + Bot API 10.3)."""
import asyncio
import logging
import random

import aiohttp
import pyrogram
from pyrogram import enums, raw, utils
from pyrogram.errors import MessageNotModified
from pyrogram.types import (
    CopyTextButton,
    DisabledButton,
    EphemeralMessageParameters,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    ReplyParameters,
)

from config import (
    COLORED_BUTTONS,
    CONTACT_URL,
    EPHEMERAL_REPLIES,
    MESSAGE_EFFECTS,
    RANDOM_START_PIC,
    START_PICS,
    START_REACTIONS,
    SUPPORT_URL,
    UPDATES_URL,
)

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

_STYLE_ENUM = {
    "default": enums.ButtonStyle.DEFAULT,
    "primary": enums.ButtonStyle.PRIMARY,
    "success": enums.ButtonStyle.SUCCESS,
    "danger": enums.ButtonStyle.DANGER,
}


def is_wzgram_client(client) -> bool:
    """True when *client* is a live WzGram/Pyrogram Client instance (not a test FakeClient)."""
    return isinstance(client, pyrogram.Client)


# ─────────────────────────── WzGram options & keyboards ───────────────────────────
def link_preview_options(
    url: str = "",
    *,
    is_disabled: bool | None = None,
    prefer_large_media: bool = False,
    prefer_small_media: bool = False,
    show_above_text: bool = False,
) -> LinkPreviewOptions:
    """Build WzGram ``LinkPreviewOptions`` (Bot API 7+ / Layer 232)."""
    if url:
        return LinkPreviewOptions(
            url=url,
            is_disabled=False if is_disabled is None else is_disabled,
            prefer_large_media=prefer_large_media,
            prefer_small_media=prefer_small_media,
            show_above_text=show_above_text,
        )
    return LinkPreviewOptions(is_disabled=True if is_disabled is None else is_disabled)


def reply_parameters(
    message_id: int | None = None,
    *,
    chat_id: int | str | None = None,
    quote: str | None = None,
    quote_parse_mode=None,
    checklist_task_id: int | None = None,
) -> ReplyParameters | None:
    """Build WzGram ``ReplyParameters`` (supports cross-chat & rich quotes)."""
    if not message_id:
        return None
    return ReplyParameters(
        message_id=message_id,
        chat_id=chat_id,
        quote=quote[:1024] if quote else None,
        quote_parse_mode=quote_parse_mode,
        checklist_task_id=checklist_task_id,
    )


def button_style(style: str | enums.ButtonStyle | None) -> enums.ButtonStyle:
    if isinstance(style, enums.ButtonStyle):
        return style if COLORED_BUTTONS else enums.ButtonStyle.DEFAULT
    if not style or not COLORED_BUTTONS:
        return enums.ButtonStyle.DEFAULT
    return _STYLE_ENUM.get(str(style).strip().lower(), enums.ButtonStyle.DEFAULT)


def styled_button(
    label: str,
    *,
    style: str | enums.ButtonStyle | None = None,
    disabled: bool = False,
    icon_custom_emoji_id: str | None = None,
    copy_text: str | CopyTextButton | None = None,
    **kwargs,
) -> InlineKeyboardButton:
    """Create a WzGram ``InlineKeyboardButton`` with native ``ButtonStyle`` / ``DisabledButton``."""
    kw = dict(kwargs)
    if copy_text is not None:
        ct = copy_text.text if hasattr(copy_text, "text") else str(copy_text)
        kw["copy_text"] = CopyTextButton(text=ct[:256])
    if disabled:
        kw.pop("callback_data", None)
        kw["disabled"] = DisabledButton()
        kw["style"] = enums.ButtonStyle.DEFAULT
    else:
        kw["style"] = button_style(style)
    if icon_custom_emoji_id:
        kw["icon_custom_emoji_id"] = str(icon_custom_emoji_id)
    return InlineKeyboardButton(label, **kw)


btn = styled_button


def wzgram_markup(markup):
    """Apply automatic ``ButtonStyle`` & ``DisabledButton`` to an ``InlineKeyboardMarkup`` for WzGram MTProto."""
    if markup is None or not COLORED_BUTTONS:
        return markup
    inline = getattr(markup, "inline_keyboard", None)
    if inline is None:
        return markup
    from core import botapi
    out = []
    for row in inline:
        new_row = []
        for b in row:
            cur_style = getattr(b, "style", enums.ButtonStyle.DEFAULT)
            cb_raw = getattr(b, "callback_data", None)
            cb_str = (cb_raw.decode() if isinstance(cb_raw, bytes) else str(cb_raw)) if cb_raw is not None else ""
            if getattr(b, "disabled", None) is not None or (cb_str and botapi.is_noop(cb_str)):
                new_row.append(
                    InlineKeyboardButton(
                        b.text,
                        disabled=DisabledButton(),
                        icon_custom_emoji_id=getattr(b, "icon_custom_emoji_id", None),
                    )
                )
                continue
            if cur_style == enums.ButtonStyle.DEFAULT:
                auto = botapi.style_for(b.text, cb_str)
                if auto:
                    cur_style = button_style(auto)
            if cur_style != getattr(b, "style", enums.ButtonStyle.DEFAULT):
                new_row.append(
                    InlineKeyboardButton(
                        b.text,
                        callback_data=b.callback_data,
                        url=b.url,
                        web_app=b.web_app,
                        login_url=b.login_url,
                        user_id=b.user_id,
                        switch_inline_query=b.switch_inline_query,
                        switch_inline_query_current_chat=b.switch_inline_query_current_chat,
                        callback_game=b.callback_game,
                        switch_inline_query_chosen_chat=b.switch_inline_query_chosen_chat,
                        copy_text=b.copy_text,
                        pay=b.pay,
                        icon_custom_emoji_id=getattr(b, "icon_custom_emoji_id", None),
                        style=cur_style,
                        requires_password=getattr(b, "requires_password", None),
                    )
                )
            else:
                new_row.append(b)
        out.append(new_row)
    return InlineKeyboardMarkup(out)


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


def copy_button(label: str, text: str, *, style: str | enums.ButtonStyle | None = None) -> InlineKeyboardButton:
    """Native 'copy to clipboard' button (CopyTextButton, Bot API 7.11 / Layer 232)."""
    return InlineKeyboardButton(label, copy_text=CopyTextButton(text=str(text)[:256]), style=button_style(style))


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
                            reply_to: int = None, effect_id: int = None, quote: str = None):
    """
    Send ``text`` with ``pic`` rendered as a LARGE link preview shown ABOVE the text
    (WzGram ``LinkPreviewOptions(prefer_large_media=True, show_above_text=True)`` +
    ``ReplyParameters`` + native ``ButtonStyle``).
    """
    from core import botapi
    if botapi.is_main(client) and await botapi.send_text(chat_id, text, reply_markup, pic=pic, reply_to=reply_to,
                                                         effect_id=effect_id):
        return True
    if is_wzgram_client(client):
        lpo = link_preview_options(pic, prefer_large_media=bool(pic), show_above_text=bool(pic))
        rp = reply_parameters(reply_to, quote=quote)
        kb = wzgram_markup(reply_markup)
        try:
            return await client.send_message(
                chat_id,
                text,
                parse_mode=HTML,
                link_preview_options=lpo,
                reply_parameters=rp,
                effect_id=effect_id if int(chat_id) > 0 else None,
                reply_markup=kb,
            )
        except Exception:
            if pic:
                return await client.send_message(
                    chat_id,
                    text,
                    parse_mode=HTML,
                    link_preview_options=link_preview_options(is_disabled=True),
                    reply_parameters=rp,
                    reply_markup=kb,
                )
            return await client.send_message(
                chat_id,
                text,
                parse_mode=HTML,
                link_preview_options=lpo,
                reply_parameters=rp,
                reply_markup=kb,
            )
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
        return await client.send_message(chat_id, text, parse_mode=HTML, disable_web_page_preview=True,
                                         reply_to_message_id=reply_to, reply_markup=reply_markup)


async def edit_with_preview(client, message, text: str, reply_markup=None, pic: str = ""):
    """Edit a text message and attach ``pic`` as a large preview above the text."""
    from core import botapi, rich
    if botapi.is_main(client) and await botapi.edit_text(message.chat.id, message.id, text, reply_markup, pic=pic):
        rich.mark(message.chat.id, message.id, False)
        return
    if rich.is_rich(message.chat.id, message.id):
        rich.mark(message.chat.id, message.id, False)
        await send_with_preview(client, message.chat.id, text, reply_markup, pic=pic)
        try:
            await message.delete()
        except Exception:
            pass
        return
    if not pic:
        return await smart_edit(message, text, reply_markup)
    if is_wzgram_client(client):
        try:
            return await client.edit_message_text(
                message.chat.id,
                message.id,
                text,
                parse_mode=HTML,
                link_preview_options=link_preview_options(pic, prefer_large_media=True, show_above_text=True),
                reply_markup=wzgram_markup(reply_markup),
            )
        except MessageNotModified:
            return
        except Exception as e:
            log.debug(f"wzgram preview edit failed ({e}), falling back")
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
        client = getattr(message, "_client", None)
        if message.text is not None or not message.media:
            from core import botapi, rich
            if botapi.is_main(client) and \
                    await botapi.edit_text(message.chat.id, message.id, text, reply_markup, preview=preview):
                rich.mark(message.chat.id, message.id, False)
                return message
            kb = wzgram_markup(reply_markup) if is_wzgram_client(client) else reply_markup
            if rich.is_rich(message.chat.id, message.id):
                rich.mark(message.chat.id, message.id, False)
                if is_wzgram_client(client):
                    new = await message.reply_text(
                        text,
                        reply_markup=kb,
                        parse_mode=HTML,
                        link_preview_options=link_preview_options(is_disabled=not preview),
                        quote=False,
                    )
                else:
                    new = await message.reply_text(text, reply_markup=kb, parse_mode=HTML,
                                                   disable_web_page_preview=not preview, quote=False)
                try:
                    await message.delete()
                except Exception:
                    pass
                return new
            if is_wzgram_client(client):
                return await message.edit_text(
                    text,
                    reply_markup=kb,
                    parse_mode=HTML,
                    link_preview_options=link_preview_options(is_disabled=not preview),
                )
            return await message.edit_text(text, reply_markup=kb, parse_mode=HTML,
                                           disable_web_page_preview=not preview)
        kb = wzgram_markup(reply_markup) if is_wzgram_client(client) else reply_markup
        if len(text) <= 1024:
            return await message.edit_caption(text, reply_markup=kb, parse_mode=HTML)
        if is_wzgram_client(client):
            new = await message.reply_text(
                text,
                reply_markup=kb,
                parse_mode=HTML,
                link_preview_options=link_preview_options(is_disabled=not preview),
                quote=False,
            )
        else:
            new = await message.reply_text(text, reply_markup=kb, parse_mode=HTML,
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


def thread_id_of(message) -> int | None:
    """Return forum / private-chat topic ``message_thread_id`` when present."""
    tid = getattr(message, "message_thread_id", None)
    if tid and (getattr(message, "is_topic_message", False) or getattr(getattr(message, "chat", None), "is_forum", False)):
        return int(tid)
    return int(tid) if tid else None


async def edit_ephemeral(client, chat_id: int, user_id: int, message_id: int, text: str, reply_markup=None) -> bool:
    """Edit an ephemeral message in-place (Bot API 10.3 → WzGram Layer 232 → normal edit fallback)."""
    from core import botapi
    if botapi.is_main(client) and await botapi.edit_ephemeral_text(
        chat_id, user_id, message_id, text=text, reply_markup=reply_markup
    ):
        return True
    if EPHEMERAL_REPLIES and is_wzgram_client(client):
        try:
            await client.edit_ephemeral_message_text(
                chat_id,
                user_id,
                message_id,
                text=text,
                parse_mode=HTML,
                link_preview_options=link_preview_options(is_disabled=True),
                reply_markup=wzgram_markup(reply_markup),
            )
            return True
        except MessageNotModified:
            return True
        except Exception as e:
            log.debug(f"wzgram edit_ephemeral fallback ({e})")
    try:
        kb = wzgram_markup(reply_markup) if is_wzgram_client(client) else reply_markup
        await client.edit_message_text(chat_id, message_id, text, reply_markup=kb, parse_mode=HTML)
        return True
    except MessageNotModified:
        return True
    except Exception as e:
        log.debug(f"edit_ephemeral fallback failed: {e}")
        return False


async def delete_ephemeral(client, chat_id: int, user_id: int, message_id: int) -> bool:
    """Delete an ephemeral message (Bot API 10.3 → WzGram Layer 232 → normal delete fallback)."""
    from core import botapi
    if botapi.is_main(client) and await botapi.delete_ephemeral(chat_id, user_id, message_id):
        return True
    if EPHEMERAL_REPLIES and is_wzgram_client(client):
        try:
            return bool(await client.delete_ephemeral_message(chat_id, user_id, message_id))
        except Exception as e:
            log.debug(f"wzgram delete_ephemeral fallback ({e})")
    try:
        await client.delete_messages(chat_id, message_id)
        return True
    except Exception as e:
        log.debug(f"delete_ephemeral fallback failed: {e}")
        return False


async def group_reply(message, text: str, reply_markup=None):
    """Reply to a command. In groups the answer is an *ephemeral* message (Bot API
    10.3 / WzGram Layer 232) that only the caller sees – no chat spam."""
    from pyrogram.enums import ChatType
    u = message.from_user
    client = getattr(message, "_client", None)
    tid = thread_id_of(message)
    if u and message.chat and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        from core import botapi
        if botapi.is_main(client) and await botapi.ephemeral(message.chat.id, u.id, text, reply_markup):
            return True
        if EPHEMERAL_REPLIES and is_wzgram_client(client):
            try:
                kw = dict(
                    parse_mode=HTML,
                    link_preview_options=link_preview_options(is_disabled=True),
                    reply_markup=wzgram_markup(reply_markup),
                    ephemeral_message_parameters=EphemeralMessageParameters(receiver_user_id=u.id),
                )
                if tid:
                    kw["message_thread_id"] = tid
                await client.send_message(message.chat.id, text, **kw)
                return True
            except Exception as e:
                log.debug(f"wzgram ephemeral group reply failed ({e}), falling back")
    if is_wzgram_client(client):
        return await message.reply_text(
            text,
            reply_markup=wzgram_markup(reply_markup),
            parse_mode=HTML,
            link_preview_options=link_preview_options(is_disabled=True),
        )
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

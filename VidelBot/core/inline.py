"""
Inline mode (enable it in @BotFather → /setinline):
  @Videl                → share card for the bot
  @Videl <url>          → short links (is.gd / TinyURL) + QR code
  @Videl <text>         → QR code for the text
"""
import html
import logging
from urllib.parse import quote

import aiohttp
from pyrogram import Client
from pyrogram.types import (InlineKeyboardButton as Btn, InlineKeyboardMarkup, InlineQuery,
                            InlineQueryResultArticle, InlineQueryResultPhoto, InputTextMessageContent)

from config import BOT_NAME

log = logging.getLogger("videl.inline")


async def _shorten(url: str) -> list:
    out = []
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as s:
        for name, api in (("is.gd", "https://is.gd/create.php?format=simple&url="),
                          ("TinyURL", "https://tinyurl.com/api-create.php?url=")):
            try:
                async with s.get(api + quote(url, safe="")) as r:
                    t = (await r.text()).strip()
                    if r.status == 200 and t.startswith("http"):
                        out.append((name, t))
            except Exception:
                pass
    return out


@Client.on_inline_query()
async def inline_handler(client: Client, query: InlineQuery):
    me = await client.get_me()
    q = (query.query or "").strip()
    results = []
    if not q:
        results.append(InlineQueryResultArticle(
            title=f"Share {BOT_NAME}",
            description="Save content · Encode videos · Clone FileStore bots · Tools",
            input_message_content=InputTextMessageContent(
                f"<b>✨ {BOT_NAME}</b> — all-in-one Telegram utility bot\n\n"
                "📥 Save restricted content\n🎬 Encode & compress videos\n⚡ Clone your own FileStore bot\n"
                "🧰 Rename · MediaInfo · Upload · QR · Short links"),
            reply_markup=InlineKeyboardMarkup([[Btn(f"🚀 Start {BOT_NAME}", url=f"https://t.me/{me.username}?start=help")]]),
        ))
    else:
        if q.startswith(("http://", "https://")):
            for name, short in await _shorten(q):
                results.append(InlineQueryResultArticle(
                    title=f"🔗 {name}: {short}", description=q[:100],
                    input_message_content=InputTextMessageContent(short, disable_web_page_preview=True),
                ))
        qr = f"https://api.qrserver.com/v1/create-qr-code/?size=512x512&data={quote(q[:900], safe='')}"
        results.append(InlineQueryResultPhoto(
            photo_url=qr, thumb_url=qr, title="🔳 QR code", description=q[:100],
            caption=f"🔳 <code>{html.escape(q[:900])}</code>",
        ))
    try:
        await query.answer(results, cache_time=5 if q else 300, is_personal=False)
    except Exception as e:
        log.debug(f"inline answer failed: {e}")

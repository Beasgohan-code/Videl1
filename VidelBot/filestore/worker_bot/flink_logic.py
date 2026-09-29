"""
/flink — formatted link generator for clone (worker) FileStore bots.

Clean re-implementation (the upstream file was obfuscated).

Flow (admins only):
  1. /flink
  2. forward / send the FIRST post from the DB (log) channel
  3. forward / send the LAST post
  → the bot replies with a neatly formatted list:
       • one deep link per file, labelled with quality + size
       • links grouped by quality (480p / 720p / 1080p …)
       • one "get all" batch link for the whole range
"""

import asyncio
import html
from collections import OrderedDict

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from filestore.fs_config import LOGGER
from filestore.utils.caption_logic import get_file_details
from filestore.utils.helpers import encode, get_message_id, get_messages, send_main_log

log = LOGGER(__name__)

_CANCEL = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data="flink:cancel")]])
_MAX_RANGE = 200


def setup_flink(app: Client, worker_db, log_channel_id: int, is_admin_func):
    """Bind /flink handlers to a worker client."""

    waiting: dict[int, asyncio.Future] = {}

    async def wait_for(user_id: int, timeout: int = 300):
        old = waiting.get(user_id)
        if old is not None and not old.done():
            old.set_result("CANCEL")            # the same command started again → end the older flow
        fut = asyncio.get_running_loop().create_future()
        waiting[user_id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            if waiting.get(user_id) is fut:    # an older flow timing out must not drop the newer one's slot
                waiting.pop(user_id, None)

    # group=1 → runs before link_gen's catcher (group=2); only active while waiting.
    @app.on_message(filters.private & ~filters.command(["start", "flink"]), group=1)
    async def _flink_input(client: Client, message: Message):
        uid = message.from_user.id if message.from_user else None
        fut = waiting.get(uid)
        if fut and not fut.done():
            fut.set_result(message)
            message.stop_propagation()

    @app.on_callback_query(filters.regex(r"^flink:cancel$"))
    async def _flink_cancel(client: Client, query: CallbackQuery):
        fut = waiting.get(query.from_user.id)
        if fut and not fut.done():
            fut.set_result("CANCEL")
        await query.answer("Cancelled")

    @app.on_message(filters.command("flink") & filters.private)
    async def handle_flink(client: Client, message: Message):
        user_id = message.from_user.id
        if not await is_admin_func(user_id):
            return

        await message.reply(
            "<b>🔗 Fᴏʀᴍᴀᴛᴛᴇᴅ Lɪɴᴋs</b>\n\n"
            "<blockquote>Fᴏʀᴡᴀʀᴅ ᴛʜᴇ <b>FIRST</b> ᴘᴏsᴛ ꜰʀᴏᴍ ʏᴏᴜʀ DB ᴄʜᴀɴɴᴇʟ\n"
            "(ᴏʀ sᴇɴᴅ ɪᴛs ʟɪɴᴋ).</blockquote>",
            reply_markup=_CANCEL,
        )
        first = await wait_for(user_id)
        if first in (None, "CANCEL"):
            return await message.reply("<b><i>🆑 Cᴀɴᴄᴇʟʟᴇᴅ / Tɪᴍᴇᴅ ᴏᴜᴛ.</i></b>")
        first_id = await get_message_id(client, first, log_channel_id)
        if not first_id:
            return await message.reply("<b>❌ Tʜᴀᴛ ᴘᴏsᴛ ɪs ɴᴏᴛ ꜰʀᴏᴍ ᴛʜᴇ DB ᴄʜᴀɴɴᴇʟ.</b>")

        await message.reply(
            "<blockquote>Nᴏᴡ ꜰᴏʀᴡᴀʀᴅ ᴛʜᴇ <b>LAST</b> ᴘᴏsᴛ (ᴏʀ sᴇɴᴅ /skip ꜰᴏʀ ᴀ sɪɴɢʟᴇ ꜰɪʟᴇ).</blockquote>",
            reply_markup=_CANCEL,
        )
        last = await wait_for(user_id)
        if last in (None, "CANCEL"):
            return await message.reply("<b><i>🆑 Cᴀɴᴄᴇʟʟᴇᴅ / Tɪᴍᴇᴅ ᴏᴜᴛ.</i></b>")
        if getattr(last, "text", None) and last.text.strip().lower() == "/skip":
            last_id = first_id
        else:
            last_id = await get_message_id(client, last, log_channel_id)
            if not last_id:
                return await message.reply("<b>❌ Tʜᴀᴛ ᴘᴏsᴛ ɪs ɴᴏᴛ ꜰʀᴏᴍ ᴛʜᴇ DB ᴄʜᴀɴɴᴇʟ.</b>")

        lo, hi = sorted((first_id, last_id))
        if hi - lo + 1 > _MAX_RANGE:
            return await message.reply(f"<b>❌ Rᴀɴɢᴇ ᴛᴏᴏ ʙɪɢ (ᴍᴀx {_MAX_RANGE} ᴘᴏsᴛs).</b>")

        wait = await message.reply("<b>⏳ Bᴜɪʟᴅɪɴɢ ʟɪɴᴋs…</b>")
        me = getattr(client, "me", None) or await client.get_me()
        mult = abs(log_channel_id)

        msgs = await get_messages(client, log_channel_id, list(range(lo, hi + 1)))
        by_quality: "OrderedDict[str, list]" = OrderedDict()
        lines = []
        for m in msgs:
            if not m or m.empty or not m.media:
                continue
            det = get_file_details(m) or {}
            name = (det.get("file_name") or "File").strip()
            quality = det.get("quality") or "Unknown Quality"
            size = det.get("file_size") or ""
            code = await encode(f"get-{m.id * mult}")
            link = f"https://t.me/{me.username}?start={code}"
            by_quality.setdefault(quality, []).append((name, size, link))
            lines.append(
                f"• <a href='{link}'>{html.escape(name[:60])}</a>"
                + (f" — <code>{html.escape(size)}</code>" if size else "")
            )

        if not lines:
            return await wait.edit("<b>❌ Nᴏ ꜰɪʟᴇs ꜰᴏᴜɴᴅ ɪɴ ᴛʜᴀᴛ ʀᴀɴɢᴇ.</b>")

        batch_code = await encode(f"get-{lo * mult}-{hi * mult}")
        batch_link = f"https://t.me/{me.username}?start={batch_code}"

        text = "<b>━━━━━━━━━━━━━━━━━━━━━\n🔗 𝗙𝗢𝗥𝗠𝗔𝗧𝗧𝗘𝗗 𝗟𝗜𝗡𝗞𝗦\n━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        for q, items in by_quality.items():
            text += f"<b>🎞 {html.escape(q)}</b>\n"
            for name, size, link in items:
                text += f"  ➜ <a href='{link}'>{html.escape(name[:55])}</a>"
                text += f" <code>[{html.escape(size)}]</code>\n" if size else "\n"
            text += "\n"
        text += f"<b>📦 Aʟʟ ꜰɪʟᴇs:</b> <a href='{batch_link}'>ɢᴇᴛ ᴀʟʟ</a>"

        # Telegram message limit – fall back to a compact list if needed.
        if len(text) > 4000:
            text = "<b>🔗 Fᴏʀᴍᴀᴛᴛᴇᴅ Lɪɴᴋs</b>\n\n" + "\n".join(lines)
            text = text[:3800] + f"\n\n<b>📦 Aʟʟ:</b> <a href='{batch_link}'>ɢᴇᴛ ᴀʟʟ</a>"

        await wait.delete()
        await message.reply(
            text,
            disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📦 Gᴇᴛ Aʟʟ", url=batch_link)]]),
        )
        await send_main_log(
            client,
            f"<b>🔗 /flink</b>\n<b>• Bot:</b> @{me.username}\n<b>• By:</b> <code>{user_id}</code>\n"
            f"<b>• Files:</b> {len(lines)}",
        )

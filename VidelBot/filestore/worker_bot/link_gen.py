
import asyncio
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from filestore.utils.helpers import encode, get_message_id, send_main_log
from filestore.database.main_db import MainDB
from filestore.fs_config import BACKEND_API_URL, LOGGER, BACKEND_API_SECRET
main_db = MainDB()
log = LOGGER(__name__)

CANCEL_MARKUP = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ᴄᴀɴᴄᴇʟ", callback_data='link_gen:cancel')]])

def setup_link_gen(app: Client, log_channel_id: int, is_admin_func):
    """Binds link generation handlers to the given app instance."""

    _waiting = {}  # user_id -> asyncio.Future

    async def wait_for_input(user_id: int, timeout: int = 300) -> Message | None:
        """Wait for the next message from this user."""
        future = asyncio.get_event_loop().create_future()
        _waiting[user_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            _waiting.pop(user_id, None)


    async def process_link(encoded: str, user_id: int, bot_id: int) -> str:
        bot = await main_db.get_bot(bot_id)
        me = getattr(app, "me", None) or await app.get_me()
        base_link = f"https://t.me/{me.username}?start={encoded}"

        if not bot:
            return base_link

        settings = bot.get("settings", {})
        if settings.get("permanent_link") and BACKEND_API_URL:
            try:
                headers = {"Authorization": f"Bearer {BACKEND_API_SECRET}"}

                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
                    async with session.post(
                        f"{BACKEND_API_URL.rstrip('/')}/api/link",
                        json={
                            "token": encoded,
                            "userId": str(bot["owner_id"])
                        },
                        headers=headers
                    ) as resp:
                        resp_data = await resp.json(content_type=None)
                        if (resp_data or {}).get("success"):
                            # Return the permanent link
                            return f"{BACKEND_API_URL.rstrip('/')}/?url={encoded}"
            except Exception as e:
                log.error(f"Permanent Link API Error: {e}")

        return base_link

    # Handler to catch input when we're waiting for it
    @app.on_message(filters.private & ~filters.command(["start", "genlink", "batch", "custom_batch", "flink"]), group=2)
    async def link_gen_input_catcher(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id in _waiting and not _waiting[user_id].done():
            _waiting[user_id].set_result(message)
            message.stop_propagation()

    @app.on_message(filters.command('genlink') & filters.private)
    async def handle_genlink(client: Client, message: Message):
        if not await is_admin_func(message.from_user.id):
            return

        user_id = message.from_user.id
        await message.reply(
            "<b>Sᴇɴᴅ ᴏʀ Fᴏʀᴡᴀʀᴅ ᴛʜᴇ Mᴇssᴀɢᴇ ᴛᴏ ɢᴇɴᴇʀᴀᴛᴇ ʟɪɴᴋ.</b>",
            reply_markup=CANCEL_MARKUP
        )

        rcv_msg = await wait_for_input(user_id)
        if rcv_msg is None or rcv_msg == "CANCEL":
            return await message.reply("<b><i>🆑 Oᴘᴇʀᴀᴛɪᴏɴ Cᴀɴᴄᴇʟʟᴇᴅ/Tɪᴍᴇᴅ Oᴜᴛ...</i></b>")

        wait_msg = await message.reply("<b>⏳ Pʀᴏᴄᴇssɪɴɢ....</b>")

        msg_id = await get_message_id(client, rcv_msg, log_channel_id)
        if not msg_id:
            return await wait_msg.edit("<b>❌ Fᴀɪʟᴇᴅ ᴛᴏ ɢᴇᴛ ᴍᴇssᴀɢᴇ ID.</b>")

        encoded = await encode(f"get-{msg_id * abs(log_channel_id)}")
        me = getattr(client, "me", None) or await client.get_me()
        link = await process_link(encoded, user_id, me.id)

        await message.reply(
            f"<b>✅ Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ:\n\n<blockquote><code>{link}</code></blockquote></b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Oᴘᴇɴ Lɪɴᴋ", url=link)]])
        )
        await wait_msg.delete()

        # Log to main log channel
        log_msg = (
            f"<b>🔗 Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n"
            f"<b>• Bᴏᴛ:</b> @{me.username}\n"
            f"<b>• Bᴏᴛ ID:</b> <code>{me.id}</code>\n"
            f"<b>• Oᴡɴᴇʀ:</b> <code>{user_id}</code>\n"
            f"<b>• Lᴏɢ Cʜᴀɴɴᴇʟ:</b> <code>{log_channel_id}</code>\n"
            f"<b>• Mᴇᴛʜᴏᴅ:</b> <code>/genlink</code>\n"
            f"<b>• Lɪɴᴋ:</b> {link}"
        )
        await send_main_log(client, log_msg)

    @app.on_message(filters.command('batch') & filters.private)
    async def handle_batch(client: Client, message: Message):
        if not await is_admin_func(message.from_user.id):
            return

        user_id = message.from_user.id
        await message.reply(
            "<b>Fᴏʀᴡᴀʀᴅ ᴛʜᴇ FIRST ᴍᴇssᴀɢᴇ ᴏʀ sᴇɴᴅ ɪᴛ.</b>",
            reply_markup=CANCEL_MARKUP
        )

        first_msg = await wait_for_input(user_id)
        if first_msg is None or first_msg == "CANCEL":
            return await message.reply("<b><i>🆑 Oᴘᴇʀᴀᴛɪᴏɴ Cᴀɴᴄᴇʟʟᴇᴅ/Tɪᴍᴇᴅ Oᴜᴛ...</i></b>")

        first_id = await get_message_id(client, first_msg, log_channel_id)
        if not first_id:
            return await message.reply("<b>❌ Fᴀɪʟᴇᴅ ᴛᴏ ɢᴇᴛ FIRST ᴍᴇssᴀɢᴇ ID.</b>")

        await message.reply(
            "<b>Nᴏᴡ Fᴏʀᴡᴀʀᴅ ᴛʜᴇ LAST ᴍᴇssᴀɢᴇ ᴏʀ sᴇɴᴅ ɪᴛ.</b>",
            reply_markup=CANCEL_MARKUP
        )

        last_msg = await wait_for_input(user_id)
        if last_msg is None or last_msg == "CANCEL":
            return await message.reply("<b><i>🆑 Oᴘᴇʀᴀᴛɪᴏɴ Cᴀɴᴄᴇʟʟᴇᴅ/Tɪᴍᴇᴅ Oᴜᴛ...</i></b>")

        last_id = await get_message_id(client, last_msg, log_channel_id)
        if not last_id:
            return await message.reply("<b>❌ Fᴀɪʟᴇᴅ ᴛᴏ ɢᴇᴛ LAST ᴍᴇssᴀɢᴇ ID.</b>")

        from filestore.worker_bot.engine import MAX_LINK_FILES
        if abs(last_id - first_id) + 1 > MAX_LINK_FILES:
            return await message.reply(f"<b>❌ Tᴏᴏ ᴍᴀɴʏ ᴘᴏsᴛs ({abs(last_id - first_id) + 1}). "
                                       f"Mᴀx {MAX_LINK_FILES} ᴘᴇʀ ʟɪɴᴋ – sᴘʟɪᴛ ɪᴛ ɪɴᴛᴏ sᴍᴀʟʟᴇʀ ʙᴀᴛᴄʜᴇs.</b>")

        # Range link
        encoded = await encode(f"get-{first_id * abs(log_channel_id)}-{last_id * abs(log_channel_id)}")
        me = getattr(client, "me", None) or await client.get_me()
        link = await process_link(encoded, user_id, me.id)

        await message.reply(
            f"<b>✅ Bᴀᴛᴄʜ Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ:\n\n<blockquote><code>{link}</code></blockquote></b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Oᴘᴇɴ Lɪɴᴋ", url=link)]])
        )

        # Log to main log channel
        log_msg = (
            f"<b>🔗 Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n"
            f"<b>• Bᴏᴛ:</b> @{me.username}\n"
            f"<b>• Bᴏᴛ ID:</b> <code>{me.id}</code>\n"
            f"<b>• Oᴡɴᴇʀ:</b> <code>{user_id}</code>\n"
            f"<b>• Lᴏɢ Cʜᴀɴɴᴇʟ:</b> <code>{log_channel_id}</code>\n"
            f"<b>• Mᴇᴛʜᴏᴅ:</b> <code>/batch</code>\n"
            f"<b>• Lɪɴᴋ:</b> {link}"
        )
        await send_main_log(client, log_msg)

    @app.on_message(filters.command('custom_batch') & filters.private)
    async def handle_custom_batch(client: Client, message: Message):
        if not await is_admin_func(message.from_user.id):
            return

        user_id = message.from_user.id
        await message.reply(
            "<b>Sᴇɴᴅ ᴀʟʟ ᴍᴇssᴀɢᴇs/ғɪʟᴇs ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴀᴅᴅ ᴛᴏ ʙᴀᴛᴄʜ ᴏɴᴇ ʙʏ ᴏɴᴇ.\n"
            "Sᴇɴᴅ /done ᴡʜᴇɴ ғɪɴɪsʜᴇᴅ.</b>",
            reply_markup=CANCEL_MARKUP
        )

        from filestore.worker_bot.engine import MAX_LINK_FILES
        ids = []

        while True:
            rcv_msg = await wait_for_input(user_id)
            if rcv_msg is None or rcv_msg == "CANCEL":
                await message.reply("<b><i>🆑 Oᴘᴇʀᴀᴛɪᴏɴ Cᴀɴᴄᴇʟʟᴇᴅ/Tɪᴍᴇᴅ Oᴜᴛ...</i></b>")
                break

            if getattr(rcv_msg, "text", None) and rcv_msg.text.split("@")[0].strip() == "/done":
                if not ids:
                    await message.reply("<b>❌ Nᴏ ᴍᴇssᴀɢᴇs ᴡᴇʀᴇ ᴀᴅᴅᴇᴅ.</b>")
                    break
                ids = await _contiguous(client, ids)
                if not ids:
                    await message.reply("<b>❌ Cᴏᴜʟᴅɴ'ᴛ sᴛᴏʀᴇ ᴛʜᴇ ʙᴀᴛᴄʜ ɪɴ ᴛʜᴇ ᴅʙ ᴄʜᴀɴɴᴇʟ.</b>")
                    break
                first_id, last_id = ids[0], ids[-1]
                encoded = await encode(f"get-{first_id * abs(log_channel_id)}-{last_id * abs(log_channel_id)}")
                me = getattr(client, "me", None) or await client.get_me()
                link = await process_link(encoded, user_id, me.id)
                await message.reply(
                    f"<b>✅ Cᴜsᴛᴏᴍ Bᴀᴛᴄʜ Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ ({len(ids)} ꜰɪʟᴇs):\n\n<blockquote><code>{link}</code></blockquote></b>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Oᴘᴇɴ Lɪɴᴋ", url=link)]])
                )

                # Log to main log channel
                log_msg = (
                    f"<b>🔗 Lɪɴᴋ Gᴇɴᴇʀᴀᴛᴇᴅ</b>\n\n"
                    f"<b>• Bᴏᴛ:</b> @{me.username}\n"
                    f"<b>• Bᴏᴛ ID:</b> <code>{me.id}</code>\n"
                    f"<b>• Oᴡɴᴇʀ:</b> <code>{user_id}</code>\n"
                    f"<b>• Lᴏɢ Cʜᴀɴɴᴇʟ:</b> <code>{log_channel_id}</code>\n"
                    f"<b>• Mᴇᴛʜᴏᴅ:</b> <code>/custom_batch</code>\n"
                    f"<b>• Lɪɴᴋ:</b> {link}"
                )
                await send_main_log(client, log_msg)
                break

            if len(ids) >= MAX_LINK_FILES:
                await rcv_msg.reply(f"<b>⚠️ Mᴀx {MAX_LINK_FILES} ꜰɪʟᴇs – sᴇɴᴅ /done.</b>", quote=True)
                continue
            msg_id = await get_message_id(client, rcv_msg, log_channel_id)
            if msg_id:
                ids.append(msg_id)
            else:
                await rcv_msg.reply("<b>❌ Fᴀɪʟᴇᴅ ᴛᴏ ᴘʀᴏᴄᴇss ᴛʜɪs ᴍᴇssᴀɢᴇ. Sᴋɪᴘᴘɪɴɢ...</b>", quote=True)

    async def _contiguous(client: Client, ids: list) -> list:
        """A share link is a range, so the chosen posts must sit next to each other in the DB channel.
        Posts picked from different places are re-copied (in order) so the link holds exactly them."""
        if ids == list(range(ids[0], ids[0] + len(ids))):
            return ids
        from pyrogram.errors import FloodWait
        fresh = []
        for mid in ids:
            for _ in range(3):
                try:
                    copied = await client.copy_message(log_channel_id, log_channel_id, mid)
                    fresh.append(copied.id)
                    break
                except FloodWait as e:
                    await asyncio.sleep(min(int(e.value) + 1, 60))
                except Exception as e:
                    log.error(f"custom_batch copy of {mid} failed: {e}")
                    break
        if not fresh:
            return []
        # another post may have landed in between – the range still starts/ends on our copies
        return fresh

    @app.on_callback_query(filters.regex(r"^link_gen:cancel$"))
    async def cancel_cb(client: Client, query: CallbackQuery):
        user_id = query.from_user.id
        if user_id in _waiting and not _waiting[user_id].done():
            _waiting[user_id].set_result("CANCEL")
        await query.answer("Cancelled")

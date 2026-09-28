
import asyncio
import time
import html as _html

from config import env_int
from pyrogram import Client, filters, ContinuePropagation
from pyrogram.errors import FloodWait
from pyrogram.handlers import MessageHandler, CallbackQueryHandler
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatJoinRequest,
)
from filestore.fs_config import API_ID, API_HASH, LOGGER, OWNERS
from filestore.database.main_db import MainDB
from filestore.database.worker_db import WorkerDB
from filestore.utils.security import decrypt_token
from filestore.utils.helpers import encode, decode, get_messages, get_message_id, get_exp_time

MAX_LINK_FILES = env_int("MAX_LINK_FILES", 1000)   # files one share link may deliver
log = LOGGER(__name__)
main_db = MainDB()

class WorkerEngine:
    """
    Manages multiple Pyrogram bot clients concurrently.

    Each user-created bot runs as its own Pyrogram Client instance
    with dynamically registered handlers.
    """

    def __init__(self):
        self.workers: dict[int, Client] = {}  # bot_id -> Client
        self._lock = asyncio.Lock()

    async def start_all_workers(self):
        """Start all active bots from the database."""
        bots = await main_db.get_all_active_bots()
        log.info(f"Starting {len(bots)} worker bots...")

        # Start workers in parallel batches for speed
        BATCH_SIZE = 50
        for i in range(0, len(bots), BATCH_SIZE):
            batch = bots[i:i + BATCH_SIZE]
            tasks = []
            for bot_doc in batch:
                tasks.append(self._safe_start_worker(bot_doc))
            await asyncio.gather(*tasks)
            if i + BATCH_SIZE < len(bots):
                log.info(f"  Started {min(i + BATCH_SIZE, len(bots))}/{len(bots)} bots...")

        log.info(f"Worker engine: {len(self.workers)} bots running")

    async def _safe_start_worker(self, bot_doc: dict):
        """Start a worker with error handling (used in parallel batches)."""
        try:
            await self.start_worker(bot_doc)
        except Exception as e:
            bot_id = bot_doc.get("_id", "?")
            log.error(f"Failed to start worker bot {bot_id}: {e}")

    async def start_worker(self, bot_doc: dict):
        """Start a single worker bot."""
        bot_id = bot_doc["_id"]

        async with self._lock:
            if bot_id in self.workers:
                log.warning(f"Worker {bot_id} already running, skipping")
                return

        try:
            token = decrypt_token(bot_doc["bot_token_encrypted"])
        except Exception as e:
            log.error(f"Cannot decrypt token for bot {bot_id}: {e}")
            return

        log_channel_id = bot_doc["log_channel_id"]
        owner_id = bot_doc["owner_id"]
        bot_username = bot_doc.get("bot_username", "unknown")
        worker_db = WorkerDB(bot_id)

        # Increased worker threads to handle concurrent requests without freezing
        app = Client(
            name=f"worker_{bot_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=token,
            in_memory=True,
            workers=4,
        )

        # Global middleware to update last_active
        # (throttle checked here first, so busy clones don't spawn a task per update)
        async def update_activity_middleware(client, update):
            if time.monotonic() - main_db._last_active_written.get(bot_id, -1e9) >= 60:
                from core.bg import spawn
                spawn(main_db.update_last_active(bot_id), name="clone-last-active")
            raise ContinuePropagation

        app.add_handler(MessageHandler(update_activity_middleware), group=-1)
        app.add_handler(CallbackQueryHandler(update_activity_middleware), group=-1)

        from filestore.worker_bot.flink_logic import setup_flink
        from filestore.worker_bot.link_gen import setup_link_gen

        # We need a small wrapper to pass `is_admin` to flink logic properly
        # since it's defined lower down, but we can just define a helper.
        async def flink_is_admin(uid: int) -> bool:
            return uid == owner_id or uid in OWNERS or await worker_db.admin_exist(uid)

        setup_flink(app, worker_db, log_channel_id, flink_is_admin)
        setup_link_gen(app, log_channel_id, flink_is_admin)

        # =====================================================================
        # REGISTER HANDLERS — Each handler is a closure that captures bot_doc
        # =====================================================================

        # ----- Helper: Check if user is admin -----
        async def is_admin(user_id: int) -> bool:
            return user_id == owner_id or user_id in OWNERS or await worker_db.admin_exist(user_id)

        # ----- Helper: Check force subscription -----
        async def check_force_sub(client: Client, user_id: int) -> bool:
            """Check if user has joined all force-sub channels."""
            if user_id == owner_id or user_id in OWNERS:
                return True

            channel_ids = await worker_db.show_channels()
            if not channel_ids:
                return True

            from pyrogram.enums import ChatMemberStatus
            from pyrogram.errors.exceptions.bad_request_400 import UserNotParticipant

            for cid in channel_ids:
                try:
                    member = await client.get_chat_member(cid, user_id)
                    if member.status not in {
                        ChatMemberStatus.OWNER,
                        ChatMemberStatus.ADMINISTRATOR,
                        ChatMemberStatus.MEMBER,
                    }:
                        return False
                except UserNotParticipant:
                    mode = await worker_db.get_channel_mode(cid)
                    if mode == "on":
                        exists = await worker_db.req_user_exist(cid, user_id)
                        if not exists:
                            return False
                    else:
                        return False
                except Exception as e:
                    log.error(f"Force-sub check error for {cid}: {e}")
                    return False
            return True

        # ----- Helper: Build force-sub buttons -----
        async def build_fsub_buttons(client: Client, user_id: int, start_param: str = None):
            """Build the force-subscribe channel buttons."""
            from datetime import datetime, timedelta

            buttons = []
            channel_ids = await worker_db.show_channels()

            for ch_id in channel_ids:
                try:
                    mode = await worker_db.get_channel_mode(ch_id)
                    name = f"📢 Join Channel"
                    link = None

                    try:
                        chat = await client.get_chat(ch_id)
                        name = f"📢 {chat.title or str(ch_id)}"
                        if chat.username and mode != "on":
                            link = f"https://t.me/{chat.username}"
                    except Exception as e:
                        log.warning(f"Could not fetch chat title for {ch_id}: {e}")

                    if not link:
                        try:
                            invite = await client.create_chat_invite_link(
                                chat_id=ch_id,
                                creates_join_request=(mode == "on")
                            )
                            link = invite.invite_link
                        except Exception as e:
                            log.error(f"Cannot create invite link for {ch_id}: {e}")

                    if link:
                        buttons.append([InlineKeyboardButton(name, url=link)])
                except Exception as e:
                    log.error(f"Error building fsub button for {ch_id}: {e}")

            if start_param:
                me = getattr(client, "me", None) or await client.get_me()
                buttons.append([
                    InlineKeyboardButton(
                        "♻️ Reload",
                        url=f"https://t.me/{me.username}?start={start_param}",
                    )
                ])

            return InlineKeyboardMarkup(buttons) if buttons else None

        # =====================================================================
        # SHARED HELPERS: gate() and deliver() – reused by /start, smart links,
        # paid links, search results and Stars purchases (filestore/worker_bot/extras.py)
        # =====================================================================
        from types import SimpleNamespace
        from filestore.database.extras_db import CloneExtras, media_details
        from filestore.worker_bot import extras as clone_extras

        xdb = CloneExtras(bot_id)

        async def fresh_doc() -> dict:
            return await main_db.get_bot(bot_id) or bot_doc

        async def gate(client: Client, message: Message, start_param: str = None, doc: dict = None) -> bool:
            """Ban / maintenance / force-sub checks. Replies and returns False when the user may not continue."""
            user_id = message.from_user.id
            doc = doc or await fresh_doc()

            if await worker_db.ban_user_exist(user_id):
                await message.reply(
                    "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                    "⛔ 𝗔𝗖𝗖𝗘𝗦𝗦 𝗗𝗘𝗡𝗜𝗘𝗗\n"
                    "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                    "<blockquote>ʏᴏᴜ ᴀʀᴇ ʙᴀɴɴᴇᴅ ꜰʀᴏᴍ ᴜsɪɴɢ ᴛʜɪs ʙᴏᴛ.</blockquote>"
                )
                return False

            settings = doc.get("settings", {})
            if settings.get("maintenance_mode") and not await is_admin(user_id):
                await message.reply(settings.get(
                    "maintenance_msg",
                    "<b>🛠 Under Maintenance</b>\n\n"
                    "<blockquote>This bot is temporarily under maintenance.\n"
                    "Please try again later.</blockquote>"
                ))
                return False

            if not await check_force_sub(client, user_id):
                fsub_markup = await build_fsub_buttons(client, user_id, start_param)
                force_pic = settings.get("force_pic", "")
                text = (
                    "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                    "🔒 𝗔𝗖𝗖𝗘𝗦𝗦 𝗥𝗘𝗦𝗧𝗥𝗜𝗖𝗧𝗘𝗗\n"
                    "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                    f"<blockquote>ʜᴇʏ {message.from_user.mention},\n\n"
                    f"ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴛʜᴇ\n"
                    f"ᴄʜᴀɴɴᴇʟs ʙᴇʟᴏᴡ ᴀɴᴅ ᴛᴀᴘ <b>♻️ ʀᴇʟᴏᴀᴅ</b>.</blockquote>"
                )
                if force_pic and force_pic.lower() not in ["none", "ɴᴏɴᴇ", "0"]:
                    await message.reply_photo(photo=force_pic, caption=text, reply_markup=fsub_markup)
                else:
                    await message.reply(text, reply_markup=fsub_markup)
                return False
            return True

        async def shortener_gate(client: Client, message: Message, user_id: int, doc: dict, reload_param: str) -> bool:
            """True → user may receive files now. Admins and clone-premium users skip verification."""
            shortener_cfg = doc.get("shortener", {})
            if not (shortener_cfg.get("enabled") and shortener_cfg.get("domain") and shortener_cfg.get("api_key_encrypted")):
                return True
            expire_secs = shortener_cfg.get("verify_expire", 86400)
            if await is_admin(user_id) or await xdb.is_premium(user_id) or await worker_db.is_verified(user_id, expire_secs):
                return True

            # Build verify URL — shorten the bot's start link so user must visit shortener
            me_ = getattr(client, "me", None) or await client.get_me()
            token = await worker_db.new_verify_token(user_id)
            verify_url = f"https://t.me/{me_.username}?start=verify_{token}"
            from filestore.utils.shortener import shorten_url
            api_key = ""
            try:
                api_key = decrypt_token(shortener_cfg["api_key_encrypted"])
            except Exception:
                # keys saved by older versions were stored in plain text
                api_key = shortener_cfg.get("api_key_encrypted", "")
            shortened = await shorten_url(verify_url, api_key, shortener_cfg.get("domain", ""),
                                          shortener_cfg.get("provider", "adlinkfly"))

            expire_hrs = expire_secs // 3600
            buttons = [[InlineKeyboardButton("🔗 ᴠᴇʀɪꜰʏ", url=shortened)]]
            tut_link = shortener_cfg.get("tutorial_link", "")
            if shortener_cfg.get("tutorial_enabled", False) and tut_link:
                buttons.append([InlineKeyboardButton("📹 ᴛᴜᴛᴏʀɪᴀʟ", url=tut_link)])
            buttons.append([InlineKeyboardButton("✅ ɪ ʜᴀᴠᴇ ᴠᴇʀɪꜰɪᴇᴅ",
                                                 url=f"https://t.me/{me_.username}?start={reload_param}")])
            if (doc.get("settings") or {}).get("premium_stars"):
                buttons.append([InlineKeyboardButton("💎 sᴋɪᴘ ᴡɪᴛʜ ᴘʀᴇᴍɪᴜᴍ",
                                                     url=f"https://t.me/{me_.username}?start=premium")])

            await message.reply(
                "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                "🔗 𝗩𝗘𝗥𝗜𝗙𝗜𝗖𝗔𝗧𝗜𝗢𝗡 𝗥𝗘𝗤𝗨𝗜𝗥𝗘𝗗\n"
                "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                f"<blockquote>ʜᴇʏ {message.from_user.mention},\n\n"
                f"ᴛᴀᴘ <b>🔗 ᴠᴇʀɪꜰʏ</b> ᴀɴᴅ ᴄᴏᴍᴘʟᴇᴛᴇ ᴛʜᴇ\n"
                f"sʜᴏʀᴛ ʟɪɴᴋ ᴛᴏ ᴜɴʟᴏᴄᴋ ꜰɪʟᴇs.\n\n"
                f"ᴀꜰᴛᴇʀ ᴠᴇʀɪꜰʏɪɴɢ, ᴛᴀᴘ\n"
                f"<b>✅ ɪ ʜᴀᴠᴇ ᴠᴇʀɪꜰɪᴇᴅ</b>.\n\n"
                f"◈ ᴠᴀʟɪᴅ ꜰᴏʀ: <b>{expire_hrs}h</b></blockquote>",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return False

        async def deliver(client: Client, message: Message, base64_string: str, *, user_id: int = None,
                          doc: dict = None, skip_shortener: bool = False, reload_param: str = None) -> int:
            """Decode a share-link payload and send its files. Returns how many files were sent."""
            user_id = user_id or message.from_user.id
            reload_param = reload_param or base64_string
            try:
                string = await decode(base64_string)
            except Exception:
                await message.reply("<b>❌ This link is broken or incomplete.</b>\n"
                                    "<i>Ask the sender for a fresh link.</i>")
                return 0
            argument = string.split("-")

            ids = []
            if len(argument) == 3:
                try:
                    start = int(int(argument[1]) / abs(log_channel_id))
                    end = int(int(argument[2]) / abs(log_channel_id))
                    if abs(end - start) + 1 > MAX_LINK_FILES:   # check BEFORE building the list
                        await message.reply(f"<b>❌ This link covers too many files (max {MAX_LINK_FILES}).</b>")
                        return 0
                    ids = list(range(start, end + 1)) if start <= end else list(range(start, end - 1, -1))
                except Exception as e:
                    log.error(f"Error decoding IDs: {e}")
                    return 0
            elif len(argument) == 2:
                try:
                    ids = [int(int(argument[1]) / abs(log_channel_id))]
                except Exception as e:
                    log.error(f"Error decoding ID: {e}")
                    return 0

            if not ids:
                return 0
            if len(ids) > MAX_LINK_FILES:        # crafted link → don't build/serve a giant range
                await message.reply(f"<b>❌ This link covers too many files (max {MAX_LINK_FILES}).</b>")
                return 0

            doc = doc or await fresh_doc()
            if not skip_shortener and not await shortener_gate(client, message, user_id, doc, reload_param):
                return 0

            # Only show loading for large batches (>5 files)
            temp_msg = None
            if len(ids) > 5:
                temp_msg = await message.reply("<b>⏳ ʟᴏᴀᴅɪɴɢ ʏᴏᴜʀ ꜰɪʟᴇs...</b>")
            try:
                messages = await get_messages(client, log_channel_id, ids)
            except Exception as e:
                await message.reply(
                    "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                    "❌ 𝗘𝗥𝗥𝗢𝗥\n"
                    "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                    "<blockquote>sᴏᴍᴇᴛʜɪɴɢ ᴡᴇɴᴛ ᴡʀᴏɴɢ ᴡʜɪʟᴇ ꜰᴇᴛᴄʜɪɴɢ ʏᴏᴜʀ ꜰɪʟᴇs.</blockquote>"
                )
                log.error(f"Error getting messages: {e}")
                return 0
            finally:
                if temp_msg:
                    try:
                        await temp_msg.delete()
                    except Exception:
                        pass

            settings = doc.get("settings", {})
            protect_content = settings.get("protect_content", False)
            custom_caption = settings.get("custom_caption", "")
            file_markup = clone_extras.file_buttons_markup(settings)

            from filestore.utils.caption_logic import get_file_details, format_caption

            sent_msgs = []
            label = ""
            for msg in messages:
                if msg.empty:
                    continue
                if not label:
                    label = (media_details(msg) or {}).get("name", "")

                # Apply custom caption formatting
                original_caption = msg.caption.html if msg.caption else ""
                if custom_caption and getattr(msg, "media", None):
                    formatted_custom = format_caption(custom_caption, get_file_details(msg))
                    caption_text = f"{original_caption}\n\n{formatted_custom}" if original_caption else formatted_custom
                else:
                    caption_text = original_caption

                for attempt in range(3):
                    try:
                        copied = await msg.copy(
                            chat_id=user_id,
                            caption=caption_text if caption_text else None,
                            parse_mode=ParseMode.HTML,
                            protect_content=protect_content,
                            reply_markup=file_markup,
                        )
                        sent_msgs.append(copied)
                        await asyncio.sleep(0)
                        break
                    except FloodWait as e:          # big batches: wait instead of dropping files
                        await asyncio.sleep(min(int(e.value) + 1, 120))
                    except Exception as e:
                        log.error(f"Failed to copy message: {e}")
                        break

            if not sent_msgs:
                await message.reply("<b>❌ These files are no longer available.</b>")
                return 0
            try:
                await xdb.record_delivery(base64_string, len(sent_msgs), label)
            except Exception as e:
                log.warning(f"analytics failed: {e}")

            # Auto-delete
            del_timer = await worker_db.get_del_timer()
            if del_timer > 0:
                notification = await message.reply(
                    f"<b>⏱ These files will be auto-deleted in {get_exp_time(del_timer)}.\n"
                    f"Save or forward them before deletion!</b>"
                )
                me = getattr(client, "me", None) or await client.get_me()      # cached after start()
                reload_url = f"https://t.me/{me.username}?start={reload_param}"
                from core.bg import spawn
                spawn(_schedule_delete(client, sent_msgs, notification, del_timer, reload_url),
                      name="clone-autodelete")
            return len(sent_msgs)

        ctx = SimpleNamespace(
            bot_id=bot_id, owner_id=owner_id, log_channel_id=log_channel_id, worker_db=worker_db, xdb=xdb,
            main_db=main_db, is_admin=is_admin, gate=gate, deliver=deliver, fresh_doc=fresh_doc,
            handle_start=None, tasks=[],
        )
        clone_extras.setup_extras(app, ctx)

        # =====================================================================
        # HANDLER: /start
        # =====================================================================

        @app.on_message(filters.command("start") & filters.private)
        async def worker_start(client: Client, message: Message):
            user_id = message.from_user.id
            current_bot_doc = await fresh_doc()

            # Track user
            if not await worker_db.present_user(user_id):
                await worker_db.add_user(user_id)
                try:
                    await xdb.record_new_user()
                except Exception:
                    pass

                if log_channel_id:
                    log_text = (
                        f"<b>#NewUser</b>\n\n"
                        f"<b>Iᴅ</b> - <code>{user_id}</code>\n"
                        f"<b>Nᴀᴍᴇ</b> - {_html.escape(message.from_user.first_name or '')}\n"
                        f"<b>username</b> - @{message.from_user.username or 'N/A'}"
                    )
                    try:
                        await client.send_message(chat_id=log_channel_id, text=log_text)
                    except Exception as e:
                        log.error(f"Failed to send #NewUser log to {log_channel_id}: {e}")

            # deep-link payload is case-sensitive base64 → take it from the raw text
            parts = (message.text or "").split(None, 1)
            param = parts[1].strip() if len(parts) > 1 else ""

            if not await gate(client, message, param or None, current_bot_doc):
                return

            # Handle /start verify — mark user as verified via shortener
            if param == "verify":
                # old static link (or typed by hand) – never grants access
                await message.reply("<b>⌛ This verification link is invalid or expired.</b>\n"
                                    "<i>Tap your file link again to get a fresh one.</i>")
                return
            if param.startswith("verify_"):
                if not await worker_db.consume_verify_token(user_id, param[7:]):
                    await message.reply("<b>⌛ This verification link is invalid or expired.</b>\n"
                                        "<i>Tap your file link again to get a fresh one.</i>")
                    return
                await message.reply(
                    "<b>━━━━━━━━━━━━━━━━━━━━━\n"
                    "✅ 𝗩𝗘𝗥𝗜𝗙𝗜𝗘𝗗\n"
                    "━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
                    "<blockquote>ʏᴏᴜ ᴀʀᴇ ɴᴏᴡ ᴠᴇʀɪꜰɪᴇᴅ!\n"
                    "ʏᴏᴜ ᴄᴀɴ ɴᴏᴡ ᴀᴄᴄᴇss ꜰɪʟᴇs.\n\n"
                    "ᴛᴀᴘ ʏᴏᴜʀ ᴏʀɪɢɪɴᴀʟ ʟɪɴᴋ ᴀɢᴀɪɴ.</blockquote>"
                )
                return

            # smart links, premium, help … (extras.py)
            if param and ctx.handle_start and await ctx.handle_start(client, message, param, current_bot_doc):
                return

            if param:
                await deliver(client, message, param, doc=current_bot_doc)
                return

            # Normal /start - welcome message
            settings = current_bot_doc.get("settings", {})
            start_pic = settings.get("start_pic", "")
            start_message = settings.get("start_message", "")

            if not start_message:
                start_message = (
                    f"<blockquote>ᴡᴇʟᴄᴏᴍᴇ {message.from_user.mention}!\n\n"
                    f"ɪ ᴄᴀɴ sᴛᴏʀᴇ ꜰɪʟᴇs ᴀɴᴅ sʜᴀʀᴇ ᴛʜᴇᴍ\n"
                    f"ᴠɪᴀ sᴘᴇᴄɪᴀʟ ʟɪɴᴋs.</blockquote>"
                )
            else:
                try:
                    me_ = getattr(client, "me", None) or await client.get_me()
                    start_message = start_message.format(
                        mention=message.from_user.mention,
                        first=message.from_user.first_name,
                        last=message.from_user.last_name or "",
                        id=user_id,
                        bot_mention=f"@{me_.username}",
                        username=message.from_user.username or "",
                    )
                except (KeyError, IndexError, ValueError):
                    # If custom message has unknown placeholders, just send it raw
                    pass

            markup = clone_extras.start_markup(settings)
            if start_pic and start_pic.lower() not in ["none", "ɴᴏɴᴇ", "0"]:
                await message.reply_photo(photo=start_pic, caption=start_message, reply_markup=markup)
            else:
                await message.reply(start_message, reply_markup=markup)

        # =====================================================================
        # HANDLER: Chat join request (request-based force-sub)
        # =====================================================================

        @app.on_chat_join_request()
        async def handle_join_request(client: Client, request: ChatJoinRequest):
            """Track join requests for request-based force-sub."""
            channel_id = request.chat.id
            user_id = request.from_user.id

            channel_ids = await worker_db.show_channels()
            if channel_id in channel_ids:
                mode = await worker_db.get_channel_mode(channel_id)
                if mode == "on":
                    # Just record that they requested to join so the bot grants access.
                    # We DO NOT auto-approve them so the owner can do it manually.
                    await worker_db.req_user(channel_id, user_id)

        # =====================================================================
        # HANDLER: /ban & /unban (admin only)
        # =====================================================================

        @app.on_message(filters.command("ban") & filters.private)
        async def handle_ban(client: Client, message: Message):
            if not await is_admin(message.from_user.id):
                return
            if len(message.command) < 2:
                await message.reply("<b>Usage:</b> <code>/ban [user_id]</code>")
                return
            try:
                target_id = int(message.command[1])
                await worker_db.add_ban_user(target_id)
                await message.reply(f"<b>✅ User {target_id} has been banned from this bot!</b>")
            except ValueError:
                await message.reply("<b>❌ Invalid User ID.</b>")

        @app.on_message(filters.command("unban") & filters.private)
        async def handle_unban(client: Client, message: Message):
            if not await is_admin(message.from_user.id):
                return
            if len(message.command) < 2:
                await message.reply("<b>Usage:</b> <code>/unban [user_id]</code>")
                return
            try:
                target_id = int(message.command[1])
                await worker_db.del_ban_user(target_id)
                await message.reply(f"<b>✅ User {target_id} has been unbanned from this bot!</b>")
            except ValueError:
                await message.reply("<b>❌ Invalid User ID.</b>")

        # =====================================================================
        # NEW: /ping, /id, /users (admin tools)
        # =====================================================================

        @app.on_message(filters.command("ping") & filters.private)
        async def handle_ping(client: Client, message: Message):
            import time
            start = time.perf_counter()
            msg = await message.reply("🏓 <b>Pong!</b>")
            latency = (time.perf_counter() - start) * 1000
            await msg.edit_text(
                f"🏓 <b>Pong!</b>\n\n"
                f"<blockquote>◈ Latency: <code>{latency:.2f} ms</code>\n"
                f"◈ Bot: @{me.username}</blockquote>"
            )

        @app.on_message(filters.command("id") & filters.private)
        async def handle_id(client: Client, message: Message):
            user = message.from_user
            text = (
                f"<b>🆔 Your Info</b>\n\n"
                f"<blockquote>"
                f"◈ <b>User ID:</b> <code>{user.id}</code>\n"
                f"◈ <b>Name:</b> {user.first_name}\n"
                f"◈ <b>Username:</b> @{user.username or 'N/A'}\n"
                f"◈ <b>Bot:</b> @{me.username}"
                f"</blockquote>"
            )
            await message.reply(text)

        @app.on_message(filters.command("users") & filters.private)
        async def handle_users_count(client: Client, message: Message):
            if not await is_admin(message.from_user.id):
                return
            total = await worker_db.total_users()
            banned = await worker_db.get_ban_users()
            admins = await worker_db.get_all_admins()
            await message.reply(
                f"<b>👥 Bot Users</b>\n\n"
                f"<blockquote>"
                f"◈ <b>Total Users:</b> <code>{total}</code>\n"
                f"◈ <b>Banned:</b> <code>{len(banned)}</code>\n"
                f"◈ <b>Admins:</b> <code>{len(admins)}</code>"
                f"</blockquote>"
            )


        # =====================================================================
        # START THE CLIENT
        # =====================================================================

        try:
            await app.start()
            app.set_parse_mode(ParseMode.HTML)

            me = getattr(app, "me", None) or await app.get_me()
            # command menus (users / admins) + scheduled-broadcast loop
            await clone_extras.after_start(app, ctx)
            app._videl_ctx = ctx

            log.info(f"Worker started: @{me.username} (ID: {bot_id})")

            async with self._lock:
                self.workers[bot_id] = app

        except Exception as e:
            log.error(f"Failed to start worker {bot_id}: {e}")
            raise

    async def stop_worker(self, bot_id: int):
        """Stop a single worker bot."""
        async with self._lock:
            app = self.workers.pop(bot_id, None)

        if app:
            for task in getattr(getattr(app, "_videl_ctx", None), "tasks", []):
                task.cancel()
            try:
                # Prevent app.stop() from hanging forever if there's a connection issue
                await asyncio.wait_for(app.stop(block=False), timeout=3.0)
                log.info(f"Worker stopped: {bot_id}")
            except Exception as e:
                log.error(f"Error stopping worker {bot_id}: {e}")

    async def stop_all_workers(self):
        """Stop all running worker bots."""
        async with self._lock:
            bot_ids = list(self.workers.keys())

        for bot_id in bot_ids:
            await self.stop_worker(bot_id)

        log.info("All workers stopped")

    def get_worker(self, bot_id: int) -> Client | None:
        """Get a running worker client by bot_id."""
        return self.workers.get(bot_id)

    @property
    def active_count(self) -> int:
        """Number of currently running workers."""
        return len(self.workers)

# =============================================================================
# AUTO-DELETE HELPER
# =============================================================================
async def _schedule_delete(
    client: Client,
    messages: list,
    notification: Message,
    delay: int,
    reload_url: str | None,
):
    """Schedule auto-deletion of messages after a delay."""
    await asyncio.sleep(delay)

    for msg in messages:
        try:
            await msg.delete()
        except Exception as e:
            log.error(f"Error deleting message {msg.id}: {e}")

    try:
        keyboard = None
        if reload_url:
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Get File Again", url=reload_url)]
            ])

        await notification.edit(
            "<b>🗑 Your files have been auto-deleted.</b>\n\n"
            "<blockquote>Click below to retrieve them again.</blockquote>",
            reply_markup=keyboard,
        )
    except Exception as e:
        log.error(f"Error updating deletion notification: {e}")


# =============================================================================
# GLOBAL WORKER ENGINE SINGLETON
# =============================================================================

worker_engine = WorkerEngine()

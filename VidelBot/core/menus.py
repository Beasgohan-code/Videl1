"""
Videl home: /start, /help, /about, /settings hub, /cancel and all menu callbacks.
Everything here edits text messages (no photo menus) so that every module
(clone-bot dashboard, encoder settings, saver settings) can edit in place.
"""
import logging

from pyrogram import Client, enums, filters
from pyrogram.errors import MessageNotModified
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from config import ADMINS, BOT_NAME, CLONE_ENABLED, FREE_LIMIT_DAILY, MAX_BOTS_PER_USER, START_PIC
from core.ui import contact_row, links_row, rows

log = logging.getLogger("videl.menus")
HTML = enums.ParseMode.HTML


# ════════════════════════════════════════════════════════════════
# Texts
# ════════════════════════════════════════════════════════════════
def _pic_prefix() -> str:
    # Invisible link → Telegram shows START_PIC as a preview above the text.
    return f"<a href='{START_PIC}'>&#8203;</a>" if START_PIC else ""


def start_text(user) -> str:
    return (
        f"{_pic_prefix()}<b>Hey {user.mention}, I'm {BOT_NAME} ✨</b>\n\n"
        "<i>Your all-in-one Telegram utility bot.</i>\n\n"
        "<blockquote>📥 <b>Save</b> posts from restricted channels\n"
        "🎬 <b>Encode</b> & compress videos (x264 / x265)\n"
        "🤖 <b>Clone</b> your own FileStore bot in 1 minute\n"
        "🧰 <b>Tools</b> — mediainfo, rename, upload, QR, short links …</blockquote>\n\n"
        "<b>Tap a button below to get started 👇</b>"
    )


HELP_HOME = (
    "<b>❓ Videl Help</b>\n\n"
    "Pick a module to see how it works.\n"
    "<blockquote>Tip: /cancel stops any running task or setup flow.</blockquote>"
)

HELP_ENC = """<b>🎬 Video Encoder</b>

<blockquote expandable><b>Encode</b>
• Reply <code>/dl</code> to a video / document
• <code>/ddl &lt;url&gt;</code> — encode from a direct link
• <code>/batch &lt;url&gt;</code> — encode a list of links
• <code>/af</code> — reorder / pick audio tracks before encoding

<b>Settings</b>
• /settings → 🎬 Encoder — codec, CRF, preset, resolution, audio, watermark, hard-subs, upload mode …
• <code>/vset</code> — view current settings · <code>/reset</code> — defaults
• <code>/thumb</code> — custom thumbnail for encodes (send a photo with caption /thumb)

<b>Queue</b>
• <code>/queue</code> — see the queue · <code>/status</code> — live system status</blockquote>

<b>Sudo:</b> /clear · /clean · /logs · /vupload · /dupload · /gupload · /speedtest
<b>Owner:</b> /addchat · /rmchat · /addsudo · /rmsudo · /exec · /sh
"""


def help_clone() -> str:
    return f"""<b>🤖 Clone FileStore Bots</b>

<blockquote expandable>Create your <b>own</b> file-sharing bot, hosted by {BOT_NAME}:
1. Make a bot with @BotFather and copy its token
2. Tap <b>🤖 Clone Bots → ➕ Create Bot</b> and send the token
3. Add your new bot as admin in a private channel and send the channel ID

<b>Your bot can:</b>
• Store any file in your channel and give a permanent share link
• <code>/genlink</code> / <code>/batch</code> — single & range links
• <code>/flink</code> — smart links grouped by quality (480p/720p/1080p …)
• Force-subscribe (multiple channels, join-request mode)
• Auto-delete delivered files, protect content
• URL shorteners + verification, custom start text / picture / caption
• Per-bot users, broadcast, bans, maintenance mode, backup / restore</blockquote>

<b>Limit:</b> {MAX_BOTS_PER_USER} bot(s) per user. Manage everything from <b>📋 My Bots</b>.
"""


HELP_TOOLS = """<b>🧰 Tools</b>

<blockquote expandable>/mediainfo — reply to a file: codecs, resolution, bitrate, tracks
/rename &lt;new name&gt; — reply to a file to rename & re-upload it
/upload — reply to a file (≤ 200 MB) to get a public download link
/short &lt;url&gt; — shorten a long link
/qr &lt;text&gt; — make a QR code
/id — chat / user / forwarded IDs
/info — user info (reply / id / username)
/json — raw JSON of a message (reply)
/ping — bot latency</blockquote>
"""

HELP_ADMIN = """<b>👮 Admin</b>

<blockquote expandable><b>Bot</b>
/stats — users, modules & server stats
/broadcast — reply to a message (add <code>-pin</code> to pin)
/ban &lt;id&gt; [reason] · /unban &lt;id&gt; · /banned
/maintenance on|off
/restart · /update (git pull + restart)

<b>Saver</b>
/add_premium &lt;id&gt; &lt;days&gt; · /remove_premium &lt;id&gt;
/set_dump &lt;chat_id&gt;

<b>Clone bots</b>
/clonestats — platform stats · /bots — list all clone bots · /sys — system</blockquote>
"""


def about_text() -> str:
    return (
        f"<b>ℹ️ About {BOT_NAME}</b>\n\n"
        "<blockquote>"
        f"<b>🤖 Name:</b> {BOT_NAME}\n"
        "<b>🧩 Modules:</b> Saver · Encoder · Clone bots · Tools\n"
        "<b>🗄 Database:</b> MongoDB\n"
        "<b>⚙️ Engine:</b> Pyrofork + FFmpeg\n"
        f"<b>🆓 Free plan:</b> {FREE_LIMIT_DAILY} saves / day"
        "</blockquote>"
    )


# ════════════════════════════════════════════════════════════════
# Keyboards
# ════════════════════════════════════════════════════════════════
def home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows(
        [Btn("📥 Save Content", callback_data="help_saver"), Btn("🎬 Encoder", callback_data="help_enc")],
        [Btn("🤖 Clone Bots", callback_data="back_menu"), Btn("🧰 Tools", callback_data="help_tools")],
        [Btn("⚙️ Settings", callback_data="settings_btn"), Btn("💎 Premium", callback_data="buy_premium")],
        [Btn("ℹ️ About", callback_data="about_btn"), Btn("❓ Help", callback_data="help_btn")],
        links_row(),
    ))


def help_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows(
        [Btn("📥 Saver", callback_data="help_saver"), Btn("🎬 Encoder", callback_data="help_enc")],
        [Btn("🤖 Clone Bots", callback_data="help_clone"), Btn("🧰 Tools", callback_data="help_tools")],
        [Btn("👮 Admin", callback_data="help_admin")] if user_id in ADMINS else [],
        [Btn("🏠 Home", callback_data="start_btn")],
    ))


def back_kb(back: str = "help_btn", extra=None) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows(
        extra or [],
        [Btn("⬅️ Back", callback_data=back), Btn("🏠 Home", callback_data="start_btn")],
    ))


def settings_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Btn("📥 Content Saver", callback_data="hub_save")],
        [Btn("🎬 Video Encoder", callback_data="hub_enc")],
        [Btn("🤖 My Clone Bots", callback_data="my_bots")],
        [Btn("🏠 Home", callback_data="start_btn"), Btn("❌ Close", callback_data="close_btn")],
    ])


SETTINGS_HUB = "<b>⚙️ Settings</b>\n\n<i>Which module do you want to configure?</i>"


def clone_hub_text() -> str:
    state = "" if CLONE_ENABLED else "\n\n🚧 <i>New clone creation is temporarily disabled.</i>"
    return (
        "<b>🤖 Clone Bots</b>\n\n"
        "<blockquote>Run your own FileStore bot — powered by this bot, no server needed.\n"
        "Share files with permanent links, force-sub, auto-delete, shorteners and more.</blockquote>"
        f"{state}"
    )


def clone_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Btn("➕ Create Bot", callback_data="create_bot"), Btn("📋 My Bots", callback_data="my_bots")],
        [Btn("📖 How it works", callback_data="help_clone")],
        [Btn("🏠 Home", callback_data="start_btn")],
    ])


# ════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════
async def _edit(query: CallbackQuery, text: str, kb: InlineKeyboardMarkup, preview: bool = False):
    try:
        if query.message.photo or query.message.video or query.message.document:
            # Old media menu → replace with a fresh text message.
            await query.message.delete()
            await query.message.reply_text(text, reply_markup=kb, parse_mode=HTML,
                                           disable_web_page_preview=not preview)
        else:
            await query.message.edit_text(text, reply_markup=kb, parse_mode=HTML,
                                          disable_web_page_preview=not preview)
    except MessageNotModified:
        pass
    except Exception as e:
        log.warning(f"menu edit failed: {e}")


def _clear_flows(user_id: int):
    try:
        from filestore.main_bot.plugins.create_bot import _creation_state
        _creation_state.pop(user_id, None)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
# Commands
# ════════════════════════════════════════════════════════════════
@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    _clear_flows(message.from_user.id)
    await message.reply_text(
        start_text(message.from_user), reply_markup=home_kb(), parse_mode=HTML,
        disable_web_page_preview=not bool(START_PIC),
    )


@Client.on_message(filters.command("start") & filters.group)
async def start_group(client: Client, message: Message):
    await message.reply_text(f"👋 <b>{BOT_NAME}</b> is alive. Use /help in private for all features.")


@Client.on_message(filters.command("help"))
async def help_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    await message.reply_text(HELP_HOME, reply_markup=help_kb(uid), parse_mode=HTML)


@Client.on_message(filters.command("about") & filters.private)
async def about_cmd(client: Client, message: Message):
    await message.reply_text(about_text(), reply_markup=back_kb("start_btn"), parse_mode=HTML)


@Client.on_message(filters.command(["clone", "mybots"]) & filters.private)
async def clone_cmd(client: Client, message: Message):
    await message.reply_text(clone_hub_text(), reply_markup=clone_hub_kb(), parse_mode=HTML)


@Client.on_message(filters.command("settings"))
async def settings_cmd(client: Client, message: Message):
    await message.reply_text(SETTINGS_HUB, reply_markup=settings_hub_kb(), parse_mode=HTML)


@Client.on_message(filters.command(["cancel", "cancellogin"]) & filters.private)
async def cancel_cmd(client: Client, message: Message):
    uid = message.from_user.id
    done = []

    from saver import session
    if uid in session.LOGIN_STATE:
        await session.cancel_login(client, message)
        return

    from filestore.main_bot.plugins.create_bot import _creation_state
    if _creation_state.pop(uid, None) is not None:
        done.append("clone-bot setup")

    from saver.start import batch_temp
    if batch_temp.IS_BATCH.get(uid) is False:
        batch_temp.IS_BATCH[uid] = True
        done.append("save task")

    if done:
        await message.reply_text(f"✅ <b>Cancelled:</b> {', '.join(done)}.")
    else:
        await message.reply_text("ℹ️ <b>Nothing to cancel.</b>\n<i>Encodes can be cancelled from their progress message.</i>")


# ════════════════════════════════════════════════════════════════
# Callbacks
# ════════════════════════════════════════════════════════════════
@Client.on_callback_query(filters.regex(
    r"^(start_btn|help_btn|about_btn|settings_btn|v_settings|buy_premium|back_menu|"
    r"help_saver|help_enc|help_clone|help_tools|help_admin|hub_save|hub_enc)$"
))
async def menu_callbacks(client: Client, query: CallbackQuery):
    data = query.data
    uid = query.from_user.id

    if data == "start_btn":
        _clear_flows(uid)
        await _edit(query, start_text(query.from_user), home_kb(), preview=bool(START_PIC))

    elif data == "help_btn":
        await _edit(query, HELP_HOME, help_kb(uid))

    elif data == "about_btn":
        await _edit(query, about_text(), back_kb("start_btn"))

    elif data in ("settings_btn", "v_settings"):
        await _edit(query, SETTINGS_HUB, settings_hub_kb())

    elif data == "buy_premium":
        from saver.start import premium_text
        kb = InlineKeyboardMarkup(rows(
            contact_row("📸 Send Payment Proof"),
            [Btn("📊 My Plan", callback_data="myplan_back_btn")],
            [Btn("🏠 Home", callback_data="start_btn")],
        ))
        await _edit(query, premium_text(), kb)

    elif data == "back_menu":
        _clear_flows(uid)
        await _edit(query, clone_hub_text(), clone_hub_kb())

    elif data == "help_saver":
        from saver.strings import HELP_TXT
        await _edit(query, HELP_TXT, back_kb(extra=[Btn("📜 All Commands", callback_data="cmd_list_btn"),
                                                   Btn("⚙️ Settings", callback_data="hub_save")]))

    elif data == "help_enc":
        await _edit(query, HELP_ENC, back_kb(extra=[Btn("⚙️ Encoder Settings", callback_data="hub_enc")]))

    elif data == "help_clone":
        await _edit(query, help_clone(), back_kb(extra=[Btn("🤖 Clone Bots", callback_data="back_menu")]))

    elif data == "help_tools":
        await _edit(query, HELP_TOOLS, back_kb())

    elif data == "help_admin":
        if uid not in ADMINS:
            return await query.answer("Admins only.", show_alert=True)
        await _edit(query, HELP_ADMIN, back_kb())

    elif data == "hub_save":
        from database.db import db
        from saver.settings import saver_settings_view
        if not await db.is_user_exist(uid):
            await db.add_user(uid, query.from_user.first_name)
        text, kb = saver_settings_view(uid, await db.check_premium(uid))
        await _edit(query, text, kb)

    elif data == "hub_enc":
        from VideoEncoder.utils.database.access_db import db as enc_db
        from VideoEncoder.utils.helper import check_chat
        from VideoEncoder.utils.settings import OpenSettings
        # check_chat() expects a Message-like object with .chat and .from_user
        query.message.from_user = query.from_user
        if not await check_chat(query.message, chat="Both"):
            return await query.answer("🔒 The encoder is limited to authorised users.", show_alert=True)
        if not await enc_db.is_user_exist(uid):
            await enc_db.add_user(uid)
        await OpenSettings(query.message, user_id=uid)

    try:
        await query.answer()
    except Exception:
        pass

"""
Videl home: /start, /help, /about, /settings, /clone, /cancel and every menu callback.

Look & feel follows the original bots:
  • Home / help / about / premium / settings → Save-Restricted-Content layout
    (random reaction, big picture, START_TXT, the same button grid – minus credits)
  • Clone Bots hub → Son-Goku FileStore START/HELP/ABOUT layout
  • Encoder page   → Video-Encoder start / help layout
Menus are text messages with a large link-preview picture above the text, so
every module (clone dashboard, encoder settings, saver settings) can edit in place.
"""
import logging
import time

from pyrogram import Client, enums, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from config import (ADMINS, BOT_NAME, CLONE_ENABLED, STARS_PLANS, SUBSCRIPTION, SUPPORT_URL,
                    UPDATES_URL)
from core import texts
from core.ui import (contact_row, edit_with_preview, effect, random_start_pic, react, readable_time,
                     rows, send_with_preview, smart_edit)

log = logging.getLogger("videl.menus")
HTML = enums.ParseMode.HTML
BOOT_TIME = time.time()
_me = {}


async def _bot(client: Client):
    if not _me:
        me = await client.get_me()
        _me.update(username=me.username, first_name=me.first_name, id=me.id)
    return _me


# ════════════════════════════════════════════════════════════════
# Texts builders
# ════════════════════════════════════════════════════════════════
async def start_text(client, user) -> str:
    b = await _bot(client)
    return texts.START_TXT.format(mention=user.mention, username=b["username"], first_name=b["first_name"],
                                  uptime=readable_time(time.time() - BOOT_TIME))


async def about_text(client) -> str:
    b = await _bot(client)
    return texts.ABOUT_TXT.format(username=b["username"], first_name=b["first_name"])


def channels_text() -> str:
    lines = []
    if UPDATES_URL:
        lines.append(f"• Updates: {UPDATES_URL.replace('https://', '')}")
    if SUPPORT_URL:
        lines.append(f"• Support: {SUPPORT_URL.replace('https://', '')}")
    return ("📢 Official Channels:\n" + "\n".join(lines) + "\n\nStay updated for new features!") if lines \
        else texts.CHANNELS_EMPTY


# ════════════════════════════════════════════════════════════════
# Keyboards
# ════════════════════════════════════════════════════════════════
def home_kb() -> InlineKeyboardMarkup:
    last = [Btn("🧰 Tools", callback_data="help_tools")]
    if UPDATES_URL or SUPPORT_URL:
        last.append(Btn("📢 Channels", callback_data="channels_info"))
    return InlineKeyboardMarkup([
        [Btn("💎 Buy Premium", callback_data="buy_premium"), Btn("🆘 Help & Guide", callback_data="help_btn")],
        [Btn("⚙️ Settings Panel", callback_data="settings_btn"), Btn("ℹ️ About Bot", callback_data="about_btn")],
        [Btn("⚡ Clone Bot", callback_data="back_menu"), Btn("🎬 Encoder", callback_data="help_enc")],
        last,
    ])


def help_kb(user_id: int, close: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows(
        [Btn("📜 Saver Commands", callback_data="cmd_list_btn"), Btn("🎬 Encoder", callback_data="help_enc")],
        [Btn("⚡ Clone Bots", callback_data="clone_help"), Btn("🧰 Tools", callback_data="help_tools")],
        [Btn("👮 Admin", callback_data="help_admin")] if user_id in ADMINS else [],
        [Btn("❌ Close Menu", callback_data="close_btn")] if close
        else [Btn("⬅️ Back to Home", callback_data="start_btn")],
    ))


def back_home_kb(back: str = None, extra=None) -> InlineKeyboardMarkup:
    last = [Btn("⬅️ Back", callback_data=back)] if back else []
    last.append(Btn("🏠 Home", callback_data="start_btn"))
    return InlineKeyboardMarkup(rows(extra or [], last))


def clone_hub_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [Btn("⚡ Create Bot", callback_data="create_bot"), Btn("📋 My Bots", callback_data="my_bots")],
        [Btn("📖 Help", callback_data="clone_help"), Btn("ℹ️ About", callback_data="clone_about")],
        [Btn("⬅️ Back to Home", callback_data="start_btn")],
    ])


def premium_kb() -> InlineKeyboardMarkup:
    stars = []
    for days, price in STARS_PLANS[:4]:
        label = "Lifetime" if days == 0 else f"{days} days"
        stars.append(Btn(f"⭐ {price} · {label}", callback_data=f"stars_buy:{days}"))
    star_rows = [stars[i:i + 2] for i in range(0, len(stars), 2)]
    return InlineKeyboardMarkup(rows(
        *star_rows,
        contact_row("📸 Send Payment Proof"),
        [Btn("📊 My Plan", callback_data="myplan_back_btn"), Btn("⬅️ Back to Home", callback_data="start_btn")],
    ))


# ════════════════════════════════════════════════════════════════
# Shared renderers (also used by other modules)
# ════════════════════════════════════════════════════════════════
async def render_home(client: Client, query: CallbackQuery):
    await edit_with_preview(client, query.message, await start_text(client, query.from_user), home_kb(),
                            pic=await random_start_pic())


async def render_settings(query_or_msg, user_id: int, edit: bool = True):
    from database.db import db
    from saver.settings import saver_settings_view
    if not await db.is_user_exist(user_id):
        name = getattr(getattr(query_or_msg, "from_user", None), "first_name", "") or ""
        await db.add_user(user_id, name)
    text, kb = saver_settings_view(user_id, await db.check_premium(user_id))
    if edit:
        return await smart_edit(query_or_msg.message, text, kb)
    return await query_or_msg.reply_text(text, reply_markup=kb, parse_mode=HTML)


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
    uid = message.from_user.id
    _clear_flows(uid)
    await react(message)

    # Deep links: t.me/<bot>?start=premium | clone | help | settings
    arg = message.command[1].lower() if len(message.command) > 1 else ""
    if arg in ("premium", "buy", "stars"):
        from saver.start import premium_text
        return await send_with_preview(client, message.chat.id, premium_text(), premium_kb(), pic=SUBSCRIPTION)
    if arg in ("clone", "mybots"):
        return await send_with_preview(client, message.chat.id,
                                       texts.CLONE_START_MSG.format(mention=message.from_user.mention),
                                       clone_hub_kb(), pic=await random_start_pic())
    if arg == "help":
        return await message.reply_text(texts.HELP_TXT, reply_markup=help_kb(uid, close=True), parse_mode=HTML)
    if arg == "settings":
        return await render_settings(message, uid, edit=False)

    await send_with_preview(
        client, message.chat.id, await start_text(client, message.from_user), home_kb(),
        pic=await random_start_pic(), reply_to=message.id, effect_id=effect("fire"),
    )


@Client.on_message(filters.command("start") & filters.group)
async def start_group(client: Client, message: Message):
    b = await _bot(client)
    await message.reply_text(
        f"👋 <b>{BOT_NAME}</b> is alive!",
        reply_markup=InlineKeyboardMarkup([[Btn("🚀 Open in private", url=f"https://t.me/{b['username']}?start=help")]]),
    )


@Client.on_message(filters.command("help"))
async def help_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    await message.reply_text(texts.HELP_TXT, reply_markup=help_kb(uid, close=True), parse_mode=HTML,
                             disable_web_page_preview=True)


@Client.on_message(filters.command("about") & filters.private)
async def about_cmd(client: Client, message: Message):
    await message.reply_text(await about_text(client), reply_markup=back_home_kb(), parse_mode=HTML,
                             disable_web_page_preview=True)


@Client.on_message(filters.command(["clone", "mybots", "create"]) & filters.private)
async def clone_cmd(client: Client, message: Message):
    _clear_flows(message.from_user.id)
    await send_with_preview(client, message.chat.id,
                            texts.CLONE_START_MSG.format(mention=message.from_user.mention),
                            clone_hub_kb(), pic=await random_start_pic())


@Client.on_message(filters.command("settings"))
async def settings_cmd(client: Client, message: Message):
    if not message.from_user:
        return
    await render_settings(message, message.from_user.id, edit=False)


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
        done.append("batch / save task")

    if done:
        await message.reply_text(f"❌ <b>Cancelled Successfully:</b> {', '.join(done)}.")
    else:
        await message.reply_text("ℹ️ <b>Nothing to cancel.</b>\n"
                                 "<i>Encodes can be cancelled from their progress message.</i>")


# ════════════════════════════════════════════════════════════════
# Callbacks
# ════════════════════════════════════════════════════════════════
@Client.on_callback_query(filters.regex(
    r"^(start_btn|help_btn|about_btn|settings_btn|v_settings|hub_save|buy_premium|back_menu|channels_info|"
    r"help_enc|help_tools|help_admin|help_clone|clone_help|clone_about|help_saver|hub_enc)$"
))
async def menu_callbacks(client: Client, query: CallbackQuery):
    data = query.data
    uid = query.from_user.id
    msg = query.message

    if data == "channels_info":
        return await query.answer(channels_text()[:200], show_alert=True)

    if data == "start_btn":
        _clear_flows(uid)
        await render_home(client, query)

    elif data in ("help_btn", "help_saver"):
        await smart_edit(msg, texts.HELP_TXT, help_kb(uid))

    elif data == "about_btn":
        await smart_edit(msg, await about_text(client), back_home_kb())

    elif data in ("settings_btn", "v_settings", "hub_save"):
        await render_settings(query, uid)

    elif data == "buy_premium":
        from saver.start import premium_text
        await edit_with_preview(client, msg, premium_text(), premium_kb(), pic=SUBSCRIPTION)

    elif data == "back_menu":
        _clear_flows(uid)
        await edit_with_preview(client, msg, texts.CLONE_START_MSG.format(mention=query.from_user.mention),
                                clone_hub_kb(), pic=await random_start_pic())
        if not CLONE_ENABLED and uid not in ADMINS:
            await query.answer("🚧 New clone creation is temporarily disabled.", show_alert=True)

    elif data in ("help_clone", "clone_help"):
        await smart_edit(msg, texts.CLONE_HELP_MSG, back_home_kb("back_menu"))

    elif data == "clone_about":
        await smart_edit(msg, texts.CLONE_ABOUT_MSG, back_home_kb("back_menu"))

    elif data == "help_enc":
        text = texts.ENC_START.format(mention=query.from_user.mention) + "\n\n" + texts.ENC_HELP
        await smart_edit(msg, text, back_home_kb("help_btn", extra=[
            Btn("📊 Stats", callback_data="stats"), Btn("⚙️ Settings", callback_data="hub_enc")]))

    elif data == "help_tools":
        b = await _bot(client)
        await smart_edit(msg, texts.TOOLS_HELP.format(username=b["username"]), back_home_kb("help_btn"))

    elif data == "help_admin":
        if uid not in ADMINS:
            return await query.answer("Admins only.", show_alert=True)
        await smart_edit(msg, texts.ADMIN_HELP, back_home_kb("help_btn"))

    elif data == "hub_enc":
        from VideoEncoder.utils.database.access_db import db as enc_db
        from VideoEncoder.utils.helper import check_chat
        from VideoEncoder.utils.settings import OpenSettings

        class _Probe:  # check_chat() needs .chat and .from_user of the *user*
            chat = msg.chat
            from_user = query.from_user

        if not await check_chat(_Probe, chat="Both"):
            return await query.answer("🔒 The encoder is limited to authorised users.", show_alert=True)
        if not await enc_db.is_user_exist(uid):
            await enc_db.add_user(uid)
        await OpenSettings(msg, user_id=uid)

    try:
        await query.answer()
    except Exception:
        pass

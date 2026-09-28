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
import asyncio
import html
import logging
import time

from pyrogram import Client, enums, filters
from pyrogram.types import (CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message,
                            ReplyKeyboardRemove)

from config import (ADMINS, BOT_NAME, CLONE_ENABLED, FREE_LIMIT_DAILY, GIFTS_ENABLED, OWNERS, STARS_PLANS,
                    SUBSCRIPTION, SUBSCRIPTION_STARS, SUPPORT_ENABLED, SUPPORT_URL, TRIAL_DAYS, UPDATES_URL)
from core import rich
from core.rich import Doc, Raw, link
from core import stream
from core import texts
from core.ui import (background, contact_row, edit_with_preview, effect, random_start_pic, react, readable_time,
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
async def premium_until(uid: int):
    """None for free users, "Permanent" or the expiry datetime for Premium ones (never raises)."""
    try:
        from database.db import db
        return await db.check_premium(uid)
    except Exception:
        return None


def plan_label(until) -> str:
    if not until:
        return f"🆓 Free · {FREE_LIMIT_DAILY} files/day"
    if hasattr(until, "strftime"):
        return f"💎 Premium · until {until:%d %b %Y}"
    return "💎 Premium · Lifetime"


async def start_text(client, user, plan: str = None) -> str:
    b = await _bot(client)
    if plan is None:
        plan = plan_label(await premium_until(user.id))
    return texts.START_TXT.format(mention=user.mention, username=b["username"], first_name=b["first_name"],
                                  uptime=readable_time(time.time() - BOOT_TIME), plan=plan)


async def about_text(client) -> str:
    return (await about_doc(client)).classic()


def help_doc():
    """/help as a rich screen: sections with how-to tables, limits comparison, tips in <details>."""
    from config import FREE_LIMIT_DAILY, FREE_LIMIT_SIZE_GB
    doc = Doc("📚", "Help & user guide", "everything this bot can do – tap a button below for details")
    doc.h("📥", "Save restricted content")
    doc.table([
        ("🌐 Public channels", "Send or forward the post link – no login needed"),
        ("🔐 Private channels", Raw("/login once, then send the <code>t.me/c/…</code> link")),
        ("📚 Batch mode", Raw("Send a range: <code>t.me/channel/100-120</code> · /cancel stops")),
    ], header=("Source", "How to use"))
    doc.h("🧩", "Modules")
    doc.table([
        ("🎬 Encoder", "Reply /dl to a video to encode it"),
        ("⚡ Clone bot", "/clone – create your own FileStore bot"),
        ("✏️ Auto-rename", Raw("/autorename <code>{title} S{season}E{episode} [{quality}]</code>, then send files")),
        ("🧰 Tools", "/rename · /mediainfo · /upload · /qr · /short"),
    ], header=("Module", "Start with"))
    doc.h("💎", "Premium & rewards")
    doc.table([
        ("⭐ Buy", "/buy – pay with Telegram Stars · /mysub for auto-renew"),
        ("🎁 Gift", "/gift – gift Premium to a friend"),
        ("🆓 Free Premium", "/trial · /redeem CODE · /refer"),
        ("💬 Support", "/support – talk to the bot owner"),
    ], header=("Action", "Command"))
    doc.h("📊", "Limits")
    doc.table([
        ("📥 Daily saves", f"{FREE_LIMIT_DAILY} / 24 h", "♾️ Unlimited"),
        ("📦 File size", f"{FREE_LIMIT_SIZE_GB:g} GB", "4 GB+"),
        ("🛂 Support", "Standard", "Priority"),
    ], header=("", "Free", "Premium"), align=("left", "center", "center"))
    doc.details("✏️ Auto-rename extras", Doc().items([
        Raw("/setmedia · /metadata · /start_sequence · /leaderboard"),
        Raw("/tutorial – full guide with every placeholder"),
    ]))
    doc.footer("Tip: /commands lists every command you can use.")
    return doc


def tools_doc(username: str):
    doc = Doc("🧰", "Tools", "handy utilities – reply to a file or send text")
    doc.table([
        ("/mediainfo", "Reply to a file: codecs, resolution, bitrate, tracks"),
        ("/rename", "Reply to a file with a new name to re-upload it"),
        ("/upload", "Reply to a file (≤ 200 MB) for a public download link"),
        ("/short", "Shorten a long link"),
        ("/qr", "Make a QR code from any text"),
        ("/id", "Chat / user / forwarded IDs"),
        ("/info", "User info (reply / id / username)"),
        ("/json", "Raw JSON of a message (reply)"),
        ("/ping", "Bot latency"),
        ("/guide", "Illustrated guide with plans & FAQ"),
    ], header=("Command", "What it does"))
    doc.details("💡 Tips", Doc().items([
        "In groups, /help /id /info /guide answer only you (ephemeral messages).",
        Raw(f"Inline: type <code>@{html.escape(username)} text</code> in any chat to share a QR / short link."),
    ]), open_=True)
    return doc


async def about_doc(client):
    b = await _bot(client)
    try:
        import aiogram
        api = aiogram.__api_version__
    except Exception:
        api = "—"
    doc = Doc("ℹ️", f"About {b['first_name']}")
    doc.table([
        ("🤖 Bot", link(f"@{b['username']}", f"https://t.me/{b['username']}")),
        ("🧩 Modules", "Saver · Encoder · Auto-Rename · Clone · Tools"),
        ("📡 Protocol", f"MTProto (Pyrofork) + Bot API {api}"),
        ("🐍 Language", link("Python 3.11", "https://www.python.org/")),
        ("🗄 Database", link("MongoDB", "https://www.mongodb.com/")),
        ("🎞 Engine", link("FFmpeg", "https://ffmpeg.org/")),
        ("⏱ Uptime", readable_time(time.time() - BOOT_TIME)),
    ], header=("Overview", ""))
    doc.details("🔒 Privacy", Doc().items([
        "/logout removes your saved login session at any time.",
        "Temporary downloads are cleaned up automatically after upload.",
        "/cancel stops any running task · /settings controls your preferences.",
    ]))
    return doc


def clone_help_text() -> str:
    import re
    import config
    days = int(getattr(config, "CLONE_INACTIVE_DAYS", 7) or 0)
    if days <= 0:   # auto-off disabled → drop the idle-bots note
        return re.sub(r"\n<blockquote><b>💤.*?</blockquote>\n", "\n", texts.CLONE_HELP_MSG, flags=re.S)
    return texts.CLONE_HELP_MSG.replace("{idle_days}", str(days))


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
    """Features first, then account, then info – same callbacks as the original SRC grid."""
    info = [Btn("ℹ️ About", callback_data="about_btn")]
    if SUPPORT_ENABLED and OWNERS:
        info.append(Btn("💬 Support", callback_data="support_btn"))
    if UPDATES_URL or SUPPORT_URL:
        info.append(Btn("📢 Channels", callback_data="channels_info"))
    return InlineKeyboardMarkup([
        [Btn("⚡ Clone Bot", callback_data="back_menu"), Btn("🎬 Encoder", callback_data="help_enc")],
        [Btn("✏️ Auto-Rename", callback_data="help_rename"), Btn("🧰 Tools", callback_data="help_tools")],
        [Btn("⚙️ Settings", callback_data="settings_btn"), Btn("🆘 Help & Guide", callback_data="help_btn")],
        [Btn("💎 Premium", callback_data="buy_premium"), Btn("🤝 Refer & Earn", callback_data="refer_btn")],
        info,
    ])


def help_kb(user_id: int, close: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(rows(
        [Btn("📜 Saver Commands", callback_data="cmd_list_btn"), Btn("🎬 Encoder", callback_data="help_enc")],
        [Btn("⚡ Clone Bots", callback_data="clone_help"), Btn("🧰 Tools", callback_data="help_tools")],
        [Btn("✏️ Auto-Rename", callback_data="help_rename")],
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


def premium_kb(until=None) -> InlineKeyboardMarkup:
    """*until*: the user's current Premium (see premium_until) → a status chip on top.
    The chip is a Bot API 10.3 disabled button (greyed out, not tappable)."""
    chip = [Btn(f"✅ {plan_label(until)} · active", callback_data="noop:plan")] if until else []
    stars = []
    for days, price in STARS_PLANS[:4]:
        label = "Lifetime" if days == 0 else f"{days} days"
        stars.append(Btn(f"⭐ {price} · {label}", callback_data=f"stars_buy:{days}"))
    star_rows = [stars[i:i + 2] for i in range(0, len(stars), 2)]
    extra = []
    if SUBSCRIPTION_STARS > 0:
        extra.append([Btn(f"🔁 ⭐ {SUBSCRIPTION_STARS} / month · auto-renew", callback_data="stars_sub")])
    gift_trial = []
    if GIFTS_ENABLED and STARS_PLANS:
        gift_trial.append(Btn("🎁 Gift a friend", callback_data="gift_premium"))
    if TRIAL_DAYS > 0:
        gift_trial.append(Btn("🆓 Free trial", callback_data="trial_btn"))
    return InlineKeyboardMarkup(rows(
        chip,
        *star_rows,
        *extra,
        gift_trial,
        contact_row("📸 Send Payment Proof"),
        [Btn("📊 My Plan", callback_data="myplan_back_btn"), Btn("⬅️ Back to Home", callback_data="start_btn")],
    ))


# ════════════════════════════════════════════════════════════════
# Shared renderers (also used by other modules)
# ════════════════════════════════════════════════════════════════
async def render_home(client: Client, query: CallbackQuery):
    text, pic = await asyncio.gather(start_text(client, query.from_user), random_start_pic())
    await edit_with_preview(client, query.message, text, home_kb(), pic=pic)


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
    """Abort any clone-bot wizard. Returns the aborted state (or None)."""
    try:
        from filestore.main_bot.plugins.create_bot import _creation_state
        return _creation_state.pop(user_id, None)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════════
# Commands
# ════════════════════════════════════════════════════════════════
@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    uid = message.from_user.id
    old = _clear_flows(uid)
    if old and old.get("picker"):
        from filestore.main_bot.plugins.create_bot import remove_channel_picker
        await remove_channel_picker(client, uid, old, "⌨️ Setup closed.")
    background(react(message))            # the reaction must never delay the reply

    # Deep links: t.me/<bot>?start=premium | clone | help | guide | settings | refer | gift | sub | trial | support
    #             | rename | tutorial | rnv_<token> (auto-rename verification)
    # (ref_<id> referral links fall through to the normal home screen)
    arg = message.command[1].lower() if len(message.command) > 1 else ""

    # #Start → owner log channel (returning users; new users are logged as #NewUser)
    from core import botlog
    if botlog.should_log_start(uid):
        background(botlog.event("Start", botlog.user_block(
            message.from_user, f"<b>🔗 Deep link:</b> <code>{botlog.esc(arg)}</code>" if arg else ""), client=client))
    if arg in ("premium", "buy", "stars"):
        from saver.start import premium_text
        return await send_with_preview(client, message.chat.id, premium_text(), premium_kb(await premium_until(uid)),
                                       pic=SUBSCRIPTION)
    if arg in ("clone", "mybots"):
        return await send_with_preview(client, message.chat.id,
                                       texts.CLONE_START_MSG.format(mention=message.from_user.mention),
                                       clone_hub_kb(), pic=await random_start_pic())
    if arg == "help":
        return await rich.reply(message, help_doc(), reply_markup=help_kb(uid, close=True))
    if arg == "settings":
        return await render_settings(message, uid, edit=False)
    if arg in ("refer", "invite", "earn"):
        from core.growth import refer_doc
        doc, kb = await refer_doc(client, uid)
        return await rich.reply(message, doc, reply_markup=kb)
    if arg == "gift":
        from core.payments import _ask_gift_target
        return await _ask_gift_target(client, message.chat.id)
    if arg in ("sub", "subscribe"):
        from core.payments import _offer_subscription
        return await _offer_subscription(client, message.chat.id, uid)
    if arg == "trial":
        from core.growth import trial_cmd
        return await trial_cmd(client, message)
    if arg.startswith("rnv_"):
        from renamer.verify import handle_start_token
        return await handle_start_token(client, message, arg[4:])
    if arg in ("rename", "autorename"):
        from renamer.handlers import panel_view
        text, kb = await panel_view(uid)
        return await message.reply_text(text, reply_markup=kb)
    if arg == "tutorial":
        from renamer.handlers import tutorial_cmd
        return await tutorial_cmd(client, message)
    if arg == "guide":
        from core.extras import send_guide
        me = client.me or await client.get_me()
        return await send_guide(client, message.chat.id, me.username)
    if arg == "support":
        from core.support import support_cmd
        message.command = ["support"]
        return await support_cmd(client, message)

    # one live draft (sendMessageDraft) shown *while* the text is built – it hides latency instead of adding it
    greeting = f"<b>👋 Hello {html.escape(message.from_user.first_name or '')},</b>"
    _, text, pic = await asyncio.gather(stream.draft(client, message.chat.id, greeting),
                                        start_text(client, message.from_user), random_start_pic())
    await send_with_preview(client, message.chat.id, text, home_kb(), pic=pic, reply_to=message.id,
                            effect_id=effect("fire"))


@Client.on_message(filters.command("start") & filters.group)
async def start_group(client: Client, message: Message):
    b = await _bot(client)
    from core.ui import group_reply
    await group_reply(
        message, f"👋 <b>{BOT_NAME}</b> is alive!",
        InlineKeyboardMarkup([[Btn("🚀 Open in private", url=f"https://t.me/{b['username']}?start=help")]]),
    )


@Client.on_message(filters.command("help"))
async def help_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if message.chat.id < 0 and uid:
        # groups: help only for the caller (ephemeral) with a deep link to the full menu
        from core.ui import group_reply
        me = client.me or await client.get_me()
        kb = InlineKeyboardMarkup([[Btn("🚀 Open full menu", url=f"https://t.me/{me.username}?start=help")]])
        return await group_reply(message, help_doc().classic(), kb)
    await rich.reply(message, help_doc(), reply_markup=help_kb(uid, close=True))


@Client.on_message(filters.command("about") & filters.private)
async def about_cmd(client: Client, message: Message):
    await rich.reply(message, await about_doc(client), reply_markup=back_home_kb())


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

    from core import support
    if support._pending.pop(uid, None):
        done.append("support message")

    try:
        from renamer.handlers import clear_user as clear_rename
        done.extend(clear_rename(uid))
    except Exception:
        pass

    from saver.start import batch_temp
    if batch_temp.IS_BATCH.get(uid) is False:
        batch_temp.IS_BATCH[uid] = True
        done.append("batch / save task")

    # ReplyKeyboardRemove also hides any picker keyboard (📢 channel / 👤 gift)
    if done:
        await message.reply_text(f"❌ <b>Cancelled Successfully:</b> {', '.join(done)}.",
                                 reply_markup=ReplyKeyboardRemove())
    else:
        await message.reply_text("ℹ️ <b>Nothing to cancel.</b>\n"
                                 "<i>Encodes can be cancelled from their progress message.</i>",
                                 reply_markup=ReplyKeyboardRemove())


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
        await rich.edit(msg, help_doc(), help_kb(uid))

    elif data == "about_btn":
        await rich.edit(msg, await about_doc(client), back_home_kb())

    elif data in ("settings_btn", "v_settings", "hub_save"):
        await render_settings(query, uid)

    elif data == "buy_premium":
        from saver.start import premium_text
        await edit_with_preview(client, msg, premium_text(), premium_kb(await premium_until(uid)), pic=SUBSCRIPTION)

    elif data == "back_menu":
        _clear_flows(uid)
        await edit_with_preview(client, msg, texts.CLONE_START_MSG.format(mention=query.from_user.mention),
                                clone_hub_kb(), pic=await random_start_pic())
        if not CLONE_ENABLED and uid not in ADMINS:
            await query.answer("🚧 New clone creation is temporarily disabled.", show_alert=True)

    elif data in ("help_clone", "clone_help"):
        await smart_edit(msg, clone_help_text(), back_home_kb("back_menu"))

    elif data == "clone_about":
        await smart_edit(msg, texts.CLONE_ABOUT_MSG, back_home_kb("back_menu"))

    elif data == "help_enc":
        text = texts.ENC_START.format(mention=query.from_user.mention) + "\n\n" + texts.ENC_HELP
        await smart_edit(msg, text, back_home_kb("help_btn", extra=[
            Btn("📊 Stats", callback_data="stats"), Btn("⚙️ Settings", callback_data="hub_enc")]))

    elif data == "help_tools":
        await rich.edit(msg, tools_doc((await _bot(client))["username"]), back_home_kb("help_btn"))

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


@Client.on_callback_query(filters.regex(r"^noop"))
async def noop_callback(client: Client, query: CallbackQuery):
    """Status chips (“noop…”) go out as Bot API 10.3 disabled buttons; this only runs for the MTProto fallback."""
    try:
        await query.answer()
    except Exception:
        pass

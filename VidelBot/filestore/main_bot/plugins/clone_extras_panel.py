"""
Main-bot side of the extra clone features: ✨ Extras panel (feature switches), 📈 Analytics,
📖 clone command guide and 👑 ownership transfer. The clone-side code is filestore/worker_bot/extras.py.
"""
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, Message

from filestore.database.main_db import MainDB
from filestore.fs_config import LOGGER, MAX_BOTS_PER_USER, OWNERS
from core.ui import smart_edit
from filestore.worker_bot.extras import TOGGLE_KEYS, analytics_text, setting, settings_panel

log = LOGGER(__name__)
main_db = MainDB()


async def _owned(query: CallbackQuery, bot_id: int):
    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != query.from_user.id:
        await query.answer("❌ Bot not found or access denied!", show_alert=True)
        return None
    return bot


def _panel(bot: dict):
    s = bot.get("settings", {}) or {}
    bot_id = bot["_id"]
    price = f"{s['premium_stars']} ⭐ / {s.get('premium_days', 30)}d" if s.get("premium_stars") else "not for sale"
    text = (
        "<b>━━━━━━━━━━━━━━━━━━━━━\n✨ 𝗘𝗫𝗧𝗥𝗔 𝗙𝗘𝗔𝗧𝗨𝗥𝗘𝗦\n━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
        f"<blockquote>◈ <b>ʙᴏᴛ:</b> @{bot.get('bot_username', 'unknown')}\n"
        f"◈ <b>ᴘʀᴇᴍɪᴜᴍ:</b> {price}\n"
        f"◈ <b>ꜰɪʟᴇ ʙᴜᴛᴛᴏɴs:</b> {sum(len(r) for r in s.get('file_buttons') or [])}\n"
        f"◈ <b>ᴘᴏsᴛ ᴄʜᴀɴɴᴇʟ:</b> {s.get('post_channel') or '—'}\n"
        f"◈ <b>ᴄᴜsᴛᴏᴍ /ʜᴇʟᴘ · /ᴀʙᴏᴜᴛ:</b> {'✅' if s.get('help_text') else '—'} · "
        f"{'✅' if s.get('about_text') else '—'}</blockquote>\n"
        "<i>Tap a switch to turn it on/off. Smart links, premium prices, buttons and broadcasts are set "
        "inside your clone – see 📖 Clone commands.</i>"
    )
    rows = settings_panel(s, f"xtg_{bot_id}_")
    rows += [
        [Btn("📈 ᴀɴᴀʟʏᴛɪᴄs", callback_data=f"xan_{bot_id}"), Btn("📖 ᴄʟᴏɴᴇ ᴄᴏᴍᴍᴀɴᴅs", callback_data=f"xcmd_{bot_id}")],
        [Btn("👑 ᴛʀᴀɴsꜰᴇʀ ᴏᴡɴᴇʀsʜɪᴘ", callback_data=f"xown_{bot_id}")],
        [Btn("🔙 ᴅᴀsʜʙᴏᴀʀᴅ", callback_data=f"dashboard_{bot_id}")],
    ]
    return text, Kb(rows)


@Client.on_callback_query(filters.regex(r"^xtr_(\d+)$"))
async def extras_panel_cb(client: Client, query: CallbackQuery):
    bot = await _owned(query, int(query.matches[0].group(1)))
    if not bot:
        return
    text, kb = _panel(bot)
    await smart_edit(query.message, text, reply_markup=kb)
    await query.answer()


@Client.on_callback_query(filters.regex(r"^xtg_(\d+)_(\w+)$"))
async def extras_toggle_cb(client: Client, query: CallbackQuery):
    bot_id, key = int(query.matches[0].group(1)), query.matches[0].group(2)
    bot = await _owned(query, bot_id)
    if not bot or key not in TOGGLE_KEYS:
        return
    value = not setting(bot.get("settings", {}), key)
    await main_db.update_setting(bot_id, key, value)
    bot.setdefault("settings", {})[key] = value
    text, kb = _panel(bot)
    await smart_edit(query.message, text, reply_markup=kb)
    await query.answer("✅ On" if value else "▫️ Off")


@Client.on_callback_query(filters.regex(r"^xan_(\d+)$"))
async def extras_analytics_cb(client: Client, query: CallbackQuery):
    bot = await _owned(query, int(query.matches[0].group(1)))
    if not bot:
        return
    text = await analytics_text(bot["_id"], bot.get("bot_username", ""))
    await smart_edit(query.message, text, reply_markup=Kb([
        [Btn("🔄 ʀᴇꜰʀᴇsʜ", callback_data=f"xan_{bot['_id']}"), Btn("🔙 ᴇxᴛʀᴀs", callback_data=f"xtr_{bot['_id']}")]]))
    await query.answer()


CLONE_GUIDE = (
    "<b>📖 Commands inside your clone</b> (admins)\n\n"
    "<b>🔗 Links & access</b>\n<blockquote>"
    "/smartlink LINK 24h x100 pass=abc stars=25 note=Name\n"
    "  ↳ expiring · first N users · password · sold for ⭐\n"
    "/links – list / delete smart links\n"
    "/setpremium 50 30 – sell 30-day premium for 50 ⭐ (skips verification)\n"
    "/addpremium ID [days] · /delpremium ID · /premiumusers</blockquote>\n"
    "<b>🗂 Channel & search</b>\n<blockquote>"
    "New storage-channel files are indexed automatically\n"
    "/index – scan older posts · /searchmode on – public /search + inline\n"
    "/autolink dm|edit|post on – auto share links · /setpostchannel ID</blockquote>\n"
    "<b>📊 Owner tools</b>\n<blockquote>"
    "/analytics – opens, growth, top links\n"
    "/broadcast [pin] [silent] [forward] [in 2h | at 21:30] (reply)\n"
    "/schedules – cancel scheduled ones · /export – users CSV + settings</blockquote>\n"
    "<b>🙋 Users</b>\n<blockquote>"
    "/request – users ask for files → /requests inbox with ✅ ❌ 💬\n"
    "/setbuttons Text - url | Text - url – buttons under files\n"
    "/sethelp · /setabout – custom texts · /antiflood · /maintenance · /settings</blockquote>"
)


@Client.on_callback_query(filters.regex(r"^xcmd_(\d+)$"))
async def extras_guide_cb(client: Client, query: CallbackQuery):
    bot = await _owned(query, int(query.matches[0].group(1)))
    if not bot:
        return
    await smart_edit(query.message, CLONE_GUIDE, reply_markup=Kb([[Btn("🔙 ᴇxᴛʀᴀs", callback_data=f"xtr_{bot['_id']}")]]))
    await query.answer()


# ─────────────────────────── 👑 ownership transfer ───────────────────────────
@Client.on_callback_query(filters.regex(r"^xown_(\d+)$"))
async def transfer_owner_cb(client: Client, query: CallbackQuery):
    bot = await _owned(query, int(query.matches[0].group(1)))
    if not bot:
        return
    from filestore.main_bot.plugins.bot_settings import _get_state
    _get_state()[query.from_user.id] = {"step": "awaiting_new_owner", "data": {"bot_id": bot["_id"]}}
    await smart_edit(
        query.message,
        "<b>👑 Transfer ownership</b>\n\n<blockquote>"
        f"Send the <b>user ID</b> of the new owner of @{bot.get('bot_username', 'unknown')}.\n\n"
        "• they must have started Videl once\n• they get full control, you lose access\n"
        "• users, files, links and settings stay as they are</blockquote>\n"
        "<i>They can see their ID with /id.</i>",
        reply_markup=Kb([[Btn("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"xtr_{bot['_id']}")]]))
    await query.answer()


async def handle_new_owner_input(client: Client, message: Message, state: dict):
    """Called from the clone-settings state machine (create_bot.handle_creation_input)."""
    from filestore.main_bot.plugins.bot_settings import _get_state
    bot_id = state["data"]["bot_id"]
    text = (message.text or "").strip()
    target = message.forward_from.id if getattr(message, "forward_from", None) else (
        int(text) if text.isdigit() else None)
    back = Kb([[Btn("🔙 ᴇxᴛʀᴀs", callback_data=f"xtr_{bot_id}")]])
    if target is None:
        return await message.reply("<b>❌ Send a numeric user ID.</b>", reply_markup=back)
    if target == message.from_user.id:
        return await message.reply("<b>❌ You already own this bot.</b>", reply_markup=back)
    from database.db import db as saver_db
    if not await saver_db.is_user_exist(target):
        return await message.reply("<b>❌ That user hasn't started Videl yet.</b> Ask them to send /start first.",
                                   reply_markup=back)
    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != message.from_user.id:
        _get_state().pop(message.from_user.id, None)
        return await message.reply("<b>❌ Access denied.</b>")
    _get_state().pop(message.from_user.id, None)
    await message.reply(
        f"<b>👑 Transfer @{bot.get('bot_username', 'unknown')} to</b> <code>{target}</code>?\n\n"
        "<i>This can't be undone by you – only the new owner can transfer it back.</i>",
        reply_markup=Kb([[Btn("✅ ʏᴇs, ᴛʀᴀɴsꜰᴇʀ", callback_data=f"xownok_{bot_id}_{target}"),
                          Btn("❌ ᴄᴀɴᴄᴇʟ", callback_data=f"xtr_{bot_id}")]]))


@Client.on_callback_query(filters.regex(r"^xownok_(\d+)_(\d+)$"))
async def transfer_owner_confirm_cb(client: Client, query: CallbackQuery):
    bot_id, target = int(query.matches[0].group(1)), int(query.matches[0].group(2))
    bot = await _owned(query, bot_id)
    if not bot:
        return
    if target not in OWNERS and await main_db.count_user_bots(target) >= MAX_BOTS_PER_USER:
        return await query.answer(f"❌ That user already has {MAX_BOTS_PER_USER} bot(s) – the maximum.",
                                  show_alert=True)
    await main_db.transfer_owner(bot_id, target)
    bot["owner_id"] = target

    # the running clone captured the old owner → restart it with the new document
    try:
        from filestore.worker_bot.engine import worker_engine
        if worker_engine.get_worker(bot_id):
            await worker_engine.stop_worker(bot_id)
            await worker_engine.start_worker(await main_db.get_bot(bot_id))
    except Exception as e:
        log.warning(f"restart after ownership transfer failed: {e}")

    try:
        from filestore.main_bot.plugins.my_bots import _clone_log
        await _clone_log(client, "CloneTransferred", query.from_user, bot, f"<b>👑 New owner:</b> <code>{target}</code>")
    except Exception:
        pass
    try:
        await client.send_message(target, f"<b>👑 You now own @{bot.get('bot_username', 'unknown')}!</b>\n"
                                          "Manage it from 🤖 My Bots.")
    except Exception:
        pass
    await smart_edit(query.message, f"<b>✅ @{bot.get('bot_username', 'unknown')} now belongs to</b> <code>{target}</code>.",
                                  reply_markup=Kb([[Btn("🔙 ᴍʏ ʙᴏᴛs", callback_data="my_bots")]]))
    await query.answer("Transferred")

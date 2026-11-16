"""Owner-only profile selector for a stopped clone; no data migration."""
import config
from pyrogram import Client, enums, filters
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

from core.ui import smart_edit
from filestore.database.main_db import MainDB
from filestore.worker_bot.engine import worker_engine

main_db = MainDB()
LABELS = {"filestore": "File Store", "group": "Group Management"}


async def _owned(query, bot_id):
    user = getattr(query, "from_user", None)
    chat = getattr(getattr(query, "message", None), "chat", None)
    if (not user or getattr(user, "is_bot", False) or not chat
            or getattr(chat, "type", None) != enums.ChatType.PRIVATE
            or getattr(chat, "id", None) != getattr(user, "id", None)):
        await query.answer("❌ Bot not found or access denied!", show_alert=True)
        return None
    bot = await main_db.get_bot(bot_id)
    if not bot or bot.get("owner_id") != user.id or bot.get("is_deleted"):
        await query.answer("❌ Bot not found or access denied!", show_alert=True)
        return None
    return bot


@Client.on_callback_query(filters.regex(r"^clprofile_(\d+)$"))
async def profile_panel(client, query):
    bot_id = int(query.matches[0].group(1))
    bot = await _owned(query, bot_id)
    if not bot:
        return
    mode = LABELS.get(bot.get("clone_profile", "filestore"), "Unknown / unsupported")
    ready = config.CLONE_PROFILES_SINGLE_INSTANCE
    text = (f"<b>🧩 Clone profile</b>\n\nSelected: <b>{mode}</b>\n\n"
            "Choose one profile for this clone. Stop the bot before switching, "
            "then start it again from the dashboard. File Store data, links and settings are retained.\n\n"
            "<b>File Store</b> serves files and links.\n"
            "<b>Group Management</b> offers /ban, /mute, /kick, /pin and more; "
            "see /help in the group. Telegram admin rights are required.\n"
            "<b>Music</b> is planned; voice-chat playback is not implemented.\n\n"
            + ("<i>Experimental: single current worker instance only.</i>" if ready else
               "<i>Switching is disabled until the operator verifies a single current worker instance.</i>"))
    buttons = Kb([
        [Btn("📁 File Store", callback_data=f"clprofile_set_{bot_id}_filestore"),
         Btn("🛡 Group Management", callback_data=f"clprofile_set_{bot_id}_group")],
        [Btn("🎵 Music · not available", callback_data=f"clprofile_set_{bot_id}_music")],
        [Btn("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")],
    ])
    await smart_edit(query.message, text, buttons)
    await query.answer()


@Client.on_callback_query(filters.regex(r"^clprofile_set_(\d+)_(\w+)$"))
async def profile_select(client, query):
    bot_id = int(query.matches[0].group(1))
    bot = await _owned(query, bot_id)
    if not bot:
        return
    mode = query.matches[0].group(2)
    if mode not in LABELS:
        return await query.answer("Music voice-chat playback is not available yet.", show_alert=True)
    try:
        result = await worker_engine.select_profile(bot_id, query.from_user.id, mode)
    except Exception:
        return await query.answer("Could not confirm the profile change. Reopen the panel before retrying.", show_alert=True)
    notices = {
        "saved": "Profile saved. Start the bot from the dashboard.",
        "unchanged": "This profile is already selected.",
        "stop_first": "Stop the bot first. If its stop could not be confirmed, ask the operator for help.",
        "disabled": "Switching is disabled until the operator verifies a single current worker instance.",
        "denied": "Bot not found or access denied!",
        "changed": "The bot changed. Reopen the panel and review it before trying again.",
        "unsupported": "This profile is unsupported. Ask the operator for help.",
        "unavailable": "This profile is not available.",
    }
    await query.answer(notices.get(result, "Could not confirm the profile change."), show_alert=True)
    if result in ("saved", "unchanged"):
        await smart_edit(query.message,
                         f"<b>🧩 Selected: {LABELS[mode]}</b>\n\n"
                         "Start the bot from its dashboard. Existing File Store data was not removed.",
                         Kb([[Btn("🔙 Dashboard", callback_data=f"dashboard_{bot_id}")]]))

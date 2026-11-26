"""Owner settings panel for a clone running in Group Management mode.

Reads and toggles the group worker's own per-chat settings
(`group_worker_settings`, scoped by clone bot + chat) from the main
bot's clone dashboard. Only the clone owner can open or change them.
"""
import html

from pyrogram import Client, enums, filters
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb

from core.ui import smart_edit
from filestore.database.main_db import MainDB
from filestore.database.mongo import get_db
from group_worker.store import GroupWorkerStore

main_db = MainDB()
_TOGGLE_KEYS = ("greetings", "shield", "dm_alerts", "captcha_on", "night_on")


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


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
    if bot.get("clone_profile", "filestore") != "group":
        await query.answer("This clone is not in Group Management mode.", show_alert=True)
        return None
    return bot


def _store() -> GroupWorkerStore:
    return GroupWorkerStore(get_db())


async def _chats(bot_id: int) -> list[dict]:
    cursor = _store()._settings.find({"bot_id": int(bot_id)}).sort("chat_id", 1)
    return [doc async for doc in cursor]


def format_chat_line(chat_id: int, settings: dict) -> str:
    flags = []
    if settings.get("shield"):
        flags.append("🛡 shield")
    if settings.get("greetings"):
        flags.append("👋 greetings")
    if settings.get("captcha_on"):
        flags.append("🤖 captcha")
    if settings.get("night_on"):
        flags.append("🌙 night")
    if settings.get("dm_alerts"):
        flags.append("📩 DM alerts")
    kinds = ", ".join(settings.get("guard_kinds") or [])
    return (f"• <code>{chat_id}</code> — {', '.join(flags) if flags else 'no features on'}"
            + (f" · guarding: {_esc(kinds)}" if kinds else "")
            + f" · 🌐 {settings.get('lang', 'en')}")


def settings_panel_text(bot_label: str, chats: list[dict]) -> str:
    lines = [f"<b>🛡 Group Management settings</b>\n<i>{_esc(bot_label)}</i>\n"]
    if not chats:
        lines.append(
            "No groups have configured this clone yet.\n"
            "Add the clone to a group, make it admin, and open /settings there — "
            "its groups will appear here for quick toggles.")
    else:
        lines.append(f"<b>{len(chats)} configured group(s):</b>")
        lines.extend(format_chat_line(int(doc["chat_id"]), doc) for doc in chats[:10])
        lines.append("\nTap a switch to toggle it for that group.")
    return "\n".join(lines)


def settings_panel_buttons(bot_id: int, chats: list[dict]) -> Kb:
    rows = []
    for doc in chats[:10]:
        chat_id = int(doc["chat_id"])
        for key in _TOGGLE_KEYS:
            on = bool(doc.get(key))
            rows.append([Btn(
                f"{'✅' if on else '▫️'} {chat_id} · {key.replace('_', ' ')}",
                callback_data=f"clgset_t_{bot_id}_{chat_id}_{key}")])
    rows.append([Btn("🔄 Refresh", callback_data=f"clgset_{bot_id}")])
    rows.append([Btn("🔙 Clone profile", callback_data=f"clprofile_{bot_id}")])
    return Kb(rows)


@Client.on_callback_query(filters.regex(r"^clgset_(\d+)$"))
async def group_settings_panel(client, query):
    bot_id = int(query.matches[0].group(1))
    bot = await _owned(query, bot_id)
    if not bot:
        return
    chats = await _chats(bot_id)
    label = f"@{bot.get('bot_username') or bot_id}"
    await smart_edit(query.message, settings_panel_text(label, chats),
                     settings_panel_buttons(bot_id, chats))
    await query.answer()


@Client.on_callback_query(filters.regex(r"^clgset_t_(\d+)_(-?\d+)_(\w+)$"))
async def group_settings_toggle(client, query):
    bot_id = int(query.matches[0].group(1))
    chat_id = int(query.matches[0].group(2))
    key = query.matches[0].group(3)
    bot = await _owned(query, bot_id)
    if not bot:
        return
    if key not in _TOGGLE_KEYS:
        return await query.answer("Unknown setting.", show_alert=True)
    store = _store()
    current = await store.get_settings(bot_id, chat_id)
    await store.update_settings(bot_id, chat_id, {key: not current.get(key)})
    chats = await _chats(bot_id)
    label = f"@{bot.get('bot_username') or bot_id}"
    await smart_edit(query.message, settings_panel_text(label, chats),
                     settings_panel_buttons(bot_id, chats))
    await query.answer("Saved ✅")

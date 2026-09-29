"""
Dynamic bot admins (from the Auto-Rename admin panel: /add_admin · /deladmin · /admins).

Admins from the ADMINS / SUDO_USERS / OWNER_ID env vars are permanent. Owners can add
more at runtime; they are stored in Mongo, loaded at boot (before the plugins build
their filters) and applied live to every `filters.user(ADMINS)` filter.
Owner-only commands (/exec, /sh, /update …) are never affected.
"""
import logging
import time

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import config
from core import ADMIN_FILTERS
from core.db import vdb

log = logging.getLogger("videl.admins")

ENV_ADMINS = frozenset(config.ADMINS)   # snapshot before any DB admin is merged
owner_only = filters.user(config.OWNERS)


def _col():
    return vdb.db["videl_admins"]


def _apply(uid: int, add: bool):
    if add:
        if uid not in config.ADMINS:
            config.ADMINS.append(uid)       # in place → every `uid in ADMINS` check sees it
        for f in ADMIN_FILTERS:
            f.add(uid)
    else:
        if uid in ENV_ADMINS:
            return
        while uid in config.ADMINS:
            config.ADMINS.remove(uid)
        for f in ADMIN_FILTERS:
            f.discard(uid)


async def load_db_admins() -> list:
    """Merge DB admins into config.ADMINS (call once at boot, before plugins load)."""
    added = []
    try:
        async for d in _col().find({}, {"_id": 1}):
            uid = int(d["_id"])
            if uid not in config.ADMINS:
                added.append(uid)
            _apply(uid, True)
    except Exception as e:
        log.warning(f"loading DB admins failed: {e}")
    if added:
        log.info(f"👮 loaded {len(added)} admin(s) from the database")
    return added


async def db_admins() -> list:
    return [d async for d in _col().find({}).sort("ts", 1)]


async def add_admin(uid: int, by: int = 0, name: str = "") -> bool:
    """Returns False if the user already is an admin."""
    if uid in config.ADMINS:
        return False
    await _col().update_one({"_id": uid}, {"$set": {"by": by, "name": name, "ts": time.time()}}, upsert=True)
    _apply(uid, True)
    return True


async def remove_admin(uid: int) -> str:
    """'env' → permanent env admin, 'no' → not an admin, 'ok' → removed."""
    if uid in ENV_ADMINS:
        return "env"
    res = await _col().delete_one({"_id": uid})
    if not res.deleted_count and uid not in config.ADMINS:
        return "no"
    _apply(uid, False)
    return "ok"


async def _sync_menu(client, uid: int, added: bool):
    """Give / take the admin command menu for that user."""
    from pyrogram.types import BotCommandScopeChat
    from core import commands
    try:
        if added:
            await client.set_bot_commands(commands._cmds(commands.USER_COMMANDS, commands.ADMIN_COMMANDS),
                                          scope=BotCommandScopeChat(uid))
        else:
            await client.delete_bot_commands(scope=BotCommandScopeChat(uid))
    except Exception as e:
        log.debug(f"admin menu sync for {uid} skipped: {e}")


def _target(message: Message):
    r = message.reply_to_message
    if r and r.from_user:
        return r.from_user.id, r.from_user.first_name or ""
    if len(message.command) > 1 and message.command[1].lstrip("-").isdigit():
        return int(message.command[1]), ""
    return None, ""


# ─────────────────────────── commands ───────────────────────────
@Client.on_message(filters.command(["add_admin", "addadmin"]) & owner_only)
async def add_admin_cmd(client: Client, message: Message):
    uid, name = _target(message)
    if not uid:
        return await message.reply_text("<b>Usage:</b> <code>/add_admin user_id</code> (or reply to the user)\n"
                                        "<i>Admins can ban, broadcast, manage premium, force-sub, verification …</i>")
    if not name:
        try:
            name = (await client.get_users(uid)).first_name or ""
        except Exception:
            name = ""
    if not await add_admin(uid, message.from_user.id, name):
        return await message.reply_text("ℹ️ That user is already an admin.")
    await _sync_menu(client, uid, True)
    from core import botlog
    await botlog.event("AdminAdded", f"<b>👮 Admin:</b> {botlog.esc(name)} (<code>{uid}</code>)\n"
                                     f"<b>➕ By:</b> <code>{message.from_user.id}</code>", client=client)
    await message.reply_text(f"✅ <b>{botlog.esc(name) or uid}</b> (<code>{uid}</code>) is now a bot admin.")
    try:
        await client.send_message(uid, f"👮 You were promoted to <b>admin</b> of {config.BOT_NAME}.\n"
                                       "Open the command menu to see your new commands.")
    except Exception:
        pass


@Client.on_message(filters.command(["deladmin", "del_admin", "rmadmin"]) & owner_only)
async def del_admin_cmd(client: Client, message: Message):
    uid, _ = _target(message)
    if not uid:
        return await message.reply_text("<b>Usage:</b> <code>/deladmin user_id</code>")
    await message.reply_text(await _remove_text(client, uid, message.from_user.id))


async def _remove_text(client, uid: int, by: int) -> str:
    res = await remove_admin(uid)
    if res == "env":
        return "ℹ️ That admin comes from the ADMINS / SUDO_USERS / OWNER_ID env vars – remove it there."
    if res == "no":
        return "❌ That user is not an admin."
    await _sync_menu(client, uid, False)
    from core import botlog
    await botlog.event("AdminRemoved", f"<b>👮 Admin:</b> <code>{uid}</code>\n<b>➖ By:</b> <code>{by}</code>",
                       client=client)
    return f"✅ Removed <code>{uid}</code> from the admins."


async def _admins_view(viewer: int):
    owners = set(config.OWNERS)
    lines = ["<b>👮 Bot admins</b>\n"]
    for uid in config.OWNERS:
        lines.append(f"👑 <code>{uid}</code> — owner")
    for uid in ENV_ADMINS - owners:
        lines.append(f"🔒 <code>{uid}</code> — env")
    kb = []
    for d in await db_admins():
        uid = int(d["_id"])
        name = d.get("name") or ""
        lines.append(f"➕ <code>{uid}</code> {name[:30]}".rstrip())
        if viewer in owners:
            kb.append([Btn(f"🗑 Remove {name[:20] or uid}", callback_data=f"vadm:rm:{uid}")])
    lines.append("\n<i>Owners: /add_admin id · /deladmin id</i>")
    kb.append([Btn("❌ Close", callback_data="close_btn")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


@Client.on_message(filters.command(["admins", "adminlist"]) & filters.user(config.ADMINS))
async def admins_cmd(client: Client, message: Message):
    text, kb = await _admins_view(message.from_user.id)
    await message.reply_text(text, reply_markup=kb)


@Client.on_callback_query(filters.regex(r"^vadm:rm:(-?\d+)$"))
async def admins_rm_cb(client: Client, query: CallbackQuery):
    if query.from_user.id not in config.OWNERS:
        return await query.answer("👑 Owners only.", show_alert=True)
    uid = int(query.matches[0].group(1))
    await query.answer((await _remove_text(client, uid, query.from_user.id))[:190], show_alert=True)
    text, kb = await _admins_view(query.from_user.id)
    from core.ui import smart_edit
    await smart_edit(query.message, text, kb)

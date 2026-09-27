"""
Admin user panel.

  /user <id | @username>   (or reply)   full profile across every module +
                                        action buttons (ban/unban, ±premium, refresh)
  /msg <id> <text>         (or reply)   message a user from the bot
  /export                               users as CSV (id, name, username, joined,
                                        premium, expiry, banned, blocked, referrals)
"""
import csv
import io
import logging
import re
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from config import ADMINS
from core.db import vdb

log = logging.getLogger("videl.userpanel")
admin_filter = filters.user(ADMINS)


def _fmt_date(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M")
    return str(value) if value else "—"


async def profile(client, uid: int) -> tuple[str, InlineKeyboardMarkup]:
    from core import botlog
    esc = botlog.esc
    vu = await vdb.get_user(uid) or {}
    try:
        from database.db import db as saver_db
        su = await saver_db.col.find_one({"id": uid}) or {}
    except Exception:
        su = {}
    try:
        from filestore.database.main_db import MainDB
        bots = await MainDB().get_user_bots(uid)
    except Exception:
        bots = []
    pays = await vdb.db["payments"].find({"user": uid, "refunded": False}).to_list(1000)
    sub = await vdb.db["subscriptions"].find_one({"user": uid, "active": True})

    name = esc(vu.get("name") or su.get("name") or "")
    uname = f"@{vu['username']}" if vu.get("username") else "—"
    try:
        tg = await client.get_users(uid)
        name = esc(" ".join(x for x in (tg.first_name, tg.last_name) if x)) or name
        uname = f"@{tg.username}" if tg.username else uname
        extra_tg = (f"\n<b>⭐ Telegram Premium:</b> {'yes' if tg.is_premium else 'no'}"
                    + (f"\n<b>🌐 DC:</b> {tg.dc_id}" if tg.dc_id else ""))
    except Exception:
        extra_tg = ""

    premium = su.get("is_premium")
    until = ("♾️ Lifetime" if not su.get("premium_expiry") else su["premium_expiry"]) if premium else "—"
    banned = vdb.is_banned(uid)
    stars = sum(p.get("stars", 0) for p in pays)
    bot_list = ", ".join(f"@{b.get('bot_username', '?')}" for b in bots[:5]) or "—"
    sub_txt = ("❌ canceled" if sub.get("canceled") else f"✅ active · {sub.get('renewals', 0)} renewal(s)") if sub else "—"
    text = (
        f"<b>👤 User profile</b>\n\n<blockquote>"
        f"<b>Name:</b> <a href=\"tg://user?id={uid}\">{name or uid}</a>\n"
        f"<b>🆔 ID:</b> <code>{uid}</code>\n<b>🔗 Username:</b> {uname}{extra_tg}\n"
        f"<b>📅 Joined:</b> {_fmt_date(vu.get('joined'))}\n"
        f"<b>🚦 Status:</b> {'🚫 banned' if banned else '✅ ok'}{' · 📵 blocked the bot' if vu.get('blocked') else ''}"
        f"</blockquote>\n<b>💎 Premium &amp; payments</b>\n<blockquote>"
        f"<b>Premium:</b> {'✅' if premium else '❌'} · <b>until:</b> {until}\n"
        f"<b>🔁 Subscription:</b> {sub_txt}\n"
        f"<b>⭐ Paid:</b> {len(pays)} order(s) · {stars} Stars\n"
        f"<b>🆓 Trial used:</b> {'yes' if vu.get('trial_used') else 'no'}\n"
        f"<b>🤝 Referrals:</b> {vu.get('referrals', 0)}"
        + (f" · <b>referred by</b> <code>{vu['referred_by']}</code>" if vu.get("referred_by") else "")
        + f"</blockquote>\n<b>📥 Saver</b>\n<blockquote>"
        f"<b>🔐 Logged in:</b> {'yes' if su.get('session') else 'no'}\n"
        f"<b>📦 Total saves:</b> {su.get('total_saves', 0)} · <b>today:</b> {su.get('daily_usage', 0)}\n"
        f"<b>📤 Dump chat:</b> <code>{su.get('dump_chat') or '—'}</code></blockquote>\n"
        f"<b>⚡ Clone bots:</b> {len(bots)} · {bot_list}"
    )
    kb = InlineKeyboardMarkup([
        [Btn("✅ Unban" if banned else "🚫 Ban", callback_data=f"uadm:{'unban' if banned else 'ban'}:{uid}"),
         Btn("🔄 Refresh", callback_data=f"uadm:view:{uid}")],
        [Btn("💎 +30 days", callback_data=f"uadm:p30:{uid}"), Btn("♾️ Lifetime", callback_data=f"uadm:plife:{uid}"),
         Btn("➖ Premium", callback_data=f"uadm:pdel:{uid}")],
        [Btn("❌ Close", callback_data="close_btn")],
    ])
    return text, kb


async def _resolve(client, message: Message):
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    if len(message.command) < 2:
        return None
    arg = message.command[1]
    if arg.lstrip("-").isdigit():
        return int(arg)
    doc = await vdb.users.find_one({"username": {"$regex": f"^{re.escape(arg.lstrip('@'))}$", "$options": "i"}})
    if doc:
        return doc["id"]
    try:
        return (await client.get_users(arg)).id
    except Exception:
        return None


@Client.on_message(filters.command(["user", "lookup", "whois"]) & admin_filter)
async def user_cmd(client: Client, message: Message):
    uid = await _resolve(client, message)
    if not uid:
        return await message.reply_text("<b>Usage:</b> <code>/user &lt;id | @username&gt;</code> (or reply to a user)")
    from core import stream
    async with stream.thinking(client, message.chat.id):
        text, kb = await profile(client, uid)
    await message.reply_text(text, reply_markup=kb, disable_web_page_preview=True)


@Client.on_callback_query(filters.regex(r"^uadm:(view|ban|unban|p30|plife|pdel):(\d+)$"))
async def uadm_cb(client: Client, query: CallbackQuery):
    if query.from_user.id not in ADMINS:
        return await query.answer("Admins only.", show_alert=True)
    action, uid = query.matches[0].group(1), int(query.matches[0].group(2))
    from core import botlog
    note = None
    if action == "ban":
        if uid in ADMINS:
            return await query.answer("You can't ban an admin.", show_alert=True)
        await vdb.ban(uid, "via /user panel")
        note = "🚫 Banned"
    elif action == "unban":
        await vdb.unban(uid)
        note = "✅ Unbanned"
    elif action in ("p30", "plife"):
        from core.payments import grant_premium, until_text
        expiry = await grant_premium(uid, 30 if action == "p30" else 0)
        note = f"💎 Premium until {until_text(expiry)}"
        try:
            await client.send_message(uid, f"💎 <b>An admin granted you Premium!</b>\n📅 Valid until: {until_text(expiry)}")
        except Exception:
            pass
    elif action == "pdel":
        from database.db import db as saver_db
        await saver_db.remove_premium(uid)
        note = "➖ Premium removed"
    if note:
        await botlog.event("AdminAction", f"<b>👤 User:</b> <code>{uid}</code>\n<b>⚙️ Action:</b> {note}\n"
                                          f"<b>👮 By:</b> {botlog.esc(query.from_user.first_name)} "
                                          f"(<code>{query.from_user.id}</code>)", client=client)
    text, kb = await profile(client, uid)
    try:
        await query.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    except Exception:
        pass
    await query.answer(note or "Refreshed")


@Client.on_message(filters.command(["msg", "dm"]) & admin_filter)
async def msg_cmd(client: Client, message: Message):
    parts = (message.text or "").split(None, 2)
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit() or (len(parts) < 3 and not message.reply_to_message):
        return await message.reply_text("<b>Usage:</b> <code>/msg &lt;user_id&gt; &lt;text&gt;</code>\n"
                                        "or reply to any message with <code>/msg &lt;user_id&gt;</code> to copy it.")
    uid = int(parts[1])
    try:
        if len(parts) >= 3:
            await client.send_message(uid, parts[2])
        else:
            await message.reply_to_message.copy(uid)
    except Exception as e:
        return await message.reply_text(f"❌ Couldn't send: <code>{e}</code>")
    await message.reply_text(f"✅ Sent to <code>{uid}</code>.")


@Client.on_message(filters.command("export") & admin_filter)
async def export_cmd(client: Client, message: Message):
    from core import stream
    async with stream.thinking(client, message.chat.id):
        premium = {}
        try:
            from database.db import db as saver_db
            async for d in saver_db.col.find({"is_premium": True}, {"id": 1, "premium_expiry": 1}):
                premium[d["id"]] = d.get("premium_expiry") or "lifetime"
        except Exception:
            pass
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["id", "name", "username", "joined", "premium", "premium_expiry", "banned", "blocked",
                    "referrals", "referred_by"])
        n = 0
        async for u in vdb.users.find({}):
            uid = u.get("id")
            w.writerow([uid, u.get("name", ""), u.get("username", ""), _fmt_date(u.get("joined")),
                        "yes" if uid in premium else "no", premium.get(uid, ""),
                        "yes" if u.get("banned") else "no", "yes" if u.get("blocked") else "no",
                        u.get("referrals", 0), u.get("referred_by", "")])
            n += 1
    data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
    data.name = f"videl_users_{datetime.now():%Y%m%d_%H%M}.csv"
    await message.reply_document(data, caption=f"📤 <b>{n}</b> users exported.")

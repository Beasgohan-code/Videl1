from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from database.db import db
from config import ADMINS, FREE_LIMIT_DAILY, FREE_LIMIT_SIZE_GB
from core.ui import contact_row
from datetime import date, datetime, timedelta
from logger import LOGGER

logger = LOGGER(__name__)

# ======================================================
# USER COMMANDS - Professional & Informative
# ======================================================

async def plan_view(user_id: int, first_name: str = ""):
    """Return (text, keyboard) describing the user's saver plan."""
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, first_name)
    user_data = await db.col.find_one({'id': user_id}) or {}

    is_premium = user_data.get('is_premium', False)
    expiry = user_data.get('premium_expiry')
    daily_usage = user_data.get('daily_usage', 0)
    total_saves = user_data.get('total_saves', 0)

    if is_premium:
        if expiry:
            try:
                exp_date = expiry if isinstance(expiry, (date, datetime)) else date.fromisoformat(str(expiry))
                if isinstance(exp_date, datetime):
                    exp_date = exp_date.date()
                days_left = (exp_date - date.today()).days
                expiry_text = f"<code>{exp_date}</code> ({days_left} days left)"
            except Exception:
                expiry_text = "<code>Active</code>"
        else:
            expiry_text = "<code>Permanent</code>"
        plan_text = (
            f"<b>👑 Premium Status: Active</b>\n\n"
            f"<b>📅 Expiry:</b> {expiry_text}\n\n"
            f"<b>♾️ Daily Saves:</b> Unlimited\n"
            f"<b>♾️ Batch Limit:</b> Unlimited\n"
            f"<b>📊 Total Lifetime Saves:</b> <code>{total_saves}</code>\n\n"
            "<i>Thank you for supporting the bot! 🎉</i>"
        )
    else:
        tokens_left = max(0, FREE_LIMIT_DAILY - daily_usage)
        plan_text = (
            f"<b>👤 Plan: Free Tier</b>\n\n"
            f"<b>🎫 Daily Saves:</b> <code>{tokens_left} / {FREE_LIMIT_DAILY}</code>\n"
            f"<b>📦 File Size Limit:</b> <code>{FREE_LIMIT_SIZE_GB:g} GB</code>\n"
            f"<b>📊 Total Lifetime Saves:</b> <code>{total_saves}</code>\n\n"
            "<i>Upgrade to Premium for unlimited access! 🚀</i>"
        )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 View Premium Plans", callback_data="premium_plans_btn")],
        *([contact_row()] if contact_row() else []),
        [InlineKeyboardButton("🏠 Home", callback_data="start_btn")],
    ])
    return plan_text, buttons


# /myplan - Detailed Plan & Quota Overview
@Client.on_message(filters.command("myplan") & filters.private)
async def my_plan(client: Client, message: Message):
    text, buttons = await plan_view(message.from_user.id, message.from_user.first_name)
    await message.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command(["premium_users", "premiumusers"]) & filters.user(ADMINS))
async def premium_users(client: Client, message: Message):
    lines = []
    async for u in await db.get_premium_users():
        if not await db._expire_if_needed(u):
            continue
        exp = u.get("premium_expiry") or "♾️ lifetime"
        name = (u.get("name") or "").replace("<", "&lt;")[:25]
        lines.append(f"• <code>{u['id']}</code> {name} — {exp}")
    if not lines:
        return await message.reply_text("👥 No premium users yet.")
    text = f"<b>👥 Premium users ({len(lines)})</b>\n\n" + "\n".join(lines)
    if len(text) > 4000:
        from io import BytesIO
        f = BytesIO(text.replace("<code>", "").replace("</code>", "").replace("<b>", "").replace("</b>", "").encode())
        f.name = "premium_users.txt"
        return await message.reply_document(f, caption=f"👥 {len(lines)} premium users")
    await message.reply_text(text)


# /premium - Premium Plans Information
@Client.on_message(filters.command("premium") & filters.private)
async def premium_info(client: Client, message: Message):
    await show_premium_plans(message)


async def show_premium_plans(message_or_query):
    from saver.start import premium_text
    text = premium_text()
    from saver.start import premium_markup
    buttons = premium_markup("myplan_back_btn")
    if isinstance(message_or_query, Message):
        await message_or_query.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML,
                                          disable_web_page_preview=True)
    else:
        await message_or_query.edit_message_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML,
                                                 disable_web_page_preview=True)


# ======================================================
# ADMIN COMMANDS - Secure & Detailed
# ======================================================

@Client.on_message(filters.command("add_premium") & filters.user(ADMINS) & filters.private)
async def add_premium_admin(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text(
            "<b>⚠️ Admin Usage:</b>\n"
            "<code>/add_premium &lt;user_id&gt; &lt;days&gt;</code>\n\n"
            "<i>Use 0 for permanent premium.</i>",
            parse_mode=enums.ParseMode.HTML
        )

    try:
        user_id = int(message.command[1])
        days = int(message.command[2])

        if days == 0:
            expiry_date = None
            duration_text = "Permanent"
        else:
            expiry_date = (date.today() + timedelta(days=days)).isoformat()
            duration_text = f"{days} days (until {expiry_date})"

        # Update DB
        await db.add_premium(user_id, expiry_date)
        from core import botlog
        await botlog.event("PremiumAdded", (
            f"<b>👤 User:</b> <a href=\"tg://user?id={user_id}\">{user_id}</a> (<code>{user_id}</code>)\n"
            f"<b>📅 Duration:</b> {duration_text}\n<b>👮 By:</b> {botlog.esc(message.from_user.first_name)} "
            f"(<code>{message.from_user.id}</code>)"), client=client)
        try:
            await client.send_message(user_id, f"🎉 <b>You received Premium!</b>\n<b>Duration:</b> {duration_text}")
        except Exception:
            pass

        await message.reply_text(
            f"<b>✅ Premium Added Successfully</b>\n\n"
            f"<b>User ID:</b> <code>{user_id}</code>\n"
            f"<b>Duration:</b> {duration_text}",
            parse_mode=enums.ParseMode.HTML
        )

    except ValueError:
        await message.reply_text("❌ <b>Error:</b> User ID and Days must be numbers.", parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        await message.reply_text(f"❌ <b>Error:</b> {e}", parse_mode=enums.ParseMode.HTML)

@Client.on_message(filters.command("remove_premium") & filters.user(ADMINS) & filters.private)
async def remove_premium_admin(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text(
            "<b>⚠️ Usage:</b> <code>/remove_premium &lt;user_id&gt;</code>",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        user_id = int(message.command[1])
        await db.remove_premium(user_id)
        from core import botlog
        await botlog.event("PremiumRemoved", (
            f"<b>👤 User:</b> <code>{user_id}</code>\n<b>👮 By:</b> {botlog.esc(message.from_user.first_name)} "
            f"(<code>{message.from_user.id}</code>)"), client=client)
        await message.reply_text(f"✅ Premium removed from <code>{user_id}</code>.")
    except Exception as e:
        await message.reply_text(f"Error: {e}")

# ======================================================
# CALLBACK QUERIES
# ======================================================

@Client.on_callback_query(filters.regex("^premium_plans_btn$"))
async def premium_plans_callback(client: Client, callback_query: CallbackQuery):
    await show_premium_plans(callback_query)

@Client.on_callback_query(filters.regex("^myplan_back_btn$"))
async def myplan_back_callback(client: Client, callback_query: CallbackQuery):
    text, buttons = await plan_view(callback_query.from_user.id, callback_query.from_user.first_name)
    try:
        await callback_query.edit_message_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass

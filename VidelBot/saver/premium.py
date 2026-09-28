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

async def plan_doc(user_id: int, first_name: str = ""):
    """Rich screen + keyboard for the user's saver plan (table: plan · expiry · quota · size · saves)."""
    from core.rich import Doc, code
    if not await db.is_user_exist(user_id):
        await db.add_user(user_id, first_name)
    u = await db.col.find_one({'id': user_id}) or {}
    is_premium, expiry = u.get('is_premium', False), u.get('premium_expiry')
    used, total_saves = u.get('daily_usage', 0), u.get('total_saves', 0)
    if is_premium:
        exp = "♾️ Lifetime"
        if expiry:
            try:
                d = expiry if isinstance(expiry, (date, datetime)) else date.fromisoformat(str(expiry))
                d = d.date() if isinstance(d, datetime) else d
                exp = f"{d} ({(d - date.today()).days} days left)"
            except Exception:
                exp = "Active"
        rows = [("👑 Plan", "Premium · active"), ("📅 Expires", exp), ("♾️ Daily saves", "Unlimited"),
                ("📦 File size", "4 GB+"), ("📊 Lifetime saves", code(total_saves))]
        doc = Doc("👑", "My plan", "thank you for supporting the bot 🎉")
    else:
        left = max(0, FREE_LIMIT_DAILY - used)
        rows = [("👤 Plan", "Free tier"), ("🎫 Saves left today", code(f"{left} / {FREE_LIMIT_DAILY}")),
                ("📦 File size", f"{FREE_LIMIT_SIZE_GB:g} GB"), ("📊 Lifetime saves", code(total_saves))]
        doc = Doc("📊", "My plan", "upgrade to Premium for unlimited access 🚀")
    doc.table(rows, header=("Item", "Value"))
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 View Premium Plans", callback_data="premium_plans_btn")],
        *([contact_row()] if contact_row() else []),
        [InlineKeyboardButton("🏠 Home", callback_data="start_btn")],
    ])
    return doc, buttons


def premium_doc():
    """Free vs Premium comparison + Stars / other prices."""
    from config import STARS_PLANS, SUBSCRIPTION_STARS, PREMIUM_PRICES, UPI_ID, QR_CODE
    from core.rich import Doc, code, link
    doc = Doc("💎", "Premium membership", "unlock unlimited access & advanced features")
    doc.table([
        ("📥 Daily saves", f"{FREE_LIMIT_DAILY}", "♾️ Unlimited"),
        ("📦 File size", f"{FREE_LIMIT_SIZE_GB:g} GB", "4 GB+"),
        ("📚 Batch saving", "Limited", "♾️ Unlimited"),
        ("⚡ Processing", "Normal", "Instant"),
        ("🖼 Thumbnail & caption", "✅", "✅"),
        ("🛂 Priority support", "—", "✅"),
    ], header=("Feature", "Free", "Premium"), align=("left", "center", "center"))
    plans = [("Lifetime" if d == 0 else f"{d} days", f"⭐ {st}") for d, st in STARS_PLANS]
    if SUBSCRIPTION_STARS > 0:
        plans.append(("Monthly · auto-renew", f"⭐ {SUBSCRIPTION_STARS} / month"))
    if plans:
        doc.h("⭐", "Pay with Telegram Stars")
        doc.table(plans, header=("Plan", "Price"), align=("left", "right"),
                  caption="Instant activation – tap a plan below")
    other = [p.strip() for p in (PREMIUM_PRICES or "").split("|") if p.strip()]
    if other or UPI_ID or QR_CODE:
        more = Doc()
        if other:
            more.table([tuple(x.strip() for x in p.split(":", 1)) if ":" in p else (p, "") for p in other],
                       header=("Plan", "Price"))
        pay = []
        if UPI_ID:
            pay.append(("💸 UPI ID", code(UPI_ID)))
        if QR_CODE:
            pay.append(("📸 QR code", link("Scan to pay", QR_CODE)))
        if pay:
            more.table(pay)
        more.text("<i>After paying, send the screenshot to the admin for activation.</i>")
        doc.details("💳 Other payment options", more)
    return doc


async def plan_view(user_id: int, first_name: str = ""):
    """Return (text, keyboard) describing the user's saver plan (classic rendering of plan_doc)."""
    doc, buttons = await plan_doc(user_id, first_name)
    return doc.classic(), buttons


# /myplan - Detailed Plan & Quota Overview
@Client.on_message(filters.command("myplan") & filters.private)
async def my_plan(client: Client, message: Message):
    from core import rich
    doc, buttons = await plan_doc(message.from_user.id, message.from_user.first_name)
    await rich.reply(message, doc, reply_markup=buttons)


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
@Client.on_message(filters.command(["premium", "premium_info"]) & filters.private)
async def premium_info(client: Client, message: Message):
    await show_premium_plans(message)


async def show_premium_plans(message_or_query):
    from core import rich
    from saver.start import premium_markup
    buttons = premium_markup("myplan_back_btn")
    if isinstance(message_or_query, Message):
        await rich.reply(message_or_query, premium_doc(), reply_markup=buttons)
    else:
        await rich.edit(message_or_query.message, premium_doc(), reply_markup=buttons)
        try:
            await message_or_query.answer()
        except Exception:
            pass


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
    from core import rich
    doc, buttons = await plan_doc(callback_query.from_user.id, callback_query.from_user.first_name)
    try:
        await rich.edit(callback_query.message, doc, reply_markup=buttons)
        await callback_query.answer()
    except Exception:
        pass

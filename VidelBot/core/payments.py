"""
Self-service Premium with Telegram Stars (⭐, currency "XTR").

Flow: 💎 Buy Premium → ⭐ plan button → invoice → pre-checkout validation →
successful payment → premium activated/extended instantly (+ log, effect).
Owners can refund with /refund <user_id> <charge_id>.
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.types import (CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup,
                            LabeledPrice, Message, PreCheckoutQuery)

from config import ADMINS, BOT_NAME, LOG_CHANNEL, OWNERS, STARS_PLANS
from core.db import vdb
from core.ui import copy_button, effect

log = logging.getLogger("videl.payments")
PLANS = dict(STARS_PLANS)  # days -> stars


def _label(days: int) -> str:
    return "Lifetime" if days == 0 else f"{days} days"


def make_payload(days: int, user_id: int) -> str:
    return f"vp:{days}:{user_id}:{int(time.time())}"


def parse_payload(payload) -> tuple:
    """Return (days, user_id) or raise ValueError."""
    if isinstance(payload, bytes):
        payload = payload.decode()
    tag, days, uid, _ = str(payload).split(":")
    if tag != "vp":
        raise ValueError("foreign payload")
    return int(days), int(uid)


def new_expiry(current_expiry, days: int):
    """Extend from the current expiry if it's still in the future. None = lifetime."""
    if days == 0:
        return None
    base = date.today()
    if current_expiry:
        try:
            cur = current_expiry if isinstance(current_expiry, date) else date.fromisoformat(str(current_expiry)[:10])
            if isinstance(cur, datetime):
                cur = cur.date()
            if cur > base:
                base = cur
        except ValueError:
            pass
    return (base + timedelta(days=days)).isoformat()


# ─────────────────────────── invoice ───────────────────────────
@Client.on_callback_query(filters.regex(r"^stars_buy:(\d+)$"))
async def stars_buy(client: Client, query: CallbackQuery):
    days = int(query.matches[0].group(1))
    if days not in PLANS:
        return await query.answer("This plan is no longer available.", show_alert=True)
    stars = PLANS[days]
    try:
        await client.send_invoice(
            chat_id=query.from_user.id,
            title=f"{BOT_NAME} Premium · {_label(days)}",
            description=("Unlimited saves, 4GB+ files, no delays, custom thumbnails & captions. "
                         f"Duration: {_label(days)}."),
            currency="XTR",
            prices=[LabeledPrice(label=f"Premium {_label(days)}", amount=stars)],
            payload=make_payload(days, query.from_user.id),
            start_parameter="premium",
        )
        await query.answer("⭐ Invoice sent below 👇")
    except Exception as e:
        log.warning(f"send_invoice failed: {e}")
        await query.answer(f"❌ Couldn't create the invoice: {e}"[:190], show_alert=True)


@Client.on_message(filters.command(["buy", "stars_buy"]) & filters.private)
async def buy_cmd(client: Client, message: Message):
    if not PLANS:
        return await message.reply_text("⭐ Stars payments are disabled. See /premium.")
    kb = [[Btn(f"⭐ {s} · {_label(d)}", callback_data=f"stars_buy:{d}")] for d, s in STARS_PLANS]
    await message.reply_text("<b>💎 Choose a Premium plan</b>\n<i>Pay securely with Telegram Stars – activated instantly.</i>",
                             reply_markup=InlineKeyboardMarkup(kb))


@Client.on_pre_checkout_query()
async def pre_checkout(client: Client, query: PreCheckoutQuery):
    try:
        days, uid = parse_payload(query.payload)
        ok = (query.currency == "XTR" and days in PLANS and PLANS[days] == query.total_amount
              and uid == query.from_user.id)
    except Exception:
        ok = False
    if ok:
        await query.answer(success=True)
    else:
        await query.answer(success=False, error="This invoice is outdated – please open 💎 Buy Premium again.")


# group -10 → runs before every gate so a payment is never swallowed
@Client.on_message(filters.successful_payment, group=-10)
async def successful_payment(client: Client, message: Message):
    sp = message.successful_payment
    try:
        days, uid = parse_payload(sp.payload)
    except Exception:
        return
    from database.db import db as saver_db
    if not await saver_db.is_user_exist(uid):
        await saver_db.add_user(uid, message.from_user.first_name if message.from_user else "")
    user = await saver_db.col.find_one({"id": uid}) or {}
    current = user.get("premium_expiry") if user.get("is_premium") else None
    if user.get("is_premium") and not current:
        expiry = None  # already lifetime
    else:
        expiry = new_expiry(current, days)
    await saver_db.add_premium(uid, expiry)
    await vdb.db["payments"].insert_one({
        "user": uid, "days": days, "stars": sp.total_amount, "currency": sp.currency,
        "charge_id": sp.telegram_payment_charge_id, "expiry": expiry,
        "date": datetime.now(timezone.utc), "refunded": False,
    })
    until = "♾️ Lifetime" if expiry is None else expiry
    try:
        await client.send_message(
            message.chat.id,
            f"<b>🎉 Payment received – thank you!</b>\n\n<blockquote><b>💎 Premium:</b> Active\n"
            f"<b>📅 Valid until:</b> {until}\n<b>⭐ Paid:</b> {sp.total_amount} Stars</blockquote>\n"
            "<i>Enjoy unlimited saves & big files!</i>",
            message_effect_id=effect("party"),
            reply_markup=InlineKeyboardMarkup([[Btn("📊 My Plan", callback_data="myplan_back_btn")],
                                               [copy_button("📋 Copy receipt ID", sp.telegram_payment_charge_id)]]),
        )
    except Exception:
        await message.reply_text(f"🎉 Premium activated until {until}. Thank you!")
    if LOG_CHANNEL:
        try:
            await client.send_message(
                LOG_CHANNEL,
                f"#StarsPayment\n<b>User:</b> {message.from_user.mention} (<code>{uid}</code>)\n"
                f"<b>Plan:</b> {_label(days)} · <b>⭐</b> {sp.total_amount}\n"
                f"<b>Charge:</b> <code>{sp.telegram_payment_charge_id}</code>",
            )
        except Exception:
            pass


# ─────────────────────────── admin ───────────────────────────
@Client.on_message(filters.command("stars") & filters.user(ADMINS))
async def stars_stats(client: Client, message: Message):
    col = vdb.db["payments"]
    agg = await col.aggregate([
        {"$match": {"refunded": False}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "stars": {"$sum": "$stars"}}},
    ]).to_list(1)
    n, total = (agg[0]["n"], agg[0]["stars"]) if agg else (0, 0)
    last = await col.find().sort("date", -1).limit(10).to_list(10)
    lines = [
        f"• <code>{p['user']}</code> · {_label(p['days'])} · ⭐{p['stars']}"
        f"{' · ↩️ refunded' if p.get('refunded') else ''}\n  <code>{p['charge_id']}</code>"
        for p in last
    ]
    plans = " · ".join(f"{_label(d)} = ⭐{s}" for d, s in STARS_PLANS) or "disabled"
    await message.reply_text(
        f"<b>⭐ Stars payments</b>\n\n<blockquote>Paid orders: <code>{n}</code>\nStars earned: <code>{total}</code>\n"
        f"Plans: {plans}</blockquote>\n\n<b>Latest:</b>\n" + ("\n".join(lines) or "—")
    )


@Client.on_message(filters.command("refund") & filters.user(OWNERS))
async def refund_cmd(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text("<b>Usage:</b> <code>/refund &lt;user_id&gt; &lt;charge_id&gt;</code> (see /stars)")
    try:
        uid = int(message.command[1])
    except ValueError:
        return await message.reply_text("❌ user_id must be a number.")
    charge = message.command[2]
    try:
        await client.refund_star_payment(uid, charge)
    except Exception as e:
        return await message.reply_text(f"❌ Refund failed: <code>{e}</code>")
    await vdb.db["payments"].update_one({"charge_id": charge}, {"$set": {"refunded": True}})
    from database.db import db as saver_db
    await saver_db.remove_premium(uid)
    await message.reply_text(f"↩️ Refunded and premium removed for <code>{uid}</code>.")
    try:
        await client.send_message(uid, "↩️ <b>Your Stars payment was refunded.</b> Premium has been removed.")
    except Exception:
        pass

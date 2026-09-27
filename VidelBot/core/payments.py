"""
Self-service Premium with Telegram Stars (⭐, currency "XTR").

Three ways to pay, all validated in one pre-checkout handler:

  • one-time plans    💎 Buy Premium → ⭐ plan → invoice            payload ``vp:days:uid:ts``
  • monthly auto-renew  🔁 Subscribe → Stars subscription link       payload ``vs:30:uid:ts``
                        (renewals arrive as new successful payments → +30 days each)
  • gift for a friend  🎁 Gift → native user picker → invoice       payload ``vg:days:buyer:target:ts``

Every successful payment activates/extends premium instantly, is stored in the
``payments`` collection and reported to the owner log (#StarsPayment).
Owners can refund with /refund <user_id> <charge_id>; users manage their
subscription with /mysub (cancel / resume auto-renew).
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone

from pyrogram import Client, StopPropagation, filters, raw
from pyrogram.types import (CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup,
                            KeyboardButton, LabeledPrice, Message, PreCheckoutQuery,
                            ReplyKeyboardMarkup, ReplyKeyboardRemove, RequestPeerTypeUser)

from config import ADMINS, BOT_NAME, GIFTS_ENABLED, OWNERS, STARS_PLANS, SUBSCRIPTION_STARS
from core.db import vdb
from core.ui import copy_button, effect

log = logging.getLogger("videl.payments")
PLANS = dict(STARS_PLANS)  # days -> stars
SUB_DAYS = 30
SUB_PERIOD = 30 * 24 * 3600   # Telegram only accepts 2592000 s (30 days) for Stars subscriptions
GIFT_BUTTON = 77              # request_user button id of the gift picker
CANCEL_GIFT = "❌ Cancel gift"


def _label(days: int) -> str:
    return "Lifetime" if days == 0 else f"{days} days"


# ─────────────────────────── payloads ───────────────────────────
def make_payload(days: int, user_id: int) -> str:
    return f"vp:{days}:{user_id}:{int(time.time())}"


def make_sub_payload(user_id: int) -> str:
    return f"vs:{SUB_DAYS}:{user_id}:{int(time.time())}"


def make_gift_payload(days: int, buyer: int, target: int) -> str:
    return f"vg:{days}:{buyer}:{target}:{int(time.time())}"


def decode_payload(payload) -> dict:
    """Return {kind, days, payer, target} for any Videl payload or raise ValueError."""
    if isinstance(payload, bytes):
        payload = payload.decode()
    parts = str(payload).split(":")
    tag = parts[0] if parts else ""
    if tag in ("vp", "vs") and len(parts) == 4:
        days, uid = int(parts[1]), int(parts[2])
        return {"kind": "plan" if tag == "vp" else "sub", "days": days, "payer": uid, "target": uid}
    if tag == "vg" and len(parts) == 5:
        return {"kind": "gift", "days": int(parts[1]), "payer": int(parts[2]), "target": int(parts[3])}
    raise ValueError("foreign payload")


def parse_payload(payload) -> tuple:
    """Return (days, user_id) of a one-time plan payload or raise ValueError."""
    info = decode_payload(payload)
    if info["kind"] != "plan":
        raise ValueError("not a plan payload")
    return info["days"], info["payer"]


def expected_amount(info: dict):
    """Stars an invoice with this payload must cost (None → not sellable)."""
    if info["kind"] == "sub":
        return SUBSCRIPTION_STARS if SUBSCRIPTION_STARS > 0 and info["days"] == SUB_DAYS else None
    if info["kind"] == "gift" and (not GIFTS_ENABLED or info["target"] == info["payer"]):
        return None
    return PLANS.get(info["days"])


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


async def grant_premium(user_id: int, days: int, name: str = ""):
    """Activate / extend saver premium by *days* (0 = lifetime). Returns the
    new expiry (ISO date) or None for lifetime. Shared by payments, gifts,
    referrals, redeem codes, trials and the admin panel."""
    from database.db import db as saver_db
    if not await saver_db.is_user_exist(user_id):
        await saver_db.add_user(user_id, name or "")
    user = await saver_db.col.find_one({"id": user_id}) or {}
    current = user.get("premium_expiry") if user.get("is_premium") else None
    if user.get("is_premium") and not current:
        expiry = None  # already lifetime
    else:
        expiry = new_expiry(current, days)
    await saver_db.add_premium(user_id, expiry)
    return expiry


def until_text(expiry) -> str:
    return "♾️ Lifetime" if expiry is None else expiry


# ─────────────────────────── one-time plans ───────────────────────────
async def _send_invoice(client, chat_id, days, payload, title_prefix="Premium", description=None):
    await client.send_invoice(
        chat_id=chat_id,
        title=f"{BOT_NAME} {title_prefix} · {_label(days)}",
        description=description or ("Unlimited saves, 4GB+ files, no delays, custom thumbnails & captions. "
                                    f"Duration: {_label(days)}."),
        currency="XTR",
        prices=[LabeledPrice(label=f"{title_prefix} {_label(days)}", amount=PLANS[days])],
        payload=payload,
        start_parameter="premium",
    )


@Client.on_callback_query(filters.regex(r"^stars_buy:(\d+)$"))
async def stars_buy(client: Client, query: CallbackQuery):
    days = int(query.matches[0].group(1))
    if days not in PLANS:
        return await query.answer("This plan is no longer available.", show_alert=True)
    try:
        await _send_invoice(client, query.from_user.id, days, make_payload(days, query.from_user.id))
        await query.answer("⭐ Invoice sent below 👇")
    except Exception as e:
        log.warning(f"send_invoice failed: {e}")
        await query.answer(f"❌ Couldn't create the invoice: {e}"[:190], show_alert=True)


def plans_keyboard(prefix: str = "stars_buy", extra: str = "") -> list:
    return [[Btn(f"⭐ {s} · {_label(d)}", callback_data=f"{prefix}:{d}{extra}")] for d, s in STARS_PLANS]


@Client.on_message(filters.command(["buy", "stars_buy"]) & filters.private)
async def buy_cmd(client: Client, message: Message):
    if not PLANS and SUBSCRIPTION_STARS <= 0:
        return await message.reply_text("⭐ Stars payments are disabled. See /premium.")
    kb = plans_keyboard()
    if SUBSCRIPTION_STARS > 0:
        kb.append([Btn(f"🔁 ⭐ {SUBSCRIPTION_STARS} / month · auto-renew", callback_data="stars_sub")])
    if GIFTS_ENABLED and PLANS:
        kb.append([Btn("🎁 Gift Premium to a friend", callback_data="gift_premium")])
    await message.reply_text("<b>💎 Choose a Premium plan</b>\n<i>Pay securely with Telegram Stars – activated instantly.</i>",
                             reply_markup=InlineKeyboardMarkup(kb))


# ─────────────────────────── subscription ───────────────────────────
async def subscription_link(client: Client, user_id: int) -> str:
    """Create a Stars subscription invoice link (auto-renews every 30 days)."""
    r = await client.invoke(raw.functions.payments.ExportInvoice(
        invoice_media=raw.types.InputMediaInvoice(
            title=f"{BOT_NAME} Premium · Monthly",
            description=(f"Premium that renews automatically every 30 days for ⭐{SUBSCRIPTION_STARS}. "
                         "Cancel anytime with /mysub."),
            invoice=raw.types.Invoice(
                currency="XTR",
                prices=[raw.types.LabeledPrice(label="Premium · 30 days", amount=SUBSCRIPTION_STARS)],
                subscription_period=SUB_PERIOD,
            ),
            payload=make_sub_payload(user_id).encode(),
            provider_data=raw.types.DataJSON(data="{}"),
        )
    ))
    return r.url


async def _offer_subscription(client: Client, chat_id: int, user_id: int):
    if SUBSCRIPTION_STARS <= 0:
        return await client.send_message(chat_id, "🔁 Subscriptions are disabled on this bot. See /buy.")
    sub = await vdb.db["subscriptions"].find_one({"user": user_id, "active": True, "canceled": {"$ne": True}})
    if sub:
        return await client.send_message(chat_id, "✅ You already have an active subscription. Manage it with /mysub.")
    url = await subscription_link(client, user_id)
    await client.send_message(
        chat_id,
        f"<b>🔁 {BOT_NAME} Premium · Monthly</b>\n\n<blockquote>⭐ <b>{SUBSCRIPTION_STARS} Stars</b> every 30 days\n"
        "✅ Renews automatically – never lose Premium\n❌ Cancel anytime with /mysub</blockquote>",
        reply_markup=InlineKeyboardMarkup([[Btn(f"⭐ Subscribe for {SUBSCRIPTION_STARS} / month", url=url)]]),
    )


@Client.on_callback_query(filters.regex(r"^stars_sub$"))
async def stars_sub_cb(client: Client, query: CallbackQuery):
    try:
        await _offer_subscription(client, query.from_user.id, query.from_user.id)
        await query.answer("🔁 Subscription link sent below 👇")
    except Exception as e:
        log.warning(f"subscription link failed: {e}")
        await query.answer(f"❌ Couldn't create the subscription: {e}"[:190], show_alert=True)


@Client.on_message(filters.command("subscribe") & filters.private)
async def subscribe_cmd(client: Client, message: Message):
    try:
        await _offer_subscription(client, message.chat.id, message.from_user.id)
    except Exception as e:
        await message.reply_text(f"❌ Couldn't create the subscription link: <code>{e}</code>")


def _sub_text(sub: dict | None, expiry) -> str:
    if not sub:
        return ("<b>🔁 My subscription</b>\n\n<blockquote>You have no Stars subscription.</blockquote>\n"
                + (f"<i>Subscribe for ⭐{SUBSCRIPTION_STARS}/month and never lose Premium.</i>" if SUBSCRIPTION_STARS > 0 else ""))
    state = "❌ Canceled – ends at the period end" if sub.get("canceled") else "✅ Active · auto-renew on"
    since = sub.get("since")
    since = since.strftime("%d %b %Y") if isinstance(since, datetime) else "—"
    return (f"<b>🔁 My subscription</b>\n\n<blockquote><b>Status:</b> {state}\n"
            f"<b>⭐ Price:</b> {sub.get('stars', SUBSCRIPTION_STARS)} Stars / 30 days\n"
            f"<b>📅 Since:</b> {since}\n<b>🔁 Renewals:</b> {sub.get('renewals', 0)}\n"
            f"<b>💎 Premium until:</b> {until_text(expiry) if expiry is not False else '—'}</blockquote>")


def _sub_kb(sub: dict | None) -> InlineKeyboardMarkup:
    if not sub:
        rows_ = [[Btn(f"🔁 Subscribe · ⭐{SUBSCRIPTION_STARS}/month", callback_data="stars_sub")]] if SUBSCRIPTION_STARS > 0 else []
    elif sub.get("canceled"):
        rows_ = [[Btn("🔁 Resume auto-renew", callback_data="sub_toggle:resume")]]
    else:
        rows_ = [[Btn("❌ Cancel auto-renew", callback_data="sub_toggle:cancel")]]
    rows_.append([Btn("📊 My Plan", callback_data="myplan_back_btn")])
    return InlineKeyboardMarkup(rows_)


async def _current_expiry(user_id: int):
    from database.db import db as saver_db
    u = await saver_db.col.find_one({"id": user_id}) or {}
    return u.get("premium_expiry") if u.get("is_premium") else False


@Client.on_message(filters.command(["mysub", "subscription"]) & filters.private)
async def mysub_cmd(client: Client, message: Message):
    sub = await vdb.db["subscriptions"].find_one({"user": message.from_user.id, "active": True})
    await message.reply_text(_sub_text(sub, await _current_expiry(message.from_user.id)), reply_markup=_sub_kb(sub))


@Client.on_callback_query(filters.regex(r"^sub_toggle:(cancel|resume)$"))
async def sub_toggle_cb(client: Client, query: CallbackQuery):
    uid = query.from_user.id
    restore = query.matches[0].group(1) == "resume"
    sub = await vdb.db["subscriptions"].find_one({"user": uid, "active": True})
    if not sub:
        return await query.answer("No active subscription.", show_alert=True)
    try:
        await client.invoke(raw.functions.payments.BotCancelStarsSubscription(
            user_id=await client.resolve_peer(uid), charge_id=sub["charge_id"], restore=restore or None))
    except Exception as e:
        return await query.answer(f"❌ Telegram error: {e}"[:190], show_alert=True)
    await vdb.db["subscriptions"].update_one({"_id": sub["_id"]}, {"$set": {"canceled": not restore}})
    sub["canceled"] = not restore
    await query.answer("🔁 Auto-renew resumed." if restore else "❌ Auto-renew canceled – Premium stays until the period ends.",
                       show_alert=True)
    try:
        await query.message.edit_text(_sub_text(sub, await _current_expiry(uid)), reply_markup=_sub_kb(sub))
    except Exception:
        pass
    from core import botlog
    await botlog.event("Subscription", botlog.user_block(query.from_user,
                       "🔁 Auto-renew <b>resumed</b>." if restore else "❌ Auto-renew <b>canceled</b>."), client=client)


# ─────────────────────────── gifts ───────────────────────────
def _gift_picker() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[KeyboardButton("👤 Choose a friend", request_user=RequestPeerTypeUser(button_id=GIFT_BUTTON, is_bot=False))],
         [KeyboardButton(CANCEL_GIFT)]],
        resize_keyboard=True, one_time_keyboard=True, placeholder="Pick who gets Premium 🎁",
    )


def _gift_plans_kb(target: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(plans_keyboard("gift_buy", f":{target}") +
                                [[Btn("❌ Cancel", callback_data="close_btn")]])


async def _ask_gift_target(client: Client, chat_id: int):
    if not GIFTS_ENABLED or not PLANS:
        return await client.send_message(chat_id, "🎁 Gifting is disabled on this bot.")
    await client.send_message(
        chat_id,
        "<b>🎁 Gift Premium</b>\n\n<blockquote>Tap <b>👤 Choose a friend</b> below and pick who should get Premium. "
        "You'll pay with Telegram Stars and they are notified instantly.</blockquote>\n"
        "<i>Tip: you can also send</i> <code>/gift user_id</code>",
        reply_markup=_gift_picker(),
    )


@Client.on_callback_query(filters.regex(r"^gift_premium$"))
async def gift_premium_cb(client: Client, query: CallbackQuery):
    await query.answer()
    await _ask_gift_target(client, query.from_user.id)


@Client.on_message(filters.command("gift") & filters.private)
async def gift_cmd(client: Client, message: Message):
    if len(message.command) > 1:
        if not GIFTS_ENABLED or not PLANS:
            return await message.reply_text("🎁 Gifting is disabled on this bot.")
        arg = message.command[1]
        try:
            target = int(arg) if arg.lstrip("-").isdigit() else (await client.get_users(arg)).id
        except Exception:
            return await message.reply_text("❌ User not found. Use the picker: send /gift")
        if target == message.from_user.id:
            return await message.reply_text("😅 You can't gift yourself – use /buy instead.")
        return await message.reply_text(f"<b>🎁 Gift Premium to</b> <code>{target}</code>\nChoose a plan:",
                                        reply_markup=_gift_plans_kb(target))
    await _ask_gift_target(client, message.chat.id)


def shared_peer_filter(button_id: int):
    """Messages carrying a peer shared through a request_user/request_chat button."""
    async def func(_, __, m: Message):
        shared = getattr(m, "chats_shared", None)
        if not shared:
            return False
        for item in (shared if isinstance(shared, list) else [shared]):
            if getattr(item, "button_id", None) == button_id:
                return True
        return False
    return filters.create(func, f"SharedPeer{button_id}")


def shared_ids(message: Message) -> list:
    """(id, name) pairs of the users/chats shared in *message*."""
    out, shared = [], getattr(message, "chats_shared", None)
    for item in (shared if isinstance(shared, list) else [shared] if shared else []):
        for u in getattr(item, "users", None) or []:
            out.append((u.user_id, " ".join(x for x in (u.first_name, getattr(u, "last_name", None)) if x) or str(u.user_id)))
        for c in getattr(item, "chats", None) or []:
            out.append((c.chat_id, c.name or str(c.chat_id)))
    return out


@Client.on_message(filters.private & shared_peer_filter(GIFT_BUTTON), group=-1)
async def gift_target_shared(client: Client, message: Message):
    picked = shared_ids(message)
    if not picked:
        raise StopPropagation
    target, name = picked[0]
    if target == message.from_user.id:
        await message.reply_text("😅 You can't gift yourself – use /buy instead.", reply_markup=ReplyKeyboardRemove())
        raise StopPropagation
    from core.botlog import esc
    await message.reply_text(f"✅ Friend selected: <b>{esc(name)}</b>", reply_markup=ReplyKeyboardRemove())
    await message.reply_text(f"<b>🎁 Gift Premium to {esc(name)}</b>\nChoose a plan:", reply_markup=_gift_plans_kb(target))
    raise StopPropagation


@Client.on_message(filters.private & filters.regex(f"^{CANCEL_GIFT}$"), group=-1)
async def gift_cancel(client: Client, message: Message):
    await message.reply_text("🎁 Gift canceled.", reply_markup=ReplyKeyboardRemove())
    raise StopPropagation


@Client.on_callback_query(filters.regex(r"^gift_buy:(\d+):(\d+)$"))
async def gift_buy_cb(client: Client, query: CallbackQuery):
    days, target = int(query.matches[0].group(1)), int(query.matches[0].group(2))
    if days not in PLANS or not GIFTS_ENABLED:
        return await query.answer("This plan is no longer available.", show_alert=True)
    if target == query.from_user.id:
        return await query.answer("You can't gift yourself.", show_alert=True)
    try:
        await _send_invoice(client, query.from_user.id, days, make_gift_payload(days, query.from_user.id, target),
                            title_prefix="Gift Premium",
                            description=f"A {_label(days)} Premium gift for user {target}. They are notified instantly.")
        await query.answer("⭐ Invoice sent below 👇")
    except Exception as e:
        await query.answer(f"❌ Couldn't create the invoice: {e}"[:190], show_alert=True)


# ─────────────────────────── checkout ───────────────────────────
@Client.on_pre_checkout_query()
async def pre_checkout(client: Client, query: PreCheckoutQuery):
    try:
        info = decode_payload(query.payload)
        amount = expected_amount(info)
        ok = (query.currency == "XTR" and amount is not None and amount == query.total_amount
              and info["payer"] == query.from_user.id)
    except Exception:
        ok = False
    if ok:
        await query.answer(success=True)
    else:
        await query.answer(success=False, error="This invoice is outdated – please open 💎 Buy Premium again.")


async def _record_subscription(uid: int, sp, expiry) -> tuple[bool, int]:
    """Upsert the subscription document. Returns (is_renewal, renewals)."""
    col = vdb.db["subscriptions"]
    now = datetime.now(timezone.utc)
    existing = await col.find_one({"user": uid, "active": True})
    if existing:
        renewals = existing.get("renewals", 0) + 1
        await col.update_one({"_id": existing["_id"]}, {"$set": {
            "last_charge": sp.telegram_payment_charge_id, "last_payment": now, "until": expiry,
            "renewals": renewals, "canceled": False}})
        return True, renewals
    await col.insert_one({
        "user": uid, "charge_id": sp.telegram_payment_charge_id, "last_charge": sp.telegram_payment_charge_id,
        "stars": sp.total_amount, "since": now, "last_payment": now, "until": expiry,
        "renewals": 0, "active": True, "canceled": False})
    return False, 0


# group -10 → runs before every gate so a payment is never swallowed
@Client.on_message(filters.successful_payment, group=-10)
async def successful_payment(client: Client, message: Message):
    sp = message.successful_payment
    try:
        info = decode_payload(sp.payload)
    except Exception:
        return
    kind, days, payer, target = info["kind"], info["days"], info["payer"], info["target"]
    buyer_name = message.from_user.first_name if message.from_user else ""
    expiry = await grant_premium(target, days, buyer_name if target == payer else "")
    renewal, renewals = False, 0
    if kind == "sub":
        renewal, renewals = await _record_subscription(payer, sp, expiry)
    await vdb.db["payments"].insert_one({
        "user": payer, "target": target, "kind": kind, "days": days, "stars": sp.total_amount,
        "currency": sp.currency, "charge_id": sp.telegram_payment_charge_id, "expiry": expiry,
        "date": datetime.now(timezone.utc), "refunded": False,
    })
    until = until_text(expiry)
    receipt = [copy_button("📋 Copy receipt ID", sp.telegram_payment_charge_id)]
    if kind == "gift":
        head = f"<b>🎁 Gift sent – thank you!</b>\n\n<blockquote><b>👤 Friend:</b> <code>{target}</code>\n" \
               f"<b>💎 Premium until:</b> {until}\n<b>⭐ Paid:</b> {sp.total_amount} Stars</blockquote>"
        kb = InlineKeyboardMarkup([receipt])
    elif kind == "sub":
        head = (f"<b>🔁 Subscription {'renewed' if renewal else 'active'} – thank you!</b>\n\n<blockquote>"
                f"<b>💎 Premium until:</b> {until}\n<b>⭐ Paid:</b> {sp.total_amount} Stars\n"
                f"<b>🔁 Renews:</b> every 30 days</blockquote>\n<i>Manage it anytime with /mysub.</i>")
        kb = InlineKeyboardMarkup([[Btn("🔁 My subscription", callback_data="mysub_btn")], receipt])
    else:
        head = (f"<b>🎉 Payment received – thank you!</b>\n\n<blockquote><b>💎 Premium:</b> Active\n"
                f"<b>📅 Valid until:</b> {until}\n<b>⭐ Paid:</b> {sp.total_amount} Stars</blockquote>\n"
                "<i>Enjoy unlimited saves & big files!</i>")
        kb = InlineKeyboardMarkup([[Btn("📊 My Plan", callback_data="myplan_back_btn")], receipt])
    try:
        await client.send_message(message.chat.id, head, message_effect_id=effect("party"), reply_markup=kb)
    except Exception:
        await message.reply_text(f"🎉 Premium active until {until}. Thank you!")

    if kind == "gift":
        from core.botlog import esc
        giver = f"<a href=\"tg://user?id={payer}\">{esc(buyer_name) or payer}</a>"
        try:
            await client.send_message(
                target,
                f"<b>🎁 You received a gift!</b>\n\n<blockquote>{giver} gifted you <b>{BOT_NAME} Premium</b> "
                f"({_label(days)}).\n<b>📅 Valid until:</b> {until}</blockquote>\n<i>Enjoy unlimited saves & big files!</i>",
                message_effect_id=effect("heart"),
                reply_markup=InlineKeyboardMarkup([[Btn("📊 My Plan", callback_data="myplan_back_btn")]]),
            )
        except Exception:
            try:
                me = client.me or await client.get_me()
                await message.reply_text(
                    "ℹ️ Your friend hasn't started the bot yet, so I couldn't notify them. "
                    "Premium is already active – share this link so they can use it:\n"
                    f"https://t.me/{me.username}")
            except Exception:
                pass

    from core import botlog
    total_paid = await vdb.db["payments"].count_documents({"user": payer, "refunded": False})
    kind_txt = {"plan": "One-time plan", "gift": f"🎁 Gift → <code>{target}</code>",
                "sub": f"🔁 Subscription{f' (renewal #{renewals})' if renewal else ' (new)'}"}[kind]
    await botlog.event("StarsPayment", botlog.user_block(message.from_user, (
        f"<b>🧾 Type:</b> {kind_txt}\n"
        f"<b>💎 Plan:</b> {_label(days)} · <b>⭐ Stars:</b> {sp.total_amount}\n"
        f"<b>📅 Premium until:</b> {until}\n"
        f"<b>🧾 Charge ID:</b> <code>{sp.telegram_payment_charge_id}</code>\n"
        f"<b>🔁 Payments by this user:</b> {total_paid}")), client=client)


@Client.on_callback_query(filters.regex(r"^mysub_btn$"))
async def mysub_btn(client: Client, query: CallbackQuery):
    sub = await vdb.db["subscriptions"].find_one({"user": query.from_user.id, "active": True})
    await query.answer()
    await client.send_message(query.from_user.id, _sub_text(sub, await _current_expiry(query.from_user.id)),
                              reply_markup=_sub_kb(sub))


# ─────────────────────────── admin ───────────────────────────
@Client.on_message(filters.command("stars") & filters.user(ADMINS))
async def stars_stats(client: Client, message: Message):
    from core import stream
    async with stream.thinking(client, message.chat.id):
        col = vdb.db["payments"]
        agg = await col.aggregate([
            {"$match": {"refunded": False}},
            {"$group": {"_id": None, "n": {"$sum": 1}, "stars": {"$sum": "$stars"}}},
        ]).to_list(1)
        n, total = (agg[0]["n"], agg[0]["stars"]) if agg else (0, 0)
        subs = await vdb.db["subscriptions"].count_documents({"active": True, "canceled": {"$ne": True}})
        gifts = await col.count_documents({"kind": "gift", "refunded": False})
        try:
            balance = f"<code>{await client.get_stars_balance()}</code> ⭐"
        except Exception as e:
            balance = f"<i>unavailable ({type(e).__name__})</i>"
        last = await col.find().sort("date", -1).limit(10).to_list(10)
    icon = {"sub": "🔁", "gift": "🎁"}
    lines = [
        f"• {icon.get(p.get('kind'), '💎')} <code>{p['user']}</code> · {_label(p['days'])} · ⭐{p['stars']}"
        f"{' · ↩️ refunded' if p.get('refunded') else ''}\n  <code>{p['charge_id']}</code>"
        for p in last
    ]
    plans = " · ".join(f"{_label(d)} = ⭐{s}" for d, s in STARS_PLANS) or "disabled"
    sub_txt = f"⭐{SUBSCRIPTION_STARS}/month" if SUBSCRIPTION_STARS > 0 else "disabled"
    await message.reply_text(
        f"<b>⭐ Stars payments</b>\n\n<blockquote><b>💰 Live bot balance:</b> {balance}\n"
        f"Paid orders: <code>{n}</code>\nStars earned: <code>{total}</code>\n"
        f"Active subscriptions: <code>{subs}</code>\nGifts sold: <code>{gifts}</code>\n"
        f"Plans: {plans}\nSubscription: {sub_txt}</blockquote>\n\n<b>Latest:</b>\n" + ("\n".join(lines) or "—")
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
    pay = await vdb.db["payments"].find_one({"charge_id": charge}) or {}
    await vdb.db["payments"].update_one({"charge_id": charge}, {"$set": {"refunded": True}})
    # a refunded subscription charge ends the subscription
    await vdb.db["subscriptions"].update_many(
        {"user": uid, "$or": [{"charge_id": charge}, {"last_charge": charge}]},
        {"$set": {"active": False, "canceled": True}})
    from database.db import db as saver_db
    premium_user = pay.get("target", uid)
    await saver_db.remove_premium(premium_user)
    from core import botlog
    await botlog.event("Refund", f"<b>👤 User:</b> <code>{uid}</code>\n<b>🧾 Charge ID:</b> <code>{charge}</code>\n"
                                 f"<b>👮 By:</b> <code>{message.from_user.id}</code>", client=client)
    await message.reply_text(f"↩️ Refunded and premium removed for <code>{premium_user}</code>.")
    try:
        await client.send_message(uid, "↩️ <b>Your Stars payment was refunded.</b> Premium has been removed.")
    except Exception:
        pass

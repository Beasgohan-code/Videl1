"""
Paid plans on top of the saver Premium (all paid with Telegram Stars).

  🎬 Encoder Pro   priority encoder queue · more queued tasks · AV1 · 2-pass exact size ·
                   logo watermark · bigger /leech downloads
  🤖 Clone Plus    CLONE_PLUS_BOTS clone bots · idle auto-off after CLONE_PLUS_IDLE_DAYS
  🚀 Clone Pro     CLONE_PRO_BOTS clone bots · clones never switch off when idle

Premium users automatically get Encoder Pro. Admins/owners get everything.
Stored in the ``plans`` collection: {_id: "<uid>:<product>", user, product, until, since}.
Payload of a plan invoice: ``vx:<product>:<uid>:<ts>`` (validated in core.payments).
"""
import logging
import time
from datetime import datetime, timedelta

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, LabeledPrice, Message

import config
from core.db import vdb

log = logging.getLogger("videl.plans")

PRODUCTS = {
    "encpro": {"label": "🎬 Encoder Pro", "stars": config.ENC_PRO_STARS,
               "perks": ["⚡ Priority in the encoder queue", f"📋 Up to {config.ENC_MAX_TASKS_PRO} tasks at once",
                         "🆕 AV1 codec & 🎯 2-pass exact size", "🖼 Your own logo watermark",
                         f"🌐 /leech up to {config.LEECH_PRO_GB:g} GB"]},
    "cloneplus": {"label": "🤖 Clone Plus", "stars": config.CLONE_PLUS_STARS, "bots": config.CLONE_PLUS_BOTS,
                  "idle_days": config.CLONE_PLUS_IDLE_DAYS,
                  "perks": [f"🤖 Up to {config.CLONE_PLUS_BOTS} clone bots",
                            f"💤 Auto-off only after {config.CLONE_PLUS_IDLE_DAYS} idle days"]},
    "clonepro": {"label": "🚀 Clone Pro", "stars": config.CLONE_PRO_STARS, "bots": config.CLONE_PRO_BOTS,
                 "idle_days": 0,
                 "perks": [f"🚀 Up to {config.CLONE_PRO_BOTS} clone bots", "♾ Clones never switch off when idle"]},
}
CLONE_TIERS = ("clonepro", "cloneplus")          # best first

_cache: dict = {}                                # (uid, product) -> (expires_at, until | None)
_TTL = 30


def col():
    return vdb.db["plans"]


def utcnow() -> datetime:
    return datetime.utcnow().replace(microsecond=0)   # naive UTC (mongomock / Mongo friendly)


def sellable() -> dict:
    return {k: p for k, p in PRODUCTS.items() if p["stars"] > 0}


# ─────────────────────────── storage ───────────────────────────
async def until(uid: int, product: str):
    """Expiry (naive UTC datetime) of an active plan, else None."""
    key = (uid, product)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    doc = await col().find_one({"_id": f"{uid}:{product}"}) or {}
    exp = doc.get("until")
    if exp is not None and getattr(exp, "tzinfo", None):
        exp = exp.replace(tzinfo=None)
    value = exp if exp and exp > utcnow() else None
    _cache[key] = (time.time() + _TTL, value)
    return value


async def grant(uid: int, product: str, days: int = None) -> datetime:
    """Activate / extend a plan. Returns the new expiry."""
    if product not in PRODUCTS:
        raise ValueError(f"unknown product {product}")
    days = config.PLAN_DAYS if days is None else days
    current = await until(uid, product)
    base = current if current and current > utcnow() else utcnow()
    new = base + timedelta(days=days)
    await col().update_one({"_id": f"{uid}:{product}"},
                           {"$set": {"user": uid, "product": product, "until": new},
                            "$setOnInsert": {"since": utcnow()}}, upsert=True)
    _cache.pop((uid, product), None)
    return new


async def revoke(uid: int, product: str):
    await col().delete_one({"_id": f"{uid}:{product}"})
    _cache.pop((uid, product), None)


async def user_plans(uid: int) -> dict:
    return {p: exp for p in PRODUCTS if (exp := await until(uid, p))}


async def _premium(uid: int) -> bool:
    try:
        from database.db import db as saver_db
        return bool(await saver_db.check_premium(uid))
    except Exception:
        return False


# ─────────────────────────── entitlements ───────────────────────────
async def is_encoder_pro(uid: int) -> bool:
    if not uid:
        return False
    if uid in config.ADMINS:
        return True
    if await until(uid, "encpro"):
        return True
    return await _premium(uid)


async def clone_tier(uid: int):
    if uid in config.OWNERS:
        return "clonepro"
    for tier in CLONE_TIERS:
        if await until(uid, tier):
            return tier
    return None


async def clone_limit(uid: int) -> int:
    """How many clone bots this user may own."""
    base = config.MAX_BOTS_PER_USER
    if uid in config.OWNERS:
        return max(base, config.CLONE_PRO_BOTS, 999)
    tier = await clone_tier(uid)
    return max(base, PRODUCTS[tier]["bots"]) if tier else base


async def clone_idle_days(uid: int) -> int:
    """Idle days before a clone of this owner switches off (0 = never)."""
    tier = await clone_tier(uid)
    if tier == "clonepro":
        return 0
    base = int(config.CLONE_INACTIVE_DAYS or 0)
    if tier == "cloneplus":
        return 0 if base == 0 else max(base, PRODUCTS["cloneplus"]["idle_days"])
    return base


# ─────────────────────────── payments ───────────────────────────
def make_payload(product: str, uid: int) -> str:
    return f"vx:{product}:{uid}:{int(time.time())}"


async def send_invoice(client, chat_id: int, uid: int, product: str):
    p = PRODUCTS[product]
    await client.send_invoice(
        chat_id=chat_id, title=f"{config.BOT_NAME} {p['label'].split(' ', 1)[1]} · {config.PLAN_DAYS} days",
        description=" · ".join(x.split(" ", 1)[1] for x in p["perks"])[:255],
        currency="XTR", prices=[LabeledPrice(label=f"{p['label']} · {config.PLAN_DAYS} days", amount=p["stars"])],
        payload=make_payload(product, uid), start_parameter="plans")


def _fmt(exp) -> str:
    return f"{exp:%d %b %Y}" if exp else "—"


async def plans_view(uid: int):
    from core.style import hdr, quote, sc
    active = await user_plans(uid)
    premium = await _premium(uid)
    lines = [hdr("💳", "Plans", "Pay with Telegram Stars · activated instantly"), ""]
    lines.append(f"<b>💎 {sc('Premium')}</b>" + (" · ✅ " + sc("active") if premium else ""))
    lines.append(quote(sc("Unlimited saves, 4GB+ files, adult-file renaming, Encoder Pro included") + " · /premium"))
    for key, p in PRODUCTS.items():
        exp = active.get(key)
        state = f" · ✅ {sc('until')} {_fmt(exp)}" if exp else (" · ✅ " + sc("via Premium")
                                                             if key == "encpro" and premium else "")
        price = f" · ⭐ {p['stars']} / {config.PLAN_DAYS}d" if p["stars"] > 0 else ""
        lines.append(f"\n<b>{p['label']}</b>{price}{state}")
        lines.append(quote("\n".join(p["perks"])))
    kb = [[Btn("💎 Premium", callback_data="buy_premium")]]
    buy = [Btn(f"{p['label']} · ⭐{p['stars']}", callback_data=f"vx_buy:{k}") for k, p in sellable().items()]
    kb += [buy[i:i + 2] for i in range(0, len(buy), 2)]
    kb.append([Btn("⬅️ Back to Home", callback_data="start_btn")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


@Client.on_message(filters.command(["plans", "encpro", "cloneplans"]) & filters.private)
async def plans_cmd(client: Client, message: Message):
    text, kb = await plans_view(message.from_user.id)
    await message.reply_text(text, reply_markup=kb, disable_web_page_preview=True)


@Client.on_callback_query(filters.regex(r"^vx_(buy:\w+|home)$"))
async def plans_cb(client: Client, query: CallbackQuery):
    data = query.data
    if data == "vx_home":
        text, kb = await plans_view(query.from_user.id)
        from core.ui import smart_edit
        await query.answer()
        return await smart_edit(query.message, text, kb)
    product = data.split(":", 1)[1]
    if product not in sellable():
        return await query.answer("This plan isn't on sale right now.", show_alert=True)
    try:
        await send_invoice(client, query.from_user.id, query.from_user.id, product)
        await query.answer("⭐ Invoice sent below 👇")
    except Exception as e:
        log.warning(f"plan invoice failed: {e}")
        await query.answer(f"❌ Couldn't create the invoice: {e}"[:190], show_alert=True)

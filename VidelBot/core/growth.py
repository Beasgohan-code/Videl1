"""
Growth tools — referrals, redeem codes and a one-time free trial.

  /refer                       personal invite link + progress (t.me/<bot>?start=ref_<id>)
                               every REFERRAL_TARGET genuinely new users → +REFERRAL_REWARD_DAYS premium
  /redeem CODE                 redeem a premium code
  /trial                       TRIAL_DAYS of premium, once per account
  /gencode <days> [count] [uses]   (admins) create codes – 0 days = lifetime
  /codes · /delcode CODE           (admins) list / delete codes

Only users who have never talked to the bot before count as referrals (the
hook runs from the #NewUser middleware), self-referrals are ignored and each
user can be referred once.
"""
import logging
import secrets
import string
from datetime import datetime, timezone
from urllib.parse import quote

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from config import ADMINS, BOT_NAME, REFERRAL_REWARD_DAYS, REFERRAL_TARGET, TRIAL_DAYS
from core.db import vdb
from core.ui import copy_button, effect

log = logging.getLogger("videl.growth")
CODE_ALPHABET = string.ascii_uppercase + string.digits


def _label(days: int) -> str:
    return "Lifetime" if days == 0 else f"{days} day{'s' if days != 1 else ''}"


async def _username(client) -> str:
    me = client.me or await client.get_me()
    return me.username


def referral_link(username: str, user_id: int) -> str:
    return f"https://t.me/{username}?start=ref_{user_id}"


# ═══════════════════════════ referrals ═══════════════════════════
async def on_new_user(client, user, text: str) -> bool:
    """Called for brand-new users. Credits the referrer of a ref_<id> deep link."""
    parts = (text or "").split()
    if len(parts) < 2 or not parts[0].startswith("/start") or not parts[1].lower().startswith("ref_"):
        return False
    try:
        ref = int(parts[1][4:])
    except ValueError:
        return False
    if ref == user.id or not await vdb.get_user(ref):
        return False
    res = await vdb.users.update_one({"id": user.id, "referred_by": {"$exists": False}},
                                     {"$set": {"referred_by": ref}})
    if not res.modified_count:
        return False
    await vdb.users.update_one({"id": ref}, {"$inc": {"referrals": 1}})
    count = (await vdb.get_user(ref) or {}).get("referrals", 1)

    from core import botlog
    rewarded, expiry = False, None
    if REFERRAL_TARGET > 0 and REFERRAL_REWARD_DAYS > 0 and count % REFERRAL_TARGET == 0:
        from core.payments import grant_premium, until_text
        expiry = await grant_premium(ref, REFERRAL_REWARD_DAYS)
        await vdb.users.update_one({"id": ref}, {"$inc": {"referral_rewards": 1}})
        rewarded = True
    name = botlog.esc(user.first_name or str(user.id))
    try:
        if rewarded:
            await client.send_message(
                ref, f"<b>🎉 Referral reward unlocked!</b>\n\n<blockquote><b>{name}</b> joined with your link.\n"
                     f"<b>👥 Referrals:</b> {count}\n<b>💎 Reward:</b> +{_label(REFERRAL_REWARD_DAYS)} Premium\n"
                     f"<b>📅 Premium until:</b> {until_text(expiry)}</blockquote>",
                message_effect_id=effect("party"))
        else:
            left = (REFERRAL_TARGET - count % REFERRAL_TARGET) if REFERRAL_TARGET > 0 else 0
            extra = f"\n<i>{left} more for +{_label(REFERRAL_REWARD_DAYS)} Premium!</i>" if left and REFERRAL_REWARD_DAYS else ""
            await client.send_message(ref, f"👥 <b>{name}</b> joined with your referral link! Total: <b>{count}</b>{extra}")
    except Exception:
        pass
    await botlog.event("Referral", botlog.user_block(user, (
        f"<b>🤝 Referred by:</b> <code>{ref}</code> (total {count})"
        + (f"\n<b>🎁 Reward:</b> +{_label(REFERRAL_REWARD_DAYS)} premium" if rewarded else ""))), client=client)
    return True


def _progress(count: int) -> str:
    if REFERRAL_TARGET <= 0:
        return ""
    done = count % REFERRAL_TARGET
    return "🟩" * done + "⬜" * (REFERRAL_TARGET - done) + f"  {done}/{REFERRAL_TARGET}"


async def refer_view(client, user_id: int):
    link = referral_link(await _username(client), user_id)
    doc = await vdb.get_user(user_id) or {}
    count, rewards = doc.get("referrals", 0), doc.get("referral_rewards", 0)
    reward_line = (f"🎁 Every <b>{REFERRAL_TARGET}</b> friends who join = <b>+{_label(REFERRAL_REWARD_DAYS)} Premium</b>\n"
                   if REFERRAL_TARGET > 0 and REFERRAL_REWARD_DAYS > 0 else "")
    from core.design import card, footer, heading, page, section
    text = page(
        heading("🤝", "Refer &amp; Earn", "Invite friends — get Premium for free."),
        card(reward_line.rstrip("\n"),
             f"👥 <b>Your referrals:</b> {count}\n🏆 <b>Rewards earned:</b> {rewards}",
             _progress(count)),
        section("🔗", "Your link", f"<code>{link}</code>"),
        footer("Tap 📤 Share to send it to a chat, or 📋 Copy it."),
    )
    share = f"https://t.me/share/url?url={quote(link)}&text={quote(f'Try {BOT_NAME} – save restricted content, encode videos & more!')}"
    kb = InlineKeyboardMarkup([
        [Btn("📤 Share link", url=share), copy_button("📋 Copy link", link)],
        [Btn("🏠 Home", callback_data="start_btn")],
    ])
    return text, kb


@Client.on_message(filters.command(["refer", "referral", "invite"]) & filters.private)
async def refer_cmd(client: Client, message: Message):
    text, kb = await refer_view(client, message.from_user.id)
    await message.reply_text(text, reply_markup=kb, disable_web_page_preview=True)


@Client.on_callback_query(filters.regex(r"^refer_btn$"))
async def refer_btn(client: Client, query: CallbackQuery):
    from core.ui import smart_edit
    text, kb = await refer_view(client, query.from_user.id)
    await smart_edit(query.message, text, kb)
    await query.answer()


# ═══════════════════════════ redeem codes ═══════════════════════════
def new_code(length: int = 10) -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
    return f"VIDEL-{raw[:5]}-{raw[5:]}"


async def create_codes(days: int, count: int = 1, uses: int = 1, by: int = 0) -> list:
    codes = []
    for _ in range(count):
        code = new_code()
        await vdb.db["codes"].insert_one({"code": code, "days": days, "uses_left": uses, "uses": uses,
                                          "used_by": [], "created_by": by,
                                          "created_at": datetime.now(timezone.utc)})
        codes.append(code)
    return codes


async def redeem(user_id: int, code: str, name: str = ""):
    """Returns (ok, message_or_expiry, days)."""
    code = code.strip().upper()
    col = vdb.db["codes"]
    doc = await col.find_one({"code": code})
    if not doc:
        return False, "❌ Invalid code.", 0
    if user_id in doc.get("used_by", []):
        return False, "⚠️ You already redeemed this code.", 0
    # atomic claim of one use
    res = await col.update_one({"code": code, "uses_left": {"$gt": 0}, "used_by": {"$ne": user_id}},
                               {"$inc": {"uses_left": -1}, "$push": {"used_by": user_id}})
    if not res.modified_count:
        return False, "⌛ This code has been fully used.", 0
    from core.payments import grant_premium
    expiry = await grant_premium(user_id, doc["days"], name)
    return True, expiry, doc["days"]


@Client.on_message(filters.command("redeem") & filters.private)
async def redeem_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("<b>🎟 Redeem a code</b>\n\nUsage: <code>/redeem VIDEL-XXXXX-XXXXX</code>")
    ok, result, days = await redeem(message.from_user.id, message.command[1], message.from_user.first_name)
    if not ok:
        return await message.reply_text(result)
    from core.payments import until_text
    await message.reply_text(
        f"<b>🎟 Code redeemed!</b>\n\n<blockquote><b>💎 Premium:</b> +{_label(days)}\n"
        f"<b>📅 Valid until:</b> {until_text(result)}</blockquote>",
        message_effect_id=effect("party"),
        reply_markup=InlineKeyboardMarkup([[Btn("📊 My Plan", callback_data="myplan_back_btn")]]))
    from core import botlog
    await botlog.event("Redeem", botlog.user_block(message.from_user, (
        f"<b>🎟 Code:</b> <code>{botlog.esc(message.command[1].upper())}</code>\n"
        f"<b>💎 Premium:</b> +{_label(days)} → {until_text(result)}")), client=client)


@Client.on_message(filters.command("gencode") & filters.user(ADMINS))
async def gencode_cmd(client: Client, message: Message):
    try:
        days = int(message.command[1])
        count = int(message.command[2]) if len(message.command) > 2 else 1
        uses = int(message.command[3]) if len(message.command) > 3 else 1
        if days < 0 or not 1 <= count <= 50 or not 1 <= uses <= 100000:
            raise ValueError
    except (IndexError, ValueError):
        return await message.reply_text(
            "<b>🎟 Generate redeem codes</b>\n\n<code>/gencode &lt;days&gt; [count] [uses]</code>\n"
            "• days: premium days (0 = lifetime)\n• count: codes to create (1-50)\n• uses: redemptions per code\n\n"
            "Example: <code>/gencode 30 5</code> → five 30-day codes")
    codes = await create_codes(days, count, uses, message.from_user.id)
    body = "\n".join(f"<code>{c}</code>" for c in codes)
    kb = InlineKeyboardMarkup([[copy_button("📋 Copy code", codes[0])]]) if count == 1 else None
    await message.reply_text(f"<b>🎟 {count} code(s) · {_label(days)} · {uses} use(s) each</b>\n\n{body}\n\n"
                             f"<i>Users redeem with</i> <code>/redeem CODE</code>", reply_markup=kb)
    from core import botlog
    await botlog.event("CodesCreated", botlog.user_block(message.from_user,
                       f"<b>🎟 Codes:</b> {count} × {_label(days)} ({uses} use(s) each)"), client=client)


@Client.on_message(filters.command("codes") & filters.user(ADMINS))
async def codes_cmd(client: Client, message: Message):
    docs = await vdb.db["codes"].find({"uses_left": {"$gt": 0}}).sort("created_at", -1).limit(30).to_list(30)
    if not docs:
        return await message.reply_text("🎟 No active codes. Create some with /gencode.")
    lines = [f"• <code>{d['code']}</code> · {_label(d['days'])} · {d['uses_left']}/{d.get('uses', 1)} left" for d in docs]
    await message.reply_text("<b>🎟 Active redeem codes</b>\n\n" + "\n".join(lines))


@Client.on_message(filters.command("delcode") & filters.user(ADMINS))
async def delcode_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("Usage: <code>/delcode CODE</code>")
    res = await vdb.db["codes"].delete_one({"code": message.command[1].upper()})
    await message.reply_text("🗑 Code deleted." if res.deleted_count else "❌ Code not found.")


# ═══════════════════════════ free trial ═══════════════════════════
async def start_trial(user_id: int, name: str = ""):
    """Returns (ok, message_or_expiry)."""
    if TRIAL_DAYS <= 0:
        return False, "🆓 Free trials are disabled on this bot."
    from database.db import db as saver_db
    if await saver_db.check_premium(user_id):
        return False, "💎 You already have Premium!"
    res = await vdb.users.update_one({"id": user_id, "trial_used": {"$ne": True}},
                                     {"$set": {"trial_used": True, "trial_at": datetime.now(timezone.utc)}})
    if not res.matched_count:
        exists = await vdb.get_user(user_id)
        if exists:
            return False, "⚠️ You already used your free trial. Get Premium with /buy."
        await vdb.users.update_one({"id": user_id}, {"$set": {"trial_used": True,
                                                              "trial_at": datetime.now(timezone.utc)}}, upsert=True)
    from core.payments import grant_premium
    return True, await grant_premium(user_id, TRIAL_DAYS, name)


async def _trial_reply(client, user, send):
    ok, result = await start_trial(user.id, user.first_name)
    if not ok:
        return await send(result)
    from core.payments import until_text
    await send(f"<b>🆓 Free trial activated!</b>\n\n<blockquote><b>💎 Premium:</b> {_label(TRIAL_DAYS)}\n"
               f"<b>📅 Valid until:</b> {until_text(result)}</blockquote>\n<i>Enjoy – upgrade anytime with /buy.</i>")
    from core import botlog
    await botlog.event("Trial", botlog.user_block(user, f"<b>🆓 Trial:</b> {_label(TRIAL_DAYS)}"), client=client)


@Client.on_message(filters.command("trial") & filters.private)
async def trial_cmd(client: Client, message: Message):
    await _trial_reply(client, message.from_user,
                       lambda t: message.reply_text(t, message_effect_id=effect("party") if "activated" in t else None))


@Client.on_callback_query(filters.regex(r"^trial_btn$"))
async def trial_btn(client: Client, query: CallbackQuery):
    await query.answer()
    await _trial_reply(client, query.from_user, lambda t: client.send_message(query.from_user.id, t))

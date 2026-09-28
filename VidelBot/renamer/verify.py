"""
Auto-Rename token verification (two shorteners, from the Auto-Rename bot):

• admins enable shortener 1 and/or 2 in /verify_settings (site + API key, AdLinkFly-style)
• a user who is not verified gets a short link → t.me/<bot>?start=rnv_<token>
• coming back too fast (< min_seconds) = bypass → token burned
• verification lasts `hours` (24 by default); Premium users and admins never need it
"""
import html
import logging
import random
import secrets

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import config
from core.ui import smart_edit
from renamer import store

log = logging.getLogger("videl.rename.verify")

# admin text-input state: uid -> {"kind": "vset", "slot": "s1", "step": "site"|"api", "site": str}
_input: dict[int, dict] = {}


async def is_exempt(uid: int) -> bool:
    if uid in config.ADMINS:
        return True
    try:
        from database.db import db as saver_db
        return bool(await saver_db.check_premium(uid))
    except Exception:
        return False


async def needs_verification(uid: int) -> bool:
    cfg = await store.verify_settings()
    if not store.active_shorteners(cfg):
        return False
    if await is_exempt(uid):
        return False
    return not await store.verified_until(uid, int(cfg.get("hours") or 24))


async def make_link(client, uid: int):
    """→ (short_url, None) or (None, error_text)"""
    from filestore.utils.shortener import shorten_url
    cfg = await store.verify_settings()
    slots = store.active_shorteners(cfg)
    if not slots:
        return None, "verification is off"
    me = client.me or await client.get_me()
    token = secrets.token_hex(6)
    target = f"https://t.me/{me.username}?start=rnv_{token}"
    random.shuffle(slots)
    for slot in slots:
        s = cfg[slot]
        short = await shorten_url(target, s["api"], s["site"], "adlinkfly")
        if short and short != target:          # shorten_url returns the input on failure
            await store.new_token(uid, slot, token)
            return short, None
        log.warning(f"verify shortener {slot} ({s['site']}) failed")
    return None, "shortener error"


def _plans_row():
    return [Btn("💎 Skip with Premium", callback_data="buy_premium")]


async def send_prompt(client, message: Message):
    uid = message.from_user.id
    cfg = await store.verify_settings()
    link, err = await make_link(client, uid)
    if not link:
        return await message.reply_text("⚠️ <b>Couldn't create your verification link right now.</b>\n"
                                        "Please try again later.", quote=True)
    kb = [[Btn("✅ Verify now", url=link)]]
    if cfg.get("tutorial"):
        kb.append([Btn("📖 How to verify", url=cfg["tutorial"])])
    kb.append(_plans_row())
    hours = int(cfg.get("hours") or 24)
    await message.reply_text(
        f"👋 Hey {message.from_user.mention},\n\n‼️ <b>You're not verified today.</b>\n\n"
        f"Verify once to use <b>Auto-Rename</b> for the next <b>{hours} hours</b>.\n"
        "<blockquote>Tap the button, finish the short link and you'll land back here automatically.</blockquote>",
        reply_markup=InlineKeyboardMarkup(kb), quote=True, disable_web_page_preview=True)


async def gate(client, message: Message) -> bool:
    """True → go ahead; False → a verification prompt was sent."""
    if not await needs_verification(message.from_user.id):
        return True
    await send_prompt(client, message)
    return False


async def handle_start_token(client, message: Message, token: str):
    """/start rnv_<token> – the user came back from the short link."""
    uid = message.from_user.id
    cfg = await store.verify_settings()
    doc = await store.get_token_doc(uid)
    if not doc.get("token") or not secrets.compare_digest(str(doc["token"]), token.lower()):
        return await message.reply_text("❌ <b>Invalid or expired verification link.</b>\n"
                                        "Use /verify to get a new one.")
    at = store.aware(doc.get("token_at"))
    age = (store.utcnow() - at).total_seconds() if at else 0
    hours = int(cfg.get("hours") or 24)
    if age > hours * 3600:
        await store.clear_token(uid)
        return await message.reply_text("⌛ <b>This verification link expired.</b> Use /verify to get a new one.")
    min_s = int(cfg.get("min_seconds") or 0)
    if min_s and age < min_s:
        await store.clear_token(uid)
        return await message.reply_text(
            f"⚠️ <b>Bypass detected!</b>\n\nYou finished the link in {int(age)} seconds.\n"
            "Please complete it properly – use /verify for a new link.")
    await store.mark_verified(uid, doc.get("shortener", ""))
    from core import botlog
    await botlog.event("RenameVerified", botlog.user_block(message.from_user, f"<b>⏱ Took:</b> {int(age)}s"),
                       client=client)
    m, s = divmod(int(age), 60)
    await message.reply_text(
        f"✅ <b>Verification successful!</b>\n\n›› Valid for <b>{hours} hours</b>.\n⏱ Time taken: {m}m {s}s\n\n"
        "Now send me your files to auto-rename them.",
        reply_markup=InlineKeyboardMarkup([_plans_row()]))


# ─────────────────────────── /verify ───────────────────────────
@Client.on_message(filters.command("verify") & filters.private)
async def verify_cmd(client: Client, message: Message):
    uid = message.from_user.id
    cfg = await store.verify_settings()
    if not store.active_shorteners(cfg):
        return await message.reply_text("✅ Verification is not required right now – just send your files!")
    if await is_exempt(uid):
        return await message.reply_text("✨ <b>You have Premium access!</b>\nPremium users don't need to verify.")
    until = await store.verified_until(uid, int(cfg.get("hours") or 24))
    if until:
        left = int((until - store.utcnow()).total_seconds())
        from core import rich
        from core.rich import Doc
        doc = Doc("✅", "You are verified!").table([
            ("📶 Status", "🟢 Verified"),
            ("⏰ Time left", f"{left // 3600}h {left % 3600 // 60}m"),
            ("🔁 Valid for", f"{int(cfg.get('hours') or 24)} h per verification"),
        ], header=("Item", "Value"))
        doc.footer("Premium users never need to verify.")
        return await rich.reply(message, doc, reply_markup=InlineKeyboardMarkup([_plans_row()]))
    await send_prompt(client, message)


# ─────────────────────────── /verify_settings (admins) ───────────────────────────
def _mask(key: str) -> str:
    return (key[:4] + "…" + key[-3:]) if len(key or "") > 8 else ("set" if key else "not set")


async def settings_view():
    cfg = await store.verify_settings()
    lines = ["<b>🔐 Rename verification</b>\n"]
    for slot, label in (("s1", "Verify 1"), ("s2", "Verify 2")):
        s = cfg[slot]
        state = "🟢 on" if s.get("on") else "🔴 off"
        lines.append(f"<b>{label}:</b> {state}\n  🌐 <code>{html.escape(s.get('site') or 'not set', quote=False)}</code> · 🔑 {_mask(s.get('api'))}")
    lines.append(f"\n<b>⏰ Valid for:</b> {cfg['hours']} h · <b>🛡 Bypass limit:</b> {cfg['min_seconds']} s")
    lines.append("<i>Premium users &amp; admins never need to verify.</i>")
    kb = [
        [Btn(("🟢 " if cfg["s1"].get("on") else "🔴 ") + "Verify 1", callback_data="rnv:tog:s1"),
         Btn(("🟢 " if cfg["s2"].get("on") else "🔴 ") + "Verify 2", callback_data="rnv:tog:s2")],
        [Btn("✏️ Set shortener 1", callback_data="rnv:set:s1"), Btn("✏️ Set shortener 2", callback_data="rnv:set:s2")],
        [Btn("⏰ 6h", callback_data="rnv:hours:6"), Btn("12h", callback_data="rnv:hours:12"),
         Btn("24h", callback_data="rnv:hours:24"), Btn("48h", callback_data="rnv:hours:48")],
        [Btn("📊 Counts", callback_data="rnv:counts"), Btn("❌ Close", callback_data="close_btn")],
    ]
    return "\n".join(lines), InlineKeyboardMarkup(kb)


@Client.on_message(filters.command(["verify_settings", "verifysettings"]) & filters.user(config.ADMINS))
async def verify_settings_cmd(client: Client, message: Message):
    text, kb = await settings_view()
    await message.reply_text(text, reply_markup=kb, disable_web_page_preview=True)


async def counts_view():
    c = await store.verify_counts()
    text = ("<b>📊 Verification statistics</b>\n\n"
            f"👥 Today: <b>{c['today']}</b>\n📊 Yesterday: <b>{c['yesterday']}</b>\n"
            f"📅 This week: <b>{c['week']}</b>\n📆 This month: <b>{c['month']}</b>\n"
            f"📋 Last month: <b>{c['last_month']}</b>")
    kb = InlineKeyboardMarkup([[Btn("🔄 Refresh", callback_data="rnv:counts"),
                                Btn("‹ Back", callback_data="rnv:home")]])
    return text, kb


@Client.on_callback_query(filters.regex(r"^rnv:"))
async def verify_settings_cb(client: Client, query: CallbackQuery):
    if query.from_user.id not in config.ADMINS:
        return await query.answer("👮 Admins only.", show_alert=True)
    parts = query.data.split(":")
    action = parts[1]
    cfg = await store.verify_settings()
    if action == "tog":
        slot = parts[2]
        if not cfg[slot].get("on") and not (cfg[slot].get("site") and cfg[slot].get("api")):
            return await query.answer("Set the shortener site and API key first.", show_alert=True)
        cfg[slot]["on"] = not cfg[slot].get("on")
        await store.save_verify_settings(cfg)
        await query.answer(f"{'Enabled' if cfg[slot]['on'] else 'Disabled'}")
    elif action == "hours":
        cfg["hours"] = int(parts[2])
        await store.save_verify_settings(cfg)
        await query.answer(f"Valid for {cfg['hours']} hours")
    elif action == "set":
        slot = parts[2]
        _input[query.from_user.id] = {"kind": "vset", "slot": slot, "step": "site"}
        await query.answer()
        return await smart_edit(
            query.message, f"<b>✏️ Shortener {slot[1]}</b>\n\nSend the shortener <b>domain</b>, e.g. <code>gplinks.com</code>\n\n"
            "<i>/cancel to abort</i>")
    elif action == "counts":
        text, kb = await counts_view()
        await query.answer()
        return await smart_edit(query.message, text, kb)
    else:
        await query.answer()
    text, kb = await settings_view()
    await smart_edit(query.message, text, kb)


async def handle_input(client, message: Message, state: dict) -> bool:
    """Admin typed a shortener site / API key. Returns True when consumed."""
    uid = message.from_user.id
    text = (message.text or "").strip()
    if not text:
        return False
    if state["step"] == "site":
        site = text.replace("https://", "").replace("http://", "").strip("/ ").split("/")[0]
        if "." not in site or " " in site:
            await message.reply_text("❌ That doesn't look like a domain. Send e.g. <code>gplinks.com</code>")
            return True
        state.update(step="api", site=site)
        await message.reply_text(f"🌐 Site: <code>{html.escape(site, quote=False)}</code>\n\nNow send the <b>API key</b>.\n<i>/cancel to abort</i>")
        return True
    cfg = await store.verify_settings()
    slot = state["slot"]
    cfg[slot].update(site=state["site"], api=text.split()[0], on=True)
    await store.save_verify_settings(cfg)
    _input.pop(uid, None)
    try:
        await message.delete()          # don't leave the API key in the chat
    except Exception:
        pass
    view, kb = await settings_view()
    await message.reply_text(f"✅ <b>Shortener {slot[1]} saved and enabled.</b>\n\n" + view, reply_markup=kb,
                             disable_web_page_preview=True)
    return True

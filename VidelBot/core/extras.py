"""
Bot API 10.x features through aiogram (see core/botapi.py).

    /guide                         rich message: headings, plan table, FAQ details (everyone)
    /botapi [test]                 bridge health, getMe capability flags, live latency (owner)
    /giftpremium <user> <3|6|12>   gift Telegram Premium paid with the bot's Stars (owner)
    /gifts                         list Telegram gifts the bot can send (owner)
    /sendgift <user> <id> [text]   send one of those gifts (owner)

Everything degrades gracefully: without aiogram / Bot API access the commands
explain why and fall back to classic HTML where it makes sense.
"""
import html
import logging
import time

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton as Btn
from pyrogram.types import InlineKeyboardMarkup, Message

from config import BOT_NAME, OWNERS, STARS_PLANS, SUBSCRIPTION_STARS, TRIAL_DAYS
from core import botapi, texts
from core.ui import HTML, group_reply

log = logging.getLogger("videl.extras")

# Bot API: Premium gifts cost a fixed amount of Stars per duration.
PREMIUM_GIFT_PRICES = {3: 1000, 6: 1500, 12: 2500}


def _label(days: int) -> str:
    return "Lifetime" if days == 0 else f"{days} days"


async def _resolve_user(client, arg: str):
    """@username / id → (id, mention html)."""
    try:
        u = await client.get_users(int(arg) if arg.lstrip("-").isdigit() else arg)
        return u.id, u.mention
    except Exception:
        return (int(arg), f"<code>{arg}</code>") if arg.lstrip("-").isdigit() else (None, None)


# ─────────────────────────── /guide (rich message) ───────────────────────────
def guide_blocks(bot_username: str) -> list:
    """Rich-message layout for /guide (Bot API sendRichMessage)."""
    from aiogram import types as at
    P = lambda *t: at.InputRichBlockParagraph(text=list(t) if len(t) > 1 else t[0])  # noqa: E731
    B = lambda t: at.RichTextBold(text=t)  # noqa: E731

    def cell(text, header=False, align="left"):
        return at.RichBlockTableCell(text=text, is_header=header or None, align=align, valign="middle")

    def bullets(*items):
        return at.InputRichBlockList(items=[at.InputRichBlockListItem(blocks=[P(i)]) for i in items])

    plan_rows = [[cell("Plan", True), cell("Price", True, "right")]]
    plan_rows += [[cell(_label(d)), cell(f"⭐ {s}", align="right")] for d, s in STARS_PLANS]
    if SUBSCRIPTION_STARS > 0:
        plan_rows.append([cell("Monthly (auto-renew)"), cell(f"⭐ {SUBSCRIPTION_STARS}/mo", align="right")])

    link = f"https://t.me/{bot_username}"
    blocks = [
        at.InputRichBlockSectionHeading(text=f"📖 {BOT_NAME} · Guide", size=1),
        P("One bot for ", B("saving restricted content"), ", ", B("encoding videos"),
          " and running your own ", B("FileStore clone bots"), "."),
        at.InputRichBlockDivider(),
        at.InputRichBlockSectionHeading(text="🚀 Quick start", size=2),
        bullets("/login – connect your account for private channels",
                "Send any t.me link – Videl saves the post (or /batch for many)",
                "Send a video – pick an encoder preset",
                "/clone – create your own FileStore bot"),
        at.InputRichBlockDetails(summary="📥 Saving content", blocks=[
            bullets("/batch · /cancel – bulk save with progress",
                    "/settings – caption, thumbnail, rename, chat & word filters",
                    "/dl · /ddl – download direct links, 30+ hosts supported")]),
        at.InputRichBlockDetails(summary="🎞 Video encoder", blocks=[
            bullets("/vset – codec, CRF, preset, resolution, audio",
                    "/queue · /status – live queue & progress",
                    "Hardsub, watermark, audio-track picker")]),
        at.InputRichBlockDetails(summary="🤖 FileStore clone bots", blocks=[
            bullets("⚡ One-tap creation (no BotFather) when available",
                    "Force-sub, auto-delete, shortener, custom start & caption",
                    "Backup / restore, maintenance mode, transfer ownership")]),
    ]
    if len(plan_rows) > 1:
        blocks += [
            at.InputRichBlockSectionHeading(text="💎 Premium plans", size=2),
            at.InputRichBlockTable(cells=plan_rows, is_bordered=True, is_striped=True,
                                   caption="Paid securely with Telegram Stars"),
        ]
    faq = [bullets("Premium removes daily limits and unlocks bigger files",
                   "Cancel a subscription anytime with /mysub",
                   "Gift Premium to a friend with /gift")]
    if TRIAL_DAYS:
        faq.append(P(f"New here? /trial gives {TRIAL_DAYS} day(s) free."))
    blocks += [
        at.InputRichBlockDetails(summary="❓ FAQ", blocks=faq),
        at.InputRichBlockButtons(buttons=[
            at.RichMessageButton(text="💎 Premium", url=f"{link}?start=premium", style="success"),
            at.RichMessageButton(text="🤖 Clone bot", url=f"{link}?start=clone", style="primary"),
            at.RichMessageButton(text="🆘 Help", url=f"{link}?start=help"),
        ]),
        at.InputRichBlockFooter(text=f"{BOT_NAME} · /help for every command"),
    ]
    return blocks


@Client.on_message(filters.command(["guide", "tutorial"]))
async def guide_cmd(client: Client, message: Message):
    me = client.me or await client.get_me()
    if message.chat.id < 0:
        kb = InlineKeyboardMarkup([[Btn("📖 Open the guide", url=f"https://t.me/{me.username}?start=guide")]])
        return await group_reply(message, f"📖 The <b>{BOT_NAME}</b> guide opens in private.", kb)
    await send_guide(client, message.chat.id, me.username)


async def send_guide(client, chat_id: int, bot_username: str):
    if botapi.enabled():
        try:
            if await botapi.send_rich(chat_id, guide_blocks(bot_username)):
                return True
        except Exception as e:  # building blocks failed → classic
            log.warning(f"rich guide failed: {e}")
    await client.send_message(chat_id, texts.HELP_TXT, parse_mode=HTML, disable_web_page_preview=True)
    return False


# ─────────────────────────── /botapi (owner) ───────────────────────────
_FLAGS = [
    ("can_join_groups", "Join groups"), ("can_read_all_group_messages", "Privacy mode off"),
    ("supports_inline_queries", "Inline mode"), ("can_connect_to_business", "Business"),
    ("has_main_web_app", "Main Mini App"), ("can_manage_bots", "Manage bots"),
    ("has_topics_enabled", "Private topics"), ("allows_users_to_create_topics", "User topics"),
]


@Client.on_message(filters.command(["botapi", "aiogram"]) & filters.user(OWNERS))
async def botapi_cmd(client: Client, message: Message):
    if not botapi.enabled():
        return await message.reply_text(
            f"<b>🛰 Bot API bridge</b>\n\n<blockquote>Status: {await botapi.status_line()}</blockquote>\n"
            "Set <code>AIOGRAM_ENABLED=True</code> (and install <code>aiogram</code>) to enable coloured buttons, "
            "ephemeral replies, rich messages, managed bots and more.")
    t = time.perf_counter()
    me = await botapi.me(refresh=True)
    ms = (time.perf_counter() - t) * 1000
    if me is None:
        return await message.reply_text("<b>🛰 Bot API unreachable</b> – Videl keeps working over MTProto.")
    flags = "\n".join(f"{'✅' if getattr(me, k, False) else '▫️'} {label}" for k, label in _FLAGS)
    from config import BOT_API_URL, COLORED_BUTTONS, EPHEMERAL_REPLIES, MANAGED_BOTS
    from core.payments import star_balance
    bal = await star_balance(client)
    text = (
        "<b>🛰 Bot API bridge</b>\n\n<blockquote>"
        f"<b>Library:</b> {botapi.version()}\n"
        f"<b>Server:</b> {html.escape(BOT_API_URL) if BOT_API_URL else 'api.telegram.org'}\n"
        f"<b>getMe latency:</b> <code>{ms:.0f} ms</code>\n"
        f"<b>Stars balance:</b> <code>{bal if bal is not None else '?'}</code> ⭐</blockquote>\n"
        f"<b>Capabilities</b>\n<blockquote>{flags}</blockquote>\n"
        "<b>Features</b>\n<blockquote>"
        f"{'✅' if COLORED_BUTTONS else '▫️'} Coloured buttons\n"
        f"{'✅' if EPHEMERAL_REPLIES else '▫️'} Ephemeral group replies\n"
        f"{'✅' if MANAGED_BOTS and getattr(me, 'can_manage_bots', False) else '▫️'} One-tap clone bots"
        f"{'' if getattr(me, 'can_manage_bots', False) else ' (enable Bot Management Mode in @BotFather)'}\n"
        "✅ Rich /guide · ✅ Drafts · ✅ Stars ledger · ✅ Premium gifts</blockquote>\n"
        "<i>Updates still arrive over MTProto; aiogram only sends.</i>"
    )
    kb = InlineKeyboardMarkup([
        [Btn("✅ Success", callback_data="botapi_demo"), Btn("❌ Danger", callback_data="botapi_demo")],
        [Btn("🚀 Primary", callback_data="botapi_demo"), Btn("Default", callback_data="botapi_demo")],
    ])
    from core.ui import send_with_preview
    await send_with_preview(client, message.chat.id, text, kb, reply_to=message.id)


@Client.on_callback_query(filters.regex(r"^botapi_demo$") & filters.user(OWNERS))
async def botapi_demo(client, query):
    await query.answer("🎨 Button colours come from the Bot API 'style' field.", show_alert=False)


# ─────────────────────────── Premium & gifts (owner) ───────────────────────────
@Client.on_message(filters.command("giftpremium") & filters.user(OWNERS))
async def giftpremium_cmd(client: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2 or not args[1].isdigit() or int(args[1]) not in PREMIUM_GIFT_PRICES:
        prices = " · ".join(f"{m} mo = ⭐{s}" for m, s in PREMIUM_GIFT_PRICES.items())
        return await message.reply_text(
            "<b>🎁 Gift Telegram Premium</b> (paid from the bot's Stars)\n\n"
            "<code>/giftpremium &lt;user_id|@username&gt; &lt;3|6|12&gt; [message]</code>\n\n" + prices)
    if not botapi.enabled():
        return await message.reply_text("❌ Needs the Bot API bridge (<code>AIOGRAM_ENABLED=True</code>).")
    uid, mention = await _resolve_user(client, args[0])
    if not uid:
        return await message.reply_text("❌ User not found.")
    months = int(args[1])
    note = " ".join(args[2:])[:128] or None
    try:
        await botapi.call(lambda b: b.gift_premium_subscription(
            user_id=uid, month_count=months, star_count=PREMIUM_GIFT_PRICES[months], text=note))
    except Exception as e:
        return await message.reply_text(f"❌ Telegram refused: <code>{html.escape(str(e))[:300]}</code>")
    await message.reply_text(f"🎁 Gifted <b>{months} months</b> of Telegram Premium to {mention}!")
    from core import botlog
    await botlog.event("PremiumGift", botlog.user_block(message.from_user,
                       f"🎁 Telegram Premium {months} mo → <code>{uid}</code> (⭐{PREMIUM_GIFT_PRICES[months]})"),
                       client=client)


@Client.on_message(filters.command("gifts") & filters.user(OWNERS))
async def gifts_cmd(client: Client, message: Message):
    if not botapi.enabled():
        return await message.reply_text("❌ Needs the Bot API bridge (<code>AIOGRAM_ENABLED=True</code>).")
    got = await botapi.try_call(lambda b: b.get_available_gifts())
    gifts = getattr(got, "gifts", None) or []
    if not gifts:
        return await message.reply_text("🎁 No gifts available right now.")
    lines = []
    for g in gifts[:40]:
        emoji = getattr(getattr(g, "sticker", None), "emoji", "") or "🎁"
        left = f" · {g.remaining_count} left" if g.remaining_count is not None else ""
        lines.append(f"{emoji} ⭐{g.star_count}{left}\n<code>{g.id}</code>")
    await message.reply_text(
        f"<b>🎁 Telegram gifts ({len(gifts)})</b>\n\n" + "\n".join(lines)
        + "\n\n<b>Send:</b> <code>/sendgift &lt;user&gt; &lt;gift_id&gt; [text]</code>")


@Client.on_message(filters.command("sendgift") & filters.user(OWNERS))
async def sendgift_cmd(client: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2:
        return await message.reply_text("<code>/sendgift &lt;user_id|@username&gt; &lt;gift_id&gt; [text]</code> "
                                        "(IDs: /gifts)")
    if not botapi.enabled():
        return await message.reply_text("❌ Needs the Bot API bridge (<code>AIOGRAM_ENABLED=True</code>).")
    uid, mention = await _resolve_user(client, args[0])
    if not uid:
        return await message.reply_text("❌ User not found.")
    note = " ".join(args[2:])[:128] or None
    try:
        await botapi.call(lambda b: b.send_gift(gift_id=args[1], user_id=uid, text=note))
    except Exception as e:
        return await message.reply_text(f"❌ Telegram refused: <code>{html.escape(str(e))[:300]}</code>")
    await message.reply_text(f"🎁 Gift sent to {mention}!")
    from core import botlog
    await botlog.event("PremiumGift", botlog.user_block(message.from_user, f"🎁 Gift <code>{args[1]}</code> → "
                                                        f"<code>{uid}</code>"), client=client)

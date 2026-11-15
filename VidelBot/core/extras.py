"""
Bot API 10.3 + WzGram Layer 232 rich features (see core/botapi.py & core/rich.py).

    /guide                         rich message: headings, plan table, FAQ details (everyone)
    /checklist <tasks>             interactive Telegram checklist (Business native + live rich card)
    /nftgift <slug|t.me/nft/...>   inspect an upgraded collectible Telegram gift (everyone)
    /botapi [test]                 WzGram Layer 232 + Bot API bridge health & capabilities (owner)
    /giftpremium <user> <3|6|12>   gift Telegram Premium paid with the bot's Stars (owner)
    /gifts [nft_link]              list Telegram gifts the bot can send or inspect an NFT gift (owner)
    /sendgift <user> <id> [text]   send one of those gifts (owner)
"""
import html
import logging
import re
import secrets
import time

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    InputChecklist,
    InputChecklistTask,
    Message,
)

from config import BOT_NAME, OWNERS, STARS_PLANS, SUBSCRIPTION_STARS, TRIAL_DAYS
from core import botapi, rich, texts
from core.db import vdb
from core.rich import Doc
from core.ui import HTML, copy_button, group_reply, is_wzgram_client, styled_button

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
        at.InputRichBlockDetails(summary="🧩 Rich tools & plugins", blocks=[
            bullets("/imdb · /anime · /mdl – rich movie, anime & drama cards",
                    "/mediainfo – reply to media or pass a direct HTTP link",
                    "/checklist – interactive task checklists")]),
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


def guide_doc(bot_username: str) -> Doc:
    """Richgram / Doc equivalent of /guide for native WzGram MTProto RichMessage."""
    link = f"https://t.me/{bot_username}"
    doc = Doc("📖", f"{BOT_NAME} · Guide", "saving · encoding · clone bots · rich plugins")
    doc.text(
        f"One bot for <b>saving restricted content</b>, <b>encoding videos</b>, "
        f"running <b>FileStore clone bots</b>, and searching media with rich cards."
    )
    doc.h("🚀", "Quick start").items([
        "/login – connect your account for private channels",
        "Send any t.me link – Videl saves the post (or /batch for many)",
        "Send a video – pick an encoder preset",
        "/clone – create your own FileStore bot",
        "/imdb · /anime · /mdl · /checklist – rich search & task tools",
    ])
    if STARS_PLANS or SUBSCRIPTION_STARS > 0:
        rows = [(_label(d), f"⭐ {s}") for d, s in STARS_PLANS]
        if SUBSCRIPTION_STARS > 0:
            rows.append(("Monthly (auto-renew)", f"⭐ {SUBSCRIPTION_STARS}/mo"))
        doc.h("💎", "Premium plans").table(rows, header=("Plan", "Price"), align=("left", "right"),
                                           caption="Paid securely with Telegram Stars")
    doc.buttons_row(
        rich.rich_button("💎 Premium", url=f"{link}?start=premium", style="success"),
        rich.rich_button("🤖 Clone bot", url=f"{link}?start=clone", style="primary"),
        rich.rich_button("🆘 Help", url=f"{link}?start=help"),
    )
    doc.footer(f"{BOT_NAME} · /help for every command")
    return doc


@Client.on_message(filters.command(["guide", "illustrated"]))
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
        except Exception as e:  # building blocks failed → fallback
            log.warning(f"rich guide failed: {e}")
    if is_wzgram_client(client):
        mid = await rich.send(client, chat_id, guide_doc(bot_username))
        if mid:
            return True
    await client.send_message(chat_id, texts.HELP_TXT, parse_mode=HTML, disable_web_page_preview=True)
    return False


# ─────────────────────────── /checklist (interactive) ───────────────────────────
def parse_checklist_input(raw_text: str) -> tuple[str, list[str]]:
    """Parse ``Title :: item 1 | item 2`` or multiline ``Title\\n- item 1\\n- item 2``."""
    cleaned = re.sub(r"^/\w+(?:@\w+)?\s*", "", (raw_text or "").strip(), count=1).strip()
    if not cleaned:
        return "", []
    title = "Checklist"
    if "::" in cleaned:
        head, rest = cleaned.split("::", 1)
        if head.strip():
            title = head.strip()[:120]
        cleaned = rest.strip()
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    if len(lines) > 1 and "|" not in lines[0] and "::" not in raw_text:
        title = lines[0][:120]
        raw_items = lines[1:]
    elif "|" in cleaned:
        raw_items = [p.strip() for p in cleaned.split("|")]
    elif ";" in cleaned:
        raw_items = [p.strip() for p in cleaned.split(";")]
    else:
        raw_items = lines
    tasks = []
    for item in raw_items:
        item = re.sub(r"^[-*•☐☑️✅\d.)]+\s*", "", item).strip()
        if item:
            tasks.append(item[:100])
    return title, tasks[:20]


def _checklist_doc(state: dict) -> Doc:
    tasks = state.get("tasks") or []
    done_count = sum(1 for t in tasks if t.get("done"))
    total = len(tasks) or 1
    pct = int(round(done_count * 100 / total))
    filled = int(round(done_count * 10 / total))
    bar = "🟩" * filled + "⬜" * (10 - filled)
    doc = Doc("☑️", state.get("title") or "Checklist", f"{done_count}/{len(tasks)} completed · {pct}%")
    doc.text(f"{bar}  <b>{done_count}/{len(tasks)}</b>")
    table_rows = []
    for idx, t in enumerate(tasks, 1):
        mark = "☑️" if t.get("done") else "☐"
        label = rich.Raw(f"<s>{rich.esc(t['text'])}</s>") if t.get("done") else t["text"]
        table_rows.append((f"{mark} #{idx}", label))
    doc.table(table_rows, header=("State", "Task"))
    doc.footer("Tap any task button below to toggle completion.")
    return doc


def _checklist_kb(cid: str, state: dict) -> InlineKeyboardMarkup:
    tasks = state.get("tasks") or []
    rows_out = []
    for idx, t in enumerate(tasks):
        done = bool(t.get("done"))
        short = t["text"] if len(t["text"]) <= 28 else t["text"][:25] + "…"
        rows_out.append([
            styled_button(
                f"{'☑️' if done else '☐'} {short}",
                callback_data=f"chk_tog:{cid}:{idx}",
                style="success" if done else None,
            )
        ])
    all_done = all(t.get("done") for t in tasks) if tasks else False
    rows_out.append([
        styled_button(
            "↺ Reset all" if all_done else "✅ Complete all",
            callback_data=f"chk_all:{cid}",
            style="primary" if not all_done else None,
        ),
        styled_button("🗑 Delete", callback_data=f"chk_del:{cid}", style="danger"),
    ])
    return InlineKeyboardMarkup(rows_out)


@Client.on_message(filters.command(["checklist", "todo"]))
async def checklist_cmd(client: Client, message: Message):
    title, items = parse_checklist_input(message.text or "")
    if not items and message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        title, items = parse_checklist_input(message.reply_to_message.text or message.reply_to_message.caption or "")
    if not items:
        usage = Doc("☑️", "Interactive Checklist", "Telegram Business native + Rich card")
        usage.items([
            rich.Raw("<code>/checklist Release Prep :: Encode 1080p | Mux subs | Upload batch</code>"),
            rich.Raw("<code>/checklist Buy milk | Pay server bill | Check logs</code>"),
            "Or reply to any bulleted message with <code>/checklist</code>",
        ])
        return await rich.reply(message, usage)

    # 1) Telegram Business connection → native Bot API / Layer 232 Checklist
    biz_id = getattr(message, "business_connection_id", None)
    if biz_id:
        sent = await botapi.send_checklist(biz_id, message.chat.id, title, items, reply_to=message.id)
        if sent:
            return
        if is_wzgram_client(client) and hasattr(client, "send_checklist"):
            try:
                cl = InputChecklist(
                    title=title,
                    tasks=[InputChecklistTask(id=i + 1, text=t) for i, t in enumerate(items)],
                    others_can_mark_tasks_as_done=True,
                )
                await client.send_checklist(message.chat.id, cl, business_connection_id=biz_id)
                return
            except Exception as e:
                log.debug(f"wzgram send_checklist fallback: {e}")

    # 2) Interactive Rich Checklist card (works in every DM & group!)
    cid = secrets.token_hex(4)
    uid = message.from_user.id if message.from_user else 0
    state = {
        "_id": cid,
        "owner": uid,
        "chat_id": message.chat.id,
        "title": title,
        "tasks": [{"text": t, "done": False} for t in items],
        "created_at": time.time(),
    }
    await vdb.db["checklists"].insert_one(state)
    await rich.reply(message, _checklist_doc(state), reply_markup=_checklist_kb(cid, state))


@Client.on_callback_query(filters.regex(r"^chk_(tog|all|del):([\w-]+)(?::(\d+))?$"))
async def checklist_cb(client: Client, query: CallbackQuery):
    action = query.matches[0].group(1)
    cid = query.matches[0].group(2)
    idx_raw = query.matches[0].group(3)
    state = await vdb.db["checklists"].find_one({"_id": cid})
    if not state:
        return await query.answer("Checklist expired.", show_alert=True)
    uid = query.from_user.id if query.from_user else 0
    is_private = getattr(getattr(query.message, "chat", None), "id", 0) > 0
    if action == "del":
        if state.get("owner") and uid not in (state["owner"], *OWNERS) and not is_private:
            return await query.answer("Only the checklist creator can delete it.", show_alert=True)
        await vdb.db["checklists"].delete_one({"_id": cid})
        try:
            await query.message.delete()
        except Exception:
            pass
        return await query.answer("🗑 Checklist deleted.")
    tasks = state.get("tasks") or []
    if action == "tog" and idx_raw is not None:
        idx = int(idx_raw)
        if 0 <= idx < len(tasks):
            tasks[idx]["done"] = not tasks[idx].get("done", False)
    elif action == "all":
        target = not all(t.get("done") for t in tasks)
        for t in tasks:
            t["done"] = target
    await vdb.db["checklists"].update_one({"_id": cid}, {"$set": {"tasks": tasks}})
    state["tasks"] = tasks
    await query.answer("✅ Updated")
    await rich.edit(query.message, _checklist_doc(state), reply_markup=_checklist_kb(cid, state))


# ─────────────────────────── /botapi (owner) ───────────────────────────
_FLAGS = [
    ("can_join_groups", "Join groups"), ("can_read_all_group_messages", "Privacy mode off"),
    ("supports_inline_queries", "Inline mode"), ("can_connect_to_business", "Business"),
    ("has_main_web_app", "Main Mini App"), ("can_manage_bots", "Manage bots"),
    ("has_topics_enabled", "Private topics"), ("allows_users_to_create_topics", "User topics"),
]


@Client.on_message(filters.command(["botapi", "aiogram"]) & filters.user(OWNERS))
async def botapi_cmd(client: Client, message: Message):
    from client import runtime_stack
    st = runtime_stack()
    mtproto_line = (
        f"<b>MTProto:</b> {st['engine']} {html.escape(str(st['version']))} · Layer <code>{st['layer']}</code>"
        + (f" · WarpCrypto {html.escape(str(st['warpcrypto']))}" if st.get("warpcrypto") else "")
        + (f" · richgram {html.escape(str(st['richgram']))}" if st.get("richgram") else "")
    )
    if not botapi.enabled():
        return await message.reply_text(
            f"<b>🛰 Bot API &amp; WzGram runtime</b>\n\n<blockquote>{mtproto_line}\n"
            f"Status: {await botapi.status_line()}</blockquote>\n"
            "WzGram Layer 232 provides native <code>InputRichMessage</code>, <code>ButtonStyle</code>, "
            "<code>ReplyParameters</code>, <code>LinkPreviewOptions</code> and <code>EphemeralMessageParameters</code>. "
            "Set <code>AIOGRAM_ENABLED=True</code> to also enable the outgoing Bot API HTTP bridge.")
    t = time.perf_counter()
    me = await botapi.me(refresh=True)
    ms = (time.perf_counter() - t) * 1000
    if me is None:
        return await message.reply_text(
            f"<b>🛰 Bot API unreachable</b> – Videl keeps working natively over {st['engine']} Layer {st['layer']}.")
    flags = "\n".join(f"{'✅' if getattr(me, k, False) else '▫️'} {label}" for k, label in _FLAGS)
    from config import BOT_API_URL, COLORED_BUTTONS, EPHEMERAL_REPLIES, MANAGED_BOTS
    from core.payments import star_balance
    bal = await star_balance(client)
    text = (
        "<b>🛰 Bot API &amp; WzGram bridge</b>\n\n<blockquote>"
        f"{mtproto_line}\n"
        f"<b>Library:</b> {botapi.version()}\n"
        f"<b>Server:</b> {html.escape(BOT_API_URL) if BOT_API_URL else 'api.telegram.org'}\n"
        f"<b>getMe latency:</b> <code>{ms:.0f} ms</code>\n"
        f"<b>Stars balance:</b> <code>{bal if bal is not None else '?'}</code> ⭐</blockquote>\n"
        f"<b>Capabilities</b>\n<blockquote>{flags}</blockquote>\n"
        "<b>Features</b>\n<blockquote>"
        f"{'✅' if COLORED_BUTTONS else '▫️'} Coloured buttons (Bot API + WzGram ButtonStyle)\n"
        f"{'✅' if EPHEMERAL_REPLIES else '▫️'} Ephemeral group replies\n"
        f"{'✅' if MANAGED_BOTS and getattr(me, 'can_manage_bots', False) else '▫️'} One-tap clone bots"
        f"{'' if getattr(me, 'can_manage_bots', False) else ' (enable Bot Management Mode in @BotFather)'}\n"
        "✅ Rich /guide · ✅ Stoppable Drafts · ✅ Checklists · ✅ Stars ledger · ✅ Collectible gifts</blockquote>\n"
        "<i>Updates arrive over WzGram MTProto Layer 232; aiogram &amp; WzGram share message IDs.</i>"
    )
    kb = InlineKeyboardMarkup([
        [Btn("✅ Success", callback_data="botapi_demo"), Btn("❌ Danger", callback_data="botapi_demo")],
        [Btn("🚀 Primary", callback_data="botapi_demo"), Btn("Default", callback_data="botapi_demo")],
    ])
    from core.ui import send_with_preview
    await send_with_preview(client, message.chat.id, text, kb, reply_to=message.id)


@Client.on_callback_query(filters.regex(r"^botapi_demo$") & filters.user(OWNERS))
async def botapi_demo(client, query):
    await query.answer("🎨 Button colours come from Bot API 10.3 & WzGram ButtonStyle.", show_alert=False)


# ─────────────────────────── Collectible / Upgraded Gift inspector ───────────────────────────
def _extract_nft_slug(arg: str) -> str:
    s = (arg or "").strip()
    m = re.search(r"(?:t\.me/nft/|nft/)([A-Za-z0-9_-]+)", s)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9]+-\d+", s):
        return s
    return ""


async def _inspect_upgraded_gift(client: Client, message: Message, slug: str):
    link = f"https://t.me/nft/{slug}"
    if not hasattr(client, "get_upgraded_gift"):
        return await message.reply_text("❌ Upgraded gift lookup requires WzGram Layer 232.")
    try:
        gift = await client.get_upgraded_gift(link)
    except Exception as e:
        return await message.reply_text(f"❌ Could not inspect collectible gift <code>{html.escape(slug)}</code>: "
                                        f"<code>{html.escape(str(e))[:200]}</code>")
    title = getattr(gift, "title", None) or getattr(gift, "name", None) or slug
    num = getattr(gift, "number", None) or getattr(gift, "collectible_id", None)
    total = getattr(gift, "total_upgraded_count", None) or getattr(gift, "max_upgraded_count", None)
    doc = Doc("💎", f"{title}" + (f" #{num}" if num else ""), "Telegram Upgraded Collectible Gift")
    rows = [("🔗 Slug", rich.code(slug))]
    if num and total:
        rows.append(("🔢 Edition", f"#{num} of {total}"))
    elif num:
        rows.append(("🔢 Edition", f"#{num}"))
    for attr, label in (("model", "🎨 Model"), ("backdrop", "🖼 Backdrop"), ("symbol", "✨ Pattern")):
        part = getattr(gift, attr, None)
        if part is not None:
            pname = getattr(part, "name", None) or str(part)
            rarity = getattr(part, "rarity_permille", None)
            val = f"{pname} ({rarity / 10:.1f}%)" if isinstance(rarity, (int, float)) else pname
            rows.append((label, val))
    owner = getattr(gift, "owner", None) or getattr(gift, "owner_name", None)
    if owner is not None:
        oname = getattr(owner, "first_name", None) or getattr(owner, "title", None) or str(owner)
        rows.append(("👤 Owner", oname))
    value_amount = getattr(gift, "value_amount", None)
    value_currency = getattr(gift, "value_currency", None)
    if value_amount and value_currency:
        rows.append(("💰 Est. Value", f"{value_amount} {value_currency}"))
    doc.kv_table(rows, key_header="Attribute", val_header="Details")
    kb = InlineKeyboardMarkup([
        [Btn("🔗 View on Telegram", url=link), copy_button("📋 Copy Link", link)],
    ])
    return await rich.reply(message, doc, reply_markup=kb)


@Client.on_message(filters.command("nftgift"))
async def nftgift_cmd(client: Client, message: Message):
    slug = _extract_nft_slug(message.command[1] if len(message.command) > 1 else "")
    if not slug:
        return await message.reply_text(
            "<b>💎 Inspect Collectible Gift</b>\n\n"
            "Usage: <code>/nftgift PlushPepe-1</code> or <code>/nftgift https://t.me/nft/PlushPepe-1</code>"
        )
    return await _inspect_upgraded_gift(client, message, slug)


# ─────────────────────────── Premium & gifts (owner) ───────────────────────────
@Client.on_message(filters.command("giftpremium") & filters.user(OWNERS))
async def giftpremium_cmd(client: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2 or not args[1].isdigit() or int(args[1]) not in PREMIUM_GIFT_PRICES:
        prices = " · ".join(f"{m} mo = ⭐{s}" for m, s in PREMIUM_GIFT_PRICES.items())
        return await message.reply_text(
            "<b>🎁 Gift Telegram Premium</b> (paid from the bot's Stars)\n\n"
            "<code>/giftpremium &lt;user_id|@username&gt; &lt;3|6|12&gt; [message]</code>\n\n" + prices)
    uid, mention = await _resolve_user(client, args[0])
    if not uid:
        return await message.reply_text("❌ User not found.")
    months = int(args[1])
    note = " ".join(args[2:])[:128] or None
    if botapi.enabled():
        try:
            await botapi.call(lambda b: b.gift_premium_subscription(
                user_id=uid, month_count=months, star_count=PREMIUM_GIFT_PRICES[months], text=note))
        except Exception as e:
            return await message.reply_text(f"❌ Telegram refused: <code>{html.escape(str(e))[:300]}</code>")
    elif is_wzgram_client(client) and hasattr(client, "gift_premium_with_stars"):
        try:
            await client.gift_premium_with_stars(
                user_id=uid, month_count=months, star_count=PREMIUM_GIFT_PRICES[months], text=note)
        except Exception as e:
            return await message.reply_text(f"❌ Telegram refused: <code>{html.escape(str(e))[:300]}</code>")
    else:
        return await message.reply_text("❌ Needs the Bot API bridge (<code>AIOGRAM_ENABLED=True</code>).")
    await message.reply_text(f"🎁 Gifted <b>{months} months</b> of Telegram Premium to {mention}!")
    from core import botlog
    await botlog.event("PremiumGift", botlog.user_block(message.from_user,
                       f"🎁 Telegram Premium {months} mo → <code>{uid}</code> (⭐{PREMIUM_GIFT_PRICES[months]})"),
                       client=client)


@Client.on_message(filters.command("gifts") & filters.user(OWNERS))
async def gifts_cmd(client: Client, message: Message):
    if len(message.command) > 1:
        slug = _extract_nft_slug(message.command[1])
        if slug:
            return await _inspect_upgraded_gift(client, message, slug)
    if not botapi.enabled():
        if is_wzgram_client(client) and hasattr(client, "get_available_gifts"):
            try:
                gifts = await client.get_available_gifts() or []
            except Exception:
                gifts = []
            if gifts:
                lines = []
                for g in gifts[:40]:
                    emoji = getattr(getattr(g, "sticker", None), "emoji", "") or "🎁"
                    left = f" · {g.remaining_count} left" if getattr(g, "remaining_count", None) is not None else ""
                    lines.append(f"{emoji} ⭐{g.star_count}{left}\n<code>{g.id}</code>")
                return await message.reply_text(
                    f"<b>🎁 Telegram gifts ({len(gifts)})</b>\n\n" + "\n".join(lines)
                    + "\n\n<b>Send:</b> <code>/sendgift &lt;user&gt; &lt;gift_id&gt; [text]</code>")
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
    uid, mention = await _resolve_user(client, args[0])
    if not uid:
        return await message.reply_text("❌ User not found.")
    note = " ".join(args[2:])[:128] or None
    if botapi.enabled():
        try:
            await botapi.call(lambda b: b.send_gift(gift_id=args[1], user_id=uid, text=note))
        except Exception as e:
            return await message.reply_text(f"❌ Telegram refused: <code>{html.escape(str(e))[:300]}</code>")
    elif is_wzgram_client(client) and hasattr(client, "send_gift"):
        try:
            await client.send_gift(user_id=uid, gift_id=int(args[1]) if args[1].isdigit() else args[1], text=note)
        except Exception as e:
            return await message.reply_text(f"❌ Telegram refused: <code>{html.escape(str(e))[:300]}</code>")
    else:
        return await message.reply_text("❌ Needs the Bot API bridge (<code>AIOGRAM_ENABLED=True</code>).")
    await message.reply_text(f"🎁 Gift sent to {mention}!")
    from core import botlog
    await botlog.event("PremiumGift", botlog.user_block(message.from_user, f"🎁 Gift <code>{args[1]}</code> → "
                                                        f"<code>{uid}</code>"), client=client)

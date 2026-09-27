"""
Extra clone-bot features – registered per clone by ``setup_extras(app, ctx)``.

A · Links & access   /smartlink (expiry · max users · password · ⭐ price) · /links
                     clone premium (/addpremium /delpremium /premiumusers /setpremium · /plan)
B · Channel & search auto-index + auto-link of storage-channel posts (/autolink /setpostchannel)
                     /index backfill · /search · inline search · /searchmode
C · Owner tools      /analytics · /broadcast (pin · silent · forward · schedule, removes blocked users)
                     /schedules · /export
D · User experience  /request + /requests inbox · /setbuttons /delbuttons (buttons under files)
                     anti-flood (/antiflood) · /maintenance · /help /about (/sethelp /setabout) · /settings

``ctx`` comes from engine.start_worker: bot_id, owner_id, log_channel_id, worker_db, xdb (CloneExtras),
main_db, is_admin(uid), gate(client, message, start_param, doc), deliver(client, message, payload, …),
fresh_doc(), tasks (background tasks cancelled on stop) and handle_start (set here).
"""
import asyncio
import csv
import html
import io
import json
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from pyrogram import Client, StopPropagation, filters
from pyrogram.errors import (FloodWait, InputUserDeactivated, MessageNotModified, PeerIdInvalid, UserDeactivated,
                             UserIsBlocked)
from pyrogram.types import (BotCommand, BotCommandScopeChat, BotCommandScopeDefault, CallbackQuery,
                            InlineKeyboardButton as Btn, InlineKeyboardMarkup as Kb, InlineQuery,
                            InlineQueryResultArticle, InputTextMessageContent, LabeledPrice, Message,
                            PreCheckoutQuery)

from filestore.database.extras_db import CloneExtras, now as db_now
from filestore.fs_config import LOGGER
from filestore.utils.helpers import decode, encode

log = LOGGER(__name__)

# ───────────────────────────── tunables ─────────────────────────────
FLOOD_LIMIT = 8            # messages …
FLOOD_WINDOW = 10          # … per this many seconds
FLOOD_BLOCK = 60           # first block (seconds); doubles per strike
REQUESTS_PER_DAY = 3
REQUESTS_PER_DAY_PREMIUM = 10
SEARCH_PAGE = 8
INDEX_LIMIT = 50_000       # most posts /index scans
MAX_BUTTON_ROWS = 8
DEAD_USER_ERRORS = (UserIsBlocked, InputUserDeactivated, UserDeactivated, PeerIdInvalid)

USER_COMMANDS = [
    ("start", "Start the bot / get files"),
    ("search", "Search files"),
    ("request", "Request a file"),
    ("plan", "Premium – skip verification"),
    ("help", "How to use this bot"),
    ("about", "About this bot"),
    ("id", "Your Telegram ID"),
]
ADMIN_COMMANDS = USER_COMMANDS + [
    ("settings", "⚙️ Feature switches"),
    ("genlink", "Link for one post"),
    ("batch", "Link for a range of posts"),
    ("custom_batch", "Link for hand-picked posts"),
    ("flink", "Formatted links"),
    ("smartlink", "Expiring / limited / password / paid link"),
    ("links", "Manage smart links"),
    ("analytics", "Clicks, top files, growth"),
    ("broadcast", "Broadcast (pin · silent · schedule)"),
    ("schedules", "Scheduled broadcasts"),
    ("requests", "Open file requests"),
    ("index", "Index the storage channel for search"),
    ("autolink", "Auto-link new channel posts"),
    ("addpremium", "Give premium"),
    ("delpremium", "Remove premium"),
    ("premiumusers", "Premium users"),
    ("setpremium", "Sell premium for Stars"),
    ("setbuttons", "Buttons under delivered files"),
    ("sethelp", "Custom /help text"),
    ("setabout", "Custom /about text"),
    ("export", "Export users & settings"),
    ("users", "User count"),
    ("ban", "Ban a user"),
    ("unban", "Unban a user"),
    ("ping", "Latency"),
]

# (setting key, label, default) – shared with the main-bot dashboard panel
TOGGLES = [
    ("search_public", "🔍 Public search", False),
    ("requests", "📨 File requests", True),
    ("antiflood", "🛡 Anti-flood", True),
    ("flood_autoban", "⛔ Flood auto-ban", False),
    ("autolink_dm", "📩 Auto-link → DM me", False),
    ("autolink_edit", "🔘 Auto-link → button on post", False),
    ("autolink_post", "📢 Auto-link → post channel", False),
    ("maintenance_mode", "🛠 Maintenance", False),
]
TOGGLE_KEYS = {k for k, _, _ in TOGGLES}


# ═════════════════════════════ pure helpers ═════════════════════════════
def setting(settings: dict, key: str):
    default = next((d for k, _, d in TOGGLES if k == key), None)
    value = (settings or {}).get(key)
    return default if value is None else value


def parse_duration(text: str) -> int | None:
    """'30m' / '24h' / '7d' / '2w' / '1d12h' → seconds (None if not a duration)."""
    text = (text or "").strip().lower()
    parts = re.findall(r"(\d+)\s*([mhdw])", text)
    if not parts or re.sub(r"(\d+)\s*([mhdw])", "", text).strip():
        return None
    unit = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    total = sum(int(n) * unit[u] for n, u in parts)
    return total if 0 < total <= 366 * 86400 else None


def human_duration(seconds: int) -> str:
    out = []
    for name, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            n, seconds = divmod(seconds, size)
            out.append(f"{n}{name}")
    return " ".join(out) or "<1m"


def human_size(size: int) -> str:
    size = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def sparkline(values: list) -> str:
    bars = "▁▂▃▄▅▆▇█"
    top = max(values) if values else 0
    if not top:
        return "▁" * len(values)
    return "".join(bars[min(7, int(v / top * 7 + 0.5))] if v else "▁" for v in values)


_URL_RE = re.compile(r"^(https?://|tg://)\S+$", re.I)


def parse_buttons(text: str) -> list:
    """'Text - https://url | Text 2 - https://url2' per line → [[[text, url], …], …]. Raises ValueError."""
    rows = []
    for line in (text or "").strip().splitlines():
        if not line.strip():
            continue
        row = []
        for cell in line.split("|"):
            if " - " not in cell:
                raise ValueError(f"missing ' - ' in: {cell.strip()[:40]}")
            label, url = cell.rsplit(" - ", 1)
            label, url = label.strip(), url.strip()
            if not label or len(label) > 40:
                raise ValueError("button text must be 1–40 characters")
            if not _URL_RE.match(url):
                raise ValueError(f"not a link: {url[:40]}")
            row.append([label, url])
        if len(row) > 3:
            raise ValueError("max 3 buttons per row")
        rows.append(row)
    if not rows:
        raise ValueError("no buttons found")
    if len(rows) > MAX_BUTTON_ROWS:
        raise ValueError(f"max {MAX_BUTTON_ROWS} rows")
    return rows


def file_buttons_markup(settings: dict):
    rows = (settings or {}).get("file_buttons") or []
    try:
        return Kb([[Btn(label, url=url) for label, url in row] for row in rows]) if rows else None
    except Exception:
        return None


def start_markup(settings: dict):
    """Buttons under the welcome message."""
    row = [Btn("❓ ʜᴇʟᴘ", callback_data="xh:help"), Btn("ℹ️ ᴀʙᴏᴜᴛ", callback_data="xh:about")]
    rows = [row]
    extra = []
    if setting(settings, "search_public"):
        extra.append(Btn("🔍 sᴇᴀʀᴄʜ", switch_inline_query_current_chat=""))
    if (settings or {}).get("premium_stars"):
        extra.append(Btn("💎 ᴘʀᴇᴍɪᴜᴍ", callback_data="xp:plan"))
    if extra:
        rows.insert(0, extra)
    return Kb(rows)


async def extract_payload(arg: str) -> str | None:
    """A share link (t.me/…?start=X, permanent ?url=X) or the raw payload → payload, if it decodes to get-…"""
    arg = (arg or "").strip()
    m = re.search(r"[?&](?:start|url)=([A-Za-z0-9_\-=]+)", arg)
    payload = m.group(1) if m else arg
    if not re.fullmatch(r"[A-Za-z0-9_\-=]{4,200}", payload or ""):
        return None
    try:
        if (await decode(payload)).startswith("get-"):
            return payload
    except Exception:
        pass
    return None


def parse_smartlink_options(tokens: list) -> dict:
    """['24h', 'x50', 'pass=abc', 'stars=25', 'note=Season', '1'] → options. Raises ValueError."""
    opts = {"expires": None, "max_uses": 0, "password": "", "stars": 0, "note": ""}
    for tok in tokens:
        low = tok.lower()
        if low.startswith(("pass=", "password=")):
            opts["password"] = tok.split("=", 1)[1]
            if not 1 <= len(opts["password"]) <= 64:
                raise ValueError("password must be 1–64 characters")
        elif low.startswith(("stars=", "price=")):
            v = tok.split("=", 1)[1]
            if not v.isdigit() or not 1 <= int(v) <= 10000:
                raise ValueError("stars must be 1–10000")
            opts["stars"] = int(v)
        elif low.startswith(("uses=", "max=")) or re.fullmatch(r"x\d+", low):
            v = low.split("=", 1)[1] if "=" in low else low[1:]
            if not v.isdigit() or not 1 <= int(v) <= 1_000_000:
                raise ValueError("uses must be 1–1000000")
            opts["max_uses"] = int(v)
        elif low.startswith("note="):
            opts["note"] = tok.split("=", 1)[1][:60]
        elif parse_duration(low):
            opts["expires"] = parse_duration(low)
        else:
            raise ValueError(f"unknown option: {tok[:30]}")
    return opts


def parse_when(tokens: list, tz_name: str):
    """Broadcast time: ['in', '2h'] or ['at', '21:30'] → naive-UTC datetime, or None. Raises ValueError."""
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if low == "in" and i + 1 < len(tokens):
            secs = parse_duration(tokens[i + 1])
            if not secs:
                raise ValueError("use e.g. in 30m / in 2h / in 1d")
            return db_now() + timedelta(seconds=secs)
        if low == "at" and i + 1 < len(tokens):
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", tokens[i + 1])
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                raise ValueError("use e.g. at 21:30 (24-hour)")
            try:
                from zoneinfo import ZoneInfo
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = timezone.utc
            local = datetime.now(tz)
            run = local.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
            if run <= local:
                run += timedelta(days=1)
            return run.astimezone(timezone.utc).replace(tzinfo=None)
    return None


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def default_help(settings: dict, admin: bool) -> str:
    lines = ["<b>❓ How to use this bot</b>", "",
             "<blockquote>• Open a file link to get its files here."]
    if setting(settings, "search_public") or admin:
        lines.append("• /search <code>name</code> – find files (or type <code>@thisbot name</code> anywhere).")
    if setting(settings, "requests"):
        lines.append("• /request <code>what you need</code> – ask the admins for a file.")
    if (settings or {}).get("premium_stars"):
        lines.append("• /plan – premium: no verification / ads.")
    lines.append("• /about – about this bot.</blockquote>")
    if admin:
        lines += ["", "<b>👮 Admin</b>",
                  "<blockquote>/settings · /smartlink · /links · /analytics · /broadcast · /schedules\n"
                  "/requests · /index · /autolink · /setpostchannel · /addpremium · /setpremium\n"
                  "/setbuttons · /sethelp · /setabout · /export · /maintenance · /antiflood</blockquote>"]
    return "\n".join(lines)


def settings_panel(settings: dict, prefix: str):
    """Toggle buttons; callback data = f'{prefix}{key}'."""
    rows = []
    for key, label, _ in TOGGLES:
        on = bool(setting(settings, key))
        rows.append([Btn(f"{'✅' if on else '▫️'} {label}", callback_data=f"{prefix}{key}")])
    return rows


async def analytics_text(bot_id: int, username: str = "") -> str:
    """Analytics report – used by /analytics in the clone and the main-bot dashboard."""
    from filestore.database.worker_db import WorkerDB
    xdb, wdb = CloneExtras(bot_id), WorkerDB(bot_id)
    days = await xdb.days(14)
    deliveries = [d.get("deliveries", 0) for _, d in days]
    new_users = [d.get("new_users", 0) for _, d in days]
    top = await xdb.top_links(5)
    total_users = await wdb.total_users()
    lines = [f"<b>📈 Analytics{' · @' + esc(username) if username else ''}</b>", "",
             "<blockquote>"
             f"<b>👥 Users:</b> {total_users}  (+{new_users[-1]} today, +{sum(new_users[-7:])} in 7d)\n"
             f"<b>📥 Opens today:</b> {deliveries[-1]}  · 7d: {sum(deliveries[-7:])}  · 14d: {sum(deliveries)}\n"
             f"<b>🔗 Smart links:</b> {await xdb.count_links()}  · <b>🗂 Indexed:</b> {await xdb.indexed_count()}\n"
             f"<b>💎 Premium:</b> {len(await xdb.list_premium(1000))}  · <b>⭐ Earned:</b> {await xdb.stars_earned()}"
             "</blockquote>",
             f"<b>Opens · 14 days</b>\n<code>{sparkline(deliveries)}</code>",
             f"<b>New users · 14 days</b>\n<code>{sparkline(new_users)}</code>"]
    if top:
        lines.append("\n<b>🏆 Top links</b>")
        for i, t in enumerate(top, 1):
            label = esc(t.get("label") or "files")[:40]
            lines.append(f"{i}. {label} – <b>{t.get('clicks', 0)}</b> opens")
    return "\n".join(lines)


# ═════════════════════════════ per-clone setup ═════════════════════════════
def setup_extras(app: Client, ctx):
    xdb: CloneExtras = ctx.xdb
    wdb = ctx.worker_db
    bot_id = ctx.bot_id

    pending_pw: dict = {}        # uid → {"code", "tries", "until"}
    pending_reply: dict = {}     # admin uid → {"rid", "user", "until"}
    search_cache: dict = {}      # token → (query, created)
    flood_hits = defaultdict(deque)
    flood_block: dict = {}
    flood_strikes: dict = {}
    admin_cache: dict = {}       # uid → (is_admin, checked_at)
    state = {"broadcast": None, "index": None}

    async def is_admin(uid: int) -> bool:
        hit = admin_cache.get(uid)
        if hit and time.monotonic() - hit[1] < 60:
            return hit[0]
        value = await ctx.is_admin(uid)
        admin_cache[uid] = (value, time.monotonic())
        return value

    async def settings_now() -> dict:
        return (await ctx.fresh_doc()).get("settings", {}) or {}

    async def set_setting(key, value):
        await ctx.main_db.update_setting(bot_id, key, value)

    async def me():
        return getattr(app, "me", None) or await app.get_me()

    async def share_link(msg_id: int) -> str:
        payload = await encode(f"get-{msg_id * abs(ctx.log_channel_id)}")
        return f"https://t.me/{(await me()).username}?start={payload}"

    def admin_only(func):
        async def wrapper(client, message):
            if not message.from_user or not await is_admin(message.from_user.id):
                return
            return await func(client, message)
        wrapper.__name__ = func.__name__
        return wrapper

    def args_of(message: Message) -> str:
        parts = (message.text or "").split(None, 1)
        return parts[1].strip() if len(parts) > 1 else ""

    # ─────────────────────────── D · anti-flood (runs first) ───────────────────────────
    @app.on_message(filters.private & filters.incoming & ~filters.service, group=-3)
    async def antiflood(client: Client, message: Message):
        if not message.from_user or message.successful_payment:
            return
        uid = message.from_user.id
        t = time.monotonic()
        if flood_block.get(uid, 0) > t:
            raise StopPropagation
        hits = flood_hits[uid]
        hits.append(t)
        while hits and t - hits[0] > FLOOD_WINDOW:
            hits.popleft()
        if len(flood_hits) > 5000:                       # forget idle users
            for k in [k for k, q in flood_hits.items() if not q or t - q[-1] > FLOOD_WINDOW]:
                flood_hits.pop(k, None)
        if len(hits) <= FLOOD_LIMIT or await is_admin(uid):
            return
        settings = await settings_now()
        if not setting(settings, "antiflood"):
            return
        hits.clear()
        strikes = flood_strikes.get(uid, (0, t))
        count = strikes[0] + 1 if t - strikes[1] < 3600 else 1
        flood_strikes[uid] = (count, t)
        if count >= 3 and setting(settings, "flood_autoban"):
            await wdb.add_ban_user(uid)
            await message.reply("<b>⛔ You were banned for flooding.</b>")
            raise StopPropagation
        block = FLOOD_BLOCK * (2 ** (count - 1))
        flood_block[uid] = t + block
        await message.reply(f"<b>🐢 Slow down!</b> You're sending too fast – wait {human_duration(block)}.")
        raise StopPropagation

    # ─────────────────────────── text-input catcher (password / request reply) ───────────────────────────
    # own group (-2): flink (1) and link_gen (2) have catchers too, and only ONE handler per group runs
    @app.on_message(filters.private & filters.incoming & ~filters.service, group=-2)
    async def input_catcher(client: Client, message: Message):
        if not message.from_user:
            return
        uid = message.from_user.id
        text = message.text or ""
        if uid in pending_reply:
            job = pending_reply.pop(uid)
            if time.monotonic() > job["until"]:
                return
            if text.startswith("/"):
                await message.reply("<b>❌ Reply cancelled.</b>")
                if text.startswith("/cancel"):
                    raise StopPropagation
                return                                # let the command run normally
            try:
                await client.send_message(job["user"], "<b>💬 Reply to your request:</b>")
                await message.copy(job["user"])
                await xdb.set_request_status(job["rid"], "replied", uid)
                await message.reply("<b>✅ Sent to the user.</b>")
            except Exception as e:
                await message.reply(f"<b>❌ Couldn't reach the user:</b> <code>{esc(e)}</code>")
            raise StopPropagation
        if uid in pending_pw and text and not text.startswith("/"):
            job = pending_pw[uid]
            if time.monotonic() > job["until"]:
                pending_pw.pop(uid, None)
                return
            link = await xdb.get_link(job["code"])
            if link and xdb.check_password(link, text):
                pending_pw.pop(uid, None)
                try:
                    await message.delete()          # don't leave the password in the chat
                except Exception:
                    pass
                await open_smart_link(client, message, job["code"], await ctx.fresh_doc(), pw_ok=True)
            else:
                job["tries"] += 1
                if job["tries"] >= 3:
                    pending_pw.pop(uid, None)
                    await message.reply("<b>🔐 Wrong password 3 times – open the link again to retry.</b>")
                else:
                    await message.reply(f"<b>🔐 Wrong password.</b> {3 - job['tries']} tries left.")
            raise StopPropagation

    # ─────────────────────────── A · smart links ───────────────────────────
    async def open_smart_link(client, message, code, doc, *, paid=False, pw_ok=False, user=None):
        user = user or message.from_user
        uid = user.id
        link = await xdb.get_link(code)
        if not link or not link.get("active", True):
            await message.reply("<b>❌ This link was removed by the admin.</b>")
            return
        if link.get("expires_at") and link["expires_at"] <= db_now():
            await message.reply("<b>⌛ This link has expired.</b>")
            return
        admin = await is_admin(uid)
        purchased = False
        if link.get("stars") and not admin:
            purchased = paid or await xdb.has_purchase(code, uid)
            if not purchased:
                if link.get("max_uses") and link.get("uses", 0) >= link["max_uses"] and uid not in link.get("users", []):
                    await message.reply("<b>🚫 This link reached its limit.</b>")
                    return
                await client.send_invoice(
                    chat_id=uid,
                    title=(link.get("note") or "Unlock files")[:32],
                    description="One-time purchase – you can reopen this link anytime.",
                    currency="XTR",
                    prices=[LabeledPrice(label="Access", amount=int(link["stars"]))],
                    payload=f"xl:{code}:{uid}:{int(time.time())}",
                )
                return
        if link.get("password") and not admin and not purchased and not pw_ok:
            pending_pw[uid] = {"code": code, "tries": 0, "until": time.monotonic() + 300}
            await message.reply("<b>🔐 This link is password-protected.</b>\n<i>Send the password now "
                                "(5 minutes).</i>")
            return
        if purchased:
            await xdb.claim_use(link, uid)       # counted, but a paying user is never turned away
        elif not admin and not await xdb.claim_use(link, uid):
            await message.reply("<b>🚫 This link reached its limit.</b>")
            return
        await ctx.deliver(client, message, link["payload"], user_id=uid, doc=doc,
                          skip_shortener=purchased, reload_param=f"sl_{code}")

    def link_summary(link: dict, username: str) -> str:
        bits = []
        if link.get("expires_at"):
            left = int((link["expires_at"] - db_now()).total_seconds())
            bits.append(f"⌛ {human_duration(left) + ' left' if left > 0 else 'expired'}")
        if link.get("max_uses"):
            bits.append(f"👥 {link.get('uses', 0)}/{link['max_uses']} users")
        if link.get("password"):
            bits.append("🔐 password")
        if link.get("stars"):
            bits.append(f"⭐ {link['stars']}")
        bits.append(f"👁 {link.get('clicks', 0)} opens")
        note = f"<b>{esc(link['note'])}</b>\n" if link.get("note") else ""
        return (f"{note}<code>https://t.me/{username}?start=sl_{link['_id']}</code>\n"
                f"<i>{' · '.join(bits)}</i>")

    @app.on_message(filters.command("smartlink") & filters.private)
    @admin_only
    async def smartlink_cmd(client, message):
        tokens = args_of(message).split()
        if message.reply_to_message and (message.reply_to_message.text or ""):
            tokens = [message.reply_to_message.text.strip()] + tokens
        if not tokens:
            return await message.reply(
                "<b>🔗 Smart links</b>\n\n<blockquote><code>/smartlink LINK [options]</code>\n\n"
                "<b>24h</b> / <b>7d</b> – expires after\n<b>x100</b> – first 100 users only\n"
                "<b>pass=secret</b> – asks for a password\n<b>stars=25</b> – sell access for ⭐ Stars\n"
                "<b>note=Name</b> – label\n\nExample:\n<code>/smartlink https://t.me/yourbot?start=Z2V0… "
                "48h x500 note=Episode1</code></blockquote>\n<i>LINK = any link made with /genlink or /batch.</i>")
        payload = await extract_payload(tokens[0])
        if not payload:
            return await message.reply("<b>❌ That isn't a file link from this bot.</b> Make one with /genlink first.")
        try:
            opts = parse_smartlink_options(tokens[1:])
        except ValueError as e:
            return await message.reply(f"<b>❌ {esc(e)}</b>")
        expires_at = db_now() + timedelta(seconds=opts["expires"]) if opts["expires"] else None
        link = await xdb.create_link(payload, message.from_user.id, expires_at=expires_at,
                                     max_uses=opts["max_uses"], password=opts["password"], stars=opts["stars"],
                                     note=opts["note"])
        username = (await me()).username
        await message.reply(
            f"<b>✅ Smart link created</b>\n\n{link_summary(link, username)}",
            reply_markup=Kb([[Btn("🔗 ᴏᴘᴇɴ", url=f"https://t.me/{username}?start=sl_{link['_id']}"),
                              Btn("🗑 ᴅᴇʟᴇᴛᴇ", callback_data=f"xl:del:{link['_id']}")]]))

    async def links_page(page: int):
        per = 5
        links = await xdb.list_links(per, page * per)
        total = await xdb.count_links()
        username = (await me()).username
        if not links:
            return "<b>🔗 No smart links yet.</b>\nCreate one with /smartlink.", None
        text = [f"<b>🔗 Smart links</b> ({total})\n"]
        rows = []
        for i, link in enumerate(links, 1 + page * per):
            text.append(f"<b>{i}.</b> {link_summary(link, username)}\n")
            rows.append([Btn(f"🗑 {i}. {(link.get('note') or link['_id'])[:20]}", callback_data=f"xl:del:{link['_id']}")])
        nav = []
        if page > 0:
            nav.append(Btn("⬅️", callback_data=f"xl:page:{page - 1}"))
        if (page + 1) * per < total:
            nav.append(Btn("➡️", callback_data=f"xl:page:{page + 1}"))
        if nav:
            rows.append(nav)
        return "\n".join(text), Kb(rows)

    @app.on_message(filters.command("links") & filters.private)
    @admin_only
    async def links_cmd(client, message):
        text, kb = await links_page(0)
        await message.reply(text, reply_markup=kb, disable_web_page_preview=True)

    @app.on_callback_query(filters.regex(r"^xl:(del|page):([\w]+)$"))
    async def links_cb(client, query: CallbackQuery):
        if not await is_admin(query.from_user.id):
            return await query.answer("Admins only", show_alert=True)
        action, arg = query.matches[0].group(1), query.matches[0].group(2)
        if action == "del":
            ok = await xdb.delete_link(arg)
            await query.answer("🗑 Deleted" if ok else "Already gone")
            page = 0
        else:
            page = int(arg) if arg.isdigit() else 0
            await query.answer()
        text, kb = await links_page(page)
        try:
            await query.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        except MessageNotModified:
            pass

    # ─────────────────────────── A · premium ───────────────────────────
    def parse_uid(token: str):
        token = (token or "").strip()
        return int(token) if token.lstrip("-").isdigit() else None

    def until_text(until) -> str:
        return "lifetime" if until is None else until.strftime("%d %b %Y, %H:%M UTC")

    @app.on_message(filters.command("addpremium") & filters.private)
    @admin_only
    async def addpremium_cmd(client, message):
        parts = args_of(message).split()
        uid = parse_uid(parts[0]) if parts else None
        if uid is None and message.reply_to_message and message.reply_to_message.from_user:
            uid = message.reply_to_message.from_user.id
        if uid is None:
            return await message.reply("<b>Usage:</b> <code>/addpremium USER_ID [days]</code>  (0 = lifetime, default 30)")
        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
        until = await xdb.add_premium(uid, days, by=f"admin:{message.from_user.id}")
        await message.reply(f"<b>💎 Premium given to</b> <code>{uid}</code> – until {until_text(until)}.")
        try:
            await client.send_message(uid, f"<b>💎 You got premium!</b>\nNo verification needed until "
                                           f"{until_text(until)}.")
        except Exception:
            pass

    @app.on_message(filters.command("delpremium") & filters.private)
    @admin_only
    async def delpremium_cmd(client, message):
        uid = parse_uid(args_of(message).split()[0]) if args_of(message) else None
        if uid is None:
            return await message.reply("<b>Usage:</b> <code>/delpremium USER_ID</code>")
        ok = await xdb.del_premium(uid)
        await message.reply("<b>✅ Premium removed.</b>" if ok else "<b>That user has no premium.</b>")

    @app.on_message(filters.command("premiumusers") & filters.private)
    @admin_only
    async def premiumusers_cmd(client, message):
        docs = await xdb.list_premium(50)
        if not docs:
            return await message.reply("<b>💎 No premium users yet.</b>")
        lines = [f"• <code>{d['_id']}</code> – {until_text(d.get('until'))}" for d in docs]
        await message.reply("<b>💎 Premium users</b>\n\n" + "\n".join(lines))

    @app.on_message(filters.command("setpremium") & filters.private)
    @admin_only
    async def setpremium_cmd(client, message):
        parts = args_of(message).lower().split()
        if parts and parts[0] in ("off", "0", "disable"):
            await set_setting("premium_stars", 0)
            return await message.reply("<b>💎 Premium sales turned off.</b>")
        if len(parts) < 1 or not parts[0].isdigit() or not 1 <= int(parts[0]) <= 10000:
            s = await settings_now()
            cur = (f"{s.get('premium_stars')} ⭐ / {s.get('premium_days', 30)} days" if s.get("premium_stars")
                   else "off")
            return await message.reply(f"<b>💎 Sell premium for Stars</b> (now: {cur})\n\n"
                                       "<code>/setpremium STARS [DAYS]</code> – e.g. <code>/setpremium 50 30</code>\n"
                                       "<code>/setpremium off</code>\n\n<i>Premium users skip shortener verification. "
                                       "Stars go to this bot's balance (withdraw in @BotFather).</i>")
        days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() and 1 <= int(parts[1]) <= 3650 else 30
        await set_setting("premium_stars", int(parts[0]))
        await set_setting("premium_days", days)
        await message.reply(f"<b>✅ Premium now sells for {int(parts[0])} ⭐ per {days} days.</b>\nUsers see it in /plan.")

    async def show_plan(client, message, user=None):
        user = user or message.from_user
        settings = await settings_now()
        is_p, until = await xdb.premium_until(user.id)
        stars, days = settings.get("premium_stars") or 0, settings.get("premium_days") or 30
        text = "<b>💎 Premium</b>\n\n<blockquote>Premium users open links without shortener verification.</blockquote>\n"
        text += f"\n<b>Your status:</b> {'✅ active – until ' + until_text(until) if is_p else '❌ not premium'}"
        kb = None
        if stars:
            text += f"\n<b>Price:</b> {stars} ⭐ / {days} days"
            kb = Kb([[Btn(f"⭐ ʙᴜʏ {days} ᴅᴀʏs – {stars} sᴛᴀʀs", callback_data="xp:buy")]])
        elif not is_p:
            text += "\n\n<i>Premium isn't for sale here – ask the admin.</i>"
        await message.reply(text, reply_markup=kb)

    @app.on_message(filters.command("plan") & filters.private)
    async def plan_cmd(client, message):
        if message.from_user and await ctx.gate(client, message):
            await show_plan(client, message)

    @app.on_callback_query(filters.regex(r"^xp:(buy|plan)$"))
    async def plan_cb(client, query: CallbackQuery):
        settings = await settings_now()
        if query.matches[0].group(1) == "plan":
            await query.answer()
            return await show_plan(client, query.message, user=query.from_user)
        stars, days = settings.get("premium_stars") or 0, settings.get("premium_days") or 30
        if not stars:
            return await query.answer("Premium isn't for sale right now.", show_alert=True)
        await query.answer()
        await client.send_invoice(chat_id=query.from_user.id, title=f"Premium · {days} days",
                                  description="Skip shortener verification on every link.", currency="XTR",
                                  prices=[LabeledPrice(label=f"Premium {days}d", amount=int(stars))],
                                  payload=f"xp:{days}:{query.from_user.id}:{int(time.time())}")

    # ─────────────────────────── A · Stars checkout ───────────────────────────
    async def expected_price(payload: str, uid: int):
        parts = (payload or "").split(":")
        if len(parts) != 4 or not parts[2].isdigit() or int(parts[2]) != uid:
            return None
        if parts[0] == "xl":
            link = await xdb.get_link(parts[1])
            if not link or not link.get("active", True) or not link.get("stars"):
                return None
            if link.get("expires_at") and link["expires_at"] <= db_now():
                return None
            return int(link["stars"])
        if parts[0] == "xp":
            settings = await settings_now()
            if settings.get("premium_stars") and str(settings.get("premium_days") or 30) == parts[1]:
                return int(settings["premium_stars"])
        return None

    @app.on_pre_checkout_query()
    async def pre_checkout(client, query: PreCheckoutQuery):
        try:
            price = await expected_price(query.payload, query.from_user.id)
            ok = query.currency == "XTR" and price is not None and price == query.total_amount
        except Exception:
            ok = False
        if ok:
            await query.answer(success=True)
        else:
            await query.answer(success=False, error="This invoice is outdated – open the link again.")

    @app.on_message(filters.successful_payment & filters.private, group=-10)
    async def paid(client, message: Message):
        sp = message.successful_payment
        parts = (sp.payload or "").split(":")
        uid = message.from_user.id
        charge = sp.telegram_payment_charge_id
        try:
            if parts[0] == "xl":
                await xdb.add_purchase(parts[1], uid, sp.total_amount, charge)
                await xdb.record_payment(uid, "link", sp.total_amount, charge, parts[1])
                await message.reply(f"<b>✅ Payment received – {sp.total_amount} ⭐. Thank you!</b>")
                await open_smart_link(client, message, parts[1], await ctx.fresh_doc(), paid=True)
            elif parts[0] == "xp":
                days = int(parts[1])
                until = await xdb.add_premium(uid, days, by="stars")
                await xdb.record_payment(uid, "premium", sp.total_amount, charge, str(days))
                await message.reply(f"<b>💎 Premium active until {until_text(until)} – thank you!</b>")
            else:
                return
            await notify_admins(client, f"<b>⭐ New payment</b>\n<code>{uid}</code> paid {sp.total_amount} ⭐ "
                                        f"({'link ' + esc(parts[1]) if parts[0] == 'xl' else 'premium'})")
        except Exception as e:
            log.error(f"[clone {bot_id}] payment handling failed: {e}")
            await message.reply("<b>⚠️ Payment received but delivery failed – contact the admin with this ID:</b>\n"
                                f"<code>{esc(charge)}</code>")
        raise StopPropagation

    async def notify_admins(client, text, reply_markup=None):
        targets = {ctx.owner_id, *(await wdb.get_all_admins())}
        for uid in list(targets)[:20]:
            try:
                await client.send_message(uid, text, reply_markup=reply_markup)
            except Exception:
                pass

    # ─────────────────────────── B · storage channel: index + auto-link ───────────────────────────
    if ctx.log_channel_id:
        @app.on_message(filters.chat(ctx.log_channel_id) & filters.channel, group=3)
        async def channel_post(client, message: Message):
            try:
                if not await xdb.index_file(message):
                    return
                settings = await settings_now()
                if not any(setting(settings, k) for k in ("autolink_dm", "autolink_edit", "autolink_post")):
                    return
                link = await share_link(message.id)
                from filestore.database.extras_db import media_details
                info = media_details(message) or {}
                name = esc(info.get("name", "file"))
                button = Kb([[Btn("📥 ɢᴇᴛ ꜰɪʟᴇ", url=link)]])
                if setting(settings, "autolink_edit") and not message.reply_markup:
                    try:
                        await client.edit_message_reply_markup(message.chat.id, message.id, button)
                    except Exception as e:
                        log.warning(f"[clone {bot_id}] autolink edit failed: {e}")
                if setting(settings, "autolink_post") and settings.get("post_channel"):
                    try:
                        await client.send_message(settings["post_channel"],
                                                  f"<b>📁 {name}</b>\n<i>{human_size(info.get('size', 0))}</i>",
                                                  reply_markup=button)
                    except Exception as e:
                        log.warning(f"[clone {bot_id}] autolink post failed: {e}")
                if setting(settings, "autolink_dm"):
                    try:
                        await client.send_message(ctx.owner_id, f"<b>🔗 New file stored:</b> {name}\n<code>{link}</code>",
                                                  reply_markup=button, disable_web_page_preview=True)
                    except Exception:
                        pass
            except Exception as e:
                log.warning(f"[clone {bot_id}] channel post handling failed: {e}")

    @app.on_message(filters.command("autolink") & filters.private)
    @admin_only
    async def autolink_cmd(client, message):
        parts = args_of(message).lower().split()
        keys = {"dm": "autolink_dm", "edit": "autolink_edit", "button": "autolink_edit", "post": "autolink_post"}
        if len(parts) == 2 and parts[0] in keys and parts[1] in ("on", "off"):
            await set_setting(keys[parts[0]], parts[1] == "on")
        s = await settings_now()
        mark = lambda k: "✅" if setting(s, k) else "▫️"  # noqa: E731
        await message.reply(
            "<b>🤖 Auto-link new storage-channel posts</b>\n\n"
            f"{mark('autolink_dm')} <b>dm</b> – send me the link\n"
            f"{mark('autolink_edit')} <b>edit</b> – add a 📥 button to the stored post\n"
            f"{mark('autolink_post')} <b>post</b> – publish a card in my post channel "
            f"({'<code>' + str(s.get('post_channel')) + '</code>' if s.get('post_channel') else 'not set – /setpostchannel'})\n\n"
            "<code>/autolink dm on</code> · <code>/autolink edit off</code> · <code>/autolink post on</code>\n"
            "<i>New files are always indexed for /search.</i>")

    @app.on_message(filters.command("setpostchannel") & filters.private)
    @admin_only
    async def setpostchannel_cmd(client, message):
        arg = args_of(message)
        if arg.lower() in ("off", "none", "0"):
            await set_setting("post_channel", 0)
            return await message.reply("<b>✅ Post channel removed.</b>")
        if not arg:
            return await message.reply("<b>Usage:</b> <code>/setpostchannel -100…</code> or <code>@channel</code> "
                                       "(add me as admin there first) · <code>/setpostchannel off</code>")
        target = int(arg) if arg.lstrip("-").isdigit() else arg
        try:
            chat = await client.get_chat(target)
            test = await client.send_message(chat.id, "✅ Connected – new files will be posted here.")
            await test.delete()
        except Exception as e:
            return await message.reply(f"<b>❌ I can't post there:</b> <code>{esc(e)}</code>\nMake me an admin first.")
        if chat.id == ctx.log_channel_id:
            return await message.reply("<b>❌ That's the storage channel – pick a different one.</b>")
        await set_setting("post_channel", chat.id)
        await message.reply(f"<b>✅ Post channel set:</b> {esc(chat.title)}\nTurn it on with <code>/autolink post on</code>.")

    @app.on_message(filters.command("index") & filters.private)
    @admin_only
    async def index_cmd(client, message):
        if state["index"] and not state["index"].done():
            return await message.reply("<b>⏳ Indexing is already running.</b>")
        status = await message.reply("<b>🗂 Indexing the storage channel…</b>")

        async def run():
            try:
                probe = await client.send_message(ctx.log_channel_id, "·")
                last = probe.id
                await probe.delete()
            except Exception as e:
                return await status.edit_text(f"<b>❌ Can't read the storage channel:</b> <code>{esc(e)}</code>")
            first = max(1, last - INDEX_LIMIT)
            found, scanned, t0 = 0, 0, time.monotonic()
            for start in range(first, last, 200):
                ids = list(range(start, min(start + 200, last)))
                try:
                    msgs = await client.get_messages(ctx.log_channel_id, ids)
                except FloodWait as e:
                    await asyncio.sleep(e.value + 1)
                    msgs = await client.get_messages(ctx.log_channel_id, ids)
                for m in msgs if isinstance(msgs, list) else [msgs]:
                    if m and not m.empty and await xdb.index_file(m):
                        found += 1
                scanned += len(ids)
                if time.monotonic() - t0 > 8:
                    t0 = time.monotonic()
                    try:
                        await status.edit_text(f"<b>🗂 Indexing…</b> {scanned}/{last - first} posts · {found} files")
                    except Exception:
                        pass
                await asyncio.sleep(0.3)
            await status.edit_text(f"<b>✅ Indexed {found} files</b> ({scanned} posts scanned).\n"
                                   f"Users can search with /search"
                                   f"{'' if setting(await settings_now(), 'search_public') else ' once you turn on /searchmode'}.")

        state["index"] = asyncio.create_task(run())
        ctx.tasks.append(state["index"])

    # ─────────────────────────── B · search ───────────────────────────
    async def search_view(query_text: str, page: int, token: str):
        docs, total = await xdb.search(query_text, page, SEARCH_PAGE)
        if not total:
            return (f"<b>🔍 No files match</b> <code>{esc(query_text)}</code>.\n"
                    "<i>Try fewer words, or /request it.</i>"), None
        pages = (total + SEARCH_PAGE - 1) // SEARCH_PAGE
        rows = [[Btn(f"📁 {d['name'][:48]} · {human_size(d.get('size'))}", url=await share_link(d["_id"]))]
                for d in docs]
        nav = []
        if page > 0:
            nav.append(Btn("⬅️", callback_data=f"xs:{token}:{page - 1}"))
        nav.append(Btn(f"{page + 1}/{pages}", callback_data="xs:noop"))
        if page + 1 < pages:
            nav.append(Btn("➡️", callback_data=f"xs:{token}:{page + 1}"))
        rows.append(nav)
        return f"<b>🔍 {total} result{'s' if total != 1 else ''} for</b> <code>{esc(query_text)}</code>", Kb(rows)

    @app.on_message(filters.command("search") & filters.private)
    async def search_cmd(client, message):
        if not message.from_user:
            return
        settings = await settings_now()
        admin = await is_admin(message.from_user.id)
        if not setting(settings, "search_public") and not admin:
            return await message.reply("<b>🔒 Search is turned off for this bot.</b>")
        if not await ctx.gate(client, message):
            return
        q = args_of(message)
        if len(q) < 2:
            return await message.reply("<b>Usage:</b> <code>/search file name</code>")
        if len(search_cache) > 500:
            for k in sorted(search_cache, key=lambda k: search_cache[k][1])[:250]:
                search_cache.pop(k, None)
        token = secrets.token_hex(3)
        search_cache[token] = (q[:64], time.monotonic())
        text, kb = await search_view(q[:64], 0, token)
        await message.reply(text, reply_markup=kb)

    @app.on_callback_query(filters.regex(r"^xs:"))
    async def search_cb(client, query: CallbackQuery):
        parts = query.data.split(":")
        if len(parts) != 3 or parts[1] not in search_cache or not parts[2].isdigit():
            return await query.answer("Search expired – run /search again." if len(parts) == 3 else "")
        text, kb = await search_view(search_cache[parts[1]][0], int(parts[2]), parts[1])
        await query.answer()
        try:
            await query.message.edit_text(text, reply_markup=kb)
        except MessageNotModified:
            pass

    @app.on_inline_query()
    async def inline_search(client, query: InlineQuery):
        settings = await settings_now()
        if not setting(settings, "search_public") and not await is_admin(query.from_user.id):
            return await query.answer([], cache_time=60, switch_pm_text="Search is off", switch_pm_parameter="help")
        q = query.query.strip()
        if len(q) < 2:
            return await query.answer([], cache_time=5, is_personal=True,
                                      switch_pm_text="Type a file name…", switch_pm_parameter="help")
        page = int(query.offset) if (query.offset or "").isdigit() else 0
        docs, total = await xdb.search(q[:64], page, 20)
        results = []
        for d in docs:
            link = await share_link(d["_id"])
            results.append(InlineQueryResultArticle(
                id=str(d["_id"]), title=d["name"][:100],
                description=f"{d.get('kind', 'file')} · {human_size(d.get('size'))}",
                input_message_content=InputTextMessageContent(f"📁 <b>{esc(d['name'])}</b>"),
                reply_markup=Kb([[Btn("📥 ɢᴇᴛ ꜰɪʟᴇ", url=link)]])))
        next_offset = str(page + 1) if (page + 1) * 20 < total else ""
        await query.answer(results, cache_time=30, is_personal=True, next_offset=next_offset,
                           switch_pm_text=f"{total} result(s)" if total else "No results",
                           switch_pm_parameter="help")

    @app.on_message(filters.command("searchmode") & filters.private)
    @admin_only
    async def searchmode_cmd(client, message):
        arg = args_of(message).lower()
        if arg in ("on", "off"):
            await set_setting("search_public", arg == "on")
        s = await settings_now()
        await message.reply(f"<b>🔍 Public search:</b> {'✅ on' if setting(s, 'search_public') else '▫️ off (admins only)'}\n"
                            f"<b>🗂 Indexed files:</b> {await xdb.indexed_count()}\n\n"
                            "<code>/searchmode on|off</code> · /index to scan older posts\n"
                            "<i>For inline search (@bot name) turn on /setinline for this bot in @BotFather.</i>")

    # ─────────────────────────── C · analytics / export ───────────────────────────
    @app.on_message(filters.command(["analytics", "stats"]) & filters.private)
    @admin_only
    async def analytics_cmd(client, message):
        await message.reply(await analytics_text(bot_id, (await me()).username))

    @app.on_message(filters.command("export") & filters.private)
    async def export_cmd(client, message):
        if not message.from_user or message.from_user.id != ctx.owner_id:
            return
        users = await wdb.full_userbase()
        banned = set(await wdb.get_ban_users())
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["user_id", "banned", "premium"])
        premium = {d["_id"] for d in await xdb.list_premium(100000)}
        for uid in users:
            writer.writerow([uid, uid in banned, uid in premium])
        users_file = io.BytesIO(buf.getvalue().encode())
        users_file.name = f"users_{bot_id}.csv"
        doc = await ctx.fresh_doc()
        backup = {
            "bot_id": bot_id, "bot_username": doc.get("bot_username"), "log_channel_id": doc.get("log_channel_id"),
            "settings": doc.get("settings", {}),
            "shortener": {k: v for k, v in (doc.get("shortener") or {}).items() if k != "api_key_encrypted"},
            "admins": await wdb.get_all_admins(), "fsub_channels": await wdb.show_channels(),
            "exported_at": db_now().isoformat() + "Z", "version": "Videl-FileStore-2.0",
        }
        settings_file = io.BytesIO(json.dumps(backup, indent=2, default=str, ensure_ascii=False).encode())
        settings_file.name = f"settings_{bot_id}.json"
        await message.reply_document(users_file, caption=f"<b>👥 {len(users)} users</b>")
        await message.reply_document(settings_file, caption="<b>⚙️ Settings backup</b>\n<i>Paste it into "
                                                            "📥 Restore in Videl to copy the settings to another bot. "
                                                            "API keys & tokens are not included.</i>")

    # ─────────────────────────── C · broadcast (+ schedule) ───────────────────────────
    async def send_one(client, uid, from_chat, msg_id, flags):
        if flags.get("forward"):
            sent = await client.forward_messages(uid, from_chat, msg_id, disable_notification=flags.get("silent"))
        else:
            sent = await client.copy_message(uid, from_chat, msg_id, disable_notification=flags.get("silent"))
        if flags.get("pin") and sent:
            try:
                await client.pin_chat_message(uid, sent.id, disable_notification=True, both_sides=True)
            except Exception:
                pass

    async def run_broadcast(client, from_chat, msg_id, flags, status: Message | None, by: int):
        users = await wdb.full_userbase()
        stats = {"ok": 0, "removed": 0, "failed": 0}
        cancel = {"flag": False}
        state["broadcast"] = cancel
        kb = Kb([[Btn("⛔ sᴛᴏᴘ", callback_data="xb:stop")]])
        t0 = time.monotonic()
        try:
            for i, uid in enumerate(users, 1):
                if cancel["flag"]:
                    break
                for attempt in range(2):
                    try:
                        await send_one(client, uid, from_chat, msg_id, flags)
                        stats["ok"] += 1
                        break
                    except FloodWait as e:
                        await asyncio.sleep(min(e.value + 1, 300))
                    except DEAD_USER_ERRORS:
                        await wdb.del_user(uid)
                        stats["removed"] += 1
                        break
                    except Exception:
                        stats["failed"] += 1
                        break
                await asyncio.sleep(0.05)
                if status and time.monotonic() - t0 > 5:
                    t0 = time.monotonic()
                    try:
                        await status.edit_text(f"<b>📣 Broadcasting…</b> {i}/{len(users)}\n"
                                               f"✅ {stats['ok']} · 🧹 {stats['removed']} · ❌ {stats['failed']}",
                                               reply_markup=kb)
                    except Exception:
                        pass
        finally:
            state["broadcast"] = None
        report = (f"<b>📣 Broadcast {'stopped' if cancel['flag'] else 'done'}</b>\n\n<blockquote>"
                  f"👥 Users: {len(users)}\n✅ Delivered: {stats['ok']}\n"
                  f"🧹 Removed (blocked / deleted): {stats['removed']}\n❌ Failed: {stats['failed']}</blockquote>")
        if status:
            try:
                return await status.edit_text(report)
            except Exception:
                pass
        try:
            await client.send_message(by, report)
        except Exception:
            pass

    @app.on_message(filters.command("broadcast") & filters.private)
    @admin_only
    async def broadcast_cmd(client, message):
        tokens = args_of(message).split()
        if not message.reply_to_message:
            return await message.reply(
                "<b>📣 Broadcast</b> – reply to the message to send.\n\n<blockquote>"
                "<code>/broadcast</code> – send now\n<code>/broadcast pin silent</code> – pin it, no sound\n"
                "<code>/broadcast forward</code> – forward instead of copy\n"
                "<code>/broadcast in 2h</code> · <code>/broadcast at 21:30</code> – schedule</blockquote>\n"
                "<i>Users who blocked the bot are removed automatically.</i>")
        flags = {k: k in [t.lower() for t in tokens] for k in ("pin", "silent", "forward")}
        from config import LOG_TZ
        try:
            when = parse_when(tokens, LOG_TZ)
        except ValueError as e:
            return await message.reply(f"<b>❌ {esc(e)}</b>")
        if when:
            sid = await xdb.add_schedule(when, message.chat.id, message.reply_to_message.id, flags, message.from_user.id)
            left = int((when - db_now()).total_seconds())
            return await message.reply(f"<b>🗓 Scheduled</b> (id <code>{sid}</code>) – in {human_duration(left)}.\n"
                                       "<i>Keep the original message; see /schedules.</i>")
        if state["broadcast"]:
            return await message.reply("<b>⏳ A broadcast is already running.</b>")
        status = await message.reply("<b>📣 Starting broadcast…</b>")
        task = asyncio.create_task(run_broadcast(client, message.chat.id, message.reply_to_message.id, flags, status,
                                                 message.from_user.id))
        ctx.tasks.append(task)

    @app.on_message(filters.command("schedules") & filters.private)
    @admin_only
    async def schedules_cmd(client, message):
        items = await xdb.list_schedules()
        if not items:
            return await message.reply("<b>🗓 No scheduled broadcasts.</b>")
        rows, lines = [], []
        for s in items:
            left = int((s["run_at"] - db_now()).total_seconds())
            flags = ", ".join(k for k, v in (s.get("flags") or {}).items() if v) or "copy"
            lines.append(f"• <code>{s['_id']}</code> – in {human_duration(max(left, 0))} ({flags})")
            rows.append([Btn(f"🗑 ᴄᴀɴᴄᴇʟ {s['_id']}", callback_data=f"xb:del:{s['_id']}")])
        await message.reply("<b>🗓 Scheduled broadcasts</b>\n\n" + "\n".join(lines), reply_markup=Kb(rows))

    @app.on_callback_query(filters.regex(r"^xb:(stop|del:\w+)$"))
    async def broadcast_cb(client, query: CallbackQuery):
        if not await is_admin(query.from_user.id):
            return await query.answer("Admins only", show_alert=True)
        if query.data == "xb:stop":
            if state["broadcast"]:
                state["broadcast"]["flag"] = True
            return await query.answer("Stopping…")
        ok = await xdb.del_schedule(query.data.split(":", 2)[2])
        await query.answer("🗑 Cancelled" if ok else "Already sent / gone", show_alert=not ok)
        try:
            await query.message.edit_reply_markup(Kb([r for r in query.message.reply_markup.inline_keyboard
                                                      if r[0].callback_data != query.data]) or None)
        except Exception:
            pass

    async def scheduler_loop():
        while True:
            await asyncio.sleep(30)
            try:
                for s in await xdb.due_schedules():
                    if not await xdb.del_schedule(s["_id"]):      # someone else took it
                        continue
                    if state["broadcast"]:
                        await xdb.add_schedule(db_now() + timedelta(minutes=5), s["from_chat"], s["msg_id"],
                                               s.get("flags") or {}, s["by"])
                        continue
                    await run_broadcast(app, s["from_chat"], s["msg_id"], s.get("flags") or {}, None, s["by"])
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"[clone {bot_id}] scheduler: {e}")

    ctx.start_scheduler = scheduler_loop

    # ─────────────────────────── D · requests ───────────────────────────
    def request_kb(rid):
        return Kb([[Btn("✅ ᴅᴏɴᴇ", callback_data=f"xr:done:{rid}"), Btn("❌ ʀᴇᴊᴇᴄᴛ", callback_data=f"xr:rej:{rid}")],
                   [Btn("💬 ʀᴇᴘʟʏ", callback_data=f"xr:reply:{rid}")]])

    @app.on_message(filters.command("request") & filters.private)
    async def request_cmd(client, message):
        if not message.from_user:
            return
        settings = await settings_now()
        if not setting(settings, "requests"):
            return await message.reply("<b>📨 Requests are turned off for this bot.</b>")
        if not await ctx.gate(client, message):
            return
        text = args_of(message)
        if len(text) < 3:
            return await message.reply("<b>Usage:</b> <code>/request Movie name 2024 1080p</code>")
        uid = message.from_user.id
        if not await is_admin(uid):
            limit = REQUESTS_PER_DAY_PREMIUM if await xdb.is_premium(uid) else REQUESTS_PER_DAY
            if await xdb.requests_today(uid) >= limit:
                return await message.reply(f"<b>⏳ You can send {limit} requests a day – try again tomorrow.</b>")
        rid = await xdb.add_request(uid, message.from_user.first_name or "", text)
        await notify_admins(client, f"<b>📨 New request</b> <code>#{rid}</code>\n\n<blockquote>{esc(text[:1000])}</blockquote>\n"
                                    f"👤 {esc(message.from_user.first_name)} (<code>{uid}</code>)",
                            reply_markup=request_kb(rid))
        await message.reply(f"<b>✅ Request sent</b> (<code>#{rid}</code>). You'll be notified here.")

    @app.on_message(filters.command("requests") & filters.private)
    @admin_only
    async def requests_cmd(client, message):
        items = await xdb.open_requests(10)
        if not items:
            return await message.reply("<b>📭 No open requests.</b>")
        for r in items:
            await message.reply(f"<b>📨 #{r['_id']}</b> · <code>{r['user']}</code> {esc(r.get('name'))}\n"
                                f"<blockquote>{esc(r['text'])}</blockquote>", reply_markup=request_kb(r["_id"]))

    @app.on_callback_query(filters.regex(r"^xr:(done|rej|reply):(\w+)$"))
    async def request_cb(client, query: CallbackQuery):
        if not await is_admin(query.from_user.id):
            return await query.answer("Admins only", show_alert=True)
        action, rid = query.matches[0].group(1), query.matches[0].group(2)
        req = await xdb.get_request(rid)
        if not req:
            return await query.answer("Request not found", show_alert=True)
        if action == "reply":
            pending_reply[query.from_user.id] = {"rid": rid, "user": req["user"], "until": time.monotonic() + 600}
            await query.answer()
            return await client.send_message(query.from_user.id, f"<b>💬 Send your reply for #{rid}</b> "
                                                                 "(any message – text, file, link). /cancel to stop.")
        status = "done" if action == "done" else "rejected"
        await xdb.set_request_status(rid, status, query.from_user.id)
        await query.answer("✅ Marked done" if status == "done" else "❌ Rejected")
        note = ("✅ Your request is ready – check the channel / links!" if status == "done"
                else "❌ Sorry, your request couldn't be fulfilled.")
        try:
            await client.send_message(req["user"], f"<b>{note}</b>\n<blockquote>{esc(req['text'][:300])}</blockquote>")
        except Exception:
            pass
        try:
            await query.message.edit_text(f"{getattr(query.message.text, 'html', query.message.text) if query.message.text else ''}\n\n"
                                          f"<b>{'✅ Done' if status == 'done' else '❌ Rejected'}</b> by "
                                          f"<code>{query.from_user.id}</code>")
        except Exception:
            pass

    # ─────────────────────────── D · buttons, help/about, switches ───────────────────────────
    @app.on_message(filters.command("setbuttons") & filters.private)
    @admin_only
    async def setbuttons_cmd(client, message):
        text = args_of(message) or (message.reply_to_message.text if message.reply_to_message else "") or ""
        if not text.strip():
            s = await settings_now()
            current = "\n".join(" | ".join(f"{l} - {u}" for l, u in row) for row in s.get("file_buttons") or []) or "none"
            return await message.reply(
                "<b>🔘 Buttons under delivered files</b>\n\n<blockquote>"
                "<code>/setbuttons Join channel - https://t.me/mychannel | Support - https://t.me/mygroup\n"
                "Website - https://example.com</code></blockquote>\n"
                "One line = one row · <code>|</code> separates buttons · /delbuttons removes them.\n\n"
                f"<b>Now:</b>\n<code>{esc(current)}</code>")
        try:
            rows = parse_buttons(text)
        except ValueError as e:
            return await message.reply(f"<b>❌ {esc(e)}</b>")
        await set_setting("file_buttons", rows)
        await message.reply("<b>✅ Buttons saved – they appear under every delivered file.</b>",
                            reply_markup=file_buttons_markup({"file_buttons": rows}))

    @app.on_message(filters.command("delbuttons") & filters.private)
    @admin_only
    async def delbuttons_cmd(client, message):
        await set_setting("file_buttons", [])
        await message.reply("<b>✅ File buttons removed.</b>")

    async def send_text_setting(client, message, key, fallback, user=None):
        user = user or message.from_user
        settings = await settings_now()
        text = settings.get(key) or ""
        if not text:
            return await message.reply(fallback(settings, await is_admin(user.id)), disable_web_page_preview=True)
        try:
            text = text.replace("{mention}", user.mention).replace("{first}", esc(user.first_name)) \
                       .replace("{bot}", "@" + (await me()).username)
            await message.reply(text, disable_web_page_preview=True)
        except Exception:
            await message.reply(esc(settings.get(key)), disable_web_page_preview=True)

    def default_about(settings, admin):
        return ("<b>ℹ️ About</b>\n\n<blockquote>A file-sharing bot made with Videl.\n"
                "Open a link to receive its files – /help shows everything I can do.</blockquote>")

    @app.on_message(filters.command("help") & filters.private)
    async def help_cmd(client, message):
        if message.from_user:
            await send_text_setting(client, message, "help_text", default_help)

    @app.on_message(filters.command("about") & filters.private)
    async def about_cmd(client, message):
        if message.from_user:
            await send_text_setting(client, message, "about_text", default_about)

    @app.on_callback_query(filters.regex(r"^xh:(help|about)$"))
    async def help_cb(client, query: CallbackQuery):
        await query.answer()
        key = query.matches[0].group(1)
        await send_text_setting(client, query.message, f"{key}_text",
                                default_help if key == "help" else default_about, user=query.from_user)

    async def set_text(message, key, name):
        text = args_of(message)
        if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
            src = message.reply_to_message
            raw = src.text or src.caption
            text = getattr(raw, "html", raw)
        if not text:
            return await message.reply(f"<b>Usage:</b> <code>/set{name} your text</code> (or reply to a message) · "
                                       f"<code>/set{name} reset</code>\n<i>Placeholders: {{mention}} {{first}} {{bot}}</i>")
        if text.strip().lower() == "reset":
            await set_setting(key, "")
            return await message.reply(f"<b>✅ /{name} reset to the default.</b>")
        await set_setting(key, text[:3500])
        await message.reply(f"<b>✅ /{name} updated.</b>")

    @app.on_message(filters.command("sethelp") & filters.private)
    @admin_only
    async def sethelp_cmd(client, message):
        await set_text(message, "help_text", "help")

    @app.on_message(filters.command("setabout") & filters.private)
    @admin_only
    async def setabout_cmd(client, message):
        await set_text(message, "about_text", "about")

    async def toggle_cmd(message, key, label):
        arg = args_of(message).lower()
        if arg in ("on", "off"):
            await set_setting(key, arg == "on")
        s = await settings_now()
        await message.reply(f"<b>{label}:</b> {'✅ on' if setting(s, key) else '▫️ off'}\n"
                            f"<code>/{message.command[0]} on|off</code>")

    @app.on_message(filters.command("maintenance") & filters.private)
    @admin_only
    async def maintenance_cmd(client, message):
        await toggle_cmd(message, "maintenance_mode", "🛠 Maintenance")

    @app.on_message(filters.command("requestmode") & filters.private)
    @admin_only
    async def requestmode_cmd(client, message):
        await toggle_cmd(message, "requests", "📨 File requests")

    @app.on_message(filters.command("antiflood") & filters.private)
    @admin_only
    async def antiflood_cmd(client, message):
        parts = args_of(message).lower().split()
        if parts[:1] == ["autoban"] and len(parts) == 2 and parts[1] in ("on", "off"):
            await set_setting("flood_autoban", parts[1] == "on")
        elif parts[:1] and parts[0] in ("on", "off"):
            await set_setting("antiflood", parts[0] == "on")
        s = await settings_now()
        await message.reply(f"<b>🛡 Anti-flood:</b> {'✅ on' if setting(s, 'antiflood') else '▫️ off'} "
                            f"(more than {FLOOD_LIMIT} messages in {FLOOD_WINDOW}s → pause)\n"
                            f"<b>⛔ Auto-ban after 3 strikes:</b> {'✅ on' if setting(s, 'flood_autoban') else '▫️ off'}\n\n"
                            "<code>/antiflood on|off</code> · <code>/antiflood autoban on|off</code>")

    @app.on_message(filters.command("settings") & filters.private)
    @admin_only
    async def settings_cmd(client, message):
        s = await settings_now()
        await message.reply("<b>⚙️ Feature switches</b>\n<i>Tap to turn on / off.</i>",
                            reply_markup=Kb(settings_panel(s, "xt:")))

    @app.on_callback_query(filters.regex(r"^xt:(\w+)$"))
    async def settings_cb(client, query: CallbackQuery):
        if not await is_admin(query.from_user.id):
            return await query.answer("Admins only", show_alert=True)
        key = query.matches[0].group(1)
        if key not in TOGGLE_KEYS:
            return await query.answer()
        s = await settings_now()
        await set_setting(key, not setting(s, key))
        s[key] = not setting(s, key)
        await query.answer("Saved")
        try:
            await query.message.edit_reply_markup(Kb(settings_panel(s, "xt:")))
        except MessageNotModified:
            pass

    # ─────────────────────────── /start hook ───────────────────────────
    async def handle_start(client, message, param, doc) -> bool:
        if param.startswith("sl_"):
            await open_smart_link(client, message, param[3:], doc)
            return True
        if param == "premium":
            await show_plan(client, message)
            return True
        if param in ("help", "about"):
            await send_text_setting(client, message, f"{param}_text", default_help if param == "help" else default_about)
            return True
        return False

    ctx.handle_start = handle_start
    ctx.open_smart_link = open_smart_link
    ctx.flood_block = flood_block
    ctx.pending_pw = pending_pw


async def after_start(app: Client, ctx):
    """Command menus + background jobs once the clone is connected."""
    try:
        await ctx.xdb.ensure_indexes()
    except Exception:
        pass
    try:
        await app.set_bot_commands([BotCommand(c, d) for c, d in USER_COMMANDS], scope=BotCommandScopeDefault())
        admins = {ctx.owner_id, *(await ctx.worker_db.get_all_admins())}
        for uid in list(admins)[:30]:
            try:
                await app.set_bot_commands([BotCommand(c, d) for c, d in ADMIN_COMMANDS],
                                           scope=BotCommandScopeChat(uid))
            except Exception:
                pass          # admin hasn't started this bot yet
    except Exception as e:
        log.warning(f"[clone {ctx.bot_id}] set commands failed: {e}")
    starter = getattr(ctx, "start_scheduler", None)
    if starter:
        ctx.tasks.append(asyncio.create_task(starter()))

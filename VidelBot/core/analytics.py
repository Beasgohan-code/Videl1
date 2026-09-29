"""
📊 Bot-wide analytics – daily counters, charts and the /analytics screen (admins).

Storage (vdb):
  stats_daily   {_id: "YYYY-MM-DD", encode, rename, save, leech, tool, clone, active}   ← $inc, one doc a day
  stats_active  {_id: "YYYY-MM-DD:uid", d: "YYYY-MM-DD"}                                ← unique daily users
New users come from videl_users.joined and revenue from payments (no double bookkeeping).

bump() / touch() never raise and never block a handler for long: touch() keeps today's user set in
memory so an active user costs ONE upsert a day.
"""
import asyncio
import io
import logging
from datetime import datetime, timedelta, timezone

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import config

log = logging.getLogger("videl.analytics")

METRICS = {                       # key → (label, colour)
    "active": ("👥 Active users", (88, 101, 242)),
    "new": ("🆕 New users", (46, 204, 113)),
    "encode": ("🎬 Encodes", (231, 76, 60)),
    "rename": ("✏️ Renames", (241, 196, 15)),
    "save": ("📥 Saves", (26, 188, 156)),
    "leech": ("🌐 Leeches", (155, 89, 182)),
    "tool": ("🧩 Mux / merge / convert", (230, 126, 34)),
    "clone": ("🤖 New clones", (52, 152, 219)),
    "stars": ("⭐ Stars", (243, 156, 18)),
}
WORK = ("encode", "rename", "save", "leech", "tool")
EVENT_TAGS = {"CloneCreated": "clone"}           # botlog tags counted automatically
RANGES = (7, 30, 90)

_today = ""
_seen: set = set()


def day(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def _db():
    from core.db import vdb
    return vdb.db


async def bump(event: str, n: int = 1):
    """Count one finished job (encode / rename / save / leech / tool / clone)."""
    try:
        await _db()["stats_daily"].update_one({"_id": day()}, {"$inc": {event: n}}, upsert=True)
    except Exception as e:
        log.debug(f"bump {event}: {e}")


def bump_later(event: str, n: int = 1):
    """Fire-and-forget bump from sync code / hot paths."""
    try:
        asyncio.get_running_loop()
        from core.bg import spawn
        spawn(bump(event, n), name="analytics-bump")
    except RuntimeError:
        pass


async def touch(uid: int):
    """Mark `uid` active today (one DB write per user per day)."""
    global _today
    d = day()
    if d != _today:
        _today = d
        _seen.clear()
    if not uid or uid in _seen:
        return
    _seen.add(uid)
    try:
        res = await _db()["stats_active"].update_one({"_id": f"{d}:{uid}"}, {"$setOnInsert": {"d": d}}, upsert=True)
        if res.upserted_id is not None:
            await bump("active")
    except Exception as e:
        log.debug(f"touch: {e}")


# ─────────────────────────── queries ───────────────────────────
def _naive(dt):
    return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt


async def series(days: int = 30) -> dict:
    """{'days': [...labels], metric: [values per day], ...} for the last `days` days (today included)."""
    days = max(1, min(366, int(days)))
    end = datetime.now(timezone.utc)
    labels = [day(end - timedelta(days=days - 1 - i)) for i in range(days)]
    idx = {d: i for i, d in enumerate(labels)}
    out = {"days": labels, **{k: [0] * days for k in METRICS}}
    db = _db()
    try:
        async for doc in db["stats_daily"].find({"_id": {"$gte": labels[0]}}):
            i = idx.get(doc["_id"])
            if i is None:
                continue
            for k in METRICS:
                if k in doc and k not in ("new", "stars"):
                    out[k][i] = int(doc.get(k) or 0)
    except Exception as e:
        log.warning(f"stats_daily read: {e}")
    since = _naive(end - timedelta(days=days))
    try:
        async for u in db["videl_users"].find({"joined": {"$gte": since}}, {"joined": 1}):
            i = idx.get(day(_naive(u["joined"])))
            if i is not None:
                out["new"][i] += 1
    except Exception as e:
        log.warning(f"new users read: {e}")
    try:
        async for p in db["payments"].find({"date": {"$gte": since}}, {"date": 1, "stars": 1, "refunded": 1}):
            i = idx.get(day(_naive(p["date"])))
            if i is not None and not p.get("refunded"):
                out["stars"][i] += int(p.get("stars") or 0)
    except Exception as e:
        log.warning(f"payments read: {e}")
    return out


async def totals() -> dict:
    db = _db()
    res = {}
    for name, coll, query in (("users", "videl_users", {}), ("banned", "videl_users", {"banned": True}),
                              ("blocked", "videl_users", {"blocked": True}),
                              ("payments", "payments", {"refunded": False})):
        try:
            res[name] = await db[coll].count_documents(query)
        except Exception:
            res[name] = 0
    try:
        from filestore.database.mongo import get_db
        res["clones"] = await get_db()["registered_bots"].count_documents({"is_deleted": {"$ne": True}})
    except Exception:
        res["clones"] = 0
    try:
        from filestore.worker_bot.engine import worker_engine
        res["clones_running"] = worker_engine.active_count
    except Exception:
        res["clones_running"] = 0
    try:
        from VideoEncoder import data as q
        res["queue"] = len(q)
    except Exception:
        res["queue"] = 0
    return res


# ─────────────────────────── charts (Pillow) ───────────────────────────
def _font(size: int):
    from PIL import ImageFont
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                           # Pillow < 10.1
        return ImageFont.load_default()


def _plain(label: str) -> str:
    """Drop the emoji – the chart font can't draw them."""
    return label.split(" ", 1)[1] if " " in label and not label[0].isalnum() else label


def chart_png(data: dict, keys, title: str, *, bars: str | None = None, width: int = 1100, height: int = 560) -> bytes:
    """Line chart (optionally the first metric as bars) → PNG bytes."""
    from PIL import Image, ImageDraw
    bg, fg, grid, muted = (18, 20, 28), (236, 238, 245), (48, 52, 66), (150, 156, 175)
    img = Image.new("RGB", (width, height), bg)
    d = ImageDraw.Draw(img)
    f_title, f_axis, f_leg = _font(26), _font(15), _font(16)
    left, right, top, bottom = 70, 30, 70, 90
    pw, ph = width - left - right, height - top - bottom
    labels = data["days"]
    n = len(labels)
    keys = [k for k in keys if k in data]
    series_ = [data[k] for k in keys] + ([data[bars]] if bars else [])
    peak = max([max(s) for s in series_ if s] + [1])
    mag = 10 ** max(0, len(str(int(peak))) - 1)
    tick = next(m * mag for m in (0.25, 0.5, 1, 2, 2.5, 5, 10) if m * mag * 4 >= peak and m * mag >= 1)
    top_val = tick * 4                                        # 4 round grid steps (0 · 25 · 50 · 75 · 100)
    d.text((left, 22), title, font=f_title, fill=fg)
    for i in range(5):                                        # grid + y labels
        y = top + ph - ph * i / 4
        d.line([(left, y), (left + pw, y)], fill=grid, width=1)
        v = top_val * i / 4
        d.text((10, y - 9), f"{v:,.0f}" if top_val >= 8 else f"{v:.1f}", font=f_axis, fill=muted)

    def x_at(i):
        return left + (pw * (i + 0.5) / n)

    def y_at(v):
        return top + ph - ph * (v / top_val)
    if bars:
        colour = METRICS[bars][1]
        bw = max(2, pw / n * 0.6)
        for i, v in enumerate(data[bars]):
            if v:
                d.rectangle([x_at(i) - bw / 2, y_at(v), x_at(i) + bw / 2, top + ph], fill=tuple(c // 2 + 20 for c in colour))
    for k in keys:
        colour = METRICS[k][1]
        pts = [(x_at(i), y_at(v)) for i, v in enumerate(data[k])]
        if len(pts) > 1:
            d.line(pts, fill=colour, width=3, joint="curve")
        r = 3 if n <= 31 else 0
        for x, y in pts if r else []:
            d.ellipse([x - r, y - r, x + r, y + r], fill=colour)
    every = max(1, n // 10)                                    # x labels
    for i in range(0, n, every):
        d.text((x_at(i) - 18, top + ph + 8), labels[i][5:], font=f_axis, fill=muted)
    x = left                                                   # legend
    for k in ([bars] if bars else []) + keys:
        label, colour = METRICS[k]
        text = f"{_plain(label)}: {sum(data[k]):,}"
        d.rectangle([x, height - 38, x + 14, height - 24], fill=colour)
        d.text((x + 20, height - 42), text, font=f_leg, fill=fg)
        x += int(d.textlength(text, font=f_leg)) + 50
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


async def charts(days: int = 30) -> tuple:
    """(users_png, work_png, data) – rendering runs in a thread."""
    data = await series(days)
    users = await asyncio.to_thread(chart_png, data, ["new"], f"Users – last {days} days", bars="active")
    work = await asyncio.to_thread(chart_png, data, list(WORK), f"Jobs – last {days} days")
    return users, work, data


# ─────────────────────────── /analytics ───────────────────────────
def _pct(a: int, b: int) -> str:
    if not b:
        return "new" if a else "–"
    return f"{(a - b) / b * 100:+.0f}%"


async def summary_text(days: int, data: dict | None = None) -> str:
    from core.style import hdr, row, sec
    data = data or await series(days * 2)
    half = len(data["days"]) // 2 if len(data["days"]) >= days * 2 else 0
    t = await totals()
    lines = [hdr("📊", "Analytics", f"last {days} days · UTC"), ""]
    lines.append(sec("👥", "Users"))
    lines += [row("Total", f"{t['users']:,}  <i>(blocked {t['blocked']:,} · banned {t['banned']:,})</i>")]
    for k in ("active", "new"):
        cur = sum(data[k][half:])
        prev = sum(data[k][:half]) if half else 0
        lines.append(row(_plain(METRICS[k][0]), f"{cur:,}  <i>{_pct(cur, prev)}</i>" if half else f"{cur:,}"))
    lines += ["", sec("⚙️", "Jobs")]
    for k in WORK + ("clone",):
        cur = sum(data[k][half:])
        prev = sum(data[k][:half]) if half else 0
        lines.append(row(_plain(METRICS[k][0]), f"{cur:,}  <i>{_pct(cur, prev)}</i>" if half else f"{cur:,}"))
    lines += ["", sec("💰", "Revenue"), row("Stars", f"{sum(data['stars'][half:]):,} ⭐"),
              "", sec("🖥", "Now"),
              row("Clone bots", f"{t['clones_running']:,} running / {t['clones']:,}"),
              row("Encoder queue", str(t["queue"]))]
    return "\n".join(lines)


def web_url() -> str:
    """Public https address of this app's web dashboard, or '' when the host didn't tell us one."""
    url = (config.KEEP_ALIVE_URL or "").strip().rstrip("/")
    return f"{url}/admin" if url.startswith("https://") else ""


def web_rows(private: bool) -> list:
    """🌐 Mini App (signed in by Telegram) + 🔗 one-time browser link – private chats only: a group member
    tapping the link would get the owner's dashboard. In groups only the token page (if a token is set)."""
    import keep_alive
    from pyrogram.types import WebAppInfo
    url = web_url()
    if not url or not keep_alive.enabled():
        return []
    if private:
        return [[Btn("🌐 Open dashboard", web_app=WebAppInfo(url=url)),
                 Btn("🔗 Open in browser", url=f"{url}#l.{keep_alive.issue_link()}")]]
    return [[Btn("🌐 Web dashboard", url=url)]] if config.ADMIN_WEB_TOKEN else []


def web_hint(private: bool) -> str:
    import keep_alive
    if not keep_alive.enabled():
        return ""
    url = web_url()
    if not url:
        return ("\n\n🌐 <b>Web dashboard</b>: this host didn't report its public address – set "
                "<code>KEEP_ALIVE_URL=https://your-app-address</code> and restart to get the button here.")
    if private:
        return (f"\n\n🌐 <b>Web dashboard</b>: <code>{url}</code>\n<i>🌐 opens it inside Telegram (signed in "
                f"automatically) · 🔗 is a one-time browser link, valid 10 minutes.</i>")
    return "\n\n🌐 <i>Send /dashboard in my private chat for the web dashboard buttons.</i>"


def _kb(days: int, private: bool = False) -> InlineKeyboardMarkup:
    rows = [[Btn(("• " if d == days else "") + f"{d} days", callback_data=f"anl:{d}") for d in RANGES]]
    rows += web_rows(private)
    return InlineKeyboardMarkup(rows)


async def send_analytics(client, chat_id: int, days: int = 30, private: bool | None = None):
    from pyrogram.types import InputMediaPhoto
    users, work, _ = await charts(days)
    text = await summary_text(days)
    a, b = io.BytesIO(users), io.BytesIO(work)
    a.name, b.name = "users.png", "jobs.png"
    try:
        await client.send_media_group(chat_id, [InputMediaPhoto(a), InputMediaPhoto(b)])
    except Exception as e:
        log.warning(f"analytics charts: {e}")
    if private is None:
        private = int(chat_id) > 0                    # user chats have positive ids
    await client.send_message(chat_id, text + web_hint(private), reply_markup=_kb(days, private),
                              disable_web_page_preview=True)


@Client.on_message(filters.command(["analytics", "dashboard"]) & filters.user(config.ADMINS))
async def analytics_cmd(client: Client, message: Message):
    parts = (message.text or "").split()
    days = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
    days = max(1, min(180, days))
    wait = await message.reply_text("📊 <i>Crunching the numbers…</i>")
    await send_analytics(client, message.chat.id, days)
    try:
        await wait.delete()
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^anl:\d+$"))
async def analytics_cb(client: Client, query: CallbackQuery):
    if query.from_user.id not in config.ADMINS:
        return await query.answer("Admins only.", show_alert=True)
    days = int(query.data.split(":")[1])
    await query.answer(f"Last {days} days…")
    await send_analytics(client, query.message.chat.id, days)


# ─────────────────────────── housekeeping ───────────────────────────
async def prune_loop(keep_days: int = 120):
    """stats_active grows by one doc per active user a day – keep ~4 months."""
    while True:
        try:
            cutoff = day(datetime.now(timezone.utc) - timedelta(days=keep_days))
            await _db()["stats_active"].delete_many({"d": {"$lt": cutoff}})
        except Exception as e:
            log.debug(f"prune: {e}")
        await asyncio.sleep(24 * 3600)


def start(app):
    from core.bg import spawn
    spawn(prune_loop(), name="analytics-prune")

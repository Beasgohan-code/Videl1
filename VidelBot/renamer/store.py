"""
Auto-Rename storage (lives in the core Videl database).

  rename_users      {_id: uid, template, auto, mode, clean, words[[old, new]…], media, mkv, meta_on, meta{…},
                     count, first_name, username, last_ts}
  rename_log        {user, ts, name, old}         – one row per rename (leaderboards + recent history)
  rename_verify     {_id: uid, token, token_at, shortener, verified_at}
  rename_verify_log {user, ts, shortener}         – verification statistics

Global settings (videl_settings): rn_enabled, rn_nsfw, rn_dump, rn_verify{…}
Captions / thumbnails are shared with the saver (/set_caption, /set_thumb).
"""
import time
from datetime import datetime, timedelta, timezone

from core.db import vdb

META_FIELDS = ("title", "author", "artist", "audio", "subtitle", "video", "encoded_by", "custom_tag")
META_LABELS = {"title": "Title", "author": "Author", "artist": "Artist", "audio": "Audio",
               "subtitle": "Subtitle", "video": "Video", "encoded_by": "Encoded by", "custom_tag": "Custom tag"}
MEDIA_TYPES = ("document", "video", "audio")
MODES = ("auto", "manual")       # auto = template, manual = ask for a name per file
MAX_WORD_RULES = 30

# One-tap template presets (key → (label, template, turn tag-cleaning on))
PRESETS = {
    "anime": ("🎌 Anime", "[S{season}-E{episode}] {title} [{quality}] [{audio}]", False),
    "series": ("📺 Series", "{title} S{season}E{episode} {quality} {source}", False),
    "movie": ("🎬 Movie", "{title} ({year}) {quality} {source} {audio}", False),
    "clean": ("🧹 Keep name, strip tags", "{filename}", True),
}

_cache: dict[int, tuple[float, dict]] = {}
CACHE_TTL = 60


def _users():
    return vdb.db["rename_users"]


def _log():
    return vdb.db["rename_log"]


def _vcol():
    return vdb.db["rename_verify"]


def _vlog():
    return vdb.db["rename_verify_log"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def naive_now() -> datetime:
    """UTC 'now' without tzinfo – what Mongo stores and returns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def aware(dt):
    """Motor returns naive UTC datetimes (tz_aware=False)."""
    if isinstance(dt, datetime) and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def ensure_indexes():
    try:
        await _log().create_index([("ts", -1)])
        await _log().create_index([("user", 1), ("ts", -1)])
        await _log().create_index("ts", expireAfterSeconds=60 * 60 * 24 * 400, name="ttl_ts")
        await _vlog().create_index([("ts", -1)])
        await _users().create_index([("count", -1)])
    except Exception:
        pass


# ─────────────────────────── per-user settings ───────────────────────────
async def get(uid: int) -> dict:
    hit = _cache.get(uid)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    doc = await _users().find_one({"_id": uid}) or {}
    _cache[uid] = (time.time(), doc)
    return doc


def cached(uid: int):
    hit = _cache.get(uid)
    return hit[1] if hit and time.time() - hit[0] < CACHE_TTL else None


async def update(uid: int, **fields):
    await _users().update_one({"_id": uid}, {"$set": fields}, upsert=True)
    _cache.pop(uid, None)


async def unset(uid: int, *fields):
    await _users().update_one({"_id": uid}, {"$unset": {f: "" for f in fields}})
    _cache.pop(uid, None)


async def set_template(uid: int, template: str):
    await update(uid, template=template, auto=True)


async def set_meta(uid: int, field: str, value):
    if field not in META_FIELDS:
        raise ValueError(field)
    if value:
        await update(uid, **{f"meta.{field}": value})
    else:
        await unset(uid, f"meta.{field}")


async def record_rename(user, new_name: str = "", old_name: str = ""):
    now = naive_now()
    await _users().update_one(
        {"_id": user.id},
        {"$inc": {"count": 1}, "$set": {"last_ts": now, "first_name": (user.first_name or "")[:64],
                                        "username": user.username or ""}},
        upsert=True)
    row = {"user": user.id, "ts": now}
    if new_name:
        row.update(name=new_name[:200], old=(old_name or "")[:200])
    await _log().insert_one(row)
    _cache.pop(user.id, None)


async def recent(uid: int, limit: int = 10) -> list:
    """Latest renames of one user (newest first) – rows written before names were logged are skipped."""
    try:
        cur = _log().find({"user": uid, "name": {"$exists": True}}).sort("ts", -1).limit(limit)
        return await cur.to_list(limit)
    except Exception:
        return []


async def total_renames() -> int:
    try:
        rows = await _users().aggregate([{"$group": {"_id": None, "n": {"$sum": "$count"}}}]).to_list(1)
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        return 0


# ─────────────────────────── leaderboard ───────────────────────────
PERIODS = {"today": "Today's", "week": "This week's", "month": "This month's", "year": "This year's",
           "all": "All-time"}


def period_start(period: str, now: datetime = None):
    """Start of the period in the log timezone (returned in UTC)."""
    from core.botlog import now as local_now
    now = now or local_now()
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "year":
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return start.astimezone(timezone.utc)


async def leaderboard(period: str, uid: int = 0, limit: int = 10):
    """→ (rows [(uid, count, first_name, username)], your_rank, your_count)"""
    start = period_start(period)
    if start is None:
        docs = await _users().find({"count": {"$gt": 0}}).sort("count", -1).limit(limit).to_list(limit)
        rows = [(d["_id"], d.get("count", 0), d.get("first_name", ""), d.get("username", "")) for d in docs]
        me = await _users().find_one({"_id": uid}) if uid else None
        mine = (me or {}).get("count", 0)
        rank = (await _users().count_documents({"count": {"$gt": mine}}) + 1) if mine else None
        return rows, rank, mine
    grouped = await _log().aggregate([
        {"$match": {"ts": {"$gte": start.replace(tzinfo=None)}}},
        {"$group": {"_id": "$user", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
    ]).to_list(None)
    names = {}
    top = grouped[:limit]
    if top:
        async for d in _users().find({"_id": {"$in": [g["_id"] for g in top]}}):
            names[d["_id"]] = (d.get("first_name", ""), d.get("username", ""))
    rows = [(g["_id"], g["n"], *names.get(g["_id"], ("", ""))) for g in top]
    rank = mine = None
    for i, g in enumerate(grouped, 1):
        if g["_id"] == uid:
            rank, mine = i, g["n"]
            break
    return rows, rank, mine or 0


# ─────────────────────────── verification ───────────────────────────
DEFAULT_VERIFY = {"s1": {"on": False, "site": "", "api": ""}, "s2": {"on": False, "site": "", "api": ""},
                  "hours": 24, "min_seconds": 60, "tutorial": ""}


async def verify_settings() -> dict:
    cfg = await vdb.get_setting("rn_verify", None) or {}
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_VERIFY.items()}
    for k, v in cfg.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


async def save_verify_settings(cfg: dict):
    await vdb.set_setting("rn_verify", cfg)


def active_shorteners(cfg: dict) -> list:
    return [k for k in ("s1", "s2") if cfg[k].get("on") and cfg[k].get("site") and cfg[k].get("api")]


async def verified_until(uid: int, hours: int):
    doc = await _vcol().find_one({"_id": uid}) or {}
    at = aware(doc.get("verified_at"))
    if not at:
        return None
    until = at + timedelta(hours=hours)
    return until if until > utcnow() else None


async def new_token(uid: int, shortener: str, token: str):
    await _vcol().update_one({"_id": uid}, {"$set": {"token": token, "token_at": naive_now(), "shortener": shortener}},
                             upsert=True)


async def get_token_doc(uid: int) -> dict:
    return await _vcol().find_one({"_id": uid}) or {}


async def clear_token(uid: int):
    await _vcol().update_one({"_id": uid}, {"$unset": {"token": "", "token_at": "", "shortener": ""}})


async def mark_verified(uid: int, shortener: str = ""):
    now = naive_now()
    await _vcol().update_one({"_id": uid}, {"$set": {"verified_at": now},
                                            "$unset": {"token": "", "token_at": "", "shortener": ""}}, upsert=True)
    await _vlog().insert_one({"user": uid, "ts": now, "shortener": shortener})


async def verify_counts() -> dict:
    from core.botlog import now as local_now
    now = local_now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    last_month = (month - timedelta(days=1)).replace(day=1)

    async def count(a, b=None):
        q = {"$gte": a.astimezone(timezone.utc).replace(tzinfo=None)}
        if b is not None:
            q["$lt"] = b.astimezone(timezone.utc).replace(tzinfo=None)
        return await _vlog().count_documents({"ts": q})

    return {"today": await count(today), "yesterday": await count(yesterday, today),
            "week": await count(week), "month": await count(month), "last_month": await count(last_month, month)}

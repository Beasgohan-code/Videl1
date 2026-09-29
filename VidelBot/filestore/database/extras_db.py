"""
Per-clone storage for the extra clone-bot features.

Collections are prefixed ``bot_{id}_`` like WorkerDB, so purge / data-transfer
(`WorkerDB.drop_all_collections`, `copy_data_from`) handle them automatically.

  links       smart links (expiry, max uses, password, Stars price)
  purchases   who bought which paid link
  premium     clone-level premium users (skip shortener / verification)
  payments    Stars payments received by the clone
  files       search index of the storage channel
  daily       per-day analytics counters
  linkstats   per-share-link click counters
  requests    file requests from users
  schedules   scheduled broadcasts
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone

from filestore.database.mongo import get_db


def now() -> datetime:
    """Naive UTC – what Mongo hands back, so comparisons never mix aware/naive."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def day_key(dt: datetime | None = None) -> str:
    return (dt or now()).strftime("%Y-%m-%d")


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()


def media_details(msg) -> dict | None:
    """Name / size / kind of the file in a channel post (None → not a file post)."""
    for kind in ("document", "video", "audio", "animation", "voice", "video_note", "photo", "sticker"):
        m = getattr(msg, kind, None)
        if m:
            name = getattr(m, "file_name", None) or getattr(m, "title", None)
            caption = (msg.caption or "") if getattr(msg, "caption", None) else ""
            if not name:
                name = caption.strip().split("\n", 1)[0][:120] or f"{kind.replace('_', ' ').title()} #{msg.id}"
            return {"name": name, "size": int(getattr(m, "file_size", 0) or 0), "kind": kind,
                    "caption": caption[:300]}
    return None


class CloneExtras:
    def __init__(self, bot_id: int):
        self.bot_id = bot_id
        db = get_db()
        p = f"bot_{bot_id}_"
        self.links = db[p + "links"]
        self.purchases = db[p + "purchases"]
        self.premium = db[p + "premium"]
        self.payments = db[p + "payments"]
        self.files = db[p + "files"]
        self.daily = db[p + "daily"]
        self.linkstats = db[p + "linkstats"]
        self.requests = db[p + "requests"]
        self.schedules = db[p + "schedules"]
        self._indexed = False

    async def ensure_indexes(self):
        if self._indexed:
            return
        self._indexed = True
        try:
            await self.files.create_index("name_lc")
            await self.linkstats.create_index([("clicks", -1)])
            await self.requests.create_index([("status", 1), ("at", -1)])
            await self.schedules.create_index("run_at")
            await self.purchases.create_index([("code", 1), ("user", 1)], unique=True)
        except Exception:
            pass   # index creation is an optimisation only

    # ─────────────────────────── smart links ───────────────────────────
    async def create_link(self, payload: str, by: int, *, expires_at=None, max_uses=0,
                          password="", stars=0, note="") -> dict:
        code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        salt = secrets.token_hex(4)
        doc = {"_id": code, "payload": payload, "by": by, "created_at": now(),
               "expires_at": expires_at, "max_uses": int(max_uses or 0), "uses": 0, "users": [],
               "clicks": 0, "salt": salt, "password": hash_password(password, salt) if password else "",
               "stars": int(stars or 0), "note": note[:60], "active": True}
        await self.links.insert_one(doc)
        return doc

    async def get_link(self, code: str) -> dict | None:
        return await self.links.find_one({"_id": code})

    async def list_links(self, limit=10, skip=0) -> list:
        return await self.links.find({}).sort("created_at", -1).skip(skip).limit(limit).to_list(length=limit)

    async def count_links(self) -> int:
        return await self.links.count_documents({})

    async def delete_link(self, code: str) -> bool:
        return (await self.links.delete_one({"_id": code})).deleted_count > 0

    async def claim_use(self, link: dict, user_id: int) -> bool:
        """Count this user against the link's use limit. Re-opening by the same user is free."""
        await self.links.update_one({"_id": link["_id"]}, {"$inc": {"clicks": 1}})
        if not link.get("max_uses"):
            return True
        if user_id in (link.get("users") or []):
            return True
        res = await self.links.update_one(
            {"_id": link["_id"], "uses": {"$lt": link["max_uses"]}, "users": {"$ne": user_id}},
            {"$inc": {"uses": 1}, "$addToSet": {"users": user_id}})
        if res.modified_count:
            return True
        fresh = await self.get_link(link["_id"]) or {}
        return user_id in (fresh.get("users") or [])

    @staticmethod
    def check_password(link: dict, password: str) -> bool:
        return bool(link.get("password")) and secrets.compare_digest(
            link["password"], hash_password(password.strip(), link.get("salt", "")))

    async def add_purchase(self, code: str, user_id: int, stars: int, charge_id: str):
        try:
            await self.purchases.insert_one({"code": code, "user": user_id, "stars": stars,
                                             "charge_id": charge_id, "at": now()})
        except Exception:
            pass    # duplicate → already bought

    async def has_purchase(self, code: str, user_id: int) -> bool:
        return bool(await self.purchases.find_one({"code": code, "user": user_id}))

    # ─────────────────────────── premium ───────────────────────────
    async def add_premium(self, user_id: int, days: int, by: str = "admin") -> datetime | None:
        """days <= 0 → lifetime. Extends an existing premium instead of resetting it."""
        doc = await self.premium.find_one({"_id": user_id})
        if days <= 0:
            until = None
        else:
            base = now()
            if doc and doc.get("until") and doc["until"] > base:
                base = doc["until"]
            until = base + timedelta(days=days)
        await self.premium.update_one({"_id": user_id}, {"$set": {"until": until, "by": by, "since": now()}},
                                      upsert=True)
        return until

    async def del_premium(self, user_id: int) -> bool:
        return (await self.premium.delete_one({"_id": user_id})).deleted_count > 0

    async def premium_until(self, user_id: int):
        """(is_premium, until) – until None means lifetime."""
        doc = await self.premium.find_one({"_id": user_id})
        if not doc:
            return False, None
        until = doc.get("until")
        if until is not None and until <= now():
            await self.premium.delete_one({"_id": user_id})
            return False, None
        return True, until

    async def is_premium(self, user_id: int) -> bool:
        return (await self.premium_until(user_id))[0]

    async def list_premium(self, limit=50) -> list:
        return await self.premium.find({}).limit(limit).to_list(length=limit)

    async def record_payment(self, user_id: int, kind: str, stars: int, charge_id: str, ref=""):
        await self.payments.insert_one({"user": user_id, "kind": kind, "stars": stars,
                                        "charge_id": charge_id, "ref": ref, "at": now()})

    async def stars_earned(self) -> int:
        total = 0
        async for d in self.payments.find({}, {"stars": 1}):
            total += int(d.get("stars") or 0)
        return total

    # ─────────────────────────── search index ───────────────────────────
    async def index_file(self, msg) -> bool:
        d = media_details(msg)
        if not d:
            return False
        await self.files.update_one({"_id": msg.id}, {"$set": {**d, "name_lc": d["name"].lower(), "at": now()}},
                                    upsert=True)
        return True

    async def search(self, query: str, page: int = 0, per_page: int = 8):
        words = [re.escape(w) for w in query.lower().split() if w][:6]
        if not words:
            return [], 0
        flt = {"$and": [{"name_lc": {"$regex": w}} for w in words]}
        total = await self.files.count_documents(flt)
        docs = await self.files.find(flt).sort("_id", -1).skip(page * per_page).limit(per_page).to_list(length=per_page)
        return docs, total

    async def indexed_count(self) -> int:
        return await self.files.count_documents({})

    # ─────────────────────────── analytics ───────────────────────────
    async def record_delivery(self, payload: str, files: int, label: str = ""):
        await self.daily.update_one({"_id": day_key()}, {"$inc": {"deliveries": 1, "files": files}}, upsert=True)
        upd = {"$inc": {"clicks": 1, "files": files}, "$set": {"last": now()}}
        if label:
            upd["$setOnInsert"] = {"label": label[:60]}
        await self.linkstats.update_one({"_id": payload}, upd, upsert=True)

    async def record_new_user(self):
        await self.daily.update_one({"_id": day_key()}, {"$inc": {"new_users": 1}}, upsert=True)

    async def days(self, n: int = 14) -> list:
        keys = [day_key(now() - timedelta(days=i)) for i in range(n - 1, -1, -1)]
        docs = {d["_id"]: d async for d in self.daily.find({"_id": {"$in": keys}})}
        return [(k, docs.get(k, {})) for k in keys]

    async def top_links(self, limit=5) -> list:
        return await self.linkstats.find({}).sort("clicks", -1).limit(limit).to_list(length=limit)

    # ─────────────────────────── requests ───────────────────────────
    async def add_request(self, user_id: int, name: str, text: str) -> str:
        rid = secrets.token_hex(4)
        await self.requests.insert_one({"_id": rid, "user": user_id, "name": name[:64], "text": text[:1000],
                                        "status": "open", "at": now()})
        return rid

    async def get_request(self, rid: str) -> dict | None:
        return await self.requests.find_one({"_id": rid})

    async def set_request_status(self, rid: str, status: str, by: int):
        await self.requests.update_one({"_id": rid}, {"$set": {"status": status, "by": by, "closed_at": now()}})

    async def open_requests(self, limit=10) -> list:
        return await self.requests.find({"status": "open"}).sort("at", -1).limit(limit).to_list(length=limit)

    async def requests_today(self, user_id: int) -> int:
        start = now().replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.requests.count_documents({"user": user_id, "at": {"$gte": start}})

    # ─────────────────────────── scheduled broadcasts ───────────────────────────
    async def add_schedule(self, run_at: datetime, from_chat: int, msg_id: int, flags: dict, by: int) -> str:
        sid = secrets.token_hex(3)
        await self.schedules.insert_one({"_id": sid, "run_at": run_at, "from_chat": from_chat, "msg_id": msg_id,
                                         "flags": flags, "by": by, "created_at": now()})
        return sid

    async def due_schedules(self) -> list:
        return await self.schedules.find({"run_at": {"$lte": now()}}).to_list(length=20)

    async def list_schedules(self) -> list:
        return await self.schedules.find({}).sort("run_at", 1).to_list(length=20)

    async def del_schedule(self, sid: str) -> bool:
        return (await self.schedules.delete_one({"_id": sid})).deleted_count > 0

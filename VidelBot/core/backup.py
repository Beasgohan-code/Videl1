"""
💾 Daily database backups (owners).

  • every day at BACKUP_HOUR (LOG_TZ) a gzip JSON of all four databases goes to BACKUP_CHAT (default: first owner)
  • /backup            → a backup right now
  • reply /backup to a backup file → preview + ♻️ Restore button (upsert by _id – never deletes anything)

Secrets are stripped unless BACKUP_INCLUDE_SECRETS=true: saver login sessions (full account access!),
API hashes, passwords. Clone bot tokens are kept – they're stored encrypted with SECRET_KEY / the env key.
Documents are streamed to disk one by one, so memory stays flat even for big databases.
"""
import asyncio
import gzip
import html
import json
import logging
import os
import tempfile
import time
from datetime import datetime, timedelta

from bson import json_util
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import config

log = logging.getLogger("videl.backup")

FORMAT = "videl-backup/1"
SECRET_FIELDS = {"session", "string_session", "session_string", "api_hash", "password", "phone_code_hash",
                 "two_fa", "otp"}
SKIP_COLLECTIONS = {"stats_active", "support_map", "enc_queue"}       # recomputable / short-lived
_pending: dict = {}      # owner uid → (chat_id, message_id, ts) of a backup file waiting for confirmation


def databases() -> dict:
    """logical key → the module's own database handle (restores map by key, so renamed DBs still work)."""
    from core.db import vdb
    from database.db import db as saver_db
    from VideoEncoder.utils.database.access_db import db as enc_db
    from filestore.database.mongo import get_db
    return {"videl": vdb.db, "saver": saver_db.db, "encoder": enc_db.db, "filestore": get_db()}


def scrub(doc: dict, include_secrets: bool) -> dict:
    if include_secrets:
        return doc
    return {k: (None if k in SECRET_FIELDS and v else v) for k, v in doc.items()}


async def dump(path: str, include_secrets: bool | None = None) -> dict:
    """Write the backup to `path` (gzip JSON). Returns {'db.collection': count}."""
    include = config.BACKUP_INCLUDE_SECRETS if include_secrets is None else include_secrets
    counts = {}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        meta = {"format": FORMAT, "bot": config.BOT_NAME, "created": datetime.utcnow().isoformat() + "Z",
                "secrets": bool(include)}
        fh.write('{"meta": ' + json.dumps(meta) + ', "dbs": {')
        for di, (key, db) in enumerate(databases().items()):
            fh.write(("," if di else "") + json.dumps(key) + ": {")
            name = getattr(db, "name", key)
            try:
                names = sorted(n for n in await db.list_collection_names() if n not in SKIP_COLLECTIONS
                               and not n.startswith("system."))
            except Exception as e:
                log.warning(f"backup: can't list {name}: {e}")
                names = []
            for ci, coll in enumerate(names):
                fh.write(("," if ci else "") + json.dumps(coll) + ": [")
                n = 0
                async for doc in db[coll].find({}):
                    fh.write(("," if n else "") + json_util.dumps(scrub(doc, include)))
                    n += 1
                    if n % 2000 == 0:
                        await asyncio.sleep(0)            # stay responsive on big collections
                fh.write("]")
                counts[f"{key}.{coll}"] = n
            fh.write("}")
        fh.write("}}")
    return counts


def load(path: str) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        data = json_util.loads(fh.read())
    if not isinstance(data, dict) or (data.get("meta") or {}).get("format") != FORMAT:
        raise ValueError("This isn't a Videl backup file.")
    return data


async def restore(data: dict) -> dict:
    """Upsert every document by _id. Returns {'db.collection': count}. Never deletes."""
    dbs = databases()
    counts = {}
    for key, colls in (data.get("dbs") or {}).items():
        if key not in dbs:
            continue
        db = dbs[key]
        for coll, docs in colls.items():
            n = 0
            for doc in docs:
                if "_id" not in doc:
                    continue
                clean = {k: v for k, v in doc.items()
                         if k != "_id" and not (k in SECRET_FIELDS and v is None)}      # keep live sessions
                if not clean:
                    continue
                await db[coll].update_one({"_id": doc["_id"]}, {"$set": clean}, upsert=True)
                n += 1
                if n % 500 == 0:
                    await asyncio.sleep(0)
            counts[f"{key}.{coll}"] = n
    return counts


def _summary(counts: dict) -> str:
    per_db = {}
    for k, n in counts.items():
        db = k.split(".", 1)[0]
        per_db[db] = per_db.get(db, 0) + n
    return " · ".join(f"{db} {n:,}" for db, n in per_db.items()) or "empty"


async def send_backup(client, chat_id: int, reason: str = "daily") -> bool:
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M")
    path = os.path.join(tempfile.gettempdir(), f"videl-backup-{stamp}.json.gz")
    t0 = time.time()
    try:
        counts = await dump(path)
        size = os.path.getsize(path)
        cap = (f"💾 <b>{html.escape(config.BOT_NAME)} backup</b> · {reason}\n"
               f"<blockquote>{_summary(counts)} documents\n{size / 1048576:.2f} MB · {time.time() - t0:.1f}s · "
               f"secrets {'included ⚠️' if config.BACKUP_INCLUDE_SECRETS else 'stripped'}</blockquote>\n"
               "<i>Restore: reply /backup to this file.</i>")
        await client.send_document(chat_id, path, caption=cap, file_name=os.path.basename(path))
        return True
    except Exception as e:
        log.error(f"backup failed: {e}")
        try:
            await client.send_message(chat_id, f"⚠️ <b>Backup failed:</b> <code>{html.escape(str(e))[:300]}</code>")
        except Exception:
            pass
        return False
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _target() -> int:
    return config.BACKUP_CHAT or (config.OWNERS[0] if config.OWNERS else 0)


def _seconds_until(hour: int) -> float:
    from core.botlog import now
    cur = now()
    nxt = cur.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    if nxt <= cur:
        nxt += timedelta(days=1)
    return (nxt - cur).total_seconds()


async def backup_loop(app):
    while True:
        await asyncio.sleep(_seconds_until(config.BACKUP_HOUR))
        if _target():
            await send_backup(app, _target())
        await asyncio.sleep(60)


def start(app):
    if config.BACKUP_ENABLED and _target():
        from core.bg import spawn
        spawn(backup_loop(app), name="daily-backup")
        log.info(f"💾 daily backup at {config.BACKUP_HOUR:02d}:00 {config.LOG_TZ} → {_target()}")


# ─────────────────────────── /backup ───────────────────────────
@Client.on_message(filters.command("backup") & filters.user(config.OWNERS))
async def backup_cmd(client: Client, message: Message):
    doc = message.reply_to_message.document if message.reply_to_message else None
    if doc:
        if not (doc.file_name or "").endswith(".json.gz"):
            return await message.reply_text("⚠️ Reply to a <code>.json.gz</code> Videl backup file.")
        _pending[message.from_user.id] = (message.chat.id, message.reply_to_message.id, time.time())
        return await message.reply_text(
            f"♻️ <b>Restore this backup?</b>\n<code>{html.escape(doc.file_name)}</code> · {doc.file_size / 1048576:.2f} MB\n\n"
            "<blockquote>Every document is <b>upserted</b> by its id – existing data is updated, nothing is deleted. "
            "Stripped secrets (login sessions) are left as they are now.</blockquote>",
            reply_markup=InlineKeyboardMarkup([[Btn("♻️ Restore", callback_data="bkr:yes"),
                                                Btn("✖️ Cancel", callback_data="bkr:no")]]))
    wait = await message.reply_text("💾 <i>Creating a backup…</i>")
    ok = await send_backup(client, message.chat.id, reason="manual")
    try:
        await wait.edit_text("✅ Backup sent." if ok else "⚠️ Backup failed – see above.")
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^bkr:(yes|no)$"))
async def backup_restore_cb(client: Client, query: CallbackQuery):
    uid = query.from_user.id
    if uid not in config.OWNERS:
        return await query.answer("Owners only.", show_alert=True)
    pending = _pending.pop(uid, None)
    if query.data == "bkr:no" or not pending or time.time() - pending[2] > 600:
        await query.answer("Cancelled." if query.data == "bkr:no" else "Expired – reply /backup again.")
        return await query.message.edit_text("✖️ Restore cancelled.")
    await query.answer("Restoring…")
    await query.message.edit_text("♻️ <i>Downloading and restoring…</i>")
    path = None
    try:
        src = await client.get_messages(pending[0], pending[1])
        path = await src.download(file_name=os.path.join(tempfile.gettempdir(), ""))
        data = await asyncio.to_thread(load, path)
        counts = await restore(data)
        meta = data.get("meta") or {}
        await query.message.edit_text(f"✅ <b>Restore complete</b>\n<blockquote>{_summary(counts)} documents upserted\n"
                                      f"from {html.escape(str(meta.get('created', '?')))}</blockquote>")
    except Exception as e:
        log.error(f"restore failed: {e}")
        await query.message.edit_text(f"⚠️ <b>Restore failed:</b> <code>{html.escape(str(e))[:300]}</code>")
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

"""
💾 Daily database backups & validated non-destructive restore (owners).

  • every day at BACKUP_HOUR (LOG_TZ) a gzip JSON of all four databases goes to BACKUP_CHAT (default: first owner)
  • /backup                → create and send a backup right now
  • reply /backup to a .json.gz backup file → preview + 🔍 Dry-Run / ♻️ Restore buttons (upserts by _id – never deletes)

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
from datetime import datetime, timedelta, timezone

from bson import json_util
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import config

log = logging.getLogger("videl.backup")

FORMAT = "videl-backup/1"
MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024  # 256 MiB decompression cap (gzip-bomb guard)
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
    created_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        meta = {"format": FORMAT, "bot": config.BOT_NAME, "created": created_iso,
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


def validate_backup_payload(data: dict) -> None:
    """Validate top-level backup envelope and `dbs` layout before touching MongoDB."""
    if not isinstance(data, dict) or (data.get("meta") or {}).get("format") != FORMAT:
        raise ValueError("This isn't a Videl backup file.")
    dbs_payload = data.get("dbs")
    if dbs_payload is not None and not isinstance(dbs_payload, dict):
        raise ValueError("Invalid backup structure: 'dbs' must be a JSON object.")
    for db_key, colls in (dbs_payload or {}).items():
        if not isinstance(db_key, str) or not isinstance(colls, dict):
            raise ValueError(f"Invalid collection map for database '{db_key}'.")
        for coll_name, docs in colls.items():
            if not isinstance(coll_name, str) or coll_name.startswith("system.") or not isinstance(docs, list):
                raise ValueError(f"Invalid document list for '{db_key}.{coll_name}'.")


def load(path: str, max_bytes: int = MAX_UNCOMPRESSED_BYTES) -> dict:
    """Safely decompress and parse a `.json.gz` backup with an uncompressed size cap."""
    chunks: list[str] = []
    total = 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        while True:
            piece = fh.read(1024 * 1024)
            if not piece:
                break
            total += len(piece.encode("utf-8"))
            if total > max_bytes:
                raise ValueError(f"Backup exceeds maximum uncompressed size ({max_bytes // (1024 * 1024)} MiB).")
            chunks.append(piece)
    data = json_util.loads("".join(chunks))
    validate_backup_payload(data)
    return data


async def restore_with_report(data: dict, *, dry_run: bool = False) -> dict:
    """Validate and optionally upsert every document by `_id`. Never deletes existing documents."""
    validate_backup_payload(data)
    dbs = databases()
    counts: dict[str, int] = {}
    inserts = 0
    updates = 0
    skipped = 0
    preserved_secrets = 0
    unknown_dbs: list[str] = []

    for key, colls in (data.get("dbs") or {}).items():
        if key not in dbs:
            unknown_dbs.append(key)
            continue
        db = dbs[key]
        for coll, docs in colls.items():
            if coll in SKIP_COLLECTIONS or coll.startswith("system."):
                continue
            n = 0
            for doc in docs:
                if not isinstance(doc, dict) or "_id" not in doc:
                    skipped += 1
                    continue
                stripped_here = sum(1 for k, v in doc.items() if k in SECRET_FIELDS and v is None)
                preserved_secrets += stripped_here
                clean = {k: v for k, v in doc.items()
                         if k != "_id" and not (k in SECRET_FIELDS and v is None)}      # keep live sessions
                if not clean:
                    skipped += 1
                    continue
                if dry_run:
                    exists = await db[coll].find_one({"_id": doc["_id"]}, {"_id": 1})
                    if exists:
                        updates += 1
                    else:
                        inserts += 1
                else:
                    res = await db[coll].update_one({"_id": doc["_id"]}, {"$set": clean}, upsert=True)
                    if res.upserted_id is not None:
                        inserts += 1
                    else:
                        updates += 1
                n += 1
                if n % 500 == 0:
                    await asyncio.sleep(0)
            counts[f"{key}.{coll}"] = n

    if not dry_run:
        # Refresh in-memory ban list, global settings, and DB admins so restored state takes effect immediately
        try:
            from core.db import vdb
            await vdb.warm_up()
            vdb._known.clear()
        except Exception as exc:
            log.warning(f"backup post-restore warm_up warning: {exc}")
        try:
            from core.admins import load_db_admins
            await load_db_admins()
        except Exception as exc:
            log.warning(f"backup post-restore admin reload warning: {exc}")

    return {
        "dry_run": dry_run,
        "counts": counts,
        "would_insert": inserts,
        "would_update": updates,
        "skipped_invalid": skipped,
        "preserved_secrets": preserved_secrets,
        "unknown_dbs": unknown_dbs,
    }


async def restore(data: dict, *, dry_run: bool = False) -> dict:
    """Upsert every document by _id. Returns {'db.collection': count}. Never deletes."""
    report = await restore_with_report(data, dry_run=dry_run)
    return report["counts"]


def _summary(counts: dict) -> str:
    per_db = {}
    for k, n in counts.items():
        db = k.split(".", 1)[0]
        per_db[db] = per_db.get(db, 0) + n
    return " · ".join(f"{db} {n:,}" for db, n in per_db.items()) or "empty"


async def send_backup(client, chat_id: int, reason: str = "daily") -> bool:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
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


def _db_doctor_doc(rep: dict):
    from core.rich import Doc
    from core.ui import styled_button as btn

    is_dry = rep["dry_run"]
    total_red = rep["total_redundant_docs"]
    pt_sec = rep.get("plaintext_secrets", 0)
    enc_on = rep.get("encryption_enabled", False)
    if enc_on:
        sec_status = f"🟢 Fernet active · {pt_sec} plaintext" if pt_sec else "🟢 Fernet active · all encrypted"
    else:
        sec_status = f"⚪ Key unset ({pt_sec} plaintext)"
    title = "Database Integrity Check" if is_dry else "Database Maintenance Complete"
    sub = "read-only dry-run audit" if is_dry else f"merged {rep['merged_users']} group(s) · encrypted {rep.get('encrypted_upgraded', 0)}"
    doc = Doc("🩺" if is_dry else "✅", title, sub)
    doc.kv_table([
        ("📥 Saver duplicates", f"{rep['saver_duplicate_users']} user(s) · {rep['saver_redundant_docs']} extra row(s)"),
        ("🎬 Encoder duplicates", f"{rep['encoder_duplicate_users']} user(s) · {rep['encoder_redundant_docs']} extra row(s)"),
        ("👤 Core user duplicates", f"{rep['videl_duplicate_users']} user(s)"),
        ("🔐 Secret encryption", sec_status),
        ("⭐ Payments recorded", f"{rep['payments_total']} ({rep['payments_missing_charge']} missing charge_id)"),
        ("🔁 Active subscriptions", str(rep["active_subscriptions"])),
        ("🤖 Registered clones", str(rep["clones_total"])),
        ("🧹 Removed extra rows", str(rep["removed_docs"]) if not is_dry else None),
        ("🔒 Secrets encrypted", str(rep.get("encrypted_upgraded", 0)) if not is_dry and rep.get("encrypted_upgraded") else None),
    ], key_header="Check", val_header="Status")
    rows = []
    if is_dry and total_red > 0:
        doc.quote(
            f"Found {total_red} redundant duplicate row(s). Merging preserves Premium, Lifetime status, "
            "login sessions, thumbnails, captions, word filters, and max save counters."
        )
        rows.append([btn(f"🧹 Merge {total_red} Duplicate Row(s)", callback_data="bkr:dedupe_ok", style="success")])
    if is_dry and enc_on and pt_sec > 0:
        rows.append([btn(f"🔐 Encrypt {pt_sec} Legacy Secret(s)", callback_data="bkr:encrypt_ok", style="primary")])
    if is_dry and total_red == 0 and (not enc_on or pt_sec == 0):
        doc.status("ok", "All collections clean", "Zero duplicate user documents detected.")
    rows.append([btn("✖️ Close", callback_data="close_btn", style="danger" if rows else None)])
    return doc, InlineKeyboardMarkup(rows)


# ─────────────────────────── /backup ───────────────────────────
@Client.on_message(filters.command("backup") & filters.user(config.OWNERS))
async def backup_cmd(client: Client, message: Message):
    subcmd = (message.command[1].lower() if getattr(message, "command", None) and len(message.command) > 1 else "")
    if subcmd in ("check", "doctor", "dedupe"):
        from core import db_doctor, rich
        rep = await db_doctor.inspect_and_deduplicate(dry_run=True)
        doc_ui, kb = _db_doctor_doc(rep)
        return await rich.reply(message, doc_ui, reply_markup=kb)

    doc = message.reply_to_message.document if message.reply_to_message else None
    if doc:
        if not (doc.file_name or "").endswith(".json.gz"):
            return await message.reply_text("⚠️ Reply to a <code>.json.gz</code> Videl backup file.")
        _pending[message.from_user.id] = (message.chat.id, message.reply_to_message.id, time.time())
        from core.ui import styled_button as btn
        return await message.reply_text(
            f"♻️ <b>Restore this backup?</b>\n<code>{html.escape(doc.file_name)}</code> · {doc.file_size / 1048576:.2f} MB\n\n"
            "<blockquote>Every document is <b>upserted</b> by its id – existing data is updated, nothing is deleted. "
            "Stripped secrets (login sessions) are left as they are now.\n"
            "Use <b>🔍 Dry-Run</b> first to validate and preview exact insert/update counts without modifying MongoDB.</blockquote>",
            reply_markup=InlineKeyboardMarkup([
                [btn("🔍 Dry-Run", callback_data="bkr:dry", style="primary"),
                 btn("♻️ Restore", callback_data="bkr:yes", style="success")],
                [btn("✖️ Cancel", callback_data="bkr:no", style="danger")],
            ]))
    wait = await message.reply_text("💾 <i>Creating a backup…</i>")
    ok = await send_backup(client, message.chat.id, reason="manual")
    try:
        await wait.edit_text("✅ Backup sent." if ok else "⚠️ Backup failed – see above.")
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^bkr:(yes|no|dry|dedupe_ok|encrypt_ok)$"))
async def backup_restore_cb(client: Client, query: CallbackQuery):
    uid = query.from_user.id
    if uid not in config.OWNERS:
        return await query.answer("Owners only.", show_alert=True)
    if query.data in ("bkr:dedupe_ok", "bkr:encrypt_ok"):
        from core import db_doctor, rich
        is_enc_only = query.data == "bkr:encrypt_ok"
        await query.answer("Encrypting legacy secrets…" if is_enc_only else "Merging duplicates…")
        rep = await db_doctor.inspect_and_deduplicate(
            dry_run=is_enc_only,
            encrypt_secrets=True,
        )
        doc_ui, kb = _db_doctor_doc(rep)
        return await rich.edit(query.message, doc_ui, reply_markup=kb)
    is_dry = query.data == "bkr:dry"
    pending = _pending.get(uid) if is_dry else _pending.pop(uid, None)
    if query.data == "bkr:no" or not pending or time.time() - pending[2] > 600:
        _pending.pop(uid, None)
        await query.answer("Cancelled." if query.data == "bkr:no" else "Expired – reply /backup again.")
        return await query.message.edit_text("✖️ Restore cancelled.")
    await query.answer("Running dry-run…" if is_dry else "Restoring…")
    await query.message.edit_text("🔍 <i>Downloading and validating (dry-run)…</i>" if is_dry
                                  else "♻️ <i>Downloading and restoring…</i>")
    path = None
    try:
        src = await client.get_messages(pending[0], pending[1])
        path = await src.download(file_name=os.path.join(tempfile.gettempdir(), ""))
        data = await asyncio.to_thread(load, path)
        rep = await restore_with_report(data, dry_run=is_dry)
        meta = data.get("meta") or {}
        created = html.escape(str(meta.get("created", "?")))
        if is_dry:
            _pending[uid] = (pending[0], pending[1], time.time())  # refresh confirmation window
            from core.ui import btn
            await query.message.edit_text(
                f"🔍 <b>Backup Dry-Run Preview</b> <i>(no database changes made)</i>\n"
                f"<blockquote><b>Summary:</b> {_summary(rep['counts'])} documents\n"
                f"<b>New inserts:</b> <code>{rep['would_insert']:,}</code> · "
                f"<b>Updates:</b> <code>{rep['would_update']:,}</code>\n"
                f"<b>Live secrets preserved:</b> <code>{rep['preserved_secrets']:,}</code> · "
                f"<b>Invalid skipped:</b> <code>{rep['skipped_invalid']:,}</code>\n"
                f"<b>Created:</b> {created}</blockquote>",
                reply_markup=InlineKeyboardMarkup([
                    [btn("♻️ Apply Restore Now", callback_data="bkr:yes", style="success"),
                     btn("✖️ Cancel", callback_data="bkr:no", style="danger")],
                ]),
            )
        else:
            await query.message.edit_text(
                f"✅ <b>Restore complete</b>\n"
                f"<blockquote>{_summary(rep['counts'])} documents upserted "
                f"(<code>{rep['would_insert']:,}</code> inserted, <code>{rep['would_update']:,}</code> updated)\n"
                f"from {created}</blockquote>"
            )
    except Exception as e:
        log.error(f"restore failed: {e}")
        await query.message.edit_text(f"⚠️ <b>Restore failed:</b> <code>{html.escape(str(e))[:300]}</code>")
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass

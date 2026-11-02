"""
Non-destructive MongoDB integrity & duplicate-user doctor (`core/db_doctor.py`).

Legacy pre-Videl1 deployments did not enforce unique indexes on `saver.users.id` or
`encoder.users.id`, allowing concurrent first-time messages to insert duplicate rows
for the same Telegram user ID. This module:
  1. Audits all four Videl databases (`videl`, `saver`, `encoder`, `filestore`) in read-only mode.
  2. Merges duplicate `saver.users` and `encoder.users` documents per `id` without losing
     entitlements (Lifetime / latest `premium_expiry`), login `session`s, custom thumbnails,
     captions, word filters, or usage counters.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("videl.db_doctor")


def merge_saver_user_docs(docs: list[dict[str, Any]]) -> tuple[Any, dict[str, Any], list[Any]]:
    """Choose surviving `_id` and build an entitlement- & secret-preserving merged document."""
    # Prefer keeping the document that already holds a live session or premium flag
    ordered = sorted(
        docs,
        key=lambda d: (
            0 if d.get("session") else 1,
            0 if d.get("is_premium") else 1,
            str(d.get("_id", "")),
        ),
    )
    keep = ordered[0]
    keep_id = keep["_id"]
    redundant_ids = [d["_id"] for d in ordered[1:] if "_id" in d]

    merged: dict[str, Any] = {}
    for d in reversed(ordered):  # older/less-preferred first, preferred overwrites non-empty scalars
        for k, v in d.items():
            if k == "_id":
                continue
            if v not in (None, "", [], {}):
                merged[k] = v
            elif k not in merged:
                merged[k] = v

    # Entitlement preservation: any True wins; Lifetime (None while is_premium=True) beats timed dates
    any_premium = any(bool(d.get("is_premium")) for d in docs)
    merged["is_premium"] = any_premium
    if any_premium:
        has_lifetime = any(bool(d.get("is_premium")) and d.get("premium_expiry") is None for d in docs)
        if has_lifetime:
            merged["premium_expiry"] = None
        else:
            expiries = [str(d["premium_expiry"]) for d in docs if d.get("premium_expiry")]
            merged["premium_expiry"] = max(expiries) if expiries else None

    # Counters: never undercount saves
    merged["total_saves"] = max((int(d.get("total_saves") or 0) for d in docs), default=0)
    merged["daily_usage"] = max((int(d.get("daily_usage") or 0) for d in docs), default=0)

    # Word filters: union of delete_words and merged replace_words
    del_words: list[str] = []
    for d in ordered:
        for w in d.get("delete_words") or []:
            if isinstance(w, str) and w and w not in del_words:
                del_words.append(w)
    if del_words or "delete_words" in merged:
        merged["delete_words"] = del_words

    repl_words: dict[str, str] = {}
    for d in reversed(ordered):
        if isinstance(d.get("replace_words"), dict):
            repl_words.update(d["replace_words"])
    if repl_words or "replace_words" in merged:
        merged["replace_words"] = repl_words

    return keep_id, merged, redundant_ids


def merge_encoder_user_docs(docs: list[dict[str, Any]]) -> tuple[Any, dict[str, Any], list[Any]]:
    """Choose surviving `_id` and merge custom encoder settings + statistics."""
    from VideoEncoder.utils.database.access_db import db as enc_db

    defaults = enc_db.new_user(int(docs[0].get("id", 0)))

    def _custom_score(doc: dict[str, Any]) -> int:
        score = 0
        for k, default_val in defaults.items():
            if k in ("id", "join_date"):
                continue
            if k in doc and doc[k] != default_val and doc[k] is not None:
                score += 1
        score += int(bool(doc.get("encodes")))
        return score

    ordered = sorted(docs, key=lambda d: (-_custom_score(d), str(d.get("_id", ""))))
    keep = ordered[0]
    keep_id = keep["_id"]
    redundant_ids = [d["_id"] for d in ordered[1:] if "_id" in d]

    merged = dict(defaults)
    for d in reversed(ordered):
        for k, v in d.items():
            if k == "_id":
                continue
            if v is not None and (k not in defaults or v != defaults[k]):
                merged[k] = v
            elif k not in merged:
                merged[k] = v

    for counter in ("encodes", "saved_bytes", "encode_seconds"):
        vals = [int(d.get(counter) or 0) for d in docs if counter in d]
        if vals:
            merged[counter] = max(vals)

    return keep_id, merged, redundant_ids


async def _scan_duplicates(col) -> dict[int, list[dict[str, Any]]]:
    by_uid: dict[int, list[dict[str, Any]]] = {}
    async for doc in col.find({"id": {"$exists": True}}):
        try:
            uid = int(doc["id"])
        except (TypeError, ValueError):
            continue
        by_uid.setdefault(uid, []).append(doc)
    return {uid: rows for uid, rows in by_uid.items() if len(rows) > 1}


async def inspect_and_deduplicate(*, dry_run: bool = True) -> dict[str, Any]:
    """Inspect all Videl databases and optionally deduplicate `saver.users` & `encoder.users`."""
    from core.db import vdb
    from database.db import db as saver_db
    from filestore.database.main_db import MainDB
    from VideoEncoder.utils.database.access_db import db as enc_db

    saver_dups = await _scan_duplicates(saver_db.col)
    enc_dups = await _scan_duplicates(enc_db.col)
    videl_dups = await _scan_duplicates(vdb.users)

    saver_redundant = sum(len(rows) - 1 for rows in saver_dups.values())
    enc_redundant = sum(len(rows) - 1 for rows in enc_dups.values())
    videl_redundant = sum(len(rows) - 1 for rows in videl_dups.values())

    merged_users = 0
    removed_docs = 0

    if not dry_run:
        for _uid, rows in saver_dups.items():
            keep_id, merged, redundant_ids = merge_saver_user_docs(rows)
            await saver_db.col.update_one({"_id": keep_id}, {"$set": merged})
            if redundant_ids:
                res = await saver_db.col.delete_many({"_id": {"$in": redundant_ids}})
                removed_docs += res.deleted_count
            merged_users += 1

        for _uid, rows in enc_dups.items():
            keep_id, merged, redundant_ids = merge_encoder_user_docs(rows)
            await enc_db.col.update_one({"_id": keep_id}, {"$set": merged})
            if redundant_ids:
                res = await enc_db.col.delete_many({"_id": {"$in": redundant_ids}})
                removed_docs += res.deleted_count
            merged_users += 1

    payments_total = await vdb.db["payments"].count_documents({})
    payments_missing_charge = await vdb.db["payments"].count_documents(
        {"$or": [{"charge_id": {"$exists": False}}, {"charge_id": ""}, {"charge_id": None}]}
    )
    active_subs = await vdb.db["subscriptions"].count_documents({"active": True})
    clones_total = await MainDB().bots.count_documents({"is_deleted": {"$ne": True}})

    return {
        "dry_run": dry_run,
        "saver_duplicate_users": len(saver_dups),
        "saver_redundant_docs": saver_redundant,
        "encoder_duplicate_users": len(enc_dups),
        "encoder_redundant_docs": enc_redundant,
        "videl_duplicate_users": len(videl_dups),
        "videl_redundant_docs": videl_redundant,
        "total_redundant_docs": saver_redundant + enc_redundant,
        "merged_users": merged_users,
        "removed_docs": removed_docs,
        "payments_total": payments_total,
        "payments_missing_charge": payments_missing_charge,
        "active_subscriptions": active_subs,
        "clones_total": clones_total,
    }

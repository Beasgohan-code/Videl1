"""
Async MongoDB persistence layer for AoiMusic Group Management modules.
Uses the existing Motor client (`AoiMusic.core.mongo.mongodb`) with lightweight
in-memory caching for hot read paths (locks, settings, AFK, approved users).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from AoiMusic.core.mongo import mongodb

# Collections (all namespaced under mgmt_* to stay cleanly isolated from music collections)
warns_col = mongodb.mgmt_warns
locks_col = mongodb.mgmt_locks
welcome_col = mongodb.mgmt_welcome
rules_col = mongodb.mgmt_rules
notes_col = mongodb.mgmt_notes
filters_col = mongodb.mgmt_filters
blacklist_col = mongodb.mgmt_blacklists
blsticker_col = mongodb.mgmt_blstickers
flood_col = mongodb.mgmt_antiflood
approve_col = mongodb.mgmt_approve
settings_col = mongodb.mgmt_settings
afk_col = mongodb.mgmt_afk
connect_col = mongodb.mgmt_connections

# Lightweight write-through caches (bounded by chat/user count)
_SETTINGS_CACHE: Dict[int, Dict[str, Any]] = {}
_LOCKS_CACHE: Dict[int, Dict[str, bool]] = {}
_AFK_CACHE: Dict[int, Optional[Dict[str, Any]]] = {}
_APPROVED_CACHE: Dict[int, set] = {}


# ---------------------------------------------------------------------------
# Generic per-chat settings (biolink, antiedit, abuse, nsfw, mediadelete,
# msgdelete, autodemote, joinrequest, cleanblue, reports, logchannel,
# nightmode, lockdown, disabled_cmds, warn_limit, warn_mode, etc.)
# ---------------------------------------------------------------------------
async def get_chat_settings(chat_id: int) -> Dict[str, Any]:
    if chat_id in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[chat_id]
    doc = await settings_col.find_one({"chat_id": chat_id}) or {"chat_id": chat_id}
    _SETTINGS_CACHE[chat_id] = doc
    return doc


async def set_chat_setting(chat_id: int, key: str, value: Any) -> None:
    await settings_col.update_one(
        {"chat_id": chat_id},
        {"$set": {key: value}},
        upsert=True,
    )
    doc = _SETTINGS_CACHE.setdefault(chat_id, {"chat_id": chat_id})
    doc[key] = value


async def update_chat_settings(chat_id: int, updates: Dict[str, Any]) -> None:
    await settings_col.update_one(
        {"chat_id": chat_id},
        {"$set": updates},
        upsert=True,
    )
    doc = _SETTINGS_CACHE.setdefault(chat_id, {"chat_id": chat_id})
    doc.update(updates)


async def add_to_setting_set(chat_id: int, key: str, item: Any) -> None:
    await settings_col.update_one(
        {"chat_id": chat_id},
        {"$addToSet": {key: item}},
        upsert=True,
    )
    _SETTINGS_CACHE.pop(chat_id, None)


async def remove_from_setting_set(chat_id: int, key: str, item: Any) -> None:
    await settings_col.update_one(
        {"chat_id": chat_id},
        {"$pull": {key: item}},
    )
    _SETTINGS_CACHE.pop(chat_id, None)


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------
async def add_warn(chat_id: int, user_id: int, reason: str = "") -> int:
    await warns_col.update_one(
        {"chat_id": chat_id, "user_id": user_id},
        {"$push": {"reasons": reason or "No reason provided"}, "$inc": {"count": 1}},
        upsert=True,
    )
    doc = await warns_col.find_one({"chat_id": chat_id, "user_id": user_id})
    return int(doc.get("count", 1)) if doc else 1


async def get_warns(chat_id: int, user_id: int) -> Dict[str, Any]:
    doc = await warns_col.find_one({"chat_id": chat_id, "user_id": user_id})
    if not doc:
        return {"count": 0, "reasons": []}
    return {"count": int(doc.get("count", 0)), "reasons": list(doc.get("reasons", []))}


async def remove_last_warn(chat_id: int, user_id: int) -> int:
    doc = await warns_col.find_one({"chat_id": chat_id, "user_id": user_id})
    if not doc or int(doc.get("count", 0)) <= 0:
        return 0
    new_count = max(0, int(doc.get("count", 0)) - 1)
    reasons = list(doc.get("reasons", []))
    if reasons:
        reasons.pop()
    if new_count == 0:
        await warns_col.delete_one({"chat_id": chat_id, "user_id": user_id})
    else:
        await warns_col.update_one(
            {"chat_id": chat_id, "user_id": user_id},
            {"$set": {"count": new_count, "reasons": reasons}},
        )
    return new_count


async def reset_warns(chat_id: int, user_id: int) -> None:
    await warns_col.delete_one({"chat_id": chat_id, "user_id": user_id})


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------
async def get_locks(chat_id: int) -> Dict[str, bool]:
    if chat_id in _LOCKS_CACHE:
        return _LOCKS_CACHE[chat_id]
    doc = await locks_col.find_one({"chat_id": chat_id})
    locks = dict(doc.get("locks", {})) if doc else {}
    _LOCKS_CACHE[chat_id] = locks
    return locks


async def set_lock(chat_id: int, lock_type: str, state: bool) -> None:
    await locks_col.update_one(
        {"chat_id": chat_id},
        {"$set": {f"locks.{lock_type}": state}},
        upsert=True,
    )
    locks = _LOCKS_CACHE.setdefault(chat_id, {})
    locks[lock_type] = state


# ---------------------------------------------------------------------------
# Approved Users
# ---------------------------------------------------------------------------
async def get_approved_users(chat_id: int) -> set:
    if chat_id in _APPROVED_CACHE:
        return _APPROVED_CACHE[chat_id]
    doc = await approve_col.find_one({"chat_id": chat_id})
    users = set(doc.get("users", [])) if doc else set()
    _APPROVED_CACHE[chat_id] = users
    return users


async def is_approved(chat_id: int, user_id: int) -> bool:
    return user_id in await get_approved_users(chat_id)


async def approve_user(chat_id: int, user_id: int) -> None:
    await approve_col.update_one(
        {"chat_id": chat_id},
        {"$addToSet": {"users": user_id}},
        upsert=True,
    )
    _APPROVED_CACHE.setdefault(chat_id, set()).add(user_id)


async def unapprove_user(chat_id: int, user_id: int) -> None:
    await approve_col.update_one(
        {"chat_id": chat_id},
        {"$pull": {"users": user_id}},
    )
    if chat_id in _APPROVED_CACHE:
        _APPROVED_CACHE[chat_id].discard(user_id)


async def unapprove_all(chat_id: int) -> None:
    await approve_col.delete_one({"chat_id": chat_id})
    _APPROVED_CACHE[chat_id] = set()


# ---------------------------------------------------------------------------
# Welcome & Goodbye
# ---------------------------------------------------------------------------
async def get_welcome(chat_id: int) -> Dict[str, Any]:
    doc = await welcome_col.find_one({"chat_id": chat_id})
    return doc or {
        "chat_id": chat_id,
        "welcome_on": False,
        "goodbye_on": False,
        "welcome_text": None,
        "goodbye_text": None,
        "clean_welcome": False,
        "clean_service": False,
        "welcome_mute": False,
        "last_welcome_id": None,
    }


async def update_welcome(chat_id: int, updates: Dict[str, Any]) -> None:
    await welcome_col.update_one(
        {"chat_id": chat_id},
        {"$set": updates},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
async def get_rules(chat_id: int) -> Optional[str]:
    doc = await rules_col.find_one({"chat_id": chat_id})
    return doc.get("rules") if doc else None


async def set_rules(chat_id: int, text: str) -> None:
    await rules_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"rules": text}},
        upsert=True,
    )


async def clear_rules(chat_id: int) -> None:
    await rules_col.delete_one({"chat_id": chat_id})


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
async def save_note(
    chat_id: int,
    name: str,
    text: str = "",
    file_id: Optional[str] = None,
    media_type: Optional[str] = None,
) -> None:
    key = name.lower().strip()
    await notes_col.update_one(
        {"chat_id": chat_id, "name": key},
        {"$set": {"text": text, "file_id": file_id, "media_type": media_type}},
        upsert=True,
    )


async def get_note(chat_id: int, name: str) -> Optional[Dict[str, Any]]:
    return await notes_col.find_one({"chat_id": chat_id, "name": name.lower().strip()})


async def delete_note(chat_id: int, name: str) -> bool:
    res = await notes_col.delete_one({"chat_id": chat_id, "name": name.lower().strip()})
    return res.deleted_count > 0


async def list_notes(chat_id: int) -> List[str]:
    cursor = notes_col.find({"chat_id": chat_id}, {"name": 1})
    return sorted([d["name"] async for d in cursor])


async def clear_all_notes(chat_id: int) -> int:
    res = await notes_col.delete_many({"chat_id": chat_id})
    return res.deleted_count


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
async def save_filter(
    chat_id: int,
    keyword: str,
    text: str = "",
    file_id: Optional[str] = None,
    media_type: Optional[str] = None,
) -> None:
    key = keyword.lower().strip()
    await filters_col.update_one(
        {"chat_id": chat_id, "keyword": key},
        {"$set": {"text": text, "file_id": file_id, "media_type": media_type}},
        upsert=True,
    )


async def get_all_filters(chat_id: int) -> List[Dict[str, Any]]:
    cursor = filters_col.find({"chat_id": chat_id})
    return [d async for d in cursor]


async def delete_filter(chat_id: int, keyword: str) -> bool:
    res = await filters_col.delete_one({"chat_id": chat_id, "keyword": keyword.lower().strip()})
    return res.deleted_count > 0


async def clear_all_filters(chat_id: int) -> int:
    res = await filters_col.delete_many({"chat_id": chat_id})
    return res.deleted_count


# ---------------------------------------------------------------------------
# Blacklists (Words & Stickers)
# ---------------------------------------------------------------------------
async def get_blacklists(chat_id: int) -> Dict[str, Any]:
    doc = await blacklist_col.find_one({"chat_id": chat_id})
    return doc or {"chat_id": chat_id, "words": [], "mode": "delete"}


async def add_blacklist_words(chat_id: int, words: List[str]) -> None:
    cleaned = [w.lower().strip() for w in words if w.strip()]
    if not cleaned:
        return
    await blacklist_col.update_one(
        {"chat_id": chat_id},
        {"$addToSet": {"words": {"$each": cleaned}}, "$setOnInsert": {"mode": "delete"}},
        upsert=True,
    )


async def remove_blacklist_words(chat_id: int, words: List[str]) -> None:
    cleaned = [w.lower().strip() for w in words if w.strip()]
    if not cleaned:
        return
    await blacklist_col.update_one(
        {"chat_id": chat_id},
        {"$pull": {"words": {"$in": cleaned}}},
    )


async def set_blacklist_mode(chat_id: int, mode: str) -> None:
    await blacklist_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"mode": mode}},
        upsert=True,
    )


async def get_blstickers(chat_id: int) -> Dict[str, Any]:
    doc = await blsticker_col.find_one({"chat_id": chat_id})
    return doc or {"chat_id": chat_id, "sets": [], "mode": "delete"}


async def add_blsticker(chat_id: int, set_name: str) -> None:
    await blsticker_col.update_one(
        {"chat_id": chat_id},
        {"$addToSet": {"sets": set_name.lower()}, "$setOnInsert": {"mode": "delete"}},
        upsert=True,
    )


async def remove_blsticker(chat_id: int, set_name: str) -> None:
    await blsticker_col.update_one(
        {"chat_id": chat_id},
        {"$pull": {"sets": set_name.lower()}},
    )


async def set_blsticker_mode(chat_id: int, mode: str) -> None:
    await blsticker_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"mode": mode}},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# Anti-Flood
# ---------------------------------------------------------------------------
async def get_flood_config(chat_id: int) -> Dict[str, Any]:
    doc = await flood_col.find_one({"chat_id": chat_id})
    return doc or {"chat_id": chat_id, "limit": 0, "mode": "mute"}


async def set_flood_limit(chat_id: int, limit: int) -> None:
    await flood_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"limit": limit}, "$setOnInsert": {"mode": "mute"}},
        upsert=True,
    )


async def set_flood_mode(chat_id: int, mode: str) -> None:
    await flood_col.update_one(
        {"chat_id": chat_id},
        {"$set": {"mode": mode}},
        upsert=True,
    )


# ---------------------------------------------------------------------------
# AFK (Global per user)
# ---------------------------------------------------------------------------
async def set_afk(user_id: int, reason: str = "") -> None:
    data = {"user_id": user_id, "reason": reason.strip(), "since": int(time.time())}
    await afk_col.update_one({"user_id": user_id}, {"$set": data}, upsert=True)
    _AFK_CACHE[user_id] = data


async def get_afk(user_id: int) -> Optional[Dict[str, Any]]:
    if user_id in _AFK_CACHE:
        return _AFK_CACHE[user_id]
    doc = await afk_col.find_one({"user_id": user_id})
    _AFK_CACHE[user_id] = doc
    return doc


async def clear_afk(user_id: int) -> Optional[Dict[str, Any]]:
    old = await get_afk(user_id)
    if old:
        await afk_col.delete_one({"user_id": user_id})
    _AFK_CACHE[user_id] = None
    return old


# ---------------------------------------------------------------------------
# Remote Group Connection
# ---------------------------------------------------------------------------
async def set_connection(user_id: int, chat_id: int) -> None:
    await connect_col.update_one(
        {"user_id": user_id},
        {"$set": {"chat_id": chat_id}},
        upsert=True,
    )


async def get_connection(user_id: int) -> Optional[int]:
    doc = await connect_col.find_one({"user_id": user_id})
    return int(doc["chat_id"]) if doc and doc.get("chat_id") else None


async def clear_connection(user_id: int) -> None:
    await connect_col.delete_one({"user_id": user_id})

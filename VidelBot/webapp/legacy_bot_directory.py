"""Read-only account-scoped legacy clone directory; never import credentials."""
import re


async def list_owned_clones(owner_id: int) -> dict:
    from filestore.database.main_db import MainDB

    # Project only the fields this account view needs, not tokens or settings.
    cursor = MainDB().bots.find(
        {"owner_id": owner_id, "is_deleted": {"$ne": True}},
        {"_id": 1, "bot_username": 1, "is_active": 1},
    ).sort("_id", 1).limit(101)
    docs = await cursor.to_list(length=101)
    items = []
    for doc in docs[:100]:
        bot_id = doc.get("_id")
        if not isinstance(bot_id, int) or isinstance(bot_id, bool) or bot_id <= 0:
            continue
        username = doc.get("bot_username")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
            username = ""
        items.append({"telegram_bot_id": str(bot_id), "username": username,
                      "enabled": doc.get("is_active", True) is True})
    return {"bots": items, "truncated": len(docs) > 100}


async def list_owned_clone_page(owner_id: int, after: int = 0) -> dict:
    """Read one keyset page; the owner is always taken from verified authentication."""
    from filestore.database.main_db import MainDB

    # Mongo stores Telegram IDs as BSON int32 or int64. Exclude malformed IDs
    # before limiting, so bad historic values cannot stall a cursor or leak data.
    query = {"owner_id": owner_id, "is_deleted": {"$ne": True},
             "_id": {"$gt": after, "$lte": 2**63 - 1},
             "$or": [{"_id": {"$type": "int"}}, {"_id": {"$type": "long"}}]}
    docs = await MainDB().bots.find(query, {"_id": 1, "bot_username": 1, "is_active": 1}).sort("_id", 1).limit(101).to_list(length=101)
    items = []
    for doc in docs[:100]:
        username = doc.get("bot_username")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", username):
            username = ""
        items.append({"telegram_bot_id": str(doc["_id"]), "username": username,
                      "enabled": doc.get("is_active", True) is True})
    return {"pagination": "id-v1", "bots": items,
            "next_after": items[-1]["telegram_bot_id"] if len(docs) > 100 else None}

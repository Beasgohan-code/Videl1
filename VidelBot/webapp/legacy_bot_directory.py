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

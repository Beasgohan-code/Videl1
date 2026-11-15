"""Reviewed workspace bot archive and soft-delete operations."""
from __future__ import annotations

import math
import time
from typing import Any

from pymongo import ReturnDocument
from webapp import db_models, services_bots


def _validate(body: dict[str, Any]) -> tuple[str, float, bool]:
    if set(body) != {"operation", "expected_updated_ts", "expected_archived"}:
        raise services_bots.SaaSValidationError("Provide the reviewed record state and operation.")
    operation, ts, archived = body["operation"], body["expected_updated_ts"], body["expected_archived"]
    if (operation not in ("archive", "unarchive", "delete") or type(ts) not in (float, int)
            or ts < 0 or ts > 10**12 or not math.isfinite(ts) or type(archived) is not bool
            or (operation == "archive" and archived) or (operation == "unarchive" and not archived)):
        raise services_bots.SaaSValidationError("Invalid reviewed record state or operation.")
    return operation, ts, archived


async def change(workspace_id: str, bot_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    operation, expected_ts, archived = _validate(body)
    col = db_models.bots_col()
    # Re-read within the scope; don't send credentials to the client. For a
    # delete, compare the stored token snapshot as well as the reviewed fields.
    observed = await col.find_one({"workspace_id": str(workspace_id), "bot_id": str(bot_id), "deleted": {"$ne": True}}, collation={"locale": "simple"})
    if observed is None:
        return None
    comparisons = [
        {"$eq": [{"$ifNull": ["$updated_ts", None]}, {"$literal": expected_ts}]},
        {"$eq": [{"$ifNull": ["$archived", None]}, {"$literal": archived}]},
    ]
    query = {"_id": observed["_id"], "workspace_id": str(workspace_id), "bot_id": str(bot_id),
             "deleted": {"$ne": True}, "$expr": {"$and": comparisons}}
    if operation == "delete":
        if "token_sealed" not in observed:
            query["token_sealed"] = {"$exists": False}
        elif observed["token_sealed"] is None or isinstance(observed["token_sealed"], str):
            query["token_sealed"] = {"$exists": True}
            comparisons.append({"$eq": [{"$ifNull": ["$token_sealed", None]}, {"$literal": observed["token_sealed"]}]})
        else:
            return None
    new_ts = max(time.time(), math.nextafter(float(expected_ts), math.inf))
    update: dict[str, Any] = {"updated_ts": new_ts}
    if operation == "delete":
        update.update(deleted=True, connected=False, token_sealed="", token_masked="", has_credentials=False)
    else:
        update["archived"] = operation == "archive"
    return await col.find_one_and_update(query, {"$set": update}, return_document=ReturnDocument.AFTER, collation={"locale": "simple"})

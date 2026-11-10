"""Conditional updates for the two public bot-record profile fields."""
from __future__ import annotations

import time
from typing import Any

from pymongo import ReturnDocument
from webapp import db_models, services_bots

_FIELDS = {"display_name": 80, "description": 300}


def _validated_patch(body: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    keys = set(body) - {"expected"}
    expected = body.get("expected")
    if not keys or not keys.issubset(_FIELDS) or not isinstance(expected, dict) or set(expected) != keys:
        raise services_bots.SaaSValidationError("Provide changed profile fields and their expected values only.")
    updates: dict[str, str] = {}
    originals: dict[str, str] = {}
    for key in keys:
        value, previous = body[key], expected[key]
        if not isinstance(value, str) or not isinstance(previous, str) or len(previous) > _FIELDS[key]:
            raise services_bots.SaaSValidationError("Invalid profile value.")
        value = value.strip()
        if len(value) > _FIELDS[key] or (key == "display_name" and len(value) < 2):
            raise services_bots.SaaSValidationError("Invalid profile value.")
        updates[key] = value
        originals[key] = previous
    return updates, originals


async def update_profile(workspace_id: str, bot_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """Compare and update only changed fields in one workspace-scoped MongoDB operation.

    Missing/null fields compare as empty strings, matching the editor. Expression
    equality avoids matching malformed arrays, and $literal prevents a user value
    beginning with $ from being interpreted as a field path.
    """
    updates, expected = _validated_patch(body)
    comparisons = [{"$eq": [{"$ifNull": [f"${key}", ""]}, {"$literal": value}]} for key, value in expected.items()]
    query = {"workspace_id": str(workspace_id), "bot_id": str(bot_id), "deleted": {"$ne": True},
             "$expr": {"$and": comparisons}}
    doc = await db_models.bots_col().find_one_and_update(
        query, {"$set": {**updates, "updated_ts": time.time()}}, return_document=ReturnDocument.AFTER,
    )
    return services_bots._public_bot_doc(doc) if doc is not None else None

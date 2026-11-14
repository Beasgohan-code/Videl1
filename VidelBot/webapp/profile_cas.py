"""Conditional updates for public bot-record profile fields."""
from __future__ import annotations

import time
from typing import Any

from pymongo import ReturnDocument
from webapp import db_models, services_bots

_TEXT_FIELDS = {"display_name": 80, "description": 300, "category": 40}
_FIELDS = frozenset((*_TEXT_FIELDS, "tags"))


def _validated_patch(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    keys = set(body) - {"expected"}
    expected = body.get("expected")
    if not keys or not keys.issubset(_FIELDS) or not isinstance(expected, dict) or set(expected) != keys:
        raise services_bots.SaaSValidationError("Provide changed profile fields and their expected values only.")
    updates: dict[str, Any] = {}
    originals: dict[str, Any] = {}
    for key in keys:
        value, previous = body[key], expected[key]
        if key == "tags":
            if (not isinstance(value, list) or not isinstance(previous, list) or len(value) > 12 or len(previous) > 12
                    or any(not isinstance(t, str) or len(t) > 24 for t in previous)
                    or any(not isinstance(t, str) or not t.strip() or len(t.strip()) > 24 for t in value)):
                raise services_bots.SaaSValidationError("Invalid profile tags.")
            updates[key] = [tag.strip() for tag in value]
            originals[key] = list(previous)
            continue
        if not isinstance(value, str) or not isinstance(previous, str) or len(previous) > _TEXT_FIELDS[key]:
            raise services_bots.SaaSValidationError("Invalid profile value.")
        value = value.strip()
        if len(value) > _TEXT_FIELDS[key] or (key == "display_name" and len(value) < 2):
            raise services_bots.SaaSValidationError("Invalid profile value.")
        updates[key] = value
        originals[key] = previous
    return updates, originals


async def update_profile(workspace_id: str, bot_id: str, body: dict[str, Any]) -> dict[str, Any] | None:
    """Compare and update changed fields in one workspace-scoped MongoDB operation.

    Missing/null optional fields compare as empty strings or lists, matching the
    editor. Expression equality requires exact arrays, and $literal prevents
    values beginning with $ from being interpreted as MongoDB expressions.
    """
    updates, expected = _validated_patch(body)
    comparisons = [{"$eq": [{"$ifNull": [f"${key}", [] if key == "tags" else ""]}, {"$literal": value}]}
                   for key, value in expected.items()]
    query = {"workspace_id": str(workspace_id), "bot_id": str(bot_id), "deleted": {"$ne": True},
             "$expr": {"$and": comparisons}}
    doc = await db_models.bots_col().find_one_and_update(
        query, {"$set": {**updates, "updated_ts": time.time()}}, return_document=ReturnDocument.AFTER,
    )
    return services_bots._public_bot_doc(doc) if doc is not None else None

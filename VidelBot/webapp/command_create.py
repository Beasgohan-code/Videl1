"""Create-only command metadata writes for the Next Studio."""
from __future__ import annotations

import time
from typing import Any

from pymongo.errors import DuplicateKeyError
from platform_core import models_db
from webapp import db_models, services_bots


class CommandExists(Exception):
    """The command name is already in use for this bot."""


async def _require_unique_index(collection: Any, keys: list[tuple[str, int]]) -> None:
    """Create/verify a full unique index before relying on it for safety.

    Duplicate or inaccessible legacy data makes creation fail closed. This does
    not change or delete any data. Rechecking also avoids trusting an old in-memory
    readiness flag if an index was removed out of band.
    """
    name = await collection.create_index(keys, unique=True)
    index = (await collection.index_information()).get(name) or {}
    if (list(index.get("key", [])) != keys or index.get("unique") is not True
            or index.get("sparse") or "partialFilterExpression" in index):
        raise RuntimeError("Required unique index is unavailable")


def _validated(body: dict[str, Any]) -> dict[str, Any]:
    allowed = {"command", "description", "category", "permission", "visibility", "enabled"}
    if set(body) != allowed:
        raise services_bots.SaaSValidationError("Provide command metadata fields only.")
    cmd, desc = body["command"], body["description"]
    cat, perm, visible, enabled = (body[k] for k in ("category", "permission", "visibility", "enabled"))
    if not isinstance(cmd, str) or not services_bots.CMD_NAME_RE.fullmatch(cmd):
        raise services_bots.SaaSValidationError("Invalid command name.")
    if not isinstance(desc, str) or not 2 <= len(desc.strip()) <= 256:
        raise services_bots.SaaSValidationError("Invalid command description.")
    if (not isinstance(cat, str) or cat not in services_bots.COMMAND_CATEGORIES or
            not isinstance(perm, str) or perm not in services_bots.COMMAND_PERMISSIONS or
            not isinstance(visible, str) or visible != ("public" if perm == "everyone" else "scoped") or
            not isinstance(enabled, bool)):
        raise services_bots.SaaSValidationError("Invalid command metadata.")
    return {"command": cmd, "description": desc.strip(), "category": cat,
            "permission": perm, "visibility": visible, "enabled": enabled}


async def create(workspace_id: str, bot_id: str, actor_id: int, body: dict[str, Any]) -> dict[str, Any]:
    values = _validated(body)
    # The command collection uses globally unique bot IDs, so both uniqueness
    # assumptions must hold. The lookup and insert are not a cross-document transaction.
    await services_bots.get_owned_workspace_bot(workspace_id, bot_id)
    await _require_unique_index(db_models.bots_col(), [("bot_id", 1)])
    collection = db_models.bot_commands_col()
    await _require_unique_index(collection, [("bot_id", 1), ("command", 1)])
    doc = {"bot_id": bot_id, **values, "custom_reply_text": "", "handler_bound": False,
           "execution_note": "Custom menu metadata / static text response (does not execute arbitrary Python)",
           "usage_count": 0, "updated_at": db_models.utcnow_iso(), "updated_ts": time.time()}
    try:
        await collection.insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise CommandExists from exc
    # If auditing fails after insertion the response is uncertain; the client must refresh.
    await models_db.log_audit(actor_id, "bot_command_create", target=f"{workspace_id}:{bot_id}:/{values['command']}")
    return doc

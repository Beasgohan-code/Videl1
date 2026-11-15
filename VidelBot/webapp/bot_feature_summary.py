"""Bounded read-only summary of selected saved workspace bot feature flags."""
from webapp import services_bots

# These are record fields, not live process health or deployment checks. Never
# serialize arbitrary keys, settings, credentials or values from historical data.
FEATURE_KEYS = (
    "filestore_enabled", "batch_links", "permanent_link", "force_subscribe",
    "force_sub_request_mode", "auto_delete", "protect_content", "media_encoder",
    "auto_watermark", "group_moderation",
)


async def summary(workspace_id: str, bot_id: str) -> dict:
    doc = await services_bots.get_owned_workspace_bot(workspace_id, bot_id)
    features = doc.get("features")
    if not isinstance(features, dict):
        features = {}
    return {
        "summary": "features-v1",
        "bot_id": bot_id,
        "features": {key: value if type(value := features.get(key)) is bool else None
                     for key in FEATURE_KEYS},
    }

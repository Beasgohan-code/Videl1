"""Validation for positive Telegram IDs read from the Mongo clone registry."""
from bson.int64 import Int64


def valid_registry_id(value):
    """Accept BSON int64 and Python int, but not bools, floats, strings or overflow."""
    return type(value) in (int, Int64) and 0 < value < 2**63

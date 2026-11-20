"""Read a small set of Telegram profile flags without inferring missing values."""

_VERIFICATION_FLAGS = frozenset({"is_verified", "is_scam", "is_fake"})


def profile_flag(user, field):
    """Prefer WzGram's nested verification status; tolerate legacy direct flags.

    Only actual booleans are returned. An absent field is not evidence of False.
    """
    if field in _VERIFICATION_FLAGS:
        value = getattr(getattr(user, "verification_status", None), field, None)
        if type(value) is bool:
            return value
    value = getattr(user, field, None)
    return value if type(value) is bool else None

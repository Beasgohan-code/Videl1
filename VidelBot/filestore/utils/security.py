"""
Token security helpers for clone (worker) bots.

If ENCRYPTION_KEY (a Fernet key) is set, clone bot tokens are encrypted
before they are stored in MongoDB. Tokens that were stored in plain text
by older versions keep working (they are detected and returned as-is).

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import os

from filestore.fs_config import LOGGER, ENCRYPTION_KEY

log = LOGGER(__name__)

_PREFIX = "enc:"
_fernet = None
_fernet_key_raw = (os.environ.get("ENCRYPTION_KEY") or ENCRYPTION_KEY or "").strip()

if _fernet_key_raw:
    try:
        from cryptography.fernet import Fernet

        _fernet = Fernet(_fernet_key_raw.encode())
    except Exception as e:  # bad key or cryptography missing
        log.error(f"ENCRYPTION_KEY is invalid – secrets will be stored unencrypted: {e}")
        _fernet = None
else:
    log.warning("ENCRYPTION_KEY not set – clone bot tokens & user sessions are stored unencrypted in MongoDB.")


def _get_fernet():
    """Return active Fernet instance (honors both monkeypatched `_fernet` and env changes)."""
    global _fernet, _fernet_key_raw
    key = (os.environ.get("ENCRYPTION_KEY") or ENCRYPTION_KEY or "").strip()
    if key == _fernet_key_raw:
        return _fernet
    _fernet_key_raw = key
    if not key:
        _fernet = None
        return None
    try:
        from cryptography.fernet import Fernet

        _fernet = Fernet(key.encode())
        return _fernet
    except Exception as e:
        log.error(f"ENCRYPTION_KEY is invalid – secrets will be stored unencrypted: {e}")
        _fernet = None
        return None


def is_encryption_enabled() -> bool:
    """True when a valid Fernet ENCRYPTION_KEY is active."""
    return _get_fernet() is not None


def encrypt_token(token: str) -> str:
    """Encrypt a bot token or user session string (no-op when no ENCRYPTION_KEY is configured)."""
    f = _get_fernet()
    if not token or f is None or token.startswith(_PREFIX):
        return token
    return _PREFIX + f.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a stored token/session. Plain-text (legacy) values are returned unchanged."""
    if not encrypted or not encrypted.startswith(_PREFIX):
        return encrypted
    f = _get_fernet()
    if f is None:
        raise ValueError("Secret is encrypted but ENCRYPTION_KEY is missing/invalid")
    return f.decrypt(encrypted[len(_PREFIX):].encode()).decode()


def mask_token(token: str) -> str:
    """Mask a bot token for display: '123456:AAH...' → '123456:****...Xyz'."""
    if not token or ":" not in token:
        return "****"
    bot_id_part, secret = token.split(":", 1)
    if len(secret) > 4:
        return f"{bot_id_part}:****...{secret[-4:]}"
    return f"{bot_id_part}:****"


def mask_api_key(key: str) -> str:
    """Mask an API key for display: 'abcdef123456' → '****3456'."""
    if not key:
        return "Not set"
    if len(key) <= 4:
        return "****"
    return f"****{key[-4:]}"

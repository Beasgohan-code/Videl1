"""
Token security helpers for clone (worker) bots.

If ENCRYPTION_KEY (a Fernet key) is set, clone bot tokens are encrypted
before they are stored in MongoDB. Tokens that were stored in plain text
by older versions keep working (they are detected and returned as-is).

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

from filestore.fs_config import LOGGER, ENCRYPTION_KEY

log = LOGGER(__name__)

_PREFIX = "enc:"
_fernet = None

if ENCRYPTION_KEY:
    try:
        from cryptography.fernet import Fernet

        _fernet = Fernet(ENCRYPTION_KEY.encode())
    except Exception as e:  # bad key or cryptography missing
        log.error(f"ENCRYPTION_KEY is invalid – clone tokens will be stored unencrypted: {e}")
        _fernet = None
else:
    log.warning("ENCRYPTION_KEY not set – clone bot tokens are stored unencrypted in MongoDB.")


def encrypt_token(token: str) -> str:
    """Encrypt a bot token (no-op when no ENCRYPTION_KEY is configured)."""
    if not token or _fernet is None:
        return token
    return _PREFIX + _fernet.encrypt(token.encode()).decode()


def decrypt_token(encrypted: str) -> str:
    """Decrypt a stored token. Plain-text (legacy) tokens are returned unchanged."""
    if not encrypted or not encrypted.startswith(_PREFIX):
        return encrypted
    if _fernet is None:
        raise ValueError("Token is encrypted but ENCRYPTION_KEY is missing/invalid")
    return _fernet.decrypt(encrypted[len(_PREFIX):].encode()).decode()


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

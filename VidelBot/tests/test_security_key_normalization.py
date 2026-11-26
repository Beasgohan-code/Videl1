"""ENCRYPTION_KEY paste-tolerance: dashboard env editors often mangle keys."""
import tests.harness  # noqa: F401  (must load before filestore/config imports)
import pytest
from cryptography.fernet import Fernet

from filestore.utils import security
from filestore.utils.security import _normalize_key, decrypt_token, encrypt_token


@pytest.fixture(autouse=True)
def _restore_security_state():
    """encrypt/decrypt cache the key in module globals — restore them so
    these tests never leak an active Fernet into later test files."""
    old_fernet, old_raw = security._fernet, security._fernet_key_raw
    yield
    security._fernet, security._fernet_key_raw = old_fernet, old_raw


def test_normalize_key_tolerates_common_paste_mistakes():
    key = Fernet.generate_key().decode()
    assert _normalize_key(key) == key
    assert _normalize_key(f"  {key}  \n") == key
    assert _normalize_key(f'"{key}"') == key
    assert _normalize_key(f"ENCRYPTION_KEY={key}") == key
    assert _normalize_key(f"{key}  # production key") == key
    assert _normalize_key(key.rstrip("=")) == key  # stripped padding restored
    assert _normalize_key("") == ""
    assert _normalize_key(None) == ""


def test_normalized_key_still_encrypts_and_decrypts(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", f'ENCRYPTION_KEY="{key.rstrip("=")}"')
    sealed = encrypt_token("123456:secret-token")
    assert sealed.startswith("enc:")
    assert decrypt_token(sealed) == "123456:secret-token"


def test_wrong_key_is_never_invented(monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    sealed = encrypt_token("123456:secret-token")
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())
    with pytest.raises(Exception):
        decrypt_token(sealed)  # a different key must fail, never silently pass

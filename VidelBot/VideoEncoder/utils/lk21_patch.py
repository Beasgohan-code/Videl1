import re
import urllib.parse

# Patch re.sre_parse for lk21/exrex compatibility on Python 3.12+
if not hasattr(re, "sre_parse"):
    import re._parser
    re.sre_parse = re._parser

_ORIGINAL_URLPARSE = urllib.parse.urlparse


def safe_urlparse(url, scheme="", allow_fragments=True):
    """Signature-compatible urlparse that never raises (lk21 feeds it odd hosts)."""
    try:
        return _ORIGINAL_URLPARSE(url, scheme, allow_fragments)
    except Exception:
        return _ORIGINAL_URLPARSE("http://invalid")


# lk21 does `from urllib.parse import urlparse` at import time, so its modules keep
# the safe version.  The global is restored right after – replacing it process-wide
# breaks urljoin() (it calls urlparse with 3 args) and with it requests redirects,
# aiogram and every other library that joins URLs.
urllib.parse.urlparse = safe_urlparse
try:
    import lk21  # noqa: F401
except Exception:  # lk21 is optional – the encoder works without it
    lk21 = None
finally:
    urllib.parse.urlparse = _ORIGINAL_URLPARSE

# Apply pyrogram save_file patch
from . import pyrogram_patch  # noqa: E402,F401

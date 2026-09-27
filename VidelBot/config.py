"""
Videl — unified configuration.

Videl is a single Telegram "utility" bot that bundles:
  • 📥 Save-Restricted-Content  (saver/)
  • 🎬 Video Encoder            (VideoEncoder/)
  • 🤖 Clone / Worker FileStore bots (filestore/)
  • 🧰 Extra tools              (core/)

Every value is read from environment variables. For local runs you can put
them in a `config.env` (or `.env`) file next to this one — see
`config.env.sample`.
"""

import os

try:
    from dotenv import load_dotenv

    for _f in ("config.env", ".env"):
        if os.path.exists(_f):
            load_dotenv(_f, override=False)
except ImportError:  # python-dotenv is optional at runtime
    pass


def _ids(*names):
    """Parse one or more env vars holding user/chat IDs separated by space or comma."""
    out = []
    for name in names:
        raw = os.environ.get(name, "") or ""
        for part in raw.replace(",", " ").split():
            part = part.strip()
            if part.lstrip("-").isdigit():
                out.append(int(part))
    # keep order, drop duplicates
    return list(dict.fromkeys(out))


def _bool(name, default=False):
    return str(os.environ.get(name, str(default))).strip().lower() in ("1", "true", "yes", "on")


# ==============================
# Telegram Bot Credentials
# ==============================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "0") or 0)
API_HASH = os.environ.get("API_HASH", "")
BOT_NAME = os.environ.get("BOT_NAME", "Videl")

# ==============================
# Users & Permissions
# ==============================
# OWNER_ID   → full control (both modules)
# SUDO_USERS → encoder sudo (clean / restart / exec …) + saver admin
# ADMINS     → saver admins (ban / broadcast / premium …)
OWNERS = _ids("OWNER_ID", "OWNERS")
OWNER_ID = OWNERS[0] if OWNERS else 0   # primary owner (int)
SUDO_USERS = _ids("SUDO_USERS")
ADMINS = list(dict.fromkeys(OWNERS + SUDO_USERS + _ids("ADMINS")))

# Chats / users allowed to use the encoder (besides owner & sudo).
EVERYONE_CHATS = _ids("EVERYONE_CHATS")
# Set to True to let *anyone* use the encoder (heavy on CPU – use with care).
ENCODER_PUBLIC = _bool("ENCODER_PUBLIC", False)

# ==============================
# Database (MongoDB)
# ==============================
DB_URI = os.environ.get("DB_URI") or os.environ.get("MONGO_URI", "")
# Database used by the Save-Restricted module
DB_NAME = os.environ.get("DB_NAME", "SaveRestricted2")
# Database used by the Encoder module
ENCODER_DB_NAME = os.environ.get("ENCODER_DB_NAME") or os.environ.get("SESSION_NAME", "VideoEncoder")

# ==============================
# Logging
# ==============================
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0") or 0)
ERROR_MESSAGE = _bool("ERROR_MESSAGE", True)

# ==============================
# Appearance / Links
# ==============================
START_PIC = os.environ.get("START_PIC", "")  # optional image shown on /start
CONTACT_URL = os.environ.get("CONTACT_URL", "")   # payment / support contact (e.g. https://t.me/you)
UPDATES_URL = os.environ.get("UPDATES_URL", "")   # optional updates channel link
SUPPORT_URL = os.environ.get("SUPPORT_URL", "")   # optional support group link

# ==============================
# Save-Restricted: limits & premium
# ==============================
FREE_LIMIT_DAILY = int(os.environ.get("FREE_LIMIT_DAILY", "10") or 10)
FREE_LIMIT_SIZE_GB = float(os.environ.get("FREE_LIMIT_SIZE_GB", "2") or 2)
UPI_ID = os.environ.get("UPI_ID", "")            # shown on the /plan page (optional)
QR_CODE = os.environ.get("QR_CODE", "")          # payment QR image URL (optional)
SUBSCRIPTION = os.environ.get("SUBSCRIPTION", "")  # banner image for /plan (optional)
PREMIUM_PRICES = os.environ.get(
    "PREMIUM_PRICES", "1 Month: ₹50 / $1 | 3 Months: ₹120 / $2.5 | Lifetime: ₹200 / $4"
)

# ==============================
# Encoder: folders & drive
# ==============================
DOWNLOAD_DIR = os.environ.get("DOWNLOAD_DIR", "VideoEncoder/utils/extras/downloads/")
ENCODE_DIR = os.environ.get("ENCODE_DIR", "VideoEncoder/utils/extras/encodes/")
DRIVE_DIR = os.environ.get("DRIVE_DIR", "")
INDEX_URL = os.environ.get("INDEX_URL", "")

# The original encoder code expects trailing slashes.
if not DOWNLOAD_DIR.endswith("/"):
    DOWNLOAD_DIR += "/"
if not ENCODE_DIR.endswith("/"):
    ENCODE_DIR += "/"

# ==============================
# Clone / Worker FileStore bots
# ==============================
MONGO_URI = DB_URI
FS_DB_NAME = os.environ.get("FS_DB_NAME") or os.environ.get("MONGO_DB_NAME", "VidelFileStore")
VIDEL_DB_NAME = os.environ.get("VIDEL_DB_NAME", "Videl")  # core (users / bans / settings)
MAIN_LOG_CHANNEL = int(os.environ.get("MAIN_LOG_CHANNEL", "0") or 0) or LOG_CHANNEL
MAX_BOTS_PER_USER = int(os.environ.get("MAX_BOTS_PER_USER", "1") or 1)
BOT_CREATION_COOLDOWN = int(os.environ.get("BOT_CREATION_COOLDOWN", "30") or 30)
HIBERNATION_HOURS = int(os.environ.get("HIBERNATION_HOURS", "48") or 48)
DEFAULT_AUTO_DELETE = int(os.environ.get("DEFAULT_AUTO_DELETE", "0") or 0)
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")  # Fernet key – encrypts clone bot tokens in DB
BACKEND_API_URL = os.environ.get("BACKEND_API_URL", "")
BACKEND_API_SECRET = os.environ.get("BACKEND_API_SECRET", "")
FREEIMAGE_API_KEY = os.environ.get("FREEIMAGE_API_KEY", "")  # empty → catbox.moe fallback
CLONE_ENABLED = _bool("CLONE_ENABLED", True)
TG_BOT_WORKERS = int(os.environ.get("TG_BOT_WORKERS", "16") or 16)

# Source code link returned by the (unlisted) /source command – required by the
# AGPL-3.0 licence of the bundled code when you run a modified copy publicly.
SOURCE_URL = os.environ.get("SOURCE_URL", "https://github.com/Beasgohan-code/Videl1")

# ==============================
# Web keep-alive
# ==============================
PORT = int(os.environ.get("PORT", "8080") or 8080)


def missing_required():
    """Return a list of required variables that are not set."""
    missing = []
    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")
    if not API_ID:
        missing.append("API_ID")
    if not API_HASH:
        missing.append("API_HASH")
    if not DB_URI:
        missing.append("DB_URI / MONGO_URI")
    if not OWNER_ID:
        missing.append("OWNER_ID")
    return missing

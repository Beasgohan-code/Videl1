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


def _clean_environ():
    """`docker run --env-file`, Railway/Render/Koyeb dashboards etc. pass values literally, so a
    line copied from config.env.sample like `KEEP_ALIVE_INTERVAL=240   # seconds` would reach the
    bot as "240   # seconds" and crash int().  Strip such inline comments and surrounding quotes
    once, here, for every module that reads os.environ."""
    import re
    comment = re.compile(r"\s+#.*$")
    for key, value in list(os.environ.items()):
        new = value.strip()
        if len(new) >= 2 and new[0] == new[-1] and new[0] in "\"'":
            new = new[1:-1]                      # quoted → keep content verbatim
        elif new.startswith("#"):
            new = ""                             # `KEY=   # comment` → empty
        else:
            new = comment.sub("", new)
        if new != value:
            os.environ[key] = new


_clean_environ()


def _env_num(cast, name, default, empty=None):
    raw = os.environ.get(name)
    if raw is None:
        return default
    raw = raw.strip()
    if raw == "":
        return default if empty is None else empty
    try:
        return cast(raw)
    except ValueError:
        import sys
        print(f"[config] {name}={raw!r} is not a valid {cast.__name__} – using {default}", file=sys.stderr)
        return default


def env_int(name, default, empty=None):
    """int env var; a malformed value logs a warning and falls back instead of crashing boot."""
    return _env_num(int, name, default, empty)


def env_float(name, default, empty=None):
    return _env_num(float, name, default, empty)


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
API_ID = env_int("API_ID", 0)
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
LOG_CHANNEL = env_int("LOG_CHANNEL", 0)
ERROR_MESSAGE = _bool("ERROR_MESSAGE", True)
# Events that are ALSO sent to every owner in DM (the log channel always gets everything).
OWNER_DM_EVENTS = set(os.environ.get(
    "OWNER_DM_EVENTS", "BotStarted BotStopped CloneCreated CloneDeleted StarsPayment LowDisk AutoRestart"
).replace(",", " ").split())
LOG_START_EVENTS = _bool("LOG_START_EVENTS", True)        # log returning users pressing /start
START_LOG_COOLDOWN_MIN = env_int("START_LOG_COOLDOWN_MIN", 60)  # per user
LOG_LOGINS = _bool("LOG_LOGINS", True)                    # log /login and /logout of the saver
LOG_LINKS = _bool("LOG_LINKS", True)                      # log clone-bot link generation
DAILY_REPORT = _bool("DAILY_REPORT", True)                # daily summary to the log channel
DAILY_REPORT_HOUR = env_int("DAILY_REPORT_HOUR", 0)   # 0-23, in LOG_TZ
LOG_TZ = os.environ.get("LOG_TZ", "Asia/Kolkata")         # timezone for log timestamps

# ==============================
# Appearance / Links
# ==============================
# Image(s) shown big on /start – space separated list → one is picked at random.
# Empty → a random anime picture from waifu.pics / nekos.life (like the original saver bot).
START_PICS = [p for p in os.environ.get("START_PIC", "").split() if p.startswith("http")]
START_PIC = START_PICS[0] if START_PICS else ""
RANDOM_START_PIC = _bool("RANDOM_START_PIC", True)
FORCE_PIC = os.environ.get("FORCE_PIC", "")      # image for the force-subscribe screen (optional)
START_REACTIONS = _bool("START_REACTIONS", True)  # react to /start with a random emoji
MESSAGE_EFFECTS = _bool("MESSAGE_EFFECTS", True)  # animated message effects in private chats
CONTACT_URL = os.environ.get("CONTACT_URL", "")   # payment / support contact (e.g. https://t.me/you)
UPDATES_URL = os.environ.get("UPDATES_URL", "")   # optional updates channel link
SUPPORT_URL = os.environ.get("SUPPORT_URL", "")   # optional support group link

# ==============================
# Main-bot Force Subscribe
# ==============================
# Channel IDs (-100…) or @usernames users must join before using Videl.
# More can be added at runtime with /add_fsub. Bot must be admin in each channel.
FSUB_CHANNELS = [
    int(x) if x.lstrip("-").isdigit() else x.lstrip("@")
    for x in os.environ.get("FSUB_CHANNELS", os.environ.get("FSUB_CHANNEL", "")).replace(",", " ").split()
]
FSUB_REQUEST_MODE = _bool("FSUB_REQUEST_MODE", False)  # use join-request links (a pending request counts)

# ==============================
# Save-Restricted: limits & premium
# ==============================
FREE_LIMIT_DAILY = env_int("FREE_LIMIT_DAILY", 10)
FREE_LIMIT_SIZE_GB = env_float("FREE_LIMIT_SIZE_GB", 2)
UPI_ID = os.environ.get("UPI_ID", "")            # shown on the /plan page (optional)
QR_CODE = os.environ.get("QR_CODE", "")          # payment QR image URL (optional)
SUBSCRIPTION = os.environ.get("SUBSCRIPTION", "")  # banner image for /plan (optional)
# Telegram Stars (⭐) self-service premium: "days:stars" pairs, 0 days = lifetime.
# Empty string disables Stars payments.
STARS_PLANS = []
for _pair in os.environ.get("STARS_PLANS", "30:100 90:250 0:500").replace(",", " ").split():
    try:
        _d, _s = _pair.split(":")
        STARS_PLANS.append((int(_d), int(_s)))
    except ValueError:
        pass
PREMIUM_PRICES = os.environ.get(
    "PREMIUM_PRICES", "1 Month: ₹50 / $1 | 3 Months: ₹120 / $2.5 | Lifetime: ₹200 / $4"
)
# Monthly auto-renewing Stars subscription (⭐ per 30 days). 0 disables it.
SUBSCRIPTION_STARS = env_int("SUBSCRIPTION_STARS", 90, empty=0)
# Let users buy Premium for a friend (native Telegram user picker + Stars).
GIFTS_ENABLED = _bool("GIFTS_ENABLED", True)

# ==============================
# Growth: referrals · redeem codes · free trial
# ==============================
REFERRAL_TARGET = env_int("REFERRAL_TARGET", 5, empty=0)          # 0 disables rewards
REFERRAL_REWARD_DAYS = env_int("REFERRAL_REWARD_DAYS", 3, empty=0)
TRIAL_DAYS = env_int("TRIAL_DAYS", 1, empty=0)                    # 0 disables /trial

# ==============================
# Modern Bot API UX
# ==============================
# Live "typing" previews (sendMessageDraft) and the "Thinking…" placeholder
# for slow commands – private chats only, silently skipped where unsupported.
STREAM_REPLIES = _bool("STREAM_REPLIES", True)

# ==============================
# aiogram · Bot API 10.3 bridge
# ==============================
# Videl receives updates over MTProto (pyrofork). aiogram is used next to it
# as an *outgoing* Bot API client for everything MTProto layer 220 can't do:
# coloured buttons, ephemeral group replies, rich messages, managed bots,
# Stars subscriptions, profile photos … (every call falls back gracefully).
AIOGRAM_ENABLED = _bool("AIOGRAM_ENABLED", True)
BOT_API_URL = os.environ.get("BOT_API_URL", "")          # optional self-hosted Bot API server
COLORED_BUTTONS = _bool("COLORED_BUTTONS", True)         # 🟩 success / 🟥 danger / 🟦 primary buttons
EPHEMERAL_REPLIES = _bool("EPHEMERAL_REPLIES", True)     # /help /id /ping … in groups → visible only to the caller
MANAGED_BOTS = _bool("MANAGED_BOTS", True)               # one-tap clone creation (needs Bot Management Mode)
MANAGED_PAIR_TIMEOUT = env_int("MANAGED_PAIR_TIMEOUT", 300)  # seconds to wait for the new bot
SUPPORT_ENABLED = _bool("SUPPORT_ENABLED", True)  # /support inbox → owners reply by replying

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
MAIN_LOG_CHANNEL = env_int("MAIN_LOG_CHANNEL", 0) or LOG_CHANNEL
MAX_BOTS_PER_USER = env_int("MAX_BOTS_PER_USER", 1)
BOT_CREATION_COOLDOWN = env_int("BOT_CREATION_COOLDOWN", 30)
CLONE_INACTIVE_DAYS = env_int("CLONE_INACTIVE_DAYS", 7)   # clones nobody used for this long are deactivated (0 = never)
HIBERNATION_HOURS = CLONE_INACTIVE_DAYS * 24             # legacy name, same meaning
DEFAULT_AUTO_DELETE = env_int("DEFAULT_AUTO_DELETE", 0)
ENCRYPTION_KEY = os.environ.get("ENCRYPTION_KEY", "")  # Fernet key – encrypts clone bot tokens in DB
BACKEND_API_URL = os.environ.get("BACKEND_API_URL", "")
BACKEND_API_SECRET = os.environ.get("BACKEND_API_SECRET", "")
FREEIMAGE_API_KEY = os.environ.get("FREEIMAGE_API_KEY", "")  # empty → catbox.moe fallback
CLONE_ENABLED = _bool("CLONE_ENABLED", True)
TG_BOT_WORKERS = env_int("TG_BOT_WORKERS", 16)

# ==============================
# Paid plans (Telegram Stars) – on top of the saver Premium
# ==============================
# 🎬 Encoder Pro: priority queue, more queued tasks, AV1, 2-pass, logo watermark, bigger /leech.
ENC_PRO_STARS = env_int("ENC_PRO_STARS", 75, empty=0)            # ⭐ per 30 days · 0 = not sold
# 🤖 Clone Plus / 🚀 Clone Pro: more clone bots, longer / no idle auto-off.
CLONE_PLUS_STARS = env_int("CLONE_PLUS_STARS", 100, empty=0)
CLONE_PRO_STARS = env_int("CLONE_PRO_STARS", 250, empty=0)
CLONE_PLUS_BOTS = env_int("CLONE_PLUS_BOTS", 3)
CLONE_PRO_BOTS = env_int("CLONE_PRO_BOTS", 10)
CLONE_PLUS_IDLE_DAYS = env_int("CLONE_PLUS_IDLE_DAYS", 30)       # Clone Pro clones never auto-off
PLAN_DAYS = env_int("PLAN_DAYS", 30)

# ==============================
# Encoder engine (Phase 15)
# ==============================
ENCODER_WORKERS = max(1, env_int("ENCODER_WORKERS", 1))          # encodes running at the same time
ENC_MAX_TASKS_FREE = max(1, env_int("ENC_MAX_TASKS_FREE", 3))    # queued + running tasks per user
ENC_MAX_TASKS_PRO = max(1, env_int("ENC_MAX_TASKS_PRO", 10))
HW_ENCODER = os.environ.get("HW_ENCODER", "auto").strip().lower()  # auto | nvenc | qsv | vaapi | off
VAAPI_DEVICE = os.environ.get("VAAPI_DEVICE", "/dev/dri/renderD128")
SPLIT_SIZE_MB = env_int("SPLIT_SIZE_MB", 1950)                   # uploads above this are split into parts
LEECH_FREE_GB = env_float("LEECH_FREE_GB", 2)
LEECH_PRO_GB = env_float("LEECH_PRO_GB", 8)
QUEUE_PERSIST = _bool("QUEUE_PERSIST", True)                    # encoder queue survives restarts
SINGLE_INSTANCE = _bool("SINGLE_INSTANCE", True)                # one live copy per bot token (core/instance.py)
SOURCE_CACHE_MIN = max(0, env_int("SOURCE_CACHE_MIN", 60))     # reuse a downloaded source for N min (0 = off)
SOURCE_CACHE_GB = max(1, env_int("SOURCE_CACHE_GB", 10))        # cache size cap
FAST_DL = _bool("FAST_DL", True)                                # parallel Telegram downloads (core/fastdl.py)
FAST_DL_WORKERS = max(1, min(16, env_int("FAST_DL_WORKERS", 6)))  # chunk requests in flight per download

# ==============================
# Owner tools: web dashboard · backups · analytics
# ==============================
# Web dashboard at https://<your-app>/admin – owners/admins open it from Telegram with /dashboard
# (🌐 Mini App button: signed in by Telegram itself · 🔗 one-time browser link). ADMIN_WEB_TOKEN is optional:
# set it to also allow opening /admin#TOKEN directly. WEB_DASHBOARD=false turns the page off completely.
WEB_DASHBOARD = _bool("WEB_DASHBOARD", True)
ADMIN_WEB_TOKEN = os.environ.get("ADMIN_WEB_TOKEN", "")
WEB_SESSION_HOURS = max(1, env_int("WEB_SESSION_HOURS", 12))      # how long a dashboard sign-in lasts
# who may show the page in a frame – Telegram Web opens Mini Apps in an iframe
WEB_FRAME_ANCESTORS = os.environ.get("WEB_FRAME_ANCESTORS", "https://web.telegram.org https://*.telegram.org").strip()
BACKUP_ENABLED = _bool("BACKUP_ENABLED", True)                  # daily gzip JSON backup → owner DM
BACKUP_HOUR = env_int("BACKUP_HOUR", 4)
BACKUP_CHAT = env_int("BACKUP_CHAT", 0)                         # 0 → first owner
BACKUP_INCLUDE_SECRETS = _bool("BACKUP_INCLUDE_SECRETS", False)  # login sessions / tokens are skipped by default
DEFAULT_LANG = os.environ.get("DEFAULT_LANG", "en").strip().lower() or "en"

# Source code link returned by the (unlisted) /source command – required by the
# AGPL-3.0 licence of the bundled code when you run a modified copy publicly.
SOURCE_URL = os.environ.get("SOURCE_URL", "https://github.com/Beasgohan-code/Videl1")

# ==============================
# Web keep-alive
# ==============================
PORT = env_int("PORT", 8080)
# Public URL of this app – pinged periodically so free hosts (Render/Koyeb/Replit…)
# don't put it to sleep. Auto-detected on Render, Koyeb, Railway and Heroku.
KEEP_ALIVE_URL = (
    os.environ.get("KEEP_ALIVE_URL")
    or os.environ.get("RENDER_EXTERNAL_URL")
    or (f"https://{os.environ['KOYEB_PUBLIC_DOMAIN']}" if os.environ.get("KOYEB_PUBLIC_DOMAIN") else "")
    or (f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN']}" if os.environ.get("RAILWAY_PUBLIC_DOMAIN") else "")
    or (f"https://{os.environ['HEROKU_APP_NAME']}.herokuapp.com" if os.environ.get("HEROKU_APP_NAME") else "")
    or ""
)
KEEP_ALIVE_INTERVAL = env_int("KEEP_ALIVE_INTERVAL", 240)  # seconds

# ==============================
# Watchdog / auto cleanup
# ==============================
WATCHDOG_INTERVAL = env_int("WATCHDOG_INTERVAL", 600)        # seconds between sweeps
CLEANUP_AFTER_HOURS = env_float("CLEANUP_AFTER_HOURS", 6)       # delete temp files older than this
MIN_FREE_DISK_GB = env_float("MIN_FREE_DISK_GB", 2)             # aggressive cleanup below this
STATE_TIMEOUT_MIN = env_int("STATE_TIMEOUT_MIN", 15)           # drop abandoned login/setup flows
AUTO_RESTART_ON_HANG = _bool("AUTO_RESTART_ON_HANG", True)                         # restart if Telegram is unreachable


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

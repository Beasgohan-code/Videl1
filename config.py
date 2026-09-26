import os
from dotenv import load_dotenv

load_dotenv()

def get_user_list(config, key):
    import orjson
    with open("{}/Emilia/{}".format(os.getcwd(), config), "rb") as json_file:
        return orjson.loads(json_file.read())[key]


class Config(object):
    # ── Required (fill via .env or here) ──────────────────
    API_ID = int(os.getenv("API_ID", "0") or 0)
    API_HASH = os.getenv("API_HASH", "")
    TOKEN = os.getenv("BOT_TOKEN", "") or os.getenv("TOKEN", "")

    BOT_ID = int(os.getenv("BOT_ID", "0") or 0)
    BOT_USERNAME = os.getenv("BOT_USERNAME", "")
    BOT_NAME = os.getenv("BOT_NAME", "Videl")

    OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
    DEV_USERS = [int(x) for x in os.getenv("DEV_USERS", str(OWNER_ID or 0)).split(",") if x.strip().isdigit()]

    # ── Database ──────────────────────────────────────────
    MONGO_DB_URL = os.getenv("MONGO_DB_URL", os.getenv("MONGO_URI", "mongodb://localhost:27017"))
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
    REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

    # ── Channels / Logs ───────────────────────────────────
    SUPPORT_CHAT = os.getenv("SUPPORT_CHAT", "")
    UPDATE_CHANNEL = os.getenv("UPDATE_CHANNEL", "")
    EVENT_LOGS = int(os.getenv("EVENT_LOGS", "0") or 0)

    # ── Appearance ────────────────────────────────────────
    START_PIC = os.getenv("START_PIC", "")

    # ── Optional APIs ─────────────────────────────────────
    WALL_API = os.getenv("WALL_API", "")
    GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
    CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY", "")
    ANILIST_CLIENT = int(os.getenv("ANILIST_CLIENT", "0") or 0)
    ANILIST_SECRET = os.getenv("ANILIST_SECRET", "")
    ANILIST_REDIRECT_URL = os.getenv("ANILIST_REDIRECT_URL", "https://anilist.co/api/v2/oauth/pin")
    SESSION_STRING = os.getenv("SESSION_STRING", "")

    # ── Clone system ──────────────────────────────────────
    CLONE_LIMIT = int(os.getenv("CLONE_LIMIT", "50") or 50)
    CLONES_PER_USER = int(os.getenv("CLONES_PER_USER", "2") or 2)
    CLONE_PREMIUM_ENABLED = os.getenv("CLONE_PREMIUM_ENABLED", "True").lower() == "true"
    CLONE_PREMIUM_STARS = int(os.getenv("CLONE_PREMIUM_STARS", "100") or 100)

    TEMP_DOWNLOAD_DIRECTORY = os.getenv("TEMP_DOWNLOAD_DIRECTORY", "./downloads")


class Production(Config):
    LOGGER = True


class Development(Config):
    LOGGER = True

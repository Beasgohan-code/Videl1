"""Centralized environment-based configuration for the Videl Music bot."""

import re
import sys
from os import getenv

from dotenv import load_dotenv
from pyrogram import filters

load_dotenv()


def _required_int(name: str) -> int:
    raw = getenv(name, "") or ""
    try:
        return int(raw.strip())
    except ValueError:
        print(
            f"[ERROR] - {name} is missing or not a valid integer. "
            "Set it in your environment / .env file and restart the bot."
        )
        sys.exit(1)


def _parse_int_list(raw: str) -> list[int]:
    return [int(x) for x in str(raw or "").replace(",", " ").split() if x.lstrip("-").isdigit()]


# --- Telegram Credentials (Required) ---
API_ID = _required_int("API_ID")
API_HASH = getenv("API_HASH", "").strip()
BOT_TOKEN = getenv("BOT_TOKEN", "").strip()

# --- Database (Required) ---
MONGO_DB_URI = getenv("MONGO_DB_URI", None)

# --- Owner & Logging ---
LOGGER_ID = int(getenv("LOGGER_ID") or 0)
OWNER_ID = _parse_int_list(getenv("OWNER_ID", ""))

# --- Assistant String Sessions (Comma-separated for multi-assistant) ---
_raw_sessions = getenv("STRING_SESSIONS", "").strip()
STRING_SESSIONS = [s.strip() for s in _raw_sessions.split(",") if s.strip()] if _raw_sessions else []
if not STRING_SESSIONS:
    for _k in ("STRING_SESSION", "STRING_SESSION1", "STRING_SESSION2", "STRING_SESSION3", "STRING_SESSION4", "STRING_SESSION5"):
        _v = getenv(_k, "").strip()
        if _v:
            STRING_SESSIONS.append(_v)
print(f"STRING_SESSIONS loaded: {len(STRING_SESSIONS)}")

# --- Playback & Limits ---
DURATION_LIMIT_MIN = int(getenv("DURATION_LIMIT", "5400"))
SONG_DOWNLOAD_DURATION = int(getenv("SONG_DOWNLOAD_DURATION_LIMIT", "5400"))
CLEANMODE_DELETE_MINS = int(getenv("CLEANMODE_MINS", "5"))
VIDEO_STREAM_LIMIT = int(getenv("VIDEO_STREAM_LIMIT", "999"))
SERVER_PLAYLIST_LIMIT = int(getenv("SERVER_PLAYLIST_LIMIT", "235"))
PLAYLIST_FETCH_LIMIT = int(getenv("PLAYLIST_FETCH_LIMIT", "255"))
TG_AUDIO_FILESIZE_LIMIT = int(getenv("TG_AUDIO_FILESIZE_LIMIT", "1073741824"))
TG_VIDEO_FILESIZE_LIMIT = int(getenv("TG_VIDEO_FILESIZE_LIMIT", "1073741824"))
YOUTUBE_DOWNLOAD_EDIT_SLEEP = int(getenv("YOUTUBE_EDIT_SLEEP", "3"))
TELEGRAM_DOWNLOAD_EDIT_SLEEP = int(getenv("TELEGRAM_EDIT_SLEEP", "5"))

# --- Instant Play, Media API & Cookies ---
INSTANT_PLAY = getenv("INSTANT_PLAY", "True")
VOTE_SKIP_LIMIT = int(getenv("VOTE_SKIP_LIMIT", "3"))
PROXY_URL = getenv("PROXY_URL", "").strip()
YT_COOKIES = getenv("YT_COOKIES", "").strip()
MEDIA_API_URL = getenv("MEDIA_API_URL", "https://host-repository.vercel.app").strip()
MEDIA_API_KEY = getenv("MEDIA_API_KEY", "").strip()

# --- Voice Chat Timed Lyrics ---
VC_LYRICS = getenv("VC_LYRICS", "True")
VC_LYRICS_TITLE = getenv("VC_LYRICS_TITLE", "True")
VC_LYRICS_MESSAGE = getenv("VC_LYRICS_MESSAGE", "False")
VC_LYRICS_NOTES = getenv("VC_LYRICS_NOTES", "True")
VC_LYRICS_NOTES_SEC = getenv("VC_LYRICS_NOTES_SEC", "30")
VC_LYRICS_GAP_SEC = getenv("VC_LYRICS_GAP_SEC", "8")
VC_LYRICS_MIN_SEC = getenv("VC_LYRICS_MIN_SEC", "2")
VC_LYRICS_LANG = getenv("VC_LYRICS_LANG", "auto").lower()

# --- Spotify API (Optional) ---
SPOTIFY_CLIENT_ID = getenv("SPOTIFY_CLIENT_ID", "").strip()
SPOTIFY_CLIENT_SECRET = getenv("SPOTIFY_CLIENT_SECRET", "").strip()

# --- Bot Behavior & Modes ---
PRIVATE_BOT_MODE = getenv("PRIVATE_BOT_MODE", "False")
SET_CMDS = getenv("SET_CMDS", "True")
AUTO_LEAVING_ASSISTANT = getenv("AUTO_LEAVING_ASSISTANT", "False")
AUTO_LEAVE_ASSISTANT_TIME = int(getenv("ASSISTANT_LEAVE_TIME", "5800"))
EXTRA_PLUGINS = getenv("EXTRA_PLUGINS", "False")
EXTRA_PLUGINS_REPO = getenv("EXTRA_PLUGINS_REPO", "").strip()
EXTRA_PLUGINS_FOLDER = getenv("EXTRA_PLUGINS_FOLDER", "plugins").strip()

# --- Links & Repository ---
SUPPORT_CHANNEL = getenv("SUPPORT_CHANNEL", "https://t.me/Telegram").strip()
SUPPORT_GROUP = getenv("SUPPORT_GROUP", "https://t.me/Telegram").strip()
PRIVACY_LINK = getenv("PRIVACY_LINK", "https://telegra.ph").strip()
GITHUB_REPO = getenv("GITHUB_REPO", "https://github.com/Beasgohan-code/Videl1").strip()
UPSTREAM_REPO = getenv("UPSTREAM_REPO", GITHUB_REPO).strip()
UPSTREAM_BRANCH = getenv("UPSTREAM_BRANCH", "main").strip()
GIT_TOKEN = getenv("GIT_TOKEN", "").strip()

# --- Hosting / Systemd (Optional) ---
HEROKU_API_KEY = getenv("HEROKU_API_KEY", "").strip()
HEROKU_APP_NAME = getenv("HEROKU_APP_NAME", "").strip()
SERVICE_NAME = getenv("SERVICE_NAME", "").strip()

# --- Internal Runtime State ---
BANNED_USERS = filters.user()
YTDOWNLOADER = 1
LOG = 2
LOG_FILE_NAME = "VidelMusicLogs.txt"
TEMP_DB_FOLDER = "tempdb"
adminlist = {}
lyrical = {}
chatstats = {}
userstats = {}
clean = {}
autoclean = []

# --- Default Images ---
START_IMG_URL = getenv("START_IMG_URL", "https://i.ibb.co/VrbBW39/file-00000000fc9c8208a9c65e000748c4d3.png")
PING_IMG_URL = getenv("PING_IMG_URL", "https://i.ibb.co/VrbBW39/file-00000000fc9c8208a9c65e000748c4d3.png")
PLAYLIST_IMG_URL = getenv("PLAYLIST_IMG_URL", "https://i.ibb.co/VrbBW39/file-00000000fc9c8208a9c65e000748c4d3.png")
GLOBAL_IMG_URL = getenv("GLOBAL_IMG_URL", "https://i.ibb.co/VrbBW39/file-00000000fc9c8208a9c65e000748c4d3.png")
STATS_IMG_URL = getenv("STATS_IMG_URL", "https://i.ibb.co/VrbBW39/file-00000000fc9c8208a9c65e000748c4d3.png")
TELEGRAM_AUDIO_URL = getenv("TELEGRAM_AUDIO_URL", "https://te.legra.ph/file/3d130381bf5945c139023.jpg")
TELEGRAM_VIDEO_URL = getenv("TELEGRAM_VIDEO_URL", "https://te.legra.ph/file/d3663021fb51e14a84aa9.jpg")
STREAM_IMG_URL = getenv("STREAM_IMG_URL", "https://te.legra.ph/file/248e6858de3f2e37393c1.jpg")
SOUNDCLOUD_IMG_URL = getenv("SOUNDCLOUD_IMG_URL", "https://te.legra.ph/file/1b78431fe8de0e497c188.jpg")
YOUTUBE_IMG_URL = getenv("YOUTUBE_IMG_URL", "https://te.legra.ph/file/98622051acad1988886be.jpg")
SPOTIFY_ARTIST_IMG_URL = getenv("SPOTIFY_ARTIST_IMG_URL", "https://te.legra.ph/file/c03f25028fa248401d519.jpg")
SPOTIFY_ALBUM_IMG_URL = getenv("SPOTIFY_ALBUM_IMG_URL", "https://te.legra.ph/file/9fe24bde84b1d31f685a9.jpg")
SPOTIFY_PLAYLIST_IMG_URL = getenv("SPOTIFY_PLAYLIST_IMG_URL", "https://te.legra.ph/file/7345db59ab5d2c5cb142a.jpg")


def time_to_seconds(time_str):
    stringt = str(time_str)
    return sum(int(x) * 60**i for i, x in enumerate(reversed(stringt.split(":"))))


def seconds_to_time(seconds):
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    return f"{minutes:02d}:{remaining_seconds:02d}"


DURATION_LIMIT = int(time_to_seconds(f"{DURATION_LIMIT_MIN}:00"))
SONG_DOWNLOAD_DURATION_LIMIT = int(time_to_seconds(f"{SONG_DOWNLOAD_DURATION}:00"))

for _name, _url in (
    ("SUPPORT_CHANNEL", SUPPORT_CHANNEL),
    ("SUPPORT_GROUP", SUPPORT_GROUP),
    ("UPSTREAM_REPO", UPSTREAM_REPO),
    ("GITHUB_REPO", GITHUB_REPO),
):
    if _url and not re.match(r"^(?:http|https)://", _url):
        print(f"[ERROR] - Your {_name} URL is invalid. It must start with https://")
        sys.exit(1)

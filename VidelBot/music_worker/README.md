# Videl Music Worker

A standalone Telegram music and group-management bot, based on the complete
feature set requested for this worker. It lives under `VidelBot/music_worker`
and uses its own configuration, dependencies, assistant sessions, and MongoDB
collections.

## Features

### Music and voice chats
- Audio/video playback, channel playback, live streams and multi-source search.
- Queue, now-playing panel, pause/resume, skip/stop, seek, speed, volume,
  repeat, shuffle, vote-skip and authorized DJ controls.
- Autoplay, personal playlists, lyrics, song downloads, top tracks, radio,
  inline search and active-player tools.
- YouTube, Spotify, Apple Music, SoundCloud, JioSaavn, Resso and Telegram media
  sources, subject to the configured providers and their availability.

### Group management
- Admin and member tools; bans, mutes, warnings and moderation logs.
- Content locks, anti-flood and other security controls, welcome/goodbye,
  notes, filters, rules, approvals, AFK and group utilities.
- Owner/sudo administration and deployment utilities from the upstream feature
  set. Carefully review the privileged commands before granting sudo access.

The bot includes rich mobile-friendly help, settings, queue and now-playing
panels. Use `/help` in Telegram to browse the command modules.

## Standalone setup

```sh
cd VidelBot/music_worker
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill the required values in .env, then:
PYTHONPATH=.. MUSIC_WORKER_STANDALONE=1 python -m music_worker
```

From `VidelBot/`, the equivalent entry point is:

```sh
MUSIC_WORKER_STANDALONE=1 python -m music_worker
```

Required settings include Telegram `API_ID`, `API_HASH`, `BOT_TOKEN`, one or
more `STRING_SESSIONS`, `MONGO_DB_URI`, and `OWNER_ID`. Optional API keys,
Spotify credentials, cookies and proxy settings are described in `.env.example`
and `config/config.py`. Never commit real tokens, sessions, database URIs, API
keys or cookies.

## Runtime boundary

This is now the standalone single-bot application. It does **not** participate
in Videl's per-clone Music-profile reconciliation, and the Videl main bot does
not launch it. Set `MUSIC_WORKER_STANDALONE=1` and run only one instance for a
bot/session; the guard is an operator acknowledgement, not a distributed lease.
The previous clone-aware modules remain in this folder for migration and
management compatibility, but are not used by the standalone entry point.

The required upstream MIT license notice is preserved in [`LICENSE`](LICENSE).

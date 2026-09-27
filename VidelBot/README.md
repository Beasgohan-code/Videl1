# ✨ Videl — all-in-one Telegram utility bot

Videl is **one** Telegram bot that combines:

| Module | What it does |
|---|---|
| 📥 **Content Saver** (`saver/`) | Save posts/media from public **and** restricted channels (via `/login`), single links or ranges, custom caption / thumbnail, word delete/replace filters, auto-forward to a dump chat, free & premium plans |
| 🎬 **Video Encoder** (`VideoEncoder/`) | x264 / x265 encoding with per-user settings (CRF, preset, resolution, audio codec, watermark, hard-subs…), queue, direct-link & batch encodes, Drive upload |
| 🤖 **Clone Bots** (`filestore/`) | Users create their **own FileStore bot** from inside Videl. Each clone runs as a worker: permanent share links, `/genlink`, `/batch`, `/custom_batch`, `/flink` (quality-grouped links), multi force-sub, join-requests, auto-delete, shorteners + verification, custom start text/pic/caption, backups, transfer, maintenance. Idle clones hibernate automatically. |
| 🧰 **Tools** (`core/`) | `/mediainfo`, `/rename`, `/upload` (public link), `/short`, `/qr`, `/id`, `/info`, `/json`, `/ping` |
| 👮 **Admin** (`core/`) | `/stats`, `/users`, `/broadcast [-pin]`, `/ban`, `/unban`, `/banned`, `/maintenance on\|off`, `/restart`, `/update` |

Everything runs in a single process on a single bot token; clone bots are extra
Pyrogram clients started by the worker engine.

## 🚀 Deploy

1. Copy `config.env.sample` → `config.env` and fill at least `BOT_TOKEN`, `API_ID`, `API_HASH`, `DB_URI`, `OWNER_ID`.
2. Generate an `ENCRYPTION_KEY` (clone-bot tokens are Fernet-encrypted in MongoDB):
   ```bash
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
   ```
3. Run:
   ```bash
   # Docker (recommended – includes ffmpeg)
   docker build -t videl . && docker run --env-file config.env -p 8080:8080 videl

   # or bare metal (needs ffmpeg + ffprobe in PATH)
   pip install -r requirements.txt && python3 run.py
   ```
   A health endpoint is served on `PORT` (default 8080) for Render / Koyeb / Railway.

> Keep the **ENCRYPTION_KEY** safe – if you lose it, existing clone bots can't be decrypted and their owners must re-add them.

## 🗂 Layout

```
run.py            entry point: loads plugins, starts clones, hibernation, health server
client.py         the single shared Pyrogram client
config.py         unified env-based configuration
core/             home menu, settings hub, middleware (bans/maintenance/users), admin, tools
saver/            restricted-content saver
VideoEncoder/     encoder (plugins + ffmpeg utils)
filestore/        clone-bot controller (main_bot/plugins) + worker engine (worker_bot/)
database/         saver database
```

## 📜 Licence

The bundled code keeps its original licences: the video encoder is AGPL-3.0 (`LICENSE-VideoEncoder-AGPL`) and the content saver is MIT (`LICENSE-save-restricted-content-bot`).
If you run a modified copy publicly you must offer its source to users – set
`SOURCE_URL` and the bot answers `/source` with it.

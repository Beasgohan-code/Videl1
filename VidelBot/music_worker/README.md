# Videl Music Clone Worker

A dedicated worker process for Videl clone bots with the **Music** profile.
It uses Videl's existing clone registry, encrypted bot-token and assistant
session fields, profile controls, owner-scoped dashboards, and Mongo-backed
per-clone settings, queues, and playlists. It does not run a separate global
music bot and does not need a second `BOT_TOKEN` or global assistant session.

## Clone lifecycle

- Reads only active, non-deleted records where `clone_profile` is `music`.
- Creates one bot client, optional encrypted-session assistant client, and
  voice-call player for each clone. Handler/store state is scoped by clone ID.
- Reconciles desired state every 10 seconds by default: starts newly activated
  clones, stops deactivated/deleted/profile-changed clones, and reloads changed
  bot or assistant credentials after the old local clients have stopped.
- Tracks clone messages and button activity through Videl's shared last-active
  lifecycle, so its idle sweep can make profile-appropriate decisions.
- Uses bounded exponential retry for failed starts and retries uncertain local
  stops before attempting replacement clients. Runtime status is only a local
  observation published to the dashboard; it is **not** a distributed lease or
  proof that another process has stopped.
- On SIGINT/SIGTERM, stops voice-call engines before assistant/bot clients and
  closes MongoDB. Deploy exactly one Music worker service instance; do not run
  multiple copies or a legacy global bot against the same clone tokens.

The controller already provides the owner flow: choose a clone's Music profile,
securely save its assistant session, then use the clone dashboard to request
start/stop and manage Music defaults. Bot tokens and user sessions stay in the
existing encrypted clone record; they are never copied to worker env variables.

## Deploy

Use the existing **VidelBot** environment and dependency set, then add the
Music playback dependencies. From the `VidelBot/` directory:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r music_worker/requirements.txt
```

Configure the same `config.env` values used by Videl, including `API_ID`,
`API_HASH`, `MONGO_URI` (or `DB_URI`), and a valid `ENCRYPTION_KEY`. Set
`CLONE_PROFILES_SINGLE_INSTANCE=True` and configure the deployment for exactly
one worker instance. For non-Docker deployments, install FFmpeg on the host
and ensure both `ffmpeg` and `ffprobe` are on `PATH`. Then start it from
`VidelBot/`:

```sh
MUSIC_CLONE_WORKER=1 python -m music_worker
```

`MUSIC_WORKER_SYNC_SECONDS` optionally changes the polling interval (2–300
seconds; default 10). `MUSIC_CLONE_WORKER=1` is a fail-closed operator
acknowledgement, not distributed fencing. Do not start a second copy. The
worker must be able to reach the same MongoDB database as the main Videl
controller.

For Docker, build from the repository root so the image includes both Videl's
shared clone modules and this worker:

```sh
docker build -f VidelBot/music_worker/Dockerfile -t videl-music-worker .
```

Run the image with Videl's regular environment variables, plus
`MUSIC_CLONE_WORKER=1`. The container has FFmpeg installed for voice playback.

## Music features

Clone-profile handlers currently provide `/play`, queue controls, `/pause`,
`/resume`, `/skip`, `/stop`, `/remove`, `/move`, settings, search and personal
playlists, with per-clone/per-chat isolation. Playback uses the clone's
assistant session when configured; a missing or unusable player degrades
honestly to queue-only mode. The archived global single-bot source remains in the repository for provenance,
but it is disabled and is not used by this clone-worker runtime.

The upstream MIT `LICENSE` and required copyright notices are retained.

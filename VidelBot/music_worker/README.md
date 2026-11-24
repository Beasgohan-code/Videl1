# Music Worker (clones)

A **separate worker program** for clones whose profile is Music — its
own codebase alongside the File Store worker and the Group Management
worker (`group_worker/`), sharing no code with either.

## Stage 1 (implemented)

- `/play <song>` or reply to an audio file to queue tracks, `/queue`,
  `/skip`, `/remove <n>`, `/stop`.
- Rich `/start` with Add-me and Owner buttons; `/settings` with DJ
  mode (admins-only control) and volume, using colored buttons.
- Per-(clone, chat) settings in `music_worker_settings`; bounded
  in-memory queues (current track is never dropped by the cap).

## Stage 2 — streaming (implemented, live-proof pending)

`player.py` wraps PyTgCalls: with a ready player, `/play` on a
replied audio file downloads and streams it into the voice chat;
`/pause`, `/resume`, `/skip` (advances to a downloaded next track),
`/stop` (leaves the call) and the volume setting control playback.
When a track ends naturally, the queue auto-advances via PyTgCalls
stream-end events; manual controls suppress the stale end event for
a few seconds so the queue never advances twice.

Compatibility was probed live (Nov 2026): PyTgCalls 3.0.1 +
WzGram 3.1.7 failed only on two legacy error names
(`GroupcallForbidden`/`GroupcallInvalid` vs WzGram's
`GroupCallInvalid`); `player.ensure_compat()` aliases them, after
which PyTgCalls imports, constructs, and exposes the full call API.
Install this package's own `requirements.txt` for the standalone
program — pytgcalls stays out of the main bot's requirements.

## Honest limits

- Actual joining of a real voice chat is only proven against real
  Telegram with a real clone; unit tests cover the wrapper with a
  fake calls object, and failures (e.g. no voice chat started) are
  reported honestly while the track stays queued.
- `/play <title>` resolves via yt-dlp (`resolver.py`, this package's
  own requirements): search phrases and supported links become direct
  audio streams; a failed lookup replies honestly instead of queueing
  a phantom track. Stream URLs can expire mid-queue on long waits.
- Queues are persisted per (clone, chat) in `music_worker_queues`
  and restored on first use after a restart, so a redeploy doesn't
  lose the night's playlist. Downloaded-file sources may vanish with
  the old container and stream URLs can expire; those tracks simply
  fail honestly when played.
- Clone profile selection for `music` in the main bot and runtime
  wiring remain separate approved steps; `run.py` does not import
  this package.

## Running standalone

    cd VidelBot && MUSIC_WORKER_STANDALONE=1 python -m music_worker

Starts every `music`-profile clone and stops them cleanly on
SIGINT/SIGTERM. The env opt-in is a fail-closed guard against two
processes serving the same clone. `run.py` does not import this
package.

## Stage 5 — full VC-bot feature set

Modelled on the public feature lists of classic Telegram VC music
bots (implemented fresh in this codebase): `/vplay` video mode,
`/playforce` (play now, keep the queue), `/clear` (upcoming only),
`/seek`/`/seekback` (restart-from-position with tracked playback
position), `/speed` (0.25–4.0× via ffmpeg atempo, kept per group),
`/autoplay` (extra search hits queue after a title), personal
playlists (`/pcreate /padd /premove /pview /pplay /pdelete`, stored
per clone+user in `music_worker_playlists`), and channel play
(`/addchannel /delchannel /cplay /cskip /cpause /cresume /cstop`)
against a linked channel's voice chat. Unresolved playlist/queue
titles are resolved lazily when their turn to play arrives.
Seek/speed act through ffmpeg parameters on stream (re)starts —
their audible effect, like joining a call, is live-proof pending.

## Stage 6 — player control panel

When a track starts, the bot posts a 🎵 NOW PLAYING panel (title,
duration, live position, requester, up-next table) with colored
control buttons — Pause/Resume toggle, Skip, Stop, Queue view and
back-to-Panel. Buttons honour DJ mode, work for channel play too
(the target chat is encoded in the callback), and refresh in place.

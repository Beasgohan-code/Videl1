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

## Honest limits

Voice-chat **streaming is not enabled yet**: `/play` queues honestly
and says the streaming engine isn't set up, rather than faking
playback. Streaming needs (1) a PyTgCalls version verified compatible
with WzGram 3.1.7 (earlier probes failed), (2) a per-clone assistant
(userbot) session with a safe lifecycle, and (3) clone profile
selection for `music` plus exclusive runtime wiring. Those are
separate approved steps; this engine is not imported by `run.py`.

## Running standalone

    cd VidelBot && MUSIC_WORKER_STANDALONE=1 python -m music_worker

Starts every `music`-profile clone and stops them cleanly on
SIGINT/SIGTERM. The env opt-in is a fail-closed guard against two
processes serving the same clone. `run.py` does not import this
package.

# Group Management Worker (clones)

A **separate worker program** for clones whose profile is Group
Management — built the same way the File Store worker
(`filestore/worker_bot/`) serves File Store clones, but as its own
codebase:

- It does **not** import or copy the main bot's `plugins/group_mod`
  plugin, and it does not modify the File Store worker in any way.
- Commands are its own: `/panel` (control room with buttons),
  `/modwarn`, `/modmute`, `/modkick`, `/modban`, `/modunban`,
  `/modpurge`, with greetings, a content shield (links/media/stickers/
  forwards/bots), warns with automatic mute/kick/ban, and a flood watch.
- Settings and warns live in `group_worker_settings` /
  `group_worker_warns`, scoped by (clone bot, chat), so clones are
  isolated from each other and from File Store data.

## Status

Stage 1 (core + group care) is implemented and unit-tested. The engine
can start/stop group-profile clones, but it is **not wired into
`run.py` yet**: today group-profile clones are still served by the
existing in-worker profile, and running both would double-handle the
same bots. Choosing the production runtime — and retiring the old path
without touching File Store behavior — is a separate approved step.

A Music worker will follow the same pattern as its own folder
(`music_worker/`) once the voice-chat dependency stack is verified;
it is not a mode flag inside this worker.

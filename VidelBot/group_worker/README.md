# Group Management Worker (clones)

A **separate worker program** for clones whose profile is Group
Management — built the same way the File Store worker
(`filestore/worker_bot/`) serves File Store clones, but as its own
codebase:

- It does **not** import or copy the main bot's `plugins/group_mod`
  plugin, and it does not modify the File Store worker in any way.
- Normal Rose-style commands: `/settings` (worker-style settings with toggles and sub-pages),
  `/warn`, `/unwarn`, `/mute`, `/tmute`, `/unmute`, `/kick`, `/ban`,
  `/tban`, `/sban`, `/unban`, `/purge`, with greetings, a content shield
  (links/media/stickers/forwards/bots), warns with automatic
  mute/kick/ban, and a flood watch.
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

## Stage 2 — rules, auto-replies, reports and DM alerts

- `/rules` / `/setrules` for group rules; `/filter`, `/stop`, `/filters`
  for keyword auto-replies (whole-word matching, commands ignored).
- `/pin` / `/unpin`, plus member `/report` (reply to a message): admins
  receive a DM with **Delete** / **Ban** / **Ignore** buttons.
- Moderation actions DM the clone owner (toggleable in `/settings`)
  with an **Undo** button for bans and mutes. DM delivery is
  best-effort — Telegram only allows it after the admin/owner has
  started the bot — and every button re-checks admin rights in the
  original group before acting.
- Commands are registered for both the default and all-group-chats
  BotCommand scopes at startup.

## Stage 3 — notes, greeting texts and night mode

- `/save`, `/get`, `/notes`, `/clear` for saved notes; `#trigger` opens a
  note inline. `/setwelcome` / `/setgoodbye` set greeting texts (and
  enable greetings) with `{name}` `{chat}` `{id}` placeholders.
- Night mode (Settings → Night hours): pick the UTC window with buttons;
  during it, members' messages are quietly deleted and admins are exempt.

# Group Management Worker (clones)

A **separate worker program** for clones whose profile is Group
Management — built the same way the File Store worker
(`filestore/worker_bot/`) serves File Store clones, but as its own
codebase:

- It does **not** import or copy the main bot's `plugins/group_mod`
  plugin, and it does not modify the File Store worker in any way.
- Standard group commands: `/settings` (worker-style settings with toggles and sub-pages),
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

## Stage 4 — join CAPTCHA, anti-raid shield and welcome DMs

- Security settings page: join CAPTCHA (new members stay muted until
  they tap their personal verify button) and an anti-raid join shield
  (6 joins/minute trips a 10-minute lock; joiners during the lock are
  banned for 15 minutes and the group is told why).
- Greetings can also DM the welcome text to the new member
  (best-effort; requires the member to be reachable by the bot).

## Stage 5 — member cards, reactions and styled buttons

- `/whois` (reply or ID) shows a member card — name, ID, chat status,
  warn count — with Warn/Mute/Kick/Ban buttons for admins. Buttons
  re-check admin rights and admin immunity before acting.
- Successful moderation actions add a small reaction (⚠️/🔨/🤐/👢) to
  the command message, best-effort so it can never fail the action.
- Settings toggles use Bot API colored button styles (green/red) when
  the client supports them, with a plain-button fallback.

## Stage 6 — approvals

- `/approve` (reply or ID, optional reason), `/unapprove`, `/approval`,
  `/approved`, and owner-only `/unapproveall`. Approved members are
  exempt from the shield, flood watch and night mode (admins remain
  governed separately). Approval state shows on the settings page.

## Stage 7 — DM connections

- In DM, `/connect <group_id>` links an admin to a group after verifying
  their admin status there; `/connection` shows it and `/disconnect`
  clears it. While connected, moderation commands (`/warn`, `/mute`,
  `/kick`, `/ban`, … by user ID) act on that group, and `/settings` (in DM)
  shows its settings read-only (buttons still live in the group).
  Connections are process-local and re-verified on every action.

## Stage 8 — DM settings naming + moderation log chat

- The DM settings view is plain `/settings` (no plugin-style command
  names anywhere in this worker; a regression test enforces that).
- `/setlog <chat_id>` (verified by a test post), `/logchannel`, and
  `/unsetlog` route moderation log posts to another group/channel.

## Stage 9 — welcome viewing, resets, media welcomes, clean welcome

- `/welcome` previews the greeting (formatted), `/welcome noformat`
  shows the raw template, `/welcome on|off` toggles greetings.
  `/resetwelcome` / `/resetgoodbye` restore the defaults.
- Replying to a photo/sticker with `/setwelcome` stores it as a media
  welcome (photo gets the text as caption; stickers send alone).
- Clean welcome (Settings → Greetings) deletes the previous welcome
  message whenever a new one is posted.

## Stage 10 — service cleaning, languages, UI refresh

- `/language` views or sets the worker's per-group UI language
  (English, हिन्दी, Español) with a matching 🌐 button in settings;
  core UI strings are localized with a safe English fallback.
- Clean service messages toggle deletes Telegram service posts
  (join/leave/pin notices) while greetings keep working.
- Settings home now uses a blockquote status card for a cleaner read.

## Stage 11 — moderation log

- Every moderation action is recorded per (clone, chat) in
  `/modlog` (newest first, kept to the latest 200 entries), and joins
  and leaves are posted to the log chat alongside moderation posts.

## Stage 12 — AFK and settings backup (fresh implementations)

Ideas reviewed from a GPL-licensed reference bot's public feature list;
implemented clean-room in this worker's own code (no third-party code
copied, so no foreign credits or license encumbrance):

- `/afk [reason]` marks a member away; replying to their message
  announces it, and their next message clears it with a welcome-back.
- `/export` posts the group's settings as a JSON backup; `/import`
  (inline or by reply) validates types and bounds against the known
  settings model and applies nothing when validation fails.

## Stage 13 — individual sticker blocklist

- Reply to any sticker with `/blocksticker` to block just that sticker
  (matched by Telegram's stable file_unique_id); `/unblocksticker`
  reverses it and `/blockedstickers` shows the count. Blocked stickers
  are deleted even when the broader shield is off; admins and approved
  members keep their existing exemptions.

## Stage 14 — federations

- The clone owner creates a federation with `/createfed` (DM or group);
  group admins join with `/joinfed <id>`, leave with `/leavefed`, and
  inspect with `/fedinfo`. `/fedban` bans a member in every joined
  group (reporting how many succeeded), `/fedunban` lifts it, and
  `/fedbans` lists them. Federation-banned users are also re-banned on
  join and on their next message. Federations are scoped per clone —
  one clone's federation can never affect another clone's groups.

## Stage 15 — admin tools and welcome rules button

- `/promote` grants moderation admin rights (no promote-others, no
  change-info), `/demote` removes them (owner is protected), `/title`
  renames the group, and `/chatinfo` shows title, ID, member count and
  feature state. Promotions are recorded in the moderation log.
- Welcome messages now carry a 📜 Read the rules button when rules are
  set; anyone can tap it to have the rules posted.

## Running standalone

This worker is its own program:

    cd VidelBot && GROUP_WORKER_STANDALONE=1 python -m group_worker

It starts every clone whose profile is `group`, and stops them all
cleanly on SIGINT/SIGTERM. The `GROUP_WORKER_STANDALONE=1` opt-in is a
fail-closed guard: the main bot (`run.py`) also serves group clones
today, and two processes serving the same clone answer every update
twice. Only run this standalone once the main process no longer
serves group clones. `run.py` does not import this package.

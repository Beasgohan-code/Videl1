# Group Management Worker (clones)

A **separate worker program** for clones whose profile is Group
Management — built the same way the File Store worker
(`filestore/worker_bot/`) serves File Store clones, but as its own
codebase:

- It does **not** import or copy the main bot's `plugins/group_mod`
  plugin, and it does not modify the File Store worker in any way.
- Standard group commands: `/settings` (worker-style settings with toggles and sub-pages),
  `/warn`, `/unwarn`, `/mute`, `/tmute`, `/unmute`, `/kick`, `/ban`,
  `/tban`, `/sban`, `/unban`, `/purge`, and `/antichannel`, with greetings,
  a content shield (links/media/stickers/forwards/bots), warns with automatic
  mute/kick/ban, and a configurable flood watch.
- Read-only AniList lookups: `/anime`, `/manga`, `/character`, `/gettags`,
  `/browse`, `/airing`, `/topanime`, and `/anisettings`. These use a bounded,
  throttled public API client and escaped, spoiler-aware result cards.
- Settings and warns live in `group_worker_settings` /
  `group_worker_warns`, scoped by (clone bot, chat), so clones are
  isolated from each other and from File Store data.
- Private `/start` and `/help`, group `/help`, interactive `/settings`, and
  list/report screens (`/notes`, `/approved`, `/blacklist`, `/blsticker`,
  `/whois`, `/locks`, `/chatinfo`, `/adminlist`, `/stats`, `/modlog`,
  `/disabled`) use structured Rich UI with a classic HTML fallback. Buttons
  use consistent success/primary/danger styles when supported. Start pages
  provide Add me, Help, Owner, Support, and Updates buttons. Support uses `SUPPORT_URL`
  then `CONTACT_URL`, with the clone owner as a fallback; Updates uses
  `UPDATES_URL` or the project's GitHub releases page. Configured links are
  accepted only as HTTPS URLs.

## Status

Stages 1–23 are implemented and covered by offline tests. This package
owns its Group Management handlers, store, client lifecycle, moderation
suite, and Rich UI; it does not import or modify the File Store worker.
The standalone engine can start and stop group-profile clones, including
retrying a client whose previous stop could not be confirmed.

Production cutover is still deliberately separate: `run.py` currently
serves Group profiles through the existing in-process profile. Do not run
both programs for the same clone; a single-controller migration and
non-overlapping rollout are still required. This standalone package is
not imported into `run.py`.

## Stage 2 — rules, auto-replies, reports and DM alerts

- `/rules` / `/setrules` for group rules; `/filter`, `/mfilter`, `/stop`,
  `/filters` and owner-only `/stopall` for whole-word text/media auto-replies.
  Text replies support Markdown and safe user/chat placeholders.
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
  (join/leave/pin notices) while greetings keep working. `/cleaner [on|off]`
  toggles both service-message and slash-command cleanup; command messages
  are removed after registered command handlers have processed them.
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

- Reply to any sticker with `/blocksticker` (or `/addblsticker`) to block
  just that sticker by Telegram's stable `file_unique_id`; `/unblocksticker`
  (or `/unblsticker`) reverses it and `/blockedstickers` (or `/blsticker`)
  lists the saved IDs. Matching stickers use the configured blacklist mode;
  admins and approved members are exempt.

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

## Stage 16 — content locks

Standard `/lock <type>`, `/unlock <type>` and `/locks` commands map
onto the shield: locking a type (links, media, stickers, forwards,
bots, all) enables the shield for it; unlocking `all`, or one type
out of an `all` lock, expands correctly. Lock changes are recorded
in the moderation log.

## Stage 17 — rich UI and full-management extras

- Rich UI kit: heading bars plus aligned monospace `<pre>` tables.
  Settings home, /chatinfo, /whois, /locks, /stats and the start
  Help/About pages now render as clean tables under bold headings.
- `/disable <command>` / `/enable <command>` / `/disabled`: per-group
  command disabling for members (a group -1 gate deletes the message
  and stops propagation; admins always bypass). Core commands
  (start/help/settings/disable/enable/disabled) can never be disabled.
- `/adminlist` shows the owner/admin/bot roster with tap-to-mention
  links; `/stats` shows members, logged actions and worker state.


## Stage 18 — bounded flood controls

The Flood watch settings page now lets group admins cycle the message
threshold (3–20 messages) and detection window (3–60 seconds), as well as
the existing automatic action. Each control has a fixed, validated option
set; these settings remain scoped to the clone and group.


## Stage 19 — AniList lookups and channel-profile guard

The worker offers read-only `/anime`, `/manga`, `/character`, `/gettags`,
`/browse`, `/airing`, `/topanime` and `/anisettings` commands. AniList requests
use a fixed GraphQL endpoint, bounded search inputs, a short process-local
cache, per-clone request throttling and timeouts. Result text is escaped,
anime/manga searches request the non-adult filter, spoiler-marked tags are
omitted, and browse buttons link only to validated AniList item URLs.
External API availability and live Telegram rendering still require
live validation.

Group admins can toggle `/antichannel` or use the Security settings page.
When enabled, posts sent under an external channel profile are deleted;
anonymous posts sent as the group itself are deliberately exempt. This only
attempts deletion (no channel ban), and Telegram bot delete permissions still
apply.


## Stage 20 — per-group word and sticker blacklists

- `/blacklist`, `/addblacklist`, `/unblacklist` list, add and remove up to
  100 blocked terms. Matches are case-insensitive whole words/phrases; quote
  phrases in commands (for example `/addblacklist "red flag"`) or reply to a
  message to add/remove its text as one term. Each clone and group has separate
  lists.
- `/blacklistmode off|del|warn|ban|kick|mute|tban|tmute [duration]` selects
  the action for both word and sticker matches. New groups default to `del`;
  timed modes accept 1 minute–365 days (for example `tban 1h`). `warn` uses
  the existing warning limit/action escalation. Approved members and group
  admins are exempt. The guard only acts on member messages; Telegram bot
  delete/restrict/ban permissions are required for corresponding actions.
- Lists are bounded to 100 terms/sticker IDs; each term is capped to 64
  characters and matching is offline-tested. Live Telegram permissions and
  moderation outcomes still need validation.


## Stage 21 — combined cleaner toggle

Group admins can use `/cleaner [on|off]` to control service-message cleanup
and deletion of incoming messages beginning with `/` after handlers have
processed commands. The toggle updates both settings together; the existing
service-message setting remains visible in Settings. Deletion requires bot
permissions and is best-effort.


## Stage 22 — Markdown and media filters

- Admins can add text replies with `/filter <trigger> <Markdown reply>`; quote
  multiword triggers. Supports `{first}`, `{last}`, `{fullname}`, `{username}`,
  `{id}`, `{chatname}`, `{chatid}` and `{mention}`. Dynamic values are escaped
  before Markdown parsing, and matching is case-insensitive whole-word/phrase.
- Reply to a photo, video, animation, document, audio, voice or sticker with
  `/mfilter <trigger>` to save a media reply. Captions use the same placeholders;
  `/stop <trigger>` removes either kind and `/filters` lists both.
- `/stopall` clears all text and media replies and is restricted to the clone
  owner (not merely a group administrator). Each list is bounded, validated on
  import, and stored per clone/group.


## Stage 23 — message and forward IDs

`/id` works in groups and private chats. It shows the current user's ID and
username, chat ID, and message ID; when replying, it reports the replied sender
instead. Forwarded user/chat origins are shown when Telegram supplies them,
including hidden-sender labels without inventing an ID. Usernames, titles and
forward metadata are HTML-escaped.

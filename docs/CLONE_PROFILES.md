# Experimental clone profiles

Clone owners can choose one profile in their private bot dashboard. Legacy records
without `clone_profile` use File Store. A profile switch changes only the registry
field; it does not delete File Store files, links, settings, administrators, or
other clone data. Stored links are not served while Group Management is active.
Stop the clone, select the profile, and start it again. Music is displayed as
unavailable: there is **no voice-chat playback runtime** in this implementation.

Group Management is an independent clone worker, not a registration of the
main bot's group plugin. It currently implements a standard subset of
group-management features — restrictions, pins, purges, warnings, filters,
rules, welcomes and notes:

- `/ban`, `/tban`, `/dban`, `/sban`, `/unban` (plus legacy `/gban`, `/gunban`);
- `/mute`, `/tmute`, `/dmute`, `/smute`, `/unmute` and `/kick`, `/dkick`, `/skick`;
- `/del`, `/pin`, `/pin loud`, `/unpin`, `/groupid` and `/help` (`/ghelp`);
- `/rules`, `/setrules`, `/resetrules` and `/welcome`, `/setwelcome`, `/resetwelcome`;
- `/save NAME TEXT`, `/get NAME`, `/notes` and `/clear NAME` for plain-text group notes;
- `/warn`, `/warns`, `/rmwarn` and `/resetwarn` for manual warning counters;
- `/filter WORD TEXT`, `/filters` and `/stopfilter WORD` for opt-in text replies.

Notes are **public to group members**, not private storage; do not save secrets.
Only directly identified group owners or administrators with change-info rights
can save or clear notes; any group member can list and read them. Names are
case-insensitive ASCII letters, digits or underscores (1–32 characters); text
is plain and escaped, limited to 1,000 characters, and each clone/group pair
holds at most 50 notes. Saving an existing name replaces it; clearing one note
is explicit and leaves other notes and clone data intact. Notes do not support
attachments, hashtags, private/admin-only delivery, rich formatting or scheduled
repeats. They are not full compatibility. Notes survive profile switches
and clone soft-deletion; like other filestore collections they can be included
in administrator backups. Clearing a note does not erase existing backups.

Note updates use a bounded optimistic compare-and-set on one clone/group MongoDB
document, with a new random version per write. Confirmed conflicts and initial
duplicate-key races are retried; uncertain writes are not automatically retried.
Other code or legacy/external writers must not modify this document without the
same version protocol; restore operations that reintroduce an old version can
cause ABA, so do not restore concurrently with note updates. Mock tests are not
proof of real MongoDB concurrency or Telegram delivery.

These commands also accept a dot prefix (for example `.ban`). Actions target a
replied-to user, numeric user ID, or resolvable `@username`. Reply-based `d` and
`s` commands delete the replied message *after* the moderation action is
confirmed. Silent commands also attempt to delete the command and do not post a
success message; if any deletion is uncertain a warning is sent separately when
possible. Timed bans/mutes accept `m`, `h`, `d` and `w` units between **5 minutes
and 365 days**. Very long API delays or an incorrect system clock can still affect
Telegram's interpretation of a timed restriction; verify the result in Telegram.
Reasons are shown in non-silent responses, but are not stored or sent to a log
channel. This is not full compatibility: automatic warning actions, advanced
filters, locks, antiflood, federations, logging, purges and other features are not implemented here.

Bans, mutes, kicks and their reversals require a **supergroup**, with the actor and
bot both having Telegram restrict-members permission; delete variants also require
delete-messages permission. `/del` requires delete-messages permission and
pin/unpin require pin-messages permission for both actor and bot. Anonymous or
sender-chat actors, admins, owners, unknown target statuses and unverifiable
permissions are rejected. Username resolution can fail when Telegram cannot
resolve a user. Bot privacy and permissions still affect which updates arrive.
Rights can change between checks and the actual action: Telegram is the final
authority. A failed API call may have taken effect, so check before retrying.
Kicking is a two-step ban/unban; if the second step is uncertain, the user may
remain banned. Muting overwrites any existing per-member restrictions; unmuting
replaces them with verified current group defaults, not their previous custom
permissions. No main-bot group settings or warning records are used; warning counters
in this worker never trigger automatic moderation.
Some previously installed per-chat Telegram command menus may remain visible even
though File Store handlers are not active.

### Opt-in keyword replies

A directly identified group owner or administrator with change-info rights can
add a reply using `/filter WORD TEXT`, replace it with the same command, and
remove it with `/stopfilter WORD`. Any group member can list triggers with
`/filters`. Commands also accept a dot prefix. Each clone/group has an
independent set of up to 25 triggers. Trigger names are case-insensitive ASCII
letters, digits or underscores, 1–32 characters; the response is plain text up
to 1,000 characters and is HTML-escaped when sent. Filters start empty and
respond only after an admin explicitly saves one. Replies are visible in the
group; do not save secrets.

On ordinary group text from directly identified non-bot users, the worker
looks for a whole-word match, using Unicode word boundaries and case-insensitive
matching. Where multiple triggers match, the longest wins; ties are alphabetical.
It skips slash/dot/exclamation/hash-prefixed messages, bot senders, sender-chat
posts, outgoing messages and text over 4,096 characters. It uses a separate
handler group so normal clone commands still run. This is **not** a complete filter system: phrases, media, formatting, buttons, per-user rules and `/stop` or
`/stopall` are not implemented. Telegram bot privacy mode or missing group
permissions can prevent ordinary messages from reaching the bot.

Reply processing is best-effort, not a durable queue. The process allows one
filter settings check per group per second and at most one attempted reply per
10 seconds per group, with a shared 20-checks/second token bucket (burst 20),
32 concurrent lookups/sends and at most 512 tracked groups. Busy, rate-limited,
saturated, out-of-order or oversized messages are skipped. Read and send calls
have timeouts; uncertain sends are not retried. These limits and replay guards
are process-local, reset on restart, and can be bypassed by multiple workers or
tracker eviction. No message content or sender ID is stored by the listener.
The bot checks Mongo for eligible messages even in groups with no filters, so
there is bounded additional database load. Disabling/removing a filter is not a
fence for a reply already in flight.

Settings use a bounded single-document compare-and-set, keyed by bot and group.
Confirmed version conflicts/initial insert races are retried; uncertain writes
are not. External writers or restores of an old version can break the CAS
protocol; do not restore concurrently with updates. Configured responses survive
profile switches and clone soft-deletion and can be included in administrator
backups. Offline mocks do not prove real Telegram delivery or Mongo concurrency.

### Manual warning counters

`/warn USER [reason]` (or reply with `/warn [reason]`) increments a manual
counter. `/warns` shows the caller's own count; an admin can use `/warns USER`
or reply to inspect another user's count. `/rmwarn USER` removes one warning,
and `/resetwarn USER` resets the count. Both also support replying to the
person. Slash and dot prefixes work. Checking others or changing counters
requires a directly identified admin with restrict-members permission. New
warnings require a currently verified member or restricted member; actors,
this bot, administrators and owners cannot be warned. Reset/removal by an admin
also works for users who have left or become admins, given an ID or resolvable
username. Telegram permissions and membership can change between checks.

This is **not** an automatic punishment system: counts never trigger bans,
mutes, kicks, expiry or deletion, and there are no warning histories or reasons
stored in the database. The reason is escaped and shown once in the group; the
original command and reply can still remain in Telegram. Counts are visible in
the group, capped at 100 per user and scoped by clone, group and user. Entries
survive profile switches and clone soft-deletion, may appear in administrator
backups, and even a reset retains an ordering watermark. Total users with
records per group are not capped. Do not put secrets in warnings.

Each counter update uses a random-version compare-and-set and the incoming
Telegram message ID as a per-user ordering watermark. Replayed commands and
older out-of-order commands are rejected rather than applied twice. As a result,
a legitimate delayed command can be skipped; check `/warns` and send a fresh
command if needed. Uncertain writes are not automatically retried. External
writers or restoring an older backup can break version/watermark assumptions and
allow replay or lost updates; do not restore concurrently with warning updates.
Offline mock tests do not establish real MongoDB concurrency, durable update
delivery or live Telegram permission behavior.

### Group bot guide and setup check

The Group Management clone's private `/start` and group `/help` show an
interactive, read-only guide with separate moderation, message, welcome, notes
and setup sections. The buttons edit the bot's existing guide message; they do
not perform moderation or change settings. Group guides are shared, so any group
member can change the visible guide page. Private guides are restricted to the
receiving private chat. The static guide callbacks check the bot-authored message
and chat before editing. If a guide cannot be edited, run `/help` again.
Ordinary Unicode labels are retained on these WzGram buttons; the separate
experimental premium button feature currently covers primary-bot Bot API text
paths, not this worker.

`/gcheck` (or `.gcheck`) in a group makes a read-only Telegram lookup of this
clone's own membership and reports its role plus restrict, delete, pin and
change-info rights as granted, missing, or unknown. It distinguishes a basic
group from a supergroup, but does not test the invoking admin's rights, target
status, bot privacy, delivery of updates or actual action success. Telegram can
change rights at any time. The result is a snapshot, not a guarantee, and no
settings are changed. The guide and diagnostics depend on Telegram receiving and
delivering messages; their behavior has only been tested offline.

### Rules and welcomes

Rules and text-only welcome settings are stored in the `clone_group_info`
collection keyed by the **clone bot ID and group chat ID**; there is no shared
chat-only settings cache. They are separate from File Store data and survive
switching profiles. `/rules` shows saved plain-text rules **in the group**, not in
a private chat. `/setrules TEXT` sets them and `/resetrules` explicitly removes
them. Configuration requires a directly identified group admin with Telegram's
change-info permission; members can read rules. Only slash and dot-prefixed
commands addressed to this clone are handled. Simultaneous changes to the same
field are last-write-wins; failed or unacknowledged writes are reported as
uncertain, never retried automatically.

Welcomes start **off**. `/setwelcome TEXT` saves plain text without enabling it;
`/welcome on` or `off` changes the toggle without deleting the text. `/welcome`
shows an admin a preview, and `/resetwelcome` explicitly clears the custom text
but leaves the toggle unchanged. The default welcome is used after reset.
Templates support only `{first}`, `{id}` and `{chatname}`; names and text are
HTML-escaped, with no formatting, buttons, mentions, media or Markdown.
Text is limited to 1,000 characters, and templates with potentially excessive
placeholder expansion are rejected. At most the first 10 entries in a join event
are considered; bots are skipped and names/titles are limited to 128 characters.
Welcome delivery depends on Telegram sending
join updates and accepting messages. There is no durable event deduplication or
automatic retry, and a lost response may mean a welcome was sent anyway.

## Choosing a mode during clone setup

After token and channel verification, the private setup wizard asks for one mode
before registering or starting a clone. File Store is available; Group Management
is available only when the operator has enabled the single-instance flag. Music is
shown as unavailable and cannot be selected. The verified channel is still required
for every mode by the existing registration flow. The choice expires after five
minutes; cancelling or expiring it does not register a bot. After registration,
stop the clone and use the dashboard profile selector to change modes.

Restoring a previously removed clone offers **Keep saved mode** rather than
silently replacing its profile. Its token, owner, deletion state and saved mode
are conditionally checked when restoring; old records without a mode retain File
Store. To change a restored clone's mode, use the dashboard after stopping it.
Unsupported saved profiles are not silently overwritten. A saved Group Management
profile cannot be restored while the feature flag is off.

The setup state, one-use buttons, verification-stop tracking and worker locks are
process-local; they are not a distributed lease or a guarantee against old or
multiple workers. A failed verification-client stop blocks setup for that bot in
this process, and an operator must verify that old clients are stopped before
retrying or restarting. A database write or startup interrupted by a lost response
may have succeeded: check My Bots and the observed runtime before retrying. The
5-minute choice timeout is in-memory, and restarts discard pending selections.
Credentials are never included in the choice buttons or confirmation. The
existing token helper still permits plaintext storage when `ENCRYPTION_KEY` is
missing or invalid; operators must configure a valid key before relying on
at-rest encryption, and must retain the original key for existing encrypted data.

## Deployment and limitations

`CLONE_PROFILES_SINGLE_INSTANCE` defaults to `False`. Enable it **only** after
an operator has verified that exactly one up-to-date controller/worker process
can run for these clones, with no old deployments, other controllers or external
writers. This is an explicit operational assumption, not an automatically
verified distributed lock. Do not enable during rolling/overlapping deployments.
The process-local per-bot lock coordinates worker start/stop and selection only
inside that process; existing transfer, delete and active-state writers are not
all serialized with it. Conditional profile updates check current owner, deletion,
active state and previous profile, but do not eliminate ABA, ambiguous database
results or every lifecycle race. If the condition cannot be guaranteed, leave
this feature disabled. A group-profile record fails closed on startup when the
flag is disabled; a legacy File Store clone still starts normally.

A failed or timed-out client stop is treated as uncertain within the current
process, preventing further profile changes/startup until the stop can be
confirmed. This memory is lost on process exit; operators must independently
ensure no old worker remains before enabling or restarting. If a switch result is
ambiguous, reopen the panel and inspect the saved mode before retrying. Actual
Telegram command menus, moderation, failure recovery and multi-process behavior
have not been validated against live Telegram or a real MongoDB replica set.
The automated tests use simulated Telegram and MongoDB and do not prove those
operational assumptions.

### Music feasibility (Videl0 reference)

Videl0 revision `6430b1e5e60e0776a7ab519e05cb98c2ca1a2ebf` has a genuine
PyTgCalls music player, but uses shared global assistant/player state and
Kurigram. It is not a tenant-isolated clone worker. An offline constructor probe
of `py-tgcalls==2.3.3` with installed WzGram 3.1.6 failed before any network
access: its Pyrogram adapter imports `GroupcallForbidden`, which WzGram does
not expose. This is not a complete compatibility audit or proof that an adapter
cannot be built. The PyPI 3.0.0 metadata requires `ntgcalls>=3,<4`; a deliberately
unsupported offline probe with available `ntgcalls==2.2.5` failed importing
`VideoRotation` before the WzGram constructor, so it is not a valid compatibility
test of the supported dependency set. The required native dependency and its
compatible versions, licenses and platform wheels need separate review; do not
install that forced combination. PyTgCalls also brings native `ntgcalls` and LGPL
obligations. See [PyTgCalls](https://github.com/pytgcalls/pytgcalls) and
[the 3.0.0 package](https://pypi.org/project/py-tgcalls/3.0.0/).

Music remains unavailable and the picker does **not** request a session string.
A session is an account credential, not a playback setup detail to collect for a
placeholder. Before enabling it, build and test a per-clone assistant and voice
runtime, compatible streaming/queue and group permissions, fail-closed encrypted
credential lifecycle and deletion, ownership isolation and revocation, resource
limits, restart/shutdown cleanup, and single-instance/distributed coordination.
Real Telegram voice-chat and native-runtime tests are still required. Do not
paste a session string into the current picker.

### Requested Group Management folder separation

The current single `WorkerEngine` is under `filestore/worker_bot/engine.py` and
imports `filestore.worker_bot.group_profile` when starting Group Management.
The six group modules are also currently inside that folder. The controller,
watchdog and existing dashboards rely on that shared engine and registry. The
requested independent folder is **not yet implemented**: moving only the group
files would break the current imports, while introducing a second engine or
Telegram receiver could cause duplicate consumers and split lifecycle locks.

The owner chose to keep every file in `filestore/worker_bot/` unchanged and
investigate a broader shared-controller migration. The existing engine creates
its own singleton and owns the per-bot locks, running clients, uncertain stops,
profile changes and shutdown. A second independent engine, runtime monkey patch,
or changing only some callers could split those safeguards and create competing
receivers. The current engine still starts group clones from its legacy imports;
no new folder is currently active.

`python scripts/audit_clone_controller.py` provides a read-only JSON inventory
of explicit application imports of the existing engine and group modules. It
parses source without importing it, excludes test files, and returns an error for
unreadable or unparseable files. Its current scan finds 35 engine references in
17 files (including two File Store link-generator references to a constant) and
11 group references in five files. It is an aid to review, not proof that all
call sites are found: computed imports, indirect references, plugins and older
running deployments still need manual inspection.

A candidate migration would need one new authoritative controller with the same
registry/locks/uncertain-stop tracking, delegation to the untouched File Store
implementation, a separately packaged group runtime, and a deliberate migration
of every caller including startup, shutdown, watchdog, dashboard, profile selection,
creation and rotation. Simply instantiating a second `WorkerEngine` is unsafe.
The protected legacy group code would remain as inactive historical code until a
separate cleanup is authorized; copying it without a clear ownership and freeze
plan risks behavior drift. Before any cutover, prove exclusive single-client
routing, correct cancellation and stop recovery, preserved records and settings,
File Store parity, old/new worker incompatibilities, and real Telegram/Mongo
behavior in a disposable environment. Keep the default-off gate and require a
coordinated non-overlapping rollout. No such controller migration, deployment or
data migration has been performed. Music remains unavailable.

## Separate Group Management worker (new)

`VidelBot/group_worker/` is a standalone worker program for Group
Management clones, built in the same spirit as the File Store worker
but as its own codebase. It does not import or copy the main bot's
`plugins/group_mod` plugin, does not modify `filestore/worker_bot/`,
and uses normal Standard commands (`/settings`, `/warn`, `/mute`,
`/tmute`, `/kick`, `/ban`, `/tban`, `/sban`, `/unban`, `/purge`), settings model
(`group_worker_settings` / `group_worker_warns`, scoped per clone bot
and chat), guard, warn and flood logic. Stage 1 (core moderation +
greetings) is implemented and unit-tested. It is not wired into
`run.py` yet: group-profile clones are still served by the existing
in-worker profile, and running both would double-handle the same bots.
The production runtime choice and retirement of the old path remain a
separate approved step. A Music worker will be its own folder under
the same pattern once the voice-chat dependency stack is verified.

## Group Management mode ON by default + clone settings (main bot)

Clone profile selection now defaults to enabled
(`CLONE_PROFILES_SINGLE_INSTANCE=True`; set it to `False` to turn
Group Management mode back off) — Group Management can be picked during
clone creation and from the clone profile panel. The profile panel also
has a **Group clone settings** button (owner-only): it lists the groups
that configured the clone and toggles greetings, shield, DM alerts,
CAPTCHA and night mode per group, stored in the group worker's own
`group_worker_settings`. These toggles take effect wherever the group
worker serves the clone; groups appear after the clone has been added
to them and /settings has been used there.

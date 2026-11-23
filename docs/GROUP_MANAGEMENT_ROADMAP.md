# Group management: staged upgrade

`group_mod` is the existing Telegram group-management plugin. This document tracks an incremental upgrade, not a claim that comprehensive management is finished. Implementation status below describes this branch; it does not imply production deployment.

## Stage 1 — moderation safety and warning policy (implemented)

- `/warnconfig` shows the group policy; `/warnconfig 3 mute` sets the threshold (1–20) and action (`mute` for six hours or `ban`). Changing the policy preserves existing warnings and applies on the next warning. The caller needs Telegram's restrict-members right; anonymous group-admin commands are supported.
- Warning entries use bounded compare-and-swap retries to reduce lost concurrent updates. An enforcement claim blocks overlapping warning changes and actions while the threshold action is pending. Warnings are cleared only after a confirmed action and a matching database delete.
- If the Telegram response is uncertain, a process is interrupted, or a reset fails, the claim can remain. **First inspect the member in Telegram and make sure any previous operation has stopped.** After at least two minutes, an authorized admin can reply to the member with `/warnrecover confirm` or use `/warnrecover <user-id-or-username> confirm`. Recovery only removes the claim; it retains warnings and does not retry, reverse, or verify an action. The age threshold does not prove an operation has stopped. After recovery, a new warning at the threshold can trigger another action.
- Moderation permission checks reject missing privileges or unknown caller/bot/target status, and prohibit targeting admins, the caller, or the bot. This can also prevent legitimate bans where Telegram cannot verify a nonmember's status.
- Passive filters avoid destructive enforcement if the sender's membership status cannot be verified. Group dashboard and report callbacks validate actions and appropriate rights, and report failures without claiming success.

### Operational limits

MongoDB and Telegram cannot participate in one transaction; action outcomes can remain ambiguous. Claims are cooperative, so older workers that do not honor them must not run simultaneously with the new code. A manually recovered claim can overlap an operation still in flight. Array compare-and-swap is not immune to ABA changes, and local configuration caches can lag across workers. A database outage can prevent saving a claim or its recovery. Tests use fake Telegram clients and mocked MongoDB; real Telegram permissions, Mongo concurrency, and multi-process behavior have not been verified. Automation settings and other moderation paths still need a separate privilege/safety audit.

### Local test evidence

- Focused group/security suite: 18 passed.
- Full bot suite: 869 passed, 33 skipped because FFmpeg is not installed, with one dependency deprecation warning. The full run preceded a final message-only clarification and redundant-check cleanup; the focused suite was rerun afterwards.
- The group command menu registers 48 entries, below Telegram's 100-command scope limit.

## Following stages — planned, not delivered

1. Audit and harden anti-raid, join-request, flood and direct moderation paths, including checking bot privileges when automation is enabled and making outcomes explicit. Add edge-case and multi-worker/integration tests.
2. Improve group management workflow: clearer settings, moderation history, configurable policies and controls, with tenant isolation, retention/privacy rules and actual Telegram permission checks.
3. Add useful separate plugins incrementally (for example, group notes or FAQ/knowledge commands), each with permissions, storage isolation, discovery, documentation and tests. Continue selecting and implementing individual plugins after the safety foundation.

## Removed plugins — user request (branch source)

`group_insights` and `group_activity` have been removed from source, plugin discovery,
the command menu and anonymous-admin exemptions. This removes the Insights commands
`/policysim` and `/ghealth` and the activity/leaderboard commands `/rankings`,
`/mystats`, `/topusers` and `/topgroups`, as well as the older `/activity`,
`/topmembers`, `/myactivity`, `/memberactivity`, `/groupactivity` and
`/activityprivacy` commands. Other group-management and plugin
features remain. The prior stages for these plugins are historical, not active features.

**Data and rollout:** This source change does not delete the old MongoDB
`group_activity_settings` or `group_activity_members` collections, other plugin
configuration, logs, backups, or previously sent Telegram messages. Once updated
workers run, the removed `/mystats` privacy controls are no longer available;
no data erasure has been performed. Older running workers can still count and
serve rankings until replaced, and previously posted buttons no longer work on
new workers. Operators must coordinate all workers and refresh Telegram command
menus (for example with the existing `/setcommands` flow); no live deployment
or data cleanup has been performed here. Plan any retention/export/deletion
separately with explicit authorization.

The selected advanced group ideas remain a long-term backlog, not delivered;
the exact original 90-item list is not reproduced. Do not reintroduce the two
removed plugins without a new request. Continue other group management work in
reviewable stages, with safety, permissions, isolation and privacy first.

## Stage 6 — join automation and raid safety (implemented)

Join-request auto-approval and the blanket emergency raid shield now require explicit confirmation and live bot-right checks; legacy unconfirmed automatic settings are inactive until reviewed. The dashboard cannot enable either dangerous mode with one tap. Disabling automation does not depend on the bot retaining action permissions. The raid guard checks the current bot and target status before a join ban; anti-flood checks bot rights at enablement and enforcement. Unconfirmed API outcomes are not reported as successful. See `VidelBot/plugins/group_mod/README.md` for the disruptive upgrade notice, exact behavior and multi-worker limitations. This is not adaptive raid detection or a durable incident/alert system.

## Stage 8 — group knowledge (implemented in branch)

The separate `group_knowledge` plugin adds a group-visible `/faq` knowledge base with key search, pagination and buttons, admin-only creation, revision-checked replacement and renaming, and explicit revision-checked deletion. Entries are manually supplied and scoped by bot and group. Limits, retention, permission checks and visibility are documented in `VidelBot/plugins/group_knowledge/README.md`. It is not a confidential moderation casebook or evidence of live Telegram/real Mongo concurrency verification. The broader group-management, security, plugin and frontend roadmaps remain open.

## Stage 9 — private moderation casebook (implemented in branch)

`group_cases` adds a manually maintained casebook in private chat using `/modcase`. Each read, write and callback checks the caller's live Telegram group-admin restriction privilege and the bot's membership; data is scoped by bot and group. Cases support searching by subject ID, revision-checked notes, close/reopen and explicit purge; no Telegram moderation action is performed. Records contain user/admin IDs and manually entered text, are not encrypted by the plugin, and persist until explicit purge; see `VidelBot/plugins/group_cases/README.md` for limits, permission races and retention caveats. This is not an immutable audit ledger or verification of live Telegram/MongoDB. Other advanced plugin, security and frontend roadmap work remains outstanding.

## Stage 10 — read-only group setup check (implemented in branch)

The main `group_mod` plugin adds admin-only `/gcheck`: a read-only diagnostic showing automation state (anti-raid, join requests, anti-flood, warning policy, locks, filters) plus a live snapshot of this bot's restrict/delete/pin/change-info/invite rights as granted, missing, or unknown. It performs no moderation and changes no settings. Rights can change after the snapshot; fake-client tests do not prove live Telegram behavior. This is separate from the clone worker and does not copy the Group Mode plugin into it.

## Stage 11 — Rose-style restriction aliases and warning policy (implemented in branch)

The main `group_mod` plugin adds Miss Rose-style restriction commands alongside the existing `/gban`, `/kick`, `/mute` family: `/ban`, `/tban <time>`, `/dban` (delete+ban), `/sban` (silent), `/unban`, `/tmute`, `/dmute`, `/smute`, `/dkick`, `/skick`, `/swarn`, `/dwarn`, `/rmwarn`, `/resetwarn`. Warning policy gains `/warnings`, `/setwarnlimit` and `/setwarnmode`; `/warnconfig` now accepts `kick|ban|tban TIME|mute|tmute TIME` and threshold enforcement applies the configured mode (plain `mute` keeps the legacy 6-hour duration). All actions reuse the existing live permission verification, admin-target immunity and unconfirmed-result handling. No Rose credits or currency were added.

## Stage 12 — granular locks, blocklists, notes, flood and CAPTCHA (implemented in branch)

- **Locks:** 40+ granular types (text, photo, video, document, audio, voice, videonote, sticker, gif, poll, contact, location, game, dice, checklist, album, command, url, invitelink, botlink, email, phone, emoji, forwarduser/forwardbot/forwardchannel, inline, externalreply, spoiler) plus legacy links/forwards/media/bots/all. `/locks list`, `/locktypes`, `/lockwarns on|off`, and custom per-lock modes via `/lock <types> ### <reason> {mode [time]}`.
- **Blocklists:** `/addblocklist`, `/rmblocklist`, `/blocklist`, `/blocklistmode`, `/blocklistdelete` with word, `"phrase"` and `(a, b)` list triggers. Locks take precedence over blocklists in the passive guard.
- **Filters:** `/filter`, `/stop`, `/filters` with word, `"phrase"`, `(a, b)` list, `prefix:` and `exact:` triggers.
- **Notes:** `/save`, `/get`, `/notes`, `/clear` plus `#trigger` retrieval in the passive guard.
- **Anti-flood:** `/setflood`, `/setfloodtimer`, `/setfloodmode`, `/flood`, `/clearflood` with configurable limit, window and enforcement mode. Unconfirmed Telegram results never claim success.
- **CAPTCHA:** `/captcha on|off`, `/captchamode button`, `/setcaptchatext`. New members are muted until they press the verify button under the welcome message; only the new member can verify.
- **Bot API 10.3:** `/pin`, `/unpin`, `/purge`, `/setwelcome`, `/resetwelcome`. Dashboards (`/gsettings`, `/gcheck`) and the command menu reflect the new state.

## Stage 13 — moderation log (implemented in branch)

Every confirmed moderation action is recorded to a bounded `group_modlog` collection (newest 1000 entries per chat retained): bans, mutes, kicks, warns, unbans, unmutes and all Rose-style aliases, with actor, target, reason and UTC timestamp. `/modlog` lists recent group actions and `/modhistory <user>` lists actions against a specific user; both are admin-only. Logging is best-effort and never raises into the moderation handler. This is an operational aid, not an immutable audit ledger; entries are not encrypted and persist until trimmed by the per-chat cap.

## Stage 14 — Bulk lock controls, URL whitelist, filter cleanup, and admin directory

Status: implemented in the regular-bot `group_mod` plugin; the protected clone worker remains untouched.

- `/lockall` enables the all-content lock, and `/unlockall` clears all locks and per-lock modes after live admin and bot permission checks.
- `/whitelist` and `/rmwhitelist` manage domains exempt from `links` and `url` locks; `/allowlist` and `/rmallowlist` are aliases. Exemption requires every URL in a message to use a whitelisted domain or subdomain, so mixed safe/unsafe messages remain blocked.
- `/stopall` clears every keyword filter after permission verification and reports how many were removed.
- `/adminlist` lists the owner and administrators currently returned by Telegram, with escaped names and username/ID labels.
- Plugin manifest/index upgraded to group_mod 1.3.0 with 82 unique commands; the Telegram group command scope remains below the 100-command ceiling.
- Evidence: focused group-management/security tests and the full backend test suite pass locally with mocked Telegram clients; no live Telegram or production deployment claim is made.

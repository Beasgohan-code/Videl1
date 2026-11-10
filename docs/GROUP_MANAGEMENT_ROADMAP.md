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

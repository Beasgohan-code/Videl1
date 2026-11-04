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

## Stage 2 — Group Insights plugin (implemented)

The independently discoverable `group_insights` plugin adds `/policysim` for read-only previews of current or draft lock rules and `/ghealth` for live bot-permission diagnostics. It shares the existing lock/keyword matcher with the live guard, does not enforce a simulation, and does not persist samples. Usage, privacy considerations, exact coverage and limitations are in `VidelBot/plugins/group_insights/README.md`.

The user's selected ideas span options 1–90 from the conversation. Treat them as a long-term backlog, not as delivered: security and reliable group operations first; cases, workflows and knowledge tools next; then engagement and integrations; advanced AI, cross-group federation, marketplaces, third-party execution and multi-bot orchestration only after separate feasibility, privacy and isolation designs. Prioritize one reviewable increment at a time. Live Telegram integration and real MongoDB multi-process tests remain outstanding.

## Stage 3 — Group Activity plugin (implemented)

The independently discoverable `group_activity` plugin adds opt-in, per-bot/per-group recorded-message totals, top-ten ranking, individual rank, group totals and per-member opt-out. `/activity on` is required before counting. The member privacy command removes their counter and name while retaining an opt-out marker. The initial version was a lifetime leaderboard; the next stage adds bounded UTC-date totals. Neither is a count of all Telegram messages. See `VidelBot/plugins/group_activity/README.md` for exact storage, permissions, retention, race and delivery limitations. Durable replay deduplication, retention management and real Telegram/MongoDB integration remain future work.

## Stage 4 — time-based activity rankings (implemented)

`/topmembers today` and `/topmembers week` use up to seven bounded UTC calendar-date buckets per member, with `/topmembers all` retaining lifetime ranking. Member and group views include today and seven-date message totals. Old lifetime-only records are not backfilled. The seven-date period includes today and is not a rolling 168-hour window. Opt-out removes the new buckets. Contention, mixed-worker deployment, retention and scale limitations are documented in the plugin README.

## Stage 5 — leaderboard navigation (implemented)

Activity leaderboards support period tabs and previous/next buttons as well as `/topmembers [all|today|week] [page]`. Pages are live, group- and bot-scoped, and capped at 100 pages of ten members. Totals can shift between page requests; large-group query performance and live Telegram callback delivery remain unverified.

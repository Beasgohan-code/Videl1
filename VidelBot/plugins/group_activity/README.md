# Group Activity

An opt-in, per-bot and per-group leaderboard based on messages the bot receives. Collection is **off by default** in every group.

## Commands

- `/activity` — admins view whether counting is on; `/activity on` enables it and `/activity off` stops future collection. Requires the Telegram restrict-members permission (or an anonymous group admin). Turning it off **does not erase existing totals**; group members can still view retained results.
- `/topmembers` or `/topmembers all` — browse recorded members by all-time message count. `/topmembers today` ranks the current UTC calendar date; `/topmembers week` ranks the last seven UTC calendar dates **including today** (not a rolling 168-hour interval). Buttons switch periods and pages; `/topmembers week 2` opens a page directly. Ten members are shown per page, up to page 100. Ties are ordered by user ID.
- `/myactivity` — view your all-time count and rank, plus today's and the last seven UTC dates' counts.
- Reply to someone with `/memberactivity` — view their recorded counts and all-time rank if available.
- `/groupactivity` — show recorded all-time group totals plus today and last-seven-date message totals.
- `/activityprivacy off` — opt out in this bot and group, remove your recorded count and display name, and exclude you from rankings and totals. `/activityprivacy on` opts back in; a fresh count starts when counting is enabled and your next message is received. `/activityprivacy` shows your choice.

The bot announces the privacy command when an admin enables counting. Leaderboards and statistics are visible in the group. The plugin stores the bot ID, group ID, user ID, a truncated display name, an all-time counter, up to seven dated activity-count slots and, for opted-out users, an opt-out marker. It does not store message text. Counts are retained until the member opts out or an operator removes them; disabling counting or the plugin does not delete records. The plugin must be enabled to use its privacy command. Other bot components may have separate storage and retention policies.

## Accuracy and operational limits

- Only incoming non-command messages from identifiable human users delivered to this bot while counting is enabled are counted. Bots, anonymous sender-chat posts, service messages and commands are excluded. This is not a count of all Telegram history or all group members. Telegram privacy mode, outages, group permissions, upstream middleware and disabled plugins can affect delivery. Messages later deleted may already have been counted.
- Telegram updates can be redelivered and the existing replay guard is process-local and time-bounded. There is no durable deduplication or real multi-process integration proof; counts are approximate. Historical participants may remain in rankings after leaving a group. Daily and seven-date counts use the UTC date when the bot processes each message, not the message's original timestamp. They begin with this upgrade; old all-time counts cannot be backfilled. Custom time zones and a rolling 168-hour period are not implemented.
- An in-flight message that read the enabled setting before `/activity off` may still be saved after the command. Member opt-out is enforced atomically in the same record as the counter; an opt-out marker is retained to prevent future recording. Manual database deletion of this marker would remove that protection. In-flight reads can briefly show a previously retrieved count after opt-out.
- Each user record contains at most seven daily slots, reused by UTC day. An aged-out slot can remain stored until reused or the user opts out, but is excluded from period totals. During extreme concurrent updates or when an event is processed late enough to target a slot already owned by a newer day, the implementation may count it only in the all-time total. The database and Telegram do not provide exactly-once processing here.
- Leaderboards are live rather than snapshots: counts or opt-outs changing between page requests can shift positions. Members with identical display names may be visually indistinguishable. Pagination is limited to the first 1,000 ranked members.
- A compound index is requested when the plugin starts; an index-creation failure is logged and can make ranking queries slow. Daily and weekly rankings compute scores across the matching group records before sorting and are not indexed by period; large-group load testing is outstanding. Collection checks query MongoDB for every eligible incoming message.
- All workers for the same bot should run this version together. An older worker does not update the dated slots and its older privacy command does not remove them. Period rankings may be incomplete and opt-out erasure may be incomplete during mixed-version operation.
- Per-bot and per-chat IDs scope the new records. Tests use fake Telegram clients and mocked MongoDB; real MongoDB concurrency and live Telegram delivery/privacy settings remain unverified.

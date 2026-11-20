# Telegram BotFather modes: supported scope and safety

The owner enabled Bot Management, Guest Mode and Guard Mode in BotFather. This
branch does **not** verify the settings on the live bot or deploy code. The
owner-only `/botapi` command shows the last available Bot API `getMe` capability
snapshot; an absent/non-boolean capability is shown as Unknown. A flag does not
prove assignment, update delivery, permission, a successful handler or an
online worker. An unsuccessful refresh can leave a cached older snapshot.

## Bot Management

The `/clone` card keeps the original small-caps FileStore layout and **Create
Bot** button. After the user taps it and passes the existing clone eligibility,
quota and cooldown checks, the method screen always offers **Paste a token**.
It also offers **Create a bot with Telegram** only if `MANAGED_BOTS` and the
outgoing bridge are enabled and a fresh bounded `getMe` identifies the configured
bot and reports `can_manage_bots=True`. A timeout, unknown/false capability,
wrong identity or bridge outage hides that option; a cached positive is not
enough. Selecting Telegram creation checks capability again; if it is no longer
confirmed, the manual button remains available. Older direct-entry shortcuts
now open the same choice screen. Users may also paste a valid token directly in
the private chat. The controls are process-local and a delayed message edit may
still arrive after a different choice is made.

Telegram opens its own creation form and the user must confirm; this is not
autonomous BotFather account creation. A suggested or typed username alone no
longer retrieves a token or starts a child-bot update poller. While the setup
window is open, the MTProto creator-bearing update can propose a bot to that
user, including a renamed bot. Videl sends its username (when valid) and numeric
ID in private and requires an explicit one-use five-minute confirmation before
retrieving a token. It then checks both the token prefix and Telegram's token
validation result against the proposed bot ID before continuing through channel
verification and the exclusive mode picker. If the update or confirmation is
missing or uncertain, manual token setup remains available.

The managed link, candidate, timeout and retrieval-failure screens use controls
bound to the active in-process managed attempt. Their Cancel and manual-token
buttons reject another attempt, an advanced wizard or a non-private chat; manual
setup still repeats the normal eligibility checks. A current Cancel can stop a
pending retrieval or validation from advancing the wizard, and retrieval checks
again after the callback acknowledgement and when its task starts. It cannot
retract a Telegram request already sent, and an edit already in flight may still
arrive. Newly rendered manual-token, channel and mode-selection Cancel controls
are also scoped to their current stage. Older unscoped Cancel and Telegram-create
buttons now direct users to `/cancel` or the current setup screen rather than
operating on a potentially different attempt. Generic Home, Back to Menu,
My Bots, dashboard and Create Bot callbacks now avoid discarding an active
setup or setting. The My Bots list remains accessible during registration;
other listed navigation is blocked until it completes. Other entry points and
old workers can still change state differently; see the registration safety
notes.

A Telegram update is not cryptographically correlated to the creation link: a
delayed update from another attempt could be offered during an active setup, so
the user must verify the bot shown. State, expiration and duplicate-click claims
are process-local, not a cross-worker lease or durable queue. Restarts, missed or
delayed updates, mixed versions, receiver overlap, and live Telegram semantics
remain unverified. Capability or a saved clone does not prove a running worker.

Managed clone token rotation requires a private owned-clone confirmation
with a one-use five-minute nonce, a readable original Fernet key,
`CLONE_PROFILES_SINGLE_INSTANCE=True` and the separate default-off
`MANAGED_ROTATION_ENABLED=True` operator opt-in. The outgoing aiogram Bot API
bridge must be available; there is no MTProto fallback for rotation because its
transport retry behavior has not been established safe for this operation.
Telegram may immediately revoke the old token, so the confirmation discloses
the risk. The operation uses the existing per-bot lock in this process and
rereads the owner/deletion/managed/token state and uncertain worker stops.

Before calling Telegram, this version conditionally records a non-secret
`managed_rotation_reservation` on the clone registry document only if the
expected state still matches and no such field exists (including null or
malformed values). Both the reservation and the final save explicitly request
MongoDB `w=majority`, `j=true`, and a 10-second write-concern timeout. A server
that cannot confirm those writes blocks rotation; a timeout/error can still mean
the write happened. This requires appropriate MongoDB server support and is
**not verified against a real server**. The reservation contains an operation ID
and timestamp, not a token; it has no automatic expiry. The handler makes one
Telegram rotation call (not the bridge's automatic RetryAfter retry), then
conditionally stores the new encrypted token **and removes the matching
reservation in the same document update**. A successful save does not restart
the worker: stop and start it from the dashboard and verify its observed state.
Previously issued confirmation buttons without a nonce are refused.

A leftover reservation blocks future rotations, including after process restart,
for this version of the handler. It can remain even if Telegram was never
contacted (e.g. cancellation or lost acknowledgement while reserving). After a
Telegram call, a failed response, encryption/key change, or uncertain save may
mean Telegram revoked the old token. Do not retry blindly or automatically
remove a reservation based on its age. Contact the operator and establish the
actual Telegram and registry state, current owner, old/new token validity and
worker state first, without sharing credentials. Clearing or repairing the
record requires an explicit, separately reviewed recovery plan; no automated
recovery or reservation-clearing command is supplied. A restore of an older
backup may remove a newer reservation, while restoring a backup that contains a
reservation may conservatively block rotation. If a final save commits but its
acknowledgement is lost, the saved token and cleared reservation may exist even
though the current process still blocks retries; after restart that process-local
uncertainty is gone. Do not interpret an absent reservation as proof that no
rotation took place.

The reservation coordinates only rotation attempts using this implementation
and the same intact Mongo document; it is **not** a distributed lifecycle lease.
Old/mixed workers and other ownership, deletion, profile, restore and token
writers may ignore or overwrite it; ABA, key changes across processes and
Telegram's irreversible call remain outside MongoDB transactions. Continue to
require a coordinated single current worker and review
`docs/CLONE_PROFILES.md` before enabling the operator assertion. No manual
restart or recovery is validated automatically. Legacy plaintext tokens matching
the bot ID can be rotated and are sealed on success; other creation paths may
still store tokens in plaintext without a valid key.

## Guest Mode — primary bot only

WzGram 3.1.6 exposes a dedicated `GuestMessageHandler` and
`answer_guest_query`. The primary bot registers a guest handler through the
existing MTProto dispatcher, **not** a second Bot API poller. It replies once
with a fixed public introduction and a prompt to open private chat for setup.
When the primary client has a syntactically valid cached username, the reply
includes a URL button to that username; otherwise it sends the introduction
without a button. The username can change or be reassigned, so recipients
should verify the bot identity before sharing sensitive information. It does not echo or store the guest message or run normal commands, paid/admin
operations, external services, AI or group moderation. It accepts only directly
identified human callers, skips unknown/bot callers, and keeps bounded
process-local replay, per-caller and global rate guards. Numeric query IDs are
canonicalized for the local replay guard (not a distributed deduplication). Queries may be skipped
under load. Responses are public in the invoking chat and can be forwarded. A
failed or timed-out answer is not retried by the handler; it might already have
been sent, and WzGram's transport may have its own retry behavior.
Restart, multiple workers and guard expiration weaken deduplication. Set
`GUEST_REPLIES_ENABLED=False` to disable this handler, independently of the
BotFather setting. Guest functionality is not installed on clone workers.

## Guard Mode — primary bot manual review

When the primary bot is actually assigned as a chat's guardian and WzGram
receives a join-request **query**, a handler ahead of the ordinary force-sub and
group-moderation handlers sends the `QUEUE` decision, leaving the decision to
other admins. It does not approve, decline, launch a CAPTCHA, write a pending
force-sub exemption or notify admins. A query is stopped from reaching legacy
handlers even when queueing is disabled, malformed or uncertain. Ordinary
join requests without a query ID continue through their existing flows. Queue
failure/timeout is not retried by the handler; actual Telegram state is unknown.
WzGram's transport may have its own retry behavior. Bounded process-local replay
and rate guards and the five-second deadline can skip requests.
`GUARD_QUEUE_ENABLED=False` stops queue answers but still isolates these query
updates from legacy handlers. This is a safe default routing attempt, not a
verified guarantee that applicants are queued or that a group is protected.

File Store and Group Management clone workers were not changed. Do **not**
assign a clone as a guardian assuming it has this primary-bot implementation;
the untouched File Store join-request handler has its own existing behavior.
Admins can use `/guardcheck <group/channel ID or @username>` in a private chat
with the primary bot. This read-only check obtains a full chat, verifies the
requester is currently an owner or administrator of that exact chat, and reports
whether Telegram exposes this bot or a different bot as its guard. An absent or
malformed guard value is **unknown**, not proof that no guard is assigned. After
checking the requesting admin, it separately looks up this bot's membership and
`can_invite_users` right. Missing, malformed or failed lookups are unavailable;
these are observations, not guarantees of current authorization. It shows only
this process's queue configuration and does not expose another bot's identity or
prove update delivery, outcomes, or protection. Unsupported links, anonymous/bot
callers and non-admins are rejected, and API failures are reported without raw
errors. Up to three reads each use a five-second timeout; transport cancellation/retries
are not proven to honor a hard wall-clock limit. Diagnostic requests use
a three-second per-caller cooldown, a one-second global start limit and a bounded
in-flight limit, independently of query handling. These are process-local limits;
multiple processes or restarts can bypass them. No group settings or applicants
are changed. Changes in privileges or assignment during or after the check can
make the snapshot stale.

Mixed/old primary workers can still process guard queries with their previous
handlers, so coordinate all workers before relying on the separation. Inspect
permissions and test on a disposable group before any rollout.

## References and validation boundaries

Telegram documents [Guest Bots](https://core.telegram.org/bots/features#guest-bots),
[Bot API guest and join-request query types](https://core.telegram.org/bots/api),
and [join-request Mini Apps](https://core.telegram.org/bots/webapps). The
installed WzGram source was inspected locally for `on_guest_message`,
`answer_guest_query`, `ChatJoinRequest.query_id` and
`answer_chat_join_request_query`. Offline tests use fake clients/mock Mongo;
no real Telegram calls, query timing, user privacy, delivery, live permission
checks, real Mongo concurrency, rollout or security certification are implied.

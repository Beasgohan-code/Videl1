# Telegram BotFather modes: supported scope and safety

The owner enabled Bot Management, Guest Mode and Guard Mode in BotFather. This
branch does **not** verify the settings on the live bot or deploy code. The
owner-only `/botapi` command shows the last available Bot API `getMe` capability
snapshot; an absent/non-boolean capability is shown as Unknown. A flag does not
prove assignment, update delivery, permission, a successful handler or an
online worker. An unsuccessful refresh can leave a cached older snapshot.

## Bot Management

The existing setup offers Telegram's managed-bot creation link when its
`MANAGED_BOTS` and outgoing bridge are enabled and `getMe` reports capability.
The user must still confirm creation in Telegram; this is not autonomous
BotFather account creation. Token/channel verification and the exclusive clone
mode picker remain in the existing setup. The proof watcher for a renamed bot
still uses that child bot's own temporary Bot API update polling; inherited
receiver overlap and managed-bot ownership/concurrency require further audit.

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
with a fixed public introduction and a prompt to open private chat for setup;
it does not echo or store the guest message or run normal commands, paid/admin
operations, external services, AI or group moderation. It accepts only directly
identified human callers, skips unknown/bot callers, and keeps bounded
process-local replay, per-caller and global rate guards. Queries may be skipped
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

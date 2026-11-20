# Telegram clone registration safety

The `/clone` wizard and the webapp's bot records are separate systems. The Telegram clone registry previously checked ownership and later replaced a document by bot ID, allowing concurrent registrations to overwrite an existing record. New registrations now use insert-only writes against MongoDB's built-in unique `_id`; a collision does not replace a record. The wizard reports conflicts and uncertain database outcomes without displaying credentials, and clears its in-memory token when registration is complete or cannot be confirmed.

A previously soft-deleted clone can still be restored by its registered owner. After a token is verified, the wizard warns that continuing updates the token and log channel and reactivates existing settings, including shortener configuration. It preserves the stored settings, creation timestamp, shortener data and other existing fields. The chosen connection method sets the managed flag. The atomic restoration requires the same owner, a still-deleted record and the same saved credential observed at token verification, using simple binary collation. If the record is malformed or has changed, restoration fails rather than replacing it. A user can cancel before continuing.

Clone removal is a **soft delete**, not erasure: the stored token, settings and associated data remain, and the Telegram token is not revoked. The confirmation now states this before the user acts. Worker stopping and status observations are process-local: another worker may still be active. Registration only reports a worker as observed when it is in the current process's worker registry; that observation does not establish its health or which credential it is using.

The creation workflow sanitizes channel-verification and local worker-start error messages, but this is not a full audit of all logs or dependencies. If a database response is uncertain, check My Bots before retrying. If the cooldown write fails after registration, this request does not attempt startup and asks for operator help; the saved record may be observed by other workers.

Remaining limitations: old/mixed-version workers can still execute the former replace operation. A credential comparison does not prevent ABA changes or all owner/deletion/worker lifecycle races. Cooldowns, quotas and worker startup are not transactional with registration, and MongoDB write/network ambiguity remains. Real Mongo, multi-process and Telegram behavior need validation before coordinated rollout. The legacy encryption helper can still store new clone tokens in plaintext when `ENCRYPTION_KEY` is unavailable; this change neither alters the key nor migrates stored credentials. Restoring a deleted clone replaces its stored token with the token voluntarily supplied by its owner; never rotate or replace the backend encryption key blindly.

## Token verification and cancelled setups

The token validation step now claims the current in-process wizard before the
Telegram verification and registry lookup. Duplicate submissions cannot start a
second verification for that same setup. Cancellation or a new wizard while a
validation, registry read, or status reply is pending prevents the old attempt
from advancing or saving its credential to the new wizard. The managed-bot path
now requires a creator-bearing update and explicit private confirmation before
retrieving a token; the suggested-username and renamed-bot proof pollers were
removed. Token results are tied to the original wizard and
verified bot ID. Errors from validation and registry lookup are displayed
generically. Cancellation does not retract a Telegram request already sent or
erase a message already posted.

These guards are process-local, not a distributed setup lock. A restarted or
second controller, missed or delayed updates, old workers, concurrent
registration/lifecycle writers, token plaintext fallback, and network or database
ambiguity remain separate issues. An update is not correlated to the specific
creation link, so the user must check the bot displayed before confirming. Do
not treat these safeguards as proof of real Telegram or MongoDB behavior,
complete secret handling, or safe overlapping workers. Group mode remains
default-off and Music remains unavailable.

## Creation controls

New manual-token, channel and mode-selection Cancel buttons are bound to the
current in-process setup and stage. The manual prompt's Telegram-create button
is likewise scoped; entering the managed link invalidates the prior manual
controls. A Cancel during token or channel verification prevents a stale result
from advancing the setup; once registration is claimed, cancellation is refused.
Old unscoped `cancel_creation` and `managed_new` buttons now fail closed with
guidance; `/cancel` remains available in private chat. This intentionally changes
behavior of old messages after an upgrade.

Generic Home, Back to Menu, My Bots and dashboard callbacks no longer silently
clear a current setup or setting. When one is active they ask the user to
continue or use `/cancel`; buttons in existing bot-settings prompts that formerly
used the generic dashboard as Cancel therefore require `/cancel` instead. A new
Create Bot callback also refuses to replace an active setup. During registration,
My Bots can still show the list without clearing registration, while navigation
to Home, Back to Menu, another creation or the dashboard waits. My Bots/dashboard
also recheck for an active flow after their database reads. Ownership checks
remain in force. These changes cover only these entry points: `/start` and
`/clone` commands still clear eligible in-process flows, and other dashboard
settings/actions, old/mixed-version workers and multi-process writers can still
change state. A sent request, picker-removal message or edit already in flight
cannot be retracted. Nonces and claims are process-local, not distributed fencing;
all behavior requires live Telegram verification.

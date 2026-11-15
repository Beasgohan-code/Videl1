# Telegram-first account and workspace setup

A person opens `/web` in a private bot chat and taps **Open Studio in
Telegram**. A new user does not fill out a registration form: the existing
SaaS login verifies Telegram's signed `initData`, consumes the one-use proof,
and provisions a personal owner workspace before returning the session. A
returning owner reuses an existing owned workspace; a collaborator gets their
own workspace without changing their existing team membership. A browser with
no Telegram launch data still needs the one-time key from the bot. No client
identity, `initDataUnsafe`, or platform-admin status is used as proof.

Personal workspace creation uses a deterministic idempotency identifier,
unique Mongo indexes, non-destructive inserts and ownership/membership checks.
A failed membership insert can be retried without creating another workspace;
conflicting records or unavailable index/storage access fail closed. The
identifier is **not** an authorization secret. The standalone memory adapter
does not enforce the required unique indexes and cannot provision accounts.
No existing workspace or demo record is deleted. The demo entry is hidden from
the refreshed sign-in screen, but this branch does **not** disable the legacy
demo API; it must be addressed separately before any rollout.

The access screen and shared shell tokens have been refreshed with a compact,
mobile-first layout, signup guidance, clear browser-key fallback and keyboard
form submission. Styling is independently authored and informed by Videl0's
mobile-first Telegram access and dark-blue visual language; no Videl0 source
was copied. The rest of the Studio still needs the staged UI rewrite and audit.

## Verification and rollout limits

Local tests use mock Mongo and synthetic signed Telegram data. They cover
non-admin first visits, distinct simultaneous launches, existing collaborator
access, retries, conflicting records, missing indexes and failed provisioning.
Browser tests exercise 320/390/768/1440px, key fallback, first visit, return
visit and navigation to real bot setup. They are not a real Telegram client,
real MongoDB, physical-device or accessibility audit.

Before rollout, test against a disposable real Mongo deployment (including index
permissions, partial failures and multi-worker races), real Telegram mobile and
desktop clients and the actual public HTTPS origin. Old workers may still make
random workspaces and allow replay; coordinate **all** workers. Sessions and
browser links are currently process-local, and a consumed proof requires a new
launch on provisioning or delivery failure. The existing Vercel configuration,
production deployment and stored records have not been changed by this work.

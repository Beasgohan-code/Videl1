# Videl frontend migration

This npm workspace contains an Astro public-site foundation (`apps/site`) and
an in-progress Next App Router Studio (`apps/studio`). The existing Python
backend and served Studio remain in place. These sources are not a production
cutover or feature parity.

```sh
npm ci
npm run typecheck
npm run build
```

The Next application is currently mounted at `/next-preview`; it includes an
overview and partial Bot, Command, File Store, Hosting, Template, Notification, Team and Analytics workflows. Other features
remain in the existing `/app`. The page finder (Ctrl/Cmd K) navigates rebuilt pages locally; it does not search workspace records. The finder and sidebar guard existing Studio drafts and unsaved workspace-creation names; the selector and form-close action also guard workspace names. Browser history and other navigation paths still need a comprehensive unsaved-state audit. To use real data, configure `VIDEL_BACKEND_ORIGIN`
to a plain backend origin at both build and server startup. The Next server
proxies browser requests via same-origin `/api/v1` paths. Without that setting,
sign-in is disabled. The Astro site's `/app` fallback is not a backend either.

**Caution:** a configured preview makes real authenticated reads and writes to
that backend. Do not point it at production without authorization; use an
isolated disposable backend and database for testing. A separate synthetic
fixture and browser test instructions are in `VidelBot/tests/browser/README.md`.

See `docs/FRONTEND_REWRITE_MIGRATION.md` and `TODO.md` for limitations,
security blockers, remaining workflows, and cutover requirements. No provider
integration, release deployment or Android APK is implied by this workspace.

The preview requires a backend containing the companion workspace API changes
(e.g. create-only template records and current file and command contracts).
The branch's older backend may not yet contain them. Do not point this preview
at an older or production backend assuming contract compatibility; the backend
reconciliation and real-service validation are still pending.

Bot management can edit the saved Studio record name, description, category and tags; this does not change the Telegram profile. It requires the conditional-profile capability and uses a separate atomic, workspace-scoped endpoint for changed fields. Category and tags additionally require explicit per-field capabilities; servers without them retain safe name/description editing. Malformed or out-of-range existing category/tag values cannot be edited here. Legacy clients and old workers may still make unconditional writes; this does not resolve authorization-revocation races or all concurrent updates.

Command creation in Next Studio uses a separate create-only endpoint; edits, enable/disable and deletion use a distinct conditional endpoint. Both require advertised capabilities, check the bot and command unique indexes, and do not fall back to legacy mutations. Conditional writes compare a snapshot of selected command metadata, including custom reply and handler binding; deletion does not compare usage counters, timestamps or other unexposed fields. Conflicts retain drafts or confirmations. Index creation may fail on legacy duplicate data or insufficient database permissions; no records are automatically deleted. Old clients can still issue unconditional writes. Permission/bot revocation races, ABA, out-of-band index removal and real Mongo/multi-worker behavior remain open. Commands are metadata, not deployed handlers.

Team mutations require the backend to explicitly advertise reviewed membership
guards; older servers are read-only in this preview. These guards do not solve
all concurrent membership or authorization races.

The conditional profile endpoint uses one document update with the expected
values in its filter, following [MongoDB's single-document atomicity guidance](https://www.mongodb.com/docs/manual/core/write-operations-atomicity/).
The local test suite uses mock Mongo, not a real multi-worker deployment. The
legacy generic bot endpoint remains unconditional for existing clients, and
permission-revocation races, index readiness and mixed-version operation are
not solved by this change.

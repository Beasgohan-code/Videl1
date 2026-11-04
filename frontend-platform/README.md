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
remain in the existing `/app`. To use real data, configure `VIDEL_BACKEND_ORIGIN`
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

Team mutations require the backend to explicitly advertise reviewed membership
guards; older servers are read-only in this preview. These guards do not solve
all concurrent membership or authorization races.

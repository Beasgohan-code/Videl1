# Prepared Vercel front-door migration (not deployed)

The user selected the **existing public front door**, not a separate Next
project. The current project `videl1-frontend` is configured with root
`vercel_frontend`, no framework/build/install command, and proxies `/app` and
`/api/v1/*` to the shared live Render backend. Public `/` redirects to `/app`.
The current production deployment is `dpl_CshP5KES4iKpU5B2KZJ4uhmogwjb`.
The branch preview is not an isolated backend. A Vercel build does not run or
roll out the Telegram bot, and nothing here deletes retained plugin data.

## Prepared candidate

`frontend-platform/apps/studio/vercel.json` provides the current root and admin
redirects and security headers for a **potential** Next-based front door.
`frontend-platform/apps/studio/next.config.mjs` keeps `/next-preview` as the
incomplete Next Studio, with same-origin `/app`, `/app/*` and `/api/v1/*`
forwarded to the configured backend. A Vercel deployment now requires an explicit
HTTPS `VIDEL_BACKEND_ORIGIN` and rejects credential-bearing, path, query and
fragment URLs; non-Vercel local test servers can still use HTTP. This config is
not used by the current project root, and it does not replace `/app` with the
incomplete Next app. Astro landing-site integration is still a separate task.
The admin redirect is specifically to the existing Render host: if a different
backend is selected, review it instead of silently retaining that destination.

Local checks: `npm run test:front-door -w @videl/studio` validates the config
and unsafe/missing backend failure paths. Frontend typecheck and build passed;
a production-mode Next server with a loopback synthetic backend served
`/next-preview` and proxied `/app`, `/app/styles.css`, and an API GET with its
query string. These checks do **not** exercise Vercel routing precedence, POSTs,
authentication, large transfers, live Render, real accounts, or tenant isolation.

## Before modifying the project or production

1. Test the candidate on a non-production Vercel deployment that builds the
   actual Next app. A branch URL using the existing root is **not** that test.
   A separate isolated backend, MongoDB and test bot are preferred. Current
   previews have SSO protection and the shared Render host is live: never run
   mutation tests there without explicit authorization.
2. Work out the exact monorepo root/build/install settings with a test deployment.
   `frontend-platform` owns the npm lockfile/workspaces; Vercel currently has
   explicit empty install/build commands. Pointing the existing project at
   `frontend-platform/apps/studio` and setting a Next framework/build affects
   **all subsequent deployments**, not just a single preview. Ensure the
   lockfile and sibling workspaces are present and the output is recognized.
   Do not change shared project settings before a safe validation plan is agreed.
3. Verify Vercel route precedence and headers, root/admin redirects, `/app` and
   assets, `/api/v1/*` query/POST/status/error behavior, `/next-preview` and
   assets, and disabled/missing backend behavior. Test secure session exchange,
   CSRF, same-origin cookies/headers, Mini App and one-time links with test
   accounts, plus rate limits, transfers, accessibility and production security.
   The old Studio must remain reachable; Next has incomplete feature parity.
4. Keep the current production deployment identified above as a rollback target;
   confirm the actual rollback procedure/permissions, current deployment ID and
   health just before a release. Coordinate with backend owners on compatible
   workers, Mongo and Telegram menu refresh. The removal of group plugins is a
   separate coordinated Render/bot rollout; stored activity data remains.
5. Obtain explicit release approval for the tested artifact, intended backend,
   expected public routing changes and rollback plan before promoting anything.
   Do not delete old deployment artifacts, databases, sessions or plugin data.

No Vercel project settings, domain aliases, production deployment, Render
worker, or stored data were changed by this preparation.

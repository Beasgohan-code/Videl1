# Isolated browser visual checks

These tests use a deliberately **synthetic, read-only** server on port 8082.
It imports the real public and Studio templates, styles, scripts and local vendor
assets, but does not use MongoDB, Telegram, any live account or production APIs.
It returns explicit sample data for the overview, bot directory, commands, configuration, fleet and File Store and refuses all mutations;
other Studio data endpoints return 503. The banner is part of the fixture.
Never point the test at a production service.

In a separate terminal, from the repository root:

```sh
python VidelBot/tests/browser/design_preview.py
```

With Chromium (and its system libraries, including NSS/NSPR) installed:

```sh
cd VidelBot/tests/browser
npm ci
VIDEL_BROWSER_EXECUTABLE=/path/to/chromium npm test
```

The test runs actual Chromium at 1440×900, 768×1024 and 390×844, takes screenshots under
`docs/screenshots/`, checks overflow at those three widths, the widget customizer, bot directory search and layout, command metadata workflow, configuration editor, fleet selection, File Store sample/pagination, guided setup and token-state clearing, failure states, menu semantics, palette keyboard navigation, browser history, persisted groups, mobile drawer/Escape focus return, reduced-motion asset fallback and a local
motion-enabled desktop path. For a serverless Chromium binary, a test-only npm
install of `@sparticuz/chromium@143.0.4` can provide an executable; system
shared libraries are still required. Tests use `--no-sandbox` and should only
run against this isolated fixture in a trusted test environment. Results are
not a substitute for real authenticated APIs, Telegram Mini App, a device
matrix, accessibility audit, performance profiling or deployed testing.


## Navigation shell — 2026-11-04 (uncommitted)

The suite exercises local palette search and keyboard selection, query history, group persistence, active role-visible navigation, mobile drawer/Escape/focus return, and desktop/tablet/mobile layouts. `studio-navigation-palette-desktop.png` and `studio-navigation-drawer-mobile.png` show the synthetic read-only fixture. JSDOM additionally tests partial scopes, synthetic fragment sign-in, denied deep links, canceled unsaved history, stale search and bot selection. These are targeted checks, not a full accessibility audit, physical-device/Telegram Mini App test, live authentication or production evidence.

## Hosting center — rewrite foundation (uncommitted)

`npm run test:hosting` runs a targeted reduced-motion Chromium check at 320, 390 and 1440px against the same read-only synthetic fixture, checking overflow, unavailable provider labels and Fleet navigation. Screenshots `hosting-synthetic-{320,390,1440}.png` use sample records and are not live backend or provider evidence. Set `VIDEL_BROWSER_EXECUTABLE` and any required system library path as above. The check does not cover the full application, physical devices or assistive technologies.

### Next migration preview (synthetic fixture only)

`next_preview_fixture.py` implements a small in-memory fake API and binds only
to loopback; never use it with production data. `next-preview.test.cjs` checks
one-time-key handling, workspace switching/creation, errors and session clearing
in Chromium at several widths. Start the fixture with a Python environment that
has aiohttp, then start the Next dev server in a separate terminal:

```sh
python VidelBot/tests/browser/next_preview_fixture.py
# separately, from frontend-platform/apps/studio:
VIDEL_BACKEND_ORIGIN=http://127.0.0.1:8084 npm run dev -- --port 3001
# separately, from VidelBot/tests/browser:
CHROMIUM_PATH=/path/to/chromium node next-preview.test.cjs
```

The browser visits `http://localhost:3001/next-preview`; the Next server proxies
relative API requests to the local fixture. This is not a real Telegram, MongoDB,
production service or device test, and no screenshot or fixture data is a real
customer workspace. Stop both test servers afterwards.

With the same two test servers running, `node next-studios.test.cjs` exercises
synthetic Bot, Command and File Studio CRUD, permission-denied presentation,
token-diagnostic handling and selected unsaved-navigation checks.
`node next-library-inbox.test.cjs` checks Hosting observations, template
create/copy/delete, notification read state, workspace isolation and permission
presentation at 320/390/1440px. `node next-team.test.cjs` checks direct member grants, reviewed role changes,
removal and role denial against the fixture. `npm run test:next` runs all eleven
Next browser suites. `node next-analytics.test.cjs` checks time/bot filters,
CSV download, workspace changes, sample disclosure, permissions and responsive
layout with fixture data. These assertions do not establish
live Telegram verification, real file safety, production RBAC or full parity.

`node next-settings.test.cjs` exercises account preference saves, account-wide workspace behavior, empty-workspace access, older-backend safety gating, draft/pending guards and uncertain responses at synthetic browser widths. These are in-memory fixtures, not evidence of Mongo concurrency or production behavior.

`node next-file-filters.test.cjs` checks category and active-record filters with synthetic uploads, trash visibility and responsive layout. The fixture is not a storage or authorization integration test.

`node next-theme.test.cjs` checks synthetic saved theme application, device color-scheme changes, navigation, sign-out/account switching, older backend reads, failures, and delayed reads at representative widths. It does not establish production or physical-device accessibility.

`node next-file-view.test.cjs` exercises synthetic account list/grid saves, workspace switching, failed saves and responsive layout. No production file storage or permissions are tested.

`node next-compact-tables.test.cjs` checks the saved account preference on the synthetic Analytics table, including workspace/reload behavior, drafts, failed saves, default values and responsive layout. It is not a real database or accessibility test.

`node next-telegram-login.test.cjs` tests Next Mini App launch and sign-out behavior with a synthetic SDK and fake API. It does not verify real Telegram signatures or MongoDB.

`telegram_auth_fixture.py` is a separate, loopback-only aiohttp app with mock
Mongo. With that fixture running and Chromium configured as above,
`node telegram-auth-backend.test.cjs` verifies a **synthetic signed** Mini App
launch against the actual login backend, including reload, sign-out and replay.
It does not prove live Telegram, real Mongo write concern or multi-worker safety.

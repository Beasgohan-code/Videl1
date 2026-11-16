# Completed work

## 2026-11-04 — Studio overview and navigation refresh

**Target:** `arena/a69b9f62-videl1` (branch baseline `d99f57c`). No merge, deployment, production service, or production data change was made.

The shared Studio Overview gained an original command-center hero and current-record snapshot, preserving the customizable widgets. Counts are derived from workspace API data, with restricted File Store values remaining unavailable and bot states described as “marked running” rather than live-verified. The shell gained a skip-to-content link that targets sign-in before authentication and workspace content after it, plus named landmarks. The command palette now has combobox/listbox semantics, Arrow Up/Down and Enter selection, visible active-result styling, and return-focus handling. Shared styles now honor reduced-motion settings and device safe-area insets, with small-screen adjustments.

The focused regression test covers rendered shell semantics, the new CSS rules, keyboard behavior code and skip-link retargeting. Verification on the final tree: `python -m pytest tests/test_webapp_saas.py -q` — **35 passed, 1 warning**; complete suite `python -m pytest tests -q` — **832 passed, 74 warnings**; Node syntax check, Python compileall for touched modules, and `git diff --check` passed.

An isolated interactive demo Preview rendered in Chromium at 1440px desktop and 390px/320px phone widths with no browser page errors and no horizontal overflow. Screenshots show seeded demo records, not real users or production telemetry:

- [Desktop Overview screenshot](assets/videl1-studio-overview-desktop.png)
- [Mobile Overview screenshot](assets/videl1-studio-overview-mobile.png)

This is a UI milestone only. It does not close the workspace-isolation, route-level authorization, runtime integration, durable scheduler, storage lifecycle, provider security, real-Mongo concurrency, or production deployment blockers in `TODO.md` and `docs/AUDIT.md`.

## Redeem-code recovery and pagination

Commit `f5fd533bd9e54d99292a454d971112cc10458da4` adds private admin `/codes [page]` listing and safe error guidance for uncertain code creation, deletion, message delivery and redemption. Acknowledged batch audits do not contain codes and no operation is automatically retried, rolled back or repaired. See `VidelBot/core/growth.py`, `VidelBot/tests/test_redeem_code_recovery.py` and `docs/REDEEM_CODE_ACCESS.md`.

Verification: focused synthetic handler tests **54 passed**; complete offline backend suite **1106 passed**, with one Python version warning. Tests use fake Telegram and mock MongoDB; no live Telegram delivery, Mongo concurrency, production deployment or repair of existing records was performed. Offset pages can shift and only show active codes; redemption and premium grants remain nontransactional.

## Redeem claim safeguards

Commit `e2d90fe59e96c6475166db05ae2657ba93b2ec28` conditionally claims the exact code record and duration read and rejects invalid or impossible durations before claiming. See `VidelBot/core/growth.py`, `VidelBot/tests/test_redeem_code_recovery.py` and `docs/REDEEM_CODE_ACCESS.md` for the behavior and limitations. Focused synthetic tests **63 passed** and the complete offline backend suite **1115 passed**, with one Python-version warning. No live MongoDB concurrency or Telegram delivery testing was performed; claims and grants remain separate and extreme existing future expiries can still overflow after claiming.

## Redeem expiry preflight

Commit `504e9d6ca611ac1e3be0cb56deb0b5fdcefb1f6e` checks the user's existing Premium expiry using the shared grant calculation before claiming a redeem-code use. Known overflow or a failed preflight read stops that attempt before its claim; users receive a sanitized explanation. Tests in `VidelBot/tests/test_redeem_code_recovery.py` cover overflow, read failure, lifetime behavior, and an expiry change after the preflight. Focused backend tests **134 passed**; full offline backend suite **1121 passed**, with one Python-version warning. These tests use mock Mongo and fake Telegram. The preflight is not atomic: another grant or date change can still cause failure after a claim; no automatic refund or entitlement repair was added. See `docs/REDEEM_CODE_ACCESS.md`.

## Next Studio overview navigation safety

Commit `dd2547d1c6516e04af9be7b81337d2befcbad103` applies the existing draft/pending guard to overview destinations and avoids clearing a draft on modified link clicks. See `frontend-platform/apps/studio/components/WorkspaceApp.tsx`, `VidelBot/tests/browser/next-navigation-safety.test.cjs` and `docs/WORKSPACE_NAVIGATION_SAFETY.md`. `npm run typecheck` and `npm run build` passed for both frontend workspaces; the complete `npm run test:next` Chromium suite passed against a synthetic API, including 320/390/1440px overview and pending-work checks. Modified clicks were dispatched with the browser default canceled, not validated as real new tabs. SPA history, real users, real Telegram, accessibility and deployment remain unverified.

## Astro preview dependency upgrade

Commit `3ebb5c7c3aa836006ec22913c58fdf307b8d758d` upgrades the Astro preview to Astro 7.4.12, `@astrojs/react` 7.1.9 and Vite 8.3.8, with locked sharp 0.35.5 and esbuild 0.28.2. It raises the frontend Node minimum to 22.23.4, adds a built-static-site browser test and a dedicated frontend build/audit workflow. Relevant files: `frontend-platform/package-lock.json`, `frontend-platform/apps/site/package.json`, `.github/workflows/frontend-platform.yml` and `VidelBot/tests/browser/astro-public-build.test.cjs`.

On Node 22.23.4, a fresh `npm ci`, both workspace typechecks and builds passed; `npm audit --json` reported **zero known vulnerabilities** for that lockfile at scan time. Chromium checked 14 static routes, theme behavior, small-screen navigation and standalone Studio fallback at 320/390/1440px; the full Next Studio suite also passed against a synthetic API. These are local tests, not production, live Telegram, real Mongo or physical-device validation. Registry advisories can change; dependency/licensing and deployment exposure reviews remain incomplete. No production configuration or data was changed. See `docs/OUTBOUND_DEPENDENCY_REVIEW.md`.

## Experimental exclusive clone profiles

Commit `08636cc3730cbac83f4661e8d0d537d3a243df59` adds an owner-only selector for a stopped clone, defaulting legacy records to File Store and preserving stored File Store data. The optional Group Management profile is deliberately limited to stateless help, group ID and permission-checked ban/unban; Music voice-chat playback is **not implemented**. The feature is disabled by default and requires an operator-verified single current worker; process-local locks do not make mixed deployments or all existing lifecycle writers safe. Files include `VidelBot/filestore/worker_bot/{engine,group_profile}.py`, `VidelBot/filestore/main_bot/plugins/clone_profiles.py`, `VidelBot/tests/test_clone_profiles.py` and `docs/CLONE_PROFILES.md`.

The complete offline backend suite passed **1152 tests**, with one Python-version warning. Tests use simulated Telegram and MongoDB, including a process-local concurrency test; live Telegram, real MongoDB concurrency, multi-process behavior and deployment have not been validated. See the document for stop uncertainty, old command menus, operational restrictions and remaining risks. No deployment or data migration was performed.

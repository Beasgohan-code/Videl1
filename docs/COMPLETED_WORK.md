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

## Clone Group Management moderation expansion

Commit `46c2027df24cae0a087047b7f145a7b55cdda63f` expands the independent clone Group Management worker using the public Miss Rose guides as behavioral references. It adds slash and dot command prefixes for ban/mute/kick variants, including timed, delete-reply and silent actions, plus message deletion and pin/unpin. It checks current Telegram actor/bot permissions and target status, protects admins/owners, handles uncertain results without retry, and retains the old `/gban` and `/gunban` aliases. See `VidelBot/filestore/worker_bot/group_profile.py`, `VidelBot/tests/test_clone_group_moderation.py` and `docs/CLONE_PROFILES.md` for exact behavior and safety limits.

Final complete offline backend suite: **1210 passed**, one Python-version warning. WzGram filters ran with fake Telegram and mock MongoDB; no live Telegram or real Mongo concurrency was tested. The test environment used pip's legacy resolver and WzGram reinstall after removing Kurigram, so it does not establish a clean dependency-resolution result. Profile switching remains disabled by default and single-instance-only; this is not full Rose feature parity, a voice-chat music implementation, or a production deployment.

## Clone group rules and text welcomes

Commit `9175f23f532b936a85f748eeb3f259d22b5fa9f1` adds the clone worker's `/rules`, `/setrules`, `/resetrules`, `/welcome`, `/setwelcome` and `/resetwelcome` with slash/dot prefixes. Configuration checks the current group administrator's change-info permission; rules and text greetings are scoped by both clone bot ID and group ID, with welcomes off by default and user-controlled content safely escaped. Existing File Store and main-bot group settings are not reused or removed. See `VidelBot/filestore/worker_bot/group_info.py`, `VidelBot/tests/test_clone_group_info.py` and `docs/CLONE_PROFILES.md` for exact behavior and limitations.

The final offline backend suite passed **1232 tests**, with one Python-version warning. These use simulated Telegram and mock MongoDB; real Telegram, MongoDB concurrency, durable delivery and overlapping deployments remain unverified. The test environment used pip's legacy resolver and reinstated WzGram after removing Kurigram; this is not a clean dependency-resolution result. Profile switching stays default-off and subject to its single-instance constraint. No production deployment was performed.

## Clone setup mode selection

Commit `a524218a4483e498686d1c136917ec065190cb45` adds a private mode choice after token and channel verification, before clone registration or startup. New records save the selected File Store or eligible Group Management profile atomically with registration. Music is visibly unavailable, and Group Management retains the default-off single-instance restriction. A restored clone retains its previously saved mode and associated data; mode and token snapshots are included in the conditional restore. See `VidelBot/filestore/main_bot/plugins/create_bot.py`, `VidelBot/filestore/database/main_db.py`, `VidelBot/tests/test_clone_creation_profiles.py` and `docs/CLONE_PROFILES.md`.

The final offline backend suite passed **1254 tests**, with one Python-version warning. Tests use fake Telegram and mock MongoDB; no live Telegram onboarding, real MongoDB concurrency or deployment was validated. Selection expiry, duplicate-click handling, verification-stop tracking and lifecycle locks are process-local, not a distributed lease. A failed or interrupted operation can leave an uncertain registration or runtime; do not blindly retry. The existing credential helper still permits plaintext when no valid encryption key is configured. The test environment used pip's legacy resolver and WzGram reinstall after removing Kurigram, not a clean dependency-resolution test.

## Optional primary-bot premium button icons and music compatibility review

Commit `55829bd6b09374ff4fa745373e26433e16fce9ab` adds default-off, bot-ID-allowlisted custom emoji icons for matching callback/URL buttons on primary Bot API text sends and edits. Original labels/actions and explicit icons are retained; definite emoji-related Bot API bad requests retry without the automatic icons. It does not decorate clone workers or rich/ephemeral/MTProto paths. See `VidelBot/core/premium_icons.py`, `VidelBot/core/botapi.py`, `VidelBot/tests/test_premium_icons.py` and `docs/PREMIUM_BUTTON_ICONS.md` for eligibility and fallback limits.

Videl0 music was reviewed as a reference, not copied. A local offline `py-tgcalls==2.3.3` / WzGram 3.1.6 constructor probe failed on missing `GroupcallForbidden`; real playback, credential lifecycle and tenant isolation are not implemented. Music remains unavailable and no session string is requested. See `docs/CLONE_PROFILES.md`.

Offline focused tests **54 passed** across premium icon, WzGram/richgram and Bot API tests; full backend suite **1258 passed**, one Python-version warning. Tests use fake Telegram and mock Mongo; no live emoji eligibility, voice chat, real Mongo concurrency, or deployment was tested. The local test environment uses the legacy pip resolver and test-only FFmpeg.

## Clone group public text notes

Commit `83b2e491a03839aaab9ea6abb05f9f935ceb6e48` adds independent, plain-text `/save`, `/get`, `/notes` and `/clear` commands (also dot-prefixed) to the Group Management worker, inspired by the [Miss Rose notes guide](https://missrose.org/docs/notes/). Notes are scoped by clone and group; group admins with change-info permission can save and clear, while group members can read them. The implementation bounds note count and size, escapes content and uses a random-version single-document CAS for concurrent changes. See `VidelBot/filestore/worker_bot/group_notes.py`, `group_profile.py`, `VidelBot/tests/test_clone_group_notes.py`, and `docs/CLONE_PROFILES.md`.

Focused offline group/profile tests **115 passed**; full backend tests **1268 passed**, one Python-version warning. Tests use fake Telegram and mock Mongo; real Telegram delivery and Mongo concurrency are unverified. Notes are public in the group, retained through profile switches and soft-deletion, and may appear in administrator backups. Restore or external writers can break CAS assumptions; do not restore concurrently with note updates. Attachments, hashtags and private/admin-only notes are not implemented. Experimental clone profiles remain disabled by default; Music remains unavailable.

## Group clone guide and bot permission snapshot

Commit `717d9f68ea4a33cf8543e6828ff6f106fe638655` replaces the Group Management clone's long private `/start` and group `/help` with an interactive read-only guide: home, moderation, messages, welcomes, notes and setup. Static callback pages only edit a bot-authored guide in a supported chat; they do not perform moderation actions. `/gcheck` / `.gcheck` inspects the current bot's group role and four reported admin permissions without changing anything. Unknown rights remain unknown; actor/target rights, bot privacy and update delivery are not checked. Existing commands and separate profile runtimes are retained. See `VidelBot/filestore/worker_bot/group_ui.py`, `group_profile.py`, `VidelBot/tests/test_clone_group_ui.py` and `docs/CLONE_PROFILES.md`.

Final focused offline tests **126 passed**; full backend suite **1279 passed**, one Python-version warning. One earlier full-suite rerun was stopped after remaining at 84% with high CPU for over five minutes; a subsequent verbose full run completed successfully. Tests use fake Telegram and mock Mongo: live guide rendering, permissions, delivery and real Mongo concurrency remain unverified. The clone UI uses ordinary Unicode buttons; the existing opt-in premium icons are not enabled for clone WzGram paths. Music and other roadmap features remain unfinished.

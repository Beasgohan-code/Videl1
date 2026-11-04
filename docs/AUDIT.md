# Videl1 webapp audit

**Review target:** `arena/a69b9f62-videl1` · **Baseline reviewed:** `d99f57c` · **Scope:** VidelBot multi-workspace SaaS UI and its directly related roadmap, docs and tests. This is a focused engineering review, not a full independent security certification.

## Summary

The branch contains two distinct browser surfaces: the legacy `/admin` operations console and the newer multi-workspace Studio at `/app` with `/api/v1/*`. The Studio has substantial UI/API scaffolding and automated integration/security tests, but the branch's own roadmap correctly says it is **not production-ready**. Frontend polish cannot resolve tenant isolation, runtime wiring, persistence, or deployment risks.

## Verified in source

- `/app`, `/app/styles.css`, `/app/app.js` are served from `VidelBot/webapp/`; the app shares the aiohttp process and session with the broader bot service.
- Existing features include multiple Studio routes, light/dark tokens, workspace and bot selectors, a command palette, mobile navigation, scoped API-key/session handling, CSRF-aware writes, and integration tests for selected RBAC, isolation, redaction, and API flows.
- The overview intentionally labels some workspace-attributed transfers and schedules unavailable instead of presenting inferred counts. That behavior should be preserved until telemetry is genuinely workspace-attributed.
- New UX work in this branch adds a skip link and named navigation landmarks, ARIA combobox/listbox command search semantics, arrow-key/Enter selection with focus restoration, reduced-motion CSS, and safe-area-aware mobile navigation. It is UI-only and makes no claims about backend capability.

## Risks that remain open

1. **Tenant isolation and route authorization:** the root `TODO.md` documents user-scoped downloads/schedules/integrations, incomplete route-by-route RBAC and API-key review, legacy membership/index concerns, and missing real-Mongo concurrency checks. Do not deploy for real tenants on the basis of this UI work.
2. **Runtime integration:** bot configuration/command metadata, automation events, scheduler startup, and several widgets do not consistently control or reflect the Telegram runtime. Clearly distinguish metadata/scaffolding from working execution.
3. **Cross-record safety:** some invitations, promotions, clone operations, and rollback paths are not transactional or bound to immutable previews; partial changes can occur on failure or concurrent edits.
4. **Storage and lifecycle:** workspace file/media cleanup, storage durability/backup, crash recovery, data migration, quotas and demo-record expiry remain unresolved. No automatic ownership inference or destructive migration should be introduced.
5. **Secrets and outbound requests:** review legacy plaintext credentials and secret-containing free text. Every provider/outbound URL flow needs SSRF and DNS-rebinding review; webhook URL validation does not certify other providers.
6. **Deployment and verification:** Vercel is a proxy to the selected Render backend, not an isolated preview. Proxy rate limits/timeouts and large transfers are unverified. Automated tests do not replace live Telegram, production-container, real-Mongo concurrency, or independent security checks.

## Action taken in this review

The Overview received a responsive command-center hero with an API-backed workspace snapshot, preserving the customizable widgets. The snapshot reports registered bots and bots marked running from existing records; it does not claim live worker polling. The app shell and palette received keyboard/accessibility and small-screen refinements. `docs/WEBAPP.md` documents the routes, safe development practices, and verification limits. `TODO.md` remains the canonical, more detailed roadmap and blocker list.

## Verification status

Verification on the final working tree: `python -m pytest tests/test_webapp_saas.py -q` — **35 passed, 1 warning**; `python -m pytest tests -q` — **832 passed, 74 warnings**; Node syntax check, Python compileall for touched modules, and `git diff --check` passed. An isolated demo-mode browser preview rendered the Overview at desktop and 390px/320px widths with no page errors or horizontal overflow; screenshots are in `docs/assets/`. This is a focused UI preview only, not a full browser audit of all Studios. The suite uses the repository's in-memory/test harness; it does not verify live Telegram, a production container, or real-Mongo concurrency. No deployment or production data changes were made as part of this review.

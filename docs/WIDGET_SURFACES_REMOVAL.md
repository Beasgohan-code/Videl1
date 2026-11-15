# Widget Surfaces Removal — Audit

**Repository:** `Beasgohan-code/Videl1`
**Branch:** `arena/a69b9f62-videl1`
**Fetched baseline:** `565904c`
**Scope:** remove the three user-selected widget surfaces while preserving unrelated bot and dashboard behavior.

## Surfaces and boundaries

| Surface | Removed | Preserved |
|---|---|---|
| Telegram `/widget` shared calculator | Command/menu entry, calculator HTML, arithmetic/query handler, saved-calculator-ID reads, widget-query polling and calculator-only tests. | The independent outgoing admin `/dashboard` statistics snapshot and supported private `/info`/`/sys` detail snapshots. The generic sender, beta endpoint configuration, primary-bot guard, snapshot fallbacks and Telegram snapshot tests remain. |
| Studio Everyday Tools | `everyday_tools` navigation item, Overview shortcut, renderer, JS/CSS assets, endpoint/test harness and stale browser-test interaction. | The authenticated Studio's other renderers, fixed API-backed Overview, navigation shell, workspace creation/rename and general web/API tools. |
| Customizable Overview dashboard widgets | Served Overview catalog/customizer, dashboard-layout API and persistence, official layout template/category, bundle export field/import application, and companion frontend `TemplateStudio` category. | Fixed Overview summary, workspace/bot/file data, analytics, notifications, standard bundles, other templates and Telegram dashboard/detail snapshots. |

## Compatibility and safety

The retained `plugins/widget/` manifest is a zero-command feature gate for existing Telegram snapshot handlers; it is not a calculator. `WIDGET_API_BETA=True` and `WIDGET_API_MODE` now apply only to outgoing snapshots, and no Bot API `getUpdates` widget-query listener remains. WzGram remains the normal incoming update consumer.

No live Telegram calls were made. Existing Mongo layout rows and legacy calculator IDs are not destructively deleted, and previously sent Telegram messages are untouched. Legacy bundle files containing an additional `dashboard_layout` key are harmlessly ignored by the regular importer.

## Verification

- Focused removal, snapshot and SaaS webapp tests: **85 passed**.
- Full Python suite: **1,049 passed, 97 warnings**.
- Retained UI DOM suite passed: legacy clone UI, Telegram login, dashboard snapshot, detail snapshot.
- Companion `frontend-platform` typecheck passed: Astro reported **0 errors, 0 warnings**; Studio `tsc --noEmit` passed.
- Python compile, plugin-index JSON validation, served JavaScript syntax, and `git diff --check` passed.
- The full Chromium visual suite did **not** run: its existing `VidelBot/tests/browser/design_preview.py` fails before startup because it imports the absent `webapp.public_site` module (`ModuleNotFoundError`). The obsolete customizer assertions were changed to absence checks; this does not constitute a successful visual run.

The unit and synthetic DOM checks do not certify live Telegram endpoint availability, rendering across clients or production deployment.

# Shared Calculator Widget Audit

**Branch:** `arena/a69b9f62-videl1`\
**Starting revision:** `59e4ff3`\
**Scope:** replace `/game` and its game-only assets/tests with a Bot API 10.4 beta shared HTML calculator.

## Findings and implementation

The previous `plugins/game` package bundled a large local-only arcade plus an outgoing `sendWidget` attempt. Its HTML games did not send widget queries, update shared state or have a bot-side query receiver. The plugin, game catalog, environment flags, Python tests and JSDOM gameplay harness have been removed.

The replacement `plugins/widget` implements `/widget`. The first successful send creates a shared widget with query permission; the returned widget ID is stored in the bot-wide Mongo settings collection. Later sends reference that shared object. The compact widget performs immediate local arithmetic and commits with `TelegramWidget.request`. The bot accepts only a constrained arithmetic grammar, updates shared public state, and answers the requesting widget. The widget watches public-state changes and updates its display.

The new Bot API transport is opt-in (`WIDGET_API_BETA=False`), uses the beta base by default, redacts response bodies and token-bearing URLs from logs, and checks the primary bot identity before sending. It polls only `widget_query` and `shared_widget_query`, refuses a configured webhook, uses the existing single-instance lease, and shuts down through the plugin lifecycle. It does not replace WzGram as the receiver for commands and callbacks.

## Verification and residual risks

Verification passed on this branch: `python -m pytest tests -q` (**890 passed, 94 warnings**) and `npm test` in `VidelBot/tests/ui` (Telegram login plus calculator widget DOM tests passed). Coverage includes expression safety, payload size, shared ID extraction/reuse, query validation and response ordering, webhook refusal, local calculation and mocked shared-state refresh. These are offline tests only; they do not verify delivery at Telegram's live Bot API 10.4 beta endpoint, production availability, cross-platform widget rendering, or compatibility on every Telegram client.

The feature remains disabled unless the operator opts in. Do not enable it on a production token until it has been exercised on an isolated bot with compatible clients and the Bot API update stream is confirmed not to have another poller. If beta methods or response fields differ from the current widget specification, `/widget` fails closed with a user-safe message; ordinary WzGram commands are unaffected.

See [`TELEGRAM_WIDGETS.md`](TELEGRAM_WIDGETS.md) for upstream protocol sources and [`VidelBot/plugins/widget/README.md`](../VidelBot/plugins/widget/README.md) for operator setup and limits.

# Shared Calculator Widget Audit

**Branch:** `arena/a69b9f62-videl1`\
**Starting revision:** `59e4ff3`\
**Scope:** replace `/game` and its game-only assets/tests with a Bot API 10.4 beta shared HTML calculator.

## Findings and implementation

The previous `plugins/game` package bundled a large local-only arcade plus an outgoing `sendWidget` attempt. Its HTML games did not send widget queries, update shared state or have a bot-side query receiver. The plugin, game catalog, environment flags, Python tests and JSDOM gameplay harness have been removed.

The replacement `plugins/widget` implements `/widget`. The first successful send creates a shared widget with query permission; the returned widget ID is stored in the bot-wide Mongo settings collection. Later sends reference that shared object. The compact widget performs immediate local arithmetic and commits with `TelegramWidget.request`. The bot accepts only a constrained arithmetic grammar, updates shared public state, and answers the requesting widget. The widget watches public-state changes and updates its display.

Per the user's follow-up request, `WIDGET_API_BETA` now defaults to `True` in code and the sample configuration, with an explicit `False` opt-out. The transport uses the beta base by default, redacts response bodies and token-bearing URLs from logs, and checks the primary bot identity before sending. It polls only `widget_query` and `shared_widget_query`, refuses a configured webhook, uses the existing single-instance lease, and shuts down through the plugin lifecycle. It does not replace WzGram as the receiver for commands and callbacks. Telegram's public Bot API page still lists 10.3; the official widget-sandbox spec maps these calls to 10.4 and says they were checked against the beta endpoint, but this repository has not exercised a live bot.

## Verification and residual risks

Verification passed on this branch: `python -m pytest tests -q` (**891 passed, 94 warnings**) and `npm test` in `VidelBot/tests/ui` (Telegram login plus calculator widget DOM tests passed). Coverage includes expression safety, payload size, default-on flag/explicit opt-out, shared ID extraction/reuse, query validation and response ordering, webhook refusal, local calculation and mocked shared-state refresh. These are offline tests only; they do not verify delivery at Telegram's live Bot API 10.4 beta endpoint, production availability, cross-platform widget rendering, or compatibility on every Telegram client.

Although the code default is now on, do not rely on it in production until it has been exercised on an isolated bot with compatible clients and the Bot API update stream is confirmed not to have another poller. Set `WIDGET_API_BETA=False` to turn it off. If beta methods or response fields differ from the current widget specification, `/widget` fails closed with a user-safe message; ordinary WzGram commands are unaffected. Pushing this code does not change a separately deployed service's environment variables.

See [`TELEGRAM_WIDGETS.md`](TELEGRAM_WIDGETS.md) for upstream protocol sources and [`VidelBot/plugins/widget/README.md`](../VidelBot/plugins/widget/README.md) for operator setup and limits.

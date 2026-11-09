# Telegram Bot API 10.4 HTML Widgets — Videl Calculator

## What changed

The former `/game` arcade and its bespoke game catalog were removed. `/widget` now sends one self-contained HTML calculator. The widget computes arithmetic locally while buttons are pressed; `=` sends a short query to the bot. Videl parses the expression using a bounded arithmetic grammar, returns the result with `answerWidgetQuery`, and updates the shared widget's `public_data` with `editSharedWidgetData`. Subsequent `/widget` commands send the persisted shared object via `sendSharedWidget`. Compatible clients watching shared public data can render the latest committed result across copies.

## Runtime shape and boundaries

- Bot API 10.4 widget methods and update types are not included in the repository's pinned aiogram 3.31 / Bot API 10.3 models. The plugin makes explicit HTTP calls to the beta API (or to a configured endpoint when deliberately selected).
- `WIDGET_API_BETA` is `False` by default. The operator must enable it on an isolated bot to test. The query listener requests only `widget_query` and `shared_widget_query`, checks `getWebhookInfo`, and refuses to start if a webhook is registered. Do not enable it if another Bot API poller consumes that token's update stream.
- WzGram remains the primary receiver for normal commands/callbacks. The extra receiver is restricted to the widget query types, not a general aiogram dispatcher.
- The widget has no network access, no external assets or libraries, and no `<input>`/form controls. HTML is checked against 32 KiB raw and 4 KiB zlib-compressed limits. Query data is kept well below Telegram's 64-byte limit; state/results are bounded to 1024 bytes.
- Telegram client rendering and API support are beta/client-version dependent. Older or incompatible clients may show only the caption or an unsupported-media placeholder. Local arithmetic and shared-state delivery are distinct: only a compatible widget client can submit a query or watch live shared state.
- Calculator input accepts numbers, parentheses, unary `+`/`-`, and binary `+`, `-`, `*`, `/`; code execution, exponentiation, oversized expressions, deep syntax, divide-by-zero and out-of-range results are rejected.

## Verification

Automated coverage checks expression safety, widget size, sender identity and shared-object reuse, query answer/update order, invalid payload rejection, webhook refusal, local calculator behavior, and simulated public-state refresh. It does not validate Telegram's live beta server or real mobile/desktop clients. Those need a separately configured test bot before production rollout.

## Sources

- [Telegram Bot API](https://core.telegram.org/bots/api) and [Bot API changelog](https://core.telegram.org/bots/api-changelog)
- Telegram's [widget sandbox format](https://github.com/TelegramMessenger/widget-sandbox/blob/main/docs/tgw-format.md), especially the Bot API 10.4 mapping, widget query and public state behavior, size limits, and method names
- Telegram's [widget SDK v1](https://github.com/TelegramMessenger/widget-sandbox/blob/main/docs/sdk-v1.md), particularly `TelegramWidget.request`, `watchPublicData`, and `publicDataChanged`
- Telegram's [widget runtime profile](https://github.com/TelegramMessenger/widget-sandbox/blob/main/docs/widget-runtime-profile.md), which defines the no-network mini-HTML runtime

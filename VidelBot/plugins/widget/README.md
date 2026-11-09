# Shared calculator widget

`/widget` sends a native, self-contained Telegram HTML calculator. Arithmetic is evaluated immediately in the widget as buttons are pressed. Pressing `=` sends a short expression through Telegram's Bot API 10.4 beta widget-query mechanism; Videl validates it without `eval`, answers that request, and updates the shared widget's public state. Copies sent with `/widget` use the same persisted `widget_id`, so supported clients that watch public state can see the latest committed result.

## Availability and safety

`WIDGET_API_BETA` now defaults to `True` in the app and sample configuration, as requested. Set it to `False` to disable the beta transport. The feature requires `AIOGRAM_ENABLED=True` and defaults to `WIDGET_API_MODE=beta`. The query poller subscribes only to `widget_query` and `shared_widget_query`, checks for an existing webhook, and refuses to start if one is configured. Do not use this on a bot whose Bot API update stream is already consumed by another poller. WzGram continues handling regular commands and callbacks. A Git push changes source defaults only; it does not change environment variables in a separately deployed service.

Telegram's public Bot API reference/changelog still reports Bot API 10.3 at the time this guide was updated. Telegram's official widget sandbox documents a Bot API 10.4 mapping and notes verification against the beta endpoint, but real widget delivery and compatible clients still need validation. The default-on flag is experimental, not a claim that production availability is guaranteed. Test on an isolated bot with a compatible Telegram client before relying on it.

`WIDGET_API_MODE=configured` sends requests to `BOT_API_URL` (or the standard Bot API if unset); use it only when that endpoint supports Bot API 10.4 widgets. The default `beta` mode uses `https://api.telegram.org/beta`. A primary-bot identity check prevents the primary token from being used by clone clients. API errors and URLs are not logged because they may contain credentials. The shared widget ID is stored in Videl's bot-wide Mongo settings so later `/widget` calls reuse the same object.

The HTML has no network access, external assets, forms, or third-party JavaScript. Its source and zlib-compressed payload are checked against Telegram's 32 KiB / 4 KiB limits. Server-side arithmetic accepts only numeric literals, parentheses, unary signs, and `+`, `-`, `*`, `/`; it has expression, complexity and output bounds. Widget queries are validated. Telegram's beta specification documents a limit of five requests per five seconds per user and bot, and one shared-object edit per second, so future game features should keep gameplay local and send only occasional bounded updates. Older Telegram clients may display only the caption or may not render the widget. Local arithmetic remains in the widget; shared updates require a client with the widget SDK and query support.

## Tests

From `VidelBot`, run `python -m pytest tests/test_widget.py -q`. UI behavior is exercised by `npm ci && npm test` in `tests/ui`. These tests use a mocked Telegram widget bridge and do not prove delivery through Telegram's live beta API or compatibility on every client. Validate on a separate test bot before depending on shared play.

# Shared calculator widget

`/widget` sends a native, self-contained Telegram HTML calculator. Arithmetic is evaluated immediately in the widget as buttons are pressed. Pressing `=` sends a short expression through Telegram's Bot API 10.4 beta widget-query mechanism; Videl validates it without `eval`, answers that request, and updates the shared widget's public state. Copies sent with `/widget` use the same persisted `widget_id`, so supported clients that watch public state can see the latest committed result.

## Availability and safety

This uses the Telegram widget beta, not Bot API 10.3. It is disabled by default. To opt in on an isolated bot, set `WIDGET_API_BETA=True`, keep `AIOGRAM_ENABLED=True`, and use `WIDGET_API_MODE=beta`. The widget query poller subscribes only to `widget_query` and `shared_widget_query`, checks for an existing webhook, and refuses to start if one is configured. Do not enable it on a bot whose Bot API update stream is already consumed elsewhere. The normal WzGram receiver continues handling commands and callbacks.

`WIDGET_API_MODE=configured` sends requests to `BOT_API_URL` (or the standard Bot API if unset); use it only when that endpoint supports Bot API 10.4 widgets. The default `beta` mode uses `https://api.telegram.org/beta`. A primary-bot identity check prevents the primary token from being used by clone clients. API errors and URLs are not logged because they may contain credentials. The shared widget ID is stored in Videl's bot-wide Mongo settings so later `/widget` calls reuse the same object.

The HTML has no network access, external assets, forms, or third-party JavaScript. Its source and zlib-compressed payload are checked against Telegram's 32 KiB / 4 KiB limits. Server-side arithmetic accepts only numeric literals, parentheses, unary signs, and `+`, `-`, `*`, `/`; it has expression, complexity and output bounds. Widget queries are opt-in, validated and limited by Telegram's own request rules. Older Telegram clients may display only the caption or may not render the widget. Local arithmetic remains in the widget; shared updates require a client with the widget SDK and query support.

## Tests

From `VidelBot`, run `python -m pytest tests/test_widget.py -q`. UI behavior is exercised by `npm ci && npm test` in `tests/ui`. These tests use a mocked Telegram widget bridge and do not prove delivery through Telegram's live beta API or compatibility on every client. Validate on a separate test bot before production use.

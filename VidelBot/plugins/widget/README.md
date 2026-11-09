# Widget platform

In a private chat with the bot, `/widget` (or `/widget calculator`) sends a native, self-contained Telegram HTML calculator. Arithmetic is evaluated immediately in the widget as buttons are pressed. Pressing `=` sends a short expression through Telegram's Bot API 10.4 beta widget-query mechanism; Videl validates it without `eval`, answers that request, and updates the shared widget's public state. Copies sent with `/widget` use the same persisted `widget_id`, so supported clients that watch public state can see the latest committed result.

## Availability and safety

`WIDGET_API_BETA` now defaults to `True` in the app and sample configuration, as requested. Set it to `False` to disable the beta transport. The feature requires `AIOGRAM_ENABLED=True` and defaults to `WIDGET_API_MODE=beta`. The query poller subscribes only to `widget_query` and `shared_widget_query`, checks for an existing webhook, and refuses to start if one is configured. Do not use this on a bot whose Bot API update stream is already consumed by another poller. WzGram continues handling regular commands and callbacks. A Git push changes source defaults only; it does not change environment variables in a separately deployed service.

The current Telegram Bot API changelog could not be independently verified in this environment. Telegram's official widget sandbox documents a Bot API 10.4 mapping and notes verification against the beta endpoint, but real widget delivery and compatible clients still need validation. The default-on flag is experimental, not a claim that production availability is guaranteed. Test on an isolated bot with a compatible Telegram client before relying on it.

`WIDGET_API_MODE=configured` sends requests to `BOT_API_URL` (or the standard Bot API if unset); use it only when that endpoint supports Bot API 10.4 widgets. The default `beta` mode uses `https://api.telegram.org/beta`. A primary-bot identity check prevents the primary token from being used by clone clients. API errors and URLs are not logged because they may contain credentials. The shared widget ID is stored in Videl's bot-wide Mongo settings so later `/widget` calls reuse the same object.

The HTML has no network access, external assets, forms, or third-party JavaScript. Its source and zlib-compressed payload are checked against Telegram's 32 KiB / 4 KiB limits. Server-side arithmetic accepts only numeric literals, parentheses, unary signs, and `+`, `-`, `*`, `/`; it has expression, complexity and output bounds. Widget queries are validated. Telegram's beta specification documents a limit of five requests per five seconds per user and bot, and one shared-object edit per second, so future game features should keep gameplay local and send only occasional bounded updates. Older Telegram clients may display only the caption or may not render the widget. Local arithmetic remains in the widget; shared updates require a client with the widget SDK and query support.

## Tests

From `VidelBot`, run `python -m pytest tests/test_widget.py -q`. UI behavior is exercised by `npm ci && npm test` in `tests/ui`. These tests use a mocked Telegram widget bridge and do not prove delivery through Telegram's live beta API or compatibility on every client. Validate on a separate test bot before depending on shared play.

## Troubleshooting a failed send

A failed `/widget` does **not** by itself prove that Telegram's beta is down or that the receiving client is incompatible. The bot now reports a safe category: request rejected, authentication/permission, endpoint/method not found, conflict, rate limit, upstream server, network/timeout, unreadable response, or local preparation/storage. Operators can use the `Calculator widget send failed` log entry's method, mode, category and numeric code; raw API descriptions, response bodies, URLs and tokens are never logged by this transport. Never post credentials or raw URLs when seeking help.

- Check `WIDGET_API_BETA`, `AIOGRAM_ENABLED`, primary bot identity and `WIDGET_API_MODE`. The endpoint is not switched automatically. `configured` depends on the operator's Bot API server.
- A `sendSharedWidget` rejection can have several causes, including a stale ID, chat restrictions or an unsupported request. Investigate before changing the saved bot-wide `shared_calculator_widget_id`; deleting it affects shared copies and does not guarantee recovery. This change never clears it or silently retries.
- A timeout/network error has an uncertain delivery outcome: check the chat before trying again. If the bot says the widget was sent but the ID could not be stored, do not assume no message was delivered. Resolve database availability first.
- Sending and client rendering are separate: after a successful send, an incompatible client may show an unsupported-media placeholder. Test with an isolated bot and a compatible client.
- **Shared-query safety:** the existing implementation starts a separate Bot API `getUpdates` poller. Checking the webhook does not detect another poller; `allowed_updates` does not isolate the token's update queue. It can conflict with another receiver and its offset can affect other pending updates. Do not enable/run it alongside an existing Bot API update consumer; this diagnostic change does not resolve that architectural risk or prove query delivery. WzGram remains the command receiver. Until a single coordinated incoming receiver supports these update types, shared-sync behavior is not production-validated.

## Everyday tools

In the bot’s private chat, `/widget tools` sends a self-contained tools widget with a bill splitter (tip percentage and up to ten people; displayed amounts round independently to two decimals, so an indivisible remainder may need manual adjustment), a converter (km/mi, kg/lb, °C/°F, cm/in and L/US gallons, including negative values) and a counter. Tabs and controls work locally in each widget on the device: these tools do not send queries, persist state or synchronize between viewers. Their outgoing sender uses aiogram, only trusted built-in assets, and the configured widget endpoint. The calculator’s shared-state behavior is unchanged. `/widget help` lists the shortcuts.

CSS and JavaScript live in `tools/` and are assembled into the one inline document required by the widget runtime. The tool widget has no network access, external assets, forms or user-supplied code. Widget sending still requires a compatible endpoint and client; a failed send may have an uncertain outcome. No automatic retry is made.

### Private chat availability

Videl attempts widget sends only in a private chat with the bot. In groups, `/widget` and `/widget tools` reply with instructions to open the bot in DM; they do not send a widget there or send unsolicited private messages. This is Videl's conservative behavior based on the reported availability, not a claim that every Telegram deployment rejects group widgets: the upstream sandbox documentation describes group support subject to server configuration. Already-sent widgets and forwarded copies are not deleted or changed.

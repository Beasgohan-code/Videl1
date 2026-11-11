# Telegram dashboard and detail snapshots

This package supports existing outgoing-only Telegram snapshots; it does **not** register a `/widget` command or run a Bot API update poller.

## Retained commands

- **`/dashboard`** remains admin-only and sends a fresh aggregate snapshot in private chat, along with the existing charts and web-dashboard entry point. Group requests keep the normal analytics reply and DM guidance. It does not track live updates, embed individual user records, or synchronize a shared widget state.
- **`/info`** and **`/sys`** retain their existing replies and may send bounded detail snapshots in private chat where enabled. `/info` group lookup continues to verify membership and fail closed; `/sys` is owner-only. Existing fallback replies remain if the outgoing snapshot cannot be sent.

These snapshots are distinct from the retired interactive calculator. They are not part of the SaaS Studio's Overview layout, and they do not expose the removed Studio Everyday Tools page.

## Configuration and operation

`WIDGET_API_BETA=True` permits the outgoing sender to use Telegram's beta endpoint; set it to `False` to use the stable/configured API route. `WIDGET_API_MODE=beta` selects Telegram's beta base URL; `configured` uses `BOT_API_URL` or the standard endpoint. An endpoint must support the relevant `sendWidget` request. `AIOGRAM_ENABLED` must also be enabled. Sends use a primary-bot identity check; clone workers and disabled/unavailable clients retain their normal non-widget behavior.

There is no query receiver, `getUpdates` call, webhook probe, shared widget ID, or live shared-state mutation. WzGram remains the bot's incoming update receiver. Previously sent Telegram messages are not deleted or edited by this source change. Existing legacy calculator IDs in bot settings are ignored and are not destructively removed.

The base Overview keeps its fixed API-backed workspace summary. User-customized dashboard layouts are no longer supported; old exported bundles may include an ignored `dashboard_layout` member. General workspace bundles, templates and automations are unaffected.

## Tests

From `VidelBot`, run `python -m pytest tests/test_telegram_dashboard_widget.py tests/test_detail_widgets.py tests/test_widget_surface_removal.py -q`. These use local/mocked data and verify code paths, not live Telegram delivery, all clients, or production service configuration.

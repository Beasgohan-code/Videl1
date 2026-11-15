# Telegram snapshot widgets

## Current code surface

The bot retains outgoing-only Telegram HTML snapshot sends for its admin `/dashboard` statistics and supported private `/info` and `/sys` detail replies. These use the shared safe HTTP sender under `VidelBot/plugins/widget/`, are guarded by the primary-bot identity check and Bot API configuration, and have non-widget reply/chart fallbacks. Snapshot content is not live; users request a fresh snapshot by invoking the existing command again.

There is no interactive `/widget` calculator, shared-calculator ID, widget-query handler, `getUpdates` poller, or shared-state mutation path. WzGram remains the incoming update receiver. `WIDGET_API_BETA` and `WIDGET_API_MODE` remain only for the retained outgoing dashboard/detail sends; they do not enable a separate receiver.

The three retired surfaces and compatibility boundaries are documented in [Widget Surfaces Removal](WIDGET_SURFACES_REMOVAL.md). Existing calculator IDs and customized dashboard-layout records, if present in a deployment database, are left untouched but are no longer read. Old workspace bundles may contain an additional `dashboard_layout` field, which the current importer ignores.

## Runtime constraints

Snapshot requests depend on the configured Bot API endpoint accepting the outgoing widget method. Local unit/DOM tests do not prove live Telegram API availability, supported-client rendering, or production configuration. The project does not start a second update consumer for widgets. Sensitive data handling, private-chat guards, role checks, and normal fallback behavior are retained for the existing dashboard/detail handlers.

See `VidelBot/plugins/widget/README.md` for operator setup, supported commands and local test commands. The supporting [Telegram widget runtime notes](TELEGRAM_WIDGET_RUNTIME.md) explain local protocol tooling; they are not evidence that removed query/shared-state features are deployed.

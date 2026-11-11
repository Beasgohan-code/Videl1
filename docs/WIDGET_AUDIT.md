# Retired-feature audit: Telegram shared calculator

**Status:** Removed by user request from `arena/a69b9f62-videl1`.
**Replacement status:** No replacement command is registered. The older `/widget` calculator and incoming widget-query polling implementation are no longer part of the active bot.

The previous implementation was audited as a Bot API 10.4 beta experiment. Its local expression parser and mocked tests did not demonstrate live Telegram delivery or compatible-client shared-state synchronization. The extra `getUpdates` listener also created a separate update-stream ownership risk. This document is retained as historical audit context, not as a setup guide or feature claim.

The current code keeps only the outgoing, non-interactive `/dashboard`, `/info`, and `/sys` snapshot widgets; it does not poll widget-query updates. For the exact retired surfaces, preserved behavior, database/bundle compatibility and verification, see [Widget Surfaces Removal](WIDGET_SURFACES_REMOVAL.md). Active setup guidance is in `VidelBot/plugins/widget/README.md` and [Telegram snapshot widgets](TELEGRAM_WIDGETS.md).

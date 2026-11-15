# Saved bot feature summary in Next Studio

In the Next migration preview, workspace members with `bot_settings` permission
can select **View features** on a bot record. The panel reads ten known saved
feature flags and labels each Enabled, Disabled or Unavailable. Unavailable means
the field is absent or not a Boolean, not that it is disabled. Other feature
keys and settings are deliberately omitted. These are saved record values, not
evidence of a running worker, deployed handlers, Telegram permissions or actual
feature enforcement. This does not cover legacy Telegram `/clone` registrations.

The new `GET /api/v1/workspaces/{ws_id}/bots/{bot_id}/feature-summary` checks
workspace membership, `bot_settings`, API-key workspace/scope where applicable,
and bot ownership through the existing workspace-scoped lookup. Its versioned
response contains the bot ID and a fixed allowlist of Boolean/null fields only;
no token, settings object, arbitrary custom keys or values are returned. The
backend lookup still reads the bot record internally, as the existing service
does. No records are written. A 404 can mean an older backend or a missing bot;
failures are displayed rather than treated as all-disabled. A newer UI requires
the new backend route; older/mixed workers may return unavailable.

Permission revocation races and multi-worker consistency are not solved by this
read. Test evidence is synthetic sessions, mock/in-memory storage and an
intercepted browser API—not real Mongo, Telegram, physical devices, a full
accessibility audit or production deployment. Existing Studio remains available
and editing, deployment and runtime monitoring parity remain open.

# Retired Studio tools and custom Overview layouts

The Studio **Everyday Tools** page and its Overview shortcut have been removed. The authenticated `/app` continues to provide the base workspace Overview, existing Studios, shared shell, and API-backed workspace/bot/file information.

User-customizable Overview widgets, their API route, saved-layout persistence and layout template have also been removed. The Overview now uses a stable, fixed summary layout. No arithmetic tool, local tool-history state, widget catalog, customize/reset controls or user-specific dashboard layouts are served.

This retirement does not remove Telegram's unrelated outgoing admin `/dashboard` statistics snapshot or private `/info` and `/sys` detail snapshots. It also does not remove the `/web` launcher or the rest of the SaaS app. Existing database layout rows and legacy calculator IDs are not destructively deleted; the application no longer reads them. See [Widget Surfaces Removal](WIDGET_SURFACES_REMOVAL.md) for audit scope and compatibility details.

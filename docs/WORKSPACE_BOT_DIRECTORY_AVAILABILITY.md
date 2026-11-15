# Workspace bot directory availability

The Next Studio loads workspace details and workspace bot records separately.
A failed or malformed bot-list response is shown as unavailable, never as an
empty directory. A scoped retry leaves the rest of the Studio mounted, including
unsaved bot setup and the separate account-scoped Telegram clone panel. The
retry does not send credentials or mutate data. The clone panel still requires
its own successful authentication and requests; it is not a fallback copy of
workspace bots.

Overview, commands and analytics do not imply an empty bot list when that read
fails; unrelated workspace tools remain usable after workspace access succeeds.
401 responses still expire the session. Pending bot-list reads are aborted and
checked against the current workspace, session and read generation to reject
stale results after switches, retries and sign-out.

This does not make failed services work, diagnose production configuration,
add workspace bot pagination or increase the existing maximum of 300 loaded
records. Search and status filters remain limited to those records. Workspace
access failure still blocks workspace views, and a successful list is not
proof of live worker health. Legacy Studio is unchanged. Browser coverage uses
an isolated fake API and intercepted synthetic responses, not real MongoDB,
Telegram, deployed services, physical devices or an accessibility audit.

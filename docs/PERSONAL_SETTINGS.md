# Next personal preferences (partial migration)

`/next-preview/settings` reads account-wide settings independent of workspace
membership and edits only theme, file view and compact tables. After a verified
save the Next preview applies dark, light or the device color scheme. The saved
list/grid preference is applied in the Next File Store, and compact-table spacing
is applied to the Analytics daily data table. It sends only changed fields and
verifies returned values. It disables edits when the backend does not advertise
`atomic_field_updates`; reset and other settings remain in the existing Studio.
Selected navigation and pending-save guards do not cover every history path.

The backend uses Mongo `$set` for validated fields rather than rewriting a cached
snapshot. Settings API GET bypasses its local cache, and writes invalidate the
local cache even on uncertain errors. This avoids the specific disjoint-field
clobber; same-field writes are last-writer-wins. Reset, older workers, caches
used elsewhere, and write/read failures still race. Update compatible workers
together: the capability does not make a mixed-version deployment safe.

Browser coverage uses an in-memory synthetic fixture. It is not real MongoDB
concurrency, Telegram or production verification. Complete the remaining preference application,
other settings/reset parity, broader navigation protection and real-service
checks before treating this migration as complete.

The theme is fetched after authentication and defaults to dark if unavailable;
a failed read is disclosed and falls back to list view and standard table spacing. No credential or preference is stored in localStorage.
System mode listens for device color-scheme changes. Signing out returns to dark.
Navigation or reload may briefly show dark while the account preference loads;
changes in another tab do not automatically update an already-open view. This
applies only to the Next preview, not the existing Studio or Astro public site.

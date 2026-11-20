# Owner clone directory

The existing owner-only, private `/clonestats` (alias `/fsstats`) summarizes
non-deleted registry records by selected profile, including unsupported Music
and unknown values. `/bots [page]` lists up to 15 records per page, including
inactive ones. Missing profile means the legacy File Store mode; malformed
profiles and states are shown as unknown, not silently assigned. Records with
invalid IDs or ambiguous deletion state are excluded and counted. Soft-deleted
records remain stored and are not displayed. Nothing is changed in the registry.

The local client observation is only whether this process has a client object;
it is **not** a health check, proof of active playback, or proof that other
processes have no client. Music voice-chat playback is not available. The
optional user count and worker lookup show unavailable on failure; a failed
registry read never becomes an empty fleet. Directory reads use a MongoDB field
projection that excludes tokens and other unrelated data. IDs and usernames are
validated before display and arbitrary registry data and exception text are not
shown.

This is a bounded *response*, not a bounded database scan: the current code loads
all projected records and sorts them by ID on each request. It is unsuitable as
a large-fleet monitoring service. Pages are not a snapshot and may shift during
writes; pages after 1000 are inaccessible. Scale work should add indexed,
keyset-based pagination and bounded profile aggregation, explicitly handling
concurrent changes and legacy/malformed records. Offline tests use mock MongoDB
and fake Telegram clients; real Mongo behavior, multiple processes and live
Telegram delivery have not been verified.

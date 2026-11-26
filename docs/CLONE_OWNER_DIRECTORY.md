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

`/bots` now fetches one bounded page at a time from MongoDB (sorted by `_id`,
with a DB-side valid/non-deleted filter and field projection), and
`/clonestats` uses bounded profile aggregation plus counts instead of loading
every projected record. The local-client count streams valid `_id`s one at a
time, so memory stays bounded even though that count still scans the fleet.
Pages are not a snapshot and may shift during writes; pages after 1000 are
inaccessible. Keyset pagination, indexed aggregation tuning, and explicit
handling of concurrent changes remain future scale work. Offline tests use
mock MongoDB and fake Telegram clients; real Mongo behavior, multiple
processes and live Telegram delivery have not been verified.

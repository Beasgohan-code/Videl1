# Encoder command admission and argument validation

Encoder submissions now resolve the user's plan before the duplicate and per-user
limit checks. There is no `await` between those checks and adding the task, so two
commands handled concurrently by the same event loop cannot both admit the same
file/settings or exceed the user's queue cap. The previous one-second sleep after
a successful submission has been removed; it delayed the handler without improving
queue ordering.

`/sample`, `/screens`, and timestamp parsing now reject malformed input before a
task is queued or media is downloaded. Sample lengths are clamped to 5–120
seconds, screenshot counts to 1–10, and non-finite or overflowing timestamps such
as `nan`, `inf`, and `1e999` are rejected. Restored queue entries are validated
again by their task handlers, so an invalid persisted command cannot start a
download.

These safeguards are process-local. They do not make the Mongo-backed queue safe
across multiple bot processes, and they do not change encoder settings, worker
counts, hardware selection, or output quality. Live Telegram, real MongoDB
concurrency, and end-to-end FFmpeg timing were not verified.

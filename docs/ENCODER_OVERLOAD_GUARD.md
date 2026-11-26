# Encoder overload guard

The Encoder now has two admission guards in addition to per-user limits:

- `ENC_MAX_QUEUE_TOTAL` (default 50) caps total queued + running Encoder tasks for everyone. When full, new tasks are rejected with a queue-full message instead of growing the in-memory queue without bound. Queue restore after restart also stops at this cap and drops excess persisted tasks.
- When container RAM is at/above `MEMORY_PAUSE_NEW_TASKS_PCT` and the queue is already non-empty, new tasks are rejected with an overloaded message. An idle queue can still start one task, preserving the existing no-starvation behavior; extra parallel slots remain held back while a task is running under memory pressure.
- When free disk is below `MIN_FREE_DISK_GB`, new tasks are rejected with a low-disk message until cleanup frees space.

`/status` shows an Admissions row mirroring these checks: `Open` with the queued/cap count, or `Paused` with the active reasons (queue full, memory pressure while busy, or low disk). Memory pressure alone does not pause an idle queue, matching the admission rule.

These guards are process-local and do not coordinate multiple bot processes or prove live Telegram/MongoDB behavior. They do not change encoder quality, worker counts, or hardware selection.

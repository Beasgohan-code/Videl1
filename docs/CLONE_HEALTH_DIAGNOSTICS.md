# Clone health diagnostics and repair limits

The owner-only private `/check` (alias `/checkbots`) reads the clone registry and
checks worker clients known to **this process**. A matching `get_me` result is a
point-in-time response, not proof of ongoing availability, correct operation in
every group, or exclusive ownership. A missing local client may exist in another
process; a failed check may be a timeout or transient transport problem. Unknown
or malformed records and changed or mismatched clients are reported separately.
Soft-deleted records are excluded. The response lists at most 12 examples per
problem category; it does not reproduce arbitrary database fields or error text.
The underlying registry read is still unbounded and checks run in batches of 20,
with a 15-second timeout per probe. It is not a scalable fleet monitor.

`/check fix` is **not** read-only. For locally unresponsive clients it attempts
to stop those still equal to the previously observed client, then invokes the
existing watchdog's **fleet-wide** `heal_workers`, which may also act on other
clones marked active, including ones outside the displayed problem list. If the client has already changed or its stop is not
confirmed, this handler stops the repair and does not invoke the watchdog. A
failed operation may still have partly succeeded; inspect the actual registry,
worker and Telegram state before any retry. Watchdog's reported count is not
independent evidence that those workers remain online. This change does not alter
the automatic watchdog or protect against old workers.

The identity check and `stop_worker` are **not atomic together**: another local
operation can replace a client between the check and stop, and other processes or
legacy lifecycle writers do not share a distributed fence. A failed or timed-out
client request may still be in progress; transport cancellation is not a proof of
completion. The worker engine's uncertain-stop tracking is process-local and is
lost on restart. `heal_workers` has its own existing concurrency and failure
limits. Coordinate all workers, do not assume a negative check means a worker has
stopped, and do not blindly use `/check fix` during overlapping deployments.
A shared fenced controller, real MongoDB and Telegram failure/concurrency tests,
and a reviewed recovery process remain necessary before treating repair as safe.
No credentials or customer records are migrated or removed by this reporting
change. The protected File Store worker folder remains unchanged.

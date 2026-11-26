# File analytics: UTC windows and limits

The workspace analytics API and CSV report **active indexed File Store records**,
not a complete upload history. Deleted records disappear from past totals; legacy
transfer histories, passive Telegram reach and downloads are unavailable. File
counts are counted separately; storage, period values and daily buckets sample
at most 5,000 matching active records. Sampling and counting are separate reads,
not a consistent snapshot, and concurrent changes can alter results.

For `days=N` the window is the rolling duration `[now - N days, now)` in UTC;
its previous comparison period is the immediately preceding equal duration.
The first and last UTC calendar dates can both be partial, so the daily series
may contain N+1 date buckets. No event at the end boundary is included. CSV
exports make a separate request and can differ from the displayed chart.

An explicit `start_date` and `end_date` must both be ISO `YYYY-MM-DD`, inclusive
UTC dates in order, spanning 1–365 days. Invalid, incomplete, reversed,
out-of-range or unrepresentable values return HTTP 400 instead of silently
falling back to a different window. The previous comparison covers the same
number of immediately preceding whole UTC dates. This is an API behavior change
for callers that previously relied on the silent fallback or clamping.

The API marks corrected date buckets with `complete_utc_buckets: true`; this
means the interval's UTC dates are present, **not** that the 5,000-record
sample is complete. Next warns when an older response lacks this marker. During
a mixed-version rollout, a CSV request may reach an older worker even if the
chart came from a newer one; upgrade all workers before relying on the fix.
These changes do not repair historical records, validate real Mongo concurrency,
or introduce new transfer tracking.

## Next Studio custom dates

The Next preview can apply an inclusive UTC range of 1–365 dates. The chart
and daily table verify the server's bucket capability and every expected date;
missing or mismatched responses are not shown as the selected range. A custom
CSV download is only offered after a valid chart response, and is discarded if
its separate response does not carry `X-Videl-Analytics-Contract: utc-range-v1`.
The header was added with this feature, so even an earlier worker that reports
complete buckets but lacks the CSV contract will block custom export. This is a
compatibility check, not an integrity signature or a snapshot: a mixed rollout
can still produce a retryable error, and the CSV can legitimately differ from
the chart as records change. Rolling preset exports remain compatible with old
servers and retain their older-backend warning.

Dates are filter drafts, not saved settings. Changing workspaces or leaving
through the supported navigation guards asks before discarding unapplied dates;
browser history and clients that suppress confirmations can still lose drafts.

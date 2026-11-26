# Shared inbox read-state safeguards

The Next preview's inbox is a shared workspace view, not a personal unread
queue. Its GET response advertises `conditional_read_updates`; when absent, the
preview shows notifications read-only. A missing/non-boolean read value is
shown as unavailable, not as unread. Filtering covers only the 80 loaded rows.
If a selected category is absent after refresh, it remains visibly selected and
continues to filter the loaded records; Clear filters explicitly resets it, the
search and read-status filters. A failed list read is not presented as an empty
inbox, while an uncertain write preserves the last successfully loaded list.

Single-record changes use the new `POST /notifications/read` endpoint, with
an explicit expected boolean read state and the desired opposite state. The
server requires logs permission, rejects ambiguous duplicate IDs in the
workspace, then conditions the update on the selected Mongo `_id`, workspace,
notification ID and expected read state. A conflict, missing record, missing
identity or unknown state returns 409. A missing acknowledgement or other
failure never returns success. The preview disables mutations on a failed or
uncertain response until a successful inbox refresh. A missing capability or
an older worker without the new route never triggers a fallback to the older
unconditional endpoint.

Mark-all is a separate explicitly confirmed action on the new endpoint. It
applies to the workspace, not the visible filter or 80-row page, and can include
new arrivals. It is not a reviewed snapshot, is not atomic across documents and
may partially complete if a request fails. Refresh before retrying; do not assume
a failure means nothing changed.

These are limited optimistic safeguards, not global serialization: legacy
clients and the older `/notifications` write endpoint remain available and can
write without the expected-state check. ABA changes, notification recreation,
concurrent duplicates inserted after the check, authorization changes during a
write, mixed writers, multi-worker races and real MongoDB behavior remain
unverified. The change does not alter notification retention, erase data or
make read state personal. Fake and synthetic tests do not establish production
or live Telegram behavior.

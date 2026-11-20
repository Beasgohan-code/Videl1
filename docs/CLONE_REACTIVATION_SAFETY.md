# Clone reactivation from idle notifications

The existing `Reactivate` button on an inactivity notice is available only to
the directly identified human owner in their private chat. It accepts only an
explicitly inactive, non-deleted File Store or Group Management record with a
stored credential. A missing profile is the legacy File Store profile; Music and
unknown profiles are not started. An already-active or malformed record requires
opening the dashboard instead. If a client is already present locally while the
record is inactive, this handler neither adopts nor stops it.

After one start attempt, the handler looks for a local client and conditionally
marks the clone active only if its owner, deletion state, active state, stored
credential, profile and last-activity value still match the earlier record.
The Mongo update resets the inactivity clock. A changed field, missing local
client, unacknowledged write or exception yields a generic uncertain-outcome
message, without exception text or a second start/stop/write. An acknowledged
update and local client are described as a snapshot, not proof of availability.
No clone data is removed and no Music or other new runtime is installed.

This comparison is **not a transaction or distributed lifecycle lease**. The
client is started before the conditional write, so a conflict or lost response
can leave it running while the record remains inactive, or the write may have
succeeded despite an error. Do not blindly retry; the operator must inspect the
registry, all relevant workers and Telegram first. The engine rereads the record
on startup, and ownership or other fields may change between the handler's read
and the start. Other writers, watchdogs, overlapping deployments, ABA changes and
old versions do not share a fence. The existing inactivity sweep and dashboard
start/stop paths are not made atomic by this change. Restart loses in-process
worker observations. Offline tests use mock MongoDB and fake clients; actual
Telegram behavior, real Mongo write guarantees and concurrency are unverified.

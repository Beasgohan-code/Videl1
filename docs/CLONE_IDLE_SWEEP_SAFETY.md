# Clone idle sweep: partial safety improvements

The automatic idle sweep handles both File Store and Group Management records.
Before attempting an idle stop it now requires a local client, reloads the clone
record, checks that key registry fields still match and verifies that the local
client object has not changed. Missing clients and stale records are skipped;
unknown or failed plan lookups are no longer replaced with a shorter free-plan
timeout. A plan value of zero still disables idle deactivation for that owner.

The sweep then makes **one** local stop attempt. Only a `True` stop result,
absence of a replacement client at the following check, and an acknowledged
conditional update of the same owner, active/deleted state, credential, profile,
last activity and creation timestamp produce a deactivated result and an owner
notice. No File Store or group data is deleted. Audit-log delivery failure does
not suppress the owner notice; neither delivery is guaranteed. User-facing
messages distinguish the local result from other processes.

If a stop returns anything other than `True`, raises, times out or is cancelled,
or if the subsequent comparison/write fails or is uncertain, this process holds
that clone ID and does not automatically repeat the idle stop on later sweeps.
A stopped client may coexist with an active record, or a write may have committed
despite a failed acknowledgement. When the bounded hold set reaches capacity,
new uncertain IDs pause idle stops for the whole process. Logs identify held
clones without logging token values or exception text. There is no automatic
hold clearing or repair command. Operators must inspect registry, clients,
other workers and Telegram before making changes; do **not** blindly retry.

These are **in-memory, process-local** holds and claims: restart loses them, and
other processes, old versions, the watchdog and manual lifecycle operations do
not honor them. Restarting may permit a new sweep attempt. The check and stop
are not atomic, a client may be replaced between them, and a watchdog can restart
a client between the post-stop check and the database write. The conditional
write is not a distributed lifecycle lease or a transaction with Telegram.
Activity updates are throttled; cached plans, changes to plans, unrecorded
activity, ABA changes, backup restores and other writers can also race. A
successful stop/conditional write does not establish fleet-wide inactivity or
future availability. The warning path and Keep Running action retain their own
existing concurrency limitations. The registry scan is unbounded.

These changes do not copy the primary group plugin, modify the File Store worker
folder, add a second receiver, or perform a migration or production rollout.
Tests use fake clients and mock MongoDB, with BSON decoding checked separately;
real MongoDB concurrency, Telegram stop behavior and mixed-worker recovery are
not verified. A single authoritative, fenced shared controller and a reviewed
operator recovery process remain necessary.

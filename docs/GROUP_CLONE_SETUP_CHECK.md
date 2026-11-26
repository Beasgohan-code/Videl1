# Group clone setup inspection

In a private chat with the primary Videl bot, a clone owner can run:

`/clonegroupcheck BOT_ID GROUP_ID`

Use the clone's positive numeric bot ID and the group's negative numeric ID.
The caller must be the directly identified human clone owner **and** a currently
verified administrator or owner of that group. The selected clone must have the
Group Management profile, a non-deleted registry record, and a client known to
this process. The handler reads the clone client's identity, group type and the
caller and clone's reported restrict, delete, pin and change-info rights. An
unknown flag is not presented as granted. Basic groups receive a moderation
limitation. No group name, username, request text, credential, exception or
other arbitrary database field is displayed; no group setting or moderation
action is changed.

This is a controller-side diagnostic implemented independently of the existing
primary `group_mod` plugin. It neither copies that plugin nor installs another
receiver or changes the File Store worker folder. The existing Group Management
profile and its current shared engine remain in the legacy worker folder; this
diagnostic does not complete the separate-worker/shared-controller migration.

The handler uses the existing local client and performs bounded reads, rate and
concurrency checks, plus owner/profile/credential/client and administrator
rechecks before reporting. These checks are process-local and not an atomic
lease: rights, ownership, credential and client can change during or after a
check, and mixed workers or ABA changes are not fenced. A cached `get_me`, bot
privacy, group update delivery, actor/target status at action time and moderation
execution are not verified. Absence of a local client is not proof no other
worker exists. Errors return a generic message; the handler does not automatically retry,
although the underlying Telegram transport may retry.
The `/gcheck` command inside the group remains available as a separate check.

Tests use fake Telegram and mock MongoDB plus a BSON round trip; no actual group,
Telegram delivery, MongoDB concurrency or multi-worker deployment was verified.

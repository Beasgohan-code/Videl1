# Experimental clone profiles

Clone owners can choose one profile in their private bot dashboard. Legacy records
without `clone_profile` use File Store. A profile switch changes only the registry
field; it does not delete File Store files, links, settings, administrators, or
other clone data. Stored links are not served while Group Management is active.
Stop the clone, select the profile, and start it again. Music is displayed as
unavailable: there is **no voice-chat playback runtime** in this implementation.

Group Management currently provides `/ghelp`, `/groupid`, `/gban` and `/gunban`.
A group admin with Telegram's restrict-members permission and a bot with the same
permission are required for ban/unban. Commands accept a replied-to user or a
numeric user ID. Anonymous/sender-chat actors, admins, owners, unknown target
statuses and unverifiable permissions are rejected. Telegram can change rights
between checks and the actual action; Telegram is the final authority. A failed
API call may have taken effect, so check member status before retrying. There are
no shared group settings, caches, warnings or automatic moderation in this profile.
Some previously installed per-chat Telegram command menus may remain visible even
though File Store handlers are not active.

## Deployment and limitations

`CLONE_PROFILES_SINGLE_INSTANCE` defaults to `False`. Enable it **only** after
an operator has verified that exactly one up-to-date controller/worker process
can run for these clones, with no old deployments, other controllers or external
writers. This is an explicit operational assumption, not an automatically
verified distributed lock. Do not enable during rolling/overlapping deployments.
The process-local per-bot lock coordinates worker start/stop and selection only
inside that process; existing transfer, delete and active-state writers are not
all serialized with it. Conditional profile updates check current owner, deletion,
active state and previous profile, but do not eliminate ABA, ambiguous database
results or every lifecycle race. If the condition cannot be guaranteed, leave
this feature disabled. A group-profile record fails closed on startup when the
flag is disabled; a legacy File Store clone still starts normally.

A failed or timed-out client stop is treated as uncertain within the current
process, preventing further profile changes/startup until the stop can be
confirmed. This memory is lost on process exit; operators must independently
ensure no old worker remains before enabling or restarting. If a switch result is
ambiguous, reopen the panel and inspect the saved mode before retrying. Actual
Telegram command menus, moderation, failure recovery and multi-process behavior
have not been validated against live Telegram or a real MongoDB replica set.
The automated tests use simulated Telegram and MongoDB and do not prove those
operational assumptions.

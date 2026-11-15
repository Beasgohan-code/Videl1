# Group moderation: automation controls

These controls operate only on Telegram updates the bot receives, with the permissions Telegram grants at the time of the action. They are not a guarantee that a group is protected or that every request is processed.

## Join requests

- `/joinmode` shows the mode. `/joinmode off` disables this automation; `/joinmode notify` posts review buttons. The caller needs invite-users permission. Enabling notify requires the bot's invite-users permission.
- `/joinmode auto confirm` explicitly enables automatic approval of **every delivered join request** without admin review. The bot and caller need invite-users permission. `/joinmode auto` without `confirm` does not enable it.
- The dashboard can toggle notification mode or switch auto off, but cannot turn on auto approval with a single click. Automatic approval cannot be newly enabled while an active or unconfirmed raid setting remains; turn the shield off first.
- **Upgrade notice:** an older saved `auto` setting without the new confirmation is treated as off. Admins must review and explicitly reconfirm it. An old unconfirmed active raid setting also prevents automatic approvals while its configured interval remains active.

## Emergency join shield

- `/antiraid` shows the state and describes the action. `/antiraid 30 confirm` activates it for 30 minutes (valid durations are 5–1,440 minutes). `/antiraid off` disables it. Activation requires the caller and bot to have both restrict-members and invite-users rights; disabling does not require the bot to retain those rights.
- Turn off automatic join approval or switch to notification mode before activating the shield; this prevents unexpected approval resuming when the shield expires.
- This is a **blanket emergency measure**, not suspicious-join detection: while active, it attempts to decline every delivered join request and temporarily ban eligible new human members for 15 minutes. It only attempts the ban after a live bot-permission check and a live check that the target is currently a non-admin member. A member update with an unknown previous status is not sufficient for the ban.
- **Upgrade notice:** an older active raid setting without explicit confirmation is treated as inactive. Review the policy and reconfirm it if appropriate. The dashboard shows an unconfirmed previous setting as inactive and can turn it off, but activation requires the command above.
- After permissions are revoked or cannot be verified, automation skips the action rather than assuming permission. Failures and uncertain outcomes are logged with per-process rate limiting; they are not a durable incident record and do not automatically notify group administrators. A failed or timed-out Telegram action might still have completed; check Telegram before retrying.

## Anti-flood

The dashboard checks the bot's restrict-members permission before turning anti-flood on. The passive guard checks the bot's current permission again before a mute and does not claim a mute succeeded if Telegram returns an unconfirmed result. The existing flood tracker is process-local and uses a fixed threshold; it is not a distributed rate limiter.

## Operational limits

Group configuration remains keyed by chat ID and is cached locally for up to 45 seconds. Workers can temporarily disagree after changes; in-flight Telegram actions may complete after a setting is turned off. Do not mix old and new workers: old workers do not honor the new confirmation flags and can still take actions based on earlier settings. Review all groups' modes after upgrade. There is no atomic transaction across MongoDB and Telegram, and privileges or target status can change after a check. Tests use fake Telegram clients and mocked MongoDB; live Telegram, real multi-worker behavior and operational alerts remain unverified.

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

## Rose-style restrictions

- `/ban`, `/tban <30m|2h|7d>`, `/dban` (delete+ban), `/sban` (silent), `/unban` — ban family. `/dban` and `/sban` require a replied message.
- `/tmute <30m|2h|7d>`, `/dmute`, `/smute` — mute family with the same delete/silent variants.
- `/dkick`, `/skick` — kick with delete/silent variants.
- `/swarn`, `/dwarn` — silent/delete warning variants. `/rmwarn` removes one warning; `/resetwarn` clears all.
- `/warnings`, `/setwarnlimit <1–20>`, `/setwarnmode <kick|ban|tban TIME|mute|tmute TIME>` — warning policy. `/warnconfig` accepts the same modes. Plain `mute` keeps the legacy 6-hour duration.
- All restriction commands reuse live caller/bot/target permission verification and admin-target immunity. Unconfirmed Telegram results are reported as failures, not successes.

## Locks

- 40+ granular types: text, photo, video, document, audio, voice, videonote, sticker, gif, poll, contact, location, game, dice, checklist, album, command, url, invitelink, botlink, email, phone, emoji, forwarduser/forwardbot/forwardchannel, inline, externalreply, spoiler, plus legacy links/forwards/media/bots/all.
- `/lock <types>` and `/unlock <types>` manage locks. `/locks list` shows all types with state; `/locktypes` lists available types.
- `/lockwarns on|off` toggles a warning message when locked content is deleted.
- Custom per-lock modes: `/lock url ### spam {tmute 1h}` applies the mode when the lock triggers.

## Blocklists

- `/addblocklist <word|"phrase"|(a, b)> <reason>` adds triggers. `/rmblocklist` removes them. `/blocklist` lists triggers.
- `/blocklistmode <nothing|warn|kick|ban|tban TIME|mute|tmute TIME>` sets enforcement. `/blocklistdelete on|off` toggles message deletion.
- Locks take precedence over blocklists in the passive guard.

## Filters and notes

- `/filter <word|"phrase"|(a, b)|prefix:text|exact:text> <reply>` adds keyword filters. `/stop` removes them. `/filters` lists them.
- `/save <trigger> <text>` saves a note. `/get <trigger>` or `#<trigger>` opens it. `/notes` lists notes. `/clear <trigger>` deletes one.

## Anti-flood

- `/setflood <2–50>` sets the message limit. `/setfloodtimer <2–50> <seconds>` sets limit and window. `/setfloodmode <kick|ban|tban TIME|mute|tmute TIME>` sets enforcement. `/flood` shows settings. `/clearflood on|off` toggles deleting all flood messages.
- The flood tracker is process-local and per-chat; it is not a distributed rate limiter.

## CAPTCHA

- `/captcha on|off` toggles the button CAPTCHA (requires welcomes enabled). `/captchamode button` selects the mode. `/setcaptchatext <text>` sets the button label.
- New members are muted until they press the verify button. Only the new member can verify; admins cannot verify on their behalf.

## Moderation log

- Every confirmed moderation action is recorded to a bounded `group_modlog` collection (newest 1000 per chat): bans, mutes, kicks, warns, unbans, unmutes and Rose-style aliases, with actor, target, reason and UTC timestamp.
- `/modlog` lists recent group actions. `/modhistory <user>` lists actions against a specific user. Both are admin-only.
- Logging is best-effort and never raises into the moderation handler. Entries are not encrypted and persist until trimmed by the per-chat cap; this is an operational aid, not an immutable audit ledger.

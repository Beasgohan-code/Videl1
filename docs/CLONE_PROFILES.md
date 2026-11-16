# Experimental clone profiles

Clone owners can choose one profile in their private bot dashboard. Legacy records
without `clone_profile` use File Store. A profile switch changes only the registry
field; it does not delete File Store files, links, settings, administrators, or
other clone data. Stored links are not served while Group Management is active.
Stop the clone, select the profile, and start it again. Music is displayed as
unavailable: there is **no voice-chat playback runtime** in this implementation.

Group Management is an independent clone worker, not a registration of the
main bot's group plugin. It currently implements a subset inspired by the public
[Miss Rose restrictions guide](https://missrose.org/docs/moderation/restrictions/),
[pins guide](https://missrose.org/docs/moderation/pins/),
[delete guide](https://missrose.org/docs/moderation/purges/),
[rules guide](https://missrose.org/docs/basics/rules/) and
[welcomes guide](https://missrose.org/docs/greetings/):

- `/ban`, `/tban`, `/dban`, `/sban`, `/unban` (plus legacy `/gban`, `/gunban`);
- `/mute`, `/tmute`, `/dmute`, `/smute`, `/unmute` and `/kick`, `/dkick`, `/skick`;
- `/del`, `/pin`, `/pin loud`, `/unpin`, `/groupid` and `/help` (`/ghelp`);
- `/rules`, `/setrules`, `/resetrules` and `/welcome`, `/setwelcome`, `/resetwelcome`.

These commands also accept a dot prefix (for example `.ban`). Actions target a
replied-to user, numeric user ID, or resolvable `@username`. Reply-based `d` and
`s` commands delete the replied message *after* the moderation action is
confirmed. Silent commands also attempt to delete the command and do not post a
success message; if any deletion is uncertain a warning is sent separately when
possible. Timed bans/mutes accept `m`, `h`, `d` and `w` units between **5 minutes
and 365 days**. Very long API delays or an incorrect system clock can still affect
Telegram's interpretation of a timed restriction; verify the result in Telegram.
Reasons are shown in non-silent responses, but are not stored or sent to a log
channel. This is not full Rose compatibility: warnings, filters, locks,
antiflood, federations, logging, purges and other Rose features are not implemented here.

Bans, mutes, kicks and their reversals require a **supergroup**, with the actor and
bot both having Telegram restrict-members permission; delete variants also require
delete-messages permission. `/del` requires delete-messages permission and
pin/unpin require pin-messages permission for both actor and bot. Anonymous or
sender-chat actors, admins, owners, unknown target statuses and unverifiable
permissions are rejected. Username resolution can fail when Telegram cannot
resolve a user. Bot privacy and permissions still affect which updates arrive.
Rights can change between checks and the actual action: Telegram is the final
authority. A failed API call may have taken effect, so check before retrying.
Kicking is a two-step ban/unban; if the second step is uncertain, the user may
remain banned. Muting overwrites any existing per-member restrictions; unmuting
replaces them with verified current group defaults, not their previous custom
permissions. No main-bot group settings, warnings or automatic moderation are used.
Some previously installed per-chat Telegram command menus may remain visible even
though File Store handlers are not active.

### Rules and welcomes

Rules and text-only welcome settings are stored in the `clone_group_info`
collection keyed by the **clone bot ID and group chat ID**; there is no shared
chat-only settings cache. They are separate from File Store data and survive
switching profiles. `/rules` shows saved plain-text rules **in the group**, not in
a private chat. `/setrules TEXT` sets them and `/resetrules` explicitly removes
them. Configuration requires a directly identified group admin with Telegram's
change-info permission; members can read rules. Only slash and dot-prefixed
commands addressed to this clone are handled. Simultaneous changes to the same
field are last-write-wins; failed or unacknowledged writes are reported as
uncertain, never retried automatically.

Welcomes start **off**. `/setwelcome TEXT` saves plain text without enabling it;
`/welcome on` or `off` changes the toggle without deleting the text. `/welcome`
shows an admin a preview, and `/resetwelcome` explicitly clears the custom text
but leaves the toggle unchanged. The default welcome is used after reset.
Templates support only `{first}`, `{id}` and `{chatname}`; names and text are
HTML-escaped, with no Rose formatting, buttons, mentions, media or Markdown.
Text is limited to 1,000 characters, and templates with potentially excessive
placeholder expansion are rejected. At most the first 10 entries in a join event
are considered; bots are skipped and names/titles are limited to 128 characters.
Welcome delivery depends on Telegram sending
join updates and accepting messages. There is no durable event deduplication or
automatic retry, and a lost response may mean a welcome was sent anyway.

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

# Premium redeem-code access

On the main bot, `/gencode <days> [count] [uses]`, `/codes` and
`/delcode CODE` are bot-admin commands. The configured `OWNER_ID`/`OWNERS`,
`SUDO_USERS` and `ADMINS` IDs are combined into the bot admin list. Owners may
also add bot-wide admins through `/add_admin`; this grants much broader bot
powers, not merely code creation. Workspace members, group administrators and
ordinary users do not gain code-generation authority just by holding those
roles. The `/gencode` handler still has its admin filter and additionally checks
the sender and direct private-chat context inside the handler.

Admin code creation, listing and deletion now require a direct chat with the
bot; a group request does not create, reveal or delete a code. A user who has a
valid code can use `/redeem CODE` in a private chat. Codes are bearer secrets:
anyone who sees an unused code can attempt to redeem it, and direct messages can
be forwarded. Don't post codes in groups, screenshots or logs. If a code was
already posted in a group, the message itself may expose it even when the bot
refuses the command; review and revoke it privately if appropriate. This change
does not delete messages, revoke existing codes, or remove historic log entries.

The successful redeem event no longer includes the code, and first-user logs
redact `/redeem` and `/delcode` arguments. Other logging paths, Telegram history
and the storage of existing codes have not been independently audited. Existing
code records are still plaintext and the global code/user database is not
workspace- or clone-scoped. Bot admins can view up to 30 active codes per
private `/codes [page]` request, through page 1000. Pages use an offset over
creation time and ID; concurrent creation, use or deletion can shift results,
so paging is not a snapshot and absence from the list does not prove deletion.
Redemption's use claim and premium grant are separate writes; failures can
consume a use without granting premium. Legacy or mixed-version workers,
concurrent entitlement grants, online guessing/rate limits and live Telegram
and Mongo behavior remain unverified. Do not enable public code creation without
an explicit permissions, quota and abuse-control design.

## Uncertain outcomes

A failed `/gencode` insertion can leave a partial batch, and Telegram delivery
can succeed even if its acknowledgement is lost. The bot warns privately to
check `/codes [page]` before retrying; if messaging itself is unavailable, the
warning may not arrive. It does not automatically retry or delete codes. A
fully acknowledged batch is logged without its codes before delivery, but the
audit is best-effort and partial or ambiguous batches may not be recorded.
Similarly, a `/delcode` failure is reported as uncertain; exhausted codes are
not shown by `/codes`, so absence is not proof of deletion.

If `/redeem` cannot confirm its claim or Premium grant, the user is asked to
check `/myplan` and contact a bot admin before retrying. The code may have been
consumed without granting Premium, or Premium may already have been granted.
This is error guidance, not atomic recovery: no transaction, compensation,
automatic refund or entitlement repair is implemented. Tests use mock Mongo
and fake Telegram only; real concurrency, delivery and database behavior still
require validation. No existing records or keys are migrated or removed.

## Claim safeguards

A redemption claim now matches the `_id`, code value and duration read before
claiming, in addition to remaining uses and the user not having redeemed it.
A deleted-and-recreated record with a different `_id`, or a duration changed
before the claim, will not be consumed using the old duration. The caller gets
a noncommittal message if the conditional claim fails. This is not an ABA
protection: recreating a record with the same ID and values, later edits, other
writers, or changes after the claim can still race. Duplicate code values are
not prevented or repaired by this change.

New durations must be integer days between zero (lifetime) and the number of
days from today to Python's maximum date; invalid legacy durations are refused
before consuming a use. No stored record is changed. This bound only rules out
expiry dates impossible even from today. A user's existing future expiry can
still cause overflow, and an elapsed day can make an extreme legacy duration
invalid. As before, a claim and grant are separate operations; there is no
transaction or automatic retry. Verify mixed workers and actual Mongo races
before relying on these safeguards in production.

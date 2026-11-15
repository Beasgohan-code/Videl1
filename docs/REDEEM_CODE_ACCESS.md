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
code records are still plaintext, `/codes` returns up to 30 active codes to a
bot admin, and the global code/user database is not workspace- or clone-scoped.
Redemption's use claim and premium grant are separate writes; failures can
consume a use without granting premium. Legacy or mixed-version workers,
concurrent entitlement grants, online guessing/rate limits and live Telegram
and Mongo behavior remain unverified. Do not enable public code creation without
an explicit permissions, quota and abuse-control design.

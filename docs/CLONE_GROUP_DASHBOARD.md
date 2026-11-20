# Group Management clone dashboard

An owned Group Management clone now opens a dedicated private dashboard from
My Bots instead of displaying File Store settings that the group profile does
not serve. It shows the saved active marker, whether this process has a local
client, the local single-instance gate, and concise group setup instructions.
`/gcheck` and `/help` are commands to run in the group; the dashboard does not
check a group's Telegram permissions or start the clone. Local client presence
is not a health check or proof that another process has no client.

When a validated cached username exists, **Add to group** uses Telegram's
`https://t.me/<username>?startgroup` link. Telegram displays its group picker;
no admin rights or start parameter are preselected. **Open bot** goes to its
private chat. Both links depend on the cached username, which may change or be
reassigned: owners should verify the bot's identity and numeric ID before adding
it or sharing sensitive information. The dashboard also links to the existing
profile, transfer, delete and applicable local start/stop controls. If the local
client observation is unavailable, start/stop is not shown; if no client is
found and the single-instance gate is disabled, Start is not shown. This is a
presentation choice, not authorization or a distributed lifecycle fence.

Unsupported/Music and malformed profiles receive a limited recovery-oriented
view rather than File Store controls or an implied music runtime. Missing
profiles retain the legacy File Store dashboard. Existing File Store records,
settings and links are not removed; they are not served while the clone is in
Group Management mode. Opening the dashboard now requires a directly identified
human owner in their own private chat and a non-deleted matching record.

Dashboard data can become stale after it is read. Old messages, independently
reachable callback handlers, other workers and old versions are not prevented by
hiding buttons here; each existing callback still has its own authorization and
lifecycle limitations. This change does not isolate or move the group worker,
change the creation flow, introduce a music player or deploy anything. Mock
MongoDB and fake Telegram tests do not prove live link handling, group rights,
operation across workers, or real client availability.

Telegram deep-link reference: <https://core.telegram.org/api/links> (group bot
links, optional `startgroup` parameter and admin rights).

Registry identity checks in this dashboard, idle-notice reactivation and owner
`/check`, `/bots` and `/clonestats` accept PyMongo's decoded `bson.int64.Int64`
as well as Python integers. Large Telegram IDs can be encoded as BSON int64;
booleans and non-integer or out-of-range values remain invalid. This focused
fix does not audit every ID validator elsewhere in the project.

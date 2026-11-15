# Reviewed workspace bot record changes

The Next bot directory uses a distinct `POST .../bots/{bot_id}/lifecycle`
route for archive, unarchive and soft-delete. The client sends the timestamp and
archived flag it reviewed, and preserves those values when delete confirmation
opens. The server requires workspace `bot_settings` and CSRF, performs a scoped
conditional Mongo update, and reports conflicts without returning credentials.
Delete also compares the server-observed workspace `token_sealed` field in the
atomic write. No conditional route on an older backend means no fallback write.
Records without a usable timestamp or Boolean archived state cannot be changed
through this path. A refresh and fresh review are required after a conflict.

A soft-delete clears `token_sealed`, `token_masked`, `has_credentials` and
`connected` on the workspace record. It is not a data purge, credential
revocation, Telegram bot deletion or worker stop; related data, legacy clone
records and other stored credential fields may remain. An audit write occurs
after deletion, so an error response can mean the change already happened.

`updated_ts` is not a monotonic version shared by all writers. Changes from
other paths that omit it, ABA/recreated records, permissions revoked after the
authorization check, mixed deployments and legacy unconditional archive/delete
and fleet/config writers can bypass these guards. The token comparison only
covers changes between the server read and the atomic delete. It does not
establish that a token was unchanged since the user last saw the record.
Other state and related data are not part of the precondition. Database clocks,
real Mongo collation/concurrency and multi-worker behavior require validation;
mock Mongo and synthetic browser tests do not establish production safety.

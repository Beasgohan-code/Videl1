# Workspace bot token sealing

Workspace bot registration and workspace token replacement require a valid
`ENCRYPTION_KEY` before Telegram verification. They now capture the configured
Fernet cipher before that asynchronous request and refuse to write if a
process-observed key change or an encryption failure occurs. Errors omit token
and internal exception details. Successful writes use the existing `enc:`
format and remain readable by the existing decryptor. Tokenless drafts are
unchanged.

This is a narrowly scoped safeguard, not a key management or rotation system.
An operator must preserve the original key for existing encrypted credentials;
changing it does not migrate them and can make them unreadable. Detection is
process-local, cannot coordinate other processes or guarantee safety if keys
change after the check, and cannot detect unobserved ABA key changes. Legacy
File Store callers and other credential paths still have their existing
behavior, including potential plaintext fallback; no data is migrated or
removed. Bot writes still have separate concurrency limitations. Verification
uses synthetic Telegram responses and mock Mongo, not live services.

# Telegram Studio sign-in — branch implementation, not deployed

`/web` and `/studio` in a private bot chat now offer **Open Studio in Telegram**.
The button opens `/app` without an embedded login key. Telegram's Mini App SDK
supplies raw signed `initData` to the existing same-origin login API. The server
verifies the bot-token signature, timestamp and user identity, then provisions
or loads that user's workspace. Users do not need to be platform admins. A
one-time browser link/key is retained as a separate fallback; a normal browser
without Telegram launch data is not automatically identified.

The served Studio handles a late-loading SDK, preserves Telegram's launch
fragment until the SDK can read it, never trusts `initDataUnsafe`, and gives an
error instead of silently ignoring a failed login. Existing one-time fragment
links continue to work. A browser Telegram Login Widget/OIDC flow like Videl0's
is not implemented here; it needs separate server verification, domain/BotFather
configuration, replay protection and real-service testing. The Next preview also attempts automatic Mini App login and keeps a one-time-key
fallback. Both interfaces remember the signed-out launch's `auth_date` as a
local UX hint, so reloading that same launch does not silently sign the user
back in. A launch with a different timestamp may sign in automatically again;
if the timestamp is unavailable or unchanged (including two launches in the
same second), the user can use **Continue with Telegram** or request a fresh
launch. The hint is never used to authenticate: the backend verifies all proofs.
If a proof was already consumed, an explicit retry requires a fresh launch.
Classic `/admin` uses a separate
existing auth path and was not migrated.

The SaaS login now consumes a digest of the verified signed hash in MongoDB using
an atomic unique `_id`, majority-acknowledged insert and TTL index. No raw launch data is stored. A repeated
proof is rejected, and storage/index failures fail closed. Consumption precedes
workspace/session creation; if provisioning or response delivery fails, the
user must reopen the Mini App for a fresh launch or use the fallback link. TTL
cleanup is asynchronous. A valid pre-existing session can be reused on page
reload; session/link storage itself remains process-local and mixed workers can
still cause sign-in failures. Older workers do not enforce this replay guard;
update **all** workers together before relying on it. The separate classic admin
Mini App path still has its pre-existing replay limitation. Do not log, export,
or expose raw launch data or token-bearing URLs.

Verification so far uses signed synthetic data, mock MongoDB, JSDOM and a local
Chromium harness with an intercepted SDK—not real Telegram or real MongoDB.
Before rollout, validate MongoDB permissions/TTL and atomic claims across real
workers, current session behavior, Telegram desktop/mobile launch and sign-out,
proxy origins/frame policy and rollback in an authorized disposable environment.
The production Vercel project settings and live Render workers are unchanged.

Local validation (synthetic, not production):

- Clean branch snapshot: 838 backend tests passed, 33 skipped (mock Mongo).
- Full legacy JSDOM suite and 11 synthetic Next Chromium suites passed.
- Signed synthetic Mini App data against the local aiohttp backend in Chromium
  verified initial login, session reload, sign-out, replay rejection and a new
  launch in the same tab; the Next fixture
  uses a fake SDK/API and verifies its own login, failure and sign-out flows.
- Astro/Next typecheck and build passed. Real MongoDB majority-write, TTL and
  multi-worker behavior and real Telegram client launch are still unverified.
  Existing sessions are reused on reload without re-checking the Telegram account;
  account switching in a reused tab needs real-client verification.

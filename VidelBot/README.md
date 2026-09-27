# ✨ Videl — all-in-one Telegram utility bot

Videl is **one** Telegram bot that combines:

| Module | What it does |
|---|---|
| 📥 **Content Saver** (`saver/`) | Save posts/media from public **and** restricted channels (via `/login`), single links or ranges, custom caption / thumbnail, word delete/replace filters, auto-forward to a dump chat, free & premium plans |
| 🎬 **Video Encoder** (`VideoEncoder/`) | x264 / x265 encoding with per-user settings (CRF, preset, resolution, audio codec, watermark, hard-subs…), queue, direct-link & batch encodes, Drive upload |
| 🤖 **Clone Bots** (`filestore/`) | Users create their **own FileStore bot** from inside Videl. Each clone runs as a worker: permanent share links, `/genlink`, `/batch`, `/custom_batch`, `/flink` (quality-grouped links), multi force-sub, join-requests, auto-delete, shorteners + verification, custom start text/pic/caption, backups, transfer, maintenance. Idle clones hibernate automatically. |
| ⭐ **Stars Premium** (`core/payments.py`) | Users buy Premium in-app with **Telegram Stars** – one-time plans, **monthly auto-renewing subscriptions** (`/mysub` cancel/resume), **gift Premium to a friend** (`/gift`, native user picker), instant activation, receipts, `/stars` (live Stars balance + revenue) and `/refund` |
| 🤝 **Growth** (`core/growth.py`) | `/refer` invite links (every `REFERRAL_TARGET` new users → `REFERRAL_REWARD_DAYS` Premium), `/redeem` codes (`/gencode`, `/codes`, `/delcode`), one-time `/trial` |
| 💬 **Support inbox** (`core/support.py`) | `/support` delivers text or media to the owners' DM – owners just **reply** to answer, users reply to follow up |
| 🔒 **Force Subscribe** (`core/fsub.py`) | Require joining one or more channels (normal or **join-request** mode), manage with `/add_fsub`, `/del_fsub`, `/fsub_list` |
| 🧰 **Tools** (`core/`) | `/mediainfo`, `/rename`, `/upload` (public link), `/short`, `/qr`, `/id`, `/info`, `/json`, `/ping`, **inline mode** (`@bot <url or text>` → short links + QR) |
| 👮 **Admin** (`core/`) | `/user <id\|@name>` (full profile + ban / premium buttons), `/msg`, `/export` (CSV), `/stats`, `/users`, `/broadcast [-pin]`, `/ban`, `/unban`, `/banned`, `/maintenance on\|off`, `/premium_users`, `/watchdog`, `/logtest`, `/report`, `/setcommands`, `/restart`, `/update`; clone owners' panel `/clonestats`, `/bots`, `/check [fix]`, `/sys` |
| 📝 **Owner log channel** (`core/botlog.py`) | Every event tagged in `LOG_CHANNEL` – who started the bot, who cloned which bot, logins, payments, bans, restarts… – boot/shutdown/clone reports also in the owners' DM, plus a daily activity report |
| 🐕 **Keep-alive + Watchdog** | Health server + self-ping for free hosts; automatic temp-file cleanup, low-disk rescue, stuck-flow expiry, clone-bot self-healing, hang detection & auto-restart |

Everything runs in a single process on a single bot token; clone bots are extra
Pyrogram clients started by the worker engine.

## 🛰 Pyrofork + aiogram (Bot API 10.3)
Videl uses **both** libraries, each for what it does best:

| | Pyrofork (MTProto, layer 220) | aiogram 3.31 (Bot API 10.3) |
|---|---|---|
| Role | receives **all updates**, runs handlers, clone workers, user sessions | **outgoing** Bot API client (`core/botapi.py`) |
| Used for | 4 GB up/downloads, restricted-content saver, encoder, raw API | 🎨 coloured buttons · 👻 ephemeral group replies · 📖 rich messages · ⚡ managed bots · ⭐ Stars subscriptions/ledger · 🎁 Premium & gifts · 🖼 profile photos · ✍️ drafts |

aiogram never polls (two consumers on one token would split the updates), message IDs are shared so either
library can edit what the other sent, and **every aiogram call falls back to MTProto** – if the Bot API is
unreachable (3 network errors → 60 s back-off) or `AIOGRAM_ENABLED=False`, Videl behaves exactly as before.
`/botapi` (owners) shows the bridge status, `getMe` capability flags, latency and a live button-colour demo.

* **Coloured buttons** (`COLORED_BUTTONS`) – every menu sent/edited through `core/ui.py` gets Bot API `style`s:
  🟩 buy / confirm / create, 🟥 delete / cancel / ban, 🟦 main actions.
* **Ephemeral replies** (`EPHEMERAL_REPLIES`) – `/help`, `/start`, `/id`, `/info`, `/guide` in groups are
  visible **only to the caller** (no chat spam); falls back to a normal reply.
* **Rich `/guide`** – `sendRichMessage` with headings, a plans table, collapsible sections and link buttons
  (classic HTML help as fallback). Also `t.me/<bot>?start=guide`.
* **⚡ One-tap clone bots** (`MANAGED_BOTS`) – turn on **Bot Management Mode** for Videl in @BotFather's Mini App.
  *Create Bot* then shows **⚡ One-tap create**: Telegram opens a pre-filled "new bot" sheet
  (`t.me/newbot/<Videl>/<random_username>`), the user taps *Create*, and Videl fetches the token itself
  (`getManagedBotToken`) – no BotFather chat, no copy-paste. The bot belongs to the user. Renamed the username?
  Send `@name` and press *Start* in the new bot to prove it's yours. Managed clones get **🔁 Rotate token**
  (`replaceManagedBotToken`) on their dashboard. Without Bot Management Mode the classic token flow is shown.
* **Stars** – subscription links via `createInvoiceLink(subscription_period)`, cancel/resume via
  `editUserStarSubscription`, `/stars` shows the live balance (`getMyStarBalance`) and Telegram's own ledger
  (`getStarTransactions`).
* **Owner gifts** – `/giftpremium <user> <3|6|12>` (Telegram Premium paid from the bot's Stars: 1000/1500/2500 ⭐),
  `/gifts` (catalogue) and `/sendgift <user> <gift_id> [text]`.
* **Profile photos** – `/setbotpic` and the clone dashboard use `setMyProfilePhoto` with the clone's own token, so a
  clone can get an avatar even while it is stopped.
* **Drafts** – streaming previews use `sendMessageDraft` first, raw MTProto second.
* `BOT_API_URL` – optional self-hosted Bot API server.

### Newer Telegram features used
* **Stars payments** (`XTR` invoices, pre-checkout validation, refunds)
* **Message effects** (🔥 on /start, 🎉 on successful payment) and **reactions** on /start
* **Large link-preview banners above the text** (`invert_media`) – the SRC-style picture start message that every menu can edit in place
* **Copy-text buttons** (receipt IDs), **expandable block quotes** in logs
* **Scoped bot commands** – owners get the admin menu, users the normal one; bot **description / about** set automatically
* **Join-request** force-subscribe, **inline mode**, global **error handler**
* **Streaming replies** (Bot API 9.5 `sendMessageDraft`) – /start and /help "type" themselves, slow commands (`/stats`, `/report`, `/check`, `/user`, `/stars`, `/export`) show Telegram's native **“Thinking…”** placeholder (`STREAM_REPLIES`)
* **Bot profile photo** (Bot API 9.4 `setMyProfilePhoto`) – owners: `/setbotpic` · `/delbotpic`; clone owners: dashboard → 📩 Start cfg → 🤖 Bot profile photo
* **Stars subscriptions** (30-day auto-renew, cancel / resume) and **Premium gifts**
* **Native pickers** (`request_chat` / `request_user` keyboard buttons) – 📢 *Select channel* when creating a clone / changing its log or force-sub channel, 👤 *Choose a friend* for gifts

## 🚀 Deploy

1. Copy `config.env.sample` → `config.env` and fill at least `BOT_TOKEN`, `API_ID`, `API_HASH`, `DB_URI`, `OWNER_ID`.
2. Generate an `ENCRYPTION_KEY` (clone-bot tokens are Fernet-encrypted in MongoDB):
   ```bash
   python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
   ```
3. Run:
   ```bash
   # Docker (recommended – includes ffmpeg)
   docker build -t videl . && docker run --env-file config.env -p 8080:8080 videl

   # or bare metal (needs ffmpeg + ffprobe in PATH)
   pip install -r requirements.txt && python3 run.py
   ```
4. Optional: in @BotFather enable **/setinline** (inline QR / short links) and, for Stars, nothing else is needed – Stars work out of the box.

**One-click configs** in the repository root: `render.yaml` (Render blueprint), `heroku.yml` + `app.json` (Heroku container stack). `Procfile` works on Koyeb / Railway / Heroku.

> Keep the **ENCRYPTION_KEY** safe – if you lose it, existing clone bots can't be decrypted and their owners must re-add them.

### Keep-alive (`keep_alive.py`)
Serves `GET /` and `GET /health` (JSON: uptime, running clones, encoder queue, last watchdog sweep) on `0.0.0.0:$PORT`.
On Render / Koyeb / Railway / Heroku the public URL is auto-detected and pinged every `KEEP_ALIVE_INTERVAL` seconds so free instances don't sleep. Set `KEEP_ALIVE_URL` manually elsewhere (leave empty on a VPS). The Docker image has a `HEALTHCHECK` on `/health`.

### Watchdog (`watchdog.py`)
Every `WATCHDOG_INTERVAL` seconds (`/watchdog` shows the report, `/watchdog run` sweeps now, `/watchdog clean` forces an aggressive sweep):

| Job | Details |
|---|---|
| 🧹 Temp cleanup | `downloads/`, encoder download/encode dirs and stray progress files older than `CLEANUP_AFTER_HOURS`; folders with a file written recently are never touched; encoder dirs are skipped while the queue is busy |
| 💽 Low disk | below `MIN_FREE_DISK_GB` → aggressive cleanup (30 min) + alert to `LOG_CHANNEL`/owner (max every 3 h) |
| ⏳ Stuck flows | `/login` and clone-setup conversations idle for `STATE_TIMEOUT_MIN` are dropped, the temp client disconnected and the user told |
| 🤖 Clone healing | disconnected clones are restarted; active clones that aren't running are started again (back-off after 3 failures, owner notified after 5) |
| 📡 Hang detection | Telegram checked every sweep; 3 failures in a row → clean process restart (`AUTO_RESTART_ON_HANG`) |
| 🧠 Memory | caches trimmed, `gc.collect()`, RSS reported |

## 📝 Owner log channel
Set `LOG_CHANNEL` (bot must be admin). Every message is tagged, so the channel is searchable:

| Tag | When | Owner DM* |
|---|---|---|
| `#BotStarted` | boot – host, versions, handlers, users, premium, clones, force-sub, restart reason | ✅ |
| `#BotStopped` | clean shutdown | ✅ |
| `#NewUser` | first contact – name, @username, ID, language, where they came from (`/start` payload), total users | |
| `#Start` | returning user pressed /start (max once per `START_LOG_COOLDOWN_MIN`) + deep link | |
| `#CloneCreated` | who cloned, clone bot name + @username + ID, DB channel, masked token, clone counts | ✅ |
| `#CloneDeleted` `#CloneStarted` `#CloneStopped` `#CloneRestarted` `#CloneTransferred` `#CloneRestored` `#CloneSettingsCopied` | clone dashboard actions | Deleted ✅ |
| `#CloneHibernated` `#CloneHealed` `#CloneFailed` | idle shutdown / watchdog repairs / repeated start failures | |
| `#LinkGenerated` | clone bots: /genlink /batch /custom_batch /flink (`LOG_LINKS`) | |
| `#Login` `#Logout` | saver account connected / removed – phone masked, sessions never logged (`LOG_LOGINS`) | |
| `#StarsPayment` `#Refund` `#PremiumAdded` `#PremiumRemoved` | premium changes (payment type: plan / 🔁 subscription + renewal no. / 🎁 gift → friend) | Stars ✅ |
| `#Subscription` `#Referral` `#Redeem` `#CodesCreated` `#Trial` | auto-renew cancel/resume, referral credit + rewards, code use / creation, trials | |
| `#Support` `#AdminAction` `#BotPhoto` | support messages (the owners also get them in DM to reply), `/user` panel actions, bot photo changes | |
| `#Ban` `#Unban` `#Broadcast` `#Maintenance` `#FsubAdded` `#FsubRemoved` | admin actions (with who did it) | |
| `#LowDisk` `#AutoRestart` `#Restart` `#Update` `#Error` | health | LowDisk/AutoRestart ✅ |
| `#DailyReport` | every day at `DAILY_REPORT_HOUR` (`LOG_TZ`): new users, new clones, Stars, totals, disk/RAM | |

\* configurable with `OWNER_DM_EVENTS`. Without a `LOG_CHANNEL` everything goes to the owners' DM.
Admins: `/logtest` (check the setup), `/report` (report now).

## 📜 Command menus
All commands are registered in Telegram automatically at every boot (`core/commands.py`):
users see the user menu (51 commands) in private chats, groups get a group menu, admins additionally see the admin
commands and owners the owner commands. `/setcommands` re-syncs without restarting (e.g. after a new admin started the bot).

## ⭐ Stars premium
`STARS_PLANS=30:100 90:250 0:500` → 30 days for 100 ⭐, 90 days for 250 ⭐, lifetime (`0`) for 500 ⭐. Buttons appear in **💎 Buy Premium**, `/premium`, `/plan` and `/buy`.
Buying while premium extends the current expiry. Owners: `/stars` (revenue + last payments with charge IDs), `/refund <user_id> <charge_id>`.
Manual UPI / QR payments (`UPI_ID`, `QR_CODE`, `/add_premium`) keep working alongside.

* **Subscription** – `SUBSCRIPTION_STARS=90` adds *🔁 ⭐90 / month · auto-renew*. Telegram charges the user every 30 days and
  each renewal extends Premium by 30 days automatically. Users manage it with `/mysub` (cancel / resume auto-renew).
* **Gifts** – `/gift` (or *🎁 Gift a friend*) opens Telegram's user picker; the buyer pays, the friend gets Premium and a notification.
* **Free Premium** – `/trial` (`TRIAL_DAYS`), `/redeem CODE` (admins create codes with `/gencode 30 5`), referral rewards.

## 🧪 Tests
```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```
The suite runs every module offline (in-memory MongoDB + fake Telegram client): plugin loading, payments, force-sub, ban/maintenance gates, all menu callbacks, watchdog cleanup / state expiry / clone healing / auto-restart, keep-alive endpoints, error handler and inline mode – plus `tests/test_phase4.py` for streaming drafts, bot photo, Stars subscriptions / gifts, native pickers, referrals, redeem codes, trial, the support inbox and the admin user panel, and `tests/test_phase5.py` for the aiogram bridge. Its `RecordingSession` captures every Bot API request exactly as aiogram would put it on the wire, validated against the Bot API 10.3 models (coloured buttons, ephemeral replies, rich messages, managed-bot pairing / ownership proof / token rotation, Stars, gifts, photos, drafts, MTProto fallbacks). Two further checks run over the whole codebase: every `callback_data` must reach exactly one handler, and every menu text must be valid Bot API HTML.

## 🗂 Layout

```
run.py            entry point: loads plugins, bot profile/commands, clones, hibernation, keep-alive, watchdog
keep_alive.py     health web server + self-ping
watchdog.py       auto-cleanup & self-healing
client.py         the single shared Pyrogram client
config.py         unified env-based configuration
core/             home menu & texts, settings hub, middleware, force-sub, Stars payments (plans / subscriptions / gifts),
                  inline, errors, admin, tools, botlog (owner log channel), commands (Telegram menus),
                  stream (live drafts), profile (bot photo), growth (referrals / codes / trial), support, userpanel,
                  botapi (aiogram bridge), extras (/guide, /botapi, Premium gifts)
saver/            restricted-content saver
VideoEncoder/     encoder (plugins + ffmpeg utils)
filestore/        clone-bot controller (main_bot/plugins, incl. managed_bots = one-tap clones) + worker engine (worker_bot/)
database/         saver database
tests/            offline test-suite
```

### Handler order
`-10` payments (never blocked) → `-4` user tracking (+ referral credit) → `-3` ban / maintenance → `-2` force-subscribe → `-1` reply routers (support inbox, gift picker) → `0` modules → `1–2` clone link generators.

## 📜 Licence

The bundled code keeps its original licences: the video encoder is AGPL-3.0 (`LICENSE-VideoEncoder-AGPL`) and the content saver is MIT (`LICENSE-save-restricted-content-bot`).
If you run a modified copy publicly you must offer its source to users – set
`SOURCE_URL` and the bot answers `/source` with it.

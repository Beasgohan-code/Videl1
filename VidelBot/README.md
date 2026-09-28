# ✨ Videl — all-in-one Telegram utility bot

Videl is **one** Telegram bot that combines:

| Module | What it does |
|---|---|
| 📥 **Content Saver** (`saver/`) | Save posts/media from public **and** restricted channels (via `/login`), single links or ranges, custom caption / thumbnail, word delete/replace filters, auto-forward to a dump chat, free & premium plans |
| 🎬 **Video Encoder** (`VideoEncoder/`) | x264 / x265 encoding with per-user settings (CRF, preset, resolution, audio codec, watermark, hard-subs…), queue, direct-link & batch encodes, Drive upload |
| 🤖 **Clone Bots** (`filestore/`) | Users create their **own FileStore bot** from inside Videl. Each clone runs as a worker: permanent share links, `/genlink`, `/batch`, `/custom_batch`, `/flink` (quality-grouped links), multi force-sub, join-requests, auto-delete, shorteners + verification, custom start text/pic/caption, backups, transfer, maintenance – plus **smart links** (expiring / first-N-users / password / sold for ⭐ Stars), **premium users** who skip the shortener, auto-index + **/search & inline search**, **analytics**, **scheduled broadcasts**, **requests inbox**, file buttons, anti-flood and **ownership transfer** ([details](#-clone-bot-extras)). Clones nobody uses for **7 days are deactivated** automatically (owner warned a day before, one-tap 🟢 Reactivate). |
| ✏️ **Auto-Rename** (`renamer/`) | Set a template once (`/autorename {title} S{season}E{episode} [{quality}]`) and every file you send comes back renamed – smart season / episode / quality / audio / year / codec extraction, classic `[SSeason] [EPEpisode]` keywords too, output as document / video / audio (`/setmedia`), optional MKV conversion, **metadata** (title, author, artist, audio / video / subtitle track titles, encoded-by, custom tag), custom thumbnail & caption, **sequence mode** (send a season in any order → sorted delivery), live progress with cancel, per-user queue, `/testrename` preview, `/leaderboard` (today / week / month / year / all-time), optional **shortener verification** for free users, anti-NSFW filter and a dump channel |
| ⭐ **Stars Premium** (`core/payments.py`) | Users buy Premium in-app with **Telegram Stars** – one-time plans, **monthly auto-renewing subscriptions** (`/mysub` cancel/resume), **gift Premium to a friend** (`/gift`, native user picker), instant activation, receipts, `/stars` (live Stars balance + revenue) and `/refund` |
| 🤝 **Growth** (`core/growth.py`) | `/refer` invite links (every `REFERRAL_TARGET` new users → `REFERRAL_REWARD_DAYS` Premium), `/redeem` codes (`/gencode`, `/codes`, `/delcode`), one-time `/trial` |
| 💬 **Support inbox** (`core/support.py`) | `/support` delivers text or media to the owners' DM – owners just **reply** to answer, users reply to follow up |
| 🔒 **Force Subscribe** (`core/fsub.py`) | Require joining one or more channels, manage with `/add_fsub`, `/del_fsub`, `/fsub_list`; `/fsub_mode` switches **join-request** mode per channel; leaving a channel re-locks the user |
| 🧰 **Tools** (`core/`) | `/mediainfo`, `/rename`, `/upload` (public link), `/short`, `/qr`, `/id`, `/info`, `/json`, `/ping`, **inline mode** (`@bot <url or text>` → short links + QR) |
| 👮 **Admin** (`core/`) | runtime admins (`/add_admin`, `/deladmin`, `/admins` – owners only, no redeploy), `/user <id\|@name>` (full profile + ban / premium buttons), `/msg`, `/export` (CSV), `/stats`, `/users`, `/broadcast [-pin]`, `/ban`, `/unban`, `/banned`, `/maintenance on\|off`, `/premium_users`, `/watchdog`, `/logtest`, `/report`, `/setcommands`, `/restart`, `/update`; clone owners' panel `/clonestats`, `/bots`, `/check [fix]`, `/sys` |
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
* **Disabled buttons** (Bot API 10.3 `DisabledButton`) – status chips such as *✅ Premium · active* on the Premium
  screen and *🔒 Limit reached* in My Bots are greyed out and not tappable. Any button with `callback_data`
  `noop` / `noop:<x>` is sent this way; the MTProto fallback answers it silently.
* **Streaming replies** (Bot API 9.5 `sendMessageDraft`) – /start shows a live draft while the home screen is built, slow commands (`/stats`, `/report`, `/check`, `/user`, `/stars`, `/export`) show Telegram's native **“Thinking…”** placeholder (`STREAM_REPLIES`)
* **Bot profile photo** (Bot API 9.4 `setMyProfilePhoto`) – owners: `/setbotpic` · `/delbotpic`; clone owners: dashboard → 📩 Start cfg → 🤖 Bot profile photo
* **Stars subscriptions** (30-day auto-renew, cancel / resume) and **Premium gifts**
* **Native pickers** (`request_chat` / `request_user` keyboard buttons) – 📢 *Select channel* when creating a clone / changing its log or force-sub channel, 👤 *Choose a friend* for gifts

## 🤖 Clone bot extras

Everything below works inside **every clone** (commands are typed in the clone, admins only unless marked 👤).
Switches also live in Videl → 🤖 My Bots → your bot → **✨ Extras**.

| Pack | Commands | What it does |
|---|---|---|
| 🔗 **Smart links** | `/smartlink LINK [24h] [x100] [pass=…] [stars=25] [note=…]` · `/links` | Wraps any `/genlink` / `/batch` link (`t.me/bot?start=sl_…`): expires after a time, only the first N users (re-opening is free), asks for a password (hashed, the typed password is deleted, 3 tries), or is **sold for Telegram Stars** – buyers can reopen forever and are never refused after paying. |
| 💎 **Premium** | `/setpremium 50 30` · `/addpremium ID [days]` · `/delpremium` · `/premiumusers` · 👤 `/plan` | Premium users skip shortener verification. Sell it for Stars (`/plan` → ⭐ Buy) or give it by hand (0 days = lifetime, extends instead of resetting). Invoices are checked on pre-checkout (price, currency, buyer). |
| 🗂 **Index & search** | `/index` · `/searchmode on` · 👤 `/search name` · inline `@clone name` · `/autolink dm\|edit\|post on` · `/setpostchannel` | New storage-channel files are indexed automatically (`/index` scans older posts). Multi-word search with pages; inline search needs **/setinline** for the clone in @BotFather. Auto-link can DM you the link, add a 📥 button to the stored post, or publish a card in another channel. |
| 📊 **Owner tools** | `/analytics` · `/broadcast [pin] [silent] [forward] [in 2h\|at 21:30]` · `/schedules` · `/export` (owner) | 14-day sparkline of opens / new users, top links, Stars earned, premium & search counts. Broadcasts show live progress with ⛔ Stop, remove users who blocked the bot and can be scheduled (`LOG_TZ`). Export = users CSV + settings JSON (no keys). |
| 🙋 **Users & UX** | 👤 `/request text` · `/requests` · `/setbuttons A - url \| B - url` · `/delbuttons` · `/sethelp` · `/setabout` · `/antiflood` · `/maintenance` · `/requestmode` · `/settings` | Requests (3/day, premium 10) land in the admins' DM with ✅ Done / ❌ Reject / 💬 Reply. Buttons appear under every delivered file. Custom `/help` & `/about` (placeholders `{mention}` `{first}` `{bot}`). Anti-flood: 8 msgs / 10 s → growing cool-down, optional auto-ban. |
| 👑 **Ownership** | Videl → My Bots → ✨ Extras → 👑 Transfer | Hand a clone (users, files, links, settings) to someone who has started Videl; confirmed, logged as `#CloneTransferred`, the clone restarts under its new owner. |

Clones get their own command menus: a short one for users and the full admin menu for the owner + admins.

## 🚀 Deploy

### 1 · Collect the variables
| Variable | Where to get it |
|---|---|
| `BOT_TOKEN` | @BotFather → /newbot |
| `API_ID`, `API_HASH` | https://my.telegram.org → API development tools |
| `DB_URI` | MongoDB Atlas (free M0 works) → Connect → Drivers |
| `OWNER_ID` | your numeric user id (send /id to the bot or @userinfobot) |
| `ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` – encrypts clone-bot tokens. **Keep it safe:** lose it and clone owners must re-add their bots |
| `LOG_CHANNEL` *(recommended)* | a channel id (`-100…`) where the bot is admin |

Everything else is optional; see `config.env.sample`. Inline `# comments` pasted along with a value are stripped automatically, and a malformed number falls back to its default with a warning instead of crashing.

### 2 · Pick a platform
All platforms build the **root `Dockerfile`** (Python 3.11 + ffmpeg/ffprobe, mediainfo, mkvtoolnix, 7z; tini as PID 1). The container serves `/health` on `$PORT` for health checks.

| Platform | How |
|---|---|
| **Railway** | New Project → Deploy from GitHub repo. `railway.toml` sets the Dockerfile builder, the `/health` check and restart-on-failure. Add the variables under *Variables*. |
| **Render** | Dashboard → New → **Blueprint** → pick the repo (`render.yaml`), or [one-click](https://render.com/deploy?repo=https://github.com/Beasgohan-code/Videl1). Fill the `sync: false` variables. |
| **Heroku** | `heroku create && heroku stack:set container && git push heroku HEAD:main` (`heroku.yml`), or [one-click](https://heroku.com/deploy?template=https://github.com/Beasgohan-code/Videl1) (`app.json`). |
| **Koyeb / Northflank / Fly.io** | Create a service from the GitHub repo with the **Dockerfile** builder, port `8080`, health check path `/health`. |
| **VPS with Docker** | `cp VidelBot/config.env.sample VidelBot/config.env` → edit → `docker compose up -d --build` (logs: `docker compose logs -f`). |
| **VPS without Docker** | `sudo apt install ffmpeg mediainfo mkvtoolnix p7zip-full` → `cd VidelBot && python3.11 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt` → create `config.env` → `python3 run.py` (systemd unit below). |
| **Any Dockerfile path / root dir** | `Dockerfile` and `VidelBot/Dockerfile` are the **same universal file**: it works with the repo root *or* `VidelBot/` as build context, so either Dockerfile path is fine. |

> ⚠️ Run **exactly one** instance per bot token (keep replicas at 1). Two copies would steal each other's updates.

<details><summary>systemd unit (VPS without Docker)</summary>

```ini
# /etc/systemd/system/videl.service  →  sudo systemctl enable --now videl
[Unit]
Description=Videl Telegram bot
After=network-online.target

[Service]
WorkingDirectory=/opt/Videl1/VidelBot
ExecStart=/opt/Videl1/VidelBot/.venv/bin/python run.py
Restart=on-failure
RestartSec=10
User=videl

[Install]
WantedBy=multi-user.target
```
</details>

### 3 · After the first start
* The owner gets a DM plus a `LOG_CHANNEL` post saying the bot is online. Commands are registered automatically.
* Optional: in @BotFather turn on **/setinline** (inline QR / short links), and enable **Bot Management Mode** for one-tap managed clone bots.

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
| 💤 Clone auto-off | hourly (`clone_lifecycle.py`): a clone with no messages / button taps for `CLONE_INACTIVE_DAYS` (default **7**, `0` = never) is stopped and marked deactivated – users, files, links and settings are kept. The owner is warned a day earlier (✅ *Keep it running*), gets 🟢 *Reactivate* when it happens, and the log channel gets `#CloneDeactivated`. Reactivating (or 🟢 Start in the dashboard) restarts the 7-day clock. |
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
| `#Ban` `#Unban` `#Broadcast` `#Maintenance` `#FsubAdded` `#FsubRemoved` `#AdminAdded` `#AdminRemoved` | admin actions (with who did it) | |
| `#RenameVerified` | a free user completed Auto-Rename shortener verification (+ time taken) | |
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

## ✏️ Auto-Rename

1. `/autorename` → set a template, e.g. `{title} S{season}E{episode} [{quality}] [{audio}]` (or the classic `[SSeason] [EPEpisode] [Quality]`), or tap a preset (🎌 Anime · 📺 Series · 🎬 Movie · 🧹 Keep name, strip tags). Keywords: `{title} {season} {episode} {quality} {source} {audio} {year} {codec} {group} {size} {filename}`.
2. Send / forward files – each is downloaded, renamed, optionally converted to MKV + tagged, and sent back with your caption & thumbnail (saver `/set_caption`, `/set_thumb`; caption keywords `{filename} {size} {duration} {title} {season} {episode} {quality} {audio} {year} {codec} {source} {group} {original}` – unknown `{words}` are left as typed).

**Smart names**
* **Movie-aware** – a file with no season *and* no episode drops the `S··E··` part (no fake "S01E01"); a season pack keeps `S02`; an episode without a season still gets `S01`.
* **Multi-episode files** – `S01E01-E03`, `S01E01-02`, `E01E02`, `Episode 1-3` → `{episode}` = `01-03`.
* Anime `Show - 12 [1080p]` beats numbers inside the title (`Kaiju No. 8 - 12` → episode 12), `2x05` is season 2 episode 5, resolutions like `1920x1080` are ignored.
* `{source}` = WEB-DL / WEBRip / BluRay / HDRip / HDTV …, `{group}` = leading `[SubsPlease]` or trailing scene tag (`…x264-RARBG`).

**Modes & clean-up** (all in the `/autorename` panel)
* 🤖 **Auto** – every file uses your template. ✍️ **Manual** – every file gets a prompt: reply with a name (extension kept automatically), or tap 💡 *Use suggestion* (your template's result) / ↩️ *Keep name* / ❌ *Skip*, with a one-off 📄 Doc · 🎥 Video · 🎵 Audio choice. With a single pending prompt plain text works too; links and commands are never captured. Up to 20 prompts wait at once and expire after 2 × `STATE_TIMEOUT_MIN`.
* 🧹 **Clean tags** – strips `@channels`, `t.me` / `http` links and site names (`www.1TamilMV.com`, `Site.net` …) from the *incoming* name – the text of your own template (e.g. your `@MyChannel`) is never touched.
* 🔁 **Word rules** – up to 30 lines: `HQ` removes, `[ESub] => ESubs` or `Tamil Dubbed | Tamil` replaces (case-insensitive, whole words for plain words). Applied after tag cleaning.
* 🕘 **History** – your last 10 renames (new ← original name). ⏹ **Cancel queue** appears while files are processing.
* `/rename New Name` (reply to a document / video / audio) now uses the same pipeline – queue, progress + cancel, MKV / metadata settings, thumbnail, caption placeholders, history, leaderboard and dump channel. It also works on a file the bot itself sent.

**Output**
* Videos without a custom or embedded thumbnail get a frame from the file (10 % in, ffmpeg).
* Duration / width / height are probed with ffprobe when Telegram didn't provide them, so files sent as video show the right size and length.

| Command | Does |
|---|---|
| `/autorename` | panel: template + presets, mode (auto / manual), pause, clean tags, word rules, history, MKV, output type, metadata, thumbnail, sequence, queue, help |
| `/setmedia` | document / video / audio / auto |
| `/metadata` · `/settitle` `/setauthor` `/setartist` `/setaudio` `/setsubtitle` `/setvideo` `/setencoded_by` `/setcustom_tag` | metadata (empty value = clear) |
| `/start_sequence` → files → `/end_sequence` | sorted delivery by season → episode → quality |
| `/testrename <name>` | preview what a file would become (shows the cleaned source name and everything detected) |
| `/rename <name>` (reply) | one-off rename through the same pipeline |
| `/leaderboard` (`/top`) | rankings; auto-deleted in groups after `LEADERBOARD_DELETE_TIMER` s |
| `/verify` | free users verify through a shortener (if enabled) – Premium & admins skip it |
| `/renameset` (admin) | global on/off, anti-NSFW, dump channel |
| `/verify_settings` (admin) | 2 shorteners, validity hours, bypass detection (< 1 min), daily counts |

MP4 → MKV / metadata use stream copy (no re-encode); if a file can't be remuxed it's sent with its original container and a note. Files are processed `RENAME_CONCURRENCY` at a time, each user may queue `RENAME_QUEUE_LIMIT` files, and `/cancel` stops everything.

## 🎨 FileStore-style UI + rich messages

User-facing screens share the look of the clone bots: `━━━` header bars with a 𝗕𝗢𝗟𝗗 title,
ꜱᴍᴀʟʟ-ᴄᴀᴘꜱ labels and `◈` rows, while values (file names, numbers, templates, links) stay in
normal letters so they remain readable and copyable. Admin, owner and log messages are unchanged.

- `core/style.py` – `sc()` small caps (keeps HTML tags, entities, `{placeholders}`, `/commands`,
  `@mentions`, links, `<code>` and ACRONYMS; escapes bare `& < >`), `hdr()`, `sec()`, `row()`, `quote()`.
- `core/rich.py` – `Doc` builds a screen once (`.h()`, `.table()`, `.items()`, `.details()`, `.footer()`)
  and renders it two ways:
  - **native rich message** (Bot API 10.1+ `sendRichMessage` / `editMessageText(rich_message=…)`): `<h1>`
    title, small-caps `<h3>` sections, bordered + striped `<table>`s, `<details>` and a `<footer>`;
  - **classic HTML** (FileStore look): 2-column tables become `◈ ʟᴀʙᴇʟ: value` rows in a quote, wider
    tables become `◈ a · b · c` rows, `<details>` becomes an expandable quote.

  The rich form is used in private chats of the main bot whenever the aiogram bridge is on. Because
  pyrofork can't read rich messages, a rich screen only carries buttons whose handlers edit through the
  Bot API (`rich.SAFE_CALLBACKS`); any other keyboard automatically gets the classic rendering. Groups
  get the classic form as an ephemeral reply, and if Telegram refuses to turn a rich screen back into a
  text one, the helpers send the new screen and delete the old message.

Rich screens: `/help`, `/about`, `/commands` (one table per section), `/stats`, `/status`, `/mediainfo`
(streams table), `/id`, `/info`, `/ping`, `/myplan`, `/premium` (Free vs Premium table + Stars prices),
`/mysub`, `/refer`, `/verify`, `/testrename`, `/leaderboard` and the 🧰 Tools help. Restyled
classic screens (photo / preview based): home, settings hub, encoder help, premium banner and the
Auto-Rename panel.

## 🎬 Encoder Pro

The video encoder got a rebuilt pipeline. Every ffmpeg command now comes from one tested builder (`VideoEncoder/utils/ffcmd.py`), and the test suite runs those commands through a real ffmpeg.

**Fixed**
| Before | Now |
|---|---|
| 10-bit used `-profile:v main` → every 10-bit encode failed | x264 `high` / `high10`, x265 `main` / `main10` · **H.264 10-bit allowed** |
| x265 got `-tune film` (doesn't exist) → failed | `film` only for x264, `animation` for both |
| `scale=1920:1080` stretched non-16:9 video and upscaled | `scale=-2:H` keeps the aspect ratio and never upscales |
| `-map 0:v?` encoded cover art as extra video streams | only the real video stream is mapped |
| MP4: no `hvc1` tag, PGS → `mov_text` crash, Vorbis/FLAC copy | `hvc1` + `+faststart`, text subs only, unsupported audio → AAC; AVI-safe audio |
| Opus at 44.1 kHz / 5.1(side) failed | 48 kHz + a layout libopus accepts |
| ❌ Cancel only flipped a flag – ffmpeg kept running | the ffmpeg process is terminated (downloads / uploads / URL downloads stop too) |
| 📊 Stats alert > 200 chars → never showed | short live summary |
| ~25 DB reads per encode, blocking probes | one settings read, one ffprobe, probes off the event loop |
| log-channel failure marked a finished upload as failed | log copy is best-effort |

**New**
- **⚡ Quick profiles:** 📱 Mobile · ⚖️ Balanced · 🎞 High quality · 🎌 Anime · 💾 Tiny · ⚡ Fast, set with one tap. The menu shows which one is active.
- **🎯 Target-size mode:** "make it ≈ 200 MB". Videl picks the bitrate from the duration and audio bitrate.
- **🧹 Filters:** deinterlace (bwdif, touches interlaced frames only), denoise (hqdn3d), EBU R128 loudness normalisation.
- **`/sample [sec]`:** encodes a 30 s clip from the middle with your settings and predicts the full file size.
- **`/trim 1:00 2:30`:** lossless, fast cut. **`/screens [n]`:** up to 10 evenly spread screenshots as an album.
- **Live progress card:** bar, %, speed, fps, ETA, current → projected size, the settings in use, and a per-task ❌ Cancel.
- **Summary card:** original → new size (−x %), time, speed, video / audio settings, a 📥 Open file button, and per-user totals ("12 encodes · saved 3.4 GB").
- **Queue:**
  - "position #N" replies;
  - `/queue` shows the whole queue on one screen;
  - `/vset` is a settings table;
  - `/clear` reports how many waiting tasks it removed.
- **Settings menus:**
  - a summary card on each page;
  - CRF ➖/➕ stepper (12–40);
  - labelled chips instead of the dead "this button not works" buttons;
  - x264-only options are hidden for H.265;
  - an 🧪 Advanced page.

## 🚀 Phase 15: engine, tools and owner suite

### Encoder engine
| Feature | How it works |
|---|---|
| ⚡ **GPU encoding** | At boot Videl test-encodes a few frames with NVENC, QSV and VAAPI. A GPU only counts if that test works, not just because ffmpeg lists the encoder. It's used automatically and falls back to the CPU for 10-bit H.264 and 2-pass. There's a per-user GPU toggle. `HW_ENCODER=auto\|nvenc\|qsv\|vaapi\|off`. |
| 💎 **AV1** (Encoder Pro) | Codec cycle H.264 → H.265 → AV1. Uses SVT-AV1 (presets 12…5), or libaom when that's the only one in the build. CRF is mapped to AV1's scale. |
| 🎯 **2-pass target size** (Encoder Pro) | Pass 1 analyses without writing output, pass 2 hits the size to within about 2 % (x264 / x265). |
| 🧵 **Parallel workers + priority** | `ENCODER_WORKERS` tasks run at once. Encoder Pro, Premium and admin tasks go ahead of waiting free tasks. Per-user caps are `ENC_MAX_TASKS_FREE` / `_PRO`. Every task gets its own folders, so parallel jobs never delete each other's files. |
| ♻️ **Queue survives restarts** | The queue is mirrored to Mongo. After a restart each task is re-queued in order (up to 48 h old) and the user is told the new position. |
| ©️ **Watermarks** | `/watermark Your text` adds a text watermark. Reply `/watermark` to a photo for a logo (Encoder Pro). Position, size and opacity are set in ⚙️ → Extras → 🎨 Style. |
| ✂️ **Auto-split over 2 GB** | Results and leeches over `SPLIT_SIZE_MB` are cut at keyframes into parts that each play on their own ("Part 1 of 3"). Files that aren't video are byte-split into `.001` `.002` … parts. |

### New commands
| Command | What it does |
|---|---|
| `/mux` | Reply to a video, then send a `.srt/.ass/.vtt` subtitle or an audio file. The track is added losslessly (MP4 subtitles become `mov_text`, audio MP4 can't hold becomes AAC). |
| `/merge` | Send 2–10 videos (Pro: 10, free: 3), then `/merge done`. Matching files are joined losslessly. Otherwise each part is matched to the first (scale/pad/fps, H.264/AAC, silent audio added where missing). |
| `/convert mp3\|m4a\|opus\|flac\|wav` | Extracts audio. `/convert gif [start] [sec]` makes a palette-optimised GIF (max 20 s). `/toaudio` and `/gif` are shortcuts. |
| `/leech <link> [\| name]` | Downloads Mega **file** links (decrypted on the fly), public Google Drive files, and direct links, then uploads to Telegram. Size limit is `LEECH_FREE_GB` / `LEECH_PRO_GB`, and files are split above 2 GB. **Private or local addresses are refused** at every redirect (SSRF guard). |
| `/lang` | Picks one of 8 languages for the home screen and menus: English, हिन्दी, മലയാളം, தமிழ், Español, Indonesia, Русский, العربية. It's detected from Telegram automatically, and there's a 🌐 button on the home screen. |
| `/analytics` (admins) | Two charts (users, jobs) plus a summary with period-over-period changes: active and new users, encodes, renames, saves, leeches, tools, clones, and Stars revenue. |
| `/backup` (owners) | Backs up all four databases right now. Reply `/backup` to a backup file to restore it (upsert only, never deletes). |

### Owner suite
- **Web dashboard:** set `ADMIN_WEB_TOKEN`, then open `https://<your-app>/admin#<token>`.
  - The token lives in the URL *fragment*, so it never reaches server logs. It's sent as a header, compared in constant time, and wrong guesses are rate-limited.
  - Without a token the routes return 404.
- **Daily backups:** a gzip JSON file sent to `BACKUP_CHAT` (default: the first owner) at `BACKUP_HOUR`.
  - Users' `/login` sessions are **stripped** unless `BACKUP_INCLUDE_SECRETS=true`.
  - Clone tokens stay in, since they're stored encrypted.
- **Analytics storage:** one counter document per day plus one row per active user per day (pruned after 120 days). New users and revenue are read from the existing collections.

### Honest limits
- **GPUs:** your host must give the container the GPU. For NVIDIA that means the NVIDIA Container Toolkit (`--gpus all`); for Intel/AMD VAAPI, `--device /dev/dri`. Free PaaS hosts (Render, Railway, Heroku) don't provide GPUs, so Videl uses the CPU there. VAAPI also needs a driver in the image (add `intel-media-va-driver` or `mesa-va-drivers` to the Dockerfile's apt line). Without one, the boot test fails and Videl uses the CPU.
- **AV1 is slow on a CPU:** a 1080p episode can take several times longer than H.265. That's why it's opt-in.
- **Mega:** single-file links only (not folders), and Mega's own free transfer quota still applies. **Google Drive:** public files only. Very large files that trigger Drive's virus-scan page may need a direct link.
- **Uploads:** the bot upload limit is 2 GB, so bigger results always arrive in parts.

## ⚡ Phase 16: faster encodes, no duplicates

| What was wrong | Fix |
|---|---|
| **Duplicate frames.** With FPS on "source", ffmpeg's MP4/MKV default is constant frame rate, so variable-frame-rate sources (phone clips, screen recordings, many web rips) were padded with copies of frames. A test clip went from 100 frames to 254 and took ~2× as long. | Frames keep their own timestamps (`-fps_mode vfr`, or `-vsync vfr` on ffmpeg 4.x, detected once at runtime). A forced FPS and AVI stay constant-rate. |
| Sources that already repeat frames (anime "on twos", slideshows) | New **Drop duplicate frames** toggle (/settings → Advanced, `mpdecimate`). Smaller files and fewer frames to encode. |
| **Duplicate tasks.** Tapping /dl twice, re-sending the same video or Telegram re-delivering an update after a reconnect meant the same thing was encoded twice. | The queue refuses a task with the same user + mode + file (`file_unique_id`) + arguments + options and says where the first one is. Re-delivered updates are dropped silently. |
| **Duplicate messages.** Queued tasks got an "Added to the queue" message, then a separate status message. Errors were a further reply. | The queue note becomes the live status card, and errors appear on that card. |
| ffmpeg could **freeze** mid-encode: stderr was only read after it finished, so a damaged input's warnings filled the 64 KB pipe. | stderr is drained while ffmpeg runs, keeping the last 16 KB for the error card. stdout goes to `/dev/null`. |
| The progress loop re-read the whole `-progress` file every 5 s (megabytes on long encodes) and noticed ffmpeg's exit up to 5 s late, twice for 2-pass. | Only the last 4 KB is read, and the loop wakes the moment ffmpeg exits. |
| **Slow Telegram downloads.** pyrofork fetches 1 MiB per round trip and opens a new connection (plus a key exchange for other DCs) for every file. | `core/fastdl.py` keeps `FAST_DL_WORKERS` (6) chunk requests in flight on one cached media session per DC and writes each chunk at its offset. Used by the encoder, Auto-Rename, /upload and /rename. Small files, photos, CDN redirects and expired references use the stock downloader, so the worst case is the old speed. `FAST_DL=False` turns it off. |
| Uploads ran their probes one after another, and auto thumbnails were full-size frames, which Telegram ignores (>320 px). | Probes and lookups run concurrently, and thumbnails are scaled to at most 320 px. |

## 🧪 Tests
```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```
The suite runs every module offline (in-memory MongoDB + fake Telegram client): plugin loading, payments, force-sub, ban/maintenance gates, all menu callbacks, watchdog cleanup / state expiry / clone healing / auto-restart, keep-alive endpoints, error handler and inline mode – plus `tests/test_phase4.py` for streaming drafts, bot photo, Stars subscriptions / gifts, native pickers, referrals, redeem codes, trial, the support inbox and the admin user panel, `tests/test_phase5.py` for the aiogram bridge, and `tests/test_phase6.py` for Auto-Rename (name extraction on real release names, templates, panels, metadata input, sequence sorting, queue / de-duplication, the full download → rename → upload → dump pipeline (plus a real-ffmpeg MKV/metadata run when ffmpeg is installed), NSFW / size limits, leaderboard periods, verification tokens & bypass detection), runtime admins and per-channel force-sub modes, and `tests/test_phase8.py` for the clone extras (smart-link limits / passwords / expiry, Stars checkout + delivery, premium, index + search, requests, anti-flood, broadcasts & schedules, export, the ✨ Extras panel and ownership transfer), and `tests/test_phase9.py` for the performance work (live progress, saver cancel / visible upload errors, encoder throttle, parallel force-sub, instant start pics, indexes, cache trimming), and `tests/test_phase10.py` for the clone lifecycle (warning → deactivation → reactivation, owner-only buttons), disabled buttons and the new home screen, and `tests/test_phase12.py` for the Auto-Rename upgrade (source / group / ranges / movie-aware templates, tag cleaning, word rules, presets, manual mode prompts, caption placeholders, probing + frame thumbnails, history, queue cancel, `/rename` on the engine), and `tests/test_phase13.py` for the rich UI (small caps keep tokens and escape HTML, every screen valid in both renderings, rich HTML tag whitelist, rich-safe keyboards and their handlers, `SendRichMessage` / rich edits on the wire, classic fallback, group rendering, replacing refused rich edits), `tests/test_phase15.py` for the Phase 15 engine (AV1 / GPU / 2-pass / watermark builders, real-ffmpeg mux / merge / audio / GIF / split runs, the scheduler, queue restore, a local fake Mega server for decryption, redirect and SSRF checks, the new commands and menus, end-to-end task runs), `tests/test_phase15_botwide.py` for analytics, the web dashboard, backups and languages, `tests/test_phase16.py` for the speed / duplicates work (real-ffmpeg VFR and dedup frame counts, the ffmpeg-version flag choice, a stderr flood that used to stall the encoder, tail-only progress, the duplicate-task guard, one status message per task, parallel downloads byte-for-byte with fallbacks and cancel, Telegram-sized thumbnails), and `tests/test_phase14.py` for Encoder Pro (the command builder for every codec / container / filter combination, real-ffmpeg encodes / trims / screenshots when ffmpeg is installed, single-read settings, cancel permissions + process kill, transfer cancel, progress / summary cards, profiles, CRF / target-size steppers, the new commands and queue screens). Its `RecordingSession` captures every Bot API request exactly as aiogram would put it on the wire, validated against the Bot API 10.3 models (coloured buttons, ephemeral replies, rich messages, managed-bot pairing / ownership proof / token rotation, Stars, gifts, photos, drafts, MTProto fallbacks). Two further checks run over the whole codebase: every `callback_data` must reach exactly one handler, and every menu text must be valid Bot API HTML.

## 🗂 Layout

```
run.py            entry point: loads plugins, bot profile/commands, clones, clone lifecycle (7-day auto-off), keep-alive, watchdog
keep_alive.py     health web server + self-ping
watchdog.py       auto-cleanup & self-healing
client.py         the single shared Pyrogram client
config.py         unified env-based configuration
core/             home menu & texts, settings hub, middleware, force-sub, Stars payments (plans / subscriptions / gifts),
                  inline, errors, admin, tools, botlog (owner log channel), commands (Telegram menus),
                  stream (live drafts), profile (bot photo), growth (referrals / codes / trial), support, userpanel,
                  botapi (aiogram bridge), extras (/guide, /botapi, Premium gifts), admins (runtime admins)
renamer/          auto-rename: extract (name parsing / templates), store (DB), engine (queue + ffmpeg pipeline),
                  handlers (commands, panels, sequence, leaderboard), verify (shortener verification)
saver/            restricted-content saver
VideoEncoder/     encoder (plugins + ffmpeg utils)
filestore/        clone-bot controller (main_bot/plugins, incl. managed_bots = one-tap clones) + worker engine (worker_bot/)
database/         saver database
tests/            offline test-suite
```

### ⚡ Performance notes
* **Live progress** (`core/progress.py`) – saver, Auto-Rename and tools edit the status message in place (no status files,
  no background pollers), skip unchanged edits, back off on FloodWait and have a ⏹ Cancel button.
* **Indexes** – the saver and encoder user tables get an `id` index at boot (every lookup used to scan the whole
  collection); clone registry and force-sub join requests are indexed too.
* **Force-sub** checks all channels at the same time; **start pictures** come from a pre-fetched pool, so `/start`
  and 🏠 Home never wait for the picture API.
* `/start` sends the reply first: the reaction and the `#Start` log post run in the background, and the plan lookup,
  start picture and live draft run in parallel (the old 3-step typewriter added ~0.5–1 s).
* Clone activity tracking checks its 60 s throttle before spawning a task, so busy clones don't create one per update.
* The watchdog also trims per-user caches (rename settings, locks, log/error de-dupe) so memory stays flat.

### Handler order
`-10` payments (never blocked) → `-4` user tracking (+ referral credit) → `-3` ban / maintenance → `-2` force-subscribe → `-1` reply routers (support inbox, gift picker, rename-settings input) → `0` modules → `1–2` clone link generators.  
Inside clones: `-10` Stars payments → `-3` anti-flood → `-2` password / request-reply input → `-1` activity → `0` commands → `1–2` link generators → `3` storage-channel indexer.

## 📜 Licence

The bundled code keeps its original licences: the video encoder is AGPL-3.0 (`LICENSE-VideoEncoder-AGPL`) and the content saver is MIT (`LICENSE-save-restricted-content-bot`).
If you run a modified copy publicly you must offer its source to users – set
`SOURCE_URL` and the bot answers `/source` with it.

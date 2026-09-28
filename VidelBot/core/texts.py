"""
All user-facing texts of Videl.

Content comes from the original bots (Save-Restricted-Content, Video Encoder,
Son-Goku FileStore, Auto-Rename) – rebranded to Videl, developer credits removed.
Layout follows the Videl design system (core/design.py): the look of the rich
/guide – sans-bold headings, blockquote cards, collapsible sections, tables and a
footer – in classic HTML so every menu can still be edited in place.

Templates that are .format()-ed keep their {placeholders}; everything else is final text.
"""
from config import BOT_NAME, CLONE_INACTIVE_DAYS, FREE_LIMIT_DAILY, FREE_LIMIT_SIZE_GB, MAX_BOTS_PER_USER
from core.design import DOT, bullets, card, cmd, details, footer, heading, kv, page, section, table

# ════════════════════════════════════════════════════════════════
# Home   ·   .format(mention, username, first_name, uptime, plan)
# ════════════════════════════════════════════════════════════════
START_TXT = page(
    "<b>👋 Hello {mention}!</b>\n"
    "<b>I'm <a href=https://t.me/{username}>{first_name}</a></b> — <i>your all-in-one Telegram workspace.</i>",
    card("<b>🟢 Online</b>" + DOT + "<b>⏱ Uptime</b> <code>{uptime}</code>",
         "<b>👤 Your plan</b>" + DOT + "{plan}"),
    section("🚀", "Quick start", bullets(
        "Send a <b>t.me link</b> — I save the post",
        "Reply <code>/dl</code> to a <b>video</b> — I encode it",
        "Set <code>/autorename</code> once — every file gets a clean name",
        "Tap <b>⚡ Clone Bot</b> — launch your own FileStore bot")),
    details("🧩", "Everything I can do",
            "📥 <b>Save</b> — restricted channels &amp; groups, whole batches",
            "🎬 <b>Encode</b> — x264 / x265, hardsub, watermark, audio tracks",
            "✏️ <b>Auto-Rename</b> — clean names, metadata &amp; thumbnails",
            "⚡ <b>Clone</b> — your own FileStore bot with smart links",
            "🧰 <b>Tools</b> — MediaInfo, uploads, QR codes, short links",
            "💎 <b>Premium</b> — Telegram Stars, gifts &amp; referrals"),
    footer("Choose an option below 👇"),
)

# ════════════════════════════════════════════════════════════════
# Help (final text – also sent as the /guide fallback)
# ════════════════════════════════════════════════════════════════
HELP_TXT = page(
    heading("📚", "Help &amp; Guide", "The essentials first — tap a section to expand it."),
    section("🚀", "Quick start", bullets(
        "Send any <b>post link</b> — public channels need no login",
        "Private channel? " + cmd("login") + " once, then send <code>t.me/c/…</code> links",
        "Many posts? Send a range: <code>https://t.me/channel/100-120</code>",
        cmd("cancel") + " stops whatever is running")),
    details("🎬", "Video encoder", bullets(
        cmd("dl", "reply to a video / file to encode it"),
        cmd("ddl", "encode from a direct link") + DOT + cmd("af", "pick audio tracks"),
        cmd("vset", "your settings") + DOT + cmd("queue") + DOT + cmd("status"))),
    details("✏️", "Auto-Rename",
            "<code>/autorename {title} S{season}E{episode} [{quality}]</code>",
            bullets("then just send files — they come back renamed",
                    cmd("setmedia") + DOT + cmd("metadata") + DOT + cmd("start_sequence"),
                    cmd("leaderboard") + DOT + cmd("tutorial"))),
    details("⚡", "Clone bots", bullets(
        cmd("clone", "create your own FileStore bot"),
        "permanent links, force-sub, shortener, auto-delete, smart links",
        f"unused clones switch off after {CLONE_INACTIVE_DAYS} days — one tap wakes them"
        if CLONE_INACTIVE_DAYS > 0 else "")),
    details("🧰", "Tools", bullets(
        cmd("rename") + DOT + cmd("mediainfo") + DOT + cmd("upload"),
        cmd("qr") + DOT + cmd("short") + DOT + cmd("id") + DOT + cmd("info"))),
    details("💎", "Premium &amp; rewards", bullets(
        cmd("buy", "pay with ⭐ Stars") + DOT + cmd("mysub", "auto-renew"),
        cmd("gift", "Premium for a friend") + DOT + cmd("refer", "earn it free"),
        cmd("trial") + DOT + "<code>/redeem CODE</code>" + DOT + cmd("support"))),
    section("📊", "Free vs Premium", table([
        ("Daily saves", f"{FREE_LIMIT_DAILY} → unlimited"),
        ("File size", f"{FREE_LIMIT_SIZE_GB:g} GB → no cap"),
        ("Batch", "5 posts → unlimited"),
    ])),
    footer("📖 /guide — illustrated tour" + DOT + "📜 /commands — every saver command"),
)

# ════════════════════════════════════════════════════════════════
# About   ·   .format(username, first_name, api, uptime)
# ════════════════════════════════════════════════════════════════
ABOUT_TXT = page(
    "<b>ℹ️ 𝗔𝗯𝗼𝘂𝘁</b> <b><a href=https://t.me/{username}>{first_name}</a></b>\n"
    "<i>One bot for saving, encoding, renaming and sharing files.</i>",
    section("🧩", "Overview",
            kv("Bot", "@{username}", "🤖"),
            kv("Modules", "Saver · Encoder · Auto-Rename · Clone · Tools", "🧩"),
            kv("Protocol", "MTProto (Pyrofork) + Bot API {api}", "📡"),
            kv("Language", "<a href='https://www.python.org/'>Python 3.11</a>", "🐍"),
            kv("Database", "<a href='https://www.mongodb.com/'>MongoDB</a>", "🗄"),
            kv("Engine", "<a href='https://ffmpeg.org/'>FFmpeg</a>", "🎞"),
            kv("Uptime", "<code>{uptime}</code>", "⏱")),
    details("🔒", "Privacy", bullets(
        cmd("logout") + " removes your saved login session at any time",
        "temporary downloads are cleaned up automatically after upload",
        cmd("cancel") + " stops any task" + DOT + cmd("settings") + " controls your preferences")),
    footer(f"{BOT_NAME}" + DOT + "/guide for the illustrated tour"),
)

CHANNELS_EMPTY = "📢 No channels configured yet."

# ════════════════════════════════════════════════════════════════
# Clone bots   ·   CLONE_START_MSG.format(mention)
# ════════════════════════════════════════════════════════════════
CLONE_START_MSG = page(
    heading("⚡", f"{BOT_NAME} FileStore", "Your own file-sharing bot — ready in two minutes."),
    "<b>Welcome, {mention}!</b>",
    card(bullets(
        "Store files and share <b>permanent links</b>",
        "Force-subscribe, shortener &amp; auto-delete",
        "Smart links — expiring · limited · password · ⭐ paid",
        "Search, requests, analytics &amp; scheduled broadcasts")),
    section("📦", "Your plan",
            kv("Limit", f"<b>{MAX_BOTS_PER_USER}</b> bot(s) per user", "🤖"),
            kv("Idle rule", f"switched off after {CLONE_INACTIVE_DAYS} days unused", "💤")
            if CLONE_INACTIVE_DAYS > 0 else ""),
    footer("Tap ⚡ Create Bot to begin 👇"),
)

# {idle_block} is filled by core.menus.clone_help_text()
CLONE_HELP_MSG = page(
    heading("📖", "Clone bot guide", "From token to live bot in four steps."),
    section("⚙️", "How to create your bot",
            "<b>❶</b> Tap <b>⚡ Create Bot</b>",
            "<b>❷</b> Send your bot token (from @BotFather)",
            "<b>❸</b> Tap <b>📢 Select channel</b> (or send its ID)",
            "<b>❹</b> Your bot starts automatically!"),
    "{idle_block}",
    details("🎛", "Dashboard features", bullets(
        "<b>Force subscribe</b> — channels (join / request)",
        "<b>URL shortener</b> — monetise links",
        "<b>Admins</b> · <b>statistics</b> · <b>auto-delete</b>",
        "<b>Start config</b> — welcome text, picture, bot avatar",
        "<b>Protect</b> · <b>maintenance</b> · <b>backup</b> · <b>transfer</b>",
        "<b>✨ Extras</b> — search, requests, anti-flood, auto-link",
        "<b>📈 Analytics</b> · <b>👑 transfer ownership</b>")),
    details("📌", "Commands inside your bot",
            cmd("start", "open / retrieve files"),
            cmd("genlink") + DOT + cmd("batch") + DOT + cmd("custom_batch") + DOT + cmd("flink"),
            cmd("smartlink") + DOT + cmd("links", "expiring / limited / password / ⭐"),
            cmd("setpremium") + DOT + cmd("addpremium", "premium skips the shortener"),
            cmd("search") + DOT + cmd("index") + DOT + cmd("autolink"),
            cmd("broadcast") + DOT + cmd("schedules", "now or later (pin · silent)"),
            cmd("analytics") + DOT + cmd("export"),
            cmd("requests") + DOT + cmd("setbuttons") + DOT + cmd("sethelp"),
            cmd("ban") + DOT + cmd("unban") + DOT + cmd("ping") + DOT + cmd("id") + DOT + cmd("users")),
)

CLONE_IDLE_BLOCK = section(
    "💤", "Idle bots",
    "A clone nobody uses for <b>{days} days</b> is switched off.",
    "You get a warning a day before and can reactivate it with one tap —",
    "users, files and links are kept.")

CLONE_ABOUT_MSG = page(
    heading("ℹ️", f"About {BOT_NAME} FileStore", "A multi-user Telegram FileStore platform."),
    section("✨", "Highlights", bullets(
        "Isolated bot per owner — your users, your files",
        "Encrypted token storage",
        "Force-sub (join + request) · URL shortener",
        "Auto-delete, admins &amp; formatted link generator",
        f"Idle clones switch off after {CLONE_INACTIVE_DAYS} days" if CLONE_INACTIVE_DAYS > 0 else "")),
    footer("Tap ⬅️ Back to create or manage your bots"),
)

# .format(mention)
FORCE_MSG = page(
    heading("🔒", "Join to continue"),
    card("Hey {mention}, this bot is free for members of our channel(s).",
         "Join below, then tap <b>♻️ Reload</b>."),
)

# ════════════════════════════════════════════════════════════════
# Encoder   ·   ENC_START.format(mention)
# ════════════════════════════════════════════════════════════════
ENC_START = page(
    heading("🎬", "Video Encoder", "Compress and convert videos with FFmpeg."),
    "Hi {mention}!",
    card("Reply <code>/dl</code> to any video or document to start."),
)

ENC_HELP = page(
    details("📕", "Commands", bullets(
        cmd("dl", "reply to a Telegram file to encode it"),
        cmd("ddl", "encode through a direct link") + DOT + cmd("batch", "encode in batch"),
        cmd("af", "pick / reorder audio tracks, then encode"),
        cmd("queue") + DOT + cmd("status", "live system status"),
        cmd("settings") + DOT + cmd("vset") + DOT + cmd("reset") + DOT + cmd("thumb"))),
    details("🛡", "Sudo", bullets(
        cmd("vupload") + DOT + cmd("dupload") + DOT + cmd("gupload"),
        cmd("clean") + DOT + cmd("clear") + DOT + cmd("logs") + DOT + cmd("speedtest"),
        cmd("restart") + DOT + cmd("update", "git pull"))),
    details("👑", "Owner", bullets(
        cmd("addchat") + DOT + cmd("addsudo") + DOT + cmd("rmsudo") + DOT + cmd("rmchat"),
        cmd("exec", "Python") + DOT + cmd("sh", "shell"))),
)

# ════════════════════════════════════════════════════════════════
# Tools & admin   ·   TOOLS_HELP.format(username)
# ════════════════════════════════════════════════════════════════
TOOLS_HELP = page(
    heading("🧰", "Tools", "Handy utilities — reply to a file or send a command."),
    section("📁", "Files", bullets(
        cmd("mediainfo", "codecs, resolution, bitrate, tracks"),
        "<code>/rename &lt;new name&gt;</code> — rename &amp; re-upload",
        cmd("upload", "public download link (≤ 200 MB)"))),
    details("🔗", "Links &amp; info", bullets(
        "<code>/short &lt;url&gt;</code> — shorten a link" + DOT + "<code>/qr &lt;text&gt;</code> — QR code",
        cmd("id") + DOT + cmd("info") + DOT + cmd("json") + DOT + cmd("ping"),
        cmd("guide", "illustrated guide with plans &amp; FAQ"))),
    footer("In groups /help /id /info /guide answer only you" + DOT
           + "inline: <code>@{username} text</code> for a QR / short link"),
)

ADMIN_HELP = page(
    heading("👮", "Admin panel", "Everything you need to run the bot."),
    details("🤖", "Bot",
            cmd("stats") + DOT + cmd("users") + DOT + "<code>/user &lt;id | @name&gt;</code>",
            "<code>/msg &lt;id&gt; &lt;text&gt;</code>" + DOT + cmd("export", "users CSV"),
            cmd("broadcast", "reply to a message (<code>-pin</code> to pin)"),
            "<code>/ban &lt;id&gt; [reason]</code>" + DOT + "<code>/unban &lt;id&gt;</code>" + DOT + cmd("banned"),
            "<code>/maintenance on|off</code>" + DOT + cmd("watchdog", "add <code>run</code> to sweep"),
            cmd("logtest") + DOT + cmd("report") + DOT + cmd("setcommands"),
            cmd("restart") + DOT + cmd("update", "git pull + restart")),
    details("👥", "Admins &amp; force-sub",
            "<code>/add_admin &lt;id&gt;</code>" + DOT + "<code>/deladmin &lt;id&gt;</code>" + DOT + cmd("admins"),
            "<code>/add_fsub &lt;chat&gt;</code>" + DOT + "<code>/del_fsub &lt;chat&gt;</code>" + DOT + cmd("fsub_list"),
            cmd("fsub_mode", "join-request mode per channel")),
    details("💎", "Premium &amp; growth",
            "<code>/add_premium &lt;id&gt; &lt;days&gt;</code>" + DOT + "<code>/remove_premium &lt;id&gt;</code>",
            cmd("premium_users") + DOT + "<code>/set_dump &lt;chat_id&gt;</code>",
            cmd("stars") + DOT + "<code>/refund &lt;user&gt; &lt;charge_id&gt;</code>",
            "<code>/gencode &lt;days&gt; [count] [uses]</code>" + DOT + cmd("codes") + DOT + "<code>/delcode</code>",
            "Support: users send /support → it lands in your DM → <b>reply</b> to answer"),
    details("✏️", "Auto-Rename",
            cmd("renameset", "on/off · anti-NSFW · dump channel"),
            cmd("verify_settings", "shorteners, validity, bypass detection")),
    details("👑", "Owner",
            cmd("setbotpic") + DOT + cmd("delbotpic", "bot profile photo"),
            cmd("botapi", "Bot API bridge status &amp; button demo"),
            "<code>/giftpremium &lt;user&gt; &lt;3|6|12&gt;</code> — Telegram Premium from Stars",
            cmd("gifts") + DOT + "<code>/sendgift &lt;user&gt; &lt;gift_id&gt;</code>"),
    details("⚡", "Clone bots",
            cmd("clonestats") + DOT + cmd("bots") + DOT + cmd("sys"),
            cmd("check", "health-check every clone (<code>/check fix</code> restarts)")),
)

# .format(badge, user_id)
SETTINGS_HUB = page(
    heading("⚙️", "Settings", "Tune how Videl saves, encodes and delivers your files."),
    card(kv("Account", "{badge}"), kv("User ID", "<code>{user_id}</code>")),
    details("🧭", "What's inside", bullets(
        "<b>Commands</b> — every saver command",
        "<b>Usage</b> — your quota and totals",
        "<b>Dump chat</b> — auto-forward saved files",
        "<b>Thumbnail · Caption</b> — how uploads look",
        "<b>Encoder</b> — codec, quality, resolution",
        "<b>Clone bots</b> — your FileStore bots")),
    footer("Pick a section below 👇"),
)

MAINT_TEXT = page(heading("🛠", "Under maintenance"),
                  card(f"<b>{BOT_NAME}</b> is being upgraded.", "<i>Please try again a little later.</i>"))
BANNED_TEXT = page(heading("🚫", "Access denied"), card("You are banned from using this bot."))

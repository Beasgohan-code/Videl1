"""
All user-facing texts of Videl.

The layouts are taken from the three original bots (Save-Restricted-Content,
Video Encoder, Son-Goku FileStore) – rebranded to Videl, with the developer
credits removed and the Videl modules added.
"""
from config import BOT_NAME, FREE_LIMIT_DAILY, FREE_LIMIT_SIZE_GB, MAX_BOTS_PER_USER
from core.style import hdr, quote, rows, sc

# ════════════════════════════════════════════════════════════════
# Home (Save-Restricted-Content layout)
# ════════════════════════════════════════════════════════════════
START_TXT = (
    hdr("👋", "Welcome") + "\n\n"
    + quote(sc("Hello") + " {mention}!\n"
            + sc("I'm") + " <b><a href=https://t.me/{username}>{first_name}</a></b> — "
            + sc("your all-in-one Telegram workspace. Save, encode, rename and share files, all from one chat."))
    + "\n" + quote(rows([("🟢 Status", "Online"), ("⏱ Uptime", "{uptime}"), ("👤 Your plan", "{plan}")]))
    + "\n" + quote("<b>✦ " + sc("What I can do") + " ✦</b>\n" + "\n".join(
        f"◈ {e} <b>{sc(t)}</b> — {sc(d)}" for e, t, d in (
            ("📥", "Save", "posts from restricted channels &amp; groups"),
            ("🎬", "Encode", "shrink videos with x264 / x265"),
            ("✏️", "Auto-Rename", "clean names, metadata &amp; thumbnails"),
            ("⚡", "Clone", "launch your own FileStore bot"),
            ("🧰", "Tools", "MediaInfo, uploads, QR codes, short links"),
            ("💎", "Premium", "Telegram Stars, gifts &amp; referrals"))), expandable=True)
    + "\n<b>" + sc("Choose an option below to get started") + " 👇</b>"
)

HELP_TXT = f"""<b>📚 Comprehensive Help &amp; User Guide</b>
<blockquote><b>1️⃣ Public Channels (No Login Required)</b></blockquote>
• Forward or send the post link directly.
• Compatible with any public channel or group.
• <i>Example Link:</i> <code>https://t.me/channel/123</code>
<blockquote><b>2️⃣ Private/Restricted Channels (Login Required)</b></blockquote>
• Use <code>/login</code> to securely connect your Telegram account.
• Send the private link (e.g., <code>t.me/c/123...</code>).
• Bot accesses content using your authenticated session.
<blockquote><b>3️⃣ Batch Downloading Mode</b></blockquote>
• Send a range link: <code>https://t.me/channel/100-120</code>
• Stop anytime with <code>/cancel</code>.
<blockquote><b>4️⃣ More Modules</b></blockquote>
• 🎬 Reply <code>/dl</code> to any video to encode it.
• ⚡ <code>/clone</code> — create your own FileStore bot.
• ✏️ <code>/autorename {{title}} S{{season}}E{{episode}} [{{quality}}]</code> — then just send files to rename them
   (<code>/setmedia</code> · <code>/metadata</code> · <code>/start_sequence</code> · <code>/leaderboard</code> · <code>/tutorial</code>)
• 🧰 <code>/rename</code> · <code>/mediainfo</code> · <code>/upload</code> · <code>/qr</code> · <code>/short</code>
<blockquote><b>5️⃣ Premium &amp; Rewards</b></blockquote>
• ⭐ <code>/buy</code> — pay with Telegram Stars · 🔁 <code>/mysub</code> monthly auto-renew
• 🎁 <code>/gift</code> — gift Premium to a friend (pick them from your contacts)
• 🆓 <code>/trial</code> · 🎟 <code>/redeem CODE</code> · 🤝 <code>/refer</code> — earn Premium free
• 💬 <code>/support</code> — talk to the bot owner
<blockquote><b>🛑 Free User Limitations:</b></blockquote>
• <b>Daily Quota:</b> {FREE_LIMIT_DAILY} Files / 24 Hours
• <b>File Size Cap:</b> {FREE_LIMIT_SIZE_GB:g}GB Maximum
<blockquote><b>💎 Premium Membership Benefits:</b></blockquote>
• Unlimited Downloads &amp; No Restrictions.
• Priority Support &amp; Advanced Features.
"""

ABOUT_TXT = """<b>ℹ️ About {first_name}</b>
<blockquote><b>╭────[ 🧩 Overview ]────⍟</b>
<b>├⍟ 🤖 Bot : <a href=https://t.me/{username}>@{username}</a></b>
<b>├⍟ 🧩 Modules : Saver · Encoder · Auto-Rename · Clone · Tools</b>
<b>├⍟ 📡 Protocol : MTProto (Pyrofork) + Bot API {api}</b>
<b>├⍟ 🐍 Language : <a href='https://www.python.org/'>Python 3.11</a></b>
<b>├⍟ 🗄 Database : <a href='https://www.mongodb.com/'>MongoDB</a></b>
<b>├⍟ 🎞 Engine : <a href='https://ffmpeg.org/'>FFmpeg</a></b>
<b>├⍟ ⏱ Uptime : {uptime}</b>
<b>╰───────────────⍟</b></blockquote>
<blockquote expandable><b>🔒 Privacy</b>
• /logout removes your saved login session at any time.
• Temporary downloads are cleaned up automatically after upload.
• /cancel stops any running task · /settings controls your preferences.</blockquote>
"""

CHANNELS_EMPTY = "📢 No channels configured yet."

# ════════════════════════════════════════════════════════════════
# Clone bots (Son-Goku FileStore layout)
# ════════════════════════════════════════════════════════════════
CLONE_START_MSG = f"""<b>━━━━━━━━━━━━━━━━━━━━━
⚡ 𝗩𝗜𝗗𝗘𝗟 𝗙𝗜𝗟𝗘𝗦𝗧𝗢𝗥𝗘 ⚡
━━━━━━━━━━━━━━━━━━━━━</b>

<blockquote>ᴡᴇʟᴄᴏᴍᴇ, {{mention}}!

ɪ ᴀᴍ ᴀ <b>ᴘʀᴇᴍɪᴜᴍ ᴍᴜʟᴛɪ-ᴜsᴇʀ ꜰɪʟᴇsᴛᴏʀᴇ</b> ᴘʟᴀᴛꜰᴏʀᴍ.
ᴄʀᴇᴀᴛᴇ ʏᴏᴜʀ ᴏᴡɴ ꜰɪʟᴇsᴛᴏʀᴇ ʙᴏᴛ ɪɴ sᴇᴄᴏɴᴅs!

╭─── ✦ ꜰᴇᴀᴛᴜʀᴇs ✦ ───╮
│ ◈ ꜰɪʟᴇ sᴛᴏʀᴀɢᴇ ᴡɪᴛʜ sʜᴀʀᴇ ʟɪɴᴋs
│ ◈ ꜰᴏʀᴄᴇ sᴜʙsᴄʀɪʙᴇ ᴄʜᴀɴɴᴇʟs
│ ◈ ᴜʀʟ sʜᴏʀᴛᴇɴᴇʀ ɪɴᴛᴇɢʀᴀᴛɪᴏɴ
│ ◈ ᴀᴅᴍɪɴ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ
│ ◈ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ ᴛɪᴍᴇʀ
│ ◈ ꜰᴏʀᴍᴀᴛᴛᴇᴅ ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴏʀ
│ ◈ sᴍᴀʀᴛ ʟɪɴᴋs · ᴇxᴘɪʀʏ · ᴘᴀssᴡᴏʀᴅ · ⭐
│ ◈ sᴇᴀʀᴄʜ · ʀᴇǫᴜᴇsᴛs · ᴀɴᴀʟʏᴛɪᴄs
│ ◈ ᴘʀᴇᴍɪᴜᴍ ᴜsᴇʀs · sᴄʜᴇᴅᴜʟᴇᴅ ʙʀᴏᴀᴅᴄᴀsᴛs
╰──────────────────╯

ʟɪᴍɪᴛ: <b>{MAX_BOTS_PER_USER}</b> ʙᴏᴛ(s) ᴘᴇʀ ᴜsᴇʀ · ᴍᴏʀᴇ ᴡɪᴛʜ /plans</blockquote>

<i>⬇️ ᴛᴀᴘ ᴀ ʙᴜᴛᴛᴏɴ ᴛᴏ ɢᴇᴛ sᴛᴀʀᴛᴇᴅ ⬇️</i>"""

CLONE_HELP_MSG = """<b>━━━━━━━━━━━━━━━━━━━━━
📖 𝗛𝗘𝗟𝗣 &amp; 𝗚𝗨𝗜𝗗𝗘
━━━━━━━━━━━━━━━━━━━━━</b>

<blockquote><b>⚙️ ʜᴏᴡ ᴛᴏ ᴄʀᴇᴀᴛᴇ ʏᴏᴜʀ ʙᴏᴛ:</b>

<b>❶</b> ᴛᴀᴘ <b>⚡ ᴄʀᴇᴀᴛᴇ ʙᴏᴛ</b>
<b>❷</b> sᴇɴᴅ ʏᴏᴜʀ ʙᴏᴛ ᴛᴏᴋᴇɴ (ꜰʀᴏᴍ @BotFather)
<b>❸</b> ᴛᴀᴘ <b>📢 sᴇʟᴇᴄᴛ ᴄʜᴀɴɴᴇʟ</b> (ᴏʀ sᴇɴᴅ ɪᴛs ɪᴅ)
<b>❹</b> ʏᴏᴜʀ ʙᴏᴛ sᴛᴀʀᴛs ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ!</blockquote>

<blockquote><b>💤 ɪᴅʟᴇ ʙᴏᴛs:</b> ᴀ ᴄʟᴏɴᴇ ɴᴏʙᴏᴅʏ ᴜsᴇs ꜰᴏʀ {idle_days} ᴅᴀʏs ɪs ᴅᴇᴀᴄᴛɪᴠᴀᴛᴇᴅ. ʏᴏᴜ ɢᴇᴛ ᴀ ᴡᴀʀɴɪɴɢ ᴀ ᴅᴀʏ ʙᴇꜰᴏʀᴇ
ᴀɴᴅ ᴄᴀɴ ʀᴇᴀᴄᴛɪᴠᴀᴛᴇ ɪᴛ ᴡɪᴛʜ ᴏɴᴇ ᴛᴀᴘ — ᴜsᴇʀs, ꜰɪʟᴇs ᴀɴᴅ ʟɪɴᴋs ᴀʀᴇ ᴋᴇᴘᴛ.</blockquote>

<blockquote expandable><b>🎛 ᴅᴀsʜʙᴏᴀʀᴅ ꜰᴇᴀᴛᴜʀᴇs:</b>

◈ <b>ꜰᴏʀᴄᴇ sᴜʙsᴄʀɪʙᴇ</b> — ᴀᴅᴅ ᴄʜᴀɴɴᴇʟs (ᴊᴏɪɴ / ʀᴇǫᴜᴇsᴛ)
◈ <b>ᴜʀʟ sʜᴏʀᴛᴇɴᴇʀ</b> — ᴍᴏɴᴇᴛɪᴢᴇ ʟɪɴᴋs
◈ <b>ᴀᴅᴍɪɴ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ</b> — ᴀᴅᴅ/ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴs
◈ <b>sᴛᴀᴛɪsᴛɪᴄs</b> — ᴠɪᴇᴡ ᴜsᴀɢᴇ sᴛᴀᴛs
◈ <b>ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ</b> — sᴇᴛ ᴛɪᴍᴇʀ
◈ <b>sᴛᴀʀᴛ ᴄᴏɴꜰɪɢ</b> — ᴄᴜsᴛᴏᴍ ᴡᴇʟᴄᴏᴍᴇ
◈ <b>ʙᴏᴛ ᴘʀᴏꜰɪʟᴇ ᴘʜᴏᴛᴏ</b> — sᴇᴛ ʏᴏᴜʀ ʙᴏᴛ's ᴀᴠᴀᴛᴀʀ (ɴᴏ @BotFather)
◈ <b>ᴘʀᴏᴛᴇᴄᴛ · ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ · ʙᴀᴄᴋᴜᴘ · ᴛʀᴀɴsꜰᴇʀ</b>
◈ <b>✨ ᴇxᴛʀᴀs</b> — sᴇᴀʀᴄʜ, ʀᴇǫᴜᴇsᴛs, ᴀɴᴛɪ-ꜰʟᴏᴏᴅ, ᴀᴜᴛᴏ-ʟɪɴᴋ sᴡɪᴛᴄʜᴇs
◈ <b>📈 ᴀɴᴀʟʏᴛɪᴄs · 👑 ᴛʀᴀɴsꜰᴇʀ ᴏᴡɴᴇʀsʜɪᴘ</b></blockquote>

<blockquote expandable><b>📌 ᴡᴏʀᴋᴇʀ ʙᴏᴛ ᴄᴏᴍᴍᴀɴᴅs:</b>

<code>/start</code> — sᴛᴀʀᴛ / ʀᴇᴛʀɪᴇᴠᴇ ꜰɪʟᴇs
<code>/genlink</code> — ʟɪɴᴋ ꜰᴏʀ ᴀ sɪɴɢʟᴇ ᴘᴏsᴛ
<code>/batch</code> — ʟɪɴᴋ ꜰᴏʀ ᴍᴜʟᴛɪᴘʟᴇ ᴘᴏsᴛs
<code>/custom_batch</code> — ᴄᴜsᴛᴏᴍ ʙᴀᴛᴄʜ
<code>/flink</code> — ꜰᴏʀᴍᴀᴛᴛᴇᴅ ʟɪɴᴋs
<code>/smartlink</code> · <code>/links</code> — ᴇxᴘɪʀɪɴɢ / ʟɪᴍɪᴛᴇᴅ / ᴘᴀssᴡᴏʀᴅ / ⭐ ʟɪɴᴋs
<code>/setpremium</code> · <code>/addpremium</code> — ᴘʀᴇᴍɪᴜᴍ sᴋɪᴘs ᴛʜᴇ sʜᴏʀᴛᴇɴᴇʀ
<code>/search</code> · <code>/index</code> · <code>/autolink</code> — ꜰɪɴᴅ &amp; ᴀᴜᴛᴏ-sʜᴀʀᴇ ꜰɪʟᴇs
<code>/broadcast</code> · <code>/schedules</code> — ɴᴏᴡ ᴏʀ sᴄʜᴇᴅᴜʟᴇᴅ (ᴘɪɴ · sɪʟᴇɴᴛ)
<code>/analytics</code> · <code>/export</code> — sᴛᴀᴛs &amp; ʙᴀᴄᴋᴜᴘ
<code>/requests</code> · <code>/setbuttons</code> · <code>/sethelp</code> — ᴜsᴇʀ ᴛᴏᴏʟs
<code>/ban</code> · <code>/unban</code> — ᴜsᴇʀ ᴍᴏᴅᴇʀᴀᴛɪᴏɴ
<code>/ping</code> · <code>/id</code> · <code>/users</code> — ᴜᴛɪʟɪᴛʏ</blockquote>"""

CLONE_ABOUT_MSG = f"""<b>━━━━━━━━━━━━━━━━━━━━━
ℹ️ 𝗔𝗕𝗢𝗨𝗧
━━━━━━━━━━━━━━━━━━━━━</b>

<blockquote><b>⚡ {BOT_NAME} ꜰɪʟᴇsᴛᴏʀᴇ</b>

ᴀ ᴘʀᴇᴍɪᴜᴍ ᴍᴜʟᴛɪ-ᴜsᴇʀ ᴛᴇʟᴇɢʀᴀᴍ
ꜰɪʟᴇsᴛᴏʀᴇ ᴘʟᴀᴛꜰᴏʀᴍ.

╭─── ✦ ʜɪɢʜʟɪɢʜᴛs ✦ ───╮
│ ◈ ɪsᴏʟᴀᴛᴇᴅ ʙᴏᴛ ɪɴsᴛᴀɴᴄᴇs
│ ◈ ᴇɴᴄʀʏᴘᴛᴇᴅ ᴛᴏᴋᴇɴ sᴛᴏʀᴀɢᴇ
│ ◈ ꜰᴏʀᴄᴇ sᴜʙ (ᴊᴏɪɴ + ʀᴇǫᴜᴇsᴛ)
│ ◈ ᴜʀʟ sʜᴏʀᴛᴇɴᴇʀ
│ ◈ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ &amp; ᴀᴅᴍɪɴ
│ ◈ ꜰᴏʀᴍᴀᴛᴛᴇᴅ ʟɪɴᴋ ɢᴇɴ
│ ◈ ᴀᴜᴛᴏ-ʜɪʙᴇʀɴᴀᴛɪᴏɴ
╰──────────────────╯</blockquote>"""

FORCE_MSG = """<b>━━━━━━━━━━━━━━━━━━━━━
🔒 𝗔𝗖𝗖𝗘𝗦𝗦 𝗥𝗘𝗦𝗧𝗥𝗜𝗖𝗧𝗘𝗗
━━━━━━━━━━━━━━━━━━━━━</b>

<blockquote>ʜᴇʏ {mention},

ᴛᴏ ᴜsᴇ ᴛʜɪs ʙᴏᴛ ʏᴏᴜ ᴍᴜsᴛ ᴊᴏɪɴ ᴏᴜʀ ᴄʜᴀɴɴᴇʟ(s) ꜰɪʀsᴛ.
ᴛᴀᴘ ᴛʜᴇ ʙᴜᴛᴛᴏɴ(s) ʙᴇʟᴏᴡ ᴀɴᴅ ᴄʟɪᴄᴋ <b>♻️ ʀᴇʟᴏᴀᴅ</b>.</blockquote>"""

# ════════════════════════════════════════════════════════════════
# Encoder (Video Encoder layout)
# ════════════════════════════════════════════════════════════════
ENC_START = (
    hdr("🎬", "Video Encoder") + "\n\n"
    + quote(sc("Hi") + " {mention}! " + sc("I'm the encoder module – I shrink and convert your videos.")
            + "\n◈ " + sc("Reply") + " <code>/dl</code> " + sc("to any video or document to start."))
)

ENC_HELP = (
    "<b>📕 " + sc("Commands") + "</b>\n"
    + quote("\n".join(f"◈ {c} — {sc(d)}" for c, d in (
        ("/dl", "reply to a Telegram file to encode it"),
        ("/compress", "shrink a video – buttons for 1080p 720p 480p 360p · /compress 480 strong"),
        ("/ddl", "encode through a direct link"),
        ("/batch", "encode in batch"), ("/af", "pick / reorder audio tracks, then encode"),
        ("/sample [sec]", "30 s test encode – check quality & size first"),
        ("/trim 1:00 2:30", "lossless cut of a part"), ("/screens [n]", "up to 10 screenshots"),
        ("/mux", "reply to a video, then send a subtitle / audio file – added losslessly"),
        ("/merge", "send 2-10 videos, then /merge done – one file"),
        ("/convert mp3", "audio from a video: mp3 m4a opus flac wav · /convert gif 1:20 8"),
        ("/watermark", "your text · reply to a photo for a logo · /watermark off"),
        ("/leech link", "Mega / Google Drive / direct link → Telegram, split above 2 GB"),
        ("/queue", "check the queue"), ("/status", "live system status"),
        ("/settings", "settings (🎬 Video Encoder)"), ("/vset", "view settings"), ("/reset", "reset settings"),
        ("/thumb", "custom thumbnail for encodes"))), expandable=True)
    + "\n\n<b>⚡ " + sc("Encoder Pro") + "</b>\n"
    + quote("\n".join(f"◈ {sc(a)} — {sc(b)}" for a, b in (
        ("Quick profiles", "Mobile, Balanced, High quality, Anime, Tiny, Fast"),
        ("Target size", "fit a video into e.g. 200 MB"),
        ("Filters", "deinterlace, denoise, drop duplicate frames, loudness normalise"),
        ("Live progress", "speed, fps, ETA, size and a real ❌ Cancel"),
        ("GPU", "NVENC / QSV / VAAPI used automatically when the server has one"),
        ("AV1 💎", "smallest files – Encoder Pro"),
        ("2-pass 💎", "target size within ~2 % – Encoder Pro"),
        ("Logo watermark 💎", "position, size, opacity – Encoder Pro"),
        ("Priority queue 💎", "Pro tasks run first, up to 10 at a time"),
        ("Big files", "results over 2 GB are split into playable parts"))), expandable=True)
    + "\n\n<b>🛡 " + sc("For sudo") + "</b>\n"
    + quote("\n".join(f"◈ {c} — {sc(d)}" for c, d in (
        ("/vupload", "video upload"), ("/dupload", "document upload"), ("/gupload", "drive upload"),
        ("/clean", "clean junk"), ("/clear", "clean queue"), ("/logs", "view logs"),
        ("/speedtest", "server speed"), ("/restart", "restart bot"), ("/update", "git pull"))), expandable=True)
    + "\n\n<b>👑 " + sc("For owner") + "</b>\n"
    + quote("◈ /addchat · /addsudo · /rmsudo · /rmchat\n◈ /exec — " + sc("execute Python") + " · /sh — "
            + sc("execute shell"))
)

# ════════════════════════════════════════════════════════════════
# Tools & admin (Videl additions)
# ════════════════════════════════════════════════════════════════
TOOLS_HELP = """<b>🧰 Tools</b>

<blockquote expandable>/mediainfo — reply to a file: codecs, resolution, bitrate, tracks
/rename &lt;new name&gt; — reply to a file to rename &amp; re-upload it
/upload — reply to a file (≤ 200 MB) to get a public download link
/short &lt;url&gt; — shorten a long link
/qr &lt;text&gt; — make a QR code
/id — chat / user / forwarded IDs
/info — user info (reply / id / username)
/json — raw JSON of a message (reply)
/ping — bot latency
/guide — illustrated guide (rich message with plans &amp; FAQ)</blockquote>

<i>In groups, /help /id /info /guide answer <b>only you</b> (ephemeral messages).</i>

<i>Inline: type <code>@{username} text</code> in any chat to share a QR / short link.</i>
"""

ADMIN_HELP = """<b>👮 Admin</b>

<blockquote expandable><b>Bot</b>
/stats — users, modules &amp; server stats
/dashboard — charts + 🌐 web dashboard (opens inside Telegram)
/users — user counts
/user &lt;id | @name&gt; — full profile + ban / premium buttons
/msg &lt;id&gt; &lt;text&gt; — message a user · /export — users CSV
/broadcast — reply to a message (add <code>-pin</code> to pin)
/ban &lt;id&gt; [reason] · /unban &lt;id&gt; · /banned
/maintenance on|off
/watchdog — cleanup / health report (add <code>run</code> to sweep now)
/logtest — test the log channel · /report — activity report now
/setcommands — re-sync the Telegram command menus
/restart · /update (git pull + restart)

<b>Admins</b> (owners)
/add_admin &lt;id&gt; · /deladmin &lt;id&gt; · /admins — runtime admins, no redeploy needed

<b>Force subscribe</b>
/add_fsub &lt;chat&gt; · /del_fsub &lt;chat&gt; · /fsub_list
/fsub_mode — join-request mode per channel (a pending request counts as joined)

<b>Auto-Rename</b>
/renameset — global on/off · anti-NSFW filter · dump channel
/verify_settings — 2 shorteners, validity hours, bypass detection, daily counts

<b>Saver</b>
/add_premium &lt;id&gt; &lt;days&gt; · /remove_premium &lt;id&gt; · /premium_users
/set_dump &lt;chat_id&gt;
/stars — Stars balance, payments, subscriptions · /refund &lt;user&gt; &lt;charge_id&gt;

<b>Growth</b>
/gencode &lt;days&gt; [count] [uses] — redeem codes (0 days = lifetime)
/codes — active codes · /delcode &lt;code&gt;
Support: users send /support → it lands in your DM → <b>reply</b> to answer.

<b>Owner</b>
/setbotpic (reply to a photo) · /delbotpic — bot profile photo
/botapi — aiogram / Bot API bridge status, capabilities &amp; live button-colour demo
/giftpremium &lt;user&gt; &lt;3|6|12&gt; — gift Telegram Premium from the bot's Stars
/gifts — Telegram gifts · /sendgift &lt;user&gt; &lt;gift_id&gt; [text]

<b>Clone bots</b>
/clonestats — platform stats · /bots — list all clone bots · /sys — system
/check — health-check every clone (<code>/check fix</code> restarts broken ones)</blockquote>
"""

SETTINGS_HUB = (
    hdr("⚙️", "Settings") + "\n\n"
    + quote(rows([("Account", "{badge}"), ("User ID", "<code>{user_id}</code>")]))
    + "\n<i>" + sc("Customize and manage your preferences below 👇") + "</i>"
)

MAINT_TEXT = f"🛠 <b>{BOT_NAME} is under maintenance.</b>\n<i>Please try again a little later.</i>"
BANNED_TEXT = "🚫 <b>You are banned from using this bot.</b>"

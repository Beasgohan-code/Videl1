"""
All user-facing texts of Videl.

The layouts are taken from the three original bots (Save-Restricted-Content,
Video Encoder, Son-Goku FileStore) – rebranded to Videl, with the developer
credits removed and the Videl modules added.
"""
from config import BOT_NAME, FREE_LIMIT_DAILY, FREE_LIMIT_SIZE_GB, MAX_BOTS_PER_USER

# ════════════════════════════════════════════════════════════════
# Home (Save-Restricted-Content layout)
# ════════════════════════════════════════════════════════════════
START_TXT = """<b>👋 Hello {mention},</b>
<b>🤖 I am <a href=https://t.me/{username}>{first_name}</a></b>
<i>Your Professional All-in-One Utility Bot.</i>
<blockquote><b>🚀 System Status: 🟢 Online</b>
<b>⚡ Performance: 10x High-Speed Processing</b>
<b>🔐 Security: End-to-End Encrypted</b>
<b>📊 Uptime: {uptime}</b></blockquote>
<blockquote expandable><b>╭─── ✦ ꜰᴇᴀᴛᴜʀᴇs ✦ ───╮</b>
<b>│ ◈ 📥 Save restricted content</b>
<b>│ ◈ 🎬 Video encoder (x264 / x265)</b>
<b>│ ◈ ⚡ Clone your own FileStore bot</b>
<b>│ ◈ 🧰 Rename · MediaInfo · Upload · QR</b>
<b>│ ◈ 💎 Premium with Telegram Stars</b>
<b>╰──────────────────╯</b></blockquote>
<b>👇 Select an Option Below to Get Started:</b>
"""

HELP_TXT = f"""<b>📚 Comprehensive Help & User Guide</b>
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
• 🧰 <code>/rename</code> · <code>/mediainfo</code> · <code>/upload</code> · <code>/qr</code> · <code>/short</code>
<blockquote><b>🛑 Free User Limitations:</b></blockquote>
• <b>Daily Quota:</b> {FREE_LIMIT_DAILY} Files / 24 Hours
• <b>File Size Cap:</b> {FREE_LIMIT_SIZE_GB:g}GB Maximum
<blockquote><b>💎 Premium Membership Benefits:</b></blockquote>
• Unlimited Downloads & No Restrictions.
• Priority Support & Advanced Features.
"""

ABOUT_TXT = """<b>ℹ️ About This Bot</b>
<blockquote><b>╭────[ 🧩 Technical Stack ]────⍟</b>
<b>├⍟ 🤖 Bot Name : <a href=https://t.me/{username}>{first_name}</a></b>
<b>├⍟ 🧩 Modules : Saver · Encoder · Clone · Tools</b>
<b>├⍟ 📚 Library : <a href='https://pyrofork.wulan17.dev/'>Pyrofork (MTProto)</a></b>
<b>├⍟ 🐍 Language : <a href='https://www.python.org/'>Python 3.11+</a></b>
<b>├⍟ 🗄 Database : <a href='https://www.mongodb.com/'>MongoDB</a></b>
<b>├⍟ 🎞 Engine : <a href='https://ffmpeg.org/'>FFmpeg</a></b>
<b>├⍟ 📡 Hosting : Dedicated High-Speed Server</b>
<b>╰───────────────⍟</b></blockquote>
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
╰──────────────────╯

ʟɪᴍɪᴛ: <b>{MAX_BOTS_PER_USER}</b> ʙᴏᴛ(s) ᴘᴇʀ ᴜsᴇʀ</blockquote>

<i>⬇️ ᴛᴀᴘ ᴀ ʙᴜᴛᴛᴏɴ ᴛᴏ ɢᴇᴛ sᴛᴀʀᴛᴇᴅ ⬇️</i>"""

CLONE_HELP_MSG = """<b>━━━━━━━━━━━━━━━━━━━━━
📖 𝗛𝗘𝗟𝗣 & 𝗚𝗨𝗜𝗗𝗘
━━━━━━━━━━━━━━━━━━━━━</b>

<blockquote><b>⚙️ ʜᴏᴡ ᴛᴏ ᴄʀᴇᴀᴛᴇ ʏᴏᴜʀ ʙᴏᴛ:</b>

<b>❶</b> ᴛᴀᴘ <b>⚡ ᴄʀᴇᴀᴛᴇ ʙᴏᴛ</b>
<b>❷</b> sᴇɴᴅ ʏᴏᴜʀ ʙᴏᴛ ᴛᴏᴋᴇɴ (ꜰʀᴏᴍ @BotFather)
<b>❸</b> sᴇɴᴅ ʏᴏᴜʀ ʟᴏɢ ᴄʜᴀɴɴᴇʟ ɪᴅ
<b>❹</b> ʏᴏᴜʀ ʙᴏᴛ sᴛᴀʀᴛs ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ!</blockquote>

<blockquote expandable><b>🎛 ᴅᴀsʜʙᴏᴀʀᴅ ꜰᴇᴀᴛᴜʀᴇs:</b>

◈ <b>ꜰᴏʀᴄᴇ sᴜʙsᴄʀɪʙᴇ</b> — ᴀᴅᴅ ᴄʜᴀɴɴᴇʟs (ᴊᴏɪɴ / ʀᴇǫᴜᴇsᴛ)
◈ <b>ᴜʀʟ sʜᴏʀᴛᴇɴᴇʀ</b> — ᴍᴏɴᴇᴛɪᴢᴇ ʟɪɴᴋs
◈ <b>ᴀᴅᴍɪɴ ᴍᴀɴᴀɢᴇᴍᴇɴᴛ</b> — ᴀᴅᴅ/ʀᴇᴍᴏᴠᴇ ᴀᴅᴍɪɴs
◈ <b>sᴛᴀᴛɪsᴛɪᴄs</b> — ᴠɪᴇᴡ ᴜsᴀɢᴇ sᴛᴀᴛs
◈ <b>ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ</b> — sᴇᴛ ᴛɪᴍᴇʀ
◈ <b>sᴛᴀʀᴛ ᴄᴏɴꜰɪɢ</b> — ᴄᴜsᴛᴏᴍ ᴡᴇʟᴄᴏᴍᴇ
◈ <b>ᴘʀᴏᴛᴇᴄᴛ · ᴍᴀɪɴᴛᴇɴᴀɴᴄᴇ · ʙᴀᴄᴋᴜᴘ · ᴛʀᴀɴsꜰᴇʀ</b></blockquote>

<blockquote expandable><b>📌 ᴡᴏʀᴋᴇʀ ʙᴏᴛ ᴄᴏᴍᴍᴀɴᴅs:</b>

<code>/start</code> — sᴛᴀʀᴛ / ʀᴇᴛʀɪᴇᴠᴇ ꜰɪʟᴇs
<code>/genlink</code> — ʟɪɴᴋ ꜰᴏʀ ᴀ sɪɴɢʟᴇ ᴘᴏsᴛ
<code>/batch</code> — ʟɪɴᴋ ꜰᴏʀ ᴍᴜʟᴛɪᴘʟᴇ ᴘᴏsᴛs
<code>/custom_batch</code> — ᴄᴜsᴛᴏᴍ ʙᴀᴛᴄʜ
<code>/flink</code> — ꜰᴏʀᴍᴀᴛᴛᴇᴅ ʟɪɴᴋs
<code>/broadcast</code> — ᴍᴇssᴀɢᴇ ᴀʟʟ ᴜsᴇʀs
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
│ ◈ ᴀᴜᴛᴏ-ᴅᴇʟᴇᴛᴇ & ᴀᴅᴍɪɴ
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
    "<b>🎬 Video Encoder</b>\n\n"
    "Hi {mention}! I'm the VideoEncoder module which will do magic with your file.\n"
    "<blockquote>Reply <code>/dl</code> to any video or document to start.</blockquote>"
)

ENC_HELP = """<b>📕 Commands List</b>:

<blockquote expandable>- Reply /dl to a Telegram file to encode it
- /ddl - encode through DDL
- /batch - encode in batch
- /af - pick / reorder audio tracks, then encode
- /queue - check queue
- /status - live system status
- /settings - settings (🎬 Video Encoder)
- /vset - view settings
- /reset - reset settings
- /thumb - custom thumbnail for encodes</blockquote>

<b>For Sudo:</b>
<blockquote expandable>- /vupload - video upload
- /dupload - doc upload
- /gupload - drive upload
- /clean - clean junk
- /clear - clean queue
- /logs - view logs
- /speedtest - server speed
- /restart - restart bot
- /update - git pull</blockquote>

<b>For Owner:</b>
<blockquote>- /addchat and /addsudo
- /rmsudo and /rmchat
- /exec - Execute Python · /sh - Execute Shell</blockquote>
"""

# ════════════════════════════════════════════════════════════════
# Tools & admin (Videl additions)
# ════════════════════════════════════════════════════════════════
TOOLS_HELP = """<b>🧰 Tools</b>

<blockquote expandable>/mediainfo — reply to a file: codecs, resolution, bitrate, tracks
/rename &lt;new name&gt; — reply to a file to rename & re-upload it
/upload — reply to a file (≤ 200 MB) to get a public download link
/short &lt;url&gt; — shorten a long link
/qr &lt;text&gt; — make a QR code
/id — chat / user / forwarded IDs
/info — user info (reply / id / username)
/json — raw JSON of a message (reply)
/ping — bot latency</blockquote>

<i>Inline: type <code>@{username} text</code> in any chat to share a QR / short link.</i>
"""

ADMIN_HELP = """<b>👮 Admin</b>

<blockquote expandable><b>Bot</b>
/stats — users, modules & server stats
/users — user counts
/broadcast — reply to a message (add <code>-pin</code> to pin)
/ban &lt;id&gt; [reason] · /unban &lt;id&gt; · /banned
/maintenance on|off
/watchdog — cleanup / health report (add <code>run</code> to sweep now)
/logtest — test the log channel · /report — activity report now
/setcommands — re-sync the Telegram command menus
/restart · /update (git pull + restart)

<b>Force subscribe</b>
/add_fsub &lt;chat&gt; · /del_fsub &lt;chat&gt; · /fsub_list

<b>Saver</b>
/add_premium &lt;id&gt; &lt;days&gt; · /remove_premium &lt;id&gt; · /premium_users
/set_dump &lt;chat_id&gt;
/stars — Stars payments · /refund &lt;user&gt; &lt;charge_id&gt;

<b>Clone bots</b>
/clonestats — platform stats · /bots — list all clone bots · /sys — system
/check — health-check every clone (<code>/check fix</code> restarts broken ones)</blockquote>
"""

SETTINGS_HUB = """<b>⚙️ Settings Dashboard</b>

<b>Account:</b> {badge}
<b>User ID:</b> <code>{user_id}</code>

<i>Customize and manage your bot preferences below for an optimized experience:</i>"""

MAINT_TEXT = f"🛠 <b>{BOT_NAME} is under maintenance.</b>\n<i>Please try again a little later.</i>"
BANNED_TEXT = "🚫 <b>You are banned from using this bot.</b>"

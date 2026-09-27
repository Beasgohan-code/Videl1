"""
Telegram command menus – every command Videl understands is registered, per scope:

  • private chats (everyone)  → USER_COMMANDS
  • group chats               → GROUP_COMMANDS
  • each admin / sudo user     → USER + ADMIN_COMMANDS
  • each owner                 → USER + ADMIN + OWNER_COMMANDS

/setcommands (admins) re-syncs the menus without restarting.
"""
import logging

from pyrogram import Client, filters
from pyrogram.types import (BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats,
                            BotCommandScopeChat, BotCommandScopeDefault, Message)

import config

log = logging.getLogger("videl.commands")

USER_COMMANDS = [
    # home
    ("start", "🏠 Home menu"),
    ("help", "❓ Help & guide for every module"),
    ("guide", "📖 Illustrated guide (rich message)"),
    ("about", "ℹ️ About the bot"),
    ("settings", "⚙️ Settings dashboard"),
    ("commands", "📜 All commands"),
    ("cancel", "❌ Cancel the current task / flow"),
    # clone bots
    ("clone", "🤖 Create your own FileStore bot"),
    ("mybots", "📋 Manage your clone bots"),
    # content saver
    ("login", "🔐 Login to save from private channels"),
    ("logout", "🚪 Logout your account"),
    ("myplan", "📊 Your plan & daily quota"),
    ("plan", "💳 Premium plan details"),
    ("premium", "💎 Premium benefits & prices"),
    ("buy", "⭐ Buy Premium with Telegram Stars"),
    ("mysub", "🔁 Monthly Stars subscription"),
    ("gift", "🎁 Gift Premium to a friend"),
    ("trial", "🆓 Free Premium trial"),
    ("redeem", "🎟 Redeem a premium code"),
    ("refer", "🤝 Refer friends & earn Premium"),
    ("support", "💬 Message the bot owner"),
    ("setchat", "📤 Set a dump chat for saved files"),
    ("set_caption", "📝 Set a custom caption"),
    ("see_caption", "👁 View your caption"),
    ("del_caption", "🗑 Delete your caption"),
    ("set_thumb", "🖼 Set a custom thumbnail (reply to a photo)"),
    ("view_thumb", "👁 View your thumbnail"),
    ("del_thumb", "🗑 Delete your thumbnail"),
    ("thumb_mode", "🔁 Toggle thumbnail mode"),
    ("set_del_word", "✂️ Words to delete from captions"),
    ("rem_del_word", "➖ Remove delete-words"),
    ("set_repl_word", "🔄 Add word replacements"),
    ("rem_repl_word", "➖ Remove word replacements"),
    # encoder
    ("dl", "🎬 Encode a replied video / file"),
    ("ddl", "🔗 Encode from a direct link"),
    ("batch", "📦 Batch-encode several links"),
    ("af", "🎧 Rearrange audio tracks (reply)"),
    ("queue", "📋 Encoder queue"),
    ("vset", "🎛 View your encoder settings"),
    ("reset", "♻️ Reset encoder settings"),
    ("thumb", "🖼 Encoder thumbnail"),
    ("status", "📈 Server & queue status"),
    ("stats", "📊 Bot statistics"),
    # tools
    ("mediainfo", "🔎 Media info (reply to a file)"),
    ("rename", "✏️ Rename a file (reply)"),
    ("upload", "☁️ Public link for a file (reply)"),
    ("short", "✂️ Shorten a URL"),
    ("qr", "🔳 Make a QR code"),
    ("id", "🆔 User / chat IDs"),
    ("info", "👤 User info"),
    ("ping", "🏓 Latency"),
]

GROUP_COMMANDS = [
    ("help", "❓ Help"),
    ("guide", "📖 Illustrated guide"),
    ("dl", "🎬 Encode a replied video / file"),
    ("ddl", "🔗 Encode from a direct link"),
    ("batch", "📦 Batch-encode several links"),
    ("af", "🎧 Rearrange audio tracks (reply)"),
    ("queue", "📋 Encoder queue"),
    ("status", "📈 Server & queue status"),
    ("stats", "📊 Bot statistics"),
    ("settings", "⚙️ Encoder settings"),
    ("mediainfo", "🔎 Media info (reply to a file)"),
    ("qr", "🔳 Make a QR code"),
    ("short", "✂️ Shorten a URL"),
    ("id", "🆔 User / chat IDs"),
    ("info", "👤 User info"),
    ("ping", "🏓 Latency"),
]

ADMIN_COMMANDS = [
    ("users", "👥 User counts"),
    ("user", "🔍 User profile & actions: id | @name"),
    ("msg", "✉️ Message a user: id text"),
    ("export", "📤 Export users as CSV"),
    ("gencode", "🎟 Create redeem codes: days count uses"),
    ("codes", "📋 Active redeem codes"),
    ("delcode", "🗑 Delete a redeem code"),
    ("broadcast", "📢 Broadcast (reply to a message)"),
    ("ban", "🚫 Ban a user"),
    ("unban", "✅ Unban a user"),
    ("banned", "📋 Banned users"),
    ("maintenance", "🛠 Maintenance on / off"),
    ("add_premium", "💎 Give premium: id days"),
    ("remove_premium", "➖ Remove premium"),
    ("premium_users", "👑 Premium users"),
    ("set_dump", "📤 Set a user's dump chat"),
    ("stars", "⭐ Stars payments & revenue"),
    ("add_fsub", "🔒 Add force-sub channel"),
    ("del_fsub", "🔓 Remove force-sub channel"),
    ("fsub_list", "📋 Force-sub channels"),
    ("watchdog", "🐕 Watchdog report / run / clean"),
    ("logtest", "📝 Test the log channel"),
    ("report", "📊 Activity report now"),
    ("setcommands", "🔄 Re-sync command menus"),
    ("restart", "♻️ Restart the bot"),
    ("clean", "🧹 Delete encoder junk files"),
    ("clear", "🗑 Clear the encoder queue"),
    ("vupload", "🎞 Upload a file as video"),
    ("dupload", "📄 Upload a file as document"),
    ("gupload", "☁️ Upload to Google Drive"),
    ("logs", "📜 Get the log file"),
    ("speedtest", "🚀 Server speed test"),
]

OWNER_COMMANDS = [
    ("refund", "↩️ Refund a Stars payment"),
    ("botapi", "🛰 Bot API bridge status (aiogram)"),
    ("giftpremium", "🎁 Gift Telegram Premium: user 3|6|12"),
    ("gifts", "🎀 Telegram gifts the bot can send"),
    ("setbotpic", "🖼 Set bot profile photo (reply)"),
    ("update", "📥 git pull + restart"),
    ("clonestats", "🤖 Clone platform statistics"),
    ("bots", "📋 All clone bots"),
    ("check", "🩺 Health-check every clone bot"),
    ("sys", "🖥 CPU / RAM / disk"),
    ("addsudo", "➕ Add encoder sudo user"),
    ("rmsudo", "➖ Remove encoder sudo user"),
    ("addchat", "➕ Authorise a chat for the encoder"),
    ("rmchat", "➖ Remove an authorised chat"),
    ("exec", "🐍 Run Python"),
    ("sh", "💻 Run shell"),
]


def _cmds(*groups):
    seen, out = set(), []
    for group in groups:
        for name, desc in group:
            if name not in seen:
                seen.add(name)
                out.append(BotCommand(name, desc[:256]))
    return out[:100]  # Telegram limit per scope


async def register_commands(client) -> dict:
    """Push all menus to Telegram. Returns {scope: count} (failed scopes are skipped)."""
    done = {}
    jobs = [
        ("default", _cmds(USER_COMMANDS), BotCommandScopeDefault()),
        ("private", _cmds(USER_COMMANDS), BotCommandScopeAllPrivateChats()),
        ("groups", _cmds(GROUP_COMMANDS), BotCommandScopeAllGroupChats()),
    ]
    owners = set(config.OWNERS)
    for uid in config.ADMINS:
        extra = (ADMIN_COMMANDS, OWNER_COMMANDS) if uid in owners else (ADMIN_COMMANDS,)
        jobs.append((f"user:{uid}", _cmds(USER_COMMANDS, *extra), BotCommandScopeChat(uid)))
    for name, cmds, scope in jobs:
        try:
            await client.set_bot_commands(cmds, scope=scope)
            done[name] = len(cmds)
        except Exception as e:  # e.g. an admin who never started the bot
            log.debug(f"set_bot_commands {name} failed: {e}")
    log.info(f"📜 command menus registered: {done}")
    return done


async def set_profile(client):
    """Bot description (empty-chat screen) and short about text."""
    try:
        await client.set_bot_info(
            lang_code="",
            description=(f"✨ {config.BOT_NAME} – all-in-one utility bot\n\n"
                         "📥 Save restricted posts & media\n🎬 Encode / compress videos\n"
                         "⚡ Create your own FileStore clone bots\n⭐ Premium with Telegram Stars · gifts · subscriptions\n"
                         "🤝 Refer friends & earn Premium\n"
                         "🧰 Rename · MediaInfo · Upload · QR · Short links\n\nTap START to begin!"),
            about=f"{config.BOT_NAME}: save restricted content, encode videos & clone FileStore bots.",
        )
    except Exception as e:
        log.debug(f"set_bot_info skipped: {e}")


@Client.on_message(filters.command(["setcommands", "updatecommands"]) & filters.user(config.ADMINS))
async def setcommands_cmd(client: Client, message: Message):
    status = await message.reply_text("🔄 <i>Registering command menus…</i>")
    done = await register_commands(client)
    await set_profile(client)
    lines = "\n".join(f"• <code>{k}</code>: {v} commands" for k, v in done.items())
    await status.edit_text(f"✅ <b>Command menus updated</b>\n\n<blockquote>{lines or 'nothing registered'}</blockquote>\n"
                           "<i>Admins/owners who never started the bot are skipped – run this again after they do.</i>")

import asyncio
import os
import sys
from pathlib import Path

# Ensure an event loop exists before Pyrogram/Pyroblack Client instantiation
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client
from pyrogram.enums import ChatMemberStatus
from pyrogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
)

import config

from ..logging import LOGGER


def _package_version(distribution: str) -> str:
    try:
        from importlib.metadata import version
        return str(version(distribution))
    except Exception:
        return "unknown"


_BOT_SESSION_FILE = Path("cache/bot_session.str")


class AyuBot(Client):
    def __init__(self):
        LOGGER(__name__).info("Starting Bot...")
        if not config.BOT_TOKEN:
            LOGGER(__name__).critical("BOT_TOKEN is missing! Set BOT_TOKEN in environment variables.")
            sys.exit(1)
        if not config.API_HASH:
            LOGGER(__name__).critical("API_HASH is missing! Set API_HASH in environment variables.")
            sys.exit(1)

        session_string = os.getenv("BOT_SESSION_STRING", "").strip() or None
        if not session_string:
            try:
                if _BOT_SESSION_FILE.exists():
                    session_string = _BOT_SESSION_FILE.read_text().strip() or None
            except Exception:
                session_string = None

        init_kwargs = dict(
            name="Videl Music",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            bot_token=config.BOT_TOKEN,
            in_memory=True,
            workers=int(os.getenv("BOT_WORKERS", "8")),
            max_concurrent_transmissions=int(os.getenv("BOT_MAX_TRANSMISSIONS", "4")),
            sleep_threshold=15,
        )
        if session_string:
            init_kwargs["session_string"] = session_string

        super().__init__(**init_kwargs)

    async def start(self, *args, **kwargs):
        from pyrogram.errors import FloodWait

        for _attempt in range(12):
            try:
                LOGGER(__name__).info("Connecting bot client (attempt %d/12)...", _attempt + 1)
                await asyncio.wait_for(super().start(*args, **kwargs), timeout=60)
                LOGGER(__name__).info("Bot client connected.")
                break
            except FloodWait as e:
                wait = int(e.value) + 5
                LOGGER(__name__).warning(
                    "Bot auth flood-wait %ss — sleeping, will retry", wait
                )
                await asyncio.sleep(wait)
            except asyncio.TimeoutError:
                LOGGER(__name__).warning(
                    "Bot connect timed out after 60s (attempt %d/12) — retrying", _attempt + 1
                )
                try:
                    await super().stop()
                except Exception:
                    pass
                await asyncio.sleep(3)
        else:
            raise RuntimeError("Bot login kept failing/flood-waiting; giving up")

        try:
            if not _BOT_SESSION_FILE.exists():
                _BOT_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                _BOT_SESSION_FILE.write_text(await self.export_session_string())
        except Exception:
            pass

        get_me = await asyncio.wait_for(self.get_me(), timeout=20)
        self.username = get_me.username
        self.id = get_me.id
        self.name = (get_me.first_name or "") + (" " + get_me.last_name if get_me.last_name else "")
        self.mention = get_me.mention

        try:
            from AoiMusic.utils.rich_msg import register_anchor_hook

            register_anchor_hook(self)
        except Exception:
            LOGGER(__name__).warning("anchor hook registration failed", exc_info=True)

        if config.LOGGER_ID:
            try:
                python_version = "%d.%d.%d" % sys.version_info[:3]
                tgcalls_version = _package_version("py-tgcalls")
                owner_display = config.OWNER_ID[0] if config.OWNER_ID else "N/A"
                start_msg = f"""
╔══════════╗
  🎵 **{self.mention}** 🎵
╚══════════╝

⚡ **ʙᴏᴛ sᴛᴀʀᴛᴇᴅ sᴜᴄᴄᴇssғᴜʟʟʏ**

┌────────────┐
│ 🔑 **ɪᴅ :** <code>{self.id}</code>
│ 🧑 **ɴᴀᴍᴇ :** {self.name}
│ 🔗 **ᴜsᴇʀɴᴀᴍᴇ :** @{self.username}
│ 📡 **ʜᴜɴᴛᴇʀ :** {owner_display}
│ 🌐 **ᴘʟᴀᴛғᴏʀᴍ :** ᴄʟᴏᴜᴅ ʟɪɴᴜx
│ 🐍 **ᴘʏᴛʜᴏɴ :** {python_version}
│ ⚙️ **ᴘʏᴛɢᴄᴀʟʟs :** v{tgcalls_version}
└────────────┘

🚀 **ʀᴇᴀᴅʏ ᴛᴏ ᴘʟᴀʏ ᴍᴜsɪᴄ**
💎 **ᴘʀᴇᴍɪᴜᴍ ᴜɪ ᴀᴄᴛɪᴠᴇ**
🔥 **ᴠᴏɪᴄᴇᴄʜᴀᴛ ᴄᴏɴɴᴇᴄᴛᴇᴅ**
"""
                await asyncio.wait_for(
                    self.send_message(
                        config.LOGGER_ID,
                        text=start_msg,
                    ),
                    timeout=15,
                )
            except Exception as ex:
                LOGGER(__name__).warning(
                    "Bot could not send to log group/channel (non-fatal): %s", type(ex).__name__
                )

            try:
                a = await asyncio.wait_for(self.get_chat_member(config.LOGGER_ID, self.id), timeout=15)
                if a.status != ChatMemberStatus.ADMINISTRATOR:
                    LOGGER(__name__).warning(
                        "Bot is not admin in log group/channel (non-fatal)."
                    )
            except Exception:
                LOGGER(__name__).warning("Could not check admin status in log group (non-fatal).")

        if config.SET_CMDS == str(True):
            try:
                await asyncio.wait_for(
                    self.set_bot_commands(
                        commands=[
                            BotCommand("start", "sᴛᴀʀᴛ ᴛʜᴇ ʙᴏᴛ"),
                            BotCommand("help", "ɢᴇᴛ ᴛʜᴇ ʜᴇʟᴘ ᴍᴇɴᴜ"),
                            BotCommand("ping", "ᴄʜᴇᴄᴋ ʙᴏᴛ ɪs ᴀʟɪᴠᴇ ᴏʀ ᴅᴇᴀᴅ"),
                        ],
                        scope=BotCommandScopeAllPrivateChats(),
                    ),
                    timeout=15,
                )
                await asyncio.wait_for(
                    self.set_bot_commands(
                        commands=[
                            BotCommand("play", "Play audio in voice chat"),
                            BotCommand("vplay", "Play video in voice chat"),
                            BotCommand("queue", "Show current music queue"),
                            BotCommand("afk", "Mark yourself as AFK"),
                            BotCommand("rules", "Show group rules"),
                            BotCommand("notes", "List saved group notes"),
                            BotCommand("report", "Report a message to admins"),
                            BotCommand("admins", "List group admins"),
                            BotCommand("id", "Get user or chat ID"),
                            BotCommand("info", "Get user profile info"),
                            BotCommand("help", "Open Music & Management help"),
                        ],
                        scope=BotCommandScopeAllGroupChats(),
                    ),
                    timeout=15,
                )
                await asyncio.wait_for(
                    self.set_bot_commands(
                        commands=[
                            BotCommand("play", "Play audio in voice chat"),
                            BotCommand("vplay", "Play video in voice chat"),
                            BotCommand("skip", "Skip current track"),
                            BotCommand("pause", "Pause playback"),
                            BotCommand("resume", "Resume playback"),
                            BotCommand("end", "Stop music & leave VC"),
                            BotCommand("settings", "Open chat settings"),
                            BotCommand("promote", "Promote user to admin"),
                            BotCommand("demote", "Demote an admin"),
                            BotCommand("ban", "Ban a user from group"),
                            BotCommand("tban", "Temporarily ban a user"),
                            BotCommand("unban", "Unban a user"),
                            BotCommand("kick", "Kick a user from group"),
                            BotCommand("mute", "Mute user (or VC without args)"),
                            BotCommand("tmute", "Temporarily mute a user"),
                            BotCommand("unmute", "Unmute user (or VC without args)"),
                            BotCommand("warn", "Warn a user"),
                            BotCommand("warns", "Check user warnings"),
                            BotCommand("resetwarns", "Reset user warnings"),
                            BotCommand("purge", "Delete messages from reply"),
                            BotCommand("del", "Delete replied message"),
                            BotCommand("lock", "Lock a message/media type"),
                            BotCommand("unlock", "Unlock a message/media type"),
                            BotCommand("nightmode", "Lock group messaging"),
                            BotCommand("rmnight", "Restore group messaging"),
                            BotCommand("setflood", "Configure anti-flood limit"),
                            BotCommand("biolink", "Toggle bio-link guard"),
                            BotCommand("antiedit", "Toggle anti-edit protection"),
                            BotCommand("welcome", "Toggle welcome messages"),
                            BotCommand("setwelcome", "Set custom welcome message"),
                            BotCommand("save", "Save a group note"),
                            BotCommand("filter", "Add keyword auto-reply filter"),
                            BotCommand("setrules", "Set group rules"),
                            BotCommand("pin", "Pin replied message"),
                            BotCommand("unpin", "Unpin message"),
                            BotCommand("tagall", "Mention all group members"),
                            BotCommand("afk", "Mark yourself as AFK"),
                        ],
                        scope=BotCommandScopeAllChatAdministrators(),
                    ),
                    timeout=15,
                )
            except Exception:
                pass

        LOGGER(__name__).info(f"MusicBot Started as {self.name} (@{self.username})")

    async def stop(self, *args, **kwargs):
        await super().stop(*args, **kwargs)

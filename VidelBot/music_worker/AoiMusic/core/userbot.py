import asyncio
import os

from pyrogram import Client
from pyrogram.errors import ChatWriteForbidden
import config

from ..logging import LOGGER

assistants = []
assistantids = []


class Userbot(Client):
    def __init__(self):
        self.clients = []
        self.sessions = config.STRING_SESSIONS

        for i, session in enumerate(self.sessions, start=1):
            client = Client(
                f"AoiMusicString{i}",
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                in_memory=True,
                no_updates=True,
                workers=int(os.getenv("ASSISTANT_WORKERS", "4")),
                max_concurrent_transmissions=int(os.getenv("ASSISTANT_MAX_TRANSMISSIONS", "2")),
                session_string=session.strip(),
            )
            self.clients.append(client)

    async def _start(self, client, index):
        LOGGER(__name__).info("Starting Assistant Client #%d...", index)
        try:
            await asyncio.wait_for(client.start(), timeout=45)
            get_me = await asyncio.wait_for(client.get_me(), timeout=20)
            client.username = get_me.username
            client.id = get_me.id
            client.mention = get_me.mention
            client.name = f"{get_me.first_name or ''} {get_me.last_name or ''}".strip()
            assistants.append(index)
            assistantids.append(get_me.id)
            LOGGER(__name__).info("Assistant #%d Started as %s (@%s)", index, client.name, client.username)

            if config.LOGGER_ID:
                assistant_msg = f"""
╔══════════╗
  🤖 **ᴀssɪsᴛᴀɴᴛ sᴛᴀʀᴛᴇᴅ** 🤖
╚══════════╝

┌────────────┐
│ 🧑 **ɴᴀᴍᴇ :** {client.name}
│ 🔑 **ɪᴅ :** <code>{client.id}</code>
│ 🔗 **ᴜsᴇʀɴᴀᴍᴇ :** @{client.username}
│ 🔢 **ɪɴsᴛᴀɴᴄᴇ :** #{index}
│ 📡 **sᴛᴀᴛᴜs :** ✅ ᴀᴄᴛɪᴠᴇ
│ 🎵 **ʀᴏʟᴇ :** ᴠᴏɪᴄᴇᴄʜᴀᴛ ᴀssɪsᴛᴀɴᴛ
└────────────┘

⚡ **ʀᴇᴀᴅʏ ᴛᴏ ᴊᴏɪɴ ᴠᴏɪᴄᴇᴄʜᴀᴛs**
💎 **ᴘʀᴇᴍɪᴜᴍ sᴛʀᴇᴀᴍɪɴɢ ᴀᴄᴛɪᴠᴇ**
"""
                try:
                    await asyncio.wait_for(client.send_message(config.LOGGER_ID, assistant_msg), timeout=15)
                except ChatWriteForbidden:
                    try:
                        await asyncio.wait_for(client.join_chat(config.LOGGER_ID), timeout=15)
                        await asyncio.wait_for(client.send_message(config.LOGGER_ID, assistant_msg), timeout=15)
                    except Exception as ex:
                        LOGGER(__name__).warning(
                            "Assistant Account %d could not access log group (non-fatal): %s",
                            index,
                            type(ex).__name__,
                        )
                except Exception as ex:
                    LOGGER(__name__).warning(
                        "Assistant Account %d could not send log message (non-fatal): %s",
                        index,
                        type(ex).__name__,
                    )

        except Exception as e:
            LOGGER(__name__).error(
                "Assistant Account %d failed to start: %s: %s", index, type(e).__name__, e
            )

    async def start(self):
        if not self.clients:
            LOGGER(__name__).warning("No Assistant Clients configured.")
            return
        tasks = [self._start(client, i) for i, client in enumerate(self.clients, start=1)]
        await asyncio.gather(*tasks)

    async def stop(self):
        """Gracefully stop all clients."""
        tasks = [client.stop() for client in self.clients]
        await asyncio.gather(*tasks, return_exceptions=True)

    def __getattr__(self, name):
        if not self.clients:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        first_client = self.clients[0]
        if hasattr(first_client, name):
            return getattr(first_client, name)
        raise AttributeError(f"'{type(first_client).__name__}' object has no attribute '{name}'")

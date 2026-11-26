import asyncio
import ctypes
import gc
import importlib
import os
import time

if __name__ == "__main__" and os.environ.get("MUSIC_WORKER_STANDALONE", "").strip() != "1":
    raise SystemExit(
        "Refusing to start: set MUSIC_WORKER_STANDALONE=1 to confirm this "
        "is the only Music worker process."
    )

from pyrogram import idle
from pytgcalls.exceptions import NoActiveGroupCall

import config
from config import BANNED_USERS
from AoiMusic import HELPABLE, LOGGER, app, userbot
from AoiMusic.core.call import AoiCall
from AoiMusic.plugins import ALL_MODULES
from AoiMusic.utils.database import get_banned_users, get_gbanned
from AoiMusic.utils.premium import install_text_entities_patch, validate_db
from AoiMusic.utils.premium_emoji import patch_pyrogram_premium_emoji

install_text_entities_patch()
patch_pyrogram_premium_emoji()


async def init():
    if len(config.STRING_SESSIONS) == 0:
        LOGGER("VidelMusic").error(
            "No Assistant Clients Vars Defined!.. Exiting Process."
        )
        return
    if not config.SPOTIFY_CLIENT_ID and not config.SPOTIFY_CLIENT_SECRET:
        LOGGER("VidelMusic").warning(
            "No Spotify vars defined. Spotify links will be resolved without the official API, which can be slower and more fragile."
        )
    LOGGER("VidelMusic").info("Loading banned users from MongoDB...")
    try:
        users = await asyncio.wait_for(get_gbanned(), timeout=15)
        for user_id in users:
            BANNED_USERS.add(user_id)
        users = await asyncio.wait_for(get_banned_users(), timeout=15)
        for user_id in users:
            BANNED_USERS.add(user_id)
        LOGGER("VidelMusic").info("Loaded %d banned users.", len(BANNED_USERS))
    except Exception as exc:
        LOGGER("VidelMusic").warning("Skipped loading banned users (%s: %s)", type(exc).__name__, exc)

    await app.start()

    LOGGER("VidelMusic").info("Validating premium emoji database...")
    try:
        await asyncio.wait_for(validate_db(app), timeout=20)
    except Exception as exc:
        LOGGER("VidelMusic").warning("Could not validate premium emoji database (%s).", type(exc).__name__)

    for all_module in ALL_MODULES:
        imported_module = importlib.import_module(all_module)
        if hasattr(imported_module, "__MODULE__") and imported_module.__MODULE__:
            if hasattr(imported_module, "__HELP__") and imported_module.__HELP__:
                HELPABLE[imported_module.__MODULE__.lower()] = imported_module
    LOGGER("VidelMusic.plugins").info("Successfully Imported All Modules")

    await userbot.start()
    await AoiCall.start()
    LOGGER("VidelMusic").info("Assistant & PyTgCalls Started Successfully")

    if config.LOGGER_ID:
        try:
            await asyncio.wait_for(
                AoiCall.stream_call(
                    "https://files.catbox.moe/qv5a0a.mp4"
                ),
                timeout=25,
            )
        except NoActiveGroupCall:
            LOGGER("VidelMusic").warning(
                "No active voice call in log group (non-fatal — bot will still work in all chats)."
            )
        except Exception as exc:
            LOGGER("VidelMusic").warning("Startup media check skipped (%s)", type(exc).__name__)

    async def _memory_trim_loop():
        from AoiMusic.misc import db as _active_db

        libc = None
        try:
            libc = ctypes.CDLL("libc.so.6")
        except Exception:
            libc = None

        while True:
            await asyncio.sleep(300)
            try:
                # Evict stale local media files older than 20 minutes that are not in active queues
                dl_dir = "downloads"
                if os.path.isdir(dl_dir):
                    now = time.time()
                    in_use = {
                        str(entry.get("file", ""))
                        for q in _active_db.values()
                        for entry in (q or [])
                        if isinstance(entry, dict)
                    }
                    for fname in os.listdir(dl_dir):
                        fpath = os.path.join(dl_dir, fname)
                        if fpath in in_use or not os.path.isfile(fpath):
                            continue
                        try:
                            if now - os.path.getmtime(fpath) > 1200:
                                os.remove(fpath)
                        except OSError:
                            pass
                gc.collect()
                if libc is not None:
                    libc.malloc_trim(0)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    trim_task = asyncio.create_task(_memory_trim_loop())
    gc.collect()
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass

    LOGGER("VidelMusic").info("Videl Music started successfully")
    await idle()
    trim_task.cancel()
    await app.stop()
    await userbot.stop()


if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_until_complete(init())
    LOGGER("VidelMusic").info("Stopping Videl Music. Goodbye")

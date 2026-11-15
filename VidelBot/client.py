"""The single WzGram (Pyrogram-compatible) client shared by every Videl module."""
import logging

import pyrogram
from pyrogram import Client, raw

import config

_log = logging.getLogger("videl.client")


def runtime_stack() -> dict:
    """Inspect active MTProto, crypto, and rich-UI libraries."""
    is_wzgram = False
    wz_ver = getattr(pyrogram, "__version__", "unknown")
    try:
        import wzgram
        is_wzgram = pyrogram is wzgram or getattr(wzgram, "__version__", "") == wz_ver
    except Exception:
        pass
    has_warp = False
    warp_ver = ""
    try:
        import importlib.metadata as _md
        warp_ver = _md.version("WarpCrypto")
        has_warp = True
    except Exception:
        pass
    has_richgram = False
    rich_avail = False
    rich_ver = ""
    try:
        import richgram
        has_richgram = True
        rich_avail = bool(getattr(richgram, "RICH_AVAILABLE", False))
        rich_ver = getattr(richgram, "__version__", "0.2.0")
    except Exception:
        pass
    has_aiogram = False
    aiogram_ver = ""
    try:
        import aiogram
        has_aiogram = True
        aiogram_ver = getattr(aiogram, "__version__", "")
    except Exception:
        pass
    layer = getattr(getattr(raw, "all", None), "layer", 0)
    if not is_wzgram:
        _log.warning(
            "pyrogram is %s (layer %s) rather than wzgram – if richgram pulled kurigram, run: "
            "pip uninstall -y kurigram && pip install --no-deps --force-reinstall wzgram==3.1.6",
            wz_ver,
            layer,
        )
    return {
        "engine": "WzGram" if is_wzgram else "Pyrogram",
        "is_wzgram": is_wzgram,
        "version": wz_ver,
        "layer": layer,
        "warpcrypto": warp_ver if has_warp else None,
        "richgram": rich_ver if has_richgram else None,
        "rich_available": rich_avail,
        "native_rich": bool(is_wzgram and hasattr(Client, "send_rich_message")),
        "botapi_engine": "aiogram" if has_aiogram else None,
        "botapi_version": aiogram_ver if has_aiogram else None,
    }


app = Client(
    name="Videl",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN,
    workers=config.TG_BOT_WORKERS,
    sleep_threshold=30,
    max_concurrent_transmissions=5,
    in_memory=False,
)

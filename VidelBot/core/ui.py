"""Small UI helpers shared by every Videl module (no handlers in here)."""
from pyrogram.types import InlineKeyboardButton

from config import CONTACT_URL, SUPPORT_URL, UPDATES_URL


def contact_row(label: str = "📞 Contact Admin"):
    """A one-button row linking to CONTACT_URL, or [] when it isn't configured."""
    return [InlineKeyboardButton(label, url=CONTACT_URL)] if CONTACT_URL else []


def links_row():
    """Optional Updates / Support buttons (only the ones that are configured)."""
    row = []
    if UPDATES_URL:
        row.append(InlineKeyboardButton("📢 Updates", url=UPDATES_URL))
    if SUPPORT_URL:
        row.append(InlineKeyboardButton("💬 Support", url=SUPPORT_URL))
    return row


def rows(*maybe_rows):
    """Build a keyboard, silently dropping empty rows."""
    return [r for r in maybe_rows if r]


def humanbytes(size) -> str:
    if not size:
        return "0 B"
    size = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


async def upload_to_host(path: str):
    """
    Upload a local file and return a public URL (or None).
    Uses freeimage.host when FREEIMAGE_API_KEY is set (images only),
    otherwise the anonymous catbox.moe API (≤ 200 MB).
    """
    import logging
    import os

    import aiohttp

    from config import FREEIMAGE_API_KEY

    log = logging.getLogger("videl.upload")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as s:
            if FREEIMAGE_API_KEY and path.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                with open(path, "rb") as f:
                    form = aiohttp.FormData()
                    form.add_field("key", FREEIMAGE_API_KEY)
                    form.add_field("source", f, filename=os.path.basename(path))
                    async with s.post("https://freeimage.host/api/1/upload", data=form) as r:
                        if r.status == 200:
                            j = await r.json(content_type=None)
                            url = (j.get("image") or {}).get("url")
                            if url:
                                return url
            with open(path, "rb") as f:
                form = aiohttp.FormData()
                form.add_field("reqtype", "fileupload")
                form.add_field("fileToUpload", f, filename=os.path.basename(path))
                async with s.post("https://catbox.moe/user/api.php", data=form) as r:
                    text = (await r.text()).strip()
                    if r.status == 200 and text.startswith("http"):
                        return text
                    log.warning(f"catbox upload failed: {r.status} {text[:100]}")
    except Exception as e:
        log.warning(f"upload_to_host failed: {e}")
    return None

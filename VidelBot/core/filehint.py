"""
"What can I do with this file?" – the answer to a video / file nobody else handled.

Every tool needs a command (/dl, /compress …) or a saved auto-rename template, so a new user who just
sent a video used to get total silence. This handler is registered LAST in group 0 (see run.load_plugins):
inside a group only the first matching handler runs, so it fires only when auto-compress, auto-rename,
clone creation and every other file flow passed. Flows that wait for a file in group -1 (mux / merge)
stop propagation, so they never reach it either.

One hint per user per HINT_COOLDOWN, one per album.
"""
import time

from pyrogram import filters
from pyrogram.handlers import MessageHandler

from core.style import hdr, hint, quote, sc

HINT_COOLDOWN = 600
_last: dict = {}           # user id → time of the last hint
_albums: dict = {}         # media_group_id → time (one hint per album)


def _busy(uid: int) -> bool:
    """True while the user is in a flow that may still want this file."""
    try:
        from renamer.handlers import _get_state
        if _get_state(uid):
            return True
    except Exception:
        pass
    try:
        from filestore.main_bot.plugins.create_bot import _creation_state
        if uid in _creation_state:
            return True
    except Exception:
        pass
    return False


def hint_text(kind: str) -> str:
    video = kind in ("video", "document")
    lines = []
    if video:
        lines += [
            f"🗜 /compress – {sc('shrink it (480p · 720p · 1080p)')}",
            f"🎞 /dl – {sc('encode with your settings')}",
            f"🔁 /convert – {sc('change the format')}",
            f"🎧 /mux – {sc('add a subtitle or audio track')}",
            "🖼 /screens · ✂️ /trim · 🧪 /sample",
        ]
    lines += [
        f"✏️ <code>/rename New Name.mkv</code> – {sc('rename once')}",
        f"🤖 /autorename – {sc('rename every file automatically')}",
        f"ℹ️ /mediainfo – {sc('tracks, codecs and size')}",
    ]
    return (hdr("📎", "Got your file", "what should I do with it?") + "\n\n"
            + quote("\n".join(lines)) + "\n\n"
            + hint("Reply to the file with one of these commands."))


async def file_hint(client, message):
    user = message.from_user
    if not user or user.is_bot:
        return
    now = time.monotonic()
    group = getattr(message, "media_group_id", None)
    if group:
        if group in _albums:
            return
        _albums[group] = now
    if now - _last.get(user.id, -HINT_COOLDOWN) < HINT_COOLDOWN or _busy(user.id):
        return
    _last[user.id] = now
    if len(_last) > 5000:                                     # forget old entries
        for d in (_last, _albums):
            for k in [k for k, t in d.items() if now - t > HINT_COOLDOWN]:
                d.pop(k, None)
    kind = "video" if message.video else "document" if message.document else "audio"
    if kind == "document":
        name = (getattr(message.document, "file_name", "") or "").lower()
        mime = getattr(message.document, "mime_type", "") or ""
        if not (mime.startswith("video/") or name.endswith((".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".ts"))):
            kind = "file"
    try:
        await message.reply_text(hint_text(kind), quote=True, disable_web_page_preview=True)
    except Exception:
        pass


FILTER = (filters.private & filters.incoming & (filters.video | filters.document | filters.audio)
          & ~filters.via_bot)


def register(app) -> int:
    """Called by run.load_plugins after every plugin – must be the last group-0 handler."""
    app.add_handler(MessageHandler(file_hint, FILTER), 0)
    return 1

"""Title/URL resolution for /play <song> (stage 3).

Uses yt-dlp (this package's own requirements.txt — never the main
bot's) to turn a search phrase or a supported link into a direct
audio stream URL that the player can hand to ffmpeg. Resolution runs
off the event loop; failures return None and the handlers say so
honestly instead of queueing a phantom track.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

_YDL_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "format": "bestaudio/best",
    "socket_timeout": 15,
}


def resolver_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:
        return False


def _extract(query: str, extractor_factory=None) -> dict | None:
    """Blocking yt-dlp extraction; returns {title, duration, source}."""
    if extractor_factory is None:
        from yt_dlp import YoutubeDL
        extractor_factory = YoutubeDL
    target = query if "://" in query else f"ytsearch1:{query}"
    try:
        with extractor_factory(dict(_YDL_OPTS)) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception:
        log.warning("track resolution failed", exc_info=True)
        return None
    if not info:
        return None
    if info.get("entries"):  # search result page → first hit
        info = next((e for e in info["entries"] if e), None)
        if not info:
            return None
    source = info.get("url")
    if not source:  # pick the best audio-only format explicitly
        formats = [f for f in info.get("formats") or [] if f.get("url") and f.get("vcodec") == "none"]
        if formats:
            source = formats[-1]["url"]
    if not source:
        return None
    return {
        "title": str(info.get("title") or query)[:120],
        "duration": info.get("duration"),
        "source": source,
    }


async def resolve_title(query: str, extractor_factory=None) -> dict | None:
    """Resolve a song title or URL to a streamable track, or None."""
    query = (query or "").strip()
    if not query:
        return None
    if extractor_factory is None and not resolver_available():
        return None
    return await asyncio.to_thread(_extract, query, extractor_factory)

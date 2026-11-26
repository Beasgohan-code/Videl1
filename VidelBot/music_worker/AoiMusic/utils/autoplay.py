"""
Autoplay & Relative Suggestions - picks related songs when the queue is about to end or becomes empty.

How the related songs are chosen
--------------------------------
1. YouTube "Mix" (RD<video_id>) of the song that is playing / just finished
   -> YouTube's own "related songs" list (best source).
2. Smart searches built from the artist / movie parts of the title
   (e.g. "Song | Movie | Artist" -> "Artist song", "Movie song").
3. A few trending fallback searches (last resort).
"""

import asyncio
import logging
import random
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

from py_yt import VideosSearch

import config
from AoiMusic.misc import db
from AoiMusic.utils.database import is_autoplay
from AoiMusic.utils.stream.queue import put_queue

try:  # py_yt already depends on httpx
    import httpx
except Exception:  # pragma: no cover
    httpx = None
try:
    import aiohttp
except Exception:  # pragma: no cover
    aiohttp = None

LOGGER = logging.getLogger("AoiMusic.autoplay")

# (all key words of a title, key words of the "song name" part if it can be guessed)
Sig = Tuple[frozenset, frozenset]

autoplay_history: Dict[int, List[str]] = {}  # video ids played / suggested, per chat
autoplay_titles: Dict[int, List[Sig]] = {}   # title signatures, catches re-uploads of a song
relative_suggestions_cache: Dict[str, dict] = {}  # vidid -> track details dict

_NON_YT_IDS = ("telegram", "soundcloud", "index_")
_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
def _cfg(name: str, default):
    return getattr(config, name, default)


def _limits() -> Tuple[int, int]:
    """(min_seconds, max_seconds) allowed for an autoplay song."""
    try:
        lo = int(_cfg("AUTOPLAY_MIN_DURATION", 60))
        hi = int(_cfg("AUTOPLAY_MAX_DURATION", 600))
        limit = int(_cfg("DURATION_LIMIT", 0) or 0)
    except Exception:
        lo, hi, limit = 60, 600, 0
    if limit > 0:
        hi = min(hi, limit)
    if lo >= hi:
        lo = 0
    return lo, hi


def _history_size() -> int:
    try:
        return max(5, int(_cfg("AUTOPLAY_HISTORY_SIZE", 40)))
    except Exception:
        return 40


def _fallback_queries() -> List[str]:
    custom = _cfg("AUTOPLAY_FALLBACK_QUERIES", None)
    if custom:
        return [str(q) for q in custom]
    year = datetime.now().year
    return [
        f"new hindi song {year}",
        "bollywood hit song",
        "romantic hindi song",
        "latest bollywood song",
    ]


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def _time_to_sec(time_str: str) -> int:
    if not time_str or str(time_str).lower() == "none":
        return 0
    try:
        return sum(
            int(x) * (60**i)
            for i, x in enumerate(reversed(str(time_str).strip().split(":")))
        )
    except Exception:
        return 0


_BRACKET_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_BRACKET_EXTRA_RE = re.compile(
    r"[\(\[\{]\s*(?:from|ost|feat\.?|ft\.?|featuring|with|prod\.?)\s*[:\-]?\s*[\"“”']?"
    r"([^\)\]\}\"“”]+)",
    re.IGNORECASE,
)
_SEPARATOR_RE = re.compile(r"\s*[|•·~]\s*|\s+[-–—]\s+|:\s+|\s+/\s+")
_PIPE_RE = re.compile(r"[|•·]")
_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")
_URL_RE = re.compile(r"(?i)^\s*(?:https?://|www\.)")
_VIDEO_JUNK_RE = re.compile(
    r"\b(?:official|music|lyrics?|lyrical|full|videos?|audio|song|hd|4k|1080p|hq|"
    r"remastered|visuali[sz]er)\b",
    re.IGNORECASE,
)

_IGNORE_TOKENS = frozenset(
    (
        "official music video videos audio song songs full lyrics lyric lyrical hd 4k 1080p hq "
        "remastered remaster visualizer visualiser version new latest hindi english punjabi "
        "haryanvi bhojpuri tamil telugu "
        "slowed reverb remix cover instrumental nightcore acoustic unplugged reprise lofi 8d dj "
        "the an of and in on to from by with for ft feat featuring prod vs"
    ).split()
)
_FLAVOUR_TOKENS = frozenset(
    "slowed reverb remix cover instrumental nightcore acoustic unplugged reprise lofi 8d dj".split()
)

_BLOCKED_TITLE_RE = re.compile(
    r"\b(?:"
    r"jukebox|juke\s*box|non[\s-]?stop|playlist|mash[\s-]?ups?|mega\s*mix|mix(?:tape)?|"
    r"songs|hits|collection|compilation|top\s*\d+|best\s+of|greatest\s+hits|"
    r"\d+\s*(?:hours?|hrs?)|24\s*/\s*7|live\s*stream(?:ing)?|"
    r"karaoke|ringtone|whatsapp\s+status|shorts|"
    r"podcast|interview|reaction|reacts?|review|trailer|teaser|"
    r"full\s+(?:album|movie)|audiobook|tutorial|episode"
    r")\b",
    re.IGNORECASE,
)

_EXCLUDE = "-jukebox -nonstop -mashup -playlist"


def _normalize(text) -> str:
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"(?i)\blo[\s-]?fi\b", "lofi", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokenize(text: str) -> List[str]:
    out: List[str] = []
    cur: List[str] = []
    for ch in text.lower():
        if ch.isalnum() or unicodedata.category(ch).startswith("M"):
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _key_tokens(text: str) -> frozenset:
    out = []
    for tok in _tokenize(_normalize(text)):
        if tok in _IGNORE_TOKENS or _YEAR_RE.match(tok):
            continue
        if len(tok) == 1 and tok.isascii() and tok.isalpha():
            continue
        out.append(tok)
    return frozenset(out)


def _flavour(title: str) -> frozenset:
    return frozenset(_tokenize(_normalize(title))) & _FLAVOUR_TOKENS


def _segments(title: str) -> Tuple[List[str], List[str]]:
    text = _normalize(title)
    extras = [m.group(1).strip(" \"'“”") for m in _BRACKET_EXTRA_RE.finditer(text)]
    main = _BRACKET_RE.sub(" ", text)
    segs = [s.strip() for s in _SEPARATOR_RE.split(main)]
    return segs, extras


def _signature(title: str) -> Sig:
    tokens = _key_tokens(title)
    segs = [s for s in _segments(title)[0] if _key_tokens(s)]
    name: frozenset = frozenset()
    if len(segs) >= 2 and (len(segs) >= 3 or _PIPE_RE.search(title or "")):
        name = _key_tokens(segs[0])
    return tokens, name


def _distinctive(words: frozenset) -> bool:
    return len(words) >= 2 or any(len(w) >= 5 for w in words)


def _same_song(a: Sig, b: Sig) -> bool:
    ta, na = a
    tb, nb = b
    if not ta or not tb:
        return False
    common = ta & tb
    if not common:
        return False
    if len(common) / len(ta | tb) >= 0.7:
        return True
    if len(common) / min(len(ta), len(tb)) >= 0.85 and _distinctive(common):
        return True
    if na and na <= tb and _distinctive(na):
        return True
    if nb and nb <= ta and _distinctive(nb):
        return True
    return False


# --------------------------------------------------------------------------- #
# History
# --------------------------------------------------------------------------- #
def record_played_track(chat_id: int, vidid: str, title: Optional[str] = None):
    size = _history_size()
    if vidid and vidid not in _NON_YT_IDS:
        hist = autoplay_history.setdefault(chat_id, [])
        if vidid not in hist:
            hist.append(vidid)
        del hist[:-size]
    if title and not _URL_RE.match(str(title)):
        sig = _signature(str(title))
        if sig[0]:
            titles = autoplay_titles.setdefault(chat_id, [])
            if sig not in titles:
                titles.append(sig)
            del titles[:-size]


# --------------------------------------------------------------------------- #
# Candidates: filtering + scoring
# --------------------------------------------------------------------------- #
@dataclass
class _Ctx:
    sig: Sig
    flavour: frozenset
    seen: Set[str]
    history: List[Sig]
    min_sec: int
    max_sec: int


def _make_candidate(
    vidid,
    title,
    dur_min,
    source: str,
    rank: int,
    thumb: Optional[str] = None,
    channel: str = "",
    qbonus: float = 0.0,
) -> Optional[dict]:
    if not vidid or not title or not dur_min or str(dur_min).strip().lower() == "none":
        return None
    dur_sec = _time_to_sec(dur_min)
    if dur_sec <= 0:
        return None
    return {
        "vidid": str(vidid),
        "title": str(title),
        "duration_min": str(dur_min),
        "duration_sec": dur_sec,
        "thumb": thumb or f"https://i.ytimg.com/vi/{vidid}/hqdefault.jpg",
        "channel": channel or "",
        "source": source,
        "rank": rank,
        "qbonus": qbonus,
    }


def _reject_reason(cand: dict, ctx: _Ctx) -> Optional[str]:
    if cand["vidid"] in ctx.seen:
        return "already played"
    if not (ctx.min_sec <= cand["duration_sec"] <= ctx.max_sec):
        return f"duration {cand['duration_sec']}s"
    if _BLOCKED_TITLE_RE.search(cand["title"]):
        return "playlist / compilation / not a single song"
    sig = cand["sig"]
    if not sig[0]:
        return "empty title"
    if _same_song(sig, ctx.sig):
        return "same song as the one that just played"
    for old in ctx.history:
        if _same_song(sig, old):
            return "same song was already played"
    return None


def _score(cand: dict, ctx: _Ctx) -> float:
    score = min(len(cand["sig"][0] & ctx.sig[0]), 4) * 1.5
    if cand["source"] == "mix":
        score += 8.0 - min(cand["rank"], 28) * 0.25
    else:
        score += 3.0 - min(cand["rank"], 14) * 0.2 + cand["qbonus"]
    flav = _flavour(cand["title"])
    score += 1.0 * len(flav & ctx.flavour) - 2.0 * len(flav - ctx.flavour)
    if 120 <= cand["duration_sec"] <= 420:
        score += 0.5
    return score


def _pick_best(cands: List[dict], ctx: _Ctx) -> Optional[dict]:
    valid: List[dict] = []
    for cand in cands:
        cand["sig"] = _signature(cand["title"])
        why = _reject_reason(cand, ctx)
        if why:
            LOGGER.debug("autoplay: skip %r (%s)", cand["title"], why)
            continue
        cand["score"] = _score(cand, ctx)
        valid.append(cand)
    if not valid:
        return None
    valid.sort(key=lambda c: c["score"], reverse=True)
    best = valid[0]["score"]
    top = [c for c in valid[:3] if c["score"] >= best - 1.5]
    return random.choice(top)


def _pick_top_k(cands: List[dict], ctx: _Ctx, k: int = 5) -> List[dict]:
    valid: List[dict] = []
    for cand in cands:
        cand["sig"] = _signature(cand["title"])
        why = _reject_reason(cand, ctx)
        if why:
            continue
        cand["score"] = _score(cand, ctx)
        valid.append(cand)
    valid.sort(key=lambda c: c["score"], reverse=True)
    chosen: List[dict] = []
    chosen_sigs: List[Sig] = []
    chosen_ids: Set[str] = set()
    for cand in valid:
        if cand["vidid"] in chosen_ids:
            continue
        if any(_same_song(cand["sig"], s) for s in chosen_sigs):
            continue
        chosen.append(cand)
        chosen_sigs.append(cand["sig"])
        chosen_ids.add(cand["vidid"])
        if len(chosen) >= k:
            break
    return chosen


def _merge(batches: List[List[dict]]) -> List[dict]:
    merged: Dict[str, dict] = {}
    for batch in batches:
        for cand in batch:
            merged.setdefault(cand["vidid"], cand)
    return list(merged.values())


# --------------------------------------------------------------------------- #
# Source 1: YouTube Mix (= YouTube's own "related songs")
# --------------------------------------------------------------------------- #
_YT_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_YT_NEXT_URL = f"https://www.youtube.com/youtubei/v1/next?key={_YT_KEY}&prettyPrint=false"
_YT_CLIENT_VERSION = "2.20250101.00.00"


async def _post_json(url: str, payload: dict, timeout: float = 8.0) -> Optional[dict]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Origin": "https://www.youtube.com",
        "X-YouTube-Client-Name": "1",
        "X-YouTube-Client-Version": _YT_CLIENT_VERSION,
        "Accept-Language": "en-US,en;q=0.9",
    }
    if httpx is not None:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    if aiohttp is not None:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.json(content_type=None)
    return None


def _yt_text(node) -> str:
    if isinstance(node, str):
        return node
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return str(node["simpleText"])
    runs = node.get("runs")
    if isinstance(runs, list):
        return "".join(str(r.get("text", "")) for r in runs if isinstance(r, dict))
    return ""


async def _fetch_mix(vidid: str) -> List[dict]:
    payload = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": _YT_CLIENT_VERSION,
                "hl": "en",
                "gl": str(_cfg("AUTOPLAY_REGION", "IN")),
            }
        },
        "videoId": vidid,
        "playlistId": f"RD{vidid}",
    }
    data = await _post_json(_YT_NEXT_URL, payload)
    try:
        items = data["contents"]["twoColumnWatchNextResults"]["playlist"]["playlist"]["contents"]
    except (KeyError, TypeError):
        return []

    out: List[dict] = []
    for rank, entry in enumerate(items):
        if not isinstance(entry, dict):
            continue
        renderer = entry.get("playlistPanelVideoRenderer")
        if renderer is None:
            wrapper = entry.get("playlistPanelVideoWrapperRenderer") or {}
            renderer = (wrapper.get("primaryRenderer") or {}).get("playlistPanelVideoRenderer")
        if not isinstance(renderer, dict):
            continue
        cand = _make_candidate(
            renderer.get("videoId"),
            _yt_text(renderer.get("title")),
            _yt_text(renderer.get("lengthText")),
            "mix",
            rank,
            channel=_yt_text(renderer.get("shortBylineText") or renderer.get("longBylineText")),
        )
        if cand:
            out.append(cand)
    return out


# --------------------------------------------------------------------------- #
# Source 2 / 3: YouTube search
# --------------------------------------------------------------------------- #
async def _search(query: str, source: str = "search", qbonus: float = 0.0) -> List[dict]:
    try:
        res = await asyncio.wait_for(VideosSearch(query, limit=15).next(), timeout=15)
    except Exception as exc:
        LOGGER.warning("autoplay: search %r failed (%s: %s)", query, type(exc).__name__, exc)
        return []
    out: List[dict] = []
    for rank, item in enumerate((res or {}).get("result") or []):
        if not isinstance(item, dict):
            continue
        thumbs = item.get("thumbnails") or []
        thumb = None
        if thumbs and isinstance(thumbs[-1], dict) and thumbs[-1].get("url"):
            thumb = thumbs[-1]["url"].split("?")[0]
        channel = item.get("channel")
        cand = _make_candidate(
            item.get("id"),
            item.get("title"),
            item.get("duration"),
            source,
            rank,
            thumb=thumb,
            channel=channel.get("name", "") if isinstance(channel, dict) else "",
            qbonus=qbonus,
        )
        if cand:
            out.append(cand)
    return out


def _clean_part(text: str) -> str:
    text = _VIDEO_JUNK_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip(" -–—|•·~:,.\"'“”")


def _clean_song_query(title: str) -> str:
    text = _BRACKET_RE.sub(" ", _normalize(title))
    return _clean_part(text) or str(title or "").strip()


def _build_queries(title: str) -> List[str]:
    segs, extras = _segments(title)
    taken: Set[frozenset] = set()

    def uniq(parts: List[str]) -> List[str]:
        out: List[str] = []
        for part in parts:
            part = _clean_part(part)
            key = _key_tokens(part)
            if part and key and key not in taken:
                taken.add(key)
                out.append(part)
        return out

    main = uniq(segs)
    extra = uniq(extras)
    if len(main) >= 3 or (len(main) == 2 and _PIPE_RE.search(title or "")):
        others = list(reversed(main[1:]))
        if len(others) > 3:
            plain = [o for o in others if "," not in o]
            if len(plain) >= 2:
                others = plain
        others += extra
    elif len(main) == 2:
        others = main + extra
    elif extra:
        others = extra
    else:
        others = main
    return [f"{part} song {_EXCLUDE}" for part in others[:3]]


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def _format_candidate_dict(cand: dict) -> dict:
    item = {
        "title": cand["title"],
        "duration_min": cand["duration_min"],
        "duration_sec": cand["duration_sec"],
        "thumb": cand["thumb"],
        "vidid": cand["vidid"],
        "link": f"https://www.youtube.com/watch?v={cand['vidid']}",
        "channel": cand.get("channel", "") or "YouTube Music",
        "source": cand.get("source", "mix"),
    }
    if len(relative_suggestions_cache) > 100:
        for _old_k in list(relative_suggestions_cache.keys())[:40]:
            relative_suggestions_cache.pop(_old_k, None)
    relative_suggestions_cache[item["vidid"]] = item
    return item


def _finalize(chat_id: int, last_title: str, cand: dict) -> dict:
    record_played_track(chat_id, cand["vidid"], cand["title"])
    LOGGER.info(
        "[autoplay] chat=%s | %r -> %r (%s, %ss, score %.1f)",
        chat_id,
        last_title,
        cand["title"],
        cand["source"],
        cand["duration_sec"],
        cand["score"],
    )
    return _format_candidate_dict(cand)


async def get_relative_suggestions(
    chat_id: int, current_title: str, current_vidid: str, count: int = 5
) -> List[dict]:
    """Fetch `count` (default 5) related songs for the currently playing song."""
    current_title = "" if _URL_RE.match(current_title or "") else str(current_title or "")
    current_vidid = str(current_vidid or "")
    record_played_track(chat_id, current_vidid, current_title)

    lo, hi = _limits()
    seen = set(autoplay_history.get(chat_id, []))
    if current_vidid:
        seen.add(current_vidid)
    ctx = _Ctx(
        sig=_signature(current_title),
        flavour=_flavour(current_title),
        seen=seen,
        history=list(autoplay_titles.get(chat_id, [])),
        min_sec=lo,
        max_sec=hi,
    )

    pool: List[dict] = []

    # 1) YouTube Mix
    if _cfg("AUTOPLAY_USE_YT_MIX", True) and _YT_ID_RE.match(current_vidid):
        try:
            mix_items = await _fetch_mix(current_vidid)
            pool.extend(mix_items)
        except Exception as exc:
            LOGGER.warning("relative_suggestions: YouTube Mix failed (%s: %s)", type(exc).__name__, exc)

    picks = _pick_top_k(pool, ctx, k=count)
    if len(picks) >= count:
        return [_format_candidate_dict(c) for c in picks[:count]]

    # 2) Smart artist / movie searches
    queries = _build_queries(current_title)
    if queries:
        batches = await asyncio.gather(
            *[_search(q, qbonus=max(0.0, 1.5 - 0.5 * i)) for i, q in enumerate(queries)]
        )
        pool = _merge([pool] + list(batches))
        picks = _pick_top_k(pool, ctx, k=count)
        if len(picks) >= count:
            return [_format_candidate_dict(c) for c in picks[:count]]

    # 3) Fallback queries if still fewer than `count`
    fallback = _fallback_queries()
    random.shuffle(fallback)
    batches = await asyncio.gather(*[_search(q, source="fallback") for q in fallback[:2]])
    pool = _merge([pool] + list(batches))
    picks = _pick_top_k(pool, ctx, k=count)
    if len(picks) < count:
        picked_ids = {c["vidid"] for c in picks}
        if current_vidid:
            picked_ids.add(current_vidid)
        for cand in pool:
            if cand["vidid"] not in picked_ids and cand.get("duration_sec", 0) >= 30:
                picks.append(cand)
                picked_ids.add(cand["vidid"])
                if len(picks) >= count:
                    break
    return [_format_candidate_dict(c) for c in picks[:count]]


async def get_next_suggested_track(
    chat_id: int, last_title: str, last_vidid: str
) -> Optional[dict]:
    """Find a related song (no repeat, no playlist / jukebox) for the finishing track."""
    last_title = "" if _URL_RE.match(last_title or "") else str(last_title or "")
    last_vidid = str(last_vidid or "")
    record_played_track(chat_id, last_vidid, last_title)

    lo, hi = _limits()
    seen = set(autoplay_history.get(chat_id, []))
    if last_vidid:
        seen.add(last_vidid)
    ctx = _Ctx(
        sig=_signature(last_title),
        flavour=_flavour(last_title),
        seen=seen,
        history=list(autoplay_titles.get(chat_id, [])),
        min_sec=lo,
        max_sec=hi,
    )

    # 1) YouTube Mix of the song that just played
    if _cfg("AUTOPLAY_USE_YT_MIX", True) and _YT_ID_RE.match(last_vidid):
        try:
            pick = _pick_best(await _fetch_mix(last_vidid), ctx)
        except Exception as exc:
            LOGGER.warning("autoplay: YouTube Mix failed (%s: %s)", type(exc).__name__, exc)
            pick = None
        if pick:
            return _finalize(chat_id, last_title, pick)

    # 2) artist / movie based searches
    queries = _build_queries(last_title)
    if queries:
        batches = await asyncio.gather(
            *[_search(q, qbonus=max(0.0, 1.5 - 0.5 * i)) for i, q in enumerate(queries)]
        )
        pick = _pick_best(_merge(list(batches)), ctx)
        if pick:
            return _finalize(chat_id, last_title, pick)

    # 3) trending fallback (not related, but better than silence)
    fallback = _fallback_queries()
    random.shuffle(fallback)
    for i in range(0, len(fallback), 2):
        batches = await asyncio.gather(
            *[_search(q, source="fallback") for q in fallback[i : i + 2]]
        )
        pick = _pick_best(_merge(list(batches)), ctx)
        if pick:
            return _finalize(chat_id, last_title, pick)

    LOGGER.info("[autoplay] chat=%s | nothing suitable found after %r", chat_id, last_title)
    return None


async def enqueue_autoplay_if_enabled(chat_id: int, popped: Optional[dict]) -> bool:
    """If queue is empty and autoplay is enabled for chat_id, fetch a suggested song and put it in db[chat_id]."""
    if not popped:
        return False
    try:
        if not await is_autoplay(chat_id):
            return False
        suggested = await get_next_suggested_track(
            chat_id,
            popped.get("title", ""),
            popped.get("vidid", ""),
        )
        if not suggested:
            return False
        if chat_id not in db or db[chat_id] is None:
            db[chat_id] = []
        await put_queue(
            chat_id,
            popped.get("chat_id", chat_id),
            f"vid_{suggested['vidid']}",
            suggested["title"],
            suggested["duration_min"],
            "🔄 ᴀᴜᴛᴏᴘʟᴀʏ (sᴜɢɢᴇsᴛᴇᴅ)",
            suggested["vidid"],
            popped.get("user_id", 0),
            popped.get("streamtype", "audio"),
        )
        return bool(db.get(chat_id))
    except Exception:
        LOGGER.warning("autoplay: could not enqueue a suggested track", exc_info=True)
        return False

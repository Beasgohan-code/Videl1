"""Isolated AniList lookup features for the clone Group Management worker.

Uses the public, fixed AniList GraphQL endpoint with bounded requests and
process-local caching. Search text is sent only as a GraphQL variable; it is
never interpolated into query text or treated as a URL.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone

import httpx
from pyrogram import filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

_ENDPOINT = "https://graphql.anilist.co"
_TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_CACHE_TTL = 60.0
_CACHE_LIMIT = 100
_MEDIA_FIELDS = """
  id type format status isAdult
  title { romaji english native }
  description(asHtml: false)
  siteUrl
  coverImage { large }
  episodes chapters volumes averageScore
  genres
  tags { name rank isMediaSpoiler isGeneralSpoiler }
  startDate { year month day }
  nextAiringEpisode { airingAt timeUntilAiring episode }
"""
_SEARCH_MEDIA = f"""
query SearchMedia($search: String!, $type: MediaType!) {{
  Media(search: $search, type: $type, isAdult: false) {{ {_MEDIA_FIELDS} }}
}}
"""
_SEARCH_CHARACTER = """
query SearchCharacter($search: String!) {
  Character(search: $search) {
    id name { full native alternative }
    description(asHtml: false) siteUrl image { large } favourites
  }
}
"""
_GENRES = "query { GenreCollection }"
_BROWSE = f"""
query BrowseMedia($page: Int!, $sort: [MediaSort!]!, $status: MediaStatus) {{
  Page(page: $page, perPage: 5) {{
    media(type: ANIME, isAdult: false, sort: $sort, status: $status) {{ {_MEDIA_FIELDS} }}
  }}
}}
"""


class AniListError(Exception):
    """Safe public error for failed or malformed AniList responses."""


class AniListClient:
    """Small bounded GraphQL client; one instance is created per clone worker."""

    def __init__(self, *, transport=None):
        self._transport = transport
        self._cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._semaphore = asyncio.Semaphore(3)
        self._request_lock = asyncio.Lock()
        self._last_request_at = 0.0

    async def _graphql(self, query: str, variables: dict | None = None):
        cache_key = json.dumps([query, variables or {}], sort_keys=True, separators=(",", ":"))
        now = time.monotonic()
        cached = self._cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_TTL:
            self._cache.move_to_end(cache_key)
            return cached[1]
        async with self._semaphore:
            async with self._request_lock:
                # Coalesce concurrent identical lookups after the first request fills cache.
                cached = self._cache.get(cache_key)
                if cached and time.monotonic() - cached[0] < _CACHE_TTL:
                    self._cache.move_to_end(cache_key)
                    return cached[1]
                delay = 0.8 - (time.monotonic() - self._last_request_at)
                if delay > 0:
                    await asyncio.sleep(delay)
                self._last_request_at = time.monotonic()
                try:
                    async with httpx.AsyncClient(transport=self._transport, timeout=_TIMEOUT) as client:
                        response = await client.post(
                            _ENDPOINT,
                            json={"query": query, "variables": variables or {}},
                            headers={"Accept": "application/json", "Content-Type": "application/json"},
                        )
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError) as exc:
                    raise AniListError("AniList request failed") from exc
        if not isinstance(payload, dict) or payload.get("errors") or not isinstance(payload.get("data"), dict):
            raise AniListError("AniList returned an invalid response")
        data = payload["data"]
        self._cache[cache_key] = (time.monotonic(), data)
        self._cache.move_to_end(cache_key)
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return data

    async def media(self, search: str, media_type: str) -> dict | None:
        search = _clean_search(search)
        if not search:
            return None
        if media_type not in ("ANIME", "MANGA"):
            raise ValueError("Unsupported media type")
        return (await self._graphql(_SEARCH_MEDIA, {"search": search, "type": media_type})).get("Media")

    async def character(self, search: str) -> dict | None:
        search = _clean_search(search)
        if not search:
            return None
        return (await self._graphql(_SEARCH_CHARACTER, {"search": search})).get("Character")

    async def genres(self) -> list[str]:
        result = (await self._graphql(_GENRES)).get("GenreCollection")
        return [str(name) for name in result if isinstance(name, str)][:80] if isinstance(result, list) else []

    async def browse(self, category: str, page: int = 1) -> list[dict]:
        sort_by, status = _browse_filter(category)
        page = min(100, max(1, int(page)))
        result = await self._graphql(_BROWSE, {
            "page": page, "sort": [sort_by], "status": status,
        })
        nodes = ((result.get("Page") or {}).get("media") or [])
        return [item for item in nodes if isinstance(item, dict)]

    async def top_anime(self, page: int = 1) -> list[dict]:
        return await self.browse("top", page)


def _clean_search(value: str) -> str:
    return " ".join(str(value or "").split())[:100]


def _browse_filter(category: str) -> tuple[str, str | None]:
    choices = {
        "trending": ("TRENDING_DESC", None),
        "upcoming": ("START_DATE", "NOT_YET_RELEASED"),
        "latest": ("UPDATED_AT_DESC", "RELEASING"),
        "top": ("SCORE_DESC", None),
    }
    try:
        return choices[category.lower()]
    except (KeyError, AttributeError):
        raise ValueError("Choose trending, upcoming, latest or top") from None


def _plain_text(value, limit: int = 650) -> str:
    text = re.sub(r"<[^>]*>", " ", str(value or ""))
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def _safe_title(item: dict) -> str:
    title = item.get("title") or {}
    return str(title.get("english") or title.get("romaji") or title.get("native") or "Untitled")[:120]


def _date(item: dict) -> str:
    date = item.get("startDate") or {}
    pieces = [date.get(k) for k in ("year", "month", "day") if date.get(k)]
    return "-".join(f"{int(value):02d}" if len(pieces) > 1 and index else str(value)
                    for index, value in enumerate(pieces)) or "unknown"


def _safe_url(value) -> str | None:
    url = str(value or "")
    return url if re.fullmatch(r"https://anilist\.co/(?:anime|manga|character)/\d+/?", url) else None


def _media_card(item: dict, media_type: str, *, airing: bool = False) -> str:
    title = _safe_title(item)
    fields = [f"<b>{html.escape(title)}</b>"]
    kind = str(item.get("format") or "Unknown").replace("_", " ").title()
    status = str(item.get("status") or "Unknown").replace("_", " ").title()
    fields.append(f"{html.escape(media_type.title())} · {html.escape(kind)} · {html.escape(status)}")
    counts = []
    for key, label in (("episodes", "episodes"), ("chapters", "chapters"), ("volumes", "volumes")):
        try:
            count = int(item.get(key))
        except (TypeError, ValueError, OverflowError):
            continue
        if count >= 0:
            counts.append(f"{count} {label}")
    if counts:
        fields.append(" · ".join(counts))
    try:
        score = int(item.get("averageScore"))
    except (TypeError, ValueError, OverflowError):
        score = None
    if score is not None and 0 <= score <= 100:
        fields.append(f"Score: {score}/100")
    genres = [html.escape(str(g)) for g in (item.get("genres") or [])[:6] if isinstance(g, str)]
    if genres:
        fields.append("Genres: " + ", ".join(genres))
    tags = [tag for tag in (item.get("tags") or [])
            if isinstance(tag, dict) and not tag.get("isMediaSpoiler") and not tag.get("isGeneralSpoiler")]
    tags.sort(key=lambda tag: int(tag.get("rank") or 0), reverse=True)
    labels = [html.escape(str(tag.get("name", ""))) for tag in tags[:4] if tag.get("name")]
    if labels:
        fields.append("Tags: " + ", ".join(labels))
    fields.append("Start: " + html.escape(_date(item)))
    if airing:
        next_ep = item.get("nextAiringEpisode") or {}
        if next_ep.get("episode") is not None:
            when = next_ep.get("airingAt")
            if when:
                stamp = datetime.fromtimestamp(int(when), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            else:
                stamp = "time unavailable"
            fields.append(f"Next: episode {int(next_ep['episode'])} · {html.escape(stamp)}")
        else:
            fields.append("Next episode: no schedule currently listed")
    description = _plain_text(item.get("description"))
    if description:
        fields.append(html.escape(description))
    return "\n".join(fields)[:3900]


def _character_card(item: dict) -> str:
    name = item.get("name") or {}
    title = str(name.get("full") or name.get("native") or "Unknown character")[:120]
    lines = [f"<b>{html.escape(title)}</b>", f"Favourites: {int(item.get('favourites') or 0)}"]
    description = _plain_text(item.get("description"))
    if description:
        lines.append(html.escape(description))
    return "\n".join(lines)[:3900]


def _media_link(item: dict) -> InlineKeyboardMarkup | None:
    url = _safe_url(item.get("siteUrl"))
    if not url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton("Open on AniList", url=url)]])


def _browse_markup(bot_id: int, category: str, page: int, items: list[dict]) -> InlineKeyboardMarkup | None:
    rows = []
    for item in items[:5]:
        url = _safe_url(item.get("siteUrl"))
        if url:
            rows.append([InlineKeyboardButton(_safe_title(item)[:60], url=url)])
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("‹ Previous", callback_data=f"ga:{bot_id}:{category}:{page - 1}"))
    nav.append(InlineKeyboardButton(f"Page {page}", callback_data=f"ga:{bot_id}:noop:1"))
    if page < 100:
        nav.append(InlineKeyboardButton("Next ›", callback_data=f"ga:{bot_id}:{category}:{page + 1}"))
    rows.append(nav)
    return InlineKeyboardMarkup(rows)


def _list_text(items: list[dict], title: str) -> str:
    if not items:
        return f"<b>{html.escape(title)}</b>\n\nNo titles were returned. Try again later."
    lines = [f"<b>{html.escape(title)}</b>"]
    for index, item in enumerate(items[:5], start=1):
        score = item.get("averageScore")
        suffix = f" · {int(score)}/100" if score is not None else ""
        lines.append(f"{index}. <b>{html.escape(_safe_title(item))}</b>{suffix} · {html.escape(_date(item))}")
    return "\n".join(lines)


def attach_anime_handlers(app, *, bot_id: int, client: AniListClient | None = None) -> AniListClient:
    """Register AniList commands on the standalone clone app and return its client."""
    service = client or AniListClient()

    async def safe_reply(message: Message, text: str, *, reply_markup=None):
        try:
            await message.reply_text(text[:3900], parse_mode="html", reply_markup=reply_markup,
                                     disable_web_page_preview=True)
        except Exception:
            # Keep network/Telegram formatting failures local to this optional feature.
            return None

    @app.on_message(filters.group & filters.command(["anime", "manga", "airing"]))
    async def _media_lookup(_client, message: Message):
        pieces = (message.text or "").split(maxsplit=1)
        search = pieces[1] if len(pieces) > 1 else ""
        command = (message.command or [""])[0].split("@", 1)[0].lower()
        if not _clean_search(search):
            return await safe_reply(message, f"Usage: <code>/{command} &lt;title&gt;</code>")
        media_type = "MANGA" if command == "manga" else "ANIME"
        try:
            item = await service.media(search, media_type)
        except AniListError:
            return await safe_reply(message, "AniList is temporarily unavailable. Please try again later.")
        if not item:
            return await safe_reply(message, "No matching title found on AniList.")
        return await safe_reply(message, _media_card(item, media_type.lower(), airing=command == "airing"),
                                reply_markup=_media_link(item))

    @app.on_message(filters.group & filters.command("character"))
    async def _character(_client, message: Message):
        pieces = (message.text or "").split(maxsplit=1)
        search = pieces[1] if len(pieces) > 1 else ""
        if not _clean_search(search):
            return await safe_reply(message, "Usage: <code>/character &lt;name&gt;</code>")
        try:
            item = await service.character(search)
        except AniListError:
            return await safe_reply(message, "AniList is temporarily unavailable. Please try again later.")
        if not item:
            return await safe_reply(message, "No matching character found on AniList.")
        return await safe_reply(message, _character_card(item), reply_markup=_media_link(item))

    @app.on_message(filters.group & filters.command("gettags"))
    async def _genres(_client, message: Message):
        try:
            genres = await service.genres()
        except AniListError:
            return await safe_reply(message, "AniList is temporarily unavailable. Please try again later.")
        if not genres:
            return await safe_reply(message, "AniList did not return any genres just now.")
        return await safe_reply(message, "<b>AniList anime genres</b>\n\n" + " · ".join(html.escape(g) for g in genres))

    async def show_browse(message: Message, category: str, page: int, *, edit: bool = False):
        category = category.lower()
        if category not in ("trending", "upcoming", "latest", "top"):
            text = "Use <code>/browse trending</code>, <code>/browse upcoming</code> or <code>/browse latest</code>."
            return await safe_reply(message, text)
        try:
            items = await service.top_anime(page) if category == "top" else await service.browse(category, page)
        except (AniListError, ValueError):
            text = "AniList is temporarily unavailable. Please try again later."
            if edit:
                try:
                    return await message.edit_text(text, parse_mode="html")
                except Exception:
                    return None
            return await safe_reply(message, text)
        title = {"trending": "Trending anime", "upcoming": "Upcoming anime",
                 "latest": "Currently airing", "top": "Top anime"}[category]
        text = _list_text(items, title)
        markup = _browse_markup(bot_id, category, page, items)
        try:
            if edit:
                return await message.edit_text(text, parse_mode="html", reply_markup=markup)
            return await message.reply_text(text, parse_mode="html", reply_markup=markup,
                                            disable_web_page_preview=True)
        except Exception:
            return None

    @app.on_message(filters.group & filters.command(["browse", "topanime"]))
    async def _browse(_client, message: Message):
        command = (message.command or [""])[0].split("@", 1)[0].lower()
        parts = (message.text or "").split(maxsplit=1)
        category = "top" if command == "topanime" else (parts[1].strip().lower() if len(parts) > 1 else "trending")
        return await show_browse(message, category, 1)

    @app.on_message(filters.group & filters.command("anisettings"))
    async def _settings(_client, message: Message):
        return await safe_reply(
            message,
            "<b>AniList lookup settings</b>\n\n"
            "This worker uses AniList's public GraphQL service, filters adult titles, "
            "does not need a group-specific token, and caches lookups briefly.\n\n"
            "Commands: /anime, /manga, /character, /gettags, /browse, /airing, /topanime.",
        )

    @app.on_callback_query(filters.regex(rf"^ga:{int(bot_id)}:"))
    async def _browse_page(_client, query: CallbackQuery):
        parts = (query.data or "").split(":")
        if len(parts) != 4:
            return await query.answer("Invalid browse control.", show_alert=True)
        category = parts[2]
        if category == "noop":
            return await query.answer()
        try:
            page = int(parts[3])
        except ValueError:
            return await query.answer("Invalid page.", show_alert=True)
        if category not in ("trending", "upcoming", "latest", "top") or not 1 <= page <= 100:
            return await query.answer("Invalid browse page.", show_alert=True)
        await query.answer("Loading…")
        await show_browse(query.message, category, page, edit=True)

    return service

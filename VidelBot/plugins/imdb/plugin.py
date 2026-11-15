"""
IMDb Rich Search & Metadata Card Plugin (inspired by WZML-X wzv3-dev plugins/imdb).

Supports:
  • ``/imdb <title>`` or ``/imdb tt0816692`` (or replying to text / media filename)
  • Interactive rich search picker with colored buttons
  • Full Movie / Series card with poster preview, ratings, countries (pycountry),
    collapsible storyline & cast/crew, IMDb/Trailer links, and one-tap ID copy.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from core import bg, rich, stream
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from core.rich import Doc
from core.ui import copy_button, styled_button

log = logging.getLogger("videl.plugins.imdb")

TT_RE = re.compile(r"\b(tt\d{6,10})\b", re.IGNORECASE)
_SEARCH_CACHE: dict[str, list[dict]] = {}


class Plugin(PluginBase):
    pass


def _clean_filename_query(raw: str) -> str:
    s = re.sub(r"\.[a-zA-Z0-9]{2,4}$", "", raw or "")
    s = re.sub(r"[._-]+", " ", s)
    s = re.sub(r"\b(480p|720p|1080p|2160p|4k|x264|x265|hevc|web[- ]?dl|bluray|hdr|10bit)\b.*", "", s, flags=re.I)
    return s.strip()[:120]


def _country_names(codes: list[str] | None, fallback: list[str] | None) -> str:
    if fallback:
        return ", ".join(str(c) for c in fallback[:4])
    if not codes:
        return ""
    out = []
    try:
        import pycountry
        for code in codes[:4]:
            c = pycountry.countries.get(alpha_2=str(code).upper())
            out.append(c.name if c else str(code))
    except Exception:
        out = [str(c) for c in codes[:4]]
    return ", ".join(out)


def _format_duration(minutes_or_val) -> str:
    if not minutes_or_val:
        return ""
    if isinstance(minutes_or_val, (int, float)):
        mins = int(minutes_or_val)
        h, m = divmod(mins, 60)
        return f"{h}h {m}m" if h else f"{m}m"
    s = str(minutes_or_val).strip()
    if s.isdigit():
        mins = int(s)
        h, m = divmod(mins, 60)
        return f"{h}h {m}m" if h else f"{m}m"
    return s


def search_imdb_sync(query: str) -> list[dict]:
    import imdbio
    res = imdbio.search_title(query)
    titles = getattr(res, "titles", None) or []
    out = []
    for t in titles[:10]:
        tid = getattr(t, "imdb_id", None) or getattr(t, "imdbId", None) or getattr(t, "id", None)
        if not tid:
            continue
        tid = str(tid)
        if not tid.startswith("tt"):
            tid = f"tt{tid}"
        out.append({
            "id": tid,
            "title": getattr(t, "title", None) or "Untitled",
            "year": getattr(t, "year", None) or "",
            "kind": getattr(t, "kind", None) or "",
            "rating": getattr(t, "rating", None),
        })
    return out


def fetch_imdb_detail_sync(tt_id: str):
    import imdbio
    clean_id = tt_id.lower()
    if not clean_id.startswith("tt"):
        clean_id = f"tt{clean_id}"
    return imdbio.get_movie(clean_id)


def build_search_screen(query: str, results: list[dict], uid: int, cache_key: str) -> tuple[Doc, InlineKeyboardMarkup]:
    doc = Doc("🎬", "IMDb Search", f"{len(results)} result(s) for “{query}”")
    rows = []
    kb_rows = []
    for idx, item in enumerate(results[:8], 1):
        yr = f" ({item['year']})" if item.get("year") else ""
        rt = f"⭐ {item['rating']}" if item.get("rating") else "—"
        rows.append((f"#{idx}", f"{item['title']}{yr}", item.get("kind") or "Title", rt))
        label = f"🎬 {item['title']}{yr}"
        if len(label) > 40:
            label = label[:37] + "…"
        kb_rows.append([
            styled_button(
                label,
                callback_data=f"plg_imdb:pick:{item['id']}:{uid}",
                style="primary" if idx == 1 else None,
            )
        ])
    doc.table(rows, header=("#", "Title", "Type", "Rating"))
    doc.footer("Select a title below to open its full IMDb card.")
    kb_rows.append([Btn("❌ Close", callback_data="close_btn")])
    return doc, InlineKeyboardMarkup(kb_rows)


def build_movie_card(detail, uid: int = 0, back_key: str = "") -> tuple[Doc, InlineKeyboardMarkup]:
    tid = getattr(detail, "imdb_id", None) or getattr(detail, "imdbId", None) or ""
    if tid and not str(tid).startswith("tt"):
        tid = f"tt{tid}"
    title = getattr(detail, "title", None) or "Unknown Title"
    year = getattr(detail, "year", None)
    year_end = getattr(detail, "year_end", None)
    yr_str = f"{year}–{year_end}" if year and year_end and year_end != year else (str(year) if year else "")
    kind = str(getattr(detail, "kind", "") or "Movie").replace("_", " ").title()
    sub = " · ".join(x for x in (kind, yr_str) if x)

    doc = Doc("🎬", title, sub)
    poster = getattr(detail, "cover_url", None) or ""
    if poster.startswith("http"):
        doc.with_preview(poster)

    rating = getattr(detail, "rating", None)
    votes = getattr(detail, "votes", None)
    rating_txt = f"⭐ {rating}/10" + (f" ({votes:,} votes)" if isinstance(votes, int) else "") if rating else "N/A"
    meta = getattr(detail, "metacritic_rating", None)
    dur = _format_duration(getattr(detail, "duration", None))
    cert = getattr(detail, "certificate", None) or getattr(detail, "mpaa", None) or ""
    genres = ", ".join(getattr(detail, "genres", None) or [])
    countries = _country_names(getattr(detail, "country_codes", None), getattr(detail, "countries", None))
    langs_list = getattr(detail, "languages_text", None) or getattr(detail, "languages", None) or []
    langs = ", ".join(str(x) for x in langs_list[:5]) if isinstance(langs_list, (list, tuple)) else str(langs_list)
    rel_date = getattr(detail, "release_date", None) or ""

    awards_obj = getattr(detail, "awards", None)
    awards_str = ""
    if awards_obj:
        w = getattr(awards_obj, "wins", 0) or 0
        n = getattr(awards_obj, "nominations", 0) or 0
        if w or n:
            awards_str = f"🏆 {w} wins & {n} nominations"

    doc.kv_table([
        ("⭐ Rating", rating_txt),
        ("🎯 Metacritic", f"{meta}/100" if meta else ""),
        ("🎭 Genres", genres),
        ("⏱ Runtime", dur),
        ("🔞 Certificate", cert),
        ("📅 Release", rel_date),
        ("🌍 Country", countries),
        ("🗣 Languages", langs),
        ("🏆 Awards", awards_str),
        ("🆔 IMDb ID", rich.code(tid) if tid else ""),
    ], key_header="Field", val_header="Details")

    plot = (getattr(detail, "plot", None) or "").strip()
    if plot:
        plot_doc = Doc().text(rich.esc(plot[:900]))
        doc.details("📖 Storyline", plot_doc, open_=True)

    directors = [getattr(p, "name", "") for p in (getattr(detail, "directors", None) or []) if getattr(p, "name", "")]
    stars = []
    for c in (getattr(detail, "stars", None) or [])[:8]:
        cname = getattr(c, "name", "")
        chars = getattr(c, "characters", None) or []
        role = f" as {chars[0]}" if chars else ""
        if cname:
            stars.append(f"{cname}{role}")
    gross = getattr(detail, "worldwide_gross", None) or ""
    if directors or stars or gross:
        cast_doc = Doc()
        rows = []
        if directors:
            rows.append(("🎬 Director(s)", ", ".join(directors[:4])))
        if stars:
            rows.append(("🌟 Cast", ", ".join(stars[:6])))
        if gross:
            rows.append(("💰 Worldwide", str(gross)))
        cast_doc.table(rows, header=("Role", "Credits"))
        doc.details("🎭 Cast & Crew", cast_doc)

    url = getattr(detail, "url", None) or (f"https://www.imdb.com/title/{tid}/" if tid else "https://www.imdb.com")
    trailers = getattr(detail, "trailers", None) or []
    trailer_url = ""
    if trailers:
        first_t = trailers[0]
        trailer_url = first_t if isinstance(first_t, str) else (getattr(first_t, "url", "") or "")

    btn_row1 = [Btn("🌐 Open on IMDb", url=url)]
    if trailer_url.startswith("http"):
        btn_row1.append(Btn("▶️ Trailer", url=trailer_url))
    kb_rows = [btn_row1]
    if tid:
        kb_rows.append([copy_button(f"📋 Copy {tid}", tid)])
    nav_row = []
    if back_key:
        nav_row.append(styled_button("🔙 Results", callback_data=f"plg_imdb:back:{back_key}:{uid}", style="primary"))
    nav_row.append(Btn("❌ Close", callback_data="close_btn"))
    kb_rows.append(nav_row)
    return doc, InlineKeyboardMarkup(kb_rows)


def _extract_query(message: Message) -> str:
    if message.command and len(message.command) > 1:
        return (message.text or "").split(None, 1)[1].strip()
    rep = message.reply_to_message
    if rep:
        for kind in ("video", "document", "audio"):
            m = getattr(rep, kind, None)
            if m and getattr(m, "file_name", None):
                return _clean_filename_query(m.file_name)
        if rep.text or rep.caption:
            return (rep.text or rep.caption).strip()[:120]
    return ""


@Client.on_message(filters.command("imdb") & plugin_filter("imdb"))
async def imdb_cmd(client: Client, message: Message):
    query = _extract_query(message)
    if not query:
        usage = Doc("🎬", "IMDb Lookup", "search movies, series & episodes")
        usage.items([
            rich.Raw("<code>/imdb Interstellar</code> — search by title"),
            rich.Raw("<code>/imdb tt0816692</code> — open directly by IMDb ID"),
            "Or reply to any video/file or message with <code>/imdb</code>",
        ])
        return await rich.reply(message, usage)

    uid = message.from_user.id if message.from_user else 0
    m_tt = TT_RE.search(query)

    async with stream.progress(message, "🎬 <i>Searching IMDb…</i>", can_stop=True) as prog:
        try:
            if m_tt:
                await prog.step("🎬 <i>Fetching IMDb title metadata…</i>")
                detail = await asyncio.to_thread(fetch_imdb_detail_sync, m_tt.group(1))
                if not detail or prog.stopped:
                    return await prog.finish("❌ Title not found on IMDb.")
                doc, kb = build_movie_card(detail, uid=uid)
                return await prog.finish_doc(doc, reply_markup=kb)

            results = await asyncio.to_thread(search_imdb_sync, query)
            if prog.stopped:
                return
            if not results:
                return await prog.finish(f"❌ No IMDb results found for <code>{html.escape(query[:80])}</code>.")
            if len(results) == 1:
                detail = await asyncio.to_thread(fetch_imdb_detail_sync, results[0]["id"])
                if not detail:
                    return await prog.finish("❌ Could not load IMDb details.")
                doc, kb = build_movie_card(detail, uid=uid)
                return await prog.finish_doc(doc, reply_markup=kb)

            cache_key = secrets.token_hex(4)
            _SEARCH_CACHE[cache_key] = {"query": query, "results": results}
            bg.trim(_SEARCH_CACHE, 300)
            doc, kb = build_search_screen(query, results, uid, cache_key)
            return await prog.finish_doc(doc, reply_markup=kb)
        except Exception as e:
            log.warning(f"imdb lookup failed: {e}")
            return await prog.finish(f"❌ IMDb lookup failed: <code>{html.escape(str(e))[:200]}</code>")


@Client.on_callback_query(filters.regex(r"^plg_imdb:(pick|back):([\w_-]+):(\d+)$"))
async def imdb_cb(client: Client, query: CallbackQuery):
    if not plugin_enabled("imdb"):
        return await query.answer("🔌 IMDb plugin is currently disabled.", show_alert=True)
    op = query.matches[0].group(1)
    token = query.matches[0].group(2)
    owner_uid = int(query.matches[0].group(3))
    caller = query.from_user.id if query.from_user else 0
    if owner_uid and caller != owner_uid:
        return await query.answer("Run /imdb yourself to browse results.", show_alert=True)

    if op == "back":
        cached = _SEARCH_CACHE.get(token)
        if not cached:
            return await query.answer("Search session expired — run /imdb again.", show_alert=True)
        await query.answer()
        doc, kb = build_search_screen(cached["query"], cached["results"], owner_uid, token)
        return await rich.edit(query.message, doc, reply_markup=kb)

    await query.answer("🎬 Loading IMDb card…")
    try:
        detail = await asyncio.to_thread(fetch_imdb_detail_sync, token)
    except Exception as e:
        return await query.answer(f"❌ {str(e)[:180]}", show_alert=True)
    if not detail:
        return await query.answer("Title not found.", show_alert=True)
    # Find cache key if one has this title so the user gets a Back button
    back_key = next((k for k, v in reversed(list(_SEARCH_CACHE.items()))
                     if any(r["id"] == token for r in v.get("results", []))), "")
    doc, kb = build_movie_card(detail, uid=owner_uid, back_key=back_key)
    await rich.edit(query.message, doc, reply_markup=kb)

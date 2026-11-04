"""
MyDramaList Rich Plugin (inspired by WZML-X wzv3-dev plugins/mydramalist).

Supports:
  • ``/mdl <drama title or slug>``
  • Interactive search picker with colored buttons
  • Rich drama detail card with poster preview, country, episodes, network,
    airing dates, rating, collapsible synopsis & main cast.
"""
from __future__ import annotations

import html
import logging
import secrets
from urllib.parse import quote

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from core import bg, rich, stream
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from core.rich import Doc
from core.ui import styled_button

log = logging.getLogger("videl.plugins.mydramalist")

MDL_API_BASES = [
    "https://kuryana.tbdh.app",
    "https://kuryana.vercel.app",
]
_MDL_SEARCH_CACHE: dict[str, dict] = {}
_SLUG_STORE: dict[str, str] = {}


class Plugin(PluginBase):
    pass


def _short_slug_token(slug: str) -> str:
    clean = (slug or "").strip("/")
    if len(clean) <= 32 and all(ch.isalnum() or ch in "-_" for ch in clean):
        return clean
    tok = f"k_{secrets.token_hex(4)}"
    _SLUG_STORE[tok] = clean
    bg.trim(_SLUG_STORE, 400)
    return tok


def _resolve_slug_token(tok: str) -> str:
    return _SLUG_STORE.get(tok, tok)


async def _mdl_get(path: str) -> dict:
    last_err = "service unreachable"
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        for base in MDL_API_BASES:
            try:
                async with session.get(f"{base}{path}") as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        if isinstance(data, dict) and not data.get("error"):
                            return data
                        last_err = str((data or {}).get("message") or "empty response")
                    else:
                        last_err = f"HTTP {resp.status}"
            except Exception as e:
                last_err = str(e)
    raise LookupError(last_err)


async def search_mdl(query: str) -> list[dict]:
    data = await _mdl_get(f"/search/q/{quote(query.strip(), safe='')}")
    dramas = ((data.get("results") or {}).get("dramas")) or data.get("dramas") or []
    out = []
    for d in dramas[:10]:
        slug = str(d.get("slug") or "").strip("/")
        if not slug:
            continue
        out.append({
            "slug": slug,
            "title": d.get("title") or slug,
            "year": d.get("year") or "",
            "type": d.get("type") or "Drama",
            "rating": d.get("rating"),
            "thumb": d.get("thumb") or "",
        })
    return out


async def fetch_mdl_drama(slug: str) -> dict:
    data = await _mdl_get(f"/id/{slug.strip('/')}")
    return data.get("data") or data


def build_mdl_search_doc(query: str, items: list[dict], uid: int) -> tuple[Doc, InlineKeyboardMarkup]:
    doc = Doc("🎭", "MyDramaList Search", f"{len(items)} result(s) for “{query}”")
    rows = []
    kb_rows = []
    for idx, item in enumerate(items[:8], 1):
        yr = f" ({item['year']})" if item.get("year") else ""
        rt = f"⭐ {item['rating']}" if item.get("rating") else "—"
        rows.append((f"#{idx}", f"{item['title']}{yr}", item.get("type") or "Drama", rt))
        tok = _short_slug_token(item["slug"])
        label = f"🎭 {item['title']}{yr}"
        if len(label) > 40:
            label = label[:37] + "…"
        kb_rows.append([
            styled_button(
                label,
                callback_data=f"plg_mdl:pick:{tok}:{uid}",
                style="primary" if idx == 1 else None,
            )
        ])
    doc.table(rows, header=("#", "Title", "Type", "Rating"))
    kb_rows.append([Btn("❌ Close", callback_data="close_btn")])
    return doc, InlineKeyboardMarkup(kb_rows)


def build_mdl_card(info: dict, slug: str, uid: int = 0, back_key: str = "") -> tuple[Doc, InlineKeyboardMarkup]:
    title = info.get("complete_title") or info.get("title") or slug
    details = info.get("details") or {}
    others = info.get("others") or {}
    sub_parts = [details.get("country") or "", details.get("type") or ""]
    doc = Doc("🎭", str(title), " · ".join(p for p in sub_parts if p))

    poster = info.get("poster") or ""
    if str(poster).startswith("http"):
        doc.with_preview(str(poster))

    rating = info.get("rating")
    genres = others.get("genres") or []
    tags = others.get("tags") or []
    network = others.get("original_network") or details.get("original_network") or ""
    if isinstance(network, list):
        network = ", ".join(str(x) for x in network[:3])

    doc.kv_table([
        ("⭐ Rating", f"{rating}/10" if rating not in (None, "", "N/A") else "N/A"),
        ("🌍 Country", details.get("country") or ""),
        ("📺 Type", details.get("type") or ""),
        ("🎞 Episodes", details.get("episodes") or ""),
        ("📅 Aired", details.get("aired") or details.get("release_date") or ""),
        ("📡 Network", network),
        ("⏱ Duration", details.get("duration") or ""),
        ("🔞 Content", details.get("content_rating") or ""),
        ("🏷 Genres", ", ".join(str(g) for g in genres[:6]) if isinstance(genres, list) else str(genres)),
    ], key_header="Field", val_header="Details")

    synopsis = str(info.get("synopsis") or "").strip()
    if synopsis:
        doc.details("📖 Synopsis", Doc().text(rich.esc(synopsis[:850])), open_=True)

    casts = info.get("casts") or []
    if casts or tags:
        extra_doc = Doc()
        if casts:
            c_rows = []
            for c in casts[:8]:
                if isinstance(c, dict) and c.get("name"):
                    c_rows.append((c["name"], c.get("role") or "Cast"))
                elif isinstance(c, str):
                    c_rows.append((c, "Cast"))
            if c_rows:
                extra_doc.table(c_rows, header=("Actor", "Role"))
        if tags and isinstance(tags, list):
            clean_tags = [str(t).replace("(Vote or add tags)", "").strip() for t in tags[:8]]
            clean_tags = [t for t in clean_tags if t]
            if clean_tags:
                extra_doc.text("<b>Tags:</b> " + ", ".join(rich.esc(t) for t in clean_tags))
        doc.details("🌟 Cast & Tags", extra_doc)

    mdl_url = info.get("link") or f"https://mydramalist.com/{slug.strip('/')}"
    kb_rows = [[Btn("🌐 Open on MyDramaList", url=str(mdl_url))]]
    nav = []
    if back_key:
        nav.append(styled_button("🔙 Results", callback_data=f"plg_mdl:back:{back_key}:{uid}", style="primary"))
    nav.append(Btn("❌ Close", callback_data="close_btn"))
    kb_rows.append(nav)
    return doc, InlineKeyboardMarkup(kb_rows)


@Client.on_message(filters.command(["mdl", "mydramalist"]) & plugin_filter("mydramalist"))
async def mdl_cmd(client: Client, message: Message):
    q = (message.text or "").split(None, 1)[1].strip() if message.command and len(message.command) > 1 else ""
    if not q and message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        q = (message.reply_to_message.text or message.reply_to_message.caption).strip()[:100]
    if not q:
        return await message.reply_text("<b>Usage:</b> <code>/mdl Alchemy of Souls</code>")

    uid = message.from_user.id if message.from_user else 0
    async with stream.progress(message, "🎭 <i>Searching MyDramaList…</i>", can_stop=True) as prog:
        try:
            items = await search_mdl(q)
        except Exception as e:
            return await prog.finish(f"❌ MyDramaList lookup failed: <code>{html.escape(str(e))[:200]}</code>")
        if prog.stopped:
            return
        if not items:
            return await prog.finish(f"❌ No dramas found on MyDramaList for <code>{html.escape(q[:80])}</code>.")
        if len(items) == 1:
            try:
                info = await fetch_mdl_drama(items[0]["slug"])
            except Exception as e:
                return await prog.finish(f"❌ Could not load drama details: <code>{html.escape(str(e))[:200]}</code>")
            doc, kb = build_mdl_card(info, items[0]["slug"], uid=uid)
            return await prog.finish_doc(doc, reply_markup=kb)

        cache_key = secrets.token_hex(4)
        _MDL_SEARCH_CACHE[cache_key] = {"query": q, "items": items}
        bg.trim(_MDL_SEARCH_CACHE, 200)
        doc, kb = build_mdl_search_doc(q, items, uid)
        return await prog.finish_doc(doc, reply_markup=kb)


@Client.on_callback_query(filters.regex(r"^plg_mdl:(pick|back):([\w:/._-]+):(\d+)$"))
async def mdl_cb(client: Client, query: CallbackQuery):
    if not plugin_enabled("mydramalist"):
        return await query.answer("🔌 MyDramaList plugin is currently disabled.", show_alert=True)
    op = query.matches[0].group(1)
    tok = query.matches[0].group(2)
    owner_uid = int(query.matches[0].group(3))
    caller = query.from_user.id if query.from_user else 0
    if owner_uid and caller != owner_uid:
        return await query.answer("Run /mdl yourself to browse results.", show_alert=True)

    if op == "back":
        cached = _MDL_SEARCH_CACHE.get(tok)
        if not cached:
            return await query.answer("Search session expired.", show_alert=True)
        await query.answer()
        doc, kb = build_mdl_search_doc(cached["query"], cached["items"], owner_uid)
        return await rich.edit(query.message, doc, reply_markup=kb)

    slug = _resolve_slug_token(tok)
    await query.answer("🎭 Loading drama card…")
    try:
        info = await fetch_mdl_drama(slug)
    except Exception as e:
        return await query.answer(f"❌ {str(e)[:180]}", show_alert=True)
    back_key = next((k for k, v in reversed(list(_MDL_SEARCH_CACHE.items()))
                     if any(it["slug"] == slug for it in v.get("items", []))), "")
    doc, kb = build_mdl_card(info, slug, uid=owner_uid, back_key=back_key)
    await rich.edit(query.message, doc, reply_markup=kb)

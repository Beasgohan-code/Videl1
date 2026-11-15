"""
AniList GraphQL v2 Rich Plugin (inspired by WZML-X wzv3-dev plugins/anilist).

Commands:
  • ``/anime <query>``     — anime card with Overview · Characters · Studios/Relations tabs
  • ``/manga <query>``     — manga / manhwa / novel card with chapters, volumes & synopsis
  • ``/character <query>`` — character bio, voice actors & top appearances
"""
from __future__ import annotations

import html
import logging
import re

import aiohttp
from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from core import bg, rich, stream
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from core.rich import Doc
from core.ui import styled_button

log = logging.getLogger("videl.plugins.anilist")

ANILIST_URL = "https://graphql.anilist.co"
_MEDIA_CACHE: dict[tuple[str, int], dict] = {}


class Plugin(PluginBase):
    pass


MEDIA_QUERY = """
query ($search: String, $id: Int, $type: MediaType) {
  Media(search: $search, id: $id, type: $type, sort: POPULARITY_DESC) {
    id
    idMal
    type
    format
    status
    episodes
    duration
    chapters
    volumes
    season
    seasonYear
    averageScore
    meanScore
    popularity
    favourites
    source
    genres
    siteUrl
    title { romaji english native }
    description(asHtml: false)
    studios(isMain: true) { nodes { name } }
    nextAiringEpisode { episode timeUntilAiring }
    characters(sort: [ROLE, RELEVANCE], perPage: 8) {
      edges { role node { name { full } } }
    }
    relations {
      edges {
        relationType(version: 2)
        node { title { english romaji } format type }
      }
    }
  }
}
"""

CHAR_QUERY = """
query ($search: String, $id: Int) {
  Character(search: $search, id: $id, sort: FAVOURITES_DESC) {
    id
    name { full native alternative }
    favourites
    siteUrl
    image { large }
    description(asHtml: false)
    media(sort: POPULARITY_DESC, perPage: 8) {
      nodes { title { english romaji } type format }
    }
  }
}
"""


def _strip_anilist_markup(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    # Remove AniList spoiler tags ~! ... !~
    s = re.sub(r"~!.+?!~", "[Spoiler]", s, flags=re.S)
    s = re.sub(r"__([^_]+)__", r"\1", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _fmt_countdown(seconds: int | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    d, rem = divmod(int(seconds), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


async def query_anilist(gql: str, variables: dict) -> dict:
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(ANILIST_URL, json={"query": gql, "variables": variables}) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200 or data.get("errors"):
                err = ((data.get("errors") or [{}])[0]).get("message") or f"HTTP {resp.status}"
                raise LookupError(err)
            return data.get("data") or {}


def build_media_doc(media: dict, kind: str, tab: str, uid: int) -> tuple[Doc, InlineKeyboardMarkup]:
    mid = int(media["id"])
    titles = media.get("title") or {}
    main_title = titles.get("english") or titles.get("romaji") or titles.get("native") or "Unknown"
    sub_parts = [
        str(media.get("format") or kind.upper()).replace("_", " "),
        str(media.get("status") or "").replace("_", " ").title(),
    ]
    if media.get("seasonYear"):
        season = str(media.get("season") or "").title()
        sub_parts.append(f"{season} {media['seasonYear']}".strip())
    icon = "🎌" if kind == "anime" else "📚"
    doc = Doc(icon, main_title, " · ".join(p for p in sub_parts if p))
    # AniList dynamic OG banner preview
    doc.with_preview(f"https://img.anili.st/media/{mid}")

    if tab == "chars":
        edges = ((media.get("characters") or {}).get("edges")) or []
        if edges:
            rows = []
            for e in edges[:8]:
                cname = ((e.get("node") or {}).get("name") or {}).get("full") or "?"
                role = str(e.get("role") or "").title()
                rows.append((cname, role or "Supporting"))
            doc.h("👥", "Main & Supporting Cast").table(rows, header=("Character", "Role"))
        else:
            doc.text("<i>No character data listed on AniList.</i>")
    elif tab == "rels":
        rels = ((media.get("relations") or {}).get("edges")) or []
        studios = [n.get("name") for n in ((media.get("studios") or {}).get("nodes") or []) if n.get("name")]
        if studios:
            doc.kv_table([("🏢 Studio(s)", ", ".join(studios))])
        if rels:
            rows = []
            for r in rels[:8]:
                rt = str(r.get("relationType") or "Related").replace("_", " ").title()
                node = r.get("node") or {}
                nt = (node.get("title") or {}).get("english") or (node.get("title") or {}).get("romaji") or "?"
                fmt = str(node.get("format") or node.get("type") or "")
                rows.append((rt, f"{nt} ({fmt})" if fmt else nt))
            doc.h("🔗", "Franchise Relations").table(rows, header=("Relation", "Title"))
        else:
            doc.text("<i>No related entries found.</i>")
    else:
        score = media.get("averageScore") or media.get("meanScore")
        rows = []
        if titles.get("romaji") and titles.get("romaji") != main_title:
            rows.append(("🔤 Romaji", titles["romaji"]))
        if titles.get("native"):
            rows.append(("🇯🇵 Native", titles["native"]))
        rows.append(("⭐ Score", f"{score}/100" if score else "N/A"))
        if kind == "anime":
            eps = media.get("episodes") or "?"
            dur = f" ({media['duration']}m/ep)" if media.get("duration") else ""
            rows.append(("🎞 Episodes", f"{eps}{dur}"))
            nxt = media.get("nextAiringEpisode")
            if nxt and nxt.get("episode"):
                rows.append(("⏳ Next Ep", f"Ep {nxt['episode']} in {_fmt_countdown(nxt.get('timeUntilAiring'))}"))
        else:
            rows.append(("📖 Chapters", str(media.get("chapters") or "?")))
            rows.append(("📚 Volumes", str(media.get("volumes") or "?")))
        if media.get("source"):
            rows.append(("📜 Source", str(media["source"]).replace("_", " ").title()))
        if media.get("genres"):
            rows.append(("🏷 Genres", ", ".join(media["genres"][:6])))
        studios = [n.get("name") for n in ((media.get("studios") or {}).get("nodes") or []) if n.get("name")]
        if studios:
            rows.append(("🏢 Studio", ", ".join(studios[:3])))
        if media.get("favourites"):
            rows.append(("❤️ Favourites", f"{int(media['favourites']):,}"))
        doc.kv_table(rows, key_header="Field", val_header="Details")

        desc = _strip_anilist_markup(media.get("description") or "")
        if desc:
            doc.details("📝 Synopsis", Doc().text(rich.esc(desc[:850])), open_=True)

    tab_row = [
        styled_button("📋 Overview", callback_data=f"plg_ani:v:{kind}:{mid}:overview:{uid}",
                      style="primary" if tab == "overview" else None),
        styled_button("👥 Cast", callback_data=f"plg_ani:v:{kind}:{mid}:chars:{uid}",
                      style="primary" if tab == "chars" else None),
        styled_button("🔗 Relations", callback_data=f"plg_ani:v:{kind}:{mid}:rels:{uid}",
                      style="primary" if tab == "rels" else None),
    ]
    links = []
    if media.get("siteUrl"):
        links.append(Btn("🌐 AniList", url=media["siteUrl"]))
    if media.get("idMal"):
        mal_type = "anime" if kind == "anime" else "manga"
        links.append(Btn("📘 MyAnimeList", url=f"https://myanimelist.net/{mal_type}/{media['idMal']}"))
    kb_rows = [tab_row]
    if links:
        kb_rows.append(links)
    kb_rows.append([Btn("❌ Close", callback_data="close_btn")])
    return doc, InlineKeyboardMarkup(kb_rows)


def build_character_doc(char: dict) -> tuple[Doc, InlineKeyboardMarkup]:
    name_obj = char.get("name") or {}
    full = name_obj.get("full") or "Character"
    native = name_obj.get("native") or ""
    doc = Doc("🧝", full, native)
    img = ((char.get("image") or {}).get("large")) or ""
    if img.startswith("http"):
        doc.with_preview(img)

    alts = [a for a in (name_obj.get("alternative") or []) if a]
    rows = []
    if native:
        rows.append(("🇯🇵 Native", native))
    if alts:
        rows.append(("🏷 Aliases", ", ".join(alts[:4])))
    if char.get("favourites") is not None:
        rows.append(("❤️ Favourites", f"{int(char['favourites']):,}"))
    doc.kv_table(rows, key_header="Field", val_header="Value")

    desc = _strip_anilist_markup(char.get("description") or "")
    if desc:
        doc.details("📖 Biography", Doc().text(rich.esc(desc[:850])), open_=True)

    appearances = ((char.get("media") or {}).get("nodes")) or []
    if appearances:
        app_rows = []
        for m in appearances[:6]:
            mt = (m.get("title") or {}).get("english") or (m.get("title") or {}).get("romaji") or "?"
            fmt = str(m.get("format") or m.get("type") or "")
            app_rows.append((mt, fmt))
        doc.details("🎬 Featured in", Doc().table(app_rows, header=("Title", "Format")))

    kb_rows = []
    if char.get("siteUrl"):
        kb_rows.append([Btn("🌐 Open on AniList", url=char["siteUrl"])])
    kb_rows.append([Btn("❌ Close", callback_data="close_btn")])
    return doc, InlineKeyboardMarkup(kb_rows)


async def _handle_media_search(message: Message, kind: str):
    q = (message.text or "").split(None, 1)[1].strip() if message.command and len(message.command) > 1 else ""
    if not q and message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        q = (message.reply_to_message.text or message.reply_to_message.caption).strip()[:100]
    if not q:
        cmd = "anime" if kind == "anime" else "manga"
        example = "Frieren" if kind == "anime" else "Berserk"
        return await message.reply_text(f"<b>Usage:</b> <code>/{cmd} {example}</code>")

    uid = message.from_user.id if message.from_user else 0
    gql_type = "ANIME" if kind == "anime" else "MANGA"
    async with stream.progress(message, f"🎌 <i>Searching AniList ({kind})…</i>", can_stop=True) as prog:
        try:
            data = await query_anilist(MEDIA_QUERY, {"search": q, "type": gql_type})
        except LookupError:
            return await prog.finish(f"❌ No {kind} found on AniList for <code>{html.escape(q[:80])}</code>.")
        except Exception as e:
            return await prog.finish(f"❌ AniList request failed: <code>{html.escape(str(e))[:200]}</code>")
        media = data.get("Media")
        if not media or prog.stopped:
            return await prog.finish(f"❌ No {kind} found for <code>{html.escape(q[:80])}</code>.")
        _MEDIA_CACHE[(kind, int(media["id"]))] = media
        bg.trim(_MEDIA_CACHE, 300)
        doc, kb = build_media_doc(media, kind, "overview", uid)
        return await prog.finish_doc(doc, reply_markup=kb)


@Client.on_message(filters.command("anime") & plugin_filter("anilist"))
async def anime_cmd(client: Client, message: Message):
    await _handle_media_search(message, "anime")


@Client.on_message(filters.command("manga") & plugin_filter("anilist"))
async def manga_cmd(client: Client, message: Message):
    await _handle_media_search(message, "manga")


@Client.on_message(filters.command(["character", "char"]) & plugin_filter("anilist"))
async def character_cmd(client: Client, message: Message):
    q = (message.text or "").split(None, 1)[1].strip() if message.command and len(message.command) > 1 else ""
    if not q:
        return await message.reply_text("<b>Usage:</b> <code>/character Spike Spiegel</code>")
    async with stream.progress(message, "🧝 <i>Searching AniList character…</i>", can_stop=True) as prog:
        try:
            data = await query_anilist(CHAR_QUERY, {"search": q})
        except LookupError:
            return await prog.finish(f"❌ Character not found for <code>{html.escape(q[:80])}</code>.")
        except Exception as e:
            return await prog.finish(f"❌ AniList error: <code>{html.escape(str(e))[:200]}</code>")
        char = data.get("Character")
        if not char or prog.stopped:
            return await prog.finish(f"❌ Character not found for <code>{html.escape(q[:80])}</code>.")
        doc, kb = build_character_doc(char)
        return await prog.finish_doc(doc, reply_markup=kb)


@Client.on_callback_query(filters.regex(r"^plg_ani:v:(\w+):(\d+):(\w+):(\d+)$"))
async def anilist_tab_cb(client: Client, query: CallbackQuery):
    if not plugin_enabled("anilist"):
        return await query.answer("🔌 AniList plugin is currently disabled.", show_alert=True)
    kind = query.matches[0].group(1)
    mid = int(query.matches[0].group(2))
    tab = query.matches[0].group(3)
    owner_uid = int(query.matches[0].group(4))
    caller = query.from_user.id if query.from_user else 0
    if owner_uid and caller != owner_uid:
        return await query.answer(f"Use /{kind} to browse your own card.", show_alert=True)

    media = _MEDIA_CACHE.get((kind, mid))
    if media is None:
        gql_type = "ANIME" if kind == "anime" else "MANGA"
        try:
            data = await query_anilist(MEDIA_QUERY, {"id": mid, "type": gql_type})
            media = data.get("Media")
            if media:
                _MEDIA_CACHE[(kind, mid)] = media
        except Exception as e:
            return await query.answer(f"❌ {str(e)[:180]}", show_alert=True)
    if not media:
        return await query.answer("Entry unavailable.", show_alert=True)
    await query.answer()
    doc, kb = build_media_doc(media, kind, tab, owner_uid)
    await rich.edit(query.message, doc, reply_markup=kb)

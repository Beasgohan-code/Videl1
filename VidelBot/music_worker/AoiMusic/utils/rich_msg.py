# AoiMusic rich message helper — Bot API 10.3 rich messages, all MTProto.
#
# Everything goes through the bot's pyroblack client: send_rich_message,
# messages.EditMessage with rich_message, delete_messages. Native MTProto
# edits keep premium-emoji document entities alive (the HTTP Bot API strips
# them back to plain emoji on edit); callback updates still arrive through
# the client's normal handlers.

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx
from pyrogram import ContinuePropagation

from AoiMusic.utils.premium import _DB_EMOJI_FALLBACK

import logging

log = logging.getLogger(__name__)

_EMOJI_DB_PATH = Path(
    os.environ.get(
        "PREMIUM_EMOJI_DB",
        os.path.join(os.path.dirname(__file__), "smart_emoji_db.json"),
    )
)
try:
    _EMOJI_DB = json.loads(_EMOJI_DB_PATH.read_text(encoding="utf-8"))
except FileNotFoundError:
    # Same graceful degradation as premium.load_db(): panels fall back to
    # plain unicode emojis when the premium emoji database is not installed.
    _EMOJI_DB = {}

# Emojis used across the panels that are missing from the DB, mapped to
# verified equivalents whose ids ARE in the DB (play/pause/stop/nav icons).
_RICH_EMOJI_FALLBACK = _DB_EMOJI_FALLBACK


def pemoji(em: str) -> str:
    """Wrap an emoji in a premium custom emoji tag when the DB has one."""
    cid = pick_id(em)
    if not cid:
        return em
    return f'<tg-emoji emoji-id="{cid}">{em}</tg-emoji>'


_CHOSEN: dict = {}


def pick_id(em: str) -> str | None:
    key = em.replace("\ufe0f", "")
    if key in _CHOSEN:
        return _CHOSEN[key]
    ids = (_EMOJI_DB.get(key) or {}).get("ids") or []
    cid = None
    if ids:
        cid = ids[0]
    else:
        fb = _RICH_EMOJI_FALLBACK.get(key)
        if fb:
            ids = (_EMOJI_DB.get(fb) or {}).get("ids") or []
            if ids:
                cid = ids[0]
    _CHOSEN[key] = cid
    return cid


def rich_esc(text: str) -> str:
    """HTML-escape text but keep the tags rich messages understand.

    Mentions arrive as full <a href="tg://user?id=…">…</a> fragments inside
    lang strings and user names; escaping those wholesale broke links and
    showed raw markup, so safe tags pass through untouched.
    """
    raw = str(text)
    out = []
    last = 0
    for m in _TAG_KEEP_RE.finditer(raw):
        out.append(str(raw[last:m.start()]).replace("&", "&amp;")
                   .replace("<", "&lt;").replace(">", "&gt;"))
        tag = m.group(0)
        out.append(not_hm(tag))
        last = m.end()
    out.append(str(raw[last:]).replace("&", "&amp;")
               .replace("<", "&lt;").replace(">", "&gt;"))
    return "".join(out)


def not_hm(tag: str) -> str:
    """Normalise one allowed tag: quote bare href values."""
    fixed = re.sub(r'href=([^"\' >][^ >]*)', r'href="\1"', tag)
    return fixed


def rich_user_link(user) -> str:
    """A render-safe user reference for rich messages and queue tags.

    Telegram's pyrogram mention is an <a href="tg://user…"> fragment, which
    rich-message rendering strips — the raw tag leaks as text on the
    now-playing card. Names must link to the t.me profile instead, or
    stay plain text when the user has no username.
    """
    try:
        name = rich_esc(user.first_name or "ᴜɴᴋɴᴏᴡɴ")
        username = getattr(user, "username", None)
        if username:
            return f'<a href="https://t.me/{rich_esc(username)}">{name}</a>'
        return name
    except Exception:
        return "ᴜɴᴋɴᴏᴡɴ"


def rich_heading(text: str, level: int = 4) -> str:
    return f"<h{level}>{text}</h{level}>"


def rich_img(url: str) -> str:
    """An <img> tag for a valid remote http(s) URL or an existing local image path."""
    u = str(url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")) and not os.path.exists(u):
        return ""
    return f'<img src="{u}"/>'



import html as _html_mod

_TG_BTN_ROW_RE = re.compile(
    r"<tg-button-row\b[^>]*>(.*?)</tg-button-row>",
    re.IGNORECASE | re.DOTALL,
)
_TG_BTN_RE = re.compile(
    r"<tg-button\b([^>]*)>(.*?)</tg-button>",
    re.IGNORECASE | re.DOTALL,
)
_TG_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)\s*=\s*"([^"]*)"')
_TG_EMOJI_ID_RE = re.compile(r"""<tg-emoji\s+emoji-id=(?:"|')?(\d+)(?:"|')?[^>]*>.*?</tg-emoji>""", re.IGNORECASE | re.DOTALL)
_TG_INNER_TAG_RE = re.compile(r"<[^>]+>")


def _parse_button_label_and_icon(attrs: dict, inner_html: str) -> tuple[str, str | None]:
    """Extract clean label text and optional premium custom emoji ID/key for pbtn()."""
    from AoiMusic.utils.premium_emoji import BLANK_BTN_TEXT

    icon = attrs.get("emoji") or None
    raw = inner_html or ""
    if not icon:
        m = _TG_EMOJI_ID_RE.search(raw)
        if m:
            icon = m.group(1)
            raw = _TG_EMOJI_ID_RE.sub("", raw, count=1)
    else:
        raw = _TG_EMOJI_ID_RE.sub("", raw)

    text = _TG_INNER_TAG_RE.sub("", raw)
    text = _html_mod.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    if attrs.get("icon_only", "").lower() in ("1", "true", "yes") or (not text and icon):
        return BLANK_BTN_TEXT, icon
    return (text or "•"), icon


def _build_normal_button(attrs_str: str, inner_html: str):
    """Build a XENOBOT2-style normal Telegram InlineKeyboardButton via pbtn()."""
    from AoiMusic.utils.premium_emoji import pbtn

    attrs = {k.lower(): _html_mod.unescape(v) for k, v in _TG_ATTR_RE.findall(attrs_str or "")}
    btype = attrs.get("type", "callback_data").lower()
    label, icon = _parse_button_label_and_icon(attrs, inner_html)

    raw_style = (attrs.get("style") or "").strip().upper()
    style = raw_style if raw_style in ("PRIMARY", "SUCCESS", "DANGER") else None

    if btype == "user" and attrs.get("user_id"):
        try:
            return pbtn(text=label, emoji=icon, style=style, user_id=int(attrs["user_id"]))
        except (TypeError, ValueError):
            pass
    if btype == "url" and attrs.get("url"):
        u = attrs["url"]
        if u.startswith("tg://user?id=") and u.split("=", 1)[1].isdigit():
            try:
                return pbtn(text=label, emoji=icon, style=style, user_id=int(u.split("=", 1)[1]))
            except (TypeError, ValueError):
                pass
        return pbtn(text=label, emoji=icon, style=style, url=u)
    if btype == "switch_inline_query_current_chat":
        return pbtn(
            text=label,
            emoji=icon,
            style=style,
            switch_inline_query_current_chat=attrs.get("query", ""),
        )
    if btype == "switch_inline_query":
        return pbtn(
            text=label,
            emoji=icon,
            style=style,
            switch_inline_query=attrs.get("query", ""),
        )
    cb = attrs.get("data") or "noop"
    cb_bytes = cb.encode("utf-8")
    if len(cb_bytes) > 64:
        cb = cb_bytes[:64].decode("utf-8", errors="ignore")
    return pbtn(text=label, emoji=icon, style=style, callback_data=cb)


def _extract_inline_keyboard(html: str, existing_markup=None):
    """Extract all <tg-button-row>/<tg-button> tags into a normal InlineKeyboardMarkup."""
    from pyrogram.types import InlineKeyboardMarkup

    if not html or "<tg-button" not in html:
        return html, existing_markup

    rows = []

    def _consume_row(match):
        row_inner = match.group(1)
        row_btns = [
            _build_normal_button(m.group(1), m.group(2))
            for m in _TG_BTN_RE.finditer(row_inner)
        ]
        if row_btns:
            rows.append(row_btns)
        return ""

    cleaned = _TG_BTN_ROW_RE.sub(_consume_row, html)

    def _consume_stray(match):
        rows.append([_build_normal_button(match.group(1), match.group(2))])
        return ""

    if "<tg-button" in cleaned:
        cleaned = _TG_BTN_RE.sub(_consume_stray, cleaned)

    if existing_markup is not None and getattr(existing_markup, "inline_keyboard", None):
        merged = list(existing_markup.inline_keyboard) + rows
        return cleaned, InlineKeyboardMarkup(merged) if merged else None
    if rows:
        return cleaned, InlineKeyboardMarkup(rows)
    return cleaned, existing_markup


def rich_button(
    text: str,
    callback_data: str = "",
    style: str = "primary",
    emoji: str = "",
    icon_only: bool = False,
) -> str:
    extra = ""
    if emoji:
        extra += f' emoji="{emoji}"'
    if icon_only:
        extra += ' icon_only="1"'
    style_attr = f' style="{style}"' if style else ""
    return (
        f'<tg-button type="callback_data"{style_attr}{extra} '
        f'data="{callback_data}">{text}</tg-button>'
    )


def rich_url_button(text: str, url: str, style: str = "primary", emoji: str = "") -> str:
    extra = f' emoji="{emoji}"' if emoji else ""
    style_attr = f' style="{style}"' if style else ""
    return f'<tg-button type="url"{style_attr}{extra} url="{url}">{text}</tg-button>'


def rich_user_button(text: str, user_id: int, style: str = "", emoji: str = "") -> str:
    extra = f' emoji="{emoji}"' if emoji else ""
    style_attr = f' style="{style}"' if style else ""
    return f'<tg-button type="user"{style_attr}{extra} user_id="{int(user_id)}">{text}</tg-button>'


def rich_switch_button(text: str, query: str, style: str = "primary") -> str:
    return (
        f'<tg-button type="switch_inline_query_current_chat" style="{style}" '
        f'query="{query}">{text}</tg-button>'
    )


def rich_button_row(*buttons: str, align: str = "") -> str:
    """Row of rich buttons; optional align: left / center / right."""
    open_attr = f' align="{align}"' if align else ""
    return f"<tg-button-row{open_attr}>" + "".join(buttons) + "</tg-button-row>"


def rich_note(text: str) -> str:
    """Block-quoted note block — draws attention like a callout card."""
    return f"<blockquote>{text}</blockquote>"


def rich_kv(label: str, value) -> str:
    """One 'key: value' line with a highlighted value."""
    return f"{str(label)} <mark>{str(value)}</mark>"


def _ctl(emoji: str, label: str, callback: str, style: str, default_label: str = "") -> str:
    text = label or default_label
    return rich_button(f"{pemoji(emoji)} {text}", callback_data=callback, style=style)


def btn_play(label: str, callback: str) -> str:
    return _ctl("▶", label, callback, "success", "ᴘʟᴀʏ")


def btn_pause(label: str, callback: str) -> str:
    return _ctl("⏸", label, callback, "primary", "ᴘᴀᴜsᴇ")


def btn_skip(label: str, callback: str) -> str:
    return _ctl("⏭", label, callback, "primary", "sᴋɪᴘ")


def btn_stop(label: str, callback: str) -> str:
    return _ctl("⏹", label, callback, "danger", "sᴛᴏᴘ")


def btn_close(label: str, callback: str = "close") -> str:
    return _ctl("❌", label, callback, "danger", "ᴄʟᴏsᴇ")


def btn_back(label: str, callback: str) -> str:
    return _ctl("⬅", label, callback, "primary", "ʙᴀᴄᴋ")


def btn_loop(label: str, callback: str) -> str:
    return _ctl("🔁", label, callback, "primary", "ʟᴏᴏᴘ")


def btn_seek(label: str, callback: str) -> str:
    return _ctl("🎚", label, callback, "primary")


def btn_on(label: str, callback: str) -> str:
    return _ctl("✅", label, callback, "success")


def btn_off(label: str, callback: str) -> str:
    return _ctl("🚫", label, callback, "danger")


# One httpx client, but only for non-Telegram HEAD checks (cover URLs).
_IMG_HTTP = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0),
    limits=httpx.Limits(max_keepalive_connections=8),
)


async def send_audio(chat_id: int, filepath: str, caption: str = "",
                     thumb: str | None = None, title: str = "",
                     performer: str = "", duration: int = 0):
    """Upload a song file via the pyroblack client; returns the Message."""
    return await _client().send_audio(
        chat_id=chat_id,
        audio=filepath,
        caption=caption,
        title=title or None,
        performer=performer or None,
        duration=duration or 0,
        thumb=thumb,
    )


async def send_video(chat_id: int, filepath: str, caption: str = "",
                     duration: int = 0, width: int = 0, height: int = 0,
                     thumb: str | None = None, streaming: bool = True):
    """Upload a video file via the pyroblack client; returns the Message."""
    return await _client().send_video(
        chat_id=chat_id,
        video=filepath,
        caption=caption,
        duration=duration or 0,
        width=width or 0,
        height=height or 0,
        supports_streaming=streaming,
        thumb=thumb,
    )



_TAG_KEEP_RE = re.compile(
    r"(<a\s+(?:href\s*=\s*(?:\"|')?[^ >]*(?:\"|')?)?[^>]*>"
    r"|</a>|<b>|</b>|<i>|</i>|<u>|</u>|<s>|</s>|<em>|</em>|<strong>|</strong>"
    r"|<code>|</code>|<pre(?:\s[^>]*)?>|</pre>|<br\s*/?>"
    r"|<blockquote>|</blockquote>|<tg-emoji[^>]*>|</tg-emoji>)"
)

def rich_md(text: str) -> str:
    """Escape markdown-ish help text and convert **bold** / `code` to HTML."""
    esc = rich_esc(text)
    esc = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", esc, flags=re.DOTALL)
    esc = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", esc)
    return esc


_EMOJI_SORTED = sorted((_EMOJI_DB.keys()), key=len, reverse=True)
_EMOJI_RE = re.compile(
    "(" + "|".join(re.escape(k) for k in _EMOJI_SORTED if len(k) >= 2) + ")"
)
_EMOJI_SINGLE_RE = re.compile(
    "(" + "|".join(re.escape(k) for k in _EMOJI_SORTED if len(k) == 1) + ")"
)


_CODE_SEG_RE = re.compile(r"(<pre\b.*?</pre>|<code\b.*?</code>)", re.DOTALL)
_P_SEG_RE = re.compile(r"(<p\b[^>]*>.*?</p>)", re.DOTALL)


def _protect_code(html: str):
    """Swap <code>/<pre> blocks for placeholders so passes can skip them."""
    stash = []

    def _keep(m):
        stash.append(m.group(0))
        return f"\x00CODE{len(stash) - 1}\x00"

    return _CODE_SEG_RE.sub(_keep, html), stash


def _restore_code(html: str, stash) -> str:
    for i, block in enumerate(stash):
        html = html.replace(f"\x00CODE{i}\x00", block)
    return html


def _nl_fix(html: str) -> str:
    """Rich paragraphs collapse newlines, so split them into real <p> blocks.

    Line breaks inside <p>…</p> become </p><p> boundaries; <code>/<pre> blocks
    (inline or standalone) keep their internal newlines.
    """
    html, stash = _protect_code(html)
    out = []
    for seg in _P_SEG_RE.split(html):
        if seg.startswith("<p"):
            seg = re.sub(r"\n", "</p><p>", seg)
            seg = seg.replace("<p></p>", "")
        out.append(seg)
    return _restore_code("".join(out), stash)


def _protect_custom_emojis(html: str):
    """Keep existing custom-emoji entities intact during visible-text emoji conversion."""
    stash = []

    def _keep(m):
        stash.append(m.group(0))
        return f"\x00EMOJI{len(stash) - 1}\x00"

    protected = re.sub(
        r"(<tg-emoji\b[^>]*>.*?</tg-emoji>"
        r"|<img\s+src=\"tg://emoji\?id=\d+\"[^>]*/?>)",
        _keep, html, flags=re.DOTALL)
    return protected, stash


def _restore_custom_emojis(html: str, stash) -> str:
    for i, tag in enumerate(stash):
        html = html.replace(f"\x00EMOJI{i}\x00", tag)
    return html


def _beautify(html: str) -> str:
    """Post-process: leftover **bold**, `code`, and premium-emoji pass.

    Every emoji char that has a DB id anywhere in visible text is upgraded to
    its premium custom-emoji tag, so panels never ship plain emoji.
    """
    html, emoji_stash = _protect_custom_emojis(html)
    html, stash = _protect_code(html)
    html = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html, flags=re.DOTALL)
    html = re.sub(r"__(.+?)__", r"<i>\1</i>", html, flags=re.DOTALL)
    html = re.sub(r"--(.+?)--", r"<u>\1</u>", html, flags=re.DOTALL)
    html = re.sub(r"~~(.+?)~~", r"<s>\1</s>", html, flags=re.DOTALL)
    html = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", html)
    html = re.sub(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', html)
    html = _restore_code(html, stash)

    def _swap(m):
        cid = pick_id(m.group(0))
        if not cid:
            return m.group(0)
        return f'<tg-emoji emoji-id="{cid}">{m.group(0)}</tg-emoji>'

    # Replace emoji only in visible text, never inside HTML tags/attributes.
    # Splitting around tags also preserves any existing <img src="tg://emoji..."/>.
    parts = re.split(r"(<[^>]*>)", html)
    for i in range(0, len(parts), 2):
        parts[i] = _EMOJI_RE.sub(_swap, parts[i])
        parts[i] = _EMOJI_SINGLE_RE.sub(_swap, parts[i])
    html = "".join(parts)
    html = _restore_custom_emojis(html, emoji_stash)
    return _nl_fix(html)


async def url_ok(url: str) -> bool:
    """HEAD-check an image URL (best effort — assumes yes on any failure)."""
    try:
        resp = await _IMG_HTTP.head(url)
        return resp.status_code == 200
    except Exception:
        return True


def md_to_rich(md: str) -> str:
    """Model markdown into rich-HTML blocks (headings/lists/code/quotes).

    The plugins that show free-form model output call this instead of wrapping
    everything in <p>, so the reply renders with Telegram's native Structures
    instead of one flat paragraph.
    """
    lines = md.splitlines()
    out: list[str] = []
    para: list[str] = []

    def flush_para() -> None:
        if para:
            out.append("<p>" + "<br>".join(rich_esc(x) for x in para) + "</p>")
            para.clear()

    i = 0
    in_code = False
    code: list[str] = []
    lang = ""
    while i < len(lines):
        s = lines[i].strip()
        if s.startswith("```"):
            if in_code:
                body = rich_esc("\n".join(code))
                out.append(f"<pre><code class=\"language-{rich_esc(lang)}\">{body}</code></pre>" if lang else f"<pre>{body}</pre>")
                code, lang = [], ""
                in_code = False
            else:
                in_code = True
                lang = s[3:].strip()
            i += 1
            continue
        if in_code:
            code.append(lines[i])
            i += 1
            continue
        if not s:
            flush_para()
            i += 1
            continue
        if s in ("---", "***", "___"):
            flush_para()
            out.append("<hr/>")
            i += 1
            continue
        hash_n = len(s) - len(s.lstrip("#"))
        if 1 <= hash_n <= 6 and s[hash_n:hash_n + 1] == " ":
            flush_para()
            out.append(f"<h4>{rich_esc(s[hash_n + 1:].strip())}</h4>")
            i += 1
            continue
        if s.startswith(("- ", "* ", "• ")):
            flush_para()
            items: list[str] = []
            while i < len(lines):
                t = lines[i].strip()
                if t.startswith(("- ", "* ", "• ")):
                    items.append(t[2:])
                    i += 1
                else:
                    break
            out.append("<ul>" + "".join(f"<li>{rich_esc(x)}</li>" for x in items) + "</ul>")
            continue
        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", s)
        if numbered:
            flush_para()
            items2: list[str] = []
            while i < len(lines):
                m2 = re.match(r"^(\d+)[.)]\s+(.*)$", lines[i].strip())
                if m2:
                    items2.append(m2.group(2))
                    i += 1
                else:
                    break
            out.append("<ol>" + "".join(f"<li>{rich_esc(x)}</li>" for x in items2) + "</ol>")
            continue
        if s.startswith(">"):
            flush_para()
            buf = [s[1:].strip()]
            while i + 1 < len(lines) and lines[i + 1].strip().startswith(">"):
                i += 1
                buf.append(lines[i].strip()[1:].strip())
            out.append("<blockquote>" + rich_esc(" ".join(x for x in buf if x)) + "</blockquote>")
            i += 1
            continue
        para.append(lines[i])
        i += 1
    flush_para()
    return "\n".join(out)


def _client():
    """The bot's MTProto client, once pyroblack rich methods are the path."""
    from AoiMusic import app as app_client

    return app_client


import time

# Dead image protection: one un-fetchable remote <img> makes Telegram reject
# the whole rich card, so known-bad URLs are dropped up front and, if a send/
# edit still fails while the HTML carries images, it is retried once without
# them (tg://emoji imgs are custom-emoji entities and are never touched).
_IMG_TAG_RE = re.compile(r'<img\s+src="((?!tg://)[^"]*)"[^>]*>')
_BAD_IMG: dict[str, float] = {}
_IMG_TTL = 1800  # 30 min — a URL may come back, keep re-checking after that.

# Some messages (old panels, chats where Telegram upgraded the rich message)
# reject EVERY edit that carries a remote <img> — each ticker tick then
# burned one failed invoke + one stripped retry and spammed the log ~200
# times/hour. After the first stripped-retry lands for a mid, remember it
# and pre-strip images for that message's later edits.
_IMG_BROKEN_MIDS: dict[tuple[int, int], float] = {}
_IMG_MID_TTL = 3600  # 1h — a message may get a fresh body with a new image.


def _mid_imgs_broken(chat_id: int, message_id: int) -> bool:
    exp = _IMG_BROKEN_MIDS.get((int(chat_id), int(message_id)))
    if exp is None:
        return False
    if exp <= time.time():
        _IMG_BROKEN_MIDS.pop((int(chat_id), int(message_id)), None)
        return False
    return True


def _mark_mid_imgs_broken(chat_id: int, message_id: int) -> None:
    key = (int(chat_id), int(message_id))
    # keep the dict small: drop everything expired on the way in
    now = time.time()
    for k in [k for k, exp in _IMG_BROKEN_MIDS.items() if exp <= now]:
        _IMG_BROKEN_MIDS.pop(k, None)
    _IMG_BROKEN_MIDS[key] = now + _IMG_MID_TTL


def _img_urls(html: str) -> list[str]:
    """Remote (non-tg://) image URLs inside the html."""
    return _IMG_TAG_RE.findall(html)


def _strip_imgs(html: str) -> str:
    """Remove every remote <img> tag; tg://emoji tags survive untouched."""
    return _IMG_TAG_RE.sub("", html)


def _mark_bad_imgs(html: str) -> None:
    """Blacklist the remote image urls found in html for _IMG_TTL."""
    exp = time.time() + _IMG_TTL
    for url in _IMG_TAG_RE.findall(html):
        if url.startswith(("http://", "https://")):
            _BAD_IMG[url] = exp


def _drop_bad_imgs(html: str) -> str:
    """Drop <img> tags whose URL is blacklisted; expired entries are pruned."""
    if not _BAD_IMG:
        return html
    now = time.time()
    for url in [u for u, exp in _BAD_IMG.items() if exp <= now]:
        _BAD_IMG.pop(url, None)
    if not _BAD_IMG:
        return html
    return _IMG_TAG_RE.sub(
        lambda m: "" if m.group(1) in _BAD_IMG else m.group(0), html
    )


# Per-chat reply anchor: Telegram strips custom emoji when a standalone
# rich message is edited (tdlib/telegram-bot-api#904) — edits keep them as
# long as the message was originally sent as a reply. Cards and status
# messages always carry one, so every later edit stays premium.
_ANCHORS: dict[int, tuple[int, float]] = {}


def set_anchor(chat_id: int, message_id: int | None) -> None:
    if message_id:
        _ANCHORS[chat_id] = (int(message_id), time.time())


def get_anchor(chat_id: int) -> int | None:
    entry = _ANCHORS.get(chat_id)
    if not entry:
        return None
    mid, ts = entry
    if time.time() - ts > 21600:
        _ANCHORS.pop(chat_id, None)
        return None
    return mid


def forget_anchor(chat_id: int, message_id: int | None) -> None:
    """Drop the chat's anchor, but only if it is exactly this message id.

    Every caller that deletes a card/anchor outside delete_msg must use this
    so the next send does not reply to a dead message (Telegram then strips
    the premium emoji from the whole card).
    """
    if message_id is None:
        return
    entry = _ANCHORS.get(chat_id)
    if entry and int(entry[0]) == int(message_id):
        _ANCHORS.pop(chat_id, None)


async def _msg_alive(chat_id: int, mid: int) -> bool:
    """Is this message still there? False only on a definitive miss.

    Any client/API hiccup answers True — a stale-but-alive reply beats a
    pointless temp anchor round-trip.
    """
    try:
        res = await _client().get_messages(chat_id, int(mid))
        if isinstance(res, (list, tuple)):
            res = res[0] if res else None
        if res is None:
            return False
        if getattr(res, "empty", False):
            return False
        return True
    except Exception:  # noqa: BLE001
        return True


async def _anchor_updater(client, message):
    try:
        if message.chat is not None and message.id:
            set_anchor(message.chat.id, message.id)
    except Exception:  # noqa: BLE001
        pass
    raise ContinuePropagation


def register_anchor_hook(app) -> None:
    """Keep a per-chat reply anchor from every incoming message.

    Telegram strips custom emoji when a standalone rich message is edited
    (tdlib/telegram-bot-api#904); edits keep them if the message was first
    sent as a reply. Every card/status send then replies to the last
    message we saw in that chat, so its later edits stay premium.
    """
    from pyrogram.handlers import MessageHandler

    app.add_handler(MessageHandler(_anchor_updater), group=-200)


def _mid_from_updates(exc):
    """Message id out of the raw Updates that pyroblack failed to parse."""
    tb = exc.__traceback__
    r = None
    while tb is not None:
        if tb.tb_frame.f_code.co_name == "send_rich_message":
            r = tb.tb_frame.f_locals.get("r")
        tb = tb.tb_next
    if r is None:
        return None
    mid = getattr(r, "id", None)
    if isinstance(mid, int):
        return mid
    ups = getattr(r, "updates", None)
    if ups is not None and not isinstance(ups, (list, tuple)):
        ups = getattr(ups, "updates", None) or []
    for u in ups or []:
        mid = getattr(getattr(u, "message", None), "id", None)
        if isinstance(mid, int):
            return mid
        # Bot sendMessage replies carry Updates(updateMessageID): the id
        # sits directly on the UpdateMessageID entry, no message payload.
        mid = getattr(u, "id", None)
        if isinstance(mid, int):
            return mid
    return None


_MSG_IS_PHOTO: dict[tuple[int, int], bool] = {}

_ALLOWED_TG_TAGS = frozenset({
    "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
    "code", "pre", "a", "blockquote", "tg-spoiler", "emoji",
})
_ANY_TAG_RE = re.compile(r"<(/?)([a-zA-Z0-9_-]+)([^>]*)>")


def _rich_html_to_tg_html(html: str) -> tuple[str, str | None]:
    """Convert rich HTML into clean standard Telegram HTML + optional first image URL."""
    if not html:
        return "", None

    img_url = None
    m_img = _IMG_TAG_RE.search(html)
    if m_img:
        u = (m_img.group(1) or "").strip()
        if (u.startswith(("http://", "https://")) or os.path.isfile(u)) and u not in _BAD_IMG:
            img_url = u
    text = _beautify(_IMG_TAG_RE.sub("", html))

    # Convert <tg-emoji emoji-id="ID">FB</tg-emoji> -> <emoji id="ID">FB</emoji>
    text = re.sub(
        r"""<tg-emoji\s+emoji-id=(?:"|')?(\d+)(?:"|')?\s*>""",
        r'<emoji id="\1">',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"</tg-emoji>", "</emoji>", text, flags=re.IGNORECASE)

    # Convert rich structural tags into standard Telegram HTML
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<hr\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<h[1-6]\b[^>]*>", "<b>", text, flags=re.IGNORECASE)
    text = re.sub(r"</h[1-6]>", "</b>\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p\b[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<aside\b[^>]*>", "<blockquote>", text, flags=re.IGNORECASE)
    text = re.sub(r"</aside>", "</blockquote>\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<mark\b[^>]*>", "<b>", text, flags=re.IGNORECASE)
    text = re.sub(r"</mark>", "</b>", text, flags=re.IGNORECASE)
    text = re.sub(r"<footer\b[^>]*>", "\n<i>", text, flags=re.IGNORECASE)
    text = re.sub(r"</footer>", "</i>", text, flags=re.IGNORECASE)

    # Strip any remaining unsupported HTML tags while keeping allowed Telegram HTML tags
    def _filter_tag(match):
        name = match.group(2).lower()
        if name in _ALLOWED_TG_TAGS:
            return match.group(0)
        return ""

    text = _ANY_TAG_RE.sub(_filter_tag, text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, img_url


async def send_rich(
    chat_id: int, html: str, reply_markup=None, reply_to: int | None = None
) -> int | None:
    """Send a normal compact Telegram photo/text message (replaces full-width rich message)."""
    from pyrogram.enums import ParseMode
    from pyrogram.errors import ChannelPrivate, ChatWriteForbidden
    from pyrogram.types import ReplyParameters

    html, reply_markup = _extract_inline_keyboard(html, reply_markup)
    tg_html, img_url = _rich_html_to_tg_html(_drop_bad_imgs(html))
    if not tg_html and not img_url:
        return None

    c = _client()
    common_kw = {"parse_mode": ParseMode.HTML}
    if reply_markup is not None:
        common_kw["reply_markup"] = reply_markup
    if reply_to:
        common_kw["reply_parameters"] = ReplyParameters(message_id=int(reply_to))

    try:
        if img_url and len(tg_html) <= 1024:
            try:
                msg = await c.send_photo(
                    chat_id=chat_id,
                    photo=img_url,
                    caption=tg_html or None,
                    **common_kw,
                )
                mid = int(msg.id)
                _MSG_IS_PHOTO[(int(chat_id), mid)] = True
                set_anchor(chat_id, mid)
                return mid
            except (ChatWriteForbidden, ChannelPrivate):
                raise
            except Exception as img_exc:
                log.warning(
                    "[msg send] send_photo failed (%s) — falling back to text (chat=%s)",
                    type(img_exc).__name__, chat_id,
                )
                _mark_bad_imgs(html)

        msg = await c.send_message(
            chat_id=chat_id,
            text=tg_html or "🎵",
            **_no_preview_kwargs(),
            **common_kw,
        )
        mid = int(msg.id)
        _MSG_IS_PHOTO[(int(chat_id), mid)] = False
        set_anchor(chat_id, mid)
        return mid
    except (ChatWriteForbidden, ChannelPrivate) as e:
        log.info("send skipped — no write access in %s (%s)", chat_id, type(e).__name__)
        return None
    except Exception:
        log.warning("send_rich (normal message) failed in %s", chat_id, exc_info=True)
        return None


async def edit_rich(chat_id: int, message_id: int, html: str, reply_markup=None) -> bool:
    """Edit a normal Telegram photo caption or text message cleanly."""
    from pyrogram.enums import ParseMode
    from pyrogram.errors import ChannelPrivate, ChatWriteForbidden, MessageIdInvalid, MessageNotModified

    if not message_id:
        return False

    html, reply_markup = _extract_inline_keyboard(html, reply_markup)
    tg_html, img_url = _rich_html_to_tg_html(_drop_bad_imgs(html))
    if not tg_html:
        return False

    c = _client()
    key = (int(chat_id), int(message_id))
    is_photo = _MSG_IS_PHOTO.get(key)
    if is_photo is None:
        is_photo = bool(img_url)

    async def _do_edit_caption() -> bool:
        await c.edit_message_caption(
            chat_id=chat_id,
            message_id=int(message_id),
            caption=tg_html[:1024],
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        _MSG_IS_PHOTO[key] = True
        return True

    async def _do_edit_text() -> bool:
        await c.edit_message_text(
            chat_id=chat_id,
            message_id=int(message_id),
            text=tg_html,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            **_no_preview_kwargs(),
        )
        _MSG_IS_PHOTO[key] = False
        return True

    first, second = (_do_edit_caption, _do_edit_text) if is_photo else (_do_edit_text, _do_edit_caption)
    try:
        return await first()
    except MessageNotModified:
        return True
    except (MessageIdInvalid, ChatWriteForbidden, ChannelPrivate):
        return False
    except Exception:
        try:
            return await second()
        except MessageNotModified:
            return True
        except (MessageIdInvalid, ChatWriteForbidden, ChannelPrivate):
            return False
        except Exception:
            log.warning("edit_rich (normal message) failed in %s (mid=%s)", chat_id, message_id, exc_info=True)
            return False


async def send_rich_draft(chat_id: int, draft_id: int, html: str) -> bool:
    """No-op: rich message drafts disabled so all bot messages stay normal compact bubbles."""
    return False


async def ai_stream(chat_id: int, chat_id_int: int, chunk_html: str) -> None:
    """Convenience wrapper the AI lane calls with progressive HTML."""
    await send_rich_draft(chat_id, chat_id_int, chunk_html)


async def edit_reply_markup(chat_id: int, message_id: int, reply_markup) -> bool:
    """Edit only the inline keyboard via the client (keeps emoji icons)."""
    from pyrogram.raw import functions as raw_functions

    try:
        c = _client()
        await c.invoke(
            raw_functions.messages.EditMessage(
                peer=await c.resolve_peer(chat_id),
                id=int(message_id),
                reply_markup=await reply_markup.write(c),
            )
        )
        return True
    except Exception:
        log.warning("reply-markup edit failed in %s", chat_id, exc_info=True)
        return False


async def delete_msg(chat_id: int, message_id: int) -> bool:
    forget_anchor(chat_id, message_id)
    try:
        # A stuck delete RPC (observed: channels.DeleteMessages losing its
        # response in supergroups) must never wedge its caller — cap it.
        await asyncio.wait_for(
            _client().delete_messages(chat_id, [int(message_id)]), 10
        )
        return True
    except Exception:
        log.warning("delete failed in %s for %s", chat_id, message_id, exc_info=True)
        return False


class RichMystic:
    """Drop-in status-message adapter for code that expects a pyrogram Message.

    edit_text/delete/reply_text map onto the MTProto rich-message helpers,
    so stream()/helpers can drive a rich card instead of a plain bubble.
    """

    def __init__(self, chat_id: int, message_id: int):
        self.chat_id = chat_id
        self.id = message_id

    async def edit_text(self, text: str, reply_markup=None, **kw) -> bool:
        if not self.id:
            return False
        return await edit_rich(self.chat_id, self.id, _style_status(text), reply_markup=reply_markup)

    async def edit(self, text: str, reply_markup=None, **kw) -> bool:
        return await self.edit_text(text, reply_markup=reply_markup, **kw)

    async def reply_text(self, text: str, reply_markup=None, **kw) -> "RichMystic":
        mid = await send_rich(self.chat_id, _style_status(text), reply_markup=reply_markup)
        return RichMystic(self.chat_id, mid) if mid else self

    async def delete(self, **kw) -> bool:
        if not self.id:
            return False
        return await delete_msg(self.chat_id, self.id)


def _style_status(text: str) -> str:
    """Turn a plain status/error string into a styled rich body.

    Every bot status flows through here (play errors, download progress,
    join failures …), so they all ship the same card look instead of a
    bare <p> bubble: leading emoji becomes a premium-emoji heading, the
    first line is the title, the rest sits in a callout block.
    """
    if not isinstance(text, str):
        text = str(text)
    if "<tg-emoji" in text or "<h4" in text:
        # already richly built by the caller — leave it alone
        return text
    esc = rich_esc(text)
    first, _, rest = esc.partition("\n")
    lead = ""
    if first and first[0].isprintable() and not first[0].isalnum() \
            and not first[0].isspace() and first[0] not in "&<*-–—([{~`\"'’“_#!?.,:;/|":
        lead = first[0]
        first = first[1:].lstrip()
    if len(first) > 64:  # long first lines are bodies, not titles
        first, rest = lead + " " + first, first + ("\n" + rest if rest else "")
        lead = ""
    heading = f"<h4>{pemoji(lead or '💠')} {first.strip()}</h4>"
    if rest.strip():
        return heading + f"<blockquote>{rest.strip()}</blockquote>"
    return heading


def _no_preview_kwargs() -> dict:
    try:
        from pyrogram.types import LinkPreviewOptions
        return {"link_preview_options": LinkPreviewOptions(is_disabled=True)}
    except Exception:
        return {"disable_web_page_preview": True}


def _normalize_reply_kwargs(kw: dict) -> dict:
    clean = dict(kw)
    if clean.pop("disable_web_page_preview", False):
        clean.setdefault("link_preview_options", _no_preview_kwargs().get("link_preview_options"))
        if clean.get("link_preview_options") is None:
            clean.pop("link_preview_options", None)
    clean.pop("quote", None)
    return clean


async def status_card(chat_id: int, text: str, fallback_message=None, **_kw):
    """Send a rich 'working on it...' card and return its adapter handle.

    fallback_message: a pyrogram Message used verbatim (reply) when the rich
    send fails, so the status stays visible instead of silently no-oping.
    """
    if fallback_message is not None:
        set_anchor(chat_id, fallback_message.id)
    mid = await send_rich(
        chat_id, _style_status(text), reply_to=get_anchor(chat_id)
    )
    if not mid and fallback_message is not None:
        try:
            return await fallback_message.reply_text(
                text, **_no_preview_kwargs()
            )
        except Exception:  # noqa: BLE001
            return RichMystic(chat_id, None)
    return RichMystic(chat_id, mid)


def ids_of(m) -> tuple[int | None, int | None]:
    """(chat_id, message_id) for a RichMystic or a pyrogram Message."""
    chat_id = getattr(m, "chat_id", None)
    if chat_id is None:
        chat_id = getattr(getattr(m, "chat", None), "id", None)
    return chat_id, getattr(m, "id", None)


async def send_note(chat_id: int, text: str, heading: str | None = None,
                    style: str = "primary") -> int | None:
    """One-off rich notice card (admin actions, statuses, log lines).

    Callout-blockquote body so the note pops against chat background, and a
    single centered close row.
    """
    body = ""
    if heading:
        body += rich_heading(f"{pemoji('🎵')} {rich_esc(heading)}", level=4)
    body += rich_note(rich_esc(text).replace("\n", "<br>"))
    body += rich_button_row(
        rich_button("ᴄʟᴏsᴇ", callback_data="close", style="danger", emoji="CROSS_BADGE"),
        align="center",
    )
    return await send_rich(chat_id, body)


async def edit_any(mystic, text: str, reply_markup=None) -> bool:
    """Rich-edit a message; pyrogram edit_text is the failure fallback.

    Accepts a RichMystic or a pyrogram Message so every edit site can stay
    row-blind: first try the rich edit, then the classic edit.
    """
    chat_id, mid = ids_of(mystic)
    if chat_id and mid:
        try:
            if await edit_rich(chat_id, mid, f"<p>{rich_esc(text)}</p>", reply_markup=reply_markup):
                return True
        except Exception:  # noqa: BLE001
            pass
    try:
        await mystic.edit_text(text, reply_markup=reply_markup,
                               **_no_preview_kwargs())
        return True
    except Exception:  # noqa: BLE001
        return False


async def reply_note(message, text: str, **kw) -> int | None:
    """Reply to a command with a rich notice card; plain reply is the fallback."""
    safe_kw = _normalize_reply_kwargs(kw)
    try:
        mid = await send_rich(message.chat.id, f"<p>{rich_esc(text)}</p>")
        if not mid and safe_kw.get("reply_markup") is not None:
            await message.reply_text(text, **safe_kw)
        return mid
    except Exception:  # noqa: BLE001
        try:
            await message.reply_text(text, **safe_kw)
        except Exception:  # noqa: BLE001
            pass
        return None


async def reply_rich(message, text: str, reply_markup=None) -> int | None:
    """Reply with a rich card; returns the new message id (or None)."""
    try:
        return await send_rich(message.chat.id, f"<p>{rich_esc(text)}</p>",
                               reply_markup=reply_markup)
    except Exception:  # noqa: BLE001
        return None

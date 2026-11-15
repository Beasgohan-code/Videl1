"""
Rich screens – one layout, three seamless tiers (Bot API 10.3 · WzGram Layer 232 · Classic HTML).

    doc = Doc("📊", "Bot statistics", "live server numbers")
    doc.table([("CPU", "12 %"), ("RAM", "40 %")], header=("Item", "Value"))
    doc.h("💾", "Disk").text("…").details("More", other_doc)
    await rich.reply(message, doc, reply_markup=kb)

Rendering & transport tiers:
  1. Bot API 10.3 bridge (``aiogram``, when enabled for the primary bot): sends
     native ``sendRichMessage`` / ``editMessageText(rich_message=…)`` with styled buttons.
  2. Native WzGram MTProto Layer 232 (``richgram`` + ``wzgram.types.InputRichMessage``):
     sends native rich blocks, inline ``<tg-button>`` rows, ``<tg-slideshow>`` /
     ``<tg-collage>`` media attachments, ``ButtonStyle`` keyboards, and
     ``EphemeralMessageParameters`` directly over MTProto — including on clone workers!
  3. Classic FileStore HTML fallback (``core/style.py``: ━ bars, 𝗕𝗢𝗟𝗗 headings,
     ꜱᴍᴀʟʟ ᴄᴀᴘꜱ labels, expandable blockquotes) whenever rich mode isn't applicable.
"""
from collections import OrderedDict
import html as _html
import logging
import re

import richgram as rg
from pyrogram.types import EphemeralMessageParameters, InputRichMessage, InputRichMessageMedia

from config import EPHEMERAL_REPLIES, WZGRAM_NATIVE_RICH
from core import style

log = logging.getLogger("videl.rich")

# Re-export richgram primitives so plugins & modules have a single import surface.
RICH_AVAILABLE = rg.RICH_AVAILABLE
RichDraft = rg.RichDraft
rich_heading = rg.rich_heading
rich_note = rg.rich_note
rich_table = rg.rich_table
rich_kv_table = rg.rich_kv_table
rich_details = rg.rich_details
rich_code = rg.rich_code
rich_pre = rg.rich_pre
rich_list = rg.rich_list
rich_hr = rg.rich_hr
rich_footer = rg.rich_footer
rich_pull_quote = rg.rich_pull_quote
rich_math = rg.rich_math
rich_thinking = rg.rich_thinking
rich_img = rg.rich_img
rich_button = rg.rich_button
rich_button_row = rg.rich_button_row
rich_to_plain = rg.rich_to_plain
add_html_hook = rg.add_html_hook

# Callback data whose handlers are safe on a rich message (Bot API / WzGram edits, new messages, answers).
SAFE_CALLBACKS = re.compile(
    r"^(close_btn|start_btn|help_btn|about_btn|settings_btn|v_settings|hub_save|buy_premium|back_menu|help_enc|"
    r"help_tools|help_admin|help_clone|clone_help|clone_about|help_saver|help_rename|cmd_list_btn|"
    r"premium_plans_btn|myplan_back_btn|settings_back_btn|dump_chat_btn|thumb_btn|caption_btn|user_stats_btn|"
    r"rn:[\w:]+|rnlb:\w+|status( (ref|page \d+ \w+|filter \w+|cancel \w+))?|refer_btn|noop(:.*)?|stars_buy:\d+|stars_sub|sub_toggle:(cancel|resume)|"
    r"plg_mgr:(list|(info|tog|rel):[\w_-]+)|plg_stg:(ok|no):[\w_-]+|plg_imdb:(pick|back):[\w:_-]+|"
    r"plg_ani:v:\w+:\d+:\w+:\d+|plg_mdl:(pick|back):[\w:/._-]+|"
    r"plg_sess:(export|cancel)|plg_gmod:[\w:_-]+|chk_(tog|all|del):[\w:_-]+|spd_rerun|"
    r"adm_st:(main|runtime|health)|bkr:(dedupe_ok|encrypt_ok))$"
)


STATUS_ICONS = {
    "success": "✅",
    "ok": "✅",
    "warning": "⚠️",
    "warn": "⚠️",
    "error": "❌",
    "fail": "❌",
    "loading": "⏳",
    "progress": "⏳",
    "empty": "📭",
    "info": "ℹ️",
}


def esc(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=False)


def custom_emoji(emoji_id: str | int, fallback: str = "✨") -> "Raw":
    """Safe ``<tg-emoji>`` wrapper — validates *emoji_id* is strictly numeric and escapes *fallback*."""
    eid = str(emoji_id or "").strip()
    fb = esc(fallback or "✨")
    if eid.isdigit() and int(eid) > 0:
        return Raw(f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>')
    return Raw(fb)


def rich_progress(current: float, total: float = 100.0, width: int = 10,
                  label: str = "", detail: str = "") -> str:
    """Format a compact progress bar with escaped label & detail (safe in rich and classic HTML)."""
    pct = 0.0 if total <= 0 else max(0.0, min(100.0, (float(current) / float(total)) * 100.0))
    width = max(4, min(20, int(width)))
    filled = int(round((pct / 100.0) * width))
    bar = "▰" * filled + "▱" * (width - filled)
    head = f"<b>{esc(label)}:</b> " if label else ""
    tail = f" · <i>{esc(detail)}</i>" if detail else ""
    return f"{head}<code>{bar}</code> <b>{pct:.0f}%</b>{tail}"


def rich_status(kind: str, title: str, detail: str = "") -> str:
    """Standardized status indicator line with HTML-escaped *title* and *detail*."""
    icon = STATUS_ICONS.get((kind or "info").lower(), "ℹ️")
    line = f"{icon} <b>{esc(title)}</b>"
    if detail:
        line += f" — {esc(detail)}"
    return line


def clamp_html(html_text: str, limit: int = 4096) -> str:
    """Truncate *html_text* safely without leaving a broken tag or unbalanced inline element."""
    if len(html_text) <= limit:
        return html_text
    cut = html_text[: max(0, limit - 64)]
    # Drop any trailing half-open '<...' tag or '&...' entity
    last_lt = cut.rfind("<")
    last_gt = cut.rfind(">")
    if last_lt > last_gt:
        cut = cut[:last_lt]
    last_amp = cut.rfind("&")
    if last_amp != -1 and ";" not in cut[last_amp:] and len(cut) - last_amp <= 10:
        cut = cut[:last_amp]
    # Close any remaining open tags in LIFO order
    stack: list[str] = []
    for m in re.finditer(r"<(/?)([a-zA-Z0-9-]+)(?:\s[^>]*)?(/?)>", cut):
        closing, tag, self_close = m.group(1), m.group(2).lower(), m.group(3)
        if self_close or tag in ("hr", "br", "img"):
            continue
        if closing:
            if tag in stack:
                while stack:
                    popped = stack.pop()
                    if popped == tag:
                        break
        else:
            stack.append(tag)
    suffix = "…" + "".join(f"</{t}>" for t in reversed(stack))
    return cut + suffix


def _cell(v) -> str:
    """Table cells / values: plain values are escaped, Raw() values pass through."""
    return v.html if isinstance(v, Raw) else esc(v)


class Raw:
    """Pre-built inline HTML (b / i / code / a / tg-spoiler) – used as-is in both renderings."""
    __slots__ = ("html",)

    def __init__(self, html: str):
        self.html = html

    def __str__(self):
        return self.html


def code(v) -> Raw:
    return Raw(f"<code>{esc(v)}</code>")


def bold(v) -> Raw:
    return Raw(f"<b>{esc(v)}</b>")


def spoiler(v) -> Raw:
    return Raw(f"<tg-spoiler>{esc(v)}</tg-spoiler>")


def link(text, url) -> Raw:
    return Raw(f'<a href="{esc(url)}">{esc(text)}</a>')


class Doc:
    def __init__(self, emoji: str = "", title: str = "", sub: str = ""):
        self.emoji, self.title, self.sub = emoji, title, sub
        self.blocks = []
        self.media: list[InputRichMessageMedia] = []
        self.preview_url: str = ""
        self.skip_entities: bool = False

    # ── configuration ──
    def with_preview(self, url: str):
        """Attach an optional large link-preview image URL for classic / WzGram sends."""
        self.preview_url = (url or "").strip()
        return self

    def attach_media(self, media_id: str, media_source):
        """Attach an ``InputRichMessageMedia`` item for ``attach://<id>`` rich blocks."""
        if media_source:
            self.media.append(InputRichMessageMedia(id=media_id, media=media_source))
        return self

    # ── building ──
    def h(self, emoji: str, title: str):
        self.blocks.append(("h", emoji, title))
        return self

    def text(self, html_text: str):
        """Inline HTML paragraph (escape user values yourself / use Raw helpers)."""
        self.blocks.append(("p", html_text))
        return self

    def table(self, rows, header=None, caption: str = "", align=None, compact: bool = False):
        rows = [tuple(r) for r in rows]
        if rows:
            self.blocks.append(("table", rows, tuple(header) if header else None, caption, align, compact))
        return self

    def kv_table(self, pairs, key_header: str = "Item", val_header: str = "Value", caption: str = ""):
        """Two-column key/value table helper aligned with ``richgram.rich_kv_table``."""
        rows = [(k, v) for k, v in pairs if v is not None and str(v) != ""]
        if rows:
            self.table(rows, header=(key_header, val_header), caption=caption)
        return self

    def items(self, items, ordered: bool = False):
        items = list(items)
        if items:
            self.blocks.append(("list", items, ordered))
        return self

    def details(self, summary: str, body: "Doc", open_: bool = False):
        if body.blocks:
            self.blocks.append(("details", summary, body, open_))
        return self

    def quote(self, html_text: str):
        self.blocks.append(("quote", html_text))
        return self

    def pull_quote(self, text_value: str):
        """Centered callout quote (``richgram.rich_pull_quote`` → ``<aside>``)."""
        if text_value:
            self.blocks.append(("pull_quote", text_value))
        return self

    def pre(self, code_text: str, language: str = ""):
        """Syntax-highlighted code block (``richgram.rich_pre``)."""
        if code_text is not None:
            self.blocks.append(("pre", str(code_text), language or ""))
        return self

    def math(self, expression: str):
        """Mathematical formula block (``richgram.rich_math`` → ``<tg-math-block>``)."""
        if expression:
            self.blocks.append(("math", str(expression)))
        return self

    def thinking(self, note: str = ""):
        """Thinking indicator block (``richgram.rich_thinking`` → ``<tg-thinking>``)."""
        self.blocks.append(("thinking", note))
        return self

    def buttons_row(self, *buttons, align: str = ""):
        """Inline ``<tg-button-row>`` inside the rich message body (WzGram / Bot API 10.3)."""
        btns = [b for b in buttons if b]
        if btns:
            self.blocks.append(("buttons_row", btns, align or None))
        return self

    def slideshow(self, sources: list[str], caption: str = ""):
        """Rich ``<tg-slideshow>`` gallery block (with graceful classic link list fallback)."""
        srcs = [s for s in sources if s]
        if srcs:
            self.blocks.append(("gallery", "tg-slideshow", srcs, caption))
        return self

    def collage(self, sources: list[str], caption: str = ""):
        """Rich ``<tg-collage>`` grid block (with graceful classic link list fallback)."""
        srcs = [s for s in sources if s]
        if srcs:
            self.blocks.append(("gallery", "tg-collage", srcs, caption))
        return self

    def divider(self):
        self.blocks.append(("hr",))
        return self

    def status(self, kind: str, title: str, detail: str = ""):
        """Append a standardized status indicator paragraph."""
        self.blocks.append(("p", rich_status(kind, title, detail)))
        return self

    def progress(self, current: float, total: float = 100.0, width: int = 10,
                 label: str = "", detail: str = ""):
        """Append a standardized progress bar paragraph."""
        self.blocks.append(("p", rich_progress(current, total=total, width=width, label=label, detail=detail)))
        return self

    def emoji(self, emoji_id: str | int, fallback: str = "✨") -> "Raw":
        """Convenience wrapper returning a safe ``<tg-emoji>`` ``Raw`` token."""
        return custom_emoji(emoji_id, fallback)

    def footer(self, text: str):
        self.blocks.append(("footer", text))
        return self

    # ── native rich HTML (Bot API sendRichMessage & WzGram InputRichMessage, html mode) ──
    def rich(self) -> str:
        out = []
        if self.title:
            out.append(f"<h1>{esc((self.emoji + ' ' if self.emoji else '') + self.title)}</h1>")
        if self.sub:
            out.append(f"<p><i>{style.sc(esc(self.sub))}</i></p>")
        out += self._rich_blocks()
        return clamp_html("".join(out), 8192)

    def plain(self) -> str:
        """Plain-text rendering stripped via ``richgram.rich_to_plain``."""
        return rich_to_plain(self.rich())

    def _rich_blocks(self) -> list:
        out = []
        for b in self.blocks:
            kind = b[0]
            if kind == "h":
                out.append(f"<h3>{esc(b[1] + ' ' if b[1] else '')}{style.sc(esc(b[2]))}</h3>")
            elif kind == "p":
                out.append(f"<p>{b[1]}</p>")
            elif kind == "table":
                _, rows, header, caption, align, compact = b
                t = ["<table bordered striped>"]
                if caption:
                    t.append(f"<caption>{esc(caption)}</caption>")
                if header:
                    t.append("<tr>" + "".join(f"<th>{style.sc(esc(x))}</th>" for x in header) + "</tr>")
                for r in rows:
                    cells = []
                    for i, v in enumerate(r):
                        a = (align[i] if align and i < len(align) else None) or "left"
                        cells.append(f'<td align="{a}">{_cell(v)}</td>' if a != "left" else f"<td>{_cell(v)}</td>")
                    t.append("<tr>" + "".join(cells) + "</tr>")
                t.append("</table>")
                out.append("".join(t))
            elif kind == "list":
                tag = "ol" if b[2] else "ul"
                out.append(f"<{tag}>" + "".join(f"<li>{_cell(i)}</li>" for i in b[1]) + f"</{tag}>")
            elif kind == "details":
                _, summary, body, open_ = b
                out.append(f"<details{' open' if open_ else ''}><summary>{esc(summary)}</summary>"
                           + "".join(body._rich_blocks()) + "</details>")
            elif kind == "quote":
                out.append(f"<blockquote>{b[1]}</blockquote>")
            elif kind == "pull_quote":
                out.append(rich_pull_quote(b[1]))
            elif kind == "pre":
                out.append(rich_pre(b[1], language=b[2] or None))
            elif kind == "math":
                out.append(rich_math(b[1]))
            elif kind == "thinking":
                out.append(rich_thinking())
            elif kind == "buttons_row":
                out.append(rich_button_row(*b[1], align=b[2]))
            elif kind == "gallery":
                _, tag, srcs, cap = b
                imgs = "".join(rich_img(s) for s in srcs)
                cap_html = rg.rich_caption(esc(cap)) if cap else ""
                out.append(f"<{tag}>{imgs}{cap_html}</{tag}>")
            elif kind == "hr":
                out.append("<hr/>")
            elif kind == "footer":
                out.append(f"<footer>{esc(b[1])}</footer>")
        return out

    # ── classic HTML (FileStore style) ──
    def classic(self) -> str:
        parts = []
        if self.title:
            parts.append(style.hdr(self.emoji, esc(self.title)))
        if self.sub:
            parts.append(f"<i>{style.sc(esc(self.sub))}</i>")
        parts += self._classic_blocks()
        return clamp_html("\n\n".join(p for p in parts if p), 4096)

    def _classic_blocks(self, nested: bool = False) -> list:
        out = []
        for b in self.blocks:
            kind = b[0]
            if kind == "h":
                out.append(style.sec(b[1], esc(b[2])))
            elif kind == "p":
                out.append(b[1])
            elif kind == "table":
                _, rows, header, caption, align, compact = b
                if len(rows[0]) == 2:
                    body = "\n".join(style.row(esc(k) if not isinstance(k, Raw) else k.html, _cell(v))
                                     for k, v in rows)
                else:
                    lines = []
                    if header:
                        lines.append("<b>" + " · ".join(style.sc(esc(x)) for x in header) + "</b>")
                    for r in rows:
                        first = r[0]
                        first = (f"<b>{style.sc(esc(first))}</b>" if isinstance(first, (str, int, float))
                                 else _cell(first))
                        lines.append("◈ " + " · ".join([first] + [_cell(v) for v in r[1:]]))
                    body = "\n".join(lines)
                if caption:
                    body += f"\n<i>{esc(caption)}</i>"
                out.append(body if nested else style.quote(body))
            elif kind == "list":
                marks = (f"<b>{i}.</b>" for i in range(1, len(b[1]) + 1)) if b[2] else iter(lambda: "•", None)
                out.append("\n".join(f"{next(marks)} {_cell(i)}" for i in b[1]))
            elif kind == "details":
                _, summary, body, open_ = b
                inner = "\n\n".join(body._classic_blocks(nested=True))
                out.append(f"<blockquote expandable><b>{esc(summary)}</b>\n{inner}</blockquote>")
            elif kind == "quote":
                out.append(style.quote(b[1]))
            elif kind == "pull_quote":
                out.append(style.quote(f"<i>“{esc(b[1])}”</i>"))
            elif kind == "pre":
                lang_attr = f' class="language-{esc(b[2])}"' if b[2] else ""
                out.append(f"<pre><code{lang_attr}>{esc(b[1])}</code></pre>")
            elif kind == "math":
                out.append(f"<pre><code>{esc(b[1])}</code></pre>")
            elif kind == "thinking":
                if b[1]:
                    out.append(f"<i>💭 {esc(b[1])}</i>")
            elif kind == "buttons_row":
                pass  # inline rich buttons are supplementary to reply_markup in classic mode
            elif kind == "gallery":
                _, _, srcs, cap = b
                links = [f'<a href="{esc(s)}">🖼 Media {idx + 1}</a>' for idx, s in enumerate(srcs) if s.startswith("http")]
                if links or cap:
                    line = " · ".join(links)
                    if cap:
                        line = (line + f"\n<i>{esc(cap)}</i>").strip()
                    out.append(style.quote(line))
            elif kind == "hr":
                out.append(style.BAR)
            elif kind == "footer":
                out.append(f"<i>{esc(b[1])}</i>")
        return out


# ─────────────────────────── sending ───────────────────────────
def markup_ok(markup) -> bool:
    """A rich message may only carry URL / copy / disabled buttons or callbacks from SAFE_CALLBACKS."""
    for row in getattr(markup, "inline_keyboard", None) or []:
        for b in row:
            if getattr(b, "disabled", None) is not None:
                continue
            data = getattr(b, "callback_data", None)
            if data is not None and not SAFE_CALLBACKS.match(str(data)):
                return False
    return True


def _is_main(client) -> bool:
    from core import botapi
    return client is None or botapi.is_main(client)


def _wzgram_rich_enabled(client) -> bool:
    from core.ui import is_wzgram_client
    return bool(WZGRAM_NATIVE_RICH and RICH_AVAILABLE and is_wzgram_client(client))


# (chat_id, message_id) of messages we sent / edited as rich messages – lets the classic edit
# helpers (core.ui.smart_edit / edit_with_preview) replace them if Telegram refuses a rich → text edit.
_RICH_IDS: "OrderedDict[tuple, None]" = OrderedDict()
_RICH_MAX = 5000


def mark(chat_id, message_id, on: bool = True):
    key = (chat_id, message_id)
    if on:
        _RICH_IDS[key] = None
        _RICH_IDS.move_to_end(key)
        while len(_RICH_IDS) > _RICH_MAX:
            _RICH_IDS.popitem(last=False)
    else:
        _RICH_IDS.pop(key, None)


def is_rich(chat_id, message_id) -> bool:
    return (chat_id, message_id) in _RICH_IDS


async def _wzgram_send_rich(client, chat_id: int, doc: Doc, reply_markup=None, reply_to: int = None,
                            ephemeral_user_id: int = None):
    """Send *doc* using WzGram Layer 232 ``send_rich_message``."""
    from core.ui import reply_parameters, wzgram_markup
    try:
        irm = InputRichMessage(
            html=doc.rich(),
            skip_entity_detection=True if doc.skip_entities else None,
            media=doc.media or None,
        )
        ephem = EphemeralMessageParameters(receiver_user_id=ephemeral_user_id) if ephemeral_user_id else None
        sent = await client.send_rich_message(
            chat_id,
            irm,
            disable_web_page_preview=not bool(doc.preview_url),
            reply_parameters=reply_parameters(reply_to),
            reply_markup=wzgram_markup(reply_markup),
            ephemeral_message_parameters=ephem,
        )
        return getattr(sent, "id", None)
    except Exception as e:
        log.debug(f"wzgram send_rich_message fallback ({e})")
        return None


async def _wzgram_edit_rich(client, message, doc: Doc, reply_markup=None) -> bool:
    """Edit *message* into a rich screen via WzGram Layer 232 ``edit_message_text(rich_text=...)``."""
    from core.ui import wzgram_markup
    try:
        irm = InputRichMessage(
            html=doc.rich(),
            skip_entity_detection=True if doc.skip_entities else None,
            media=doc.media or None,
        )
        await client.edit_message_text(
            message.chat.id,
            message.id,
            rich_text=irm,
            rich_text_media=doc.media or None,
            reply_markup=wzgram_markup(reply_markup),
        )
        return True
    except Exception as e:
        if "not modified" in str(e).lower():
            return True
        log.debug(f"wzgram edit_rich fallback ({e})")
        return False


async def send(client, chat_id: int, doc: Doc, reply_markup=None, reply_to: int = None):
    """Rich message when possible, classic FileStore-style HTML otherwise. Returns the message id."""
    from core import botapi
    if _is_main(client) and botapi.enabled() and markup_ok(reply_markup) and not doc.media and not doc.preview_url:
        mid = await botapi.send_rich_html(chat_id, doc.rich(), reply_markup, reply_to=reply_to)
        if mid:
            mark(chat_id, mid)
            return mid
    if _wzgram_rich_enabled(client) and markup_ok(reply_markup) and not doc.preview_url:
        mid = await _wzgram_send_rich(client, chat_id, doc, reply_markup=reply_markup, reply_to=reply_to)
        if mid:
            mark(chat_id, mid)
            return mid
    if doc.preview_url:
        from core.ui import send_with_preview
        res = await send_with_preview(client, chat_id, doc.classic()[:4096], reply_markup=reply_markup,
                                      pic=doc.preview_url, reply_to=reply_to)
        return getattr(res, "id", None)
    from core.ui import HTML, is_wzgram_client, link_preview_options, reply_parameters, wzgram_markup
    if is_wzgram_client(client):
        m = await client.send_message(
            chat_id,
            doc.classic()[:4096],
            reply_markup=wzgram_markup(reply_markup),
            parse_mode=HTML,
            link_preview_options=link_preview_options(is_disabled=True),
            reply_parameters=reply_parameters(reply_to),
        )
        return getattr(m, "id", None)
    m = await client.send_message(chat_id, doc.classic()[:4096], reply_markup=reply_markup, parse_mode=HTML,
                                  disable_web_page_preview=True, reply_to_message_id=reply_to)
    return getattr(m, "id", None)


async def reply(message, doc: Doc, reply_markup=None, quote: bool = False):
    """Answer a command with a rich screen (groups: ephemeral reply for the caller)."""
    from pyrogram.enums import ChatType
    chat = getattr(message, "chat", None)
    client = getattr(message, "_client", None)
    if chat is not None and getattr(chat, "type", None) in (ChatType.GROUP, ChatType.SUPERGROUP):
        u = getattr(message, "from_user", None)
        if u and EPHEMERAL_REPLIES and _wzgram_rich_enabled(client) and markup_ok(reply_markup) and not doc.preview_url:
            mid = await _wzgram_send_rich(
                client,
                message.chat.id,
                doc,
                reply_markup=reply_markup,
                reply_to=message.id if quote else None,
                ephemeral_user_id=u.id,
            )
            if mid:
                return mid
        from core.ui import group_reply
        await group_reply(message, doc.classic()[:4096], reply_markup)
        return None
    from core import botapi
    if _is_main(client) and botapi.enabled() and markup_ok(reply_markup) and not doc.media and not doc.preview_url:
        mid = await botapi.send_rich_html(message.chat.id, doc.rich(), reply_markup,
                                          reply_to=message.id if quote else None)
        if mid:
            mark(message.chat.id, mid)
            return mid
    if _wzgram_rich_enabled(client) and markup_ok(reply_markup) and not doc.preview_url:
        mid = await _wzgram_send_rich(
            client,
            message.chat.id,
            doc,
            reply_markup=reply_markup,
            reply_to=message.id if quote else None,
        )
        if mid:
            mark(message.chat.id, mid)
            return mid
    if doc.preview_url:
        from core.ui import send_with_preview
        res = await send_with_preview(
            client,
            message.chat.id,
            doc.classic()[:4096],
            reply_markup=reply_markup,
            pic=doc.preview_url,
            reply_to=message.id if quote else None,
        )
        return getattr(res, "id", None)
    from core.ui import HTML, is_wzgram_client, link_preview_options, wzgram_markup
    if is_wzgram_client(client):
        m = await message.reply_text(
            doc.classic()[:4096],
            reply_markup=wzgram_markup(reply_markup),
            parse_mode=HTML,
            link_preview_options=link_preview_options(is_disabled=True),
            quote=quote,
        )
        return getattr(m, "id", None)
    m = await message.reply_text(doc.classic()[:4096], reply_markup=reply_markup, parse_mode=HTML,
                                 disable_web_page_preview=True, quote=quote)
    return getattr(m, "id", None)


async def edit(message, doc: Doc, reply_markup=None):
    """Edit a bot message into a rich screen (works on text and rich messages), else smart_edit."""
    from core import botapi
    client = getattr(message, "_client", None)
    if doc.preview_url:
        from core.ui import edit_with_preview
        return await edit_with_preview(client, message, doc.classic()[:4096], reply_markup=reply_markup,
                                       pic=doc.preview_url)
    if _is_main(client) and botapi.enabled() and markup_ok(reply_markup) and not doc.media:
        if getattr(message, "media", None) and getattr(message, "text", None) is None:
            mid = await botapi.send_rich_html(message.chat.id, doc.rich(), reply_markup)
            if mid:
                mark(message.chat.id, mid)
                try:
                    await message.delete()
                except Exception:
                    pass
                return message
        elif await botapi.edit_rich_html(message.chat.id, message.id, doc.rich(), reply_markup):
            mark(message.chat.id, message.id)
            return message
    if _wzgram_rich_enabled(client) and markup_ok(reply_markup):
        if getattr(message, "media", None) and getattr(message, "text", None) is None:
            mid = await _wzgram_send_rich(client, message.chat.id, doc, reply_markup=reply_markup)
            if mid:
                mark(message.chat.id, mid)
                try:
                    await message.delete()
                except Exception:
                    pass
                return message
        elif await _wzgram_edit_rich(client, message, doc, reply_markup=reply_markup):
            mark(message.chat.id, message.id)
            return message
    from core.ui import smart_edit
    return await smart_edit(message, doc.classic()[:4096], reply_markup)


async def answer_ephemeral(query, doc: Doc, reply_markup=None, replace: bool = False):
    """Reply to a callback query with a private ephemeral rich card (WzGram Layer 232 / richgram)."""
    client = getattr(query, "_client", None) or getattr(getattr(query, "message", None), "_client", None)
    msg = getattr(query, "message", None)
    user = getattr(query, "from_user", None)
    if not (msg and user):
        return await query.answer(doc.plain()[:190], show_alert=True)
    if _wzgram_rich_enabled(client):
        from core.ui import wzgram_markup
        try:
            await rg.rich_answer(
                query,
                doc.rich(),
                reply_markup=wzgram_markup(reply_markup),
                client=client,
            )
            return True
        except Exception as e:
            log.debug(f"rich_answer failed ({e}), falling back")
    from core.ui import group_reply
    try:
        await query.answer()
    except Exception:
        pass
    if replace:
        return await edit(msg, doc, reply_markup=reply_markup)
    return await group_reply(msg, doc.classic(), reply_markup=reply_markup)


def _ensure_doc(doc_or_html: "Doc | str") -> Doc:
    if isinstance(doc_or_html, Doc):
        return doc_or_html
    return Doc().text(str(doc_or_html or ""))


async def rich_send(client, chat_id: int, doc_or_html: "Doc | str", reply_markup=None, reply_to: int = None):
    """Centralized rich send helper accepting either a :class:`Doc` or prebuilt HTML string."""
    return await send(client, chat_id, _ensure_doc(doc_or_html), reply_markup=reply_markup, reply_to=reply_to)


async def rich_reply(message, doc_or_html: "Doc | str", reply_markup=None, quote: bool = False):
    """Centralized rich reply helper accepting either a :class:`Doc` or prebuilt HTML string."""
    return await reply(message, _ensure_doc(doc_or_html), reply_markup=reply_markup, quote=quote)


async def rich_edit(message, doc_or_html: "Doc | str", reply_markup=None):
    """Centralized rich edit helper accepting either a :class:`Doc` or prebuilt HTML string."""
    return await edit(message, _ensure_doc(doc_or_html), reply_markup=reply_markup)


async def ephemeral_edit(client, chat_id: int, user_id: int, message_id: int,
                         doc_or_html: "Doc | str", reply_markup=None) -> bool:
    """Edit an ephemeral message in-place with a Rich :class:`Doc` (Bot API 10.3 → WzGram → classic)."""
    doc = _ensure_doc(doc_or_html)
    from core import botapi
    if _is_main(client) and botapi.enabled() and markup_ok(reply_markup):
        if await botapi.edit_ephemeral_text(
            chat_id, user_id, message_id, rich_html=doc.rich(), reply_markup=reply_markup
        ):
            return True
    if EPHEMERAL_REPLIES and _wzgram_rich_enabled(client) and markup_ok(reply_markup):
        from core.ui import wzgram_markup
        try:
            await client.edit_ephemeral_message_text(
                chat_id,
                user_id,
                message_id,
                rich_text=InputRichMessage(html=doc.rich(), media=doc.media or None),
                reply_markup=wzgram_markup(reply_markup),
            )
            return True
        except Exception as e:
            if "not modified" in str(e).lower():
                return True
            log.debug(f"wzgram rich ephemeral_edit fallback ({e})")
    from core.ui import edit_ephemeral
    return await edit_ephemeral(client, chat_id, user_id, message_id, doc.classic(), reply_markup=reply_markup)


async def ephemeral_delete(client, chat_id: int, user_id: int, message_id: int) -> bool:
    """Delete an ephemeral message (Bot API 10.3 → WzGram Layer 232 → normal delete fallback)."""
    from core.ui import delete_ephemeral
    return await delete_ephemeral(client, chat_id, user_id, message_id)

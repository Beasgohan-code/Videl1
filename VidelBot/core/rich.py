"""
Rich screens – one layout, two renderings.

    doc = Doc("📊", "Bot statistics", "live server numbers")
    doc.table([("CPU", "12 %"), ("RAM", "40 %")], header=("Item", "Value"))
    doc.h("💾", "Disk").text("…").details("More", other_doc)
    await rich.reply(message, doc, reply_markup=kb)

• With the Bot API bridge (aiogram, Bot API 10.1+) the screen goes out as a **native rich
  message** (sendRichMessage, HTML mode): real headings, bordered / striped tables,
  collapsible details, lists and a footer.
• Otherwise (or if Telegram refuses) the same Doc renders to classic HTML in the
  FileStore style of core/style.py (━ bars, 𝗕𝗢𝗟𝗗 heading, ꜱᴍᴀʟʟ ᴄᴀᴘꜱ labels, blockquotes).

pyrofork (MTProto layer 220) can't read rich messages, so a rich message only carries
buttons whose handlers edit through the Bot API (smart_edit / edit_with_preview /
rich.edit), send a new message, answer, or delete – see SAFE_CALLBACKS. A keyboard with
any other callback automatically uses the classic rendering instead.
In groups, rich screens are sent as ephemeral classic replies (only the caller sees them).
"""
from collections import OrderedDict
import html as _html
import logging
import re

from core import style

log = logging.getLogger("videl.rich")

# Callback data whose handlers are safe on a rich message (Bot API edits / new messages / answers).
SAFE_CALLBACKS = re.compile(
    r"^(close_btn|start_btn|help_btn|about_btn|settings_btn|v_settings|hub_save|buy_premium|back_menu|help_enc|"
    r"help_tools|help_admin|help_clone|clone_help|clone_about|help_saver|help_rename|cmd_list_btn|"
    r"premium_plans_btn|myplan_back_btn|settings_back_btn|dump_chat_btn|thumb_btn|caption_btn|user_stats_btn|"
    r"rn:[\w:]+|rnlb:\w+|status ref|refer_btn|noop(:.*)?|stars_buy:\d+|stars_sub|sub_toggle:(cancel|resume))$")


def esc(v) -> str:
    return _html.escape(str(v if v is not None else ""), quote=False)


def _cell(v) -> str:
    """Table cells / values: plain values are escaped, Raw() values pass through."""
    return v.html if isinstance(v, Raw) else esc(v)


class Raw:
    """Pre-built inline HTML (b / i / code / a) – used as-is in both renderings."""
    __slots__ = ("html",)

    def __init__(self, html: str):
        self.html = html

    def __str__(self):
        return self.html


def code(v) -> Raw:
    return Raw(f"<code>{esc(v)}</code>")


def bold(v) -> Raw:
    return Raw(f"<b>{esc(v)}</b>")


def link(text, url) -> Raw:
    return Raw(f'<a href="{esc(url)}">{esc(text)}</a>')


class Doc:
    def __init__(self, emoji: str = "", title: str = "", sub: str = ""):
        self.emoji, self.title, self.sub = emoji, title, sub
        self.blocks = []

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

    def divider(self):
        self.blocks.append(("hr",))
        return self

    def footer(self, text: str):
        self.blocks.append(("footer", text))
        return self

    # ── native rich HTML (Bot API sendRichMessage, html mode) ──
    def rich(self) -> str:
        out = []
        if self.title:
            out.append(f"<h1>{esc((self.emoji + ' ' if self.emoji else '') + self.title)}</h1>")
        if self.sub:
            out.append(f"<p><i>{style.sc(esc(self.sub))}</i></p>")
        out += self._rich_blocks()
        return "".join(out)

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
                # (Bot API 10.3's is_compact has no documented HTML attribute → `compact` is only a hint
                #  for the classic rendering; the rich table always uses bordered + striped)
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
            elif kind == "hr":
                out.append("<hr/>")
            elif kind == "footer":
                out.append(f"<footer>{esc(b[1])}</footer>")
        return out

    # ── classic HTML (FileStore style) ──
    def classic(self) -> str:
        parts = []
        if self.title:
            parts.append(style.hdr(self.emoji, self.title))
        if self.sub:
            parts.append(f"<i>{style.sc(esc(self.sub))}</i>")
        parts += self._classic_blocks()
        return "\n\n".join(p for p in parts if p)

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
            elif kind == "hr":
                out.append(style.BAR)
            elif kind == "footer":
                out.append(f"<i>{esc(b[1])}</i>")
        return out


# ─────────────────────────── sending ───────────────────────────
def markup_ok(markup) -> bool:
    """A rich message may only carry URL / copy buttons or callbacks from SAFE_CALLBACKS."""
    for row in getattr(markup, "inline_keyboard", None) or []:
        for b in row:
            data = getattr(b, "callback_data", None)
            if data is not None and not SAFE_CALLBACKS.match(str(data)):
                return False
    return True


def _is_main(client) -> bool:
    from core import botapi
    return client is None or botapi.is_main(client)


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


async def send(client, chat_id: int, doc: Doc, reply_markup=None, reply_to: int = None):
    """Rich message when possible, classic FileStore-style HTML otherwise. Returns the message id."""
    from core import botapi
    if _is_main(client) and botapi.enabled() and markup_ok(reply_markup):
        mid = await botapi.send_rich_html(chat_id, doc.rich(), reply_markup, reply_to=reply_to)
        if mid:
            mark(chat_id, mid)
            return mid
    from core.ui import HTML
    m = await client.send_message(chat_id, doc.classic()[:4096], reply_markup=reply_markup, parse_mode=HTML,
                                  disable_web_page_preview=True, reply_to_message_id=reply_to)
    return getattr(m, "id", None)


async def reply(message, doc: Doc, reply_markup=None, quote: bool = False):
    """Answer a command with a rich screen (groups: ephemeral classic reply for the caller)."""
    from pyrogram.enums import ChatType
    chat = getattr(message, "chat", None)
    if chat is not None and getattr(chat, "type", None) in (ChatType.GROUP, ChatType.SUPERGROUP):
        from core.ui import group_reply
        await group_reply(message, doc.classic()[:4096], reply_markup)
        return None
    client = getattr(message, "_client", None)
    from core import botapi
    if _is_main(client) and botapi.enabled() and markup_ok(reply_markup):
        mid = await botapi.send_rich_html(message.chat.id, doc.rich(), reply_markup,
                                          reply_to=message.id if quote else None)
        if mid:
            mark(message.chat.id, mid)
            return mid
    from core.ui import HTML
    m = await message.reply_text(doc.classic()[:4096], reply_markup=reply_markup, parse_mode=HTML,
                                 disable_web_page_preview=True, quote=quote)
    return getattr(m, "id", None)


async def edit(message, doc: Doc, reply_markup=None):
    """Edit a bot message into a rich screen (works on text and rich messages), else smart_edit."""
    from core import botapi
    client = getattr(message, "_client", None)
    if _is_main(client) and botapi.enabled() and markup_ok(reply_markup):
        if getattr(message, "media", None) and getattr(message, "text", None) is None:
            # a real photo / video screen can't become a text message → send the rich screen, drop the old one
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
    from core.ui import smart_edit
    return await smart_edit(message, doc.classic()[:4096], reply_markup)

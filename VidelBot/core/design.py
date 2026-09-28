"""
Videl design system – the /guide look for every HTML menu.

The rich /guide (Bot API rich message) is built from headings, bold runs, collapsible
details, tables, a button row and a footer. Interactive menus must stay classic HTML
messages (they are edited in place on every tap and their callbacks arrive over MTProto),
so these helpers reproduce the same structure with Bot API HTML:

    heading   → 𝗦𝗮𝗻𝘀-𝗯𝗼𝗹𝗱 title (+ italic subtitle)          (≈ InputRichBlockSectionHeading)
    card      → <blockquote> block                           (≈ InputRichBlockBlockQuotation)
    details   → heading + <blockquote expandable>            (≈ InputRichBlockDetails)
    table     → aligned “label · value” rows inside a card   (≈ InputRichBlockTable)
    footer    → small italic line                            (≈ InputRichBlockFooter)

Sans-bold is only used for headings and short accents: it is decorative Unicode, so
labels that people search for, commands and numbers users copy stay plain text.
"""
import html as _html

_SANS = {**{chr(65 + i): chr(0x1D5D4 + i) for i in range(26)},
         **{chr(97 + i): chr(0x1D5EE + i) for i in range(26)},
         **{chr(48 + i): chr(0x1D7EC + i) for i in range(10)}}
_SANS_BACK = {v: k for k, v in _SANS.items()}

BULLET = "▸"
DOT = " · "


def sans(text: str) -> str:
    """𝗦𝗮𝗻𝘀-𝘀𝗲𝗿𝗶𝗳 𝗯𝗼𝗹𝗱 (letters and digits only; emoji, punctuation and HTML entities untouched)."""
    out, i = [], 0
    while i < len(text):
        if text[i] == "&":                       # keep entities such as &amp; intact
            j = text.find(";", i)
            if 0 < j - i <= 8:
                out.append(text[i:j + 1])
                i = j + 1
                continue
        out.append(_SANS.get(text[i], text[i]))
        i += 1
    return "".join(out)


def plain(text: str) -> str:
    """Undo sans() – for logs, search and tests."""
    return "".join(_SANS_BACK.get(c, c) for c in text)


def heading(emoji: str, title: str, sub: str = "") -> str:
    head = f"<b>{emoji} {sans(title)}</b>" if emoji else f"<b>{sans(title)}</b>"
    return head + (f"\n<i>{sub}</i>" if sub else "")


def card(*lines, expandable: bool = False) -> str:
    body = "\n".join(str(x) for x in lines if x not in (None, "", False))
    if not body:
        return ""
    return f"<blockquote expandable>{body}</blockquote>" if expandable else f"<blockquote>{body}</blockquote>"


def section(emoji: str, title: str, *lines, expandable: bool = False, sub: str = "") -> str:
    body = card(*lines, expandable=expandable)
    return f"{heading(emoji, title, sub)}\n{body}" if body else ""


def details(emoji: str, title: str, *lines) -> str:
    """Collapsible section – the HTML twin of the guide's ▸ details blocks."""
    return section(emoji, title, *lines, expandable=True)


def bullets(*items) -> str:
    return "\n".join(f"{BULLET} {i}" for i in items if i)


def kv(label: str, value, emoji: str = "") -> str:
    return f"{emoji + ' ' if emoji else ''}<b>{label}</b>{DOT}{value}"


def table(rows, header: tuple = None) -> str:
    """Rows of (label, value) → one line each; a header row is shown in sans-bold."""
    lines = []
    if header:
        lines.append(f"<b>{sans(header[0])}</b>{DOT}<b>{sans(header[1])}</b>")
    lines += [f"{BULLET} {a}{DOT}<b>{b}</b>" for a, b in rows]
    return "\n".join(lines)


def cmd(command: str, desc: str = "") -> str:
    return f"<code>/{command}</code>" + (f" — {desc}" if desc else "")


def bar(done: int, total: int, width: int = 10) -> str:
    """▰▰▰▱▱▱ progress bar."""
    if total <= 0:
        return "▱" * width
    filled = max(0, min(width, round(width * done / total)))
    return "▰" * filled + "▱" * (width - filled)


def footer(text: str) -> str:
    return f"<i>{text}</i>"


def esc(text) -> str:
    return _html.escape(str(text or ""), quote=False)


def page(*parts) -> str:
    """Join sections with one blank line (empty parts dropped)."""
    return "\n\n".join(p for p in parts if p)

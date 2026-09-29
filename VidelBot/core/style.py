"""
FileStore-style text helpers (the look of the clone bot screens), used across the
user-facing menus of the main bot:

    hdr("✏️", "Auto-Rename")   →  ━━━━ bar · ✏️ 𝗔𝗨𝗧𝗢-𝗥𝗘𝗡𝗔𝗠𝗘 · ━━━━ bar   (bold)
    sc("Your plan")           →  ʏᴏᴜʀ ᴘʟᴀɴ      (small caps; HTML tags, entities, {placeholders},
                                                /commands, @mentions, links, ACRONYMS and <code> are kept)
    row("Status", "🟢 Active") →  ◈ <b>ꜱᴛᴀᴛᴜꜱ:</b> 🟢 Active   (label small caps, value normal)
    sec("🧩", "Modules")       →  <b>🧩 ᴍᴏᴅᴜʟᴇꜱ</b>

Values (file names, numbers, templates …) always stay in normal letters so they
remain readable and copyable – only headings and labels are stylised.
"""
import re

_SMALL = str.maketrans("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
                       "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡxʏᴢ")

# Segments that must never be converted.
_KEEP = re.compile(
    r"(<code>.*?</code>|<pre[^>]*>.*?</pre>|</?[a-zA-Z][^<>]*>|&[a-zA-Z]+;|&#\d+;|&#x[0-9a-fA-F]+;"
    r"|\{[^{}]*\}|(?<![\w/])/[A-Za-z_]\w*|@\w+|https?://\S+|t\.me/\S+"
    r"|(?<![A-Za-z])[A-Z][A-Z0-9]+\+?(?![A-Za-z]))",   # acronyms (CPU, RAM, 4GB+, MKV, ID …) stay readable
    re.S)

_TAGS = re.compile(r"(</?[a-zA-Z][^<>]*>|&[a-zA-Z]+;|&#\d+;|&#x[0-9a-fA-F]+;|\{[^{}]*\})")

BAR = "━" * 21


def _escape_plain(p: str) -> str:
    """Bare & < > in plain text → entities (existing tags / entities are _KEEP segments)."""
    return p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def sc(text: str) -> str:
    """Small caps for plain words; markup and technical tokens are left untouched.
    Bare &, < and > in the plain parts are escaped, so the result is always valid HTML."""
    parts = _KEEP.split(str(text or ""))
    return "".join(p if i % 2 else _escape_plain(p.translate(_SMALL)) for i, p in enumerate(parts))


def sans_bold(text: str) -> str:
    """𝗦𝗮𝗻𝘀-𝘀𝗲𝗿𝗶𝗳 𝗯𝗼𝗹𝗱 letters/digits (headings only – never for values)."""
    parts = _TAGS.split(str(text or ""))
    if len(parts) > 1:
        return "".join(p if i % 2 else sans_bold(p) for i, p in enumerate(parts))
    out = []
    for ch in parts[0]:
        o = ord(ch)
        if 65 <= o <= 90:
            out.append(chr(0x1D5D4 + o - 65))
        elif 97 <= o <= 122:
            out.append(chr(0x1D5EE + o - 97))
        elif 48 <= o <= 57:
            out.append(chr(0x1D7EC + o - 48))
        elif ch in "&<>":
            out.append(_escape_plain(ch))
        else:
            out.append(ch)
    return "".join(out)


def hdr(emoji: str, title: str, sub: str = "") -> str:
    """FileStore heading block: bars + emoji + 𝗕𝗢𝗟𝗗 title (+ optional small-caps subtitle)."""
    head = f"<b>{BAR}\n{emoji} {sans_bold(_upper(title))}\n{BAR}</b>"
    return head + (f"\n<i>{sc(sub)}</i>" if sub else "")


def _upper(t: str) -> str:
    """Upper-case everything except entities / tags / {placeholders}."""
    parts = _TAGS.split(str(t or ""))
    return "".join(p if i % 2 else p.upper() for i, p in enumerate(parts))


def sec(emoji: str, title: str) -> str:
    return f"<b>{emoji} {sc(title)}</b>" if emoji else f"<b>{sc(title)}</b>"


def row(label: str, value, mark: str = "◈") -> str:
    return f"{mark} <b>{sc(label)}:</b> {value}"


def rows(pairs, mark: str = "◈") -> str:
    return "\n".join(row(k, v, mark) for k, v in pairs)


def quote(body: str, expandable: bool = False) -> str:
    return f"<blockquote{' expandable' if expandable else ''}>{body}</blockquote>"


def hint(text: str) -> str:
    return f"<i>{sc(text)}</i>"

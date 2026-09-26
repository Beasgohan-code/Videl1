"""
Videl Rich UI helpers
Use these for cleaner headings, tables and panels across modules.
Works with Markdown / HTML parse modes used by the bot.
"""

from __future__ import annotations
from typing import Sequence


def h1(text: str) -> str:
    return f"**{text}**"


def h2(text: str) -> str:
    return f"**✦ {text}**"


def h3(text: str) -> str:
    return f"**• {text}**"


def divider() -> str:
    return "──────────────"


def panel(title: str, body: str, footer: str = "") -> str:
    parts = [h2(title), divider(), body.strip()]
    if footer:
        parts += [divider(), f"_{footer}_"]
    return "\n".join(parts)


def table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """Simple Markdown table (universally supported)."""
    header = " | ".join(str(h) for h in headers)
    sep = " | ".join("---" for _ in headers)
    body = "\n".join(" | ".join(str(c) for c in row) for row in rows)
    return f"{header}\n{sep}\n{body}"


def bullet(items: Sequence[str]) -> str:
    return "\n".join(f"• {i}" for i in items)


def code(text: str) -> str:
    return f"`{text}`"

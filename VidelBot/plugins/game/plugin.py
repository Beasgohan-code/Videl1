"""A chat game plus an explicitly opted-in, outgoing-only beta widget.

The fallback carries its board in Telegram's callback buttons; it is not a
leaderboard or multiplayer state. Widget code is local to the viewer and does
not send widget queries, so no second incoming Telegram receiver is needed.
"""
from __future__ import annotations

import logging
import os
import re
import zlib
from pathlib import Path

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from core import botapi
from core.plugin_manager import PluginBase, plugin_enabled, plugin_filter
from plugins.game import chat_games

log = logging.getLogger("videl.plugins.game")
WIDGET = Path(__file__).with_name("arcade.html")
SHELL = Path(__file__).with_name("shell.html")
GAMES = (
    ("classic", "Arcade classics: 3 games"),
    ("slide", "Sliding tiles"), ("mines", "Minefield"),
    ("sudoku", "Sudoku 4×4"), ("maze", "Maze"), ("merge", "Merge 128"),
    ("connect", "Connect Four"), ("nim", "Take the last"),
    ("rps", "Rock paper scissors"), ("hangman", "Word rescue"),
    ("reversi", "Mini reversi"), ("snake", "Snake"),
    ("whack", "Target tap"), ("reaction", "Reaction test"),
    ("numbers", "Number rush"), ("simon", "Pattern memory"),
    ("math", "Math sprint"), ("scramble", "Word scramble"),
    ("trivia", "Quick trivia"), ("higher", "Higher or lower"),
    ("code", "Code breaker"),
)
GAME_KEYS = frozenset(key for key, _ in GAMES)
# Original compact vector marks; no network assets or third-party image licences.
ART = {
    "puzzle": '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="8" height="8" rx="2"/><rect x="13" y="3" width="8" height="8" rx="2"/><rect x="3" y="13" width="8" height="8" rx="2"/><path d="M16 13v8m-4-4h8"/></svg>',
    "classic": '<svg viewBox="0 0 24 24"><path d="M7 9h10c2 0 3 2 4 6s-1 5-3 3l-3-2H9l-3 2c-2 2-4 1-3-3s2-6 4-6Z"/><path d="M7 12v4m-2-2h4m7-1h.1m2 2h.1"/></svg>',
    "action": '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/></svg>',
    "brain": '<svg viewBox="0 0 24 24"><path d="M9 18h6m-5 3h4M8 15c-2-1-3-3-3-5a7 7 0 0 1 14 0c0 2-1 4-3 5l-1 2H9z"/><path d="m10 10 2-2 2 2-2 2-2-2Z"/></svg>',
}
STYLES = {
    "slide": ("puzzle", "#9fd8ff"), "mines": ("puzzle", "#ffca97"),
    "sudoku": ("puzzle", "#cabaff"), "maze": ("puzzle", "#bceba8"),
    "merge": ("puzzle", "#ffe09c"), "connect": ("classic", "#ffb9aa"),
    "nim": ("classic", "#b8e8ba"), "rps": ("classic", "#f4b8dc"),
    "hangman": ("classic", "#ffdc9f"), "reversi": ("classic", "#9fe3db"),
    "snake": ("action", "#a9ebac"), "whack": ("action", "#ffc0a3"),
    "reaction": ("action", "#ffe398"), "numbers": ("action", "#a7d9ff"),
    "simon": ("action", "#d3baff"), "math": ("brain", "#b2e7d0"),
    "scramble": ("brain", "#f6c2a9"), "trivia": ("brain", "#c8bcff"),
    "higher": ("brain", "#b2dbff"), "code": ("brain", "#ffe097"),
}
PAGE_SIZE = 6
MENU_CALLBACK = re.compile(r"\Avgg:([1-9][0-9]{0,19}):(page|play|chat|cat):([a-z0-9]+)\Z")
CATEGORIES = (("all", "All"), ("puzzle", "🧩 Puzzle"), ("classic", "🎲 Classic"),
              ("action", "⚡ Action"), ("brain", "🧠 Brain"))
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6),
         (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
CALLBACK = re.compile(r"\Avga:([1-9][0-9]{0,19}):([XO-]{9}):([0-8r])\Z")


class Plugin(PluginBase):
    pass


def winner(board: str) -> str:
    for a, b, c in LINES:
        if board[a] != "-" and board[a] == board[b] == board[c]:
            return board[a]
    return ""


def move(board: str, cell: int) -> str:
    """Place X and let O win, block, take the centre, or take a free cell."""
    if winner(board) or board[cell] != "-":
        return board
    b = list(board)
    b[cell] = "X"
    if winner("".join(b)) or "-" not in b:
        return "".join(b)
    pick = None
    for sign in ("O", "X"):
        for i, value in enumerate(b):
            if value == "-":
                b[i] = sign
                if winner("".join(b)) == sign:
                    pick = i
                b[i] = "-"
                if pick is not None:
                    break
        if pick is not None:
            break
    if pick is None:
        pick = 4 if b[4] == "-" else b.index("-")
    b[pick] = "O"
    return "".join(b)


def screen(uid: int, board: str):
    result = winner(board)
    status = ("You win!" if result == "X" else "Bot wins." if result == "O"
              else "Draw." if "-" not in board else "You are X. Get three in a row.")
    text = ("🎮 <b>Videl Arcade · Tic-tac-toe</b>\n" + status +
            "\n\nThis board is for the person who started it. Use /game for your own.")
    rows = []
    for start in (0, 3, 6):
        rows.append([InlineKeyboardButton(board[i] if board[i] != "-" else "·",
                     callback_data=f"vga:{uid}:{board}:{i}") for i in range(start, start + 3)])
    rows.append([InlineKeyboardButton("New game", callback_data=f"vga:{uid}:---------:r")])
    rows.append([InlineKeyboardButton("🕹 More chat games", callback_data=f"vgc:{uid}:menu"),
                 InlineKeyboardButton("🎮 Widget games", callback_data=f"vgg:{uid}:page:0")])
    return text, InlineKeyboardMarkup(rows)


def game_menu(uid: int, page: int, category: str = "all"):
    labels = dict(CATEGORIES)
    if category not in labels:
        raise ValueError("Unknown category")
    entries = [(key, label) for key, label in GAMES
               if category == "all" or ("classic" if key == "classic" else STYLES[key][0]) == category]
    last = (len(entries) - 1) // PAGE_SIZE
    page = max(0, min(page, last))
    text = ("🎮 <b>Videl Arcade · Widget games</b>\n"
            "23 single-player games: puzzles, classics, action and brain games. "
            "Widgets require Telegram's beta support; "
            "progress stays on this device and may reset.\n"
            + ("Widget beta is off here; the chat games are still available.\n"
               if not _widget_enabled() else
               "Widget sending is unavailable; the chat games are still available.\n"
               if not botapi.enabled() else
               "Widget beta is enabled; a supported Telegram client and endpoint are required.\n")
            + f"{labels[category]} · Page {page + 1}/{last + 1}")
    tabs = [InlineKeyboardButton(("• " if cat == category else "") + label,
                                 callback_data=f"vgg:{uid}:cat:{cat}0") for cat, label in CATEGORIES]
    rows = [tabs[:3], tabs[3:]]
    items = entries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows += [[InlineKeyboardButton(label, callback_data=f"vgg:{uid}:play:{key}")]
             for key, label in items]
    def page_data(n):
        return f"vgg:{uid}:page:{n}" if category == "all" else f"vgg:{uid}:cat:{category}{n}"
    nav = []
    if page:
        nav.append(InlineKeyboardButton("‹ Previous", callback_data=page_data(page - 1)))
    if page < last:
        nav.append(InlineKeyboardButton("Next ›", callback_data=page_data(page + 1)))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("🕹 Play chat games", callback_data=f"vgc:{uid}:menu")])
    return text, InlineKeyboardMarkup(rows)


def _replace_once(source: str, marker: str, content: str) -> str:
    if source.count(marker) != 1:
        raise ValueError("Invalid game template")
    return source.replace(marker, content, 1)


def widget_html(key: str) -> str:
    """Assemble trusted local HTML, CSS and JS for Telegram's no-network sandbox."""
    if key not in GAME_KEYS:
        raise ValueError("Unknown game")
    root = WIDGET.parent
    if key == "classic":
        template = WIDGET.read_text(encoding="utf-8")
        css = (root / "classic.css").read_text(encoding="utf-8")
        script = (root / "classic.js").read_text(encoding="utf-8")
        if "</style" in css.lower() or "</script" in script.lower():
            raise ValueError("Invalid widget source")
        return _replace_once(_replace_once(template, "/* CLASSIC_STYLES */", css),
                             "/* CLASSIC_ENGINE */", script)
    shell = SHELL.read_text(encoding="utf-8")
    css = (root / "arcade.css").read_text(encoding="utf-8")
    engine = (root / "engine.js").read_text(encoding="utf-8")
    code = (root / "games" / f"{key}.js").read_text(encoding="utf-8")
    if "</style" in css.lower() or "</script" in (engine + code).lower():
        raise ValueError("Invalid widget source")
    genre, accent = STYLES[key]
    css = _replace_once(css, "/* PALETTE */", f"--accent:{accent}")
    engine = _replace_once(engine, "/* GAME_CODE */", code)
    return (_replace_once(_replace_once(_replace_once(_replace_once(shell,
             "/* ARCADE_STYLES */", css), "/* ARCADE_ENGINE */", engine),
             "<!-- ART -->", ART[genre]), "<!-- GENRE -->", genre.upper()))


def _widget_enabled() -> bool:
    return os.environ.get("GAME_WIDGETS_BETA", "").strip().lower() in {"1", "true", "yes", "on"}


async def send_widget(client, chat_id: int, key: str = "classic", thread_id: int | None = None) -> bool:
    """Best effort beta send using only the existing primary bot's aiogram session.

    Never use a primary token for a different client. Errors are logged by type
    only: HTTP exceptions can include the secret-bearing Bot API URL. No retry is
    attempted after an ambiguous response. The ordinary game is already sent.
    """
    if not _widget_enabled() or not botapi.enabled():
        return False
    try:
        outgoing = botapi.bot()
        if outgoing is None or not getattr(client, "me", None) or client.me.id != outgoing.id:
            return False
        html = widget_html(key)
        raw = html.encode("utf-8")
        if len(raw) > 32768 or len(zlib.compress(raw, 9)) > 4096:
            log.warning("Game widget exceeds the Telegram beta size limit")
            return False
        # aiogram 10.3 doesn't include sendWidget; TelegramMethod uses the
        # configured session and Bot API endpoint without an extra receiver.
        from aiogram.methods.base import TelegramMethod
        from aiogram.types import Message as ApiMessage

        class SendWidget(TelegramMethod[ApiMessage]):
            __returning__ = ApiMessage
            __api_method__ = "sendWidget"
            chat_id: int
            html: str
            message_thread_id: int | None = None
            width: int | None = None
            height: int | None = None
            caption: str = "Videl Arcade: single-player games. On older clients, play using the buttons above."

        await outgoing(SendWidget(chat_id=chat_id, html=html, message_thread_id=thread_id))
        return True
    except Exception as exc:
        log.warning("Beta game widget unavailable (%s); chat game remains available", type(exc).__name__)
        return False


@Client.on_message(filters.command("game") & (filters.private | filters.group) & plugin_filter("game"))
async def game_command(client, message):
    user = message.from_user
    if not user:
        await message.reply_text("Start /game as a user to play.")
        return
    text, markup = screen(user.id, "---------")
    await message.reply_text(text, reply_markup=markup)


@Client.on_callback_query(filters.regex(r"^vga:"))
async def game_callback(client, query):
    if not plugin_enabled("game"):
        await query.answer("Games are disabled.", show_alert=True)
        return
    match = CALLBACK.fullmatch(query.data or "")
    if not match or not query.from_user or not query.message:
        await query.answer("Invalid game. Start again with /game.", show_alert=True)
        return
    owner, board, action = match.groups()
    if query.from_user.id != int(owner):
        await query.answer("This is someone else's game. Use /game for yours.", show_alert=True)
        return
    if board.count("X") not in (board.count("O"), board.count("O") + 1):
        await query.answer("Invalid game. Start again with /game.", show_alert=True)
        return
    if action == "r":
        board = "---------"
    elif winner(board) or board[int(action)] != "-":
        await query.answer("Choose an empty tile or start a new game.")
        return
    else:
        board = move(board, int(action))
    await query.answer()
    text, markup = screen(int(owner), board)
    await query.edit_message_text(text, reply_markup=markup)


@Client.on_callback_query(filters.regex(r"^vgg:"))
async def game_menu_callback(client, query):
    if not plugin_enabled("game"):
        await query.answer("Games are disabled.", show_alert=True)
        return
    match = MENU_CALLBACK.fullmatch(query.data or "")
    if not match or not query.from_user or not query.message:
        await query.answer("Invalid menu. Start again with /game.", show_alert=True)
        return
    owner, action, value = match.groups()
    if query.from_user.id != int(owner):
        await query.answer("This is someone else's game. Use /game for yours.", show_alert=True)
        return
    uid = int(owner)
    if action == "chat" and value == "0":
        await query.answer()
        text, markup = screen(uid, "---------")
        await query.edit_message_text(text, reply_markup=markup)
    elif action == "page" and value.isdecimal() and len(value) < 4:
        await query.answer()
        text, markup = game_menu(uid, int(value))
        await query.edit_message_text(text, reply_markup=markup)
    elif action == "cat":
        category = re.fullmatch(r"(all|puzzle|classic|action|brain)([0-9]{1,2})", value)
        if not category:
            await query.answer("Invalid category. Start again with /game.", show_alert=True)
            return
        await query.answer()
        text, markup = game_menu(uid, int(category.group(2)), category.group(1))
        await query.edit_message_text(text, reply_markup=markup)
    elif action == "play" and value in GAME_KEYS:
        if not _widget_enabled() or not botapi.enabled():
            await query.answer("Widget sending is unavailable here. The chat games still work.", show_alert=True)
            return
        await query.answer("Opening game…")
        from core.ui import thread_id_of
        sent = await send_widget(client, query.message.chat.id, value, thread_id_of(query.message))
        if not sent:
            await query.message.reply_text("Widget games are unavailable right now. The chat game still works; try /game.")
    else:
        await query.answer("Invalid game. Start again with /game.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^vgc:"))
async def chat_game_callback(client, query):
    if not plugin_enabled("game"):
        await query.answer("Games are disabled.", show_alert=True)
        return
    if not query.from_user or not query.message:
        await query.answer("Invalid game. Start again with /game.", show_alert=True)
        return
    try:
        text, markup = chat_games.act(query.data or "", query.from_user.id)
    except chat_games.GameError as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer()
    await query.edit_message_text(text, reply_markup=markup)

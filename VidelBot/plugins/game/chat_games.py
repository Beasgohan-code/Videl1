"""Stateless, owner-scoped casual games for standard Telegram buttons.

Boards live in callback data, so they survive restarts without a database. They
are not authoritative scores: stale or simultaneous callbacks can race.
"""
from __future__ import annotations

import re
import secrets

from pyrogram.types import InlineKeyboardButton as Button, InlineKeyboardMarkup

_OWNER = r"([1-9][0-9]{0,19})"
MENU = re.compile(rf"\Avgc:{_OWNER}:menu\Z")
NIM = re.compile(rf"\Avgc:{_OWNER}:nim:([0-9]{{1,2}}):([123r])\Z")
RPS = re.compile(rf"\Avgc:{_OWNER}:rps:([0-3]):([0-3]):([rpsn])\Z")
HIGHER = re.compile(rf"\Avgc:{_OWNER}:high:([0-8]):([0-8]):([0-9]{{1,2}}):([hln])\Z")


class GameError(ValueError):
    pass


def menu(uid: int):
    text = ("🎮 <b>Videl Arcade · Chat games</b>\n"
            "Choose a single-player game. These work without widget beta support. "
            "In groups, only the person who opened the board can play it.")
    rows = [
        [Button("✖️ Tic-tac-toe", callback_data=f"vgg:{uid}:chat:0")],
        [Button("🪨 Take the last", callback_data=f"vgc:{uid}:nim:17:r")],
        [Button("✋ Rock paper scissors", callback_data=f"vgc:{uid}:rps:0:0:n")],
        [Button("🔢 Higher or lower", callback_data=f"vgc:{uid}:high:0:0:1:n")],
        [Button("🎮 Browse widget games", callback_data=f"vgg:{uid}:page:0")],
    ]
    return text, InlineKeyboardMarkup(rows)


def nim_screen(uid: int, left: int, status: str = ""):
    text = ("🪨 <b>Take the last</b>\nTake 1–3 stones; whoever takes the last wins.\n"
            f"<b>Stones left: {left}</b>\n" + status)
    rows = []
    if left:
        rows.append([Button(f"Take {n}", callback_data=f"vgc:{uid}:nim:{left}:{n}")
                     for n in (1, 2, 3) if n <= left])
    rows.append([Button("↻ New game", callback_data=f"vgc:{uid}:nim:17:r"),
                 Button("‹ Chat games", callback_data=f"vgc:{uid}:menu")])
    return text, InlineKeyboardMarkup(rows)


def rps_screen(uid: int, wins: int, losses: int, status: str = ""):
    text = ("✋ <b>Rock paper scissors</b>\nFirst to 3 wins.\n"
            f"<b>You {wins} · Bot {losses}</b>\n" + status)
    rows = []
    if wins < 3 and losses < 3:
        rows.append([Button(label, callback_data=f"vgc:{uid}:rps:{wins}:{losses}:{key}")
                     for key, label in (("r", "🪨 Rock"), ("p", "📄 Paper"), ("s", "✂️ Scissors"))])
    rows.append([Button("↻ New game", callback_data=f"vgc:{uid}:rps:0:0:n"),
                 Button("‹ Chat games", callback_data=f"vgc:{uid}:menu")])
    return text, InlineKeyboardMarkup(rows)


def higher_screen(uid: int, round_no: int, score: int, current: int, status: str = ""):
    text = ("🔢 <b>Higher or lower</b>\nGuess the next number from 1–12. "
            "Equal numbers earn no point. Reach 5 points in 8 rounds.\n"
            f"<b>Current: {current} · Round: {round_no}/8 · Score: {score}</b>\n" + status)
    rows = []
    if round_no < 8:
        rows.append([Button("⬆️ Higher", callback_data=f"vgc:{uid}:high:{round_no}:{score}:{current}:h"),
                     Button("⬇️ Lower", callback_data=f"vgc:{uid}:high:{round_no}:{score}:{current}:l")])
    rows.append([Button("↻ New game", callback_data=f"vgc:{uid}:high:0:0:1:n"),
                 Button("‹ Chat games", callback_data=f"vgc:{uid}:menu")])
    return text, InlineKeyboardMarkup(rows)


def act(data: str, uid: int):
    """Return the next screen, rejecting malformed or other users' callbacks."""
    match = next((m for regex in (MENU, NIM, RPS, HIGHER) if (m := regex.fullmatch(data))), None)
    if not match:
        raise GameError("Invalid game. Start again with /game.")
    if int(match.group(1)) != uid:
        raise GameError("This is someone else's game. Use /game for yours.")
    if match.re is MENU:
        return menu(uid)
    if match.re is NIM:
        left, action = int(match.group(2)), match.group(3)
        if left > 17:
            raise GameError("Invalid game. Start again with /game.")
        if action == "r":
            return nim_screen(uid, 17)
        take = int(action)
        if not left or take > left:
            raise GameError("Choose an available move or start a new game.")
        left -= take
        if not left:
            return nim_screen(uid, 0, "🎉 You took the last stone. You win!")
        bot = min(left, left % 4 or 1)
        left -= bot
        status = f"Bot took {bot}. " + ("Bot wins." if not left else "Your turn.")
        return nim_screen(uid, left, status)
    if match.re is RPS:
        wins, losses, action = int(match.group(2)), int(match.group(3)), match.group(4)
        if wins == losses == 3:
            raise GameError("Invalid game. Start again with /game.")
        if action == "n":
            return rps_screen(uid, 0, 0)
        if wins == 3 or losses == 3:
            raise GameError("Game over. Start a new game.")
        names = ("Rock", "Paper", "Scissors")
        player, bot = "rps".index(action), secrets.randbelow(3)
        if player == bot:
            status = f"Both chose {names[bot]}. Draw."
        elif (player - bot) % 3 == 1:
            wins += 1
            status = f"Bot chose {names[bot]}. " + ("🎉 You win!" if wins == 3 else "Round won.")
        else:
            losses += 1
            status = f"Bot chose {names[bot]}. " + ("Bot wins." if losses == 3 else "Round lost.")
        return rps_screen(uid, wins, losses, status)
    round_no, score, current, action = int(match.group(2)), int(match.group(3)), int(match.group(4)), match.group(5)
    if score > round_no or not 1 <= current <= 12:
        raise GameError("Invalid game. Start again with /game.")
    if action == "n":
        return higher_screen(uid, 0, 0, secrets.randbelow(12) + 1)
    if round_no >= 8:
        raise GameError("Game over. Start a new game.")
    new = secrets.randbelow(12) + 1
    correct = (action == "h" and new > current) or (action == "l" and new < current)
    score += int(correct)
    round_no += 1
    status = "Equal — no point." if new == current else "Correct!" if correct else "Not this time."
    if round_no == 8:
        status += " 🎉 You win!" if score >= 5 else " Game over — try again."
    return higher_screen(uid, round_no, score, new, status)

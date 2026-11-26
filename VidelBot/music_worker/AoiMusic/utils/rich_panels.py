
#
# Now-playing rich card with a live progress bar.
#
# The card is a TEXT rich message (Bot API 10.3): buttons live in the message
# HTML, so the classic photo card is gone. A background refresher re-renders
# the bar every ~18s straight from db[chat_id][0]["played"] (the 1s tick in
# seeker.timer), so pause/resume/seek stay honest.
#

import asyncio
import os
import re
import time

import config
from AoiMusic import LOGGER
from AoiMusic.misc import db
from AoiMusic.utils.database import autoplay_cache, is_autoplay
from AoiMusic.utils.inline.play import get_progress_bar
from AoiMusic.utils.premium_emoji import BLANK_BTN_TEXT, pe
from AoiMusic.utils.rich_msg import (
    RichMystic,
    edit_rich,
    get_anchor,
    pemoji,
    rich_note,
    rich_url_button,
    rich_button,
    rich_button_row,
    rich_esc,
    rich_heading,
    rich_kv,
    rich_img,
    send_rich,
)

_TICK = 18

_panel_content = {}
_closed_panels = set()
_menu_page: dict = {}


def mark_panel_closed(chat_id: int, message_id: int) -> None:
    """Stop the refresher when the panel is closed/replaced."""
    _closed_panels.add((chat_id, message_id))
    _panel_content.pop((chat_id, message_id), None)
    _menu_page.pop((chat_id, message_id), None)


def _bar_style(percentage: float) -> str:
    pct = int(percentage) if percentage else 0
    if pct <= 10:
        return "primary"
    if pct < 25:
        return "success"
    if pct < 40:
        return "danger"
    if pct < 55:
        return "primary"
    if pct < 70:
        return "success"
    if pct < 85:
        return "danger"
    return "primary"


def _ctl_style() -> str:
    # Keep the skip button style stable across periodic panel refreshes.
    return "primary"


def _xeno_bar(percentage: float) -> str:
    umm = int(percentage or 0)
    if umm <= 10:
        return "◉—————————"
    if 10 < umm < 20:
        return "—◉————————"
    if 20 <= umm < 30:
        return "——◉———————"
    if 30 <= umm < 40:
        return "———◉——————"
    if 40 <= umm < 50:
        return "————◉—————"
    if 50 <= umm < 60:
        return "—————◉————"
    if 60 <= umm < 70:
        return "——————◉———"
    if 70 <= umm < 80:
        return "———————◉——"
    if 80 <= umm < 95:
        return "————————◉—"
    return "—————————◉"


def _autoplay_button(chat_id: int) -> str:
    enabled = bool(autoplay_cache.get(chat_id, False))
    return rich_button(
        f"ᴀᴜᴛᴏ : {'ᴏɴ' if enabled else 'ᴏғғ'}",
        callback_data=f"AutoplayToggle|{chat_id}",
        style="success" if enabled else "",
        emoji="SPARKLES_AI" if enabled else "BAN_BLOCK",
    )


def _toggle_button(chat_id: int, playing: bool = True) -> str:
    if playing and db.get(chat_id):
        return rich_button("ᴘᴀᴜsᴇ", callback_data=f"ADMIN Pp|{chat_id}", style="primary", emoji="PAUSE")
    return rich_button("ʀᴇsᴜᴍᴇ", callback_data=f"ADMIN Pp|{chat_id}", style="success", emoji="PLAY")


def _toggle_icon_button(chat_id: int, playing: bool = True) -> str:
    """Single Pause/Resume icon-only toggle button for Row 2."""
    if playing and db.get(chat_id):
        return rich_button(
            BLANK_BTN_TEXT,
            callback_data=f"ADMIN Pp|{chat_id}",
            style="primary",
            emoji="PAUSE",
            icon_only=True,
        )
    return rich_button(
        BLANK_BTN_TEXT,
        callback_data=f"ADMIN Pp|{chat_id}",
        style="success",
        emoji="PLAY",
        icon_only=True,
    )


def _rows(chat_id: int, videoid, elapsed: float, total_sec: float, playing: bool = True) -> str:
    try:
        from AoiMusic.utils.formatters import seconds_to_min
        _sec = int(elapsed or 0)
        played_txt = seconds_to_min(_sec) if _sec > 0 else "00:00"
    except Exception:
        played_txt = "00:00"

    try:
        dur_txt = seconds_to_min(int(total_sec)) if total_sec else "--:--"
    except Exception:
        dur_txt = "--:--"

    pct = (int(elapsed or 0) / total_sec * 100) if total_sec else 0
    bar = _xeno_bar(pct)

    # Row 1: Live timer progress bar
    rows = rich_button_row(
        rich_button(
            f"{played_txt} {bar} {dur_txt}",
            callback_data="GetTimer",
            style="success",
            emoji="TIMER",
        )
    )
    # Row 2: 4-icon controls row (Pause/Resume toggle | Replay | Skip | Stop)
    rows += rich_button_row(
        _toggle_icon_button(chat_id, playing=playing),
        rich_button(BLANK_BTN_TEXT, callback_data=f"ADMIN Replay|{chat_id}", style="primary", emoji="REPEAT", icon_only=True),
        rich_button(BLANK_BTN_TEXT, callback_data=f"ADMIN Skip|{chat_id}", style="primary", emoji="NEXT_TRACK", icon_only=True),
        rich_button(BLANK_BTN_TEXT, callback_data=f"ADMIN Stop|{chat_id}", style="danger", emoji="STOP", icon_only=True),
    )
    # Row 3: Playlist + compact Autoplay toggle
    rows += rich_button_row(
        rich_button("ᴘʟᴀʏʟɪsᴛ", callback_data=f"add_playlist {videoid}", style="success", emoji="PLUS"),
        _autoplay_button(chat_id),
    )
    # Row 4: Compact Suggestions + Controls Menu
    rows += rich_button_row(
        rich_button("sᴜɢɢᴇsᴛ", callback_data=f"GetSuggestions|{chat_id}", style="", emoji="SPARKLES_AI"),
        rich_button("ᴍᴇɴᴜ", callback_data=f"PanelMarkup {videoid}|{chat_id}", style="primary", emoji="SLIDERS_EQ"),
    )
    # Row 5: Close
    rows += rich_button_row(
        rich_button("ᴄʟᴏsᴇ", callback_data="close", style="danger", emoji="CLOSE"),
    )
    return rows


_SOURCE_STYLE = {
    "Media API": "ᴍᴇᴅɪᴀ ᴀᴘɪ",
    "YouTube": "ʏᴏᴜᴛᴜʙᴇ",
    "Telegram": "ᴛᴇʟᴇɢʀᴀᴍ",
    "SoundCloud": "sᴏᴜɴᴅᴄʟᴏᴜᴅ",
    "SaaVn": "ᴊɪᴏsᴀᴀᴠɴ",
}


def _source_label(chat_id: int) -> str:
    """Which pipe is feeding the current track, for the card's "via" footer."""
    got = db.get(chat_id)
    if not got:
        return "Videl Music"
    entry = got[0]
    tag = str(entry.get("source") or "").strip()
    if tag:
        return _SOURCE_STYLE.get(tag, tag)
    arg = str(entry.get("vidid") or "l" + str(entry.get("file") or ""))
    if arg == "telegram" or "telegram" in arg:
        return "Telegram"
    if "saavn" in arg:
        return "JioSaavn"
    if "soundcloud" in arg:
        return "SoundCloud"
    if "index_" in arg:
        return "Direct Stream"
    if "live_" in arg:
        return "YouTube Live"
    if "vid_" in arg:
        return "YouTube"
    return "Videl Music"


def _next_html(chat_id: int) -> str:
    """One-line preview of the queued track after the current one.

    Empty queue renders a gentle nudge to add something instead of nothing.
    """
    try:
        got = db.get(chat_id)
        if not got or len(got) < 2:
            return f"<p>{pe('MUSIC_SINGLE')} <i>ᴜᴘ ɴᴇxᴛ : ǫᴜᴇᴜᴇ ɪs ᴇᴍᴘᴛʏ — ᴜsᴇ /play ᴏʀ ᴛᴜʀɴ ᴏɴ ᴀᴜᴛᴏᴘʟᴀʏ</i></p>"
        nxt = got[1]
        title = rich_esc(str(nxt.get("title") or "Unknown")[:30])
        dur = str(nxt.get("dur") or "--:--").replace(" ", "")
        # Older queue entries carry pyrogram tg:// mentions the rich renderer
        # strips to raw text; unwrap them so only the name survives.
        by_raw = str(nxt.get("by") or "ᴛʜᴇ ʜᴏꜱᴛ")[:64]
        by = rich_esc(re.sub(r'<a href="tg://user[^"]*">(.*?)</a>', r"\1", by_raw))
        arg = str(nxt.get("vidid") or "")
        if arg not in ("telegram", "soundcloud") and "index_" not in arg and "live_" not in arg and arg:
            link = f'<a href="https://youtu.be/{arg}">{title}</a>'
        else:
            link = title
        return (
            f"<p>{pe('NEXT_TRACK')} <b>ᴜᴘ ɴᴇxᴛ :</b> {link}<br>"
            f"{pemoji('⏱')} {dur}  ·  {pemoji('👤')} {by}</p>"
        )
    except Exception:
        return ""


def _body(
    _: dict | None,
    title: str,
    duration_min: str,
    user_name: str,
    thumb=None,
    videoid=None,
    src: str = "",
) -> str:
    body = ""
    thumb_url = str(thumb) if thumb else ""
    if thumb_url and (thumb_url.startswith(("http://", "https://")) or os.path.exists(thumb_url)):
        body += rich_img(thumb_url)
    title_txt = rich_esc(str(title)[:40])
    if (
        videoid
        and str(videoid) not in ("telegram", "soundcloud")
        and "index_" not in str(videoid)
        and "live_" not in str(videoid)
    ):
        title_txt = f'<a href="https://youtu.be/{videoid}">{title_txt}</a>'
    body += (
        "<blockquote>"
        f"{pe('EQUALIZER')} <b>sᴛᴀʀᴛᴇᴅ sᴛʀᴇᴀᴍɪɴɢ</b> {pe('DOUBLE_MUSIC_NOTES')}<br><br>"
        f"{pe('MUSIC_NOTE')} <b>ᴛɪᴛʟᴇ :</b> {title_txt}<br>"
        f"{pe('CLOCK_DURATION')} <b>ᴅᴜʀᴀᴛɪᴏɴ :</b> {rich_esc(duration_min)} ᴍɪɴᴜᴛᴇs<br>"
        f"{pe('USER')} <b>ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ :</b> {rich_esc(user_name)}<br>"
        f"{pe('BOLT_FAST')} <b>sᴏᴜʀᴄᴇ :</b> {rich_esc(src or 'ᴀᴏɪ ᴍᴜsɪᴄ')}"
        "</blockquote>"
    )
    return body


async def _send_card(chat_id: int, html: str) -> int | None:
    return await send_rich(chat_id, html, reply_to=get_anchor(chat_id))


def _register(chat_id: int, mid: int, videoid, total_sec: float):
    playing = db.get(chat_id)
    if playing:
        playing[0]["mystic"] = RichMystic(chat_id, mid)
        playing[0]["markup"] = "rich"
        playing[0]["rich_mid"] = mid


async def _maxres(videoid, thumb) -> str | None:
    """Return already-known thumb immediately without blocking on HTTP HEAD probes."""
    if thumb and str(thumb).startswith(("http://", "https://")):
        return thumb
    if not videoid:
        return thumb
    return f"https://i.ytimg.com/vi/{videoid}/hqdefault.jpg"


async def spawn_now_playing(
    _,
    chat_id: int,
    videoid,
    title: str,
    duration_min: str,
    user_name: str,
    thumb=None,
) -> int | None:
    """Send the now-playing card, register it on the db and start ticking."""
    try:
        await is_autoplay(chat_id)
    except Exception:
        pass
    playing = db.get(chat_id)
    total_sec = playing[0].get("seconds", 0) if playing else 0
    try:
        total_sec = float(total_sec or 0)
    except Exception:
        total_sec = 0
    thumb = await _maxres(videoid, thumb)
    src_label = _source_label(chat_id)
    try:
        from AoiMusic.utils.thumbnails import build_now_playing_card
        card_img = await build_now_playing_card(
            videoid=str(videoid or ""),
            title=str(title or "Unknown"),
            duration=str(duration_min or "00:00"),
            requester=str(user_name or "User"),
            artist=src_label,
            song_thumb=thumb,
            elapsed="00:00",
        )
        if card_img:
            thumb = card_img
    except Exception:
        pass
    body = _body(_, title, duration_min, user_name, thumb, videoid, src=src_label) + _next_html(chat_id)
    mid = await _send_card(chat_id, body + _rows(chat_id, videoid, 0, total_sec))
    if not mid:
        return None
    _panel_content[(chat_id, mid)] = (body, videoid, total_sec, time.time())
    _register(chat_id, mid, videoid, total_sec)
    if chat_id < 0:
        # Start-of-stream board bump for the per-chat /hot chart; fire-and-
        # forget so a slow mongo never delays the card.
        asyncio.create_task(_board_bump(chat_id, title))
        # Optional AI fun-fact (one cached AI call per unique song title
        # ever) — fire-and-forget, never blocks or fails the card.
    asyncio.create_task(
        _ticker(chat_id, mid, videoid, body, _panel_content[(chat_id, mid)])
    )
    return mid



async def _board_bump(chat_id: int, title: str) -> None:
    from AoiMusic.utils.database import count_track_play

    try:
        await count_track_play(chat_id, title)
    except Exception as e:
        LOGGER(__name__).warning(f"/hot board bump failed for {chat_id}: {e}")


async def _ticker(chat_id: int, mid: int, videoid, body: str, snapshot: tuple) -> None:
    _body_s, videoid, total_sec, start_t = snapshot
    entry = _panel_content.get((chat_id, mid))
    if entry:
        start_t = entry[3]
    try:
        dur_float = float(total_sec or 0)
    except Exception:
        dur_float = 0
    fail_streak = 0
    while (chat_id, mid) not in _closed_panels:
        await asyncio.sleep(_TICK)
        if (chat_id, mid) in _closed_panels:
            break
        playing = db.get(chat_id)
        if not playing or playing[0].get("rich_mid") != mid:
            break
        if (chat_id, mid) in _menu_page:
            continue
        played = float(playing[0].get("played", 0) or 0)
        html = body + _rows(chat_id, videoid, played, dur_float, playing=True)
        ok = await edit_rich(chat_id, mid, html)
        # A permanently dead message (deleted card, cleared history) used to
        # keep the ticker editing every tick forever — spamming the log with
        # a retry warning each time. Three consecutive failed edits mean the
        # panel is not coming back.
        fail_streak = 0 if ok else fail_streak + 1
        if fail_streak >= 3:
            _closed_panels.add((chat_id, mid))
            break
        if dur_float and played >= dur_float:
            break


async def refresh_after_toggle(chat_id: int, message_id: int, paused: bool) -> bool:
    """Re-render after a play/pause toggle, honouring an open menu page."""
    page = _menu_page.get((chat_id, message_id))
    if page is not None:
        playing = db.get(chat_id)
        videoid = playing[0].get("vidid") if playing else None
        return await show_menu(None, chat_id, message_id, videoid, page)
    return await refresh_panel(chat_id, message_id, paused)


async def refresh_panel(chat_id: int, message_id: int, paused: bool = False) -> bool:
    """Re-render the panel after pause/seek/state changes."""
    playing = db.get(chat_id)
    if not playing:
        return False
    if (chat_id, message_id) in _menu_page:
        return True
    cached = _panel_content.get((chat_id, message_id))
    videoid = playing[0].get("vidid")
    total_sec = playing[0].get("seconds") or 0
    title = (playing[0].get("title") or "").title()
    duration_min = playing[0].get("dur") or "--:--"
    user_name = playing[0].get("by") or "Videl Music"
    body = (
        cached[0]
        if cached
        else _body(None, title, duration_min, user_name, videoid=videoid, src=_source_label(chat_id))
        + _next_html(chat_id)
    )
    played = float(playing[0].get("played", 0) or 0)
    return await edit_rich(
        chat_id, message_id, body + _rows(chat_id, videoid, played, total_sec, playing=not paused)
    )


async def replace_panel(chat_id: int, message_id: int, text: str) -> bool:
    """Kill the ticker and leave a short status line on the old panel."""
    mark_panel_closed(chat_id, message_id)
    return await edit_rich(chat_id, message_id, f"<p>{rich_esc(text)}</p>")


async def snapshot_card(chat_id: int) -> int | None:
    """Re-send an up-to-date now-playing card without touching the live panel.

    Used by the mirror command (/player): the active panel keeps ticking,
    this is a read-only copy built straight from db state.
    """
    playing = db.get(chat_id)
    if not playing:
        return None
    videoid = playing[0].get("vidid")
    try:
        total_sec = float(playing[0].get("seconds") or 0)
    except Exception:
        total_sec = 0
    title = (playing[0].get("title") or "Unknown").title()
    duration_min = playing[0].get("dur") or "--:--"
    user_name = playing[0].get("by") or "Videl Music"
    thumb = await _maxres(videoid, None)
    src_label = _source_label(chat_id)
    try:
        from AoiMusic.utils.thumbnails import build_now_playing_card
        card_img = await build_now_playing_card(
            videoid=str(videoid or ""),
            title=str(title or "Unknown"),
            duration=str(duration_min or "00:00"),
            requester=str(user_name or "User"),
            artist=src_label,
            song_thumb=thumb,
            elapsed="00:00",
        )
        if card_img:
            thumb = card_img
    except Exception:
        pass
    body = _body(None, title, duration_min, user_name, thumb, videoid, src=src_label) + _next_html(chat_id)
    played = float(playing[0].get("played", 0) or 0)
    return await _send_card(chat_id, body + _rows(chat_id, videoid, played, total_sec))


async def notice_card(
    _,
    chat_id: int,
    heading: str,
    body_text: str,
    thumb=None,
) -> int | None:
    """Small rich notice card (added-to-queue, playlist done, etc.)."""
    html = rich_heading(f"{pemoji('🎵')} {rich_esc(heading)}", level=4)
    if thumb:
        html += rich_img(thumb)
    if body_text:
        html += rich_note(rich_esc(body_text).replace("\n", "<br>"))
    html += rich_button_row(
        rich_button("ᴄʟᴏsᴇ", callback_data="close", style="danger", emoji="CLOSE"),
        align="center",
    )
    return await _send_card(chat_id, html)


async def _ensure_body(_, chat_id: int, mid: int, videoid) -> tuple[str, float]:
    """Cached card body, or a rebuild from db (restart-surviving panels)."""
    cached = _panel_content.get((chat_id, mid))
    if cached:
        return cached[0], cached[2]
    playing = db.get(chat_id)
    total_sec = 0.0
    title, duration_min, user_name = "Unknown", "--:--", "Videl Music"
    if playing:
        try:
            total_sec = float(playing[0].get("seconds") or 0)
        except Exception:
            total_sec = 0
        title = (playing[0].get("title") or "Unknown").title()
        duration_min = playing[0].get("dur") or "--:--"
        user_name = playing[0].get("by") or "Videl Music"
    thumb = await _maxres(videoid, None)
    return _body(_, title, duration_min, user_name, thumb, videoid, src=_source_label(chat_id)) + _next_html(chat_id), total_sec


async def show_menu(_, chat_id: int, mid: int, videoid, page: int) -> bool:
    """Open a menu page on the rich panel; False if the message is not rich."""
    body, _t = await _ensure_body(_, chat_id, mid, videoid)
    ok = await edit_rich(chat_id, mid, body + menu_pages_rows(_, videoid, chat_id, page))
    if ok:
        _menu_page[(chat_id, mid)] = page
    return ok


async def show_main(chat_id: int, mid: int, videoid, playing: bool = True) -> bool:
    """Leave the menu and re-render the main progress rows."""
    body, total_sec = await _ensure_body(None, chat_id, mid, videoid)
    played = 0.0
    cur = db.get(chat_id)
    if cur:
        played = float(cur[0].get("played", 0) or 0)
    ok = await edit_rich(
        chat_id, mid, body + _rows(chat_id, videoid, played, total_sec, playing=playing)
    )
    if ok:
        _menu_page.pop((chat_id, mid), None)
    return ok


def menu_pages_rows(_, videoid, chat_id: int, page: int) -> str:
    """The 3-page player menu (controls / audio+queue / seek) in XENOBOT2 style."""
    fuzzy = f"MainMarkup {videoid}|{chat_id}"
    nav = rich_button_row(
        rich_button("ᴘʀᴇᴠ", callback_data=f"Pages Back|{page}|{videoid}|{chat_id}", style="primary", emoji="LEFT_CHEVRON"),
        rich_button("ʙᴀᴄᴋ", callback_data=fuzzy, style="primary", emoji="BACK_ARROW"),
        rich_button("ɴᴇxᴛ", callback_data=f"Pages Forw|{page}|{videoid}|{chat_id}", style="primary", emoji="RIGHT_CHEVRON"),
    )
    if page == 0:
        rows = (
            rich_button_row(
                _toggle_button(chat_id),
                rich_button("sᴋɪᴘ", callback_data=f"ADMIN Skip|{chat_id}", style="primary", emoji="NEXT_TRACK"),
                rich_button("sᴛᴏᴘ", callback_data=f"ADMIN Stop|{chat_id}", style="danger", emoji="STOP"),
            )
            + rich_button_row(
                rich_button("ǫᴜᴇᴜᴇ", callback_data=f"GetQueued {chat_id}|{videoid}", style="success", emoji="QUEUE_LIST"),
                rich_button("ʟᴏᴏᴘ", callback_data=f"ADMIN Loop|{chat_id}", style="primary", emoji="REPEAT"),
            )
        )
    elif page == 1:
        rows = (
            rich_button_row(
                rich_button("ᴍᴜᴛᴇ", callback_data=f"ADMIN Mute|{chat_id}", style="primary", emoji="VOLUME_MUTE"),
                rich_button("ᴜɴᴍᴜᴛᴇ", callback_data=f"ADMIN Unmute|{chat_id}", style="success", emoji="VOLUME_UP"),
            )
            + rich_button_row(
                rich_button("sʜᴜғғʟᴇ", callback_data=f"ADMIN Shuffle|{chat_id}", style="danger", emoji="SHUFFLE"),
                rich_button("ᴠᴏᴛᴇ-sᴋɪᴘ", callback_data=f"VSkip|{chat_id}", style="primary", emoji="NEXT_TRACK"),
            )
        )
    else:
        rows = (
            rich_button_row(
                rich_button("-10s", callback_data=f"ADMIN 1|{chat_id}", style="primary", emoji="PREVIOUS_TRACK"),
                rich_button("+10s", callback_data=f"ADMIN 2|{chat_id}", style="primary", emoji="NEXT_TRACK"),
            )
            + rich_button_row(
                rich_button("-30s", callback_data=f"ADMIN 3|{chat_id}", style="primary", emoji="PREVIOUS_TRACK"),
                rich_button("+30s", callback_data=f"ADMIN 4|{chat_id}", style="primary", emoji="NEXT_TRACK"),
            )
        )
    return rows + nav

async def reattach_from_db(_, chat_id: int, mid: int) -> bool:
    """Rebuild the now-playing panel on an existing message and restart the ticker."""
    playing = db.get(chat_id)
    if not playing:
        return False
    videoid = playing[0].get("vidid")
    try:
        total_sec = float(playing[0].get("seconds") or 0)
    except Exception:
        total_sec = 0
    title = (playing[0].get("title") or "").title()
    duration_min = playing[0].get("dur") or "--:--"
    user_name = playing[0].get("by") or "Videl Music"
    thumb = await _maxres(videoid, None)
    body = _body(_, title, duration_min, user_name, thumb, videoid, src=_source_label(chat_id)) + _next_html(chat_id)
    played = float(playing[0].get("played", 0) or 0)
    ok = await edit_rich(chat_id, mid, body + _rows(chat_id, videoid, played, total_sec))
    if ok:
        _closed_panels.discard((chat_id, mid))
        _menu_page.pop((chat_id, mid), None)
        _panel_content[(chat_id, mid)] = (body, videoid, total_sec, time.time())
        _register(chat_id, mid, videoid, total_sec)
        asyncio.create_task(
            _ticker(chat_id, mid, videoid, body, _panel_content[(chat_id, mid)])
        )
    return ok

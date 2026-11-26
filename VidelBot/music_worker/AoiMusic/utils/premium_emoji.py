"""
Centralized Telegram Premium (Custom) Emoji registry (Musiciconis 104-emoji pack), HTML helpers,
and Bot API 9.4+ InlineKeyboardButton builder for AoiMusic.

Supports:
  1. Text / Captions: <tg-emoji emoji-id="...">fallback</tg-emoji> (HTML)
  2. Buttons: icon_custom_emoji_id + style (PRIMARY / SUCCESS / DANGER)
     with full async/sync compatibility for Kurigram 2.2+ and Pyrogram
     (including MTProto raw.types.KeyboardButtonStyle injection).
"""

from __future__ import annotations

import inspect
import logging
import re
from typing import Any, Dict, Optional, Tuple

from pyrogram import enums
from pyrogram.types import InlineKeyboardButton

logger = logging.getLogger(__name__)

# Invisible 0px non-whitespace character so icon-only buttons render ONLY the centered premium emoji
BLANK_BTN_TEXT = "\u2060"

# All 59 configured Telegram Premium Custom Emoji IDs: key -> (emoji_id, fallback_emoji)
PREMIUM_EMOJIS: Dict[str, Tuple[str, str]] = {
    # --- Canonical Musiciconis Pack Keys (99 unique icons) ---
    "PLAY": ("6066663132623807248", "▶️"),
    "PAUSE": ("6066418362437608659", "⏸️"),
    "STOP": ("6066654031588107383", "⏹️"),
    "PREVIOUS_TRACK": ("6066492935954767988", "⏮️"),
    "NEXT_TRACK": ("6066451463750558746", "⏭️"),
    "MUSIC_NOTE": ("6066607426897977062", "🎵"),
    "DOUBLE_MUSIC_NOTES": ("6066500426377732551", "🎶"),
    "HEADPHONES": ("6069022186360807592", "🎧"),
    "MICROPHONE": ("6066698115132434387", "🎤"),
    "VOLUME_UP": ("6069033293146235233", "🔊"),
    "VOLUME_DOWN": ("6066517610541883855", "🔉"),
    "VOLUME_MUTE": ("6066695134425131213", "🔇"),
    "REPEAT": ("6066811193031402055", "🔁"),
    "SHUFFLE": ("6068679336301437040", "🔀"),
    "EQUALIZER": ("6066421922965496337", "📊"),
    "VINYL_DISC": ("6066871240969167384", "💿"),
    "QUEUE_LIST": ("6066733492778049530", "📋"),
    "SLIDERS_EQ": ("6068611501587964858", "🎚️"),
    "VIDEO_STREAM": ("6066909517717709517", "🎬"),
    "IMAGE_PHOTO": ("6066907950054647168", "🖼️"),
    "CLOSE": ("6066466053754463286", "❌"),
    "CHECK": ("6066820255412396225", "✔️"),
    "CHECK_CIRCLE": ("6066426591594946706", "✅"),
    "PLUS": ("6069080524901589066", "➕"),
    "MINUS": ("6069098147152407481", "➖"),
    "REFRESH": ("6066643856810582424", "🔄"),
    "SEARCH": ("6066390569704240313", "🔎"),
    "SETTINGS": ("6066744943160861801", "⚙️"),
    "HOME": ("6066493627444502468", "🏠"),
    "MENU": ("6068858329063498934", "☰"),
    "GRID": ("6068839418322493676", "🔢"),
    "BACK_ARROW": ("6066659211318667082", "⬅️"),
    "FORWARD_ARROW": ("6066487970972574543", "➡️"),
    "LEFT_CHEVRON": ("6068982135790773184", "◀️"),
    "RIGHT_CHEVRON": ("6066847584289300037", "▶️"),
    "UP_ARROW": ("6066502921753730261", "⬆️"),
    "DOWN_ARROW": ("6066869149320096120", "⬇️"),
    "REPLY_UNDO": ("6066773689376974660", "↩️"),
    "FORWARD_REDO": ("6068977707679491244", "↪️"),
    "DOWNLOAD": ("6066567341968205231", "⬇️"),
    "UPLOAD": ("6068905788452118309", "⬆️"),
    "SHARE": ("6066454770875376573", "📤"),
    "LINK": ("6068787500757818784", "🔗"),
    "COPY": ("6066511434378911635", "📋"),
    "EDIT_PENCIL": ("6066788180596629769", "✏️"),
    "TRASH_DELETE": ("6066593365175051883", "🗑️"),
    "POWER": ("6069089677476898273", "⏻"),
    "LOGOUT_EXIT": ("6066453804507734747", "🚪"),
    "BOT": ("6066465456754009142", "🤖"),
    "USER": ("6068943987891250263", "👤"),
    "USERS_GROUP": ("6069153556525490415", "👥"),
    "CHAT_BUBBLE": ("6068955730331836389", "💬"),
    "SEND_PAPERPLANE": ("6066781111080461731", "✈️"),
    "BELL_NOTIFY": ("6066892797410026603", "🔔"),
    "MEGAPHONE": ("6066739479962462015", "📣"),
    "BROADCAST_LIVE": ("6066731577222636252", "📡"),
    "HELP_SUPPORT": ("6066518181772534867", "❓"),
    "INFO": ("6066810230958727559", "ℹ️"),
    "WARNING": ("6068917728461202134", "⚠️"),
    "BAN_BLOCK": ("6066496702641087303", "🚫"),
    "RANK_1": ("6066824481660215642", "🥇"),
    "RANK_2": ("6066561045546149371", "🥈"),
    "RANK_3": ("6066390664193516627", "🥉"),
    "TOP_PODIUM": ("6069147178499056826", "🏆"),
    "CROWN_OWNER": ("6066440932490748910", "👑"),
    "TROPHY": ("6069099929563833379", "🏆"),
    "MEDAL": ("6066573458001634091", "🏅"),
    "STAR": ("6068887487596470694", "⭐"),
    "SPARKLES_AI": ("6066656509784237873", "✨"),
    "DIAMOND_VIP": ("6066369953861217466", "💎"),
    "FIRE_STREAK": ("6066570580373544527", "🔥"),
    "BOLT_FAST": ("6069025987406864231", "⚡"),
    "HEART": ("6066908821933014310", "❤️"),
    "GIFT": ("6066902422431736194", "🎁"),
    "COIN": ("6066439974713041800", "🪙"),
    "WALLET": ("6066573664160064324", "👛"),
    "SHOP_BAG": ("6066531964322586517", "🛍️"),
    "CART": ("6069068275654861776", "🛒"),
    "TICKET_PASS": ("6068945688698299114", "🎫"),
    "VERIFIED_BADGE": ("6068908631720468739", "✅"),
    "LOCK": ("6066820006304293400", "🔒"),
    "UNLOCK": ("6066356987354948403", "🔓"),
    "SHIELD_ADMIN": ("6069034461377341161", "🛡️"),
    "SHIELD_CHECK": ("6066539682378818005", "🛡️"),
    "KEY_ACCESS": ("6066687386304128094", "🔑"),
    "CLOCK_DURATION": ("6066884624087262867", "🕒"),
    "TIMER_COOLDOWN": ("6068633259892285750", "⏱️"),
    "CALENDAR_DAILY": ("6066622115686129339", "📅"),
    "CHART_STATS": ("6068970977465738321", "📊"),
    "TRENDING_UP": ("6068972704042591222", "📈"),
    "CPU_SERVER": ("6066457717222940664", "🖥️"),
    "DATABASE": ("6066620191540781859", "💾"),
    "WIFI_PING": ("6066539334486466977", "📶"),
    "TERMINAL_DEV": ("6066621647534695715", "💻"),
    "GLOBE_LANG": ("6068829578552421812", "🌐"),
    "GAMEPAD": ("6066673363235906350", "🎮"),
    "DICE_LUCK": ("6066644080148882936", "🎲"),
    "FOLDER": ("6066763802362259241", "📁"),
    "DOCUMENT_RULES": ("6066514191747915995", "📄"),
    # --- Backward-Compatible Legacy Aliases ---
    "MIC_ON": ("6066698115132434387", "🎤"),
    "MIC_OFF": ("6066695134425131213", "🔇"),
    "SPEAKER_ON": ("6069033293146235233", "🔊"),
    "SPEAKER_OFF": ("6066695134425131213", "🔇"),
    "MUSIC_ON": ("6066607426897977062", "🎵"),
    "MUSIC_OFF": ("6066695134425131213", "🔇"),
    "PLAY_CIRCLE": ("6066663132623807248", "▶️"),
    "VIDEO_ON": ("6066909517717709517", "🎬"),
    "STOP_CIRCLE": ("6066654031588107383", "⏹️"),
    "VIDEO_OFF": ("6066695134425131213", "🔇"),
    "PLAYLIST": ("6066733492778049530", "📋"),
    "ROBOT": ("6066465456754009142", "🤖"),
    "FORWARD": ("6066487970972574543", "➡️"),
    "PIN": ("6066810230958727559", "ℹ️"),
    "MUTE": ("6066695134425131213", "🔇"),
    "SPEED": ("6066451463750558746", "⏭️"),
    "TIMER": ("6068633259892285750", "⏱️"),
    "LOADING": ("6066643856810582424", "🔄"),
    "MAIL": ("6066781111080461731", "✈️"),
    "YOUTUBE": ("6066909517717709517", "🎬"),
    "CROSS_BADGE": ("6066466053754463286", "❌"),
    "SPARKLES": ("6066656509784237873", "✨"),
    "CLOCK_24H": ("6066884624087262867", "🕒"),
    "HOURGLASS": ("6068633259892285750", "⏱️"),
    "GIFT_ALT": ("6066902422431736194", "🎁"),
    "USER_BLOCKED": ("6066496702641087303", "🚫"),
    "COMPOSE": ("6066788180596629769", "✏️"),
    "PENCIL": ("6066788180596629769", "✏️"),
    "FILE": ("6066514191747915995", "📄"),
    "SEARCH_ALT": ("6066390569704240313", "🔎"),
    "LIVE_DOT": ("6066731577222636252", "📡"),
    "PLAY_OUTLINE": ("6066663132623807248", "▶️"),
    "LOOP": ("6066811193031402055", "🔁"),
    "LIGHTNING": ("6069025987406864231", "⚡"),
    "EQ_OFF": ("6066418362437608659", "⏸️"),
    "ADD_PLAYLIST": ("6069080524901589066", "➕"),
    "MUSIC_SINGLE": ("6066607426897977062", "🎵"),
    "DOWNLOAD_TRAY": ("6066567341968205231", "⬇️"),
    "CHECKMARK": ("6066426591594946706", "✅"),
    "NEW_BADGE": ("6066656509784237873", "✨"),
    "WAVE": ("6066656509784237873", "✨"),
    "ANIME_WINK": ("6066656509784237873", "✨"),
    "ANIME_COOL": ("6068887487596470694", "⭐"),
    "BEST_BADGE": ("6066440932490748910", "👑"),
    "VERIFIED": ("6068908631720468739", "✅"),
    "GRID_MENU": ("6068839418322493676", "🔢"),
    "TRASH": ("6066593365175051883", "🗑️"),
    "CLOVER": ("6066656509784237873", "✨"),
    "CROWN": ("6066440932490748910", "👑"),
    "LEVEL_UP": ("6068972704042591222", "📈"),
    "TOP_ICON": ("6069147178499056826", "🏆"),
}

_TG_EMOJI_OPEN_RE = re.compile(
    r"""<tg-emoji\s+emoji-id=(?:"|')?(\d+)(?:"|')?\s*>""",
    re.IGNORECASE,
)
_TG_EMOJI_CLOSE_RE = re.compile(r"</tg-emoji>", re.IGNORECASE)

_IKB_PARAMS: Optional[set] = None
_IKB_ICON_PARAM: Optional[str] = None
_IKB_ICON_IS_INT: bool = False
_PATCHED: bool = False


def _detect_ikb_capabilities() -> None:
    global _IKB_PARAMS, _IKB_ICON_PARAM, _IKB_ICON_IS_INT
    if _IKB_PARAMS is not None:
        return
    try:
        sig = inspect.signature(InlineKeyboardButton.__init__)
        _IKB_PARAMS = set(sig.parameters.keys())
        for candidate in ("icon_custom_emoji_id", "icon", "custom_emoji_id"):
            if candidate in _IKB_PARAMS:
                _IKB_ICON_PARAM = candidate
                ann = sig.parameters[candidate].annotation
                if ann is int or (isinstance(ann, str) and "int" in ann and "str" not in ann):
                    _IKB_ICON_IS_INT = True
                break
    except Exception:
        _IKB_PARAMS = set()
        _IKB_ICON_PARAM = None


def eid(key: str) -> str:
    """Return the raw custom emoji ID string for a registry key (or pass-through numeric ID)."""
    entry = PREMIUM_EMOJIS.get(key)
    if entry:
        return entry[0]
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        return str(key)
    raise KeyError(f"Unknown premium emoji key: {key!r}")


def fallback(key: str) -> str:
    """Return the fallback standard emoji for a registry key."""
    entry = PREMIUM_EMOJIS.get(key)
    return entry[1] if entry else ""


def tg_emoji(key: str, custom_fallback: Optional[str] = None) -> str:
    """Return an HTML <tg-emoji emoji-id="...">fallback</tg-emoji> tag for text/captions."""
    entry = PREMIUM_EMOJIS.get(key)
    if entry:
        emoji_id, fb = entry
        return f'<tg-emoji emoji-id="{emoji_id}">{custom_fallback or fb}</tg-emoji>'
    if isinstance(key, int) or (isinstance(key, str) and key.isdigit()):
        return f'<tg-emoji emoji-id="{key}">{custom_fallback or "✨"}</tg-emoji>'
    return custom_fallback or ""


def pe(key: str, custom_fallback: Optional[str] = None) -> str:
    """Short alias for tg_emoji()."""
    return tg_emoji(key, custom_fallback=custom_fallback)


def _normalize_tg_emoji_html(text: Any) -> Any:
    """Convert <tg-emoji emoji-id="...">...</tg-emoji> into Pyrogram/Kurigram <emoji id="...">...</emoji>."""
    if isinstance(text, str) and "<tg-emoji" in text.lower():
        text = _TG_EMOJI_OPEN_RE.sub(r'<emoji id="\1">', text)
        text = _TG_EMOJI_CLOSE_RE.sub("</emoji>", text)
    return text


def _resolve_style(style: Optional[str]) -> Any:
    """Resolve 'PRIMARY' / 'SUCCESS' / 'DANGER' into enums.ButtonStyle if supported."""
    if not style:
        return None
    btn_style_enum = getattr(enums, "ButtonStyle", None)
    if btn_style_enum is None:
        return None
    return getattr(btn_style_enum, str(style).upper(), None)


def pbtn(
    text: str,
    *,
    emoji: Optional[str] = None,
    style: Optional[str] = None,
    **kwargs: Any,
) -> InlineKeyboardButton:
    """
    Build an InlineKeyboardButton with Telegram Bot API 9.4+ custom emoji icon
    (icon_custom_emoji_id) and optional colored button style (PRIMARY/SUCCESS/DANGER).
    """
    _detect_ikb_capabilities()

    btn_kwargs = dict(kwargs)
    resolved_icon: Optional[str] = None
    if emoji:
        try:
            resolved_icon = eid(emoji)
        except KeyError:
            resolved_icon = None

    resolved_style = _resolve_style(style)
    if resolved_style is not None and _IKB_PARAMS and "style" in _IKB_PARAMS:
        btn_kwargs["style"] = resolved_style

    used_native_icon = False
    if resolved_icon and _IKB_ICON_PARAM:
        icon_val: Any = int(resolved_icon) if _IKB_ICON_IS_INT else resolved_icon
        btn_kwargs[_IKB_ICON_PARAM] = icon_val
        used_native_icon = True

    try:
        btn = InlineKeyboardButton(text=text, **btn_kwargs)
    except (TypeError, ValueError):
        if used_native_icon and _IKB_ICON_PARAM in btn_kwargs:
            alt_val = (
                str(resolved_icon)
                if isinstance(btn_kwargs[_IKB_ICON_PARAM], int)
                else int(resolved_icon)
            )
            btn_kwargs[_IKB_ICON_PARAM] = alt_val
            try:
                btn = InlineKeyboardButton(text=text, **btn_kwargs)
            except (TypeError, ValueError):
                btn_kwargs.pop(_IKB_ICON_PARAM, None)
                used_native_icon = False
                try:
                    btn = InlineKeyboardButton(text=text, **btn_kwargs)
                except (TypeError, ValueError):
                    btn_kwargs.pop("style", None)
                    btn = InlineKeyboardButton(text=text, **btn_kwargs)
        else:
            btn_kwargs.pop("style", None)
            btn = InlineKeyboardButton(text=text, **btn_kwargs)

    if resolved_icon and not used_native_icon:
        try:
            setattr(btn, "_pe_icon_id", int(resolved_icon))
            setattr(btn, "_pe_style_name", str(style).upper() if style else None)
        except Exception:
            pass

    return btn


def _inject_raw_button_style(btn_self: Any, raw_btn: Any, kb_style_cls: Any) -> Any:
    """Attach MTProto KeyboardButtonStyle(icon=...) to a serialized raw button if needed."""
    icon_id = getattr(btn_self, "_pe_icon_id", None)
    if icon_id and raw_btn is not None and hasattr(raw_btn, "style"):
        existing = getattr(raw_btn, "style", None)
        style_name = getattr(btn_self, "_pe_style_name", None)
        bg_primary = getattr(existing, "bg_primary", None) or (
            True if style_name == "PRIMARY" else None
        )
        bg_danger = getattr(existing, "bg_danger", None) or (
            True if style_name == "DANGER" else None
        )
        bg_success = getattr(existing, "bg_success", None) or (
            True if style_name == "SUCCESS" else None
        )
        try:
            raw_btn.style = kb_style_cls(
                bg_primary=bg_primary,
                bg_danger=bg_danger,
                bg_success=bg_success,
                icon=int(icon_id),
            )
        except Exception:
            pass
    return raw_btn


def patch_pyrogram_premium_emoji() -> None:
    """
    Install safe, idempotent, async/sync-aware runtime shims for Kurigram & Pyrogram:
      1. Normalizes <tg-emoji emoji-id="..."> into <emoji id="..."> in HTML.parse().
      2. Injects MTProto raw.types.KeyboardButtonStyle(icon=...) in InlineKeyboardButton.write().
    """
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True
    _detect_ikb_capabilities()

    try:
        from pyrogram.parser.html import HTML as PyroHTML

        orig_parse = getattr(PyroHTML, "parse", None)
        if orig_parse and not getattr(orig_parse, "_tg_emoji_patched", False):
            if inspect.iscoroutinefunction(orig_parse):
                async def _patched_html_parse(self, text: str, *args: Any, **kwargs: Any):
                    return await orig_parse(self, _normalize_tg_emoji_html(text), *args, **kwargs)
            else:
                def _patched_html_parse(self, text: str, *args: Any, **kwargs: Any):
                    return orig_parse(self, _normalize_tg_emoji_html(text), *args, **kwargs)

            _patched_html_parse._tg_emoji_patched = True
            PyroHTML.parse = _patched_html_parse
            logger.info("Enabled <tg-emoji> -> <emoji> HTML compatibility shim.")
    except Exception as exc:
        logger.debug("Skipped HTML <tg-emoji> shim: %s", exc)

    try:
        from pyrogram import raw

        kb_style_cls = getattr(raw.types, "KeyboardButtonStyle", None)
        orig_write = getattr(InlineKeyboardButton, "write", None)
        if (
            kb_style_cls is not None
            and orig_write is not None
            and not getattr(orig_write, "_pe_write_patched", False)
        ):
            try:
                style_sig = inspect.signature(kb_style_cls.__init__)
                has_icon_field = "icon" in style_sig.parameters
            except Exception:
                has_icon_field = False

            if has_icon_field:
                if inspect.iscoroutinefunction(orig_write):
                    async def _patched_write(self, *args: Any, **kwargs: Any):
                        raw_btn = await orig_write(self, *args, **kwargs)
                        return _inject_raw_button_style(self, raw_btn, kb_style_cls)
                else:
                    def _patched_write(self, *args: Any, **kwargs: Any):
                        raw_btn = orig_write(self, *args, **kwargs)
                        return _inject_raw_button_style(self, raw_btn, kb_style_cls)

                _patched_write._pe_write_patched = True
                InlineKeyboardButton.write = _patched_write
                logger.info("Enabled MTProto KeyboardButtonStyle icon compatibility shim.")
    except Exception as exc:
        logger.debug("Skipped InlineKeyboardButton.write shim: %s", exc)

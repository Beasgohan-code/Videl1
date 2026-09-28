"""
aiogram bridge — Bot API 10.3 next to pyrofork (MTProto layer 220).

Why both?
  • pyrofork receives **all updates** (one consumer per token – running aiogram
    polling as well would split the updates between the two libraries) and does
    everything MTProto is great at: 4 GB files, user sessions for the saver,
    clone workers, raw API.
  • aiogram is used as an **outgoing Bot API client** for the features that only
    exist in the newest Bot API and are missing from layer 220:
      – coloured inline buttons (``style`` = success / danger / primary)
      – ephemeral messages in groups (visible only to one user)
      – rich messages (headings, tables, collapsible details)
      – managed bots (one-tap clone creation, token rotation)
      – Stars subscription links / cancel, star balance & transactions,
        Telegram Premium gifts, profile photos, message drafts
  Message IDs are shared, so a message sent by aiogram can be edited/deleted by
  pyrofork and vice-versa; callback buttons sent by aiogram arrive through
  pyrofork's normal handlers.

Every helper here is best-effort: when aiogram is disabled, not installed, or
Telegram rejects a call, it returns ``None``/``False`` and the caller uses the
pyrofork path instead. Nothing in this module raises into a handler unless the
function name says so (``call``).
"""
import logging
import time

from config import (AIOGRAM_ENABLED, BOT_API_URL, BOT_TOKEN, COLORED_BUTTONS, EPHEMERAL_REPLIES)

log = logging.getLogger("videl.botapi")

try:  # aiogram is optional at import time – Videl still runs without it
    import aiogram
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.session.aiohttp import AiohttpSession
    from aiogram.client.telegram import TelegramAPIServer
    from aiogram import types as at
    HAVE_AIOGRAM = True
except Exception:  # pragma: no cover - exercised only without aiogram installed
    aiogram = None
    HAVE_AIOGRAM = False

_bot = None               # main aiogram Bot
_session = None           # optional injected session (tests / custom)
_workers: dict = {}       # token → Bot for clone bots
_me = None                # cached getMe (aiogram User)
_me_ts = 0.0
_override = None          # tests can force-enable/disable
_failures = 0             # consecutive network failures → temporary back-off
_backoff_until = 0.0


# ─────────────────────────── lifecycle ───────────────────────────
def enabled() -> bool:
    if _override is not None:
        return _override and HAVE_AIOGRAM and time.time() >= _backoff_until
    return HAVE_AIOGRAM and AIOGRAM_ENABLED and bool(BOT_TOKEN) and time.time() >= _backoff_until


def install(session=None, enable: bool = True):
    """Use a custom aiogram session (tests) and force the bridge on/off."""
    global _session, _bot, _override, _me, _failures, _backoff_until
    _session, _bot, _override, _me = session, None, enable, None
    _failures, _backoff_until = 0, 0.0
    _workers.clear()


REQUEST_TIMEOUT = 12  # seconds – a slow Bot API must never stall a menu (MTProto fallback kicks in)


def _new_session():
    if _session is not None:
        return _session
    kw = {"timeout": REQUEST_TIMEOUT}
    if BOT_API_URL:
        kw["api"] = TelegramAPIServer.from_base(BOT_API_URL.rstrip("/"))
    return AiohttpSession(**kw)


def bot():
    """The shared aiogram Bot for Videl's own token (or None)."""
    global _bot
    if not enabled():
        return None
    if _bot is None:
        _bot = Bot(BOT_TOKEN, session=_new_session(), default=DefaultBotProperties(parse_mode="HTML"))
    return _bot


def worker(token: str):
    """An aiogram Bot for a clone's token (cached)."""
    if not enabled() or not token:
        return None
    b = _workers.get(token)
    if b is None:
        b = _workers[token] = Bot(token, session=_new_session(), default=DefaultBotProperties(parse_mode="HTML"))
    return b


async def close():
    global _bot
    for b in [_bot, *_workers.values()]:
        if b is not None and _session is None:
            try:
                await b.session.close()
            except Exception:
                pass
    _bot = None
    _workers.clear()


def _ok():
    global _failures
    _failures = 0


def _failed(e: Exception):
    """Network errors trip a short back-off so a Bot API outage never slows handlers down."""
    global _failures, _backoff_until
    name = type(e).__name__
    if "Network" in name or "Timeout" in name or "ClientConnector" in name or "ServerError" in name:
        _failures += 1
        if _failures >= 3:
            _backoff_until = time.time() + 60
            log.warning(f"Bot API unreachable ({name}) – using MTProto only for 60s")
    log.debug(f"Bot API call failed: {name}: {e}")


async def call(coro_factory):
    """Run ``coro_factory(bot)`` and return its result. Raises on Telegram errors."""
    b = bot()
    if b is None:
        raise RuntimeError("aiogram bridge disabled")
    try:
        res = await coro_factory(b)
        _ok()
        return res
    except Exception as e:
        _failed(e)
        raise


async def try_call(coro_factory, default=None):
    """Like :func:`call` but returns *default* instead of raising."""
    try:
        return await call(coro_factory)
    except Exception:
        return default


async def me(refresh: bool = False):
    """Cached aiogram ``getMe`` (has Bot API-only flags such as ``can_manage_bots``)."""
    global _me, _me_ts
    if _me is None or refresh or time.time() - _me_ts > 3600:
        got = await try_call(lambda b: b.get_me())
        if got is not None:
            _me, _me_ts = got, time.time()
    return _me


async def can_manage_bots() -> bool:
    m = await me()
    return bool(m and getattr(m, "can_manage_bots", False))


def version() -> str:
    if not HAVE_AIOGRAM:
        return "not installed"
    return f"aiogram {aiogram.__version__} · Bot API {aiogram.__api_version__}"


# ─────────────────────────── keyboards ───────────────────────────
_DANGER = ("❌", "🗑", "🚫", "🔴", "⛔", "➖", "🛑", "⏹")
_SUCCESS = ("✅", "💎", "⭐", "🟢", "🎁", "🆓", "🎉", "♾", "🔁 Resume", "💳")
_PRIMARY = ("⚡", "🚀", "🤖 Create", "🔑", "🤝", "📢 Select", "✨")


def style_for(text: str, data: str = "") -> str | None:
    """Pick a Bot API button style from the label / callback (None = default)."""
    if not COLORED_BUTTONS:
        return None
    t = (text or "").strip()
    d = data or ""
    if d.startswith(("confirm_delete_", "do_delete_", "cancel_creation", "support_cancel", "uadm:ban", "uadm:pdel",
                     "sub_toggle:cancel", "xownok_")) or t.startswith(_DANGER):
        return "danger"
    if d.startswith(("stars_buy:", "stars_sub", "gift_buy:", "buy_premium", "trial_btn", "create_bot", "uadm:p",
                     "sub_toggle:resume")) or t.startswith(_SUCCESS):
        return "success"
    if t.startswith(_PRIMARY):
        return "primary"
    return None


def is_noop(data: str) -> bool:
    """Status chips / page indicators use callback_data "noop" or "noop:<anything>"."""
    return data == "noop" or data.startswith("noop:")


def convert_markup(markup):
    """pyrofork InlineKeyboardMarkup → aiogram InlineKeyboardMarkup with styles.
    Returns None if a button can't be represented (caller then uses pyrofork)."""
    if markup is None or not HAVE_AIOGRAM:
        return None
    rows = getattr(markup, "inline_keyboard", None)
    if rows is None:
        return None
    out = []
    for row in rows:
        new_row = []
        for b in row:
            kw = {"text": b.text, "style": style_for(b.text, getattr(b, "callback_data", "") or "")}
            if getattr(b, "callback_data", None) is not None:
                data = b.callback_data
                data = data.decode() if isinstance(data, bytes) else data
                if is_noop(data) and hasattr(at, "DisabledButton"):
                    kw["disabled"] = at.DisabledButton()      # Bot API 10.3: greyed out, does nothing
                    kw["style"] = None
                else:
                    kw["callback_data"] = data
            elif getattr(b, "url", None):
                kw["url"] = b.url
            elif getattr(b, "copy_text", None):
                kw["copy_text"] = at.CopyTextButton(text=b.copy_text)
            elif getattr(b, "switch_inline_query_current_chat", None) is not None:
                kw["switch_inline_query_current_chat"] = b.switch_inline_query_current_chat
            elif getattr(b, "switch_inline_query", None) is not None:
                kw["switch_inline_query"] = b.switch_inline_query
            elif getattr(b, "web_app", None):
                kw["web_app"] = at.WebAppInfo(url=b.web_app.url)
            elif getattr(b, "user_id", None):
                kw["url"] = f"tg://user?id={b.user_id}"
            else:
                return None  # login_url / callback_game / pay … → keep pyrofork
            if kw["style"] is None:
                kw.pop("style")
            new_row.append(at.InlineKeyboardButton(**kw))
        out.append(new_row)
    return at.InlineKeyboardMarkup(inline_keyboard=out)


def colored() -> bool:
    return COLORED_BUTTONS and enabled()


def is_main(client) -> bool:
    """True when a pyrofork *client* is Videl itself (not a clone worker or user session):
    aiogram always speaks with Videl's token, so it must never touch another bot's chats."""
    if client is None:
        return True
    me = getattr(client, "me", None)
    if me is None:
        return False
    try:
        return int(getattr(me, "id", 0)) == int(BOT_TOKEN.split(":", 1)[0])
    except (TypeError, ValueError):
        return False


def _preview(pic: str, preview: bool = False):
    if pic:
        return at.LinkPreviewOptions(url=pic, prefer_large_media=True, show_above_text=True)
    return at.LinkPreviewOptions(is_disabled=not preview)


async def send_text(chat_id: int, text: str, reply_markup=None, pic: str = "", reply_to: int = None,
                    effect_id=None) -> bool:
    """Send an HTML text message (optional big preview above the text) with coloured buttons."""
    if not colored():
        return False
    kb = convert_markup(reply_markup)
    if reply_markup is not None and kb is None:
        return False
    kwargs = dict(chat_id=chat_id, text=text, link_preview_options=_preview(pic), reply_markup=kb)
    if reply_to:
        kwargs["reply_parameters"] = at.ReplyParameters(message_id=reply_to, allow_sending_without_reply=True)
    if effect_id and int(chat_id) > 0:
        kwargs["message_effect_id"] = str(effect_id)
    try:
        await call(lambda b: b.send_message(**kwargs))
        return True
    except Exception as e:
        if "effect" in str(e).lower() and "message_effect_id" in kwargs:
            kwargs.pop("message_effect_id")
            return bool(await try_call(lambda b: b.send_message(**kwargs)))
        return False


async def edit_text(chat_id: int, message_id: int, text: str, reply_markup=None, pic: str = "",
                    preview: bool = False) -> bool:
    """Edit a message's text (+ preview) with coloured buttons. True when handled."""
    if not colored():
        return False
    kb = convert_markup(reply_markup)
    if reply_markup is not None and kb is None:
        return False
    try:
        await call(lambda b: b.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                                 link_preview_options=_preview(pic, preview), reply_markup=kb))
        return True
    except Exception as e:
        return "not modified" in str(e).lower()


# ─────────────────────────── ephemeral / drafts / rich ───────────────────────────
async def ephemeral(chat_id: int, user_id: int, text: str, reply_markup=None) -> bool:
    """Send a group message only *user_id* can see (Bot API 10.3). True on success."""
    if not (EPHEMERAL_REPLIES and enabled()) or int(chat_id) > 0:
        return False
    # MTProto layer 220 can't parse callbacks coming from ephemeral messages →
    # only link / copy buttons are allowed on them.
    for row in getattr(reply_markup, "inline_keyboard", None) or []:
        if any(getattr(b, "callback_data", None) is not None for b in row):
            return False
    kb = convert_markup(reply_markup) if reply_markup is not None else None
    if reply_markup is not None and kb is None:
        return False
    res = await try_call(lambda b: b.send_message(
        chat_id=chat_id, text=text, reply_markup=kb, link_preview_options=at.LinkPreviewOptions(is_disabled=True),
        ephemeral_message_parameters=at.EphemeralMessageParameters(receiver_user_id=user_id)))
    return res is not None


async def draft(chat_id: int, draft_id: int, text: str = "") -> bool:
    """Bot API ``sendMessageDraft`` (empty text → “Thinking…”)."""
    if not enabled() or int(chat_id) <= 0:
        return False
    res = await try_call(lambda b: b.send_message_draft(chat_id=chat_id, draft_id=draft_id, text=text or ""))
    return bool(res)


async def send_rich(chat_id: int, blocks: list, reply_markup=None) -> bool:
    """Send a rich message built from ``InputRichBlock*`` objects."""
    if not enabled():
        return False
    kb = convert_markup(reply_markup) if reply_markup is not None else None
    res = await try_call(lambda b: b.send_rich_message(
        chat_id=chat_id, rich_message=at.InputRichMessage(blocks=blocks), reply_markup=kb))
    return res is not None


async def status_line() -> str:
    """One-line health summary for the boot report / /botapi."""
    if not HAVE_AIOGRAM:
        return "❌ aiogram not installed"
    if not enabled():
        return f"⏸ off ({version()})"
    m = await me(refresh=True)
    if m is None:
        return f"⚠️ unreachable ({version()})"
    from config import MANAGED_BOTS
    flags = []
    if COLORED_BUTTONS:
        flags.append("🎨 colours")
    if EPHEMERAL_REPLIES:
        flags.append("👻 ephemeral")
    if MANAGED_BOTS:
        flags.append("🤖 managed bots " + ("✅" if getattr(m, "can_manage_bots", False) else "(enable in @BotFather)"))
    return f"✅ {version()}" + (" · " + " · ".join(flags) if flags else "")

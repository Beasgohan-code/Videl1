"""
✏️ Auto-Rename (full feature set of the Auto-Rename bot, rebuilt for Videl):

  /autorename <template>   save a template → every file you send is renamed automatically
  /autorename              settings panel (mode · pause · clean tags · word rules · media type · MP4→MKV ·
                           metadata · thumbnail · history · queue)
  ✍️ Manual mode            every file asks for a new name (💡 suggestion / 📄 keep name / send-as buttons)
  /setmedia                send as document / video / audio / auto
  /metadata  /settitle …   ffmpeg metadata (title, author, artist, audio, subtitle, video, encoded_by, custom_tag)
  /start_sequence  /end_sequence   collect files → sent back sorted by season / episode / quality
  /testrename <name>       preview the result without uploading
  /leaderboard             top renamers (today · week · month · year · all-time)
  /tutorial                placeholders & examples
  /renameset (admins)      global switch · anti-NSFW · dump channel · verification

Captions and thumbnails are shared with the saver (/set_caption {filename} {size} {duration} {title}
{episode} {quality} … , /set_thumb).
Ban / maintenance / force-sub are enforced by the core middleware (groups -3 / -2).
"""
import asyncio
import html
import logging
import os

from config import env_int
import time

from pyrogram import Client, StopPropagation, enums, filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

import config
from core.db import vdb
from core.ui import smart_edit
from renamer import engine, extract, store, verify

log = logging.getLogger("videl.rename.ui")

LEADERBOARD_DELETE_TIMER = env_int("LEADERBOARD_DELETE_TIMER", 30)
LEADERBOARD_PIC = os.environ.get("LEADERBOARD_PIC", "")
SAMPLE = "[SubsPlease] Solo Leveling S02E05 (1080p) [Dual Audio Hindi Jap] x265.mkv"

_state: dict[int, dict] = {}                 # uid -> {"kind": template|meta|thumb|dump, "field": …, "ts": …}
_sequences: dict[int, list] = {}             # uid -> [Message, …]
_seq_notes: dict[int, list] = {}             # uid -> reply ids to clean up
_prompts: dict[int, dict] = {}               # uid -> {file msg id: {"msg", "ts", "send_as", "sugg", "prompt"}}
MAX_PROMPTS = 20
_indexes_ready = False


def esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _state_ttl() -> int:
    return max(1, getattr(config, "STATE_TIMEOUT_MIN", 15)) * 60


def _get_state(uid: int):
    st = _state.get(uid)
    if st and time.time() - st.get("ts", 0) > _state_ttl():
        _state.pop(uid, None)
        return None
    return st


def _live_prompts(uid: int) -> dict:
    """Pending manual-mode name prompts of one user (expired ones dropped)."""
    box = _prompts.get(uid)
    if not box:
        return {}
    ttl = _state_ttl() * 2
    for pid in [p for p, e in box.items() if time.time() - e["ts"] > ttl]:
        box.pop(pid, None)
    if not box:
        _prompts.pop(uid, None)
    return box or {}


def prune_prompts():
    """Watchdog hook: forget expired prompts of every user."""
    for uid in list(_prompts):
        _live_prompts(uid)


def clear_user(uid: int) -> list:
    """Used by /cancel – returns what was cancelled."""
    done = []
    if _prompts.pop(uid, None):
        done.append("rename prompts")
    if _state.pop(uid, None) or verify._input.pop(uid, None):
        done.append("rename input")
    if _sequences.pop(uid, None) is not None:
        _seq_notes.pop(uid, None)
        done.append("rename sequence")
    n = engine.cancel_user(uid)
    if n:
        done.append(f"{n} rename job(s)")
    return done


async def _enabled() -> bool:
    return bool(await vdb.get_setting("rn_enabled", True))


async def _ensure_indexes():
    global _indexes_ready
    if not _indexes_ready:
        _indexes_ready = True
        await store.ensure_indexes()


# ─────────────────────────── filters ───────────────────────────
async def _auto_filter(_, __, message: Message) -> bool:
    user = message.from_user
    if not user or user.is_bot:
        return False
    uid = user.id
    if uid in _sequences:
        return True
    try:
        from filestore.main_bot.plugins.create_bot import _creation_state
        if uid in _creation_state:
            return False
    except Exception:
        pass
    if _get_state(uid):
        return False
    if not await _enabled():
        return False
    s = await store.get(uid)
    if not s.get("auto", True):
        return False
    return s.get("mode") == "manual" or bool(s.get("template"))


auto_filter = filters.create(_auto_filter)


_LINKISH = ("http://", "https://", "t.me/", "telegram.me/", "tg://")


def _prompt_for(message: Message):
    """The manual-mode prompt a text message answers: a reply to it, or the only one pending."""
    user = message.from_user
    box = _live_prompts(user.id) if user else {}
    text = (message.text or "").strip()
    if not box or not text or text.startswith("/"):
        return None
    reply = getattr(message, "reply_to_message", None)
    rid = getattr(reply, "id", None) if reply else None
    if rid is not None:
        for key, entry in box.items():
            if rid == key or rid == getattr(entry.get("prompt"), "id", None):
                return key
    if len(box) == 1 and "\n" not in text and not any(k in text.lower() for k in _LINKISH):
        return next(iter(box))
    return None


def _input_filter_fn(_, __, message: Message) -> bool:
    user = message.from_user
    if not user:
        return False
    if (message.text or "").startswith("/"):
        return False
    return bool(_get_state(user.id)) or user.id in verify._input or _prompt_for(message) is not None


input_filter = filters.create(_input_filter_fn)


# ─────────────────────────── views ───────────────────────────
def _media_label(pref) -> str:
    return {"document": "📄 Document", "video": "🎥 Video", "audio": "🎵 Audio"}.get(pref, "🤖 Auto")


def _mode_label(mode) -> str:
    return "✍️ Manual" if mode == "manual" else "🤖 Auto"


def preview_name(s: dict, name: str = SAMPLE) -> str:
    """What the user's current settings turn `name` into (template, clean tags, word rules, MKV)."""
    tpl = s.get("template")
    if tpl:
        return extract.new_filename(tpl, name, s.get("mkv", True), clean=s.get("clean", False),
                                    words=s.get("words"))
    return extract.prepare_source(name, s.get("clean", False), s.get("words"))


async def panel_view(uid: int):
    s = await store.get(uid)
    tpl = s.get("template")
    manual = s.get("mode") == "manual"
    if not tpl and not manual:
        status = "⚪ Not set up"
    elif not await _enabled():
        status = "⛔ Disabled by the admin"
    else:
        status = "🟢 Active" if s.get("auto", True) else "⏸ Paused"
    meta_on = s.get("meta_on", False)
    words = s.get("words") or []
    busy = engine.pending(uid)
    mode_line = "✍️ Manual – I ask for a name for every file" if manual else "🤖 Auto – renamed with your template"
    from core.style import hdr, quote, rows as srows, sc
    yes = lambda on: "✅" if on else "❌"   # noqa: E731
    info = [
        ("📶 Status", status),
        ("🎛 Mode", mode_line),
        ("🧩 Template", f"<code>{esc(tpl)}</code>" if tpl else "<i>not set – tap ✏️ Set template</i>"),
        ("📦 Send as", _media_label(s.get("media"))),
        ("🎞 MP4 → MKV", yes(s.get("mkv", True))),
        ("🏷 Metadata", yes(meta_on)),
        ("🧹 Clean tags", yes(s.get("clean"))),
        ("🔁 Word rules", len(words)),
        ("📊 Renamed so far", s.get("count", 0)),
    ]
    if busy:
        info.append(("⏳ Queue", f"{busy} file(s) in progress"))
    text = hdr("✏️", "Auto-Rename") + "\n\n" + quote(srows(info)) + "\n"
    if tpl:
        text += (f"\n<b>👁 {sc('Example')}</b>\n" + quote(f"<code>{esc(SAMPLE)}</code>\n➜ <code>{esc(preview_name(s))}</code>"))
    elif manual:
        text += "\n<i>" + sc("Send any file and I'll ask what to call it. Set a template to get a 💡 one-tap suggestion.") + "</i>"
    else:
        text += "\n<i>" + sc("Example") + ":</i> <code>/autorename {title} S{season}E{episode} [{quality}] [{audio}]</code>"
    pause = []
    if tpl or manual:
        pause.append(Btn("⏸ Pause" if s.get("auto", True) else "▶️ Resume", callback_data="rn:auto"))
    if tpl:
        pause.append(Btn("🗑 Delete template", callback_data="rn:del"))
    kb = [
        [Btn("✏️ Set template", callback_data="rn:tpl"), Btn("📖 Placeholders", callback_data="rn:help")],
        pause,
        [Btn(f"Mode: {_mode_label(s.get('mode'))}", callback_data="rn:mode"),
         Btn(f"🧹 Clean tags {'✅' if s.get('clean') else '❌'}", callback_data="rn:clean")],
        [Btn(f"🔁 Word rules ({len(words)})", callback_data="rn:words"), Btn("🕘 History", callback_data="rn:hist")],
        [Btn(f"📦 {_media_label(s.get('media'))}", callback_data="rn:media"),
         Btn(f"🎞 MP4→MKV {'✅' if s.get('mkv', True) else '❌'}", callback_data="rn:mkv")],
        [Btn("🏷 Metadata", callback_data="rn:meta"), Btn("🖼 Thumbnail & caption", callback_data="rn:thumb")],
        [Btn("📋 Sequence", callback_data="rn:seq"), Btn("🏆 Leaderboard", callback_data="rnlb:all")],
        [Btn(f"⏹ Cancel queue ({busy})", callback_data="rn:cq")] if busy else [],
        [Btn("❌ Close", callback_data="close_btn")],
    ]
    return text, InlineKeyboardMarkup([r for r in kb if r])


def template_prompt():
    presets = [Btn(label, callback_data=f"rn:pre:{key}") for key, (label, _t, _c) in store.PRESETS.items()]
    text = ("<b>✏️ Send your new template</b>\n\nExample:\n"
            "<code>{title} S{season}E{episode} [{quality}] [{audio}]</code>\n\n"
            "<b>Or pick a preset:</b>\n<blockquote>"
            + "\n".join(f"{label} – <code>{esc(t)}</code>" for label, t, _c in store.PRESETS.values())
            + "</blockquote>\n<i>/cancel to abort</i>")
    kb = [presets[:2], presets[2:], [Btn("📖 Placeholders", callback_data="rn:help"),
                                     Btn("‹ Back", callback_data="rn:home")]]
    return text, InlineKeyboardMarkup(kb)


async def words_view(uid: int):
    s = await store.get(uid)
    rules = s.get("words") or []
    lines = ["<b>🔁 Word rules</b>\n",
             "<i>Applied to the original name before renaming – remove spam words or fix spellings.</i>\n"]
    if rules:
        body = "\n".join(f"• <code>{esc(o)}</code> ➜ " + (f"<code>{esc(n)}</code>" if n else "<i>removed</i>")
                         for o, n in rules)
        lines.append(f"<blockquote expandable>{body}</blockquote>")
    else:
        lines.append("<i>No rules yet.</i>")
    lines.append(f"\n<b>👁 Example:</b>\n<code>{esc(SAMPLE)}</code>\n➜ <code>{esc(preview_name(s))}</code>")
    kb = [[Btn("✏️ Set rules", callback_data="rn:wset")] +
          ([Btn("🗑 Clear all", callback_data="rn:wclr")] if rules else []),
          [Btn("‹ Back", callback_data="rn:home")]]
    return "\n".join(lines), InlineKeyboardMarkup(kb)


WORDS_HELP = ("<b>🔁 Send your word rules</b> – one per line:\n\n"
              "<blockquote><code>HQ</code> – remove the word\n"
              "<code>[ESub] =&gt; ESubs</code> – replace\n"
              "<code>Tamil Dubbed | Tamil</code> – replace</blockquote>\n"
              f"<i>Up to {store.MAX_WORD_RULES} rules, not case-sensitive. The new list replaces the old one. "
              "/cancel to abort</i>")


async def history_view(uid: int):
    rows = await store.recent(uid, 10)
    lines = ["<b>🕘 Your last renames</b>\n"]
    if not rows:
        lines.append("<i>Nothing yet – renamed files will show up here.</i>")
    for r in rows:
        ts = store.aware(r.get("ts"))
        when = ts.strftime("%d %b · %H:%M UTC") if ts else ""
        lines.append(f"• <code>{esc(r.get('name'))}</code>\n   <i>{esc(when)} · from</i> "
                     f"<code>{esc((r.get('old') or '—')[:80])}</code>")
    return "\n".join(lines), InlineKeyboardMarkup([[Btn("‹ Back", callback_data="rn:home")]])


TUTORIAL = (
    "<b>📖 Auto-Rename – how to</b>\n\n"
    "1️⃣ Save a template:\n<code>/autorename {title} S{season}E{episode} [{quality}] [{audio}]</code>\n"
    "2️⃣ Send (or forward) your files – they come back renamed.\n\n"
    "<b>Placeholders</b> (read from the original file name):\n"
    "<blockquote>{title} – series / movie name\n{season} – 01, 02 …\n{episode} – 01, 02 … (01-03 for multi-episode files)\n"
    "{quality} – 480p · 720p · 1080p · 4K …\n{source} – WEB-DL · BluRay · HDRip …\n"
    "{audio} – Dual · Multi · Hindi · Jap · AAC …\n{year} – 2024\n{codec} – x264 · x265 · HEVC\n"
    "{group} – release group (SubsPlease, YTS …)\n{size} – file size\n{filename} – the original name</blockquote>\n"
    "<b>Movie-aware:</b> files without a season and episode drop the S··E·· part automatically.\n"
    "<b>Classic style also works:</b>\n<code>[SSeason] [EPEpisode] [Quality] [Audio] Your Channel</code>\n\n"
    "<b>Modes</b>\n<blockquote>🤖 Auto – every file is renamed with your template\n"
    "✍️ Manual – I ask for a name for each file (💡 suggestion · 📄 keep name)\n"
    "/rename New Name – reply to any file for a one-off rename</blockquote>\n"
    "<b>Extras</b>\n<blockquote>🧹 Clean tags – strips @channels, links &amp; site names\n"
    "🔁 Word rules – remove / replace words\n/setmedia – document · video · audio\n"
    "/metadata – title, author, audio / subtitle titles …\n"
    "/set_caption – {filename} {size} {duration} {title} {episode} {quality} …\n/set_thumb – reply to a photo\n"
    "/start_sequence → send files → /end_sequence – sorted episodes\n/testrename name.mkv – preview only\n"
    "/leaderboard – top renamers · /cancel – stop the queue</blockquote>"
)


async def meta_view(uid: int):
    s = await store.get(uid)
    meta = s.get("meta") or {}
    on = s.get("meta_on", False)
    lines = [f"<b>🏷 Metadata:</b> {'🟢 On' if on else '🔴 Off'}\n"]
    for f in store.META_FIELDS:
        lines.append(f"◈ <b>{store.META_LABELS[f]}:</b> <code>{esc(meta.get(f)) or '—'}</code>")
    lines.append("\n<i>Applied with ffmpeg stream copy (no quality loss). Tap a field to change it.</i>")
    fields = [Btn(f"✏️ {store.META_LABELS[f]}", callback_data=f"rn:mf:{f}") for f in store.META_FIELDS]
    kb = [[Btn("🟢 On" + (" ✅" if on else ""), callback_data="rn:mon:1"),
           Btn("🔴 Off" + ("" if on else " ✅"), callback_data="rn:mon:0")]]
    kb += [fields[i:i + 2] for i in range(0, len(fields), 2)]
    kb.append([Btn("🧹 Clear all", callback_data="rn:mclr"), Btn("ℹ️ How it works", callback_data="rn:minfo")])
    kb.append([Btn("‹ Back", callback_data="rn:home")])
    return "\n".join(lines), InlineKeyboardMarkup(kb)


META_INFO = (
    "<b>🏷 Managing metadata</b>\n\n"
    "<blockquote>• <b>Title</b> – title of the media\n• <b>Author / Artist</b> – creator tags\n"
    "• <b>Audio / Subtitle / Video</b> – titles of every audio, subtitle and video track\n"
    "• <b>Encoded by / Custom tag</b> – extra tags</blockquote>\n"
    "<b>Commands</b>\n<code>/settitle</code> · <code>/setauthor</code> · <code>/setartist</code> · <code>/setaudio</code> · "
    "<code>/setsubtitle</code> · <code>/setvideo</code> · <code>/setencoded_by</code> · <code>/setcustom_tag</code>\n\n"
    "<i>Example:</i> <code>/settitle My Channel Release</code>\n"
    "Send the command alone to clear that field. /metadata turns everything on or off."
)


async def thumb_view(uid: int):
    thumb = caption = None
    try:
        from database.db import db as saver_db
        thumb = await saver_db.get_thumbnail(uid)
        caption = await saver_db.get_caption(uid)
    except Exception:
        pass
    text = ("<b>🖼 Thumbnail &amp; caption</b>\n\n"
            f"<b>Thumbnail:</b> {'🟢 custom' if thumb else '⚪ the file’s own'}\n"
            f"<b>Caption:</b> <code>{esc(caption) if caption else 'default (new file name)'}</code>\n\n"
            "<i>Caption placeholders: {filename} {size} {duration} {title} {season} {episode} {quality} "
            "{audio} {year} {source} {original}\nNo thumbnail? Videos get a frame from the file automatically.\n"
            "Set a caption with</i> <code>/set_caption 📁 {filename} | 💾 {filesize}</code>")
    kb = [[Btn("🖼 Set thumbnail", callback_data="rn:tset")]]
    if thumb:
        kb.append([Btn("👁 View", callback_data="rn:tview"), Btn("🗑 Delete", callback_data="rn:tdel")])
    if caption:
        kb.append([Btn("🗑 Delete caption", callback_data="rn:cdel")])
    kb.append([Btn("‹ Back", callback_data="rn:home")])
    return text, InlineKeyboardMarkup(kb)


def media_kb(current) -> InlineKeyboardMarkup:
    def b(label, key):
        return Btn(label + (" ✅" if current == key or (key == "auto" and current not in store.MEDIA_TYPES) else ""),
                   callback_data=f"rn:mt:{key}")
    return InlineKeyboardMarkup([[b("📄 Document", "document"), b("🎥 Video", "video")],
                                 [b("🎵 Audio", "audio"), b("🤖 Auto", "auto")],
                                 [Btn("‹ Back", callback_data="rn:home")]])


SEQ_HELP = ("<b>📋 Sequence mode</b>\n\nSend a whole season in any order and get it back sorted "
            "by season → episode → quality.\n\n<blockquote>/start_sequence – start collecting\n"
            "…send or forward your files…\n/end_sequence – rename &amp; send them in order\n"
            "/cancel – drop the sequence</blockquote>")


async def leaderboard_doc(period: str, uid: int):
    """Top-10 renamers as a ranked table (rich) + period keyboard."""
    from core.botlog import now as local_now
    from core.rich import Doc
    period = period if period in store.PERIODS else "all"
    rows, rank, mine = await store.leaderboard(period, uid)
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    doc = Doc("🏆", f"{store.PERIODS[period]} top renamers")
    if rows:
        doc.table([(medals.get(i, f"{i}."), (name or "Anonymous")[:32], f"@{username}" if username else "—", n)
                   for i, (u, n, name, username) in enumerate(rows, 1)],
                  header=("#", "Name", "Username", "Renames"), align=("center", "left", "left", "right"))
    else:
        doc.text("<i>No renames in this period yet.</i>")
    if rank:
        doc.table([("📍 Your rank", f"#{rank}"), ("✏️ Your renames", mine)])
    doc.footer(f"Updated {local_now().strftime('%d %b %Y · %I:%M %p')}")
    return doc, _leaderboard_kb(period)


def _leaderboard_kb(period: str):
    btns = [Btn(("• " if p == period else "") + label, callback_data=f"rnlb:{p}")
            for p, label in (("today", "Today"), ("week", "Week"), ("month", "Month"), ("year", "Year"),
                             ("all", "All-time"))]
    return InlineKeyboardMarkup([btns[:3], btns[3:]])


async def leaderboard_view(period: str, uid: int):
    period = period if period in store.PERIODS else "all"
    rows, rank, mine = await store.leaderboard(period, uid)
    lines = [f"<b>🏆 {store.PERIODS[period]} top 10 renamers</b>\n"]
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    for i, (u, n, name, username) in enumerate(rows, 1):
        who = esc(name or "Anonymous")[:32]
        tag = f" (@{esc(username)})" if username else ""
        lines.append(f"{medals.get(i, f'{i}.')} <b>{who}</b>{tag} ➜ <i>{n} renames</i>")
    if not rows:
        lines.append("<i>No renames in this period yet.</i>")
    if rank:
        lines.append(f"\n<b>Your rank:</b> #{rank} with {mine} renames")
    from core.botlog import now as local_now
    lines.append(f"\n<i>Updated {local_now().strftime('%d %b %Y · %I:%M %p')}</i>")
    return "\n".join(lines), _leaderboard_kb(period)


async def admin_view():
    enabled = await _enabled()
    nsfw = await vdb.get_setting("rn_nsfw", True)
    dump = await engine.dump_chat()
    cfg = await store.verify_settings()
    active = len(store.active_shorteners(cfg))
    text = ("<b>⚙️ Auto-Rename – admin</b>\n\n"
            f"<b>Auto-Rename:</b> {'🟢 enabled' if enabled else '🔴 disabled'}\n"
            f"<b>Anti-NSFW filter:</b> {'🟢 on' if nsfw else '🔴 off'}\n"
            f"<b>Dump channel:</b> {f'<code>{dump}</code>' if dump else 'off'}\n"
            f"<b>Verification:</b> {f'🟢 {active} shortener(s)' if active else '🔴 off'}\n"
            f"<b>Concurrency:</b> {engine.CONCURRENCY} jobs · queue limit {engine.QUEUE_LIMIT}/user\n"
            f"<b>Total renames:</b> {await store.total_renames()}")
    kb = [[Btn(("🟢" if enabled else "🔴") + " Auto-Rename", callback_data="rna:en"),
           Btn(("🟢" if nsfw else "🔴") + " Anti-NSFW", callback_data="rna:nsfw")],
          [Btn("📤 Set dump channel", callback_data="rna:dump")] +
          ([Btn("🗑 Clear dump", callback_data="rna:undump")] if dump else []),
          [Btn("🔐 Verification", callback_data="rnv:home"), Btn("❌ Close", callback_data="close_btn")]]
    return text, InlineKeyboardMarkup(kb)


# ─────────────────────────── commands ───────────────────────────
@Client.on_message(filters.command(["autorename", "auto_rename", "showformat", "format"]) & filters.private)
async def autorename_cmd(client: Client, message: Message):
    await _ensure_indexes()
    uid = message.from_user.id
    parts = (message.text or "").split(None, 1)
    if message.command[0].lower() in ("autorename", "auto_rename") and len(parts) > 1 and parts[1].strip():
        template = parts[1].strip()[:200]
        await store.set_template(uid, template)
        preview = preview_name(await store.get(uid))
        return await message.reply_text(
            "🌟 <b>Template saved – you're ready to auto-rename!</b>\n\n"
            f"<b>Template:</b> <code>{esc(template)}</code>\n\n<b>👁 Example:</b>\n<code>{esc(SAMPLE)}</code>\n"
            f"➜ <code>{esc(preview)}</code>\n\n📩 Now send or forward the files you want to rename.",
            reply_markup=InlineKeyboardMarkup([[Btn("⚙️ Rename settings", callback_data="rn:home")]]))
    text, kb = await panel_view(uid)
    await message.reply_text(text, reply_markup=kb)


@Client.on_message(filters.command(["setmedia", "set_media"]) & filters.private)
async def setmedia_cmd(client: Client, message: Message):
    s = await store.get(message.from_user.id)
    await message.reply_text("<b>📦 How should renamed files be sent?</b>\n\n"
                             "<i>🤖 Auto = audio files as audio, everything else as a document.</i>",
                             reply_markup=media_kb(s.get("media")))


@Client.on_message(filters.command("metadata") & filters.private)
async def metadata_cmd(client: Client, message: Message):
    text, kb = await meta_view(message.from_user.id)
    await message.reply_text(text, reply_markup=kb)


_META_CMDS = {"settitle": "title", "setauthor": "author", "setartist": "artist", "setaudio": "audio",
              "setsubtitle": "subtitle", "setvideo": "video", "setencoded_by": "encoded_by",
              "setencodedby": "encoded_by", "setcustom_tag": "custom_tag", "setcustomtag": "custom_tag"}


@Client.on_message(filters.command(list(_META_CMDS)) & filters.private)
async def set_meta_cmd(client: Client, message: Message):
    field = _META_CMDS[message.command[0].lower()]
    parts = (message.text or "").split(None, 1)
    value = parts[1].strip()[:120] if len(parts) > 1 else ""
    await store.set_meta(message.from_user.id, field, value)
    label = store.META_LABELS[field]
    if value:
        s = await store.get(message.from_user.id)
        hint = "" if s.get("meta_on") else "\n<i>Metadata is off – turn it on in /metadata.</i>"
        await message.reply_text(f"✅ <b>{label}</b> saved: <code>{esc(value)}</code>{hint}")
    else:
        await message.reply_text(f"🗑 <b>{label}</b> cleared.\n<i>Usage:</i> <code>/{message.command[0]} your text</code>")


@Client.on_message(filters.command(["start_sequence", "sequence", "startsequence"]) & filters.private)
async def start_sequence_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if uid in _sequences:
        return await message.reply_text(f"📋 A sequence is already running ({len(_sequences[uid])} files). "
                                        "Send more files or use /end_sequence.")
    _sequences[uid] = []
    _seq_notes[uid] = []
    s = await store.get(uid)
    extra = "" if s.get("template") else "\n<i>No template set – files will be sent back sorted, without renaming.</i>"
    await message.reply_text("📋 <b>Sequence started!</b> Send your files now, then use /end_sequence." + extra)


@Client.on_message(filters.command(["end_sequence", "endsequence"]) & filters.private)
async def end_sequence_cmd(client: Client, message: Message):
    uid = message.from_user.id
    files = _sequences.pop(uid, None)
    notes = _seq_notes.pop(uid, [])
    if files is None:
        return await message.reply_text("ℹ️ No sequence running. Start one with /start_sequence.")
    if not files:
        return await message.reply_text("📭 The sequence was empty.")
    files.sort(key=lambda m: extract.sort_key(engine.original_name(m)))
    if notes:
        try:
            await client.delete_messages(message.chat.id, notes)
        except Exception:
            pass
    s = await store.get(uid)
    if s.get("template") and await _enabled():
        if not await verify.gate(client, message):
            return
        queued = 0
        for m in files:
            if await engine.submit(client, m):
                queued += 1
        skipped = len(files) - queued
        return await message.reply_text(
            f"✅ <b>{queued} file(s) queued in order</b> (season → episode → quality)."
            + (f"\n⚠️ {skipped} skipped – queue limit {engine.QUEUE_LIMIT}." if skipped else ""))
    status = await message.reply_text(f"📤 Sending {len(files)} files in order…")
    for m in files:
        try:
            await m.copy(message.chat.id)
            await asyncio.sleep(0.4)
        except Exception as e:
            log.debug(f"sequence copy failed: {e}")
    await status.edit_text(f"✅ All {len(files)} files sent in sequence!")


@Client.on_message(filters.command(["testrename", "rnpreview", "previewrename"]) & filters.private)
async def testrename_cmd(client: Client, message: Message):
    uid = message.from_user.id
    s = await store.get(uid)
    tpl = s.get("template")
    parts = (message.text or "").split(None, 1)
    name = parts[1].strip() if len(parts) > 1 else engine.original_name(message.reply_to_message) \
        if message.reply_to_message else ""
    if not tpl:
        return await message.reply_text("⚠️ Set a template first: <code>/autorename {title} S{season}E{episode}</code>")
    if not name:
        return await message.reply_text("<b>Usage:</b> <code>/testrename Some.Show.S01E02.1080p.mkv</code>\n"
                                        "<i>or reply to a file with /testrename</i>")
    src = extract.prepare_source(name, s.get("clean", False), s.get("words"))
    info = extract.parse(src)
    new = preview_name(s, name)
    from core import rich
    from core.rich import Doc, code
    labels = {"episode_end": "last episode"}
    doc = Doc("👁", "Rename preview")
    doc.table([("📄 Original", code(name)), *([("🧹 Cleaned", code(src))] if src != name else []),
               ("✏️ Renamed", code(new))], header=("File", "Name"))
    detected = [(labels.get(k, k).replace("_", " ").capitalize(), code(v)) for k, v in info.items()
                if v not in (None, "")]
    doc.h("🔎", "Detected")
    if detected:
        doc.table(detected, header=("Field", "Value"), compact=True)
    else:
        doc.text("<i>Nothing detected – only {filename} placeholders will change.</i>")
    doc.footer("Preview only – nothing was uploaded.")
    await rich.reply(message, doc)


@Client.on_message(filters.command("tutorial") & filters.private)
async def tutorial_cmd(client: Client, message: Message):
    await message.reply_text(TUTORIAL, reply_markup=InlineKeyboardMarkup(
        [[Btn("⚙️ Rename settings", callback_data="rn:home")]]), disable_web_page_preview=True)


@Client.on_message(filters.command(["leaderboard", "top"]))
async def leaderboard_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
    if message.chat.type == enums.ChatType.PRIVATE and not LEADERBOARD_PIC:
        from core import rich
        doc, kb = await leaderboard_doc("all", uid)
        return await rich.reply(message, doc, reply_markup=kb)
    text, kb = await leaderboard_view("all", uid)
    if LEADERBOARD_PIC:
        try:
            sent = await message.reply_photo(LEADERBOARD_PIC, caption=text, reply_markup=kb)
        except Exception:
            sent = await message.reply_text(text, reply_markup=kb)
    else:
        sent = await message.reply_text(text, reply_markup=kb)
    if message.chat.type != enums.ChatType.PRIVATE and LEADERBOARD_DELETE_TIMER > 0:
        asyncio.create_task(_delete_later([sent, message], LEADERBOARD_DELETE_TIMER))


async def _delete_later(msgs, delay: int):
    await asyncio.sleep(delay)
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass


@Client.on_message(filters.command(["renameset", "rename_settings", "renamesettings"]) & filters.user(config.ADMINS))
async def renameset_cmd(client: Client, message: Message):
    text, kb = await admin_view()
    await message.reply_text(text, reply_markup=kb)


# ─────────────────────────── incoming files ───────────────────────────
@Client.on_message(filters.private & filters.incoming & (filters.document | filters.video | filters.audio)
                   & auto_filter)
async def incoming_file(client: Client, message: Message):
    await _ensure_indexes()
    uid = message.from_user.id
    kind, media = engine.media_of(message)
    if not media or engine.is_duplicate(media.file_unique_id):
        return
    if uid in _sequences:
        _sequences[uid].append(message)
        n = len(_sequences[uid])
        note = await message.reply_text(f"📥 Added to sequence (<b>{n}</b>). Send more or /end_sequence.", quote=True)
        _seq_notes.setdefault(uid, []).append(note.id)
        return
    if not await verify.gate(client, message):
        return
    s = await store.get(uid)
    if s.get("mode") == "manual":
        return await ask_name(message, s)
    await _queue(client, message)


async def _queue(client, message: Message, name: str = "", send_as: str = "", user=None):
    extra = {k: v for k, v in (("name", name), ("send_as", send_as), ("user", user)) if v}
    pos = await engine.submit(client, message, **extra)
    if not pos:
        return await message.reply_text(f"⏳ Your queue is full ({engine.QUEUE_LIMIT} files). "
                                        "Wait for it to finish or /cancel.", quote=True)
    if pos > 1:
        await message.reply_text(f"🕒 Queued – position <b>#{pos}</b>.", quote=True)
    return pos


# ─────────────────────────── manual mode ───────────────────────────
def _prompt_kb(pid: int, entry: dict) -> InlineKeyboardMarkup:
    cur = entry.get("send_as") or ""
    types = [Btn(label + (" ✅" if cur == key else ""), callback_data=f"rnm:t:{pid}:{key}")
             for key, label in (("document", "📄 Doc"), ("video", "🎥 Video"), ("audio", "🎵 Audio"))]
    row = [Btn("💡 Use suggestion", callback_data=f"rnm:use:{pid}")] if entry.get("sugg") else []
    row.append(Btn("↩️ Keep name", callback_data=f"rnm:keep:{pid}"))
    return InlineKeyboardMarkup([row, types, [Btn("❌ Skip", callback_data=f"rnm:x:{pid}")]])


def _prompt_text(entry: dict) -> str:
    old = engine.original_name(entry["msg"])
    text = f"✍️ <b>Send the new name for:</b>\n<code>{esc(old)}</code>\n"
    if entry.get("sugg"):
        text += f"\n💡 <b>Suggestion:</b>\n<code>{esc(entry['sugg'])}</code>\n"
    send_as = entry.get("send_as")
    text += (f"\n<b>Send as:</b> {_media_label(send_as) if send_as else 'your default'}\n"
             "<i>Reply to this message with the name – the extension is kept automatically.</i>")
    return text


async def ask_name(message: Message, s: dict):
    uid = message.from_user.id
    box = _live_prompts(uid)
    if len(box) >= MAX_PROMPTS:
        return await message.reply_text(f"⏳ {MAX_PROMPTS} files are already waiting for a name – answer those first "
                                        "or /cancel.", quote=True)
    old = engine.original_name(message)
    sugg = preview_name(s, old) if (s.get("template") or s.get("clean") or s.get("words")) else ""
    if sugg == old:
        sugg = ""
    entry = {"msg": message, "ts": time.time(), "send_as": "", "sugg": sugg}
    _prompts.setdefault(uid, {})[message.id] = entry
    entry["prompt"] = await message.reply_text(_prompt_text(entry), quote=True,
                                               reply_markup=_prompt_kb(message.id, entry))


async def _finish_prompt(client, uid: int, pid: int, name: str, user=None):
    entry = _live_prompts(uid).pop(pid, None)
    if not entry:
        return None
    if not _prompts.get(uid):
        _prompts.pop(uid, None)
    prompt = entry.get("prompt")
    if prompt:
        try:
            await prompt.delete()
        except Exception:
            pass
    return await _queue(client, entry["msg"], name=name, send_as=entry.get("send_as", ""), user=user)


# ─────────────────────────── text / photo input (group -1) ───────────────────────────
@Client.on_message(filters.private & filters.incoming & input_filter, group=-1)
async def rename_input(client: Client, message: Message):
    uid = message.from_user.id
    if uid in verify._input:
        if await verify.handle_input(client, message, verify._input[uid]):
            raise StopPropagation
        return
    st = _get_state(uid)
    if not st:
        pid = _prompt_for(message)
        if pid is None:
            return
        await _finish_prompt(client, uid, pid, (message.text or "").strip()[:200], user=message.from_user)
        raise StopPropagation
    kind = st["kind"]
    text = (message.text or "").strip()
    if kind == "thumb":
        if not message.photo:
            await message.reply_text("🖼 Please send a <b>photo</b> (or /cancel).")
            raise StopPropagation
        from database.db import db as saver_db
        if not await saver_db.is_user_exist(uid):
            await saver_db.add_user(uid, message.from_user.first_name)
        await saver_db.set_thumbnail(uid, message.photo.file_id)
        _state.pop(uid, None)
        view, kb = await thumb_view(uid)
        await message.reply_text("✅ <b>Thumbnail saved.</b>\n\n" + view, reply_markup=kb)
        raise StopPropagation
    if not text:
        await message.reply_text("✏️ Please send text (or /cancel).")
        raise StopPropagation
    if kind == "template":
        _state.pop(uid, None)
        await store.set_template(uid, text[:200])
        view, kb = await panel_view(uid)
        await message.reply_text("✅ <b>Template saved.</b>\n\n" + view, reply_markup=kb)
    elif kind == "meta":
        _state.pop(uid, None)
        await store.set_meta(uid, st["field"], text[:120])
        view, kb = await meta_view(uid)
        await message.reply_text(f"✅ <b>{store.META_LABELS[st['field']]}</b> saved.\n\n" + view, reply_markup=kb)
    elif kind == "words":
        _state.pop(uid, None)
        rules = extract.parse_word_rules(text, store.MAX_WORD_RULES)
        await store.update(uid, words=rules)
        view, kb = await words_view(uid)
        await message.reply_text(f"✅ <b>{len(rules)} word rule(s) saved.</b>\n\n" + view, reply_markup=kb)
    elif kind == "dump":
        await _set_dump(client, message, text)
    raise StopPropagation


async def _set_dump(client, message: Message, text: str):
    uid = message.from_user.id
    chat = None
    if message.forward_from_chat:
        chat = message.forward_from_chat.id
    elif text.lstrip("-").isdigit():
        chat = int(text)
    elif text.startswith("@"):
        chat = text
    if chat is None:
        await message.reply_text("❌ Send the channel ID (<code>-100…</code>), its @username, or forward a post from it.")
        return
    try:
        probe = await client.send_message(chat, "✅ Auto-Rename dump channel connected.")
        chat_id = probe.chat.id
        try:
            await probe.delete()
        except Exception:
            pass
    except Exception as e:
        await message.reply_text(f"❌ I can't post there – make me an admin first.\n<code>{esc(e)}</code>")
        return
    _state.pop(uid, None)
    await vdb.set_setting("rn_dump", chat_id)
    view, kb = await admin_view()
    await message.reply_text(f"✅ Dump channel set to <code>{chat_id}</code>.\n\n" + view, reply_markup=kb)


# ─────────────────────────── callbacks ───────────────────────────
@Client.on_callback_query(filters.regex(r"^rn:"))
async def rename_cb(client: Client, query: CallbackQuery):
    uid = query.from_user.id
    parts = query.data.split(":")
    action = parts[1]
    msg = query.message

    async def show(view):
        text, kb = view
        await smart_edit(msg, text, kb)

    if action == "home":
        await query.answer()
        return await show(await panel_view(uid))
    if action == "help":
        await query.answer()
        return await show((TUTORIAL, InlineKeyboardMarkup([[Btn("‹ Back", callback_data="rn:home")]])))
    if action == "tpl":
        _state[uid] = {"kind": "template", "ts": time.time()}
        await query.answer()
        return await show(template_prompt())
    if action == "pre" and len(parts) > 2 and parts[2] in store.PRESETS:
        label, tpl, clean = store.PRESETS[parts[2]]
        _state.pop(uid, None)
        await store.set_template(uid, tpl)
        if clean:
            await store.update(uid, clean=True)
        await query.answer(f"✅ {label} template saved")
        return await show(await panel_view(uid))
    if action == "mode":
        s = await store.get(uid)
        new = "auto" if s.get("mode") == "manual" else "manual"
        await store.update(uid, mode=new, auto=True)
        await query.answer("✍️ Manual: I'll ask for a name for every file" if new == "manual"
                           else ("🤖 Auto: files use your template" if s.get("template")
                                 else "🤖 Auto – set a template to start"), show_alert=new == "manual")
        return await show(await panel_view(uid))
    if action == "clean":
        s = await store.get(uid)
        await store.update(uid, clean=not s.get("clean", False))
        await query.answer("🧹 Clean tags " + ("off" if s.get("clean") else "on"))
        return await show(await panel_view(uid))
    if action == "words":
        await query.answer()
        return await show(await words_view(uid))
    if action == "wset":
        _state[uid] = {"kind": "words", "ts": time.time()}
        await query.answer()
        return await show((WORDS_HELP, InlineKeyboardMarkup([[Btn("‹ Back", callback_data="rn:words")]])))
    if action == "wclr":
        await store.unset(uid, "words")
        await query.answer("🗑 Word rules cleared")
        return await show(await words_view(uid))
    if action == "hist":
        await query.answer()
        return await show(await history_view(uid))
    if action == "cq":
        n = engine.cancel_user(uid)
        await query.answer(f"⏹ Cancelled {n} job(s)" if n else "Nothing to cancel.")
        return await show(await panel_view(uid))
    if action == "auto":
        s = await store.get(uid)
        await store.update(uid, auto=not s.get("auto", True))
        await query.answer("⏸ Paused" if s.get("auto", True) else "▶️ Resumed")
        return await show(await panel_view(uid))
    if action == "del":
        await store.unset(uid, "template")
        await query.answer("🗑 Template deleted")
        return await show(await panel_view(uid))
    if action == "mkv":
        s = await store.get(uid)
        await store.update(uid, mkv=not s.get("mkv", True))
        await query.answer("MP4 → MKV " + ("off" if s.get("mkv", True) else "on"))
        return await show(await panel_view(uid))
    if action == "media":
        s = await store.get(uid)
        await query.answer()
        return await show(("<b>📦 How should renamed files be sent?</b>\n\n"
                           "<i>🤖 Auto = audio files as audio, everything else as a document.</i>",
                           media_kb(s.get("media"))))
    if action == "mt":
        choice = parts[2]
        if choice in store.MEDIA_TYPES:
            await store.update(uid, media=choice)
        else:
            await store.unset(uid, "media")
        await query.answer(f"Send as: {_media_label(choice)}")
        return await show(await panel_view(uid))
    if action == "meta":
        await query.answer()
        return await show(await meta_view(uid))
    if action == "mon":
        await store.update(uid, meta_on=parts[2] == "1")
        await query.answer("Metadata " + ("on" if parts[2] == "1" else "off"))
        return await show(await meta_view(uid))
    if action == "mf" and parts[2] in store.META_FIELDS:
        _state[uid] = {"kind": "meta", "field": parts[2], "ts": time.time()}
        await query.answer()
        return await show((f"<b>✏️ Send the new {store.META_LABELS[parts[2]]}</b>\n\n<i>/cancel to abort</i>",
                           InlineKeyboardMarkup([[Btn("‹ Back", callback_data="rn:meta")]])))
    if action == "mclr":
        await store.unset(uid, "meta")
        await query.answer("🧹 Metadata cleared")
        return await show(await meta_view(uid))
    if action == "minfo":
        await query.answer()
        return await show((META_INFO, InlineKeyboardMarkup([[Btn("‹ Back", callback_data="rn:meta")]])))
    if action == "thumb":
        await query.answer()
        return await show(await thumb_view(uid))
    if action == "tset":
        _state[uid] = {"kind": "thumb", "ts": time.time()}
        await query.answer()
        return await show(("<b>🖼 Send me a photo</b> to use as your thumbnail.\n<i>/cancel to abort</i>",
                           InlineKeyboardMarkup([[Btn("‹ Back", callback_data="rn:thumb")]])))
    if action in ("tdel", "cdel", "tview"):
        from database.db import db as saver_db
        if action == "tview":
            thumb = await saver_db.get_thumbnail(uid)
            await query.answer()
            if thumb:
                try:
                    await client.send_photo(uid, thumb, caption="🖼 Your thumbnail")
                except Exception:
                    await query.answer("Couldn't load the thumbnail.", show_alert=True)
            return
        if action == "tdel":
            await saver_db.del_thumbnail(uid)
            await query.answer("🗑 Thumbnail deleted")
        else:
            await saver_db.del_caption(uid)
            await query.answer("🗑 Caption deleted")
        return await show(await thumb_view(uid))
    if action == "seq":
        await query.answer()
        return await show((SEQ_HELP, InlineKeyboardMarkup([[Btn("‹ Back", callback_data="rn:home")]])))
    await query.answer()


@Client.on_callback_query(filters.regex(r"^rnm:"))
async def manual_prompt_cb(client: Client, query: CallbackQuery):
    uid = query.from_user.id
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    try:
        pid = int(parts[2])
    except (IndexError, ValueError):
        return await query.answer()
    entry = _live_prompts(uid).get(pid)
    if not entry:
        await query.answer("This prompt expired – send the file again.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    if action == "t" and len(parts) > 3 and parts[3] in store.MEDIA_TYPES:
        entry["send_as"] = "" if entry.get("send_as") == parts[3] else parts[3]
        entry["ts"] = time.time()
        await query.answer(f"Send as: {_media_label(entry['send_as'])}" if entry["send_as"] else "Send as: default")
        return await smart_edit(query.message, _prompt_text(entry), _prompt_kb(pid, entry))
    if action == "x":
        _live_prompts(uid).pop(pid, None)
        await query.answer("Skipped")
        try:
            await query.message.delete()
        except Exception:
            pass
        return
    if action in ("use", "keep"):
        name = entry.get("sugg") if action == "use" else engine.original_name(entry["msg"])
        await query.answer("✅ Renaming…" if action == "use" else "✅ Keeping the name…")
        await _finish_prompt(client, uid, pid, name or engine.original_name(entry["msg"]), user=query.from_user)
        return
    await query.answer()


@Client.on_callback_query(filters.regex(r"^help_rename$"))
async def help_rename_cb(client: Client, query: CallbackQuery):
    await query.answer()
    await smart_edit(query.message, TUTORIAL, InlineKeyboardMarkup([
        [Btn("⚙️ Rename settings", callback_data="rn:home"), Btn("🏆 Leaderboard", callback_data="rnlb:all")],
        [Btn("⬅️ Back", callback_data="help_btn")]]))


@Client.on_callback_query(filters.regex(r"^rnlb:(\w+)$"))
async def leaderboard_cb(client: Client, query: CallbackQuery):
    period = query.data.split(":", 1)[1]
    await query.answer()
    msg = query.message
    if msg.chat.type == enums.ChatType.PRIVATE and not getattr(msg, "photo", None):
        from core import rich
        doc, kb = await leaderboard_doc(period, query.from_user.id)
        return await rich.edit(msg, doc, kb)
    text, kb = await leaderboard_view(period, query.from_user.id)
    await smart_edit(msg, text, kb)


@Client.on_callback_query(filters.regex(r"^rnx:(\d+)$"))
async def cancel_job_cb(client: Client, query: CallbackQuery):
    if engine.cancel_job(int(query.matches[0].group(1)), query.from_user.id):
        await query.answer("⏹ Cancelling…")
    else:
        await query.answer("This job already finished.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^rna:"))
async def rename_admin_cb(client: Client, query: CallbackQuery):
    if query.from_user.id not in config.ADMINS:
        return await query.answer("👮 Admins only.", show_alert=True)
    action = query.data.split(":")[1]
    if action == "en":
        await vdb.set_setting("rn_enabled", not await _enabled())
    elif action == "nsfw":
        await vdb.set_setting("rn_nsfw", not await vdb.get_setting("rn_nsfw", True))
    elif action == "undump":
        await vdb.set_setting("rn_dump", 0)
    elif action == "dump":
        _state[query.from_user.id] = {"kind": "dump", "ts": time.time()}
        await query.answer()
        return await smart_edit(query.message, "<b>📤 Dump channel</b>\n\nSend the channel ID (<code>-100…</code>) or "
                                "@username, or forward any post from it.\n"
                                "<i>I must be an admin there. /cancel to abort.</i>")
    await query.answer("✅ Updated")
    text, kb = await admin_view()
    await smart_edit(query.message, text, kb)

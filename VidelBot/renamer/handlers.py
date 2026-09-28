"""
✏️ Auto-Rename (full feature set of the Auto-Rename bot, rebuilt for Videl):

  /autorename <template>   save a template → every file you send is renamed automatically
  /autorename              settings panel (pause · media type · MP4→MKV · metadata · thumbnail …)
  /setmedia                send as document / video / audio / auto
  /metadata  /settitle …   ffmpeg metadata (title, author, artist, audio, subtitle, video, encoded_by, custom_tag)
  /start_sequence  /end_sequence   collect files → sent back sorted by season / episode / quality
  /testrename <name>       preview the result without uploading
  /leaderboard             top renamers (today · week · month · year · all-time)
  /tutorial                placeholders & examples
  /renameset (admins)      global switch · anti-NSFW · dump channel · verification

Captions and thumbnails are shared with the saver (/set_caption {filename} {filesize} {duration}, /set_thumb).
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


def clear_user(uid: int) -> list:
    """Used by /cancel – returns what was cancelled."""
    done = []
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
    return bool(s.get("template")) and s.get("auto", True)


auto_filter = filters.create(_auto_filter)


def _input_filter_fn(_, __, message: Message) -> bool:
    user = message.from_user
    if not user:
        return False
    if (message.text or "").startswith("/"):
        return False
    return bool(_get_state(user.id)) or user.id in verify._input


input_filter = filters.create(_input_filter_fn)


# ─────────────────────────── views ───────────────────────────
def _media_label(pref) -> str:
    return {"document": "📄 Document", "video": "🎥 Video", "audio": "🎵 Audio"}.get(pref, "🤖 Auto")


async def panel_view(uid: int):
    s = await store.get(uid)
    tpl = s.get("template")
    if not tpl:
        status = "⚪ Not set up"
    elif not await _enabled():
        status = "⛔ Disabled by the admin"
    else:
        status = "🟢 Active" if s.get("auto", True) else "⏸ Paused"
    meta_on = s.get("meta_on", False)
    text = (
        "<b>✏️ Auto-Rename</b>\n\n"
        f"<b>Status:</b> {status}\n"
        f"<b>Template:</b> <code>{esc(tpl) if tpl else 'not set – tap ✏️ Set template'}</code>\n"
        f"<b>Send as:</b> {_media_label(s.get('media'))}\n"
        f"<b>MP4 → MKV:</b> {'✅' if s.get('mkv', True) else '❌'} · <b>Metadata:</b> {'✅' if meta_on else '❌'}\n"
        f"<b>Renamed so far:</b> {s.get('count', 0)}\n"
    )
    if tpl:
        text += f"\n<b>👁 Example:</b>\n<code>{esc(SAMPLE)}</code>\n➜ <code>{esc(extract.new_filename(tpl, SAMPLE, s.get('mkv', True)))}</code>"
    else:
        text += "\n<i>Example:</i> <code>/autorename {title} S{season}E{episode} [{quality}] [{audio}]</code>"
    pause = [Btn("⏸ Pause" if s.get("auto", True) else "▶️ Resume", callback_data="rn:auto"),
             Btn("🗑 Delete template", callback_data="rn:del")] if tpl else []
    kb = [
        [Btn("✏️ Set template", callback_data="rn:tpl"), Btn("📖 Placeholders", callback_data="rn:help")],
        pause,
        [Btn(f"📦 {_media_label(s.get('media'))}", callback_data="rn:media"),
         Btn(f"🎞 MP4→MKV {'✅' if s.get('mkv', True) else '❌'}", callback_data="rn:mkv")],
        [Btn("🏷 Metadata", callback_data="rn:meta"), Btn("🖼 Thumbnail & caption", callback_data="rn:thumb")],
        [Btn("📋 Sequence", callback_data="rn:seq"), Btn("🏆 Leaderboard", callback_data="rnlb:all")],
        [Btn("❌ Close", callback_data="close_btn")],
    ]
    return text, InlineKeyboardMarkup([r for r in kb if r])


TUTORIAL = (
    "<b>📖 Auto-Rename – how to</b>\n\n"
    "1️⃣ Save a template:\n<code>/autorename {title} S{season}E{episode} [{quality}] [{audio}]</code>\n"
    "2️⃣ Send (or forward) your files – they come back renamed.\n\n"
    "<b>Placeholders</b> (read from the original file name):\n"
    "<blockquote>{title} – series / movie name\n{season} – 01, 02 …\n{episode} – 01, 02 …\n"
    "{quality} – 480p · 720p · 1080p · 4K · WEB-DL …\n{audio} – Dual · Multi · Hindi · Jap · AAC …\n"
    "{year} – 2024\n{codec} – x264 · x265 · HEVC\n{filename} – the original name</blockquote>\n"
    "<b>Classic style also works:</b>\n<code>[SSeason] [EPEpisode] [Quality] [Audio] Your Channel</code>\n\n"
    "<b>Extras</b>\n<blockquote>/setmedia – document · video · audio\n/metadata – title, author, audio / subtitle titles …\n"
    "/set_caption – {filename} {filesize} {duration}\n/set_thumb – reply to a photo\n"
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
            "<i>Caption placeholders: {filename} {filesize} {duration}\n"
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
    btns = [Btn(("• " if p == period else "") + label, callback_data=f"rnlb:{p}")
            for p, label in (("today", "Today"), ("week", "Week"), ("month", "Month"), ("year", "Year"),
                             ("all", "All-time"))]
    return "\n".join(lines), InlineKeyboardMarkup([btns[:3], btns[3:]])


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
        preview = extract.new_filename(template, SAMPLE, (await store.get(uid)).get("mkv", True))
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
    info = extract.parse(name)
    new = extract.new_filename(tpl, name, s.get("mkv", True))
    detected = "\n".join(f"• {k}: <code>{esc(v)}</code>" for k, v in info.items() if v not in (None, ""))
    await message.reply_text(f"<b>👁 Rename preview</b>\n\n<code>{esc(name)}</code>\n➜ <code>{esc(new)}</code>\n\n"
                             f"<b>Detected</b>\n{detected or '—'}")


@Client.on_message(filters.command("tutorial") & filters.private)
async def tutorial_cmd(client: Client, message: Message):
    await message.reply_text(TUTORIAL, reply_markup=InlineKeyboardMarkup(
        [[Btn("⚙️ Rename settings", callback_data="rn:home")]]), disable_web_page_preview=True)


@Client.on_message(filters.command(["leaderboard", "top"]))
async def leaderboard_cmd(client: Client, message: Message):
    uid = message.from_user.id if message.from_user else 0
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
    pos = await engine.submit(client, message)
    if not pos:
        return await message.reply_text(f"⏳ Your queue is full ({engine.QUEUE_LIMIT} files). "
                                        "Wait for it to finish or /cancel.", quote=True)
    if pos > 1:
        await message.reply_text(f"🕒 Queued – position <b>#{pos}</b>.", quote=True)


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
        return
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
        return await show(("<b>✏️ Send your new template</b>\n\nExample:\n"
                           "<code>{title} S{season}E{episode} [{quality}] [{audio}]</code>\n\n<i>/cancel to abort</i>",
                           InlineKeyboardMarkup([[Btn("📖 Placeholders", callback_data="rn:help")]])))
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


@Client.on_callback_query(filters.regex(r"^help_rename$"))
async def help_rename_cb(client: Client, query: CallbackQuery):
    await query.answer()
    await smart_edit(query.message, TUTORIAL, InlineKeyboardMarkup([
        [Btn("⚙️ Rename settings", callback_data="rn:home"), Btn("🏆 Leaderboard", callback_data="rnlb:all")],
        [Btn("⬅️ Back", callback_data="help_btn")]]))


@Client.on_callback_query(filters.regex(r"^rnlb:(\w+)$"))
async def leaderboard_cb(client: Client, query: CallbackQuery):
    text, kb = await leaderboard_view(query.matches[0].group(1), query.from_user.id)
    await query.answer()
    await smart_edit(query.message, text, kb)


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

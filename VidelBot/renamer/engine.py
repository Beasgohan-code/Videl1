"""
Auto-Rename pipeline: download → (MP4→MKV) → metadata → thumbnail/caption → upload → dump.

• template jobs (auto mode) and named jobs (manual mode / reply /rename) share one pipeline
• one job at a time per user (FIFO), RENAME_CONCURRENCY jobs globally
• live progress with ⏹ Cancel (StopTransmission), /cancel drops the whole queue
• every ffmpeg step is stream-copy only and falls back gracefully instead of failing
"""
import asyncio
import html
import itertools
import json
import logging
import os

from config import env_int
import shutil
import time
from dataclasses import dataclass, field

from pyrogram import StopTransmission
from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup, Message

from core.ui import humanbytes, readable_time
from renamer import extract, store

log = logging.getLogger("videl.rename")

WORK_DIR = "downloads/rename"
TG_LIMIT = 2000 * 1024 * 1024               # bots can upload up to 2000 MiB
CONCURRENCY = max(1, env_int("RENAME_CONCURRENCY", 3))
QUEUE_LIMIT = max(1, env_int("RENAME_QUEUE_LIMIT", 30))
DUMP_CHANNEL = env_int("RENAME_DUMP_CHANNEL", 0)
PROGRESS_EVERY = 5

SEM = asyncio.Semaphore(CONCURRENCY)
_locks: dict[int, asyncio.Lock] = {}
_pending: dict[int, int] = {}                 # uid -> jobs queued or running
_jobs: dict[int, "Job"] = {}                  # job id -> job
_cancel_before: dict[int, float] = {}         # uid -> jobs created before this time are dropped
_recent: dict[str, float] = {}                # file_unique_id -> ts (duplicate-update guard)
_ids = itertools.count(1)
VIDEO_EXT = (".mp4", ".mkv", ".avi", ".webm", ".mov", ".m4v", ".ts", ".flv", ".wmv")
AUDIO_EXT = (".mp3", ".flac", ".wav", ".ogg", ".m4a", ".aac", ".opus", ".wma")


class Cancelled(Exception):
    pass


@dataclass
class Job:
    uid: int
    message: Message
    id: int = field(default_factory=lambda: next(_ids))
    created: float = field(default_factory=time.time)
    cancelled: bool = False
    status: Message = None
    label: str = ""                 # escaped new file name, shown in the progress message
    name: str = ""                  # explicit new name (manual mode / /rename) – skips the template
    send_as: str = ""               # one-off upload type override ("document" / "video" / "audio")
    user: object = None             # who asked (defaults to the file's sender)


def media_of(message: Message):
    for kind in ("document", "video", "audio"):
        m = getattr(message, kind, None)
        if m:
            return kind, m
    return None, None


def original_name(message: Message) -> str:
    kind, m = media_of(message)
    if not m:
        return ""
    name = getattr(m, "file_name", None)
    if name:
        return name
    ext = {"video": ".mp4", "audio": ".mp3"}.get(kind, "")
    mime = getattr(m, "mime_type", "") or ""
    if mime.startswith("video/"):
        ext = "." + (mime.split("/")[1].replace("x-matroska", "mkv").replace("quicktime", "mov"))
    return f"{kind}_{m.file_unique_id}{ext}"


def pick_type(pref: str, filename: str) -> str:
    """User preference (/setmedia) → else guessed from the extension like the original bot."""
    if pref in store.MEDIA_TYPES:
        return pref
    low = filename.lower()
    if low.endswith(AUDIO_EXT):
        return "audio"
    return "document"


def pending(uid: int) -> int:
    return _pending.get(uid, 0)


def running(uid: int) -> list:
    """Live jobs of one user, oldest first (the first one is being processed)."""
    return sorted((j for j in _jobs.values() if j.uid == uid and not j.cancelled), key=lambda j: j.id)


def global_load() -> tuple[int, int]:
    """(jobs queued or running across all users, concurrency slots)."""
    return sum(_pending.values()), CONCURRENCY


def is_duplicate(unique_id: str) -> bool:
    now = time.time()
    for k, t in list(_recent.items()):
        if now - t > 60:
            _recent.pop(k, None)
    if unique_id in _recent and now - _recent[unique_id] < 10:
        return True
    _recent[unique_id] = now
    return False


def cancel_user(uid: int) -> int:
    """Cancel the running job and drop everything queued. Returns how many jobs were affected."""
    _cancel_before[uid] = time.time()
    n = 0
    for job in list(_jobs.values()):
        if job.uid == uid and not job.cancelled:
            job.cancelled = True
            n += 1
    return n


def cancel_job(job_id: int, uid: int) -> bool:
    job = _jobs.get(job_id)
    if not job or job.uid != uid:
        return False
    job.cancelled = True
    return True


def _cancel_kb(job: Job):
    return InlineKeyboardMarkup([[Btn("⏹ Cancel", callback_data=f"rnx:{job.id}")]])


def _bar(pct: float) -> str:
    filled = int(pct // 5)
    return "■" * filled + "□" * (20 - filled)


RENAME_PROGRESS = ("<b>{title}</b>\n📄 <code>{name}</code>\n\n<code>[{bar}]</code> <b>{percentage:.1f}%</b>\n\n"
                   "💾 {current} / {total}\n⚡ {speed}/s · ⏳ {eta}")


def progress_cb(job: Job, title: str):
    """Live progress with ⏹ Cancel; cancelling raises StopTransmission inside pyrogram."""
    from core.progress import LiveProgress

    class _Bar(LiveProgress):
        def render(self, current, total):
            return super().render(current, total).replace("█", "■").replace("░", "□")

    return _Bar(job.status, title, template=RENAME_PROGRESS, every=PROGRESS_EVERY,
                cancel=lambda: job.cancelled, reply_markup=_cancel_kb(job), name=job.label or "file").update


# ─────────────────────────── ffmpeg helpers ───────────────────────────
def ffmpeg_command(inp: str, out: str, meta: dict = None, to_mkv: bool = False, srt: bool = False) -> list:
    cmd = [shutil.which("ffmpeg") or "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", inp,
           "-map", "0", "-c", "copy"]
    if srt:
        cmd += ["-c:s", "srt"]
    for key, val in (meta or {}).items():
        if not val:
            continue
        if key == "video":
            cmd += ["-metadata:s:v", f"title={val}"]
        elif key == "audio":
            cmd += ["-metadata:s:a", f"title={val}"]
        elif key == "subtitle":
            cmd += ["-metadata:s:s", f"title={val}"]
        elif key in store.META_FIELDS:
            cmd += ["-metadata", f"{key}={val}"]
    if to_mkv:
        cmd += ["-f", "matroska"]
    cmd.append(out)
    return cmd


async def _run(cmd: list, timeout: int = 1800) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "timeout"
    return proc.returncode, (err or b"").decode(errors="ignore")[-400:]


async def remux(path: str, out: str, meta: dict, to_mkv: bool) -> str:
    """Stream-copy with metadata / into MKV. Returns the new path, or '' if every attempt failed."""
    if not shutil.which("ffmpeg"):
        return ""
    attempts = [dict(srt=False)]
    if to_mkv:
        attempts.append(dict(srt=True))       # mov_text subtitles can't be copied into Matroska
    for opts in attempts:
        code, err = await _run(ffmpeg_command(path, out, meta, to_mkv, **opts))
        if code == 0 and os.path.exists(out) and os.path.getsize(out) > 0:
            return out
        log.info(f"ffmpeg remux failed ({opts}): {err.strip()[:200]}")
    try:
        os.remove(out)
    except OSError:
        pass
    return ""


async def probe_media(path: str) -> tuple[int, int, int]:
    """(duration s, width, height) via ffprobe – zeros when unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0, 0, 0
    proc = await asyncio.create_subprocess_exec(ffprobe, "-v", "quiet", "-print_format", "json", "-show_format",
                                                "-show_streams", path, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.PIPE)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 60)
        data = json.loads(out or b"{}")
        duration = int(float(data.get("format", {}).get("duration", 0) or 0))
        video = next((st for st in data.get("streams", []) if st.get("codec_type") == "video"
                      and not (st.get("disposition") or {}).get("attached_pic")), {})
        return duration, int(video.get("width") or 0), int(video.get("height") or 0)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
        return 0, 0, 0


async def probe_duration(path: str) -> int:
    return (await probe_media(path))[0]


async def frame_thumbnail(path: str, dst: str, duration: int = 0) -> str:
    """Grab one frame (10 % in, max 60 s) as a 320 px JPEG – for videos without any thumbnail."""
    if not shutil.which("ffmpeg"):
        return ""
    at = min(max(duration * 0.1, 1), 60) if duration else 1
    code, err = await _run([shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.1f}",
                            "-i", path, "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "3", dst], timeout=90)
    if code == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0:
        return dst
    log.debug(f"frame thumbnail failed: {err.strip()[:120]}")
    return ""


def make_jpeg(src: str, dst: str) -> str:
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGB")
            im.thumbnail((320, 320))
            im.save(dst, "JPEG", quality=90)
        return dst
    except Exception as e:
        log.debug(f"thumbnail convert failed: {e}")
        return ""


CAPTION_KEYS = ("filename", "filesize", "size", "duration", "title", "season", "episode", "quality", "audio",
                "year", "codec", "source", "group", "original")


class _KeepMissing(dict):
    def __missing__(self, key):          # unknown {placeholders} stay as typed
        return "{" + key + "}"


def caption_values(new_name: str, size: int, duration: int, old_name: str = "") -> dict:
    info = extract.parse(new_name)
    ep = info.get("episode")
    values = {
        "filename": new_name, "filesize": humanbytes(size), "size": humanbytes(size),
        "duration": readable_time(duration) if duration else "N/A",
        "title": info.get("title") or "", "season": f"{info['season']:02d}" if info.get("season") else "",
        "episode": (f"{ep:02d}" + (f"-{info['episode_end']:02d}" if info.get("episode_end") else "")) if ep else "",
        "quality": info.get("quality") or "", "audio": info.get("audio") or "", "year": info.get("year") or "",
        "codec": info.get("codec") or "", "source": info.get("source") or "", "group": info.get("group") or "",
        "original": old_name or new_name,
    }
    return {k: html.escape(str(v), quote=False) for k, v in values.items()}


def build_caption(template: str, new_name: str, size: int, duration: int, old_name: str = "") -> str:
    """Saver /set_caption template with {filename} {size} {duration} {title} {season} {episode} {quality}
    {audio} {year} {codec} {source} {group} {original}; default = the file name in monospace."""
    values = caption_values(new_name, size, duration, old_name)
    if template:
        try:
            return template.format_map(_KeepMissing(values))[:1024]
        except Exception:
            return template[:1024]
    return f"<code>{values['filename']}</code>"[:1024]


async def dump_chat() -> int:
    from core.db import vdb
    return int(await vdb.get_setting("rn_dump", 0) or 0) or DUMP_CHANNEL


# ─────────────────────────── queue ───────────────────────────
async def submit(client, message: Message, name: str = "", send_as: str = "", user=None) -> int:
    """Queue a file. Returns its position (1 = running now), 0 if the queue is full.
    `name` renames to exactly that (manual mode / /rename) instead of using the template;
    `user` is who asked when that isn't the file's sender (a reply to a file the bot sent)."""
    user = user or message.from_user
    uid = user.id
    if pending(uid) >= QUEUE_LIMIT:
        return 0
    job = Job(uid=uid, message=message, name=name or "", user=user,
              send_as=send_as if send_as in store.MEDIA_TYPES else "")
    _pending[uid] = pending(uid) + 1
    _jobs[job.id] = job
    asyncio.create_task(_worker(client, job))
    return _pending[uid]


async def _worker(client, job: Job):
    uid = job.uid
    lock = _locks.setdefault(uid, asyncio.Lock())
    try:
        async with lock:
            if job.cancelled or job.created <= _cancel_before.get(uid, 0):
                return
            async with SEM:
                if job.cancelled:
                    return
                await process(client, job)
    except Exception as e:
        log.exception(f"rename job {job.id} crashed: {e}")
    finally:
        _jobs.pop(job.id, None)
        _pending[uid] = max(0, pending(uid) - 1)
        if not _pending[uid]:
            _pending.pop(uid, None)


async def process(client, job: Job):
    message = job.message
    user = job.user or message.from_user
    uid = user.id
    kind, media = media_of(message)
    old_name = original_name(message)
    settings = await store.get(uid)
    template = settings.get("template")
    if not media or not (template or job.name):
        return
    to_mkv = settings.get("mkv", True)
    size = getattr(media, "file_size", 0) or 0
    if job.name:
        new_name = extract.manual_filename(job.name, old_name, to_mkv=to_mkv)
    else:
        new_name = extract.new_filename(template, old_name, to_mkv=to_mkv, clean=settings.get("clean", False),
                                        words=settings.get("words"), size=humanbytes(size) if size else "")
    job.label = html.escape(new_name[:80], quote=False)

    async def reply(text):
        try:
            return await message.reply_text(text, quote=True)
        except Exception:
            return None

    from core.db import vdb
    if await vdb.get_setting("rn_nsfw", True):
        bad = extract.is_nsfw(old_name, new_name)
        if bad:
            await reply("🔞 <b>NSFW content detected</b> – file rejected.")
            return
    if size > TG_LIMIT:
        await reply(f"❌ <b>Too big:</b> {humanbytes(size)}. Bots can upload up to 2 GB.")
        return
    os.makedirs(WORK_DIR, exist_ok=True)
    free = shutil.disk_usage(WORK_DIR).free
    if size and free < size * 2.2 + 200 * 1024 * 1024:
        await reply("⏳ <b>Server storage is busy right now.</b> Please try again in a few minutes.")
        return

    job.status = await reply(f"📥 <b>Preparing</b> <code>{html.escape(new_name, quote=False)}</code>…")
    if not job.status:
        return
    workdir = os.path.join(WORK_DIR, f"{uid}_{job.id}_{int(time.time())}")
    os.makedirs(workdir, exist_ok=True)
    try:
        # 1. download
        src = os.path.join(workdir, "src" + (os.path.splitext(old_name)[1] or ""))
        try:
            path = await client.download_media(message, file_name=src,
                                               progress=progress_cb(job, "📥 Downloading…"))
        except StopTransmission:
            raise Cancelled
        if job.cancelled:
            raise Cancelled
        if not path or not os.path.exists(path):
            raise RuntimeError("download failed")

        # 2. MP4 → MKV and/or metadata (stream copy)
        meta = settings.get("meta") or {}
        meta_on = settings.get("meta_on", False) and any(meta.get(k) for k in store.META_FIELDS)
        wants_mkv = new_name.lower().endswith(".mkv") and not old_name.lower().endswith(".mkv")
        note = ""
        if wants_mkv or meta_on:
            await job.status.edit_text("⚙️ <b>" + ("Converting to MKV" if wants_mkv else "Adding metadata") +
                                       "…</b>", reply_markup=_cancel_kb(job))
            out_ext = ".mkv" if wants_mkv else os.path.splitext(path)[1]
            done = await remux(path, os.path.join(workdir, "out" + out_ext), meta if meta_on else None, wants_mkv)
            if not done and wants_mkv and meta_on:
                # MKV failed → keep the container, still try the metadata
                done = await remux(path, os.path.join(workdir, "out" + os.path.splitext(path)[1]), meta, False)
                wants_mkv = False
            if done:
                path = done
            else:
                note = "\n<i>⚠️ Metadata / MKV step skipped (unsupported streams).</i>"
                wants_mkv = False
            if not wants_mkv and new_name.lower().endswith(".mkv") and not old_name.lower().endswith(".mkv"):
                new_name = new_name[:-4] + os.path.splitext(old_name)[1]
        if job.cancelled:
            raise Cancelled
        final = os.path.join(workdir, new_name)
        os.replace(path, final)
        size = os.path.getsize(final)

        # 3. duration / size, thumbnail, caption
        send_as = job.send_as or pick_type(settings.get("media"), new_name)
        is_video = new_name.lower().endswith(VIDEO_EXT)
        duration = int(getattr(media, "duration", 0) or 0)
        width = getattr(media, "width", 0) or 0
        height = getattr(media, "height", 0) or 0
        if (not duration and (is_video or send_as != "document")) or (send_as == "video" and not (width and height)):
            p_dur, p_w, p_h = await probe_media(final)
            duration = duration or p_dur
            width, height = (width, height) if width and height else (p_w, p_h)
        thumb = await _thumbnail(client, uid, media, workdir)
        if not thumb and is_video and send_as != "audio":
            thumb = await frame_thumbnail(final, os.path.join(workdir, "frame.jpg"), duration)
        caption_tpl = ""
        try:
            from database.db import db as saver_db
            caption_tpl = await saver_db.get_caption(uid) or ""
        except Exception:
            pass
        caption = build_caption(caption_tpl, new_name, size, duration, old_name)

        # 4. upload
        up = progress_cb(job, "📤 Uploading…")
        common = dict(caption=caption, reply_to_message_id=message.id, progress=up)
        if send_as == "video":
            sent = await client.send_video(message.chat.id, final, file_name=new_name, duration=duration,
                                           width=width, height=height, thumb=thumb or None,
                                           supports_streaming=True, **common)
        elif send_as == "audio":
            sent = await client.send_audio(message.chat.id, final, file_name=new_name, duration=duration,
                                           thumb=thumb or None, title=meta.get("title") if meta_on else None,
                                           performer=meta.get("artist") if meta_on else None, **common)
        else:
            sent = await client.send_document(message.chat.id, final, file_name=new_name, thumb=thumb or None,
                                              force_document=True, **common)
        if job.cancelled or sent is None:
            raise Cancelled

        # 5. stats + dump channel
        await store.record_rename(user, new_name, old_name)
        await _dump(client, sent, user, old_name, new_name, size)
        if note:
            await job.status.edit_text(f"✅ <b>Done:</b> <code>{html.escape(new_name, quote=False)}</code>{note}")
        else:
            try:
                await job.status.delete()
            except Exception:
                pass
    except Cancelled:
        try:
            await job.status.edit_text(f"⏹ <b>Cancelled:</b> <code>{html.escape(new_name, quote=False)}</code>")
        except Exception:
            pass
    except Exception as e:
        log.warning(f"rename failed for {uid}: {e}")
        try:
            await job.status.edit_text(f"❌ <b>Rename failed:</b> <code>{str(e)[:300]}</code>")
        except Exception:
            pass
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def _thumbnail(client, uid: int, media, workdir: str) -> str:
    """User thumbnail (/set_thumb) → else the file's own Telegram thumbnail."""
    file_id = None
    try:
        from database.db import db as saver_db
        file_id = await saver_db.get_thumbnail(uid)
    except Exception:
        pass
    if not file_id and getattr(media, "thumbs", None):
        file_id = media.thumbs[0].file_id
    if not file_id:
        return ""
    try:
        raw = await client.download_media(file_id, file_name=os.path.join(workdir, "thumb_src"))
        return make_jpeg(raw, os.path.join(workdir, "thumb.jpg")) if raw else ""
    except Exception as e:
        log.debug(f"thumbnail download failed: {e}")
        return ""


async def _dump(client, sent, user, old_name: str, new_name: str, size: int):
    chat = await dump_chat()
    if not chat:
        return
    from core.botlog import esc
    caption = (f"<b>✏️ Renamed</b>\n\n<b>👤 User:</b> {esc(user.first_name or '')} "
               f"(<code>{user.id}</code>){' @' + user.username if user.username else ''}\n"
               f"<b>📄 Original:</b> <code>{esc(old_name)}</code>\n<b>🆕 New:</b> <code>{esc(new_name)}</code>\n"
               f"<b>💾 Size:</b> {humanbytes(size)}")
    try:
        await sent.copy(chat, caption=caption[:1024])
    except Exception as e:
        log.debug(f"rename dump failed: {e}")

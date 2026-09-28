import html
import shutil

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery

from .. import LOGGER, app, data, download_dir, log, owner, sudo_users
from ..plugins.queue import queue_answer
from ..utils import ffcmd, jobs
from ..utils.database.access_db import db
from ..utils.settings import (AdvancedSettings, AudioSettings, ExtraSettings, OpenSettings, ProfileSettings,
                              VideoSettings, WmSettings)
from ..video_utils.audio_selector import sessions


_ENC_EXACT = {
    "closeMeh", "VideoSettings", "OpenSettings", "AudioSettings", "ExtraSettings", "AdvancedSettings",
    "EncProfiles", "Watermark", "WmSettings", "cancel", "stats",
}


def _is_encoder_cb(_, __, cb: CallbackQuery) -> bool:
    d = cb.data or ""
    return d in _ENC_EXACT or d.startswith(("trigger", "queue+", "audiosel", "encp:", "enc_cancel:"))


encoder_cb_filter = filters.create(_is_encoder_cb)

MENUS = {"video": VideoSettings, "audio": AudioSettings, "extra": ExtraSettings, "adv": AdvancedSettings,
         "wm": WmSettings}

# callback → (field, values in cycle order, menu)  – same orders as the original bot
CYCLES = {
    "triggerextensions": ("extensions", ["MP4", "MKV", "AVI"], "video"),
    "triggerframe": ("frame", ["source", "pal", "film", "23.976", "30", "60", "ntsc"], "video"),
    "triggerPreset": ("preset", ffcmd.PRESET_ORDER, "video"),
    "triggerResolution": ("resolution", ["OG", "1080", "720", "576", "480"], "video"),
    "triggerreframe": ("reframe", ["pass", "4", "8", "16"], "video"),
    "triggersamplerate": ("sample", ["44.1K", "48K", "source"], "audio"),
    "triggerbitrate": ("bitrate", ["400", "320", "256", "224", "192", "160", "128", "source"], "audio"),
    "triggerAudioCodec": ("audio", ["dd", "copy", "aac", "opus", "alac", "vorbis"], "audio"),
    "triggerAudioChannels": ("channels", ["source", "1.0", "2.0", "2.1", "5.1", "7.1"], "audio"),
    "triggerOpacity": ("motion_opacity", ["50", "75", "100"], "extra"),
    "triggerWmPos": ("wm_pos", ffcmd.WM_POSITIONS, "wm"),
    "triggerWmSize": ("wm_size", ["s", "m", "l"], "wm"),
    "triggerWmOpacity": ("wm_opacity", ffcmd.WM_OPACITY, "wm"),
}

# callback → (field, menu)
TOGGLES = {
    "triggerMode": ("drive", "extra"), "triggerUploadMode": ("upload_as_doc", "extra"),
    "triggerResize": ("resize", "extra"), "triggerMetadata": ("metadata", "extra"),
    "triggerVideo": ("watermark", "extra"), "triggerMotion": ("motion_watermark", "extra"),
    "triggerHardsub": ("hardsub", "extra"), "triggerSubtitles": ("subtitles", "extra"),
    "triggerBits": ("bits", "video"), "triggerHevc": ("hevc", "video"), "triggertune": ("tune", "video"),
    "triggercabac": ("cabac", "video"), "triggeraspect": ("aspect", "video"),
    "triggerDeint": ("deinterlace", "adv"), "triggerDenoise": ("denoise", "adv"),
    "triggerDedup": ("dedup", "adv"),
    "triggerLoudnorm": ("loudnorm", "adv"), "triggerLoudnorm:a": ("loudnorm", "audio"),
    "triggerVideo:w": ("watermark", "wm"), "triggerHw": ("hw", "video"),
}

# Encoder Pro only (admins, Encoder Pro, Premium) – callback → (field, menu, pitch)
PRO_TOGGLES = {
    "triggerTwopass": ("twopass", "adv", "🎯 2-pass exact size is an 🎬 Encoder Pro feature – /plans"),
    "triggerLogo": ("logo", "extra", "🖼 Logo watermarks are an 🎬 Encoder Pro feature – /plans"),
    "triggerLogo:w": ("logo", "wm", "🖼 Logo watermarks are an 🎬 Encoder Pro feature – /plans"),
}
CODECS = ["h264", "hevc", "av1"]

# (callback, new value) → one-time hint shown as a toast
NOTES = {
    ("triggerHevc", True): ("H.265 gives ~40% smaller files but encodes slower.", False),
    ("triggerBits", True): ("10-bit: smoother gradients, less banding (H.264 uses High10).", False),
    ("triggerAudioChannels", "7.1"): ("7.1 is meant for Blu-ray sources.", True),
    ("triggerreframe", "16"): ("Reframe 16 may not play on every device.", True),
    ("triggeraspect", True): ("This forces the video to 16:9.", False),
    ("triggerHardsub", True): ("Hardsub works with text subtitles (SRT / ASS), not PGS pictures.", False),
    ("triggerDeint", True): ("Only interlaced frames are processed – safe to leave on.", False),
    ("triggerLoudnorm", True): ("Audio will be re-encoded to even out the volume.", False),
    ("triggerDedup", True): ("Repeated frames are dropped – great for anime, slideshows and screen recordings "
                             "(smaller + faster). The FPS setting is ignored while this is on.", True),
    ("triggerTwopass", True): ("2-pass: the size lands within ~2 % of your target; encoding takes ~1.7× longer.", False),
    ("triggerHw", False): ("GPU off – encodes use the CPU (slower, a little smaller at the same quality).", False),
    ("triggerCodec", "av1"): ("AV1: the smallest files (~30 % below H.265) but the slowest encode. "
                              "Plays on modern phones, browsers and TVs.", True),
}


async def _pro(uid: int) -> bool:
    try:
        from core.plans import is_encoder_pro
        return await is_encoder_pro(uid)
    except Exception:
        return uid in sudo_users or uid == owner

CRF_MIN, CRF_MAX = 12, 40


async def _is_admin(uid: int) -> bool:
    if uid in owner or uid in sudo_users:
        return True
    try:
        return str(uid) in str(await db.get_sudo() or "").replace(",", " ").split()
    except Exception:
        return False


async def _cancel(bot, cb: CallbackQuery, key):
    job = jobs.get(key) if key is not None else jobs.latest()
    if not job:
        await cb.answer("Nothing to cancel – this task already finished.", show_alert=True)
        return
    uid = cb.from_user.id
    if uid != job.user_id and not await _is_admin(uid):
        await cb.answer("Only the user who started this task (or an admin) can cancel it.", show_alert=True)
        return
    await cb.answer("Cancelling…")
    await jobs.cancel(job.key)
    try:
        await cb.message.edit_text("🚫 <b>Task cancelled.</b>")
    except Exception:
        pass
    if log:
        try:
            who = f"<a href='tg://user?id={uid}'>{html.escape(cb.from_user.first_name or str(uid))}</a>"
            await bot.send_message(log, f"🚫 <b>Encoder task cancelled</b> by {who}\n"
                                        f"<code>{html.escape(job.name or '-')[:80]}</code> · stage: {job.stage}")
        except Exception:
            pass


def _stats_text() -> str:
    """Short enough for a callback alert (Telegram's limit is 200 characters)."""
    parts = [f"📊 Encoder · queue: {len(data)}"]
    running = jobs.active()
    if running:
        j = running[0]
        parts.append(f"▶️ {(j.name or 'task')[:40]} ({j.stage})")
    try:
        import psutil
        parts.append(f"CPU {psutil.cpu_percent(interval=None):.0f}% · RAM {psutil.virtual_memory().percent:.0f}%")
    except Exception:
        pass
    try:
        free = shutil.disk_usage(download_dir).free / 1024 ** 3
        parts.append(f"Disk free {free:.1f} GB")
    except Exception:
        pass
    return "\n".join(parts)[:200]


@Client.on_callback_query(encoder_cb_filter)
async def callback_handlers(bot: Client, cb: CallbackQuery):
    d = cb.data or ""
    uid = cb.from_user.id
    note = None
    try:
        if d == "closeMeh":
            await cb.message.delete(True)
            return

        elif d == "OpenSettings":
            await OpenSettings(cb.message, user_id=uid)
        elif d == "VideoSettings":
            await VideoSettings(cb.message, user_id=uid)
        elif d == "AudioSettings":
            await AudioSettings(cb.message, user_id=uid)
        elif d == "ExtraSettings":
            await ExtraSettings(cb.message, user_id=uid)
        elif d == "AdvancedSettings":
            await AdvancedSettings(cb.message, user_id=uid)
        elif d == "EncProfiles":
            await ProfileSettings(cb.message, user_id=uid)

        elif d.startswith("encp:"):
            key = d.split(":", 1)[1]
            if key not in ffcmd.PROFILES:
                await cb.answer("This profile no longer exists.", show_alert=True)
                return
            label, desc, vals = ffcmd.PROFILES[key]
            await db.update_settings(uid, **vals)
            note = (f"✅ {label} applied – {desc}", False)
            await ProfileSettings(cb.message, user_id=uid)

        elif d in CYCLES:
            field, values, menu = CYCLES[d]
            s = await db.get_settings(uid)
            cur = str(s.get(field))
            nxt = values[(values.index(cur) + 1) % len(values)] if cur in values else values[0]
            await db.update_settings(uid, **{field: nxt})
            note = NOTES.get((d, nxt))
            await MENUS[menu](cb.message, user_id=uid)

        elif d in TOGGLES:
            field, menu = TOGGLES[d]
            s = await db.get_settings(uid)
            new = not bool(s.get(field))
            await db.update_settings(uid, **{field: new})
            note = NOTES.get((d.split(":")[0], new))
            await MENUS[menu](cb.message, user_id=uid)

        elif d == "triggerCodec":
            s = await db.get_settings(uid)
            cur = ffcmd.video_codec(s)
            nxt = CODECS[(CODECS.index(cur) + 1) % len(CODECS)]
            if nxt == "av1" and not await _pro(uid):
                nxt = "h264"
                note = ("💎 AV1 is an 🎬 Encoder Pro feature – /plans", True)
            await db.update_settings(uid, hevc=nxt == "hevc", av1=nxt == "av1")
            note = note or NOTES.get((d, nxt)) or NOTES.get(("triggerHevc", True) if nxt == "hevc" else ("", ""))
            await VideoSettings(cb.message, user_id=uid)

        elif d in PRO_TOGGLES:
            field, menu, pitch = PRO_TOGGLES[d]
            s = await db.get_settings(uid)
            new = not bool(s.get(field))
            if field == "logo" and new and not s.get("logo_id"):
                note = ("🖼 Set a logo first: reply /watermark to a photo (PNG with transparency looks best).", True)
            elif new and not await _pro(uid):
                note = (pitch, True)
            else:
                await db.update_settings(uid, **{field: new})
                note = NOTES.get((d.split(":")[0], new))
                await MENUS[menu](cb.message, user_id=uid)

        elif d == "WmSettings":
            await WmSettings(cb.message, user_id=uid)

        elif d in ("triggerCRF", "triggerCRFdown"):
            s = await db.get_settings(uid)
            step = 1 if d == "triggerCRF" else -1
            crf = max(CRF_MIN, min(CRF_MAX, int(s["crf"]) + step))
            if crf == int(s["crf"]):
                note = (f"CRF range is {CRF_MIN}–{CRF_MAX}.", False)
            else:
                await db.update_settings(uid, crf=crf)
                await VideoSettings(cb.message, user_id=uid)

        elif d == "triggerEncMode":
            s = await db.get_settings(uid)
            if s["mode"] == "size":
                await db.update_settings(uid, mode="crf")
            else:
                await db.update_settings(uid, mode="size", target_mb=s["target_mb"] or 200)
                note = ("🎯 Target size: Videl picks the bitrate so the file lands near your size.", False)
            await AdvancedSettings(cb.message, user_id=uid)

        elif d in ("triggerTargetSize", "triggerTargetDown"):
            s = await db.get_settings(uid)
            sizes = ffcmd.TARGET_SIZES[1:]
            cur = s["target_mb"] if s["target_mb"] in sizes else 200
            i = sizes.index(cur) + (1 if d == "triggerTargetSize" else -1)
            await db.update_settings(uid, target_mb=sizes[max(0, min(len(sizes) - 1, i))])
            await AdvancedSettings(cb.message, user_id=uid)

        elif d == "Watermark":            # label buttons on menus sent before the upgrade
            await cb.answer()
            return

        elif d.startswith("audiosel"):
            if uid in sessions:
                await sessions[uid].resolve_callback(cb)
            else:
                await cb.answer("Session expired. Please try again.", show_alert=True)
            return

        elif d == "cancel" or d.startswith("enc_cancel:"):
            key = None
            if d.startswith("enc_cancel:"):
                raw = d.split(":", 1)[1]
                key = int(raw) if raw.lstrip("-").isdigit() else None
            await _cancel(bot, cb, key)
            return

        elif d == "stats":
            await cb.answer(_stats_text(), show_alert=True)
            return

        elif d.startswith("queue+"):
            await queue_answer(app, cb)
            return

        else:
            await cb.answer()
            return

        try:
            if note:
                await cb.answer(note[0][:200], show_alert=note[1])
            else:
                await cb.answer()
        except Exception:
            pass
    except Exception as e:
        LOGGER.error(f"Error in callback_handlers ({d}): {e}")
        try:
            await cb.answer("An error occurred. Please try again later.", show_alert=True)
        except Exception:
            pass

"""
Encoder settings menus (Encoder Pro).

Every screen reads the whole settings document ONCE (the old menus made up to 12 DB round
trips per render), shows a readable summary card, and uses labelled status chips
(`noop:enc`) instead of dead “Sir, this button not works XD” buttons.
"""
import asyncio
import html

from pyrogram.errors import FloodWait, MessageNotModified
from pyrogram.types import InlineKeyboardButton as Btn
from pyrogram.types import InlineKeyboardMarkup, Message

from core.style import hdr, hint, row, sec
from .. import LOGGER
from . import ffcmd
from .database.access_db import db
from .display_progress import humanbytes


def _on(v) -> str:
    return "✅" if v else "▫️"


def _chip(text: str) -> Btn:
    return Btn(text, callback_data="noop:enc")


async def _render(event: Message, text: str, rows, retry=None):
    try:
        await event.edit(text=text, reply_markup=InlineKeyboardMarkup(rows), disable_web_page_preview=True)
    except FloodWait as e:
        await asyncio.sleep(getattr(e, "value", getattr(e, "x", 5)))
        if retry:
            await retry()
    except MessageNotModified:
        pass
    except Exception as e:
        LOGGER.error(f"encoder settings render: {e}")
        try:
            await event.edit(f"An error occurred while opening the settings: {e}")
        except Exception:
            pass


def _profile_label(s: dict) -> str:
    key = ffcmd.matches_profile(s)
    return ffcmd.PROFILES[key][0] if key else "🛠 Custom"


# ─────────────────────────── hub ───────────────────────────
async def OpenSettings(event: Message, user_id: int):
    s = await db.get_settings(user_id)
    d = ffcmd.describe(s)
    lines = [hdr("🎬", "Encoder settings", "Changes apply to your next encode"), "",
             row("Profile", _profile_label(s)),
             row("Video", f"{d['codec']} · {d['quality']} · {d['resolution']} · {d['preset']}"),
             row("Audio", f"{d['audio']} · {d['audio_bitrate']} · {d['channels']}"),
             row("Output", f"{d['container']} · {d['upload']}"),
             row("Filters", d["filters"])]
    if s.get("enc_count"):
        saved = max(0, s["enc_in"] - s["enc_out"])
        lines.append(row("Your encodes", f"{s['enc_count']} · saved {humanbytes(saved) or '0 B'}"))
    lines += ["", hint("Tip: /sample tests your settings on 30 s of the video first.")]
    rows = [
        [Btn("⚡ Quick profiles", callback_data="EncProfiles")],
        [Btn("🎞 Video", callback_data="VideoSettings"), Btn("🔊 Audio", callback_data="AudioSettings")],
        [Btn("🧪 Advanced", callback_data="AdvancedSettings"), Btn("🧩 Extras", callback_data="ExtraSettings")],
        [Btn("⬅️ Settings Hub", callback_data="v_settings"), Btn("✖️ Close", callback_data="closeMeh")],
    ]
    await _render(event, "\n".join(lines), rows, lambda: OpenSettings(event, user_id))


# ─────────────────────────── profiles ───────────────────────────
async def ProfileSettings(event: Message, user_id: int):
    s = await db.get_settings(user_id)
    current = ffcmd.matches_profile(s)
    lines = [hdr("⚡", "Quick profiles", "One tap sets codec, quality, resolution and audio"), ""]
    for key, (label, desc, _vals) in ffcmd.PROFILES.items():
        mark = " ✅" if key == current else ""
        lines.append(f"<b>{html.escape(label)}</b>{mark}\n   <i>{html.escape(desc)}</i>")
    lines += ["", hint("Fine-tune anything afterwards in Video / Audio.")]
    keys = list(ffcmd.PROFILES)
    rows = []
    for i in range(0, len(keys), 2):
        rows.append([Btn(ffcmd.PROFILES[k][0] + (" ✅" if k == current else ""), callback_data=f"encp:{k}")
                     for k in keys[i:i + 2]])
    rows.append([Btn("⬅️ Back", callback_data="OpenSettings")])
    await _render(event, "\n".join(lines), rows, lambda: ProfileSettings(event, user_id))


# ─────────────────────────── video ───────────────────────────
async def VideoSettings(event: Message, user_id: int):
    s = await db.get_settings(user_id)
    d = ffcmd.describe(s)
    x265 = bool(s["hevc"])
    lines = [hdr("🎞", "Video settings"), "",
             row("Codec", d["codec"]), row("Quality", d["quality"] + ("  <i>(target-size mode on)</i>"
                                                                      if s["mode"] == "size" and s["target_mb"] else "")),
             row("Resolution", d["resolution"] + " <i>(keeps aspect, never upscales)</i>"),
             row("Preset", d["preset"]), row("Tune", d["tune"]), row("FPS", d["fps"]),
             row("Container", d["container"]), "",
             hint("Lower CRF = better quality & bigger file. 18-20 near-lossless, 22-26 balanced, 28+ small.")]
    rows = [
        [_chip("🎞 ʙᴀꜱɪᴄ")],
        [Btn(f"Codec: {'H.265' if x265 else 'H.264'}", callback_data="triggerHevc"),
         Btn(f"Bits: {'10' if s['bits'] else '8'}", callback_data="triggerBits")],
        [Btn("➖", callback_data="triggerCRFdown"), _chip(f"CRF {s['crf']}"),
         Btn("➕", callback_data="triggerCRF")],
        [Btn(f"Res: {d['resolution']}", callback_data="triggerResolution"),
         Btn(f"Ext: {d['container']}", callback_data="triggerextensions")],
        [Btn(f"Preset: {d['preset']}", callback_data="triggerPreset"),
         Btn(f"Tune: {d['tune']}", callback_data="triggertune")],
        [_chip("⚙️ ᴀᴅᴠᴀɴᴄᴇᴅ")],
        [Btn(f"FPS: {d['fps']}", callback_data="triggerframe"),
         Btn(f"Aspect: {'16:9' if s['aspect'] else 'Source'}", callback_data="triggeraspect")],
    ]
    if not x265:            # x264-only options
        rows.append([Btn(f"CABAC {_on(s['cabac'])}", callback_data="triggercabac"),
                     Btn(f"Reframe: {str(s['reframe']).capitalize()}", callback_data="triggerreframe")])
    rows.append([Btn("⬅️ Back", callback_data="OpenSettings")])
    await _render(event, "\n".join(lines), rows, lambda: VideoSettings(event, user_id))


# ─────────────────────────── audio ───────────────────────────
async def AudioSettings(event: Message, user_id: int):
    s = await db.get_settings(user_id)
    d = ffcmd.describe(s)
    lines = [hdr("🔊", "Audio settings"), "",
             row("Codec", d["audio"]), row("Bitrate", d["audio_bitrate"]), row("Channels", d["channels"]),
             row("Sample rate", d["sample_rate"]), row("Loudness", "Normalised" if s["loudnorm"] else "Source"), "",
             hint("Source (copy) is fastest. MP4 can't hold every codec – Videl converts to AAC automatically.")]
    rows = [
        [Btn(f"Codec: {d['audio']}", callback_data="triggerAudioCodec"),
         Btn(f"Channels: {d['channels']}", callback_data="triggerAudioChannels")],
        [Btn(f"Sample: {d['sample_rate']}", callback_data="triggersamplerate"),
         Btn(f"Bitrate: {d['audio_bitrate']}", callback_data="triggerbitrate")],
        [Btn(f"Loudness normalise {_on(s['loudnorm'])}", callback_data="triggerLoudnorm:a")],
        [Btn("⬅️ Back", callback_data="OpenSettings")],
    ]
    await _render(event, "\n".join(lines), rows, lambda: AudioSettings(event, user_id))


# ─────────────────────────── advanced ───────────────────────────
async def AdvancedSettings(event: Message, user_id: int):
    s = await db.get_settings(user_id)
    size_mode = s["mode"] == "size"
    target = s["target_mb"] or 200
    lines = [hdr("🧪", "Advanced"), "",
             sec("🎯", "Rate control"),
             row("Mode", f"Target size ≈ {target} MB" if size_mode else f"Constant quality (CRF {s['crf']})"),
             hint("Target size picks the bitrate so the file fits – handy for the 2 GB / 4 GB upload limit."), "",
             sec("🧹", "Filters"),
             row("Deinterlace", "On (bwdif, only interlaced frames)" if s["deinterlace"] else "Off"),
             row("Denoise", "On (hqdn3d light)" if s["denoise"] else "Off"),
             row("Loudness", "EBU R128 normalise" if s["loudnorm"] else "Off")]
    rows = [
        [Btn(f"Mode: {'🎯 Target size' if size_mode else '💎 CRF'}", callback_data="triggerEncMode")],
    ]
    if size_mode:
        rows.append([Btn("➖", callback_data="triggerTargetDown"), _chip(f"≈ {target} MB"),
                     Btn("➕", callback_data="triggerTargetSize")])
    rows += [
        [Btn(f"Deinterlace {_on(s['deinterlace'])}", callback_data="triggerDeint"),
         Btn(f"Denoise {_on(s['denoise'])}", callback_data="triggerDenoise")],
        [Btn(f"Loudness normalise {_on(s['loudnorm'])}", callback_data="triggerLoudnorm")],
        [Btn("⬅️ Back", callback_data="OpenSettings")],
    ]
    await _render(event, "\n".join(lines), rows, lambda: AdvancedSettings(event, user_id))


# ─────────────────────────── extras ───────────────────────────
async def ExtraSettings(event: Message, user_id: int):
    s = await db.get_settings(user_id)
    d = ffcmd.describe(s)
    lines = [hdr("🧩", "Extras"), "",
             row("Subtitles", d["subtitles"]), row("Upload", d["upload"]), row("Watermark", d["watermark"]), "",
             hint("Hardsub burns the first subtitle track into the picture (text subtitles only).")]
    rows = [
        [_chip("📜 ꜱᴜʙᴛɪᴛʟᴇꜱ")],
        [Btn(f"Hardsub {_on(s['hardsub'])}", callback_data="triggerHardsub"),
         Btn(f"Copy subs {_on(s['subtitles'])}", callback_data="triggerSubtitles")],
        [_chip("📤 ᴜᴘʟᴏᴀᴅ")],
        [Btn("☁️ G-Drive" if s["drive"] else "✈️ Telegram", callback_data="triggerMode"),
         Btn("📄 Document" if s["upload_as_doc"] else "🎬 Video", callback_data="triggerUploadMode")],
        [_chip("©️ ᴡᴀᴛᴇʀᴍᴀʀᴋ")],
        [Btn(f"Metadata {_on(s['metadata'])}", callback_data="triggerMetadata"),
         Btn(f"Text {_on(s['watermark'])}", callback_data="triggerVideo")],
        [Btn(f"Motion {_on(s['motion_watermark'])}", callback_data="triggerMotion"),
         Btn(f"Opacity: {s['motion_opacity']}%", callback_data="triggerOpacity")],
        [Btn("⬅️ Back", callback_data="OpenSettings")],
    ]
    await _render(event, "\n".join(lines), rows, lambda: ExtraSettings(event, user_id))

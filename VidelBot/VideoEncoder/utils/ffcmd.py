"""
Videl encoder – ffmpeg command builder (pure functions, no I/O).

Everything the encoder runs is assembled here from the user's settings dict and one
ffprobe result, so it can be unit-tested and verified with a real ffmpeg.

Fixes over the original command line:
  • profile follows codec + bit depth (x264 high / high10, x265 main / main10) – `main` with
    10-bit made every 10-bit encode fail
  • x265 has no `film` tune (only animation / grain …) – it's only passed to x264
  • resolution keeps the aspect ratio (`scale=-2:H`) and never upscales
  • only the real video stream is mapped (cover art / attached pictures are skipped)
  • MP4: HEVC gets the `hvc1` tag, `+faststart`, only text subtitles (mov_text), audio that
    MP4 can't hold is converted to AAC; AVI gets AVI-safe audio
  • Opus: 48 kHz only and a layout libopus accepts
  • all filters go into one -vf chain, subtitle paths are escaped
  • new: quality profiles, target-size mode, deinterlace, denoise, loudness normalisation,
    sample encodes, trims, screenshots
"""
import math
import os

# ─────────────────────────── settings ───────────────────────────
DEFAULTS = dict(
    extensions="MKV", hevc=False, aspect=False, cabac=False, reframe="pass", tune=True, frame="source",
    audio="aac", sample="source", bitrate="source", bits=False, channels="source", drive=False, preset="sf",
    metadata=True, hardsub=False, watermark=False, subtitles=True, resolution="OG", upload_as_doc=False,
    crf=22, resize=False, thumbnail=None, motion_watermark=False, motion_opacity="50",
    # Encoder Pro
    mode="crf", target_mb=0, deinterlace=False, denoise=False, loudnorm=False,
)

PRESETS = {"uf": "ultrafast", "sf": "superfast", "vf": "veryfast", "f": "fast", "m": "medium", "s": "slow"}
PRESET_ORDER = ["uf", "sf", "vf", "f", "m", "s"]
RESOLUTIONS = {"1080": 1080, "720": 720, "576": 576, "480": 480}
FPS = {"ntsc": "ntsc", "pal": "pal", "film": "film", "23.976": "24000/1001", "30": "30", "60": "60"}
AUDIO_CODECS = {"dd": "ac3", "aac": "aac", "vorbis": "libvorbis", "alac": "alac", "opus": "libopus", "copy": "copy"}
CHANNELS = {"1.0": 1, "2.0": 2, "2.1": 3, "5.1": 6, "7.1": 8}
SAMPLE_RATES = {"44.1K": 44100, "48K": 48000}
TARGET_SIZES = [0, 50, 100, 200, 300, 500, 700, 1000, 1500, 1900]     # MB · 0 = off

MP4_AUDIO_OK = {"aac", "ac3", "eac3", "mp3", "alac", "opus"}
AVI_AUDIO_OK = {"mp3", "ac3", "aac", "pcm_s16le"}
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
BITMAP_SUBS = {"hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub", "dvb_subtitle", "xsub"}

# One-tap quality profiles: key → (label, description, settings)
PROFILES = {
    "mobile": ("📱 Mobile", "480p H.264 · small & plays everywhere",
               dict(hevc=False, bits=False, resolution="480", crf=26, preset="vf", audio="aac", bitrate="128",
                    channels="2.0", tune=False, mode="crf")),
    "balanced": ("⚖️ Balanced", "720p HEVC · great size / quality",
                 dict(hevc=True, bits=False, resolution="720", crf=26, preset="vf", audio="aac", bitrate="128",
                      channels="2.0", tune=False, mode="crf")),
    "hq": ("🎞 High quality", "1080p HEVC 10-bit · near source",
           dict(hevc=True, bits=True, resolution="1080", crf=22, preset="f", audio="aac", bitrate="192",
                channels="source", tune=False, mode="crf")),
    "anime": ("🎌 Anime", "720p HEVC 10-bit · animation tune",
              dict(hevc=True, bits=True, resolution="720", crf=24, preset="f", audio="opus", bitrate="128",
                   channels="2.0", tune=True, mode="crf")),
    "tiny": ("💾 Tiny", "480p HEVC · smallest files",
             dict(hevc=True, bits=False, resolution="480", crf=30, preset="vf", audio="opus", bitrate="128",
                  channels="2.0", tune=False, mode="crf")),
    "fast": ("⚡ Fast", "source res H.264 · quickest encode",
             dict(hevc=False, bits=False, resolution="OG", crf=24, preset="uf", audio="copy", bitrate="source",
                  channels="source", tune=False, mode="crf")),
}


def merge(settings: dict | None) -> dict:
    """User document → full settings dict (missing / legacy keys get defaults)."""
    s = dict(DEFAULTS)
    for k, v in (settings or {}).items():
        if k in DEFAULTS and v is not None:
            s[k] = v
    try:
        s["crf"] = max(0, min(51, int(s["crf"])))
    except (TypeError, ValueError):
        s["crf"] = DEFAULTS["crf"]
    try:
        s["target_mb"] = max(0, int(s["target_mb"] or 0))
    except (TypeError, ValueError):
        s["target_mb"] = 0
    return s


def matches_profile(s: dict) -> str | None:
    for key, (_, _, vals) in PROFILES.items():
        if all(s.get(k) == v for k, v in vals.items()):
            return key
    return None


# ─────────────────────────── probe ───────────────────────────
def summarize(probe: dict | None) -> dict:
    """ffprobe JSON (-show_streams -show_format) → what the builder needs."""
    probe = probe or {}
    streams = probe.get("streams") or []
    fmt = probe.get("format") or {}
    videos = [st for st in streams if st.get("codec_type") == "video"
              and not (st.get("disposition") or {}).get("attached_pic")]
    real = [st for st in videos if st.get("codec_name") not in ("mjpeg", "png", "bmp", "gif", "webp")]
    video = (real or videos or [None])[0]
    audio = [st for st in streams if st.get("codec_type") == "audio"]
    subs = [st for st in streams if st.get("codec_type") == "subtitle"]
    try:
        duration = float(fmt.get("duration") or (video or {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    try:
        size = int(fmt.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return {"ok": bool(streams), "video": video, "audio": audio, "subs": subs, "duration": duration, "size": size,
            "height": int((video or {}).get("height") or 0), "width": int((video or {}).get("width") or 0),
            "interlaced": (video or {}).get("field_order") not in (None, "", "progressive", "unknown")}


# ─────────────────────────── helpers ───────────────────────────
def filter_path(path: str) -> str:
    """Escape a file path for `subtitles=filename=…` inside a -vf chain.

    ffmpeg parses it twice: once as a filter option value (escape \\ ' :) and once as part
    of the filtergraph (escape \\ ' [ ] , ;) – both levels are applied here."""
    p = os.path.abspath(path).replace("\\", "/")
    for ch in ("\\", "'", ":"):
        p = p.replace(ch, "\\" + ch)
    for ch in ("\\", "'", "[", "]", ",", ";"):
        p = p.replace(ch, "\\" + ch)
    return p


_PER_CHANNEL_KBPS = {"aac": 70, "libopus": 48, "ac3": 80, "libvorbis": 64, "alac": 500}


def audio_kbps(s: dict, info: dict) -> int:
    """Rough total audio bitrate of the output (for target-size maths)."""
    streams = info.get("audio") or [{}]
    ch_out = CHANNELS.get(s["channels"])
    codec = AUDIO_CODECS.get(s["audio"], "copy")
    total = 0
    for st in streams:
        if codec == "copy":
            try:
                total += int(st.get("bit_rate") or 0) // 1000 or 128
            except (TypeError, ValueError):
                total += 128
        elif str(s["bitrate"]).isdigit() and codec != "alac":
            total += int(s["bitrate"])
        else:
            ch = ch_out or int(st.get("channels") or 2)
            total += _PER_CHANNEL_KBPS.get(codec, 64) * ch
    return total


def target_video_kbps(target_mb: int, duration: float, a_kbps: int) -> int:
    """Video bitrate that makes the whole file ≈ target_mb (3 % container overhead)."""
    if not target_mb or not duration or duration <= 0:
        return 0
    total_kbit = target_mb * 8 * 1024 * 0.97
    return max(150, int(total_kbit / duration - a_kbps))


def estimate_size(s: dict, info: dict) -> int:
    """Very rough output size in bytes (shown before / during an encode)."""
    d = info.get("duration") or 0
    if not d:
        return 0
    if s.get("mode") == "size" and s.get("target_mb"):
        return int(s["target_mb"] * 1024 * 1024)
    h = RESOLUTIONS.get(str(s["resolution"])) or info.get("height") or 720
    h = min(h, info.get("height") or h)
    base = {480: 700, 576: 900, 720: 1400, 1080: 2800}.get(h, int(h * 2.2))     # kbps at crf 23 (x264)
    kbps = base * (0.6 if s["hevc"] else 1.0) * (0.89 ** (s["crf"] - 23))
    return int((kbps + audio_kbps(s, info)) * 1000 / 8 * d)


# ─────────────────────────── command ───────────────────────────
def output_ext(s: dict) -> str:
    return {"MP4": ".mp4", "AVI": ".avi"}.get(str(s["extensions"]).upper(), ".mkv")


def build_command(src: str, out: str, s: dict, info: dict | None = None, *, progress: str | None = None,
                  audio_map=None, subs_file: str | None = None, watermark_file: str | None = None,
                  motion_file: str | None = None, sample: tuple | None = None, metadata_title: str = "Videl",
                  threads: int = 0) -> list:
    """Full ffmpeg argv (output path last)."""
    s = merge(s)
    info = info or summarize(None)
    ext = output_ext(s)
    mp4, avi = ext == ".mp4", ext == ".avi"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if progress:
        cmd += ["-progress", progress, "-nostats"]
    if sample:
        cmd += ["-ss", f"{max(0.0, float(sample[0])):.2f}", "-t", f"{float(sample[1]):.2f}"]
    cmd += ["-i", src]

    # ── streams ──
    has_video = info["video"] is not None or not info["ok"]
    if info["video"] is not None:
        cmd += ["-map", f"0:{info['video'].get('index', 0)}"]
    elif not info["ok"]:
        cmd += ["-map", "0:v:0?"]
    if audio_map:
        for idx in audio_map:
            cmd += ["-map", f"0:{idx}"]
    else:
        cmd += ["-map", "0:a?"]
    cmd += ["-map_chapters", "0", "-map_metadata", "0"]

    # ── video ──
    if has_video:
        x265 = bool(s["hevc"])
        ten = bool(s["bits"])
        cmd += ["-c:v", "libx265" if x265 else "libx264",
                "-pix_fmt", "yuv420p10le" if ten else "yuv420p",
                "-profile:v", ("main10" if ten else "main") if x265 else ("high10" if ten else "high"),
                "-preset", PRESETS.get(s["preset"], "slow")]
        if s["tune"]:
            cmd += ["-tune", "animation"]
        elif not x265:
            cmd += ["-tune", "film"]
        v_kbps = 0
        if s["mode"] == "size" and s["target_mb"] and not sample:
            v_kbps = target_video_kbps(s["target_mb"], info["duration"], audio_kbps(s, info))
        if v_kbps:
            cmd += ["-b:v", f"{v_kbps}k", "-maxrate", f"{int(v_kbps * 1.5)}k", "-bufsize", f"{v_kbps * 2}k"]
        else:
            cmd += ["-crf", str(s["crf"])]
        if not x265:
            cmd += ["-coder", "1" if s["cabac"] else "0"]
            if str(s["reframe"]) in ("4", "8", "16"):
                cmd += ["-refs", str(s["reframe"])]
        else:
            cmd += ["-x265-params", "log-level=error"]
        if s["frame"] in FPS:
            cmd += ["-r", FPS[s["frame"]]]
        if s["aspect"]:
            cmd += ["-aspect", "16:9"]
        if x265 and mp4:
            cmd += ["-tag:v", "hvc1"]

        vf = []
        if s["deinterlace"]:
            vf.append("bwdif=mode=send_frame:parity=auto:deint=interlaced")
        if s["denoise"]:
            vf.append("hqdn3d=1.5:1.5:6:6")
        target_h = RESOLUTIONS.get(str(s["resolution"]))
        if target_h and (not info["height"] or info["height"] > target_h):
            vf.append(f"scale=-2:{target_h}:flags=lanczos")
        if watermark_file:
            vf.append(f"subtitles=filename={filter_path(watermark_file)}")
        if subs_file:
            vf.append(f"subtitles=filename={filter_path(subs_file)}")
        if motion_file:
            vf.append(f"subtitles=filename={filter_path(motion_file)}")
        if vf:
            cmd += ["-vf", ",".join(vf)]

    # ── audio ──
    codec = AUDIO_CODECS.get(s["audio"], "copy")
    src_codecs = {st.get("codec_name") for st in info["audio"]}
    if codec == "copy":
        if s["loudnorm"]:
            codec = "aac"
        elif mp4 and src_codecs - MP4_AUDIO_OK:
            codec = "aac"
        elif avi and src_codecs - AVI_AUDIO_OK:
            codec = "ac3"
    if mp4 and codec == "libvorbis":
        codec = "aac"
    if avi and codec in ("libvorbis", "libopus", "alac"):
        codec = "ac3"
    cmd += ["-c:a", codec]
    if codec != "copy":
        rate = SAMPLE_RATES.get(s["sample"])
        if codec == "libopus":
            cmd += ["-vbr", "on"]
            rate = 48000 if rate else None           # libopus can't do 44.1 kHz
        if s["loudnorm"] and not rate:
            rate = 48000                             # loudnorm would otherwise output 192 kHz
        if rate:
            cmd += ["-ar", str(rate)]
        if codec != "alac" and str(s["bitrate"]).isdigit():
            cmd += ["-b:a", f"{s['bitrate']}k"]
        ch = CHANNELS.get(s["channels"])
        if ch:
            cmd += ["-ac", str(ch)]
        af = []
        if s["loudnorm"]:
            af.append("loudnorm=I=-16:TP=-1.5:LRA=11")
        if codec == "libopus":
            af.append("aformat=channel_layouts=7.1|5.1|stereo|mono")
        if af:
            cmd += ["-af", ",".join(af)]
    if audio_map:                                    # the first chosen track is the only default one
        for i in range(len(audio_map)):
            cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]

    # ── subtitles / attachments ──
    if s["subtitles"] and not s["hardsub"] and not avi and not sample:
        if mp4:
            text = [st for st in info["subs"] if st.get("codec_name") in TEXT_SUBS]
            for st in text:
                cmd += ["-map", f"0:{st.get('index')}"]
            if text:
                cmd += ["-c:s", "mov_text"]
        elif info["subs"] or not info["ok"]:
            cmd += ["-map", "0:s?", "-c:s", "copy", "-map", "0:t?", "-c:t", "copy"]

    # ── metadata / container ──
    if s["metadata"] and metadata_title:
        cmd += ["-metadata", f"title={metadata_title}", "-metadata:s:v", f"title={metadata_title}",
                "-metadata:s:a", f"title={metadata_title}"]
    if mp4:
        cmd += ["-movflags", "+faststart"]
    cmd += ["-max_muxing_queue_size", "4096", "-threads", str(threads)]
    cmd.append(out)
    return cmd


def trim_command(src: str, out: str, start: float, end: float | None) -> list:
    """Fast lossless cut (stream copy, keyframe accurate)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", src, "-map", "0", "-c", "copy", "-avoid_negative_ts", "make_zero"]
    if out.lower().endswith(".mp4"):
        cmd += ["-movflags", "+faststart"]
    return cmd + [out]


def screenshot_times(duration: float, n: int) -> list:
    """n evenly spread timestamps, skipping the very start / end."""
    n = max(1, min(10, int(n)))
    if not duration or duration <= 0:
        return [0.0] * 1
    step = duration / (n + 1)
    return [round(step * (i + 1), 2) for i in range(n)]


def screenshot_command(src: str, out: str, at: float, width: int = 1280) -> list:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{at:.2f}", "-i", src,
            "-frames:v", "1", "-vf", f"scale='min({width},iw)':-2", "-q:v", "3", out]


def sample_window(duration: float, length: int = 30) -> tuple:
    """(start, length) – a clip from the middle of the video."""
    length = max(5, min(120, int(length)))
    if not duration or duration <= length:
        return 0.0, float(duration or length)
    return max(0.0, duration / 2 - length / 2), float(length)


def parse_timestamp(text: str) -> float | None:
    """'90', '1:30', '01:02:03.5' → seconds."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        parts = [float(p) for p in text.split(":")]
    except ValueError:
        return None
    if len(parts) > 3 or any(p < 0 for p in parts):
        return None
    sec = 0.0
    for p in parts:
        sec = sec * 60 + p
    return sec


def fmt_ts(sec: float) -> str:
    sec = max(0, int(sec or 0))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def describe(s: dict) -> dict:
    """Human labels for the settings (menus, /vset, summaries)."""
    s = merge(s)
    res = {"OG": "Source"}.get(s["resolution"], f"{s['resolution']}p")
    return {
        "codec": ("H.265" if s["hevc"] else "H.264") + (" 10-bit" if s["bits"] else ""),
        "quality": (f"≈ {s['target_mb']} MB" if s["mode"] == "size" and s["target_mb"] else f"CRF {s['crf']}"),
        "resolution": res,
        "preset": PRESETS.get(s["preset"], "slow").capitalize(),
        "tune": "Animation" if s["tune"] else ("Film" if not s["hevc"] else "None"),
        "fps": {"source": "Source", "23.976": "23.976"}.get(s["frame"], str(s["frame"]).upper()),
        "container": str(s["extensions"]).upper(),
        "audio": {"dd": "AC3", "copy": "Source (copy)"}.get(s["audio"], str(s["audio"]).upper()),
        "audio_bitrate": "Source" if not str(s["bitrate"]).isdigit() else f"{s['bitrate']}k",
        "channels": {"1.0": "Mono", "2.0": "Stereo", "source": "Source"}.get(s["channels"], s["channels"]),
        "sample_rate": {"44.1K": "44.1 kHz", "48K": "48 kHz"}.get(s["sample"], "Source"),
        "filters": " · ".join(x for x, on in (("Deinterlace", s["deinterlace"]), ("Denoise", s["denoise"]),
                                              ("Loudnorm", s["loudnorm"])) if on) or "None",
        "subtitles": "Hardsub" if s["hardsub"] else ("Copy" if s["subtitles"] else "Off"),
        "upload": ("G-Drive" if s["drive"] else "Telegram") + (" · Document" if s["upload_as_doc"] else " · Video"),
        "watermark": " · ".join(x for x, on in (("Text", s["watermark"]), ("Motion", s["motion_watermark"]),
                                                ("Metadata", s["metadata"])) if on) or "Off",
    }


def ceil_div(a, b):
    return int(math.ceil(a / b)) if b else 0

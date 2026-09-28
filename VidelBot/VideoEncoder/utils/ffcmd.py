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
    # Phase 16
    dedup=False,
    # Phase 17
    size_guard=True,
    # Phase 15
    av1=False, twopass=False, hw=True, logo=False, logo_id=None, wm_text="", wm_pos="br", wm_size="m",
    wm_opacity="75",
)

# ─────────────────────────── codecs / encoders ───────────────────────────
SOFTWARE = {"h264": "libx264", "hevc": "libx265", "av1": "libsvtav1"}
AV1_SOFTWARE = ("libsvtav1", "libaom-av1")
HW_ENCODERS = {
    "nvenc": {"h264": "h264_nvenc", "hevc": "hevc_nvenc", "av1": "av1_nvenc"},
    "qsv": {"h264": "h264_qsv", "hevc": "hevc_qsv", "av1": "av1_qsv"},
    "vaapi": {"h264": "h264_vaapi", "hevc": "hevc_vaapi", "av1": "av1_vaapi"},
}
SVT_PRESETS = {"uf": 12, "sf": 10, "vf": 9, "f": 8, "m": 6, "s": 5}
AOM_CPU = {"uf": 8, "sf": 8, "vf": 7, "f": 6, "m": 5, "s": 4}
NV_PRESETS = {"uf": "p1", "sf": "p2", "vf": "p3", "f": "p4", "m": "p5", "s": "p6"}
QSV_PRESETS = {"uf": "veryfast", "sf": "veryfast", "vf": "faster", "f": "fast", "m": "medium", "s": "slow"}
WM_POSITIONS = ["br", "bl", "tr", "tl", "c"]
WM_POS_LABEL = {"br": "↘️ Bottom right", "bl": "↙️ Bottom left", "tr": "↗️ Top right", "tl": "↖️ Top left", "c": "⏺ Centre"}
WM_SIZES = {"s": 0.06, "m": 0.09, "l": 0.13}
WM_OPACITY = ["100", "75", "50", "30"]
_OVERLAY_XY = {"br": "W-w-W*0.02:H-h-H*0.03", "bl": "W*0.02:H-h-H*0.03", "tr": "W-w-W*0.02:H*0.03",
               "tl": "W*0.02:H*0.03", "c": "(W-w)/2:(H-h)/2"}
_ASS_ALIGN = {"br": 3, "bl": 1, "tr": 9, "tl": 7, "c": 5}


def video_codec(s: dict) -> str:
    return "av1" if s.get("av1") else ("hevc" if s.get("hevc") else "h264")


def av1_crf(crf: int) -> int:
    """x264-style CRF → a similar-looking AV1 CRF (0-63 scale)."""
    return max(10, min(63, round(int(crf) * 1.3 + 3)))


def hw_kind(encoder: str | None) -> str | None:
    for kind in HW_ENCODERS:
        if encoder and encoder.endswith("_" + kind):
            return kind
    return None


def twopass_applies(s: dict, encoder: str | None = None, sample=None) -> bool:
    """2-pass only makes sense for target-size encodes on software x264 / x265 / libaom."""
    s = merge(s)
    enc = encoder or SOFTWARE[video_codec(s)]
    return bool(s["twopass"] and s["mode"] == "size" and s["target_mb"] and not sample
                and enc in ("libx264", "libx265", "libaom-av1"))

PRESETS = {"uf": "ultrafast", "sf": "superfast", "vf": "veryfast", "f": "fast", "m": "medium", "s": "slow"}
PRESET_ORDER = ["uf", "sf", "vf", "f", "m", "s"]
RESOLUTIONS = {"1080": 1080, "720": 720, "576": 576, "480": 480, "360": 360}
# Downscaling scaler: bicubic is ~9 % faster than lanczos and looks the same after a downscale at these
# presets; the slow presets (quality first) keep lanczos.
FAST_SCALER_PRESETS = {"uf", "sf", "vf", "f"}
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
    kbps = base * {"av1": 0.5, "hevc": 0.6, "h264": 1.0}[video_codec(s)] * (0.89 ** (s["crf"] - 23))
    return int((kbps + audio_kbps(s, info)) * 1000 / 8 * d)


# ─────────────────────────── command ───────────────────────────
def output_ext(s: dict) -> str:
    ext = {"MP4": ".mp4", "AVI": ".avi"}.get(str(s["extensions"]).upper(), ".mkv")
    if ext == ".avi" and s.get("av1"):
        return ".mkv"                      # AVI can't carry AV1
    return ext


def logo_height(s: dict, info: dict) -> int:
    """Logo height in px (even) – a share of the OUTPUT picture height."""
    s = merge(s)
    h = info.get("height") or 720
    target_h = RESOLUTIONS.get(str(s["resolution"]))
    if target_h and h > target_h:
        h = target_h
    px = int(round(h * WM_SIZES.get(s["wm_size"], 0.09)))
    return max(16, px - px % 2)


def text_watermark_ass(text: str, pos: str = "br", size: str = "m", opacity: str = "75") -> str:
    """A static ASS subtitle that draws the user's watermark text."""
    alpha = {"100": "00", "75": "40", "50": "80", "30": "B3"}.get(str(opacity), "40")
    font = {"s": 34, "m": 46, "l": 62}.get(size, 46)
    clean = str(text or "").replace("\n", " ").replace("{", "(").replace("}", ")")[:60]
    return ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\nWrapStyle: 2\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: WM,Arial,{font},&H{alpha}FFFFFF,&H000000FF,&H{alpha}000000,&H{alpha}000000,1,0,0,0,100,100,0,0,1,2,1,"
            f"{_ASS_ALIGN.get(pos, 3)},40,40,30,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 0,0:00:00.00,9:59:59.99,WM,,0,0,0,,{clean}\n")


def build_command(src: str, out: str, s: dict, info: dict | None = None, *, progress: str | None = None,
                  audio_map=None, subs_file: str | None = None, watermark_file: str | None = None,
                  motion_file: str | None = None, sample: tuple | None = None, metadata_title: str = "Videl",
                  threads: int = 0, encoder: str | None = None, logo_file: str | None = None,
                  text_wm_file: str | None = None, pass_no: int | None = None, passlog: str | None = None,
                  vaapi_device: str = "/dev/dri/renderD128", fps_flag: str | None = "-fps_mode") -> list:
    """Full ffmpeg argv (output path last).

    encoder   exact ffmpeg video encoder (from hw.pick_encoder); default = software for the codec
    logo_file PNG/JPG overlaid with -filter_complex (position / size / opacity from the settings)
    pass_no   1 → analysis pass (no audio, null muxer) · 2 → final pass · None → single pass
    fps_flag  "-fps_mode" (ffmpeg ≥ 5.1) or "-vsync" (4.x) – see hw.fps_mode_flag(); None → leave ffmpeg's default

    Frame timing: with the FPS setting on "source" the frames keep their own timestamps (vfr). ffmpeg's
    default for MP4/MKV is CFR, which *duplicates* frames to fill every gap of a variable-frame-rate
    source (phone clips, screen recordings, many web rips) – thousands of extra frames to encode.
    """
    s = merge(s)
    info = info or summarize(None)
    ext = output_ext(s)
    mp4, avi = ext == ".mp4", ext == ".avi"
    codec_kind = video_codec(s)
    enc = encoder or SOFTWARE[codec_kind]
    hw = hw_kind(enc)
    first_pass = pass_no == 1
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y"]
    if hw == "vaapi":
        cmd += ["-vaapi_device", vaapi_device]
    if progress:
        cmd += ["-progress", progress, "-nostats"]
    if sample:
        cmd += ["-ss", f"{max(0.0, float(sample[0])):.2f}", "-t", f"{float(sample[1]):.2f}"]
    if hw == "nvenc" and not first_pass:
        # decode on the GPU too (frames come back to RAM, so every CPU filter still works); ffmpeg falls back
        # to software decoding by itself when the codec / GPU can't do it
        cmd += ["-hwaccel", "cuda"]
    if threads:
        cmd += ["-threads", str(threads)]            # decoder share too – parallel workers split the cores fairly
    cmd += ["-i", src]
    if logo_file:
        cmd += ["-i", logo_file]

    # ── streams ──
    has_video = info["video"] is not None or not info["ok"]
    v_in = f"0:{info['video'].get('index', 0)}" if info["video"] is not None else "0:v:0"

    # ── video filters ──
    vf = []
    if has_video:
        # Order = speed: deinterlace (must see full fields) → downscale → everything else on the small
        # picture. Denoising after the downscale measured ~16 % faster, dedup after it ~4 %.
        if s["deinterlace"]:
            vf.append("bwdif=mode=send_frame:parity=auto:deint=interlaced")
        target_h = RESOLUTIONS.get(str(s["resolution"]))
        if target_h and (not info["height"] or info["height"] > target_h):
            scaler = "bicubic" if s["preset"] in FAST_SCALER_PRESETS else "lanczos"
            vf.append(f"scale=-2:{target_h}:flags={scaler}")
        if s["dedup"] and not avi:                   # drop repeated frames before the costly filters
            vf.append("mpdecimate")
        if s["denoise"]:
            vf.append("hqdn3d=1.5:1.5:6:6")
        for f in (watermark_file, text_wm_file, subs_file, motion_file):
            if f:
                vf.append(f"subtitles=filename={filter_path(f)}")
    ten = bool(s["bits"])
    hw_tail = []
    if hw == "vaapi":
        hw_tail = [f"format={'p010' if ten else 'nv12'}", "hwupload"]

    if has_video and logo_file:
        alpha = int(s["wm_opacity"]) / 100 if str(s["wm_opacity"]).isdigit() else 0.75
        chain = ",".join(vf) or "null"
        graph = (f"[{v_in}]{chain}[base];"
                 f"[1:v]format=rgba,scale=-2:{logo_height(s, info)},colorchannelmixer=aa={alpha:.2f}[lg];"
                 f"[base][lg]overlay={_OVERLAY_XY.get(s['wm_pos'], _OVERLAY_XY['br'])}:format=auto"
                 + ("," + ",".join(hw_tail) if hw_tail else "") + "[vout]")
        cmd += ["-filter_complex", graph, "-map", "[vout]"]
    elif info["video"] is not None:
        cmd += ["-map", v_in]
    elif not info["ok"]:
        cmd += ["-map", "0:v:0?"]
    if not first_pass:
        if audio_map:
            for idx in audio_map:
                cmd += ["-map", f"0:{idx}"]
        else:
            cmd += ["-map", "0:a?"]
        cmd += ["-map_chapters", "0", "-map_metadata", "0"]

    # ── video ──
    if has_video:
        preset = s["preset"] if s["preset"] in PRESETS else "s"
        v_kbps = 0
        if s["mode"] == "size" and s["target_mb"] and not sample:
            v_kbps = target_video_kbps(s["target_mb"], info["duration"], audio_kbps(s, info))
        rate = (["-b:v", f"{v_kbps}k", "-maxrate", f"{int(v_kbps * 1.5)}k", "-bufsize", f"{v_kbps * 2}k"]
                if v_kbps else None)
        if enc in ("libx264", "libx265"):
            x265 = enc == "libx265"
            cmd += ["-c:v", enc, "-pix_fmt", "yuv420p10le" if ten else "yuv420p",
                    "-profile:v", ("main10" if ten else "main") if x265 else ("high10" if ten else "high"),
                    "-preset", PRESETS[preset]]
            if s["tune"]:
                cmd += ["-tune", "animation"]
            elif not x265:
                cmd += ["-tune", "film"]
            if pass_no and v_kbps:
                cmd += ["-b:v", f"{v_kbps}k"]          # 2-pass: plain ABR, the passes do the rest
            elif rate:
                cmd += rate
            else:
                cmd += ["-crf", str(s["crf"])]
            if not x265:
                cmd += ["-coder", "1" if s["cabac"] else "0"]
                if str(s["reframe"]) in ("4", "8", "16"):
                    cmd += ["-refs", str(s["reframe"])]
                if pass_no:
                    cmd += ["-pass", str(pass_no)] + (["-passlogfile", passlog] if passlog else [])
            else:
                params = "log-level=error"
                if pass_no:
                    params += f":pass={pass_no}" + (f":stats={passlog}.x265" if passlog else "")
                cmd += ["-x265-params", params]
            if x265 and mp4:
                cmd += ["-tag:v", "hvc1"]
        elif enc in AV1_SOFTWARE:
            cmd += ["-c:v", enc, "-pix_fmt", "yuv420p10le" if ten else "yuv420p"]
            if enc == "libsvtav1":
                cmd += ["-preset", str(SVT_PRESETS[preset])]
                cmd += ["-b:v", f"{v_kbps}k"] if v_kbps else ["-crf", str(av1_crf(s["crf"]))]
                cmd += ["-svtav1-params", "tune=0" + (":film-grain=8" if s["denoise"] else "")]
            else:
                cmd += ["-cpu-used", str(AOM_CPU[preset]), "-row-mt", "1", "-tiles", "2x2"]
                cmd += ["-b:v", f"{v_kbps}k"] if v_kbps else ["-crf", str(av1_crf(s["crf"])), "-b:v", "0"]
                if pass_no:
                    cmd += ["-pass", str(pass_no)] + (["-passlogfile", passlog] if passlog else [])
            cmd += ["-g", "240"]
        elif hw == "nvenc":
            cmd += ["-c:v", enc, "-pix_fmt", "p010le" if ten else "yuv420p", "-preset", NV_PRESETS[preset],
                    "-tune", "hq", "-rc", "vbr", "-spatial-aq", "1"]
            if codec_kind == "hevc":
                cmd += ["-profile:v", "main10" if ten else "main"]
            elif codec_kind == "h264":
                cmd += ["-profile:v", "high"]
            if rate:
                cmd += rate + (["-multipass", "fullres"] if s["twopass"] else [])
            else:
                cmd += ["-cq", str(av1_crf(s["crf"]) if codec_kind == "av1" else s["crf"]), "-b:v", "0"]
            if codec_kind == "hevc" and mp4:
                cmd += ["-tag:v", "hvc1"]
        elif hw == "qsv":
            cmd += ["-c:v", enc, "-pix_fmt", "p010le" if ten else "nv12", "-preset", QSV_PRESETS[preset]]
            if codec_kind == "hevc":
                cmd += ["-profile:v", "main10" if ten else "main"]
            cmd += rate if rate else ["-global_quality", str(s["crf"])]
            if codec_kind == "hevc" and mp4:
                cmd += ["-tag:v", "hvc1"]
        elif hw == "vaapi":
            cmd += ["-c:v", enc]
            if codec_kind == "hevc":
                cmd += ["-profile:v", "main10" if ten else "main"]
            cmd += rate if rate else ["-rc_mode", "CQP", "-qp", str(s["crf"])]
            if codec_kind == "hevc" and mp4:
                cmd += ["-tag:v", "hvc1"]
        else:                                        # unknown encoder name – let ffmpeg pick defaults
            cmd += ["-c:v", enc]
            cmd += rate if rate else []
        if s["dedup"] and not avi:                   # a forced -r would put the dropped frames right back
            cmd += [fps_flag or "-fps_mode", "vfr"]
        elif s["frame"] in FPS:
            cmd += ["-r", FPS[s["frame"]]]
        elif fps_flag and not avi:                   # AVI has no timestamps – it stays constant-rate
            cmd += [fps_flag, "vfr"]
        if s["aspect"]:
            cmd += ["-aspect", "16:9"]
        if not logo_file:
            chain = vf + hw_tail
            if chain:
                cmd += ["-vf", ",".join(chain)]

    if first_pass:                                   # analysis pass: video only, thrown away
        return cmd + ["-an", "-sn", "-dn", "-threads", str(threads), "-f", "null", os.devnull]

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
    if audio_copy_ok(s, info, codec, audio_map):
        codec = "copy"                               # already what was asked for – re-encoding only costs time
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


_AUDIO_NAME = {"ac3": "ac3", "aac": "aac", "libvorbis": "vorbis", "alac": "alac", "libopus": "opus"}


def audio_copy_ok(s: dict, info: dict | None, codec: str, audio_map=None) -> bool:
    """Smart audio copy: the source audio already *is* the requested codec and nothing about it should change
    (bitrate / sample rate on "source", channel count already right, no loudness filter) → copy instead of
    decoding + re-encoding it. Lossless, and the native AAC encoder alone takes ~32 s of CPU per 10 minutes
    of 5.1 – minutes per movie that were stolen from the video encode."""
    s = merge(s)
    info = info or {}
    want = _AUDIO_NAME.get(codec)
    streams = info.get("audio") or []
    if not want or not info.get("ok") or not streams or s["loudnorm"] or str(s["bitrate"]).isdigit():
        return False
    if audio_map:
        chosen = {int(i) for i in audio_map if str(i).lstrip("-").isdigit()}
        streams = [st for st in streams if st.get("index") in chosen]
        if not streams:
            return False
    ch, rate = CHANNELS.get(s["channels"]), SAMPLE_RATES.get(s["sample"])
    for st in streams:
        if st.get("codec_name") != want:
            return False
        if ch and int(st.get("channels") or 0) != ch:
            return False
        if rate and str(st.get("sample_rate") or "") != str(rate):
            return False
    return True
_MP4_VIDEO_OK = {"h264", "hevc", "av1", "mpeg4"}


def guard_applies(s: dict, info: dict | None) -> bool:
    """May the size guard swap a bigger encode for a lossless remux of the source?

    Only when the user asked for nothing but "the same codec, smaller": no downscale, burn-in, watermark,
    filter, fps / aspect / bit-depth change, and the source's audio already is what they asked for.
    A codec switch (e.g. HEVC → H.264 for an old TV) never qualifies – there the bigger file is the point.
    """
    s = merge(s)
    info = info or {}
    v = info.get("video") or {}
    if not info.get("ok") or not v or not s["size_guard"]:
        return False
    if (v.get("codec_name") or "") != video_codec(s):
        return False
    transforms = (s["resolution"] != "OG", s["hardsub"], s["watermark"], s["motion_watermark"],
                  bool(s["logo"] and s["logo_id"]), s["deinterlace"], s["denoise"], s["dedup"], s["aspect"],
                  s["frame"] in FPS, s["loudnorm"], s["channels"] != "source", s["sample"] != "source", s["bits"])
    if any(transforms):
        return False
    ext = output_ext(s)
    if ext == ".avi":
        return False
    audio = {st.get("codec_name") for st in info.get("audio") or []}
    want = AUDIO_CODECS.get(s["audio"], "copy")
    if want != "copy" and audio and audio != {_AUDIO_NAME.get(want, want)}:
        return False
    if ext == ".mp4" and ((v.get("codec_name") not in _MP4_VIDEO_OK) or (audio - MP4_AUDIO_OK)):
        return False
    return True


def remux_command(src: str, out: str, s: dict, info: dict | None = None) -> list:
    """Lossless copy of the source into the output container (video, audio, chapters, subtitles if kept)."""
    s = merge(s)
    info = info or {}
    mp4 = out.lower().endswith(".mp4")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src,
           "-map", "0:V", "-map", "0:a?", "-map_chapters", "0", "-map_metadata", "0"]
    sub_codec = []
    if s["subtitles"]:
        if mp4:
            text = [st for st in info.get("subs") or [] if st.get("codec_name") in TEXT_SUBS]
            for st in text:
                cmd += ["-map", f"0:{st.get('index')}"]
            sub_codec = ["-c:s", "mov_text"] if text else []
        else:
            cmd += ["-map", "0:s?", "-map", "0:t?"]
    cmd += ["-c", "copy"] + sub_codec
    if mp4:
        cmd += ["-movflags", "+faststart"]
    return cmd + [out]


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
        "codec": {"av1": "AV1", "hevc": "H.265", "h264": "H.264"}[video_codec(s)] + (" 10-bit" if s["bits"] else ""),
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
                                              ("Dedup", s["dedup"]), ("Loudnorm", s["loudnorm"])) if on)
                   or "None",
        "subtitles": "Hardsub" if s["hardsub"] else ("Copy" if s["subtitles"] else "Off"),
        "upload": ("G-Drive" if s["drive"] else "Telegram") + (" · Document" if s["upload_as_doc"] else " · Video"),
        "watermark": " · ".join(x for x, on in (("Text", s["watermark"]), ("Logo", s["logo"] and s["logo_id"]),
                                                ("Motion", s["motion_watermark"]),
                                                ("Metadata", s["metadata"])) if on) or "Off",
        "rate": ("2-pass" if s["twopass"] and s["mode"] == "size" else "1-pass"),
    }


def ceil_div(a, b):
    return int(math.ceil(a / b)) if b else 0


# ─────────────────────────── Phase 15 tools ───────────────────────────
SUB_EXT = {".srt": "subrip", ".ass": "ass", ".ssa": "ass", ".vtt": "webvtt"}
AUDIO_EXT = {".mp3", ".m4a", ".aac", ".ac3", ".eac3", ".opus", ".ogg", ".flac", ".wav", ".mka", ".dts", ".wma"}
CONVERT_FORMATS = {
    "mp3": (".mp3", ["-c:a", "libmp3lame", "-q:a", "2"]),
    "m4a": (".m4a", ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]),
    "opus": (".opus", ["-c:a", "libopus", "-b:a", "128k", "-vbr", "on", "-ar", "48000",
                       "-af", "aformat=channel_layouts=7.1|5.1|stereo|mono"]),
    "flac": (".flac", ["-c:a", "flac"]),
    "wav": (".wav", ["-c:a", "pcm_s16le"]),
}
GIF_MAX = 20


def track_kind(filename: str, mime: str = "") -> str | None:
    """'sub' / 'audio' / None for a file somebody wants to add to a video."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in SUB_EXT:
        return "sub"
    if ext in AUDIO_EXT or str(mime or "").startswith("audio/"):
        return "audio"
    return None


def mux_ext(src: str) -> str:
    ext = os.path.splitext(src)[1].lower()
    return ext if ext in (".mkv", ".mp4") else ".mkv"


def mux_command(video: str, track: str, out: str, kind: str, info: dict | None = None, track_codec: str = "",
                title: str = "Videl") -> list:
    """Add one subtitle / audio track to a video without re-encoding the picture."""
    info = info or summarize(None)
    mp4 = out.lower().endswith(".mp4")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", video]
    cmd += ["-i", track, "-map", "0:v?", "-map", "0:a?", "-map", "0:s?"]
    if not mp4:
        cmd += ["-map", "0:t?"]
    cmd += ["-map", "1:0", "-c", "copy"]
    n_audio, n_subs = len(info.get("audio") or []), len(info.get("subs") or [])
    if kind == "sub":
        if mp4:
            cmd += ["-c:s", "mov_text"]
        elif track.lower().endswith(".vtt"):
            cmd += [f"-c:s:{n_subs}", "srt"]
        cmd += [f"-metadata:s:s:{n_subs}", f"title={title}", f"-disposition:s:{n_subs}", "default"]
    else:
        if mp4 and track_codec not in MP4_AUDIO_OK:
            cmd += [f"-c:a:{n_audio}", "aac", f"-b:a:{n_audio}", "192k"]
        cmd += [f"-metadata:s:a:{n_audio}", f"title={title}"]
    if mp4:
        cmd += ["-movflags", "+faststart"]
    return cmd + ["-max_muxing_queue_size", "4096", out]


def can_concat_copy(infos: list) -> bool:
    """True when every part has the same video codec / size and the same audio codec (lossless join)."""
    if len(infos) < 2 or not all(i.get("video") for i in infos):
        return False
    def key(i):
        v = i["video"]
        a = (i.get("audio") or [{}])[0]
        return (v.get("codec_name"), v.get("width"), v.get("height"), v.get("pix_fmt"),
                a.get("codec_name"), a.get("sample_rate"), a.get("channels"), bool(i.get("audio")))
    return len({key(i) for i in infos}) == 1


def concat_list(paths: list) -> str:
    """Contents of an ffmpeg concat-demuxer list file."""
    def q(p):
        return "'" + os.path.abspath(p).replace("'", "'\\''") + "'"
    return "ffconcat version 1.0\n" + "".join(f"file {q(p)}\n" for p in paths)


def concat_command(list_file: str, out: str) -> list:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-f", "concat", "-safe", "0",
           "-i", list_file, "-map", "0:v", "-map", "0:a?", "-c", "copy"]
    if out.lower().endswith(".mp4"):
        cmd += ["-movflags", "+faststart"]
    return cmd + [out]


def merge_target(infos: list) -> tuple:
    """(width, height, fps) every part is normalised to – the first video decides."""
    v = infos[0].get("video") or {}
    w, h = int(v.get("width") or 1280), int(v.get("height") or 720)
    w, h = w - w % 2, h - h % 2
    fps = "30"
    rate = str(v.get("avg_frame_rate") or v.get("r_frame_rate") or "")
    try:
        num, den = (rate.split("/") + ["1"])[:2]
        value = float(num) / float(den or 1)
        if 5 <= value <= 120:
            fps = f"{value:.3f}".rstrip("0").rstrip(".")
    except (ValueError, ZeroDivisionError):
        pass
    return w, h, fps


def normalize_command(src: str, out: str, info: dict, width: int, height: int, fps: str) -> list:
    """Re-encode one merge part to a common size / fps / codec (silent audio added when missing)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src]
    has_audio = bool(info.get("audio"))
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    vidx = (info.get("video") or {}).get("index", 0)
    cmd += ["-map", f"0:{vidx}", "-map", "0:a:0" if has_audio else "1:a:0"]
    if not has_audio:
        cmd += ["-shortest"]
    cmd += ["-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                   f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-max_muxing_queue_size", "4096", out]
    return cmd


def audio_extract_command(src: str, out: str, fmt: str, stream: int | None = None) -> list:
    _ext, args = CONVERT_FORMATS[fmt]
    return (["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src, "-vn", "-sn", "-dn",
             "-map", f"0:{stream}" if stream is not None else "0:a:0", "-map_metadata", "0"] + args + [out])


def gif_window(duration: float, start: float | None, length: float | None) -> tuple:
    length = max(1.0, min(float(GIF_MAX), float(length or 6)))
    if start is None:
        start = max(0.0, (duration or 0) / 2 - length / 2)
    if duration and start >= duration:
        start = max(0.0, duration - length)
    return float(start), length


def gif_command(src: str, out: str, start: float, length: float, width: int = 480, fps: int = 12) -> list:
    graph = (f"fps={fps},scale='min({width},iw)':-2:flags=lanczos,split[a][b];"
             "[a]palettegen=stats_mode=diff:max_colors=192[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-ss", f"{start:.2f}", "-t",
            f"{length:.2f}", "-i", src, "-filter_complex", graph, "-loop", "0", out]


def split_plan(size: int, duration: float, limit: int) -> float:
    """segment_time (seconds) so each part stays under `limit` bytes (5 % safety for keyframes)."""
    if not size or not duration or size <= limit:
        return 0.0
    return max(10.0, duration * limit / size * 0.95)


def split_command(src: str, pattern: str, segment_time: float) -> list:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src, "-map", "0", "-c", "copy",
            "-f", "segment", "-segment_time", f"{segment_time:.2f}", "-reset_timestamps", "1", pattern]

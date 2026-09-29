"""
🗜 File compressor (/compress) – pure logic: options, the settings override for the encoder, size estimates,
and the small-caps cards + keyboard. No I/O here, so every piece is unit-tested.

Speed first – the numbers behind the choices (2-vCPU box, 1080p → 720p):
  • H.264 `veryfast`: ~18 % slower than `superfast` but files ~2.4× smaller at the same CRF (superfast turns off
    mb-tree / psy) – far less to upload, so end-to-end it's the fastest way to a small file.
  • H.265 `superfast`: ~2× the time of H.264 veryfast for a further ~10–30 % saving → offered, never forced.
  • CABAC on (the encoder's legacy default is CAVLC ≈ 10 % bigger for no speed gain), 8-bit, no tune.
  • bicubic downscale, audio copied when it already matches, decoder threads shared across workers
    (see ffcmd.build_command).
"""
import html
import re

from core.style import hdr, hint, quote, row, sc

RES = ["1080", "720", "480", "360"]
CODECS = {"h264": ("⚡", "H.264", "fast · plays everywhere"), "hevc": ("📦", "H.265", "smaller · slower")}
LEVELS = {"light": ("🟢", "Light"), "balanced": ("🟡", "Balanced"), "strong": ("🔴", "Strong")}
CRF = {"h264": {"light": 23, "balanced": 26, "strong": 30}, "hevc": {"light": 25, "balanced": 28, "strong": 32}}
PRESET = {"h264": "vf", "hevc": "sf"}
AUDIO = ["128", "96", "64", "copy"]                    # AAC kbps (stereo) · copy = keep the original track(s)
TARGETS = [0, 10, 25, 50, 100, 200, 500, 1000]         # MB · 0 = off (quality mode)
FORMATS = ["MP4", "MKV"]
DEFAULT = {"res": "720", "codec": "h264", "level": "balanced", "audio": "128", "target": 0, "fmt": "MP4",
           "compare": True, "sdr": True, "cut": None, "album": False}
# Per-task keys: never remembered as the user's "last choice".
TASK_KEYS = ("cut", "album")
ALBUM_MAX = 10                  # Telegram albums hold up to 10 files
AUTO_MIN_MB = 5                 # auto-compress ignores files smaller than this (nothing worth saving)

# One-tap presets (row under the quality buttons).
QUICK = {
    "mobile": ("📱", "Mobile", {"res": "480", "codec": "h264", "level": "strong", "audio": "64", "target": 0, "fmt": "MP4"}),
    "10": ("💬", "10 MB", {"res": "360", "codec": "h264", "level": "balanced", "audio": "64", "target": 10, "fmt": "MP4"}),
    "25": ("📧", "25 MB", {"res": "480", "codec": "h264", "level": "balanced", "audio": "96", "target": 25, "fmt": "MP4"}),
}
MIN_VIDEO_KBPS = 150            # ffcmd.target_video_kbps never goes lower – below it a target can't be met
LIGHT_KBPS = {1080: 1500, 720: 800, 480: 420, 360: 260, 240: 160}   # sources under this are already lean
# Size watch: stop an encode that is heading for a file bigger than the source.
WATCH_FROM = 0.15               # judge only after 15 % of the video (the first seconds are noisy)
WATCH_MARGIN = 1.15             # …and only when it's clearly bigger (projected ≥ 115 % of the source)
WATCH_TICKS = 2                 # two progress ticks in a row

# Typical H.264 veryfast video bitrate at "balanced" (kbps) – only for the rough size estimate on the card.
_BASE_KBPS = {1080: 2400, 720: 1200, 480: 620, 360: 380, 240: 230}
_LEVEL_X = {"light": 1.45, "balanced": 1.0, "strong": 0.6}
_HEVC_X = 0.7
LOCK = "🔒"


def normalize(opts: dict | None) -> dict:
    """Any stored / typed dict → a complete, valid options dict."""
    o = dict(DEFAULT)
    for k, v in (opts or {}).items():
        if k in o and v is not None:
            o[k] = v
    o["res"] = str(o["res"]).rstrip("pP")
    if o["res"] not in RES:
        o["res"] = DEFAULT["res"]
    if o["codec"] not in CODECS:
        o["codec"] = DEFAULT["codec"]
    if o["level"] not in LEVELS:
        o["level"] = DEFAULT["level"]
    if str(o["audio"]) not in AUDIO:
        o["audio"] = DEFAULT["audio"]
    o["audio"] = str(o["audio"])
    try:
        o["target"] = int(o["target"] or 0)
    except (TypeError, ValueError):
        o["target"] = 0
    if o["target"] < 0 or o["target"] > 4000:
        o["target"] = 0
    o["fmt"] = str(o["fmt"]).upper()
    if o["fmt"] not in FORMATS:
        o["fmt"] = DEFAULT["fmt"]
    o["compare"], o["sdr"], o["album"] = bool(o["compare"]), bool(o["sdr"]), bool(o["album"])
    o["cut"] = _clean_cut(o["cut"])
    return o


def _clean_cut(cut):
    """[start, end] seconds (end 0 = to the end) or None."""
    try:
        start, end = float(cut[0] or 0), float(cut[1] or 0)
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if start < 0 or end < 0 or (end and end <= start) or (not start and not end):
        return None
    return [round(start, 2), round(end, 2)]


def remembered(opts: dict) -> dict:
    """What is saved as the user's last choice (per-task keys dropped)."""
    o = normalize(opts)
    for k in TASK_KEYS:
        o[k] = DEFAULT[k]
    return o


_ARG_RES = re.compile(r"^(1080|720|480|360)p?$", re.I)
_ARG_MB = re.compile(r"^(\d{1,4})\s*(mb|m)$", re.I)
_ARG_GB = re.compile(r"^(\d(?:\.\d+)?)\s*(gb|g)$", re.I)
_TS = r"\d{1,3}(?::\d{1,2}){0,2}(?:\.\d+)?"
_ARG_CUT = re.compile(rf"^({_TS})?\s*[-–—]\s*({_TS})?$")


def _secs(ts: str | None) -> float:
    if not ts:
        return 0.0
    sec = 0.0
    for p in ts.split(":"):
        sec = sec * 60 + float(p)
    return sec


def parse_cut(word: str):
    """'10:00-25:00' → [600, 1500] · '1:30-' → [90, 0] (to the end) · '-5:00' → [0, 300] · else None."""
    m = _ARG_CUT.match((word or "").strip())
    if not m or not (m.group(1) or m.group(2)):
        return None
    return _clean_cut([_secs(m.group(1)), _secs(m.group(2))])


def parse_args(text: str) -> dict:
    """`/compress 480 strong hevc 50mb mkv 64k` → {res, level, codec, target, fmt, audio}. Unknown words are ignored."""
    out: dict = {}
    for w in (text or "").split()[1:]:
        lw = w.lower().strip(",")
        cut = parse_cut(lw)
        if cut:
            out["cut"] = cut
        elif lw in ("keephdr", "hdr"):
            out["sdr"] = False
        elif lw in ("sdr", "tonemap"):
            out["sdr"] = True
        elif lw in ("nocompare", "nopreview"):
            out["compare"] = False
        elif lw in ("mobile", "phone"):
            out.update(QUICK["mobile"][2])
        elif _ARG_RES.match(lw):
            out["res"] = _ARG_RES.match(lw).group(1)
        elif lw in LEVELS:
            out["level"] = lw
        elif lw in ("low", "high", "medium", "max", "min", "best"):
            out["level"] = {"low": "strong", "min": "strong", "max": "light", "best": "light", "high": "light",
                            "medium": "balanced"}[lw]
        elif lw in ("h264", "x264", "avc", "h.264"):
            out["codec"] = "h264"
        elif lw in ("h265", "x265", "hevc", "h.265"):
            out["codec"] = "hevc"
        elif lw in ("mp4", "mkv"):
            out["fmt"] = lw.upper()
        elif lw in ("copy", "keep", "original"):
            out["audio"] = "copy"
        elif lw.rstrip("k") in ("128", "96", "64") and lw.endswith("k"):
            out["audio"] = lw.rstrip("k")
        elif _ARG_MB.match(lw):
            out["target"] = int(_ARG_MB.match(lw).group(1))
        elif _ARG_GB.match(lw):
            out["target"] = int(float(_ARG_GB.match(lw).group(1)) * 1000)
    return out


def is_oneshot(args: dict) -> bool:
    """Typed choices start right away; a bare cut / HDR switch still shows the panel (with it pre-set)."""
    return any(k not in ("cut", "sdr", "compare") for k in args)


# ─────────────────────────── file facts ───────────────────────────
def cut_span(cut, duration: float) -> tuple | None:
    """(start, length) actually encoded – same rules as ffcmd.cut_window."""
    from .ffcmd import cut_window
    return cut_window(cut, duration)


def view(opts: dict, meta: dict) -> dict:
    """What the card talks about: one file, the part of it that is cut, or the whole album (totals)."""
    opts = normalize(opts)
    meta = dict(meta or {})
    items = meta.pop("album", None) or []
    if opts["album"] and len(items) > 1:
        return {"name": f"Album · {len(items)} videos", "size": sum(int(m.get("size") or 0) for m in items),
                "duration": sum(int(m.get("duration") or 0) for m in items),
                "height": max(int(m.get("height") or 0) for m in items),
                "width": max(int(m.get("width") or 0) for m in items), "items": items}
    span = cut_span(opts["cut"], meta.get("duration") or 0) if opts["cut"] else None
    if span:
        dur = int(meta.get("duration") or 0)
        meta["size"] = int(int(meta.get("size") or 0) * span[1] / dur) if dur else meta.get("size")
        meta["duration"] = int(round(span[1]))
        meta["span"] = span
    return meta


def fmt_cut(cut) -> str:
    cut = _clean_cut(cut)
    if not cut:
        return ""
    return f"{_dur(cut[0])}–{_dur(cut[1]) if cut[1] else sc('end')}"


def meta_of(message) -> dict:
    """What Telegram already tells us about the file (no download needed for the card)."""
    m = getattr(message, "video", None) or getattr(message, "document", None) or getattr(message, "animation", None)
    if m is None:
        return {}
    return {"name": getattr(m, "file_name", None) or "video", "size": int(getattr(m, "file_size", 0) or 0),
            "duration": int(getattr(m, "duration", 0) or 0), "height": int(getattr(m, "height", 0) or 0),
            "width": int(getattr(m, "width", 0) or 0)}


def size_class(height: int, width: int = 0) -> int:
    """Standard class of a picture: 240 / 360 / 480 / 720 / 1080 (0 = unknown). Width counts too, so a
    1920×800 scope film is 1080p and a 1280×536 one is 720p."""
    eff = max(int(height or 0), int(width or 0) * 9 / 16)
    if not eff:
        return 0
    for b in (240, 360, 480, 720, 1080):
        if eff <= b * 1.05:
            return b
    return 1080


def src_class(meta: dict) -> int:
    meta = meta or {}
    return size_class(meta.get("height") or 0, meta.get("width") or 0)


def can_pick(res: str, meta: dict) -> bool:
    """A resolution above the source would be an upscale (bigger, not better) → locked on the card."""
    cls = src_class(meta)
    return not cls or int(res) <= cls


def out_class(opts: dict, meta: dict) -> int:
    cls = src_class(meta)
    r = int(opts["res"])
    return min(r, cls) if cls else r


def audio_kbps(opts: dict) -> int:
    return 160 if opts["audio"] == "copy" else int(opts["audio"])


def raw_estimate(opts: dict, meta: dict) -> int | None:
    """Rough output size in bytes from typical bitrates, *not* capped at the source size."""
    opts = normalize(opts)
    dur = int((meta or {}).get("duration") or 0)
    if not dur:
        return None
    kbps = _BASE_KBPS.get(out_class(opts, meta), 700) * _LEVEL_X[opts["level"]]
    if opts["codec"] == "hevc":
        kbps *= _HEVC_X
    return int((kbps + audio_kbps(opts)) * 1000 / 8 * dur)


def estimate(opts: dict, meta: dict) -> int | None:
    """Rough output size in bytes (None when Telegram gave no duration). An album view → the sum of its files."""
    opts = normalize(opts)
    if "album" in (meta or {}):                       # raw file facts → what the card is about
        meta = view(opts, meta)
    items = (meta or {}).get("items")
    if items:
        parts = [estimate(opts, m) for m in items]
        known = [p for p in parts if p]
        return sum(known) if known else None
    if opts["target"]:
        size = int((meta or {}).get("size") or 0)
        if size and opts["target"] * 1024 * 1024 >= size:
            return size
        return opts["target"] * 1024 * 1024
    total = raw_estimate(opts, meta)
    if total is None:
        return None
    size = int((meta or {}).get("size") or 0)
    return int(min(total, size)) if size else int(total)


def source_kbps(meta: dict) -> int:
    meta = meta or {}
    dur, size = int(meta.get("duration") or 0), int(meta.get("size") or 0)
    return int(size * 8 / 1000 / dur) if dur and size else 0


def min_target_mb(duration: float, a_kbps: int) -> int:
    """Smallest target the encoder can actually hit for this length (its video bitrate floor + audio)."""
    if not duration:
        return 0
    return int((MIN_VIDEO_KBPS + a_kbps) * duration / 8 / 1024 / 0.97) + 1


def warnings(opts: dict, meta: dict) -> list:
    """Problems worth knowing *before* spending minutes on an encode."""
    opts = normalize(opts)
    meta = meta or {}
    if "album" in meta:
        meta = view(opts, meta)
    if meta.get("items"):                             # an album: judge by its longest video
        meta = max(meta["items"], key=lambda m: int(m.get("duration") or 0))
    out = []
    dur = int(meta.get("duration") or 0)
    if opts["target"] and dur:
        need = min_target_mb(dur, audio_kbps(opts))
        if opts["target"] < need:
            out.append(f"{opts['target']} MB is too small for {_dur(dur)} – the smallest this can get is ≈ {need} MB")
    if opts["target"] and meta.get("size") and opts["target"] * 1024 * 1024 >= meta["size"] * 0.97:
        out.append(f"the file is already under {opts['target']} MB – quality mode will be used")
    kbps, cls = source_kbps(meta), src_class(meta)
    if not opts["target"] and kbps and cls and kbps < LIGHT_KBPS.get(cls, 0):
        out.append(f"already light ({kbps} kbps) – pick 🔴 Strong or a lower quality to save much")
    return out


def apply_quick(opts: dict, name: str, meta: dict) -> tuple[dict, str]:
    if name not in QUICK:
        return normalize(opts), ""
    ico, title, preset = QUICK[name]
    o = normalize({**normalize(opts), **preset})
    v = view(o, meta)
    if not can_pick(o["res"], v):
        for r in RES:
            if can_pick(r, v):
                o["res"] = r
                break
    return o, f"{ico} {title}"


# ─────────────────────────── speed history / size watch ───────────────────────────
def speed_key(opts: dict, height: int) -> str:
    opts = normalize(opts)
    return f"{opts['codec']}:{min(int(opts['res']), size_class(height) or int(opts['res']))}"


def eta_seconds(opts: dict, meta: dict, speeds: dict | None) -> int | None:
    """Encode time from this server's own history (× realtime per codec + output size)."""
    if "album" in (meta or {}):
        meta = view(opts, meta)
    if (meta or {}).get("items"):
        parts = [eta_seconds(opts, m, speeds) for m in meta["items"]]
        return sum(parts) if parts and all(parts) else None
    dur = int((meta or {}).get("duration") or 0)
    factor = (speeds or {}).get(speed_key(opts, src_class(meta)))
    try:
        factor = float(factor)
    except (TypeError, ValueError):
        return None
    return int(dur / factor) if dur and factor > 0 else None


def learn(speeds: dict, key: str, realtime: float, weight: float = 0.3) -> dict:
    """Exponential average, so one odd file doesn't swing the estimate."""
    out = dict(speeds or {})
    if realtime and realtime > 0:
        old = out.get(key)
        out[key] = round(realtime if not old else old * (1 - weight) + realtime * weight, 3)
    return out


def projected_size(size_now: int, done_s: float, total_s: float) -> int | None:
    if not size_now or not done_s or not total_s or done_s <= 0:
        return None
    return int(size_now * total_s / done_s)


def watch_verdict(size_now: int, done_s: float, total_s: float, limit: int) -> bool:
    """True when this tick says "will end up clearly bigger than the source"."""
    if not limit or not total_s or done_s < total_s * WATCH_FROM:
        return False
    proj = projected_size(size_now, done_s, total_s)
    return bool(proj and proj >= limit * WATCH_MARGIN)


# ─────────────────────────── encoder override ───────────────────────────
def override(opts: dict, info: dict | None, src_size: int = 0) -> tuple[dict, list]:
    """Options + ffprobe summary → (settings override for encoding.encode, notes for the card)."""
    opts = normalize(opts)
    info = info or {}
    notes = []
    codec = opts["codec"]
    o = {
        "resolution": opts["res"], "hevc": codec == "hevc", "av1": False, "preset": PRESET[codec],
        "crf": CRF[codec][opts["level"]], "mode": "crf", "target_mb": 0, "twopass": False,
        "extensions": opts["fmt"], "tune": False, "cabac": True, "reframe": "pass", "frame": "source",
        "aspect": False, "bits": False, "hardsub": False, "watermark": False, "motion_watermark": False,
        "logo": False, "denoise": False, "loudnorm": False, "dedup": False, "size_guard": False,
        "deinterlace": bool(info.get("interlaced")), "subtitles": True, "sample": "source",
    }
    if opts["audio"] == "copy":
        o.update(audio="copy", bitrate="source", channels="source")
    else:
        many = any(int(st.get("channels") or 0) > 2 for st in info.get("audio") or [])
        o.update(audio="aac", bitrate=opts["audio"], channels="2.0" if many else "source")
        if many:
            notes.append("surround audio → stereo")
    if opts["target"]:
        if src_size and opts["target"] * 1024 * 1024 >= src_size * 0.97:
            notes.append(f"target {opts['target']} MB ≥ the file itself – quality mode used")
        else:
            o.update(mode="size", target_mb=opts["target"])
    h = int(info.get("height") or 0)
    if h and int(opts["res"]) >= h:
        notes.append(f"kept {h}p – never upscaled")
    span = cut_span(opts["cut"], info.get("duration") or 0) if opts["cut"] else None
    if span:
        notes.append(f"cut {_dur(span[0])}–{_dur(span[0] + span[1])}")
    return o, notes


def wants_tonemap(opts: dict, info: dict | None) -> str:
    """'HDR10' / 'HLG' when the source is HDR and the user didn't ask to keep it → tone-map to SDR."""
    return (info or {}).get("hdr") or "" if normalize(opts)["sdr"] else ""


def label(opts: dict, height: int = 0) -> str:
    opts = normalize(opts)
    res = f"{min(int(opts['res']), height) if height else opts['res']}p"
    return f"{res} {CODECS[opts['codec']][1]}"


# ─────────────────────────── cards ───────────────────────────
def _size(n) -> str:
    from .display_progress import humanbytes
    return humanbytes(int(n or 0)) or "0 B"


def _dur(sec: int) -> str:
    sec = int(sec or 0)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def panel_text(opts: dict, meta: dict, speeds: dict | None = None, auto: bool = False) -> str:
    opts = normalize(opts)
    v = view(opts, meta)
    album = bool(v.get("items"))
    facts = [(f"📚 <b>{sc('Album')}</b> · {len(v['items'])} {sc('videos')}" if album
              else f"📄 <code>{html.escape(str(v.get('name') or 'video'))[:70]}</code>")]
    bits = []
    if v.get("size"):
        bits.append(f"💾 {_size(v['size'])}")
    if v.get("duration"):
        bits.append(f"⏱ {_dur(v['duration'])}")
    if v.get("height"):
        bits.append(f"🎞 {'≤ ' if album else ''}{v['height']}p")
    if bits:
        facts.append(" · ".join(bits))
    if v.get("span"):
        facts.append(f"✂️ {sc('only')} {fmt_cut(opts['cut'])}")
    ico, cname, csub = CODECS[opts["codec"]]
    lico, lname = LEVELS[opts["level"]]
    audio = sc("Keep original") if opts["audio"] == "copy" else f"AAC {opts['audio']}k"
    target = (f"{opts['target']} MB" + (f" <i>({sc('each')})</i>" if album else "")) if opts["target"] \
        else sc("Off · quality mode")
    qual = f"{sc(lname)} <i>(CRF {CRF[opts['codec']][opts['level']]})</i>" if not opts["target"] else \
        f"<s>{sc(lname)}</s> <i>({sc('target size decides')})</i>"
    lines = [hdr("🗜", "File compressor", "pick a quality – then tap start"), "",
             quote("\n".join(facts)), "",
             row("Quality", f"<b>{opts['res']}p</b>" + ("" if can_pick(opts["res"], v) else f" {LOCK}")),
             row("Codec", f"{ico} {cname} · <i>{sc(csub)}</i>"),
             row("Level", f"{lico} {qual}"),
             row("Audio", audio),
             row("Format", opts["fmt"] + (f" · <i>{sc('streams in Telegram')}</i>" if opts["fmt"] == "MP4"
                                          else f" · <i>{sc('keeps every subtitle')}</i>")),
             row("Target", target)]
    extras = [("🖼 " + sc("before / after")) if opts["compare"] else "",
              ("🌈 " + sc("HDR → SDR")) if opts["sdr"] else ("🌈 " + sc("keep HDR")),
              ("🤖 " + sc("auto on")) if auto else ""]
    lines.append(row("Extras", " · ".join(x for x in extras if x)))
    est = estimate(opts, v)
    if est:
        src = int(v.get("size") or 0)
        pct = f" <b>(−{(1 - est / src) * 100:.0f}%)</b>" if src and est < src else ""
        lines += ["", f"📉 <b>{sc('Estimate')}:</b> ≈ {_size(est)}{pct}"]
        eta = eta_seconds(opts, v, speeds)
        if eta:
            lines.append(f"⏱ <b>{sc('Encode time')}:</b> ≈ {_eta(eta)} <i>({sc('from this server')})</i>")
        lines.append(hint("a rough guess – busy, grainy scenes need more bits"))
    warn = warnings(opts, v)
    if warn:
        lines += [""] + [f"⚠️ <i>{html.escape(w)}</i>" for w in warn]
    return "\n".join(lines)


def _eta(sec: int) -> str:
    sec = max(1, int(sec))
    if sec < 90:
        return f"{sec} s"
    if sec < 5400:
        return f"{round(sec / 60)} min"
    return f"{sec / 3600:.1f} h"


def _mark(on: bool, text: str) -> str:
    return f"✅ {text}" if on else text


def keyboard(opts: dict, meta: dict, auto: bool = False):
    from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup
    opts = normalize(opts)
    v = view(opts, meta)
    res_row = []
    for r in RES:
        if can_pick(r, v):
            res_row.append(Btn(_mark(opts["res"] == r, sc(f"{r}p")), callback_data=f"cmp:res:{r}"))
        else:
            res_row.append(Btn(f"{LOCK} {sc(r + 'p')}", callback_data=f"cmp:lock:{r}"))
    codec_row = [Btn(_mark(opts["codec"] == k, f"{v_[0]} {sc(v_[1])}"), callback_data=f"cmp:codec:{k}")
                 for k, v_ in CODECS.items()]
    level_row = [Btn(_mark(opts["level"] == k and not opts["target"], f"{v_[0]} {sc(v_[1])}"),
                     callback_data=f"cmp:level:{k}") for k, v_ in LEVELS.items()]
    audio = sc("keep") if opts["audio"] == "copy" else sc(f"{opts['audio']}k")
    target = f"{opts['target']}ᴍʙ" if opts["target"] else sc("off")
    extra_row = [Btn(f"🔊 {audio}", callback_data="cmp:audio"),
                 Btn(f"🎞 {opts['fmt']}", callback_data="cmp:fmt"),
                 Btn(f"🎯 {target}", callback_data="cmp:target")]
    quick_row = [Btn(_mark(_is_quick(opts, k), f"{v_[0]} {sc(v_[1])}"), callback_data=f"cmp:quick:{k}")
                 for k, v_ in QUICK.items()]
    more_row = [Btn(_mark(opts["compare"], f"🖼 {sc('Compare')}"), callback_data="cmp:compare"),
                Btn(f"🌈 {sc('SDR') if opts['sdr'] else sc('HDR')}", callback_data="cmp:sdr"),
                Btn(_mark(auto, f"🤖 {sc('Auto')}"), callback_data="cmp:auto")]
    rows = [res_row, quick_row, codec_row, level_row, extra_row, more_row]
    album = (meta or {}).get("album") or []
    if len(album) > 1:
        rows.append([Btn(_mark(opts["album"], f"📚 {sc('Whole album')} · {len(album)}"), callback_data="cmp:album:on"),
                     Btn(_mark(not opts["album"], f"🎞 {sc('This one')}"), callback_data="cmp:album:off")])
    if opts["cut"] and not v.get("items"):
        rows.append([Btn(f"✂️ {fmt_cut(opts['cut'])} · ✖️ {sc('whole video')}", callback_data="cmp:cut:clear")])
    rows.append([Btn(f"🚀 {sc('Start')}", callback_data="cmp:go"), Btn(f"✖️ {sc('Close')}", callback_data="cmp:close")])
    return InlineKeyboardMarkup(rows)


def _is_quick(opts: dict, name: str) -> bool:
    preset = QUICK[name][2]
    return all(opts.get(k) == v for k, v in preset.items() if k != "res")


def apply(opts: dict, action: str, value: str = "") -> tuple[dict, str]:
    """One button tap → (new options, toast text)."""
    o = normalize(opts)
    if action == "res" and value in RES:
        o["res"] = value
        return o, f"🎞 {value}p"
    if action == "codec" and value in CODECS:
        o["codec"] = value
        return o, f"{CODECS[value][0]} {CODECS[value][1]} – {CODECS[value][2]}"
    if action == "level" and value in LEVELS:
        o["level"], o["target"] = value, 0
        return o, f"{LEVELS[value][0]} {LEVELS[value][1]} · CRF {CRF[o['codec']][value]}"
    if action == "audio":
        o["audio"] = AUDIO[(AUDIO.index(o["audio"]) + 1) % len(AUDIO)]
        return o, "🔊 " + ("keep the original audio" if o["audio"] == "copy" else f"AAC {o['audio']}k")
    if action == "fmt":
        o["fmt"] = FORMATS[(FORMATS.index(o["fmt"]) + 1) % len(FORMATS)]
        return o, f"🎞 {o['fmt']}"
    if action == "target":
        i = TARGETS.index(o["target"]) if o["target"] in TARGETS else 0
        o["target"] = TARGETS[(i + 1) % len(TARGETS)]
        return o, f"🎯 target {o['target']} MB" if o["target"] else "🎯 target off – quality mode"
    if action == "compare":
        o["compare"] = not o["compare"]
        return o, "🖼 a before / after frame comes with the result" if o["compare"] else "🖼 no comparison frame"
    if action == "sdr":
        o["sdr"] = not o["sdr"]
        return o, ("🌈 HDR videos are converted to normal colours (SDR)" if o["sdr"]
                   else "🌈 HDR is kept as it is (looks washed out on many phones)")
    if action == "album" and value in ("on", "off"):
        o["album"] = value == "on"
        return o, "📚 every video of the album" if o["album"] else "🎞 only this video"
    if action == "cut" and value == "clear":
        o["cut"] = None
        return o, "✂️ the whole video"
    return o, ""


def queued_text(opts: dict, meta: dict) -> str:
    opts = normalize(opts)
    v = view(opts, meta)
    sub = label(opts, int(v.get("height") or 0))
    if v.get("span"):
        sub += f" · ✂️ {fmt_cut(opts['cut'])}"
    lines = [hdr("🗜", "Compressing", sub), hint("getting your file ready…")]
    lines += [f"⚠️ <i>{html.escape(w)}</i>" for w in warnings(opts, v)]
    return "\n".join(lines)


def batch_text(opts: dict, queued: int, total: int, limited: int = 0) -> str:
    """Summary card after 🚀 on a whole album – every video gets its own live status card below."""
    opts = normalize(opts)
    lines = [hdr("📚", "Album compress", label(opts)), "",
             row("Queued", f"<b>{queued}</b> / {total} {sc('videos')}"),
             row("Settings", (f"{opts['target']} MB {sc('each')}" if opts["target"]
                              else f"{LEVELS[opts['level']][1]} · CRF {CRF[opts['codec']][opts['level']]}")
                 + f" · {opts['fmt']}")]
    if limited:
        lines += ["", f"⚠️ <i>{limited} {sc('skipped – your queue limit is full. send')}</i> <code>/compress</code> "
                      f"<i>{sc('again later')}.</i>"]
    lines += ["", hint("each video gets its own status card below.")]
    return "\n".join(lines)


def auto_text(opts: dict, meta: dict) -> str:
    return (queued_text(opts, meta) + "\n" + f"<i>🤖 {sc('auto-compress is on · turn it off')}:</i> "
            "<code>/compress auto off</code>")


def auto_state_text(on: bool, opts: dict) -> str:
    opts = remembered(opts)
    if not on:
        return "\n".join([hdr("🤖", "Auto-compress", "off"), "",
                          hint("videos you send are left alone."),
                          f"<i>{sc('turn it on')}:</i> <code>/compress auto on</code>"])
    return "\n".join([hdr("🤖", "Auto-compress", "on"), "",
                      quote(f"{sc('every video you send me in private is compressed to')} <b>{label(opts)}</b> · "
                            + (f"{opts['target']} MB" if opts["target"] else LEVELS[opts["level"]][1])
                            + f" · {opts['fmt']}"), "",
                      f"<i>{sc('uses your last')}</i> <code>/compress</code> <i>{sc('choice')} · "
                      f"{sc(f'files under {AUTO_MIN_MB} MB are skipped')}</i>",
                      f"<i>{sc('turn it off')}:</i> <code>/compress auto off</code>"])


def compare_caption(opts: dict, info: dict, old: int, new: int, at: float) -> str:
    opts = normalize(opts)
    h = int((info or {}).get("height") or 0)
    return (f"🖼 <b>{sc('Before')}</b> ◀️ {h or '?'}p · {_size(old)}   |   ▶️ <b>{sc('After')}</b> "
            f"{label(opts, h)} · {_size(new)}\n" + hint(f"same frame at {_dur(int(at))} – zoom in to compare"))


def working_text(opts: dict, info: dict, notes: list) -> str:
    opts = normalize(opts)
    lines = [f"<b>🗜 {sc('Compressing')} → {label(opts, int(info.get('height') or 0))}</b>",
             hint(f"{LEVELS[opts['level']][1]} · {opts['fmt']}" if not opts["target"]
                  else f"target {opts['target']} MB · {opts['fmt']}")]
    if notes:
        lines.append("<i>" + " · ".join(html.escape(n) for n in notes) + "</i>")
    return "\n".join(lines)


def done_text(name: str, opts: dict, info: dict, old: int, new: int, elapsed: float, notes: list) -> str:
    opts = normalize(opts)
    saved = (1 - new / old) * 100 if old else 0
    dur = float(info.get("duration") or 0)
    speed = f" · {dur / elapsed:.1f}× {sc('realtime')}" if dur and elapsed else ""
    from .display_progress import TimeFormatter
    lines = [hdr("✅", "Compressed", f"saved {saved:.0f}%" if saved > 0 else ""), "",
             f"<code>{html.escape(name)[:80]}</code>", "",
             row("Size", f"{_size(old)} → <b>{_size(new)}</b>" + (f" <b>(−{saved:.1f}%)</b>" if saved > 0 else "")),
             row("Video", f"{label(opts, int(info.get('height') or 0))} · "
                          + (f"{opts['target']} MB target" if opts["target"] else
                             f"{LEVELS[opts['level']][1]} (CRF {CRF[opts['codec']][opts['level']]})")),
             row("Audio", "original" if opts["audio"] == "copy" else f"AAC {opts['audio']}k"),
             row("Time", f"{TimeFormatter(elapsed)}{speed}")]
    if notes:
        lines += ["", hint(" · ".join(notes))]
    return "\n".join(lines)


def bigger_text(opts: dict, old: int, new: int, stopped_at: float | None = None) -> str:
    """stopped_at: fraction done when the size watch stopped the encode early (new = projected size)."""
    opts = normalize(opts)
    tips = []
    if opts["level"] != "strong" and not opts["target"]:
        tips.append("🔴 Strong")
    if opts["res"] != "360":
        tips.append(f"a lower resolution than {opts['res']}p")
    if opts["codec"] == "h264":
        tips.append("📦 H.265")
    result = (f"≈ {_size(new)} – {sc('heading bigger')}" if stopped_at is not None
              else f"{_size(new)} – {sc('not smaller')}")
    rows_ = [row("Source", _size(old)), row("Result", result)]
    if stopped_at is not None:
        rows_.append(row("Stopped at", f"{stopped_at * 100:.0f}% – {sc('the rest of the time was saved')}"))
    return "\n".join([hdr("🟰", "Already compact"), ""] + rows_ + ["",
                      hint("the file is already well compressed, so nothing was sent.")
                      + ("\n" + hint("try: ") + html.escape(" · ".join(tips)) if tips else "")])


def usage_text() -> str:
    return "\n".join([
        hdr("🗜", "File compressor", "make videos smaller – fast"), "",
        quote("\n".join([
            f"1️⃣ {sc('Reply')} <code>/compress</code> {sc('to a video')}",
            f"2️⃣ {sc('Pick')} <b>1080p · 720p · 480p · 360p</b>, {sc('codec, level, audio, target')}",
            f"3️⃣ {sc('Tap')} 🚀 {sc('Start')}"])), "",
        f"<b>⚡ {sc('One-shot')}</b> <i>({sc('skips the buttons')})</i>",
        "<code>/compress 480</code>\n<code>/compress 720 strong hevc</code>\n<code>/compress 360 50mb</code>\n"
        "<code>/compress mobile</code>", "",
        f"<b>✂️ {sc('Only a part')}</b>",
        "<code>/compress 480 10:00-25:00</code>\n<code>/compress 1:30-</code> <i>(→ {})</i>".format(sc("to the end")), "",
        f"<b>✨ {sc('More')}</b>",
        f"📚 {sc('reply to an album → compress every video in it')}\n"
        f"🖼 {sc('before / after frame with every result')}\n"
        f"🌈 {sc('HDR videos are fixed to normal colours automatically')}\n"
        f"🤖 <code>/compress auto on</code> – {sc('compress every video you send')}", "",
        hint("your last choice is remembered for next time."),
    ])

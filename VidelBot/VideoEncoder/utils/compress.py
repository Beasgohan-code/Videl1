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
TARGETS = [0, 25, 50, 100, 200, 500, 1000]             # MB · 0 = off (quality mode)
FORMATS = ["MP4", "MKV"]
DEFAULT = {"res": "720", "codec": "h264", "level": "balanced", "audio": "128", "target": 0, "fmt": "MP4"}

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
    return o


_ARG_RES = re.compile(r"^(1080|720|480|360)p?$", re.I)
_ARG_MB = re.compile(r"^(\d{1,4})\s*(mb|m)$", re.I)
_ARG_GB = re.compile(r"^(\d(?:\.\d+)?)\s*(gb|g)$", re.I)


def parse_args(text: str) -> dict:
    """`/compress 480 strong hevc 50mb mkv 64k` → {res, level, codec, target, fmt, audio}. Unknown words are ignored."""
    out: dict = {}
    for w in (text or "").split()[1:]:
        lw = w.lower().strip(",")
        if _ARG_RES.match(lw):
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


# ─────────────────────────── file facts ───────────────────────────
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


def estimate(opts: dict, meta: dict) -> int | None:
    """Rough output size in bytes (None when Telegram gave no duration)."""
    opts = normalize(opts)
    dur = int((meta or {}).get("duration") or 0)
    if opts["target"]:
        return opts["target"] * 1024 * 1024
    if not dur:
        return None
    kbps = _BASE_KBPS.get(out_class(opts, meta), 700) * _LEVEL_X[opts["level"]]
    if opts["codec"] == "hevc":
        kbps *= _HEVC_X
    total = (kbps + audio_kbps(opts)) * 1000 / 8 * dur
    size = int((meta or {}).get("size") or 0)
    return int(min(total, size)) if size else int(total)


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
    return o, notes


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


def panel_text(opts: dict, meta: dict) -> str:
    opts = normalize(opts)
    meta = meta or {}
    facts = [f"📄 <code>{html.escape(str(meta.get('name') or 'video'))[:70]}</code>"]
    bits = []
    if meta.get("size"):
        bits.append(f"💾 {_size(meta['size'])}")
    if meta.get("duration"):
        bits.append(f"⏱ {_dur(meta['duration'])}")
    if meta.get("height"):
        bits.append(f"🎞 {meta['height']}p")
    if bits:
        facts.append(" · ".join(bits))
    ico, cname, csub = CODECS[opts["codec"]]
    lico, lname = LEVELS[opts["level"]]
    audio = sc("Keep original") if opts["audio"] == "copy" else f"AAC {opts['audio']}k"
    target = f"{opts['target']} MB" if opts["target"] else sc("Off · quality mode")
    qual = f"{sc(lname)} <i>(CRF {CRF[opts['codec']][opts['level']]})</i>" if not opts["target"] else \
        f"<s>{sc(lname)}</s> <i>({sc('target size decides')})</i>"
    lines = [hdr("🗜", "File compressor", "pick a quality – then tap start"), "",
             quote("\n".join(facts)), "",
             row("Quality", f"<b>{opts['res']}p</b>" + ("" if can_pick(opts["res"], meta) else f" {LOCK}")),
             row("Codec", f"{ico} {cname} · <i>{sc(csub)}</i>"),
             row("Level", f"{lico} {qual}"),
             row("Audio", audio),
             row("Format", opts["fmt"] + (f" · <i>{sc('streams in Telegram')}</i>" if opts["fmt"] == "MP4"
                                          else f" · <i>{sc('keeps every subtitle')}</i>")),
             row("Target", target)]
    est = estimate(opts, meta)
    if est:
        src = int(meta.get("size") or 0)
        pct = f" <b>(−{(1 - est / src) * 100:.0f}%)</b>" if src and est < src else ""
        lines += ["", f"📉 <b>{sc('Estimate')}:</b> ≈ {_size(est)}{pct}"]
        lines.append(hint("a rough guess – busy, grainy scenes need more bits"))
    return "\n".join(lines)


def _mark(on: bool, text: str) -> str:
    return f"✅ {text}" if on else text


def keyboard(opts: dict, meta: dict):
    from pyrogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup
    opts = normalize(opts)
    res_row = []
    for r in RES:
        if can_pick(r, meta):
            res_row.append(Btn(_mark(opts["res"] == r, sc(f"{r}p")), callback_data=f"cmp:res:{r}"))
        else:
            res_row.append(Btn(f"{LOCK} {sc(r + 'p')}", callback_data=f"cmp:lock:{r}"))
    codec_row = [Btn(_mark(opts["codec"] == k, f"{v[0]} {sc(v[1])}"), callback_data=f"cmp:codec:{k}")
                 for k, v in CODECS.items()]
    level_row = [Btn(_mark(opts["level"] == k and not opts["target"], f"{v[0]} {sc(v[1])}"),
                     callback_data=f"cmp:level:{k}") for k, v in LEVELS.items()]
    audio = sc("keep") if opts["audio"] == "copy" else sc(f"{opts['audio']}k")
    target = f"{opts['target']}ᴍʙ" if opts["target"] else sc("off")
    extra_row = [Btn(f"🔊 {audio}", callback_data="cmp:audio"),
                 Btn(f"🎞 {opts['fmt']}", callback_data="cmp:fmt"),
                 Btn(f"🎯 {target}", callback_data="cmp:target")]
    return InlineKeyboardMarkup([res_row, codec_row, level_row, extra_row,
                                 [Btn(f"🚀 {sc('Start')}", callback_data="cmp:go"),
                                  Btn(f"✖️ {sc('Close')}", callback_data="cmp:close")]])


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
    return o, ""


def queued_text(opts: dict, meta: dict) -> str:
    opts = normalize(opts)
    return "\n".join([hdr("🗜", "Compressing", label(opts, int((meta or {}).get("height") or 0))),
                      hint("getting your file ready…")])


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


def bigger_text(opts: dict, old: int, new: int) -> str:
    opts = normalize(opts)
    tips = []
    if opts["level"] != "strong" and not opts["target"]:
        tips.append("🔴 Strong")
    if opts["res"] != "360":
        tips.append(f"a lower resolution than {opts['res']}p")
    if opts["codec"] == "h264":
        tips.append("📦 H.265")
    return "\n".join([hdr("🟰", "Already compact"), "",
                      row("Source", _size(old)), row("Result", f"{_size(new)} – {sc('not smaller')}"), "",
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
        "<code>/compress 480</code>\n<code>/compress 720 strong hevc</code>\n<code>/compress 360 50mb</code>", "",
        hint("your last choice is remembered for next time."),
    ])

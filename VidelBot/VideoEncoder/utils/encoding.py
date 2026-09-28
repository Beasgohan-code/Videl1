import asyncio
import html as _html
import json
import math
import os
import re
import subprocess
import time

from hachoir.metadata import extractMetadata
from hachoir.parser import createParser
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .. import LOGGER, download_dir, encode_dir
from .database.access_db import db
from . import ffcmd, jobs
from .display_progress import TimeFormatter, humanbytes
from core.style import hdr, hint, row


def get_codec(filepath, channel='v:0'):
    try:
        output = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', channel,
                                          '-show_entries', 'stream=codec_name,codec_tag_string', '-of',
                                          'default=nokey=1:noprint_wrappers=1', filepath])
        return output.decode('utf-8').split()
    except subprocess.CalledProcessError as e:
        LOGGER.error(f"ffprobe failed for {filepath}: {e}")
        return []
    except Exception as e:
        LOGGER.error(f"ffprobe exception for {filepath}: {e}")
        return []

def get_media_streams(filepath):
    try:
        cmd = ['ffprobe', '-hide_banner', '-print_format', 'json', '-show_streams', filepath]
        output = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return json.loads(output.decode('utf-8')).get('streams', [])
    except Exception as e:
        LOGGER.error(f"Failed to get media streams: {e}")
        return []

async def extract_subs(filepath, msg, user_id):

    path, extension = os.path.splitext(filepath)
    name = os.path.basename(path)
    check = await asyncio.to_thread(get_codec, filepath, 's:0')
    if not check:
        return None
    if any(c in ffcmd.BITMAP_SUBS for c in check):
        return None                      # picture subtitles (PGS / VobSub) can't become .ass
    else:
        output = os.path.join(encode_dir, str(msg.id) + '.ass')

    return await asyncio.to_thread(_extract_subs_sync, filepath, output)


def _extract_subs_sync(filepath, output):
    """ffmpeg / mkvextract / font install – blocking, so it runs in a worker thread."""
    try:
        subprocess.call(['ffmpeg', '-y', '-i', filepath, '-map', 's:0', output])
        # mkvextract might not be in PATH on Windows, handle gracefully
        try:
            subprocess.call(['mkvextract', 'attachments', filepath, '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16',
                            '17', '18', '19', '20', '21', '22', '23', '24', '25', '26', '27', '28', '29', '30', '31', '32', '33', '34', '35', '36', '37', '38', '39', '40'])
        except FileNotFoundError:
            LOGGER.warning("mkvextract not found, skipping attachments extraction.")
        except Exception as e:
            LOGGER.error(f"mkvextract failed: {e}")

        # Moving fonts is Linux specific and dangerous on Windows to assume /usr/share/fonts/
        # We will only attempt this on Linux-like environments or skip if it fails
        try:
            if os.name != 'nt':
                subprocess.run([f"mv -f *.JFPROJ *.FNT *.PFA *.ETX *.WOFF *.FOT *.TTF *.SFD *.VLW *.VFB *.PFB *.OTF *.GXF *.WOFF2 *.ODTTF *.BF *.CHR *.TTC *.BDF *.FON *.GF *.PMT *.AMFM  *.MF *.PFM *.COMPOSITEFONT *.PF2 *.GDR *.ABF *.VNF *.PCF *.SFP *.MXF *.DFONT *.UFO *.PFR *.TFM *.GLIF *.XFN *.AFM *.TTE *.XFT *.ACFM *.EOT *.FFIL *.PK *.SUIT *.NFTR *.EUF *.TXF *.CHA *.LWFN *.T65 *.MCF *.YTF *.F3F *.FEA *.SFT *.PFT /usr/share/fonts/"], shell=True)
                subprocess.run([f"mv -f *.jfproj *.fnt *.pfa *.etx *.woff *.fot *.ttf *.sfd *.vlw *.vfb *.pfb *.otf *.gxf *.woff2 *.odttf *.bf *.chr *.ttc *.bdf *.fon *.gf *.pmt *.amfm  *.mf *.pfm *.compositefont *.pf2 *.gdr *.abf *.vnf *.pcf *.sfp *.mxf *.dfont *.ufo *.pfr *.tfm *.glif *.xfn *.afm *.tte *.xft *.acfm *.eot *.ffil *.pk *.suit *.nftr *.euf *.txf *.cha *.lwfn *.t65 *.mcf *.ytf *.f3f *.fea *.sft *.pft /usr/share/fonts/ && fc-cache -f"], shell=True)
        except Exception as e:
            LOGGER.warning(f"Font moving failed (likely not supported on this OS): {e}")

        if not os.path.exists(output) or os.path.getsize(output) == 0:
            LOGGER.error("Extract subs failed: ffmpeg produced no subtitle file")
            return None
        return output
    except Exception as e:
        LOGGER.error(f"Extract subs failed: {e}")
        return None


EXTRAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "extras")
WATERMARK_ASS = os.path.join(EXTRAS_DIR, "watermark.ass")


class Encoded(str):
    """Output path (a plain str for old callers) + what the summary needs."""
    info: dict = None
    settings: dict = None
    elapsed: float = 0.0
    sample: tuple = None
    guard: tuple = None          # (encoded bytes, remux bytes) when the size guard swapped the file
    encoder: str = ""
    where: str = "CPU"


def _probe_sync(filepath):
    try:
        out = subprocess.check_output(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams',
                                       '-show_format', filepath], stderr=subprocess.DEVNULL, timeout=120)
        return json.loads(out.decode('utf-8', 'ignore') or '{}')
    except Exception as e:
        LOGGER.warning(f"ffprobe failed for {os.path.basename(str(filepath))}: {e}")
        return {}


async def probe(filepath) -> dict:
    """One ffprobe for everything (streams + format), off the event loop → ffcmd.summarize() dict."""
    info = ffcmd.summarize(await asyncio.to_thread(_probe_sync, filepath))
    if not info["size"]:
        try:
            info["size"] = os.path.getsize(filepath)
        except OSError:
            pass
    if not info["duration"]:
        try:
            info["duration"] = float(await asyncio.to_thread(get_duration, filepath) or 0)
        except Exception:
            pass
    return info


def _motion_ass(opacity: str) -> str:
    alpha = {'50': '80', '75': '40', '100': '00'}.get(str(opacity), '80')
    motion_text = "Watermark"
    if os.path.exists(WATERMARK_ASS):
        with open(WATERMARK_ASS, 'r', encoding='utf-8') as f:
            for line in f:
                if line.startswith('Title:'):
                    motion_text = line.replace('Title:', '').strip() or motion_text
                    break
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: MotionStyle,Arial,48,&H{alpha}FFFFFF,&H000000FF,&H{alpha}000000,&H{alpha}000000,0,0,0,0,100,100,0,0,1,2,1,2,10,10,20,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,9:59:59.99,MotionStyle,,0,0,0,Banner;10;0;50,{motion_text}
"""


def cancel_markup(key):
    return InlineKeyboardMarkup([[InlineKeyboardButton('❌ Cancel', callback_data=f'enc_cancel:{key}'),
                                  InlineKeyboardButton('📊 Stats', callback_data='stats')]])


async def encode(filepath, message, msg, audio_map=None, opts=None):
    """Encode `filepath` with the user's settings. Returns an `Encoded` path, or None on failure / cancel."""
    opts = opts or {}
    uid = message.from_user.id
    settings = await db.get_settings(uid)
    settings.update(opts.get("override") or {})
    info = opts.get("info") or await probe(filepath)

    from . import hw
    from .scheduler import task_dirs
    encoder, where = hw.pick_encoder(settings)
    if encoder is None:
        LAST_ERROR[msg.id] = f"{ffcmd.describe(settings)['codec']}: {where}. Pick H.264 / H.265 in /settings."
        return None
    if settings.get("av1") or settings.get("twopass"):
        pro = True
        try:
            from core.plans import is_encoder_pro
            pro = await is_encoder_pro(uid)
        except Exception:
            pass
        if not pro and settings.get("av1"):
            LAST_ERROR[msg.id] = "AV1 is an 🎬 Encoder Pro feature – see /plans, or switch the codec in /settings."
            return None
        if not pro:
            settings["twopass"] = False           # 2-pass is Pro-only → plain single pass
    out_dir = opts.get("out_dir") or task_dirs(message)[1]
    os.makedirs(out_dir, exist_ok=True)

    path, _ext = os.path.splitext(filepath)
    name = os.path.basename(path)
    sample = None
    if opts.get("sample"):
        sample = ffcmd.sample_window(info["duration"], opts["sample"])
        name += ".sample"
    output_filepath = os.path.join(out_dir, name + ffcmd.output_ext(settings))
    if os.path.abspath(output_filepath) == os.path.abspath(filepath):
        output_filepath = os.path.join(out_dir, name + ".videl" + ffcmd.output_ext(settings))
    if os.path.isfile(output_filepath):
        os.remove(output_filepath)

    progress = os.path.join(out_dir, f"process_{msg.id}.txt")
    open(progress, 'w').close()

    subs_file = os.path.join(encode_dir, str(msg.id) + '.ass') if settings["hardsub"] else None
    if subs_file and not os.path.isfile(subs_file):
        subs_file = None
    watermark_file = text_wm_file = motion_file = logo_file = None
    temp_files = [progress]
    if settings["watermark"]:
        if (settings.get("wm_text") or "").strip():             # the user's own text
            text_wm_file = os.path.join(out_dir, f"wm_{msg.id}.ass")
            with open(text_wm_file, 'w', encoding='utf-8') as f:
                f.write(ffcmd.text_watermark_ass(settings["wm_text"], settings["wm_pos"], settings["wm_size"],
                                                 settings["wm_opacity"]))
            temp_files.append(text_wm_file)
        elif os.path.isfile(WATERMARK_ASS):
            watermark_file = WATERMARK_ASS
    if settings["motion_watermark"]:
        motion_file = os.path.join(out_dir, f"motion_{msg.id}.ass")
        with open(motion_file, 'w', encoding='utf-8') as f:
            f.write(_motion_ass(settings["motion_opacity"]))
        temp_files.append(motion_file)
    if settings["logo"] and settings.get("logo_id"):
        logo_file = await _fetch_logo(message, settings["logo_id"], out_dir, msg.id)
        if logo_file:
            temp_files.append(logo_file)

    twopass = ffcmd.twopass_applies(settings, encoder, sample)
    passlog = os.path.join(out_dir, f"2pass_{msg.id}") if twopass else None
    common = dict(audio_map=audio_map, subs_file=subs_file, watermark_file=watermark_file, motion_file=motion_file,
                  sample=sample, encoder=encoder, logo_file=logo_file, text_wm_file=text_wm_file,
                  vaapi_device=_cfg_vaapi(), fps_flag=await _fps_flag(), threads=encoder_threads())
    passes = [1, 2] if twopass else [None]
    owned = jobs.get(msg.id) is None              # a task may have registered it already (download stage)
    job = jobs.register(msg.id, uid, getattr(getattr(msg, "chat", None), "id", None), name=name)
    started = time.time()
    proc = None
    stderr = b""
    total = (sample[1] if sample else info["duration"]) or None
    try:
        for pass_no in passes:
            if job.cancelled:
                break
            open(progress, 'w').close()
            target = os.devnull if pass_no == 1 else output_filepath
            command = ffcmd.build_command(filepath, target, settings, info, progress=progress, pass_no=pass_no,
                                          passlog=passlog, **common)
            LOGGER.info(f"ffmpeg ({where}{', pass ' + str(pass_no) if pass_no else ''}): {' '.join(command[:-1])} …")
            stage = {1: "Pass 1/2 · analysing", 2: "Pass 2/2 · encoding"}.get(pass_no, "Encoding")
            # stdout is unused (progress goes to a file). stderr is drained *while* ffmpeg runs: reading it
            # only after the progress loop let a chatty input (decode warnings on a damaged file) fill the
            # 64 KB pipe and freeze ffmpeg mid-encode.
            proc = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.DEVNULL,
                                                        stdout=asyncio.subprocess.DEVNULL,
                                                        stderr=asyncio.subprocess.PIPE)
            drain = asyncio.ensure_future(_drain(proc.stderr))
            jobs.attach(msg.id, proc)
            try:
                await handle_progress(proc, msg, message, filepath, progress_file=progress, total_time=total,
                                      settings=settings, info=info, stage=f"{stage} · {where}")
                await proc.wait()
            except BaseException:
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                raise
            finally:
                stderr = await _drained(drain)
            if proc.returncode != 0:
                break
    finally:
        if owned:
            jobs.unregister(msg.id)
        if passlog and os.path.isdir(out_dir):     # x264 .log/.mbtree · x265 .x265/.cutree
            temp_files += [os.path.join(out_dir, p) for p in os.listdir(out_dir) if p.startswith(f"2pass_{msg.id}")]
        for f in temp_files:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
    if job.cancelled:
        LOGGER.info(f"Encode cancelled by the user: {name}")
        if os.path.isfile(output_filepath):
            os.remove(output_filepath)
        return None
    e_response = (stderr or b"").decode(errors="ignore").strip()
    if proc is None or proc.returncode != 0 or not os.path.isfile(output_filepath) or os.path.getsize(output_filepath) == 0:
        LOGGER.error(f"Encoding failed (exit {getattr(proc, 'returncode', None)}): {e_response[-800:]}")
        if os.path.isfile(output_filepath):
            os.remove(output_filepath)
        LAST_ERROR[msg.id] = e_response[-300:]
        return None
    out = Encoded(output_filepath)
    out.info, out.settings, out.elapsed, out.sample = info, settings, time.time() - started, sample
    out.encoder, out.where = encoder, where
    return out


def encoder_threads(cpus: int | None = None, workers: int | None = None) -> int:
    """ffmpeg -threads for one encode. One worker → 0 (ffmpeg uses every core). With N parallel workers each
    gets cpus/N: N encoders all sized for the whole machine thrash caches and context-switch, and in
    practice finish later than the same encodes sharing the cores fairly."""
    import config
    cpus = cpus or os.cpu_count() or 1
    workers = workers or getattr(config, "ENCODER_WORKERS", 1)
    if workers <= 1:
        return 0
    return max(2, cpus // workers)


async def _drain(stream, keep: int = 16384) -> bytes:
    """Read a pipe to EOF, keeping only the last `keep` bytes (enough for the error card)."""
    buf = bytearray()
    if stream is None:
        return b""
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return bytes(buf)
        buf += chunk
        if len(buf) > keep * 2:
            del buf[:-keep]


async def _drained(task) -> bytes:
    try:
        return (await asyncio.wait_for(task, 10))[-16384:]
    except Exception:                 # pipe stuck / cancelled – the error text is a nice-to-have
        task.cancel()
        return b""


async def _fps_flag() -> str:
    from . import hw
    try:
        return await asyncio.to_thread(hw.fps_mode_flag)     # one `ffmpeg -version` per process
    except Exception:
        return "-fps_mode"


def _cfg_vaapi() -> str:
    import config
    return config.VAAPI_DEVICE


async def _fetch_logo(message, file_id, out_dir, key):
    """Download the user's logo (a Telegram photo / image file_id) → local path, or None."""
    try:
        client = getattr(message, "_client", None)
        if client is None:
            from .. import app as client
        return await client.download_media(file_id, file_name=os.path.join(out_dir, f"logo_{key}.png"))
    except Exception as e:
        LOGGER.warning(f"logo download failed: {e}")
        return None


async def split_for_upload(path: str, limit: int, key=None) -> list:
    """Split a file bigger than `limit` bytes into parts that fit. Videos are cut at keyframes with stream
    copy (every part plays on its own); anything else is byte-split into .001 .002 … (join with 7-Zip / cat).
    Returns the list of part paths ([path] when no split is needed)."""
    size = os.path.getsize(path)
    if size <= limit:
        return [path]
    base, ext = os.path.splitext(path)
    parts_dir = base + ".parts"
    os.makedirs(parts_dir, exist_ok=True)
    info = await probe(path)
    if info.get("video") is not None and info.get("duration"):
        seg = ffcmd.split_plan(size, info["duration"], limit)
        for _attempt in range(4):
            for old in os.listdir(parts_dir):
                os.remove(os.path.join(parts_dir, old))
            pattern = os.path.join(parts_dir, os.path.basename(base) + ".part%03d" + (ext or ".mkv"))
            code, err = await run_ffmpeg(ffcmd.split_command(path, pattern, seg), key)
            parts = sorted(os.path.join(parts_dir, p) for p in os.listdir(parts_dir))
            if code == 0 and parts and all(os.path.getsize(p) <= limit for p in parts):
                return parts
            if key is not None and jobs.is_cancelled(key):
                return []
            seg *= 0.75                           # a keyframe gap made one part too big – cut shorter
            LOGGER.warning(f"split retry ({code}): {err[-200:]}")
    # byte split fallback
    for old in os.listdir(parts_dir):
        os.remove(os.path.join(parts_dir, old))
    parts, n = [], 1
    with open(path, "rb") as src:
        while True:
            if key is not None and jobs.is_cancelled(key):
                return []
            part = os.path.join(parts_dir, f"{os.path.basename(path)}.{n:03d}")
            written = 0
            with open(part, "wb") as dst:
                while written < limit:
                    chunk = src.read(min(8 * 1024 * 1024, limit - written))
                    if not chunk:
                        break
                    dst.write(chunk)
                    written += len(chunk)
            if not written:
                os.remove(part)
                break
            parts.append(part)
            n += 1
    return parts


LAST_ERROR: dict = {}


async def run_ffmpeg(cmd, key=None, timeout=None):
    """Run a short ffmpeg job (trim, screenshots …) – cancellable when `key` is a registered job."""
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    if key is not None:
        jobs.attach(key, proc)
    try:
        _out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "timeout"
    return proc.returncode, err.decode(errors="ignore")[-400:]


async def trim(filepath, start, end, key=None, out_dir=None):
    """Lossless cut → new file path (or None)."""
    base, ext = os.path.splitext(os.path.basename(filepath))
    out = os.path.join(out_dir or encode_dir, f"{base}.trim_{int(start)}-{int(end) if end is not None else 'end'}{ext or '.mkv'}")
    code, err = await run_ffmpeg(ffcmd.trim_command(filepath, out, start, end), key)
    if code != 0 or not os.path.isfile(out) or os.path.getsize(out) == 0:
        LOGGER.error(f"trim failed ({code}): {err}")
        return None
    return out


async def screenshots(filepath, count, duration=None, key=None, out_dir=None):
    """`count` evenly spread JPEG screenshots → list of paths."""
    if not duration:
        duration = (await probe(filepath))["duration"]
    base = os.path.join(out_dir or encode_dir, f"shot_{int(time.time() * 1000)}")
    shots = []
    for i, at in enumerate(ffcmd.screenshot_times(duration, count)):
        if key is not None and jobs.is_cancelled(key):
            break
        out = f"{base}_{i + 1:02d}.jpg"
        code, _ = await run_ffmpeg(ffcmd.screenshot_command(filepath, out, at), key, timeout=120)
        if code == 0 and os.path.isfile(out) and os.path.getsize(out):
            shots.append((out, at))
    return shots


def summary_text(result, new_size: int, link: str | None = None, title="Encode complete") -> str:
    """Before → after card shown once the upload is done."""
    info, s = result.info or {}, ffcmd.merge(result.settings)
    d = ffcmd.describe(s)
    old = info.get("size") or 0
    lines = [hdr("✅", title), f"<code>{_html.escape(os.path.basename(str(result))[:80])}</code>", ""]
    if result.sample:
        start, length = result.sample
        lines.append(row("Sample", f"{ffcmd.fmt_ts(start)} → {ffcmd.fmt_ts(start + length)} ({int(length)}s)"))
        lines.append(row("Sample size", humanbytes(new_size) or "0 B"))
        if info.get("duration") and length:
            full = int(new_size * info["duration"] / length)
            ratio = f" ({(1 - full / old) * 100:+.0f}%)".replace("+", "−", 1) if old and full < old else ""
            lines.append(row("Full video ≈", f"{humanbytes(full)}{ratio}"))
    else:
        if old:
            pct = (1 - new_size / old) * 100 if old else 0
            change = f"−{pct:.1f}%" if pct >= 0 else f"+{-pct:.1f}%"
            lines.append(row("Size", f"{humanbytes(old)} → <b>{humanbytes(new_size)}</b> ({change})"))
        else:
            lines.append(row("Size", humanbytes(new_size) or "0 B"))
    speed = ""
    if info.get("duration") and result.elapsed:
        length = result.sample[1] if result.sample else info["duration"]
        speed = f" · {length / result.elapsed:.2f}x"
    lines.append(row("Time", f"{TimeFormatter(result.elapsed)}{speed}"))
    lines.append(row("Video", f"{d['codec']} · {d['quality']} · {d['resolution']} · {d['preset']}"))
    lines.append(row("Audio", f"{d['audio']} · {d['audio_bitrate']} · {d['channels']}"))
    if d["filters"] != "None":
        lines.append(row("Filters", d["filters"]))
    if result.sample:
        lines += ["", hint("Happy with it? Send /dl to encode the whole file with these settings.")]
    return "\n".join(lines)


def get_thumbnail(in_filename, path, ttl):
    out_filename = os.path.join(path, str(time.time()) + ".jpg")
    try:
        # ffmpeg -ss <ttl> -i <in_filename> -vframes 1 -y <out_filename>
        command = [
            'ffmpeg', '-hide_banner', '-loglevel', 'error',
            '-ss', str(ttl),
            '-i', in_filename,
            '-vframes', '1',
            # Telegram ignores thumbnails over 320 px / 200 KB – a full-size 1080p frame never showed up
            '-vf', 'scale=320:320:force_original_aspect_ratio=decrease', '-q:v', '4',
            '-y', out_filename
        ]
        subprocess.run(command, check=True, capture_output=True)
        if os.path.isfile(out_filename):
            return out_filename
        else:
            LOGGER.warning(f"Thumbnail file not created: {out_filename}")
            return None
    except subprocess.CalledProcessError as e:
        LOGGER.warning(f"Thumbnail generation failed (CalledProcessError): {e.stderr.decode().strip() if e.stderr else e}")
        return None
    except Exception as e:
        LOGGER.warning(f"Thumbnail generation failed: {e}")
        return None


def get_duration(filepath):
    try:
        # Try using ffprobe first
        cmd = [
            'ffprobe', '-v', 'error', '-show_entries',
            'format=duration', '-of',
            'default=noprint_wrappers=1:nokey=1', filepath
        ]
        output = subprocess.check_output(cmd).decode('utf-8').strip()
        return int(float(output))
    except Exception as e:
        LOGGER.warning(f"ffprobe duration failed: {e}, falling back to hachoir")
        try:
            metadata = extractMetadata(createParser(filepath))
            if metadata and metadata.has("duration"):
                return metadata.get('duration').seconds
        except Exception as e:
            LOGGER.error(f"hachoir duration failed: {e}")
    return 0


def get_width_height(filepath):
    try:
        # Try using ffprobe first
        cmd = [
            'ffprobe', '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height', '-of',
            'csv=s=x:p=0', filepath
        ]
        output = subprocess.check_output(cmd).decode('utf-8').strip()
        width, height = map(int, output.split('x'))
        return width, height
    except Exception as e:
        LOGGER.warning(f"ffprobe width/height failed: {e}, falling back to hachoir")
        try:
            metadata = extractMetadata(createParser(filepath))
            if metadata and metadata.has("width") and metadata.has("height"):
                return metadata.get("width"), metadata.get("height")
        except Exception as e:
            LOGGER.error(f"hachoir width/height failed: {e}")
    return (1280, 720)


def _media_info_sync(saved_file_path):
    try:
        process = subprocess.run(['ffmpeg', "-hide_banner", '-i', saved_file_path],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        output = process.stdout.decode(errors="ignore").strip()
    except Exception:
        return None, None
    duration = re.search(r"Duration:\s*(\d*):(\d*):(\d+\.?\d*)[\s\w*$]", output)
    bitrates = re.search(r"bitrate:\s*(\d+)[\s\w*$]", output)
    if duration is not None:
        total_seconds = int(duration.group(1)) * 3600 + int(duration.group(2)) * 60 + \
            math.floor(float(duration.group(3)))
    else:
        total_seconds = None
    return total_seconds, (bitrates.group(1) if bitrates is not None else None)


async def media_info(saved_file_path):
    """(duration seconds, bitrate) – runs ffmpeg in a worker thread (it used to block the event loop)."""
    return await asyncio.to_thread(_media_info_sync, saved_file_path)


def _last(pattern, text, cast=float):
    found = re.findall(pattern, text)
    for v in reversed(found):
        try:
            return cast(v)
        except (TypeError, ValueError):
            continue
    return None


def progress_text(name, elapsed_media, total_time, speed, fps, size_now, wall, settings=None, stage="Encoding"):
    """The live progress card (pure – unit tested)."""
    lines = [hdr("🎬", stage), f"<code>{_html.escape(name[:60])}</code>", ""]
    if not total_time:
        lines.append(row("Done", TimeFormatter(elapsed_media) or '0s'))
        eta = "-"
        pct = None
    else:
        pct = max(0, min(100, math.floor(elapsed_media * 100 / total_time)))
        filled = math.floor(pct / 5)
        remaining = math.floor((total_time - elapsed_media) / speed) if speed and speed > 0 else 0
        eta = TimeFormatter(remaining) if remaining > 0 else "-"
        lines.append(f"<code>[{'█' * filled}{'░' * (20 - filled)}]</code> <b>{pct}%</b>")
        lines.append(row("Encoded", f"{TimeFormatter(elapsed_media) or '0s'} of {TimeFormatter(total_time)}"))
    speed_txt = f"{speed:g}x" if speed and speed > 0 else "—"
    fps_txt = f" · {fps:g} fps" if fps else ""
    lines.append(row("Speed", f"{speed_txt}{fps_txt} · ETA {eta}"))
    if size_now:
        size_txt = humanbytes(size_now)
        if pct and pct >= 3:
            size_txt += f" → ≈ {humanbytes(size_now * 100 / pct)}"
        lines.append(row("Size", size_txt))
    lines.append(row("Elapsed", TimeFormatter(wall) or "0s"))
    if settings:
        d = ffcmd.describe(settings)
        lines.append(row("Using", f"{d['codec']} · {d['quality']} · {d['resolution']} · {d['preset']}"))
    return "\n".join(lines)


async def handle_progress(proc, msg, message, filepath, progress_file=None, total_time=None, settings=None,
                          info=None, stage="Encoding"):
    name = os.path.basename(filepath)
    started = time.time()
    progress_file = progress_file or (download_dir + 'process.txt')
    LOGGER.info("ffmpeg_process: " + str(getattr(proc, "pid", "?")))
    last_stats = None
    probed = total_time is not None
    # One waiter for the whole encode: a tick ends early the moment ffmpeg exits, instead of the next
    # stage starting up to 5 s late (twice for 2-pass). Test doubles without wait() fall back to sleep.
    waiter = asyncio.ensure_future(proc.wait()) if asyncio.iscoroutinefunction(getattr(proc, "wait", None)) else None
    try:
        while proc.returncode is None:
            if waiter is not None:
                await asyncio.wait({waiter}, timeout=PROGRESS_TICK)
                if waiter.done():
                    break
            else:
                await asyncio.sleep(PROGRESS_TICK)
            if jobs.is_cancelled(getattr(msg, "id", None)):
                break
            text = _tail(progress_file)
            if text is None:
                continue                  # ffmpeg hasn't written progress yet
            state = re.findall(r"progress=(\w+)", text)
            if state and state[-1] == "end":
                break
            stats = await _progress_stats(text, name, total_time, probed, filepath, started, settings, stage)
            if stats is None:
                continue
            probed, total_time, stats = True, stats[0], stats[1]
            if stats == last_stats:
                continue                  # nothing new – don't spend an API call
            last_stats = stats
            try:
                await msg.edit(text=stats, reply_markup=cancel_markup(getattr(msg, "id", 0)))
            except Exception:
                pass
    finally:
        if waiter is not None and not waiter.done():
            waiter.cancel()


PROGRESS_TICK = 5


def _tail(path: str, size: int = 4096) -> str | None:
    """The last few KB of ffmpeg's -progress file. It gets a ~12-line block appended twice a second, so
    re-reading the whole thing every tick meant scanning megabytes by the end of a long encode."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            f.seek(max(0, end - size))
            return f.read().decode(errors="ignore")
    except OSError:
        return None


async def _progress_stats(text, name, total_time, probed, filepath, started, settings, stage):
    """→ (total_time, card text) or None when this tick can't be rendered."""
    try:
        speed = _last(r"speed=\s*(\d+\.?\d*)x", text)
        if speed is None:
            speed = _last(r"speed=\s*(\d+\.?\d*)", text)
        speed = speed if speed is not None else 1.0
        us = _last(r"out_time_(?:ms|us)=(\d+)", text, int)
        elapsed_media = (us or 0) / 1000000
        fps = _last(r"fps=(\d+\.?\d*)", text)
        size_now = _last(r"total_size=(\d+)", text, int)
        if not probed:
            total_time, _ = await media_info(filepath)   # probe once, not every tick
        return total_time, progress_text(name, elapsed_media, total_time, speed, fps, size_now,
                                         time.time() - started, settings, stage=stage)
    except Exception as e:               # never let a progress glitch orphan the ffmpeg process
        LOGGER.warning(f"encode progress: {e}")
        return None

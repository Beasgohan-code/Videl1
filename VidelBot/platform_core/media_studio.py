"""
Validated Media Processing Studio (`VidelBot/platform_core/media_studio.py`).

Shared backend for Webapp `Media Studio` / `Conversions` and Telegram `/studio`:
  • Magic-byte file signature inspection (rejects spoofed extensions before processing)
  • Video tools (probe, compress, convert, scale, thumbnails, extract audio, subtitle convert/mux,
    trim, split, merge, metadata edit)
  • Audio tools (convert, compress, tags, cover art extract/embed, trim, merge, EBU R128 loudnorm,
    waveform PNG generation)
  • Image tools (convert, resize, compress, thumbnail, EXIF inspect/strip, multi-image to PDF)
  • Document & Archive tools (PDF metadata/merge/split/extract via `pypdf`, safe ZIP create &
    Zip-Slip/Zip-Bomb-guarded extract, MD5/SHA-1/SHA-256 checksums)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import time
import zipfile
from typing import Any

from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter

from platform_core import destinations
from platform_core.providers import sanitize_filename

# Guard against decompression bombs
Image.MAX_IMAGE_PIXELS = 64_000_000
FFMPEG_TIMEOUT = 300.0
MAX_ARCHIVE_EXTRACT_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_FILES = 500


class MediaStudioError(ValueError):
    """Raised when input validation, magic-byte check, or media processing fails."""


STUDIO_CATALOG: dict[str, list[dict[str, str]]] = {
    "video": [
        {"op": "video_probe", "label": "Inspect Video Streams & Metadata"},
        {"op": "video_compress", "label": "Compress Video (H.264 CRF)"},
        {"op": "video_convert", "label": "Convert Container (MP4 / MKV / WebM / MOV)"},
        {"op": "video_scale", "label": "Change Resolution (360p / 480p / 720p / 1080p)"},
        {"op": "video_thumbnails", "label": "Generate Video Thumbnail / Frames"},
        {"op": "video_extract_audio", "label": "Extract Audio Track"},
        {"op": "video_trim", "label": "Trim / Cut Clip"},
        {"op": "video_split", "label": "Split Video into Segments"},
        {"op": "video_merge", "label": "Merge Multiple Videos"},
        {"op": "video_mux_sub", "label": "Mux Subtitle into Video"},
        {"op": "video_sub_convert", "label": "Convert Subtitle (SRT / VTT / ASS)"},
        {"op": "video_metadata", "label": "Edit Video Title / Artist Metadata"},
    ],
    "audio": [
        {"op": "audio_convert", "label": "Convert Audio (MP3 / AAC / M4A / OGG / OPUS / WAV / FLAC)"},
        {"op": "audio_compress", "label": "Compress Audio Bitrate"},
        {"op": "audio_normalize", "label": "Loudness Normalization (EBU R128)"},
        {"op": "audio_waveform", "label": "Generate Waveform Preview PNG"},
        {"op": "audio_tags", "label": "Edit ID3 / Audio Metadata Tags"},
        {"op": "audio_cover_embed", "label": "Embed Cover Art"},
        {"op": "audio_cover_extract", "label": "Extract Cover Art"},
        {"op": "audio_trim", "label": "Trim Audio Clip"},
        {"op": "audio_merge", "label": "Merge Audio Files"},
    ],
    "image": [
        {"op": "image_convert", "label": "Convert Image (PNG / JPG / WebP / BMP / ICO)"},
        {"op": "image_resize", "label": "Resize Dimensions"},
        {"op": "image_compress", "label": "Optimize & Compress Image"},
        {"op": "image_thumbnail", "label": "Generate Thumbnail"},
        {"op": "image_metadata_view", "label": "Inspect Image & EXIF Metadata"},
        {"op": "image_metadata_strip", "label": "Strip EXIF / Privacy Metadata"},
        {"op": "images_to_pdf", "label": "Combine Images into PDF"},
    ],
    "document": [
        {"op": "pdf_metadata", "label": "Inspect PDF Pages & Metadata"},
        {"op": "pdf_merge", "label": "Merge PDF Documents"},
        {"op": "pdf_extract_pages", "label": "Split / Extract PDF Pages"},
        {"op": "archive_zip", "label": "Create ZIP Archive"},
        {"op": "archive_extract", "label": "Safe Extract ZIP Archive"},
        {"op": "checksum", "label": "Generate & Verify MD5 / SHA-1 / SHA-256"},
    ],
}


def detect_magic_kind(path: str) -> str:
    """Inspect real file header magic bytes (not file extension alone)."""
    if not os.path.isfile(path):
        raise MediaStudioError("Input file does not exist.")
    with open(path, "rb") as fp:
        head = fp.read(64)
    if not head:
        raise MediaStudioError("Input file is empty (0 bytes).")
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n") or head.startswith(b"\xff\xd8\xff") or head.startswith((b"GIF87a", b"GIF89a")) or head.startswith(b"BM"):
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "image"
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "zip"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio"
    if head[:4] == b"fLaC" or head[:4] == b"OggS" or head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xf1"):
        return "audio"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "media_ebml"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return "audio"
        return "media_mp4"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video"
    # Subtitle plain text check (UTF-8 without NUL bytes)
    if b"\x00" not in head:
        return "text"
    return "binary"


def _require_kind(path: str, allowed: set[str], label: str) -> str:
    kind = detect_magic_kind(path)
    if kind not in allowed:
        raise MediaStudioError(
            f"Invalid {label} file header (detected '{kind}' magic bytes instead of {sorted(allowed)})."
        )
    return kind


async def _run_cmd(args: list[str], *, timeout: float = FFMPEG_TIMEOUT) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.communicate()
        raise MediaStudioError(f"Media processing timed out after {int(timeout)}s.") from exc
    return (
        proc.returncode or 0,
        out_b.decode("utf-8", errors="replace"),
        err_b.decode("utf-8", errors="replace"),
    )


def _unique_out(out_dir: str, stem: str, suffix: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    clean_stem = sanitize_filename(stem, fallback="output")
    base_stem = os.path.splitext(clean_stem)[0][:120]
    candidate = os.path.join(out_dir, f"{base_stem}{suffix}")
    if os.path.exists(candidate):
        candidate = os.path.join(out_dir, f"{base_stem}_{int(time.time())}{suffix}")
    return candidate


# ─────────────────────────── 1. Video Tools ───────────────────────────
async def video_probe(path: str) -> dict[str, Any]:
    _require_kind(path, {"media_mp4", "media_ebml", "video", "audio"}, "media")
    rc, out, err = await _run_cmd(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
        timeout=30.0,
    )
    if rc != 0:
        raise MediaStudioError(f"ffprobe failed: {err.strip()[-200:]}")
    raw = json.loads(out or "{}")
    fmt = raw.get("format") or {}
    streams = raw.get("streams") or []
    v_streams = [s for s in streams if s.get("codec_type") == "video"]
    a_streams = [s for s in streams if s.get("codec_type") == "audio"]
    s_streams = [s for s in streams if s.get("codec_type") == "subtitle"]
    v0 = v_streams[0] if v_streams else {}
    return {
        "container": fmt.get("format_name", ""),
        "duration_sec": round(float(fmt.get("duration") or 0.0), 2),
        "size_bytes": int(fmt.get("size") or os.path.getsize(path)),
        "bit_rate": int(fmt.get("bit_rate") or 0),
        "video_codec": v0.get("codec_name", ""),
        "width": int(v0.get("width") or 0),
        "height": int(v0.get("height") or 0),
        "audio_tracks": len(a_streams),
        "subtitle_tracks": len(s_streams),
        "tags": fmt.get("tags") or {},
    }


async def video_compress(path: str, out_dir: str, *, crf: int = 28, preset: str = "veryfast") -> str:
    _require_kind(path, {"media_mp4", "media_ebml", "video"}, "video")
    crf_val = max(18, min(40, int(crf)))
    pr = preset if preset in ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium") else "veryfast"
    out = _unique_out(out_dir, os.path.basename(path), "_compressed.mp4")
    rc, _, err = await _run_cmd(
        [
            "ffmpeg", "-y", "-i", path,
            "-c:v", "libx264", "-preset", pr, "-crf", str(crf_val),
            "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out,
        ]
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Video compression failed: {err.strip()[-220:]}")
    return out


async def video_convert(path: str, out_dir: str, *, target_ext: str = "mp4") -> str:
    _require_kind(path, {"media_mp4", "media_ebml", "video"}, "video")
    ext = target_ext.lower().lstrip(".")
    if ext not in ("mp4", "mkv", "webm", "mov"):
        raise MediaStudioError("Supported video containers: mp4, mkv, webm, mov.")
    out = _unique_out(out_dir, os.path.basename(path), f"_converted.{ext}")
    if ext == "webm":
        cmd = ["ffmpeg", "-y", "-i", path, "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "33", "-c:a", "libopus", out]
    else:
        cmd = ["ffmpeg", "-y", "-i", path, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out]
    rc, _, err = await _run_cmd(cmd)
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Video conversion failed: {err.strip()[-220:]}")
    return out


async def video_scale(path: str, out_dir: str, *, height: int = 720) -> str:
    _require_kind(path, {"media_mp4", "media_ebml", "video"}, "video")
    h = int(height)
    if h not in (240, 360, 480, 720, 1080):
        raise MediaStudioError("Supported target heights: 240, 360, 480, 720, 1080.")
    out = _unique_out(out_dir, os.path.basename(path), f"_{h}p.mp4")
    rc, _, err = await _run_cmd(
        [
            "ffmpeg", "-y", "-i", path,
            "-vf", f"scale=-2:{h}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
            "-c:a", "aac", out,
        ]
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Resolution scaling failed: {err.strip()[-220:]}")
    return out


async def video_thumbnails(path: str, out_dir: str, *, at_sec: float = 0.5) -> str:
    _require_kind(path, {"media_mp4", "media_ebml", "video"}, "video")
    out = _unique_out(out_dir, os.path.basename(path), "_thumb.png")
    rc, _, err = await _run_cmd(
        ["ffmpeg", "-y", "-ss", str(max(0.0, float(at_sec))), "-i", path, "-frames:v", "1", out],
        timeout=60.0,
    )
    if rc != 0 or not os.path.isfile(out):
        # Fallback to frame 0 for ultra-short clips
        rc, _, err = await _run_cmd(["ffmpeg", "-y", "-i", path, "-frames:v", "1", out], timeout=60.0)
        if rc != 0 or not os.path.isfile(out):
            raise MediaStudioError(f"Thumbnail generation failed: {err.strip()[-200:]}")
    return out


async def video_extract_audio(path: str, out_dir: str, *, fmt: str = "mp3") -> str:
    _require_kind(path, {"media_mp4", "media_ebml", "video"}, "video")
    ext = fmt.lower().lstrip(".")
    codec_map = {"mp3": "libmp3lame", "aac": "aac", "m4a": "aac", "wav": "pcm_s16le", "opus": "libopus", "flac": "flac"}
    if ext not in codec_map:
        raise MediaStudioError(f"Unsupported extracted audio format '{ext}'.")
    out = _unique_out(out_dir, os.path.basename(path), f"_audio.{ext}")
    rc, _, err = await _run_cmd(["ffmpeg", "-y", "-i", path, "-vn", "-c:a", codec_map[ext], out])
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Audio extraction failed: {err.strip()[-200:]}")
    return out


async def video_trim(path: str, out_dir: str, *, start_sec: float, end_sec: float) -> str:
    _require_kind(path, {"media_mp4", "media_ebml", "video"}, "video")
    st = max(0.0, float(start_sec))
    en = float(end_sec)
    if en <= st:
        raise MediaStudioError("end_sec must be greater than start_sec.")
    ext = os.path.splitext(path)[1] or ".mp4"
    out = _unique_out(out_dir, os.path.basename(path), f"_trim{ext}")
    rc, _, err = await _run_cmd(
        ["ffmpeg", "-y", "-ss", str(st), "-to", str(en), "-i", path, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out]
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Video trim failed: {err.strip()[-200:]}")
    return out


async def video_split_segments(path: str, out_dir: str, *, segment_sec: int = 30) -> list[str]:
    _require_kind(path, {"media_mp4", "media_ebml", "video"}, "video")
    seg = max(1, min(7200, int(segment_sec)))
    os.makedirs(out_dir, exist_ok=True)
    stem = sanitize_filename(os.path.splitext(os.path.basename(path))[0], fallback="clip")[:80]
    pattern = os.path.join(out_dir, f"{stem}_part%03d.mp4")
    rc, _, err = await _run_cmd(
        [
            "ffmpeg", "-y", "-i", path,
            "-c:v", "libx264", "-preset", "veryfast", "-g", "30",
            "-c:a", "aac", "-f", "segment", "-segment_time", str(seg),
            "-reset_timestamps", "1", pattern,
        ]
    )
    created = sorted(
        os.path.join(out_dir, f) for f in os.listdir(out_dir) if f.startswith(f"{stem}_part") and f.endswith(".mp4")
    )
    if rc != 0 or not created:
        raise MediaStudioError(f"Video split failed: {err.strip()[-200:]}")
    return created


async def video_merge(paths: list[str], out_dir: str, *, out_name: str = "merged_video.mp4") -> str:
    if len(paths) < 2:
        raise MediaStudioError("Select at least 2 videos to merge.")
    for p in paths:
        _require_kind(p, {"media_mp4", "media_ebml", "video"}, "video")
    out = _unique_out(out_dir, out_name, ".mp4")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as lst:
        list_path = lst.name
        for p in paths:
            escaped = os.path.abspath(p).replace("'", "'\\''")
            lst.write(f"file '{escaped}'\n")
    try:
        rc, _, err = await _run_cmd(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", out]
        )
    finally:
        try:
            os.remove(list_path)
        except OSError:
            pass
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Video merge failed: {err.strip()[-200:]}")
    return out


async def video_subtitle_convert(sub_path: str, out_dir: str, *, target_ext: str = "vtt") -> str:
    _require_kind(sub_path, {"text"}, "subtitle")
    ext = target_ext.lower().lstrip(".")
    if ext not in ("srt", "vtt", "ass"):
        raise MediaStudioError("Supported subtitle formats: srt, vtt, ass.")
    out = _unique_out(out_dir, os.path.basename(sub_path), f".{ext}")
    rc, _, err = await _run_cmd(["ffmpeg", "-y", "-i", sub_path, out], timeout=45.0)
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Subtitle conversion failed: {err.strip()[-200:]}")
    return out


async def video_mux_subtitle(video_path: str, sub_path: str, out_dir: str) -> str:
    _require_kind(video_path, {"media_mp4", "media_ebml", "video"}, "video")
    _require_kind(sub_path, {"text"}, "subtitle")
    out = _unique_out(out_dir, os.path.basename(video_path), "_subtitled.mkv")
    rc, _, err = await _run_cmd(
        ["ffmpeg", "-y", "-i", video_path, "-i", sub_path, "-c", "copy", "-c:s", "srt", out]
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Subtitle mux failed: {err.strip()[-200:]}")
    return out


async def video_edit_metadata(path: str, out_dir: str, *, title: str = "", artist: str = "", comment: str = "") -> str:
    _require_kind(path, {"media_mp4", "media_ebml", "video"}, "video")
    ext = os.path.splitext(path)[1] or ".mp4"
    out = _unique_out(out_dir, os.path.basename(path), f"_meta{ext}")
    cmd = ["ffmpeg", "-y", "-i", path, "-c", "copy"]
    if title:
        cmd += ["-metadata", f"title={title[:180]}"]
    if artist:
        cmd += ["-metadata", f"artist={artist[:180]}"]
    if comment:
        cmd += ["-metadata", f"comment={comment[:300]}"]
    cmd.append(out)
    rc, _, err = await _run_cmd(cmd, timeout=60.0)
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Video metadata update failed: {err.strip()[-200:]}")
    return out


# ─────────────────────────── 2. Audio Tools ───────────────────────────
async def audio_convert(path: str, out_dir: str, *, target_ext: str = "mp3", bitrate: str = "192k") -> str:
    _require_kind(path, {"audio", "media_mp4", "media_ebml", "video"}, "audio")
    ext = target_ext.lower().lstrip(".")
    codec_map = {
        "mp3": ("libmp3lame", True),
        "aac": ("aac", True),
        "m4a": ("aac", True),
        "ogg": ("libvorbis", True),
        "opus": ("libopus", True),
        "wav": ("pcm_s16le", False),
        "flac": ("flac", False),
    }
    if ext not in codec_map:
        raise MediaStudioError("Supported audio formats: mp3, aac, m4a, ogg, opus, wav, flac.")
    codec, use_br = codec_map[ext]
    br = bitrate if bitrate in ("64k", "96k", "128k", "192k", "256k", "320k") else "192k"
    out = _unique_out(out_dir, os.path.basename(path), f".{ext}")
    cmd = ["ffmpeg", "-y", "-i", path, "-vn", "-c:a", codec]
    if use_br:
        cmd += ["-b:a", br]
    cmd.append(out)
    rc, _, err = await _run_cmd(cmd)
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Audio conversion failed: {err.strip()[-200:]}")
    return out


async def audio_normalize(path: str, out_dir: str) -> str:
    _require_kind(path, {"audio", "media_mp4", "media_ebml"}, "audio")
    out = _unique_out(out_dir, os.path.basename(path), "_normalized.mp3")
    rc, _, err = await _run_cmd(
        ["ffmpeg", "-y", "-i", path, "-vn", "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "libmp3lame", "-b:a", "192k", out]
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Audio loudness normalization failed: {err.strip()[-200:]}")
    return out


async def audio_waveform(path: str, out_dir: str, *, width: int = 800, height: int = 200) -> str:
    _require_kind(path, {"audio", "media_mp4", "media_ebml", "video"}, "audio")
    w = max(200, min(1920, int(width)))
    h = max(80, min(600, int(height)))
    out = _unique_out(out_dir, os.path.basename(path), "_waveform.png")
    rc, _, err = await _run_cmd(
        [
            "ffmpeg", "-y", "-i", path,
            "-filter_complex", f"aformat=channel_layouts=mono,showwavespic=s={w}x{h}:colors=#2563eb",
            "-frames:v", "1", out,
        ],
        timeout=60.0,
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Waveform preview failed: {err.strip()[-200:]}")
    return out


async def audio_edit_tags(path: str, out_dir: str, *, title: str = "", artist: str = "", album: str = "") -> str:
    _require_kind(path, {"audio", "media_mp4"}, "audio")
    ext = os.path.splitext(path)[1] or ".mp3"
    out = _unique_out(out_dir, os.path.basename(path), f"_tagged{ext}")
    cmd = ["ffmpeg", "-y", "-i", path, "-c", "copy"]
    if title:
        cmd += ["-metadata", f"title={title[:180]}"]
    if artist:
        cmd += ["-metadata", f"artist={artist[:180]}"]
    if album:
        cmd += ["-metadata", f"album={album[:180]}"]
    cmd.append(out)
    rc, _, err = await _run_cmd(cmd, timeout=60.0)
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Audio tag edit failed: {err.strip()[-200:]}")
    return out


async def audio_embed_cover(audio_path: str, image_path: str, out_dir: str) -> str:
    _require_kind(audio_path, {"audio", "media_mp4"}, "audio")
    _require_kind(image_path, {"image"}, "image")
    out = _unique_out(out_dir, os.path.basename(audio_path), "_cover.mp3")
    rc, _, err = await _run_cmd(
        [
            "ffmpeg", "-y", "-i", audio_path, "-i", image_path,
            "-map", "0:a:0", "-map", "1:v:0",
            "-c:a", "libmp3lame", "-b:a", "192k", "-c:v", "mjpeg",
            "-id3v2_version", "3", out,
        ],
        timeout=90.0,
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Cover art embed failed: {err.strip()[-200:]}")
    return out


async def audio_extract_cover(audio_path: str, out_dir: str) -> str:
    _require_kind(audio_path, {"audio", "media_mp4"}, "audio")
    out = _unique_out(out_dir, os.path.basename(audio_path), "_cover.jpg")
    rc, _, err = await _run_cmd(
        ["ffmpeg", "-y", "-i", audio_path, "-an", "-vcodec", "mjpeg", "-frames:v", "1", out],
        timeout=45.0,
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError("Audio file does not contain an embedded cover art stream.")
    return out


async def audio_trim(path: str, out_dir: str, *, start_sec: float, end_sec: float) -> str:
    _require_kind(path, {"audio", "media_mp4", "media_ebml"}, "audio")
    st = max(0.0, float(start_sec))
    en = float(end_sec)
    if en <= st:
        raise MediaStudioError("end_sec must be greater than start_sec.")
    out = _unique_out(out_dir, os.path.basename(path), "_trim.mp3")
    rc, _, err = await _run_cmd(
        ["ffmpeg", "-y", "-ss", str(st), "-to", str(en), "-i", path, "-vn", "-c:a", "libmp3lame", "-b:a", "192k", out]
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Audio trim failed: {err.strip()[-200:]}")
    return out


async def audio_merge(paths: list[str], out_dir: str, *, out_name: str = "merged_audio.mp3") -> str:
    if len(paths) < 2:
        raise MediaStudioError("Select at least 2 audio files to merge.")
    for p in paths:
        _require_kind(p, {"audio", "media_mp4", "media_ebml"}, "audio")
    out = _unique_out(out_dir, out_name, ".mp3")
    inputs: list[str] = []
    filter_parts: list[str] = []
    for idx, p in enumerate(paths):
        inputs += ["-i", p]
        filter_parts.append(f"[{idx}:a:0]")
    flt = "".join(filter_parts) + f"concat=n={len(paths)}:v=0:a=1[outa]"
    rc, _, err = await _run_cmd(
        ["ffmpeg", "-y", *inputs, "-filter_complex", flt, "-map", "[outa]", "-c:a", "libmp3lame", "-b:a", "192k", out]
    )
    if rc != 0 or not os.path.isfile(out):
        raise MediaStudioError(f"Audio merge failed: {err.strip()[-200:]}")
    return out


# ─────────────────────────── 3. Image Tools ───────────────────────────
def image_convert_sync(path: str, out_dir: str, *, target_fmt: str = "webp", quality: int = 85) -> str:
    _require_kind(path, {"image"}, "image")
    fmt_key = target_fmt.lower().lstrip(".")
    pil_map = {"png": ("PNG", ".png"), "jpg": ("JPEG", ".jpg"), "jpeg": ("JPEG", ".jpg"), "webp": ("WEBP", ".webp"), "bmp": ("BMP", ".bmp"), "ico": ("ICO", ".ico")}
    if fmt_key not in pil_map:
        raise MediaStudioError("Supported image formats: png, jpg, webp, bmp, ico.")
    pil_fmt, suffix = pil_map[fmt_key]
    q = max(10, min(100, int(quality)))
    out = _unique_out(out_dir, os.path.basename(path), suffix)
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if pil_fmt in ("JPEG", "BMP") and img.mode in ("RGBA", "LA", "P"):
            bg_img = Image.new("RGB", img.size, (255, 255, 255))
            rgba = img.convert("RGBA")
            bg_img.paste(rgba, mask=rgba.split()[-1])
            img = bg_img
        elif pil_fmt == "ICO":
            img = img.convert("RGBA")
            img.thumbnail((256, 256))
        save_kw: dict[str, Any] = {}
        if pil_fmt in ("JPEG", "WEBP"):
            save_kw["quality"] = q
            save_kw["optimize"] = True
        img.save(out, format=pil_fmt, **save_kw)
    return out


def image_resize_sync(
    path: str, out_dir: str, *, width: int, height: int, keep_aspect: bool = True
) -> str:
    _require_kind(path, {"image"}, "image")
    w = max(16, min(8192, int(width)))
    h = max(16, min(8192, int(height)))
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        ext = ".png"
    out = _unique_out(out_dir, os.path.basename(path), f"_{w}x{h}{ext}")
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if keep_aspect:
            copy_img = img.copy()
            copy_img.thumbnail((w, h), Image.Resampling.LANCZOS)
            copy_img.save(out)
        else:
            resized = img.resize((w, h), Image.Resampling.LANCZOS)
            resized.save(out)
    return out


def image_inspect_metadata_sync(path: str) -> dict[str, Any]:
    _require_kind(path, {"image"}, "image")
    with Image.open(path) as img:
        exif = img.getexif()
        exif_dict = {str(k): str(v)[:160] for k, v in exif.items()} if exif else {}
        return {
            "format": img.format,
            "mode": img.mode,
            "width": img.width,
            "height": img.height,
            "size_bytes": os.path.getsize(path),
            "exif_tag_count": len(exif_dict),
            "exif": exif_dict,
        }


def image_strip_metadata_sync(path: str, out_dir: str) -> str:
    _require_kind(path, {"image"}, "image")
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        ext = ".png"
    out = _unique_out(out_dir, os.path.basename(path), f"_clean{ext}")
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        clean = Image.new(img.mode, img.size)
        clean.putdata(list(img.getdata()))
        clean.save(out)
    return out


def images_to_pdf_sync(paths: list[str], out_dir: str, *, out_name: str = "images_bundle.pdf") -> str:
    if not paths:
        raise MediaStudioError("Provide at least one image to generate a PDF.")
    rgb_pages: list[Image.Image] = []
    try:
        for p in paths:
            _require_kind(p, {"image"}, "image")
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im)
                rgb_pages.append(im.convert("RGB"))
        out = _unique_out(out_dir, out_name, ".pdf")
        rgb_pages[0].save(out, format="PDF", save_all=True, append_images=rgb_pages[1:])
        return out
    finally:
        for pg in rgb_pages:
            pg.close()


# ─────────────────────────── 4. Document (PDF) & Archive Tools ───────────────────────────
def pdf_metadata_sync(path: str) -> dict[str, Any]:
    _require_kind(path, {"pdf"}, "PDF")
    reader = PdfReader(path)
    meta = reader.metadata or {}
    return {
        "pages": len(reader.pages),
        "encrypted": bool(reader.is_encrypted),
        "title": str(getattr(meta, "title", "") or ""),
        "author": str(getattr(meta, "author", "") or ""),
        "creator": str(getattr(meta, "creator", "") or ""),
        "size_bytes": os.path.getsize(path),
    }


def pdf_merge_sync(paths: list[str], out_dir: str, *, out_name: str = "merged.pdf") -> str:
    if len(paths) < 2:
        raise MediaStudioError("Provide at least 2 PDF files to merge.")
    writer = PdfWriter()
    for p in paths:
        _require_kind(p, {"pdf"}, "PDF")
        reader = PdfReader(p)
        for pg in reader.pages:
            writer.add_page(pg)
    out = _unique_out(out_dir, out_name, ".pdf")
    with open(out, "wb") as fp:
        writer.write(fp)
    return out


def pdf_extract_pages_sync(path: str, out_dir: str, *, start_page: int = 1, end_page: int = 1) -> str:
    _require_kind(path, {"pdf"}, "PDF")
    reader = PdfReader(path)
    total = len(reader.pages)
    if total == 0:
        raise MediaStudioError("PDF has 0 pages.")
    s = max(1, min(total, int(start_page)))
    e = max(s, min(total, int(end_page)))
    writer = PdfWriter()
    for idx in range(s - 1, e):
        writer.add_page(reader.pages[idx])
    out = _unique_out(out_dir, os.path.basename(path), f"_p{s}-{e}.pdf")
    with open(out, "wb") as fp:
        writer.write(fp)
    return out


def archive_create_zip_sync(paths: list[str], out_dir: str, *, out_name: str = "archive.zip") -> str:
    if not paths:
        raise MediaStudioError("Select at least one file to archive.")
    out = _unique_out(out_dir, out_name, ".zip")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        used_names: set[str] = set()
        for p in paths:
            if not os.path.isfile(p):
                raise MediaStudioError(f"File not found: {os.path.basename(p)}")
            arc = sanitize_filename(os.path.basename(p))
            if arc in used_names:
                stem, ext = os.path.splitext(arc)
                arc = f"{stem}_{len(used_names)}{ext}"
            used_names.add(arc)
            zf.write(p, arcname=arc)
    return out


def archive_extract_safe_sync(
    archive_path: str,
    out_dir: str,
    *,
    max_total_bytes: int = MAX_ARCHIVE_EXTRACT_BYTES,
    max_files: int = MAX_ARCHIVE_FILES,
) -> list[str]:
    """Safely extract a ZIP archive while blocking Zip-Slip (`..`/abs/symlink) and Zip-Bombs."""
    _require_kind(archive_path, {"zip"}, "ZIP archive")
    extract_root = os.path.realpath(_unique_out(out_dir, os.path.basename(archive_path), "_extracted"))
    os.makedirs(extract_root, exist_ok=True)
    extracted_paths: list[str] = []
    cumulative = 0

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                raise MediaStudioError(f"Archive contains {len(infos)} entries (> {max_files} safety limit).")
            for info in infos:
                raw_name = info.filename.replace("\\", "/")
                if raw_name.endswith("/"):
                    continue
                parts = [seg for seg in raw_name.split("/") if seg]
                if not parts or raw_name.startswith("/") or any(seg in (".", "..") for seg in parts):
                    raise MediaStudioError(f"Blocked unsafe Zip-Slip archive entry: '{info.filename}'")
                # Reject Unix symlinks inside ZIP (S_IFLNK = 0o120000)
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise MediaStudioError(f"Blocked symbolic link inside archive: '{info.filename}'")
                if info.file_size > max_total_bytes or (cumulative + info.file_size) > max_total_bytes:
                    raise MediaStudioError("Archive uncompressed size exceeds safety quota (Zip-Bomb protection).")
                dest_file = os.path.realpath(os.path.join(extract_root, *parts))
                if not dest_file.startswith(extract_root + os.sep):
                    raise MediaStudioError("Blocked archive path traversal outside extraction root.")
                os.makedirs(os.path.dirname(dest_file), exist_ok=True)
                with zf.open(info, "r") as src, open(dest_file, "wb") as dst:
                    while True:
                        chunk = src.read(65536)
                        if not chunk:
                            break
                        cumulative += len(chunk)
                        if cumulative > max_total_bytes:
                            raise MediaStudioError("Archive stream exceeded uncompressed byte quota.")
                        dst.write(chunk)
                extracted_paths.append(dest_file)
    except Exception:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise

    return extracted_paths


def file_checksums_sync(path: str, *, expected: str = "") -> dict[str, Any]:
    if not os.path.isfile(path):
        raise MediaStudioError("File does not exist.")
    md5 = hashlib.md5()
    sha1 = hashlib.sha1()
    sha256 = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(262144), b""):
            md5.update(block)
            sha1.update(block)
            sha256.update(block)
    digests = {
        "md5": md5.hexdigest(),
        "sha1": sha1.hexdigest(),
        "sha256": sha256.hexdigest(),
        "size_bytes": os.path.getsize(path),
    }
    if expected:
        exp = expected.strip().lower()
        digests["verified"] = exp in (digests["md5"], digests["sha1"], digests["sha256"])
    return digests


# ─────────────────────────── Unified User-Sandboxed Studio Runner ───────────────────────────
async def run_studio_operation(user_id: int, op: str, params: dict[str, Any], *, workspace_id: str | None = None) -> dict[str, Any]:
    """Run in legacy user storage or an explicitly selected isolated media sandbox."""
    uid = int(user_id)
    udir = destinations.user_workspace_dir(uid) if workspace_id is None else destinations.media_workspace_dir(uid, workspace_id)

    def _in_file(key: str = "file") -> str:
        return destinations.resolve_user_path(uid, str(params.get(key) or ""), must_exist=True, workspace_id=workspace_id)

    def _in_files(key: str = "files") -> list[str]:
        raw = params.get(key) or []
        if isinstance(raw, str):
            raw = [x.strip() for x in raw.split(",") if x.strip()]
        return [destinations.resolve_user_path(uid, str(p), must_exist=True, workspace_id=workspace_id) for p in raw]

    def _rel(p: str) -> str:
        return os.path.relpath(p, udir).replace("\\", "/")

    # Unique output directories prevent concurrent jobs from overwriting one another.
    out_dir = udir if workspace_id is None else os.path.join(udir, "outputs", secrets.token_hex(16))
    action = (op or "").strip().lower()

    if action == "video_probe":
        return {"op": action, "info": await video_probe(_in_file())}
    if action == "video_compress":
        out = await video_compress(_in_file(), out_dir, crf=int(params.get("crf") or 28), preset=str(params.get("preset") or "veryfast"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_convert":
        out = await video_convert(_in_file(), out_dir, target_ext=str(params.get("format") or "mp4"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_scale":
        out = await video_scale(_in_file(), out_dir, height=int(params.get("height") or 720))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_thumbnails":
        out = await video_thumbnails(_in_file(), out_dir, at_sec=float(params.get("at_sec") or 0.5))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_extract_audio":
        out = await video_extract_audio(_in_file(), out_dir, fmt=str(params.get("format") or "mp3"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_trim":
        out = await video_trim(_in_file(), out_dir, start_sec=float(params.get("start_sec") or 0), end_sec=float(params.get("end_sec") or 5))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_split":
        outs = await video_split_segments(_in_file(), out_dir, segment_sec=int(params.get("segment_sec") or 30))
        return {"op": action, "outputs": [_rel(p) for p in outs]}
    if action == "video_merge":
        out = await video_merge(_in_files(), out_dir, out_name=str(params.get("out_name") or "merged.mp4"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_sub_convert":
        out = await video_subtitle_convert(_in_file(), out_dir, target_ext=str(params.get("format") or "vtt"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_mux_sub":
        out = await video_mux_subtitle(_in_file("file"), _in_file("subtitle_file"), out_dir)
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "video_metadata":
        out = await video_edit_metadata(
            _in_file(), out_dir,
            title=str(params.get("title") or ""),
            artist=str(params.get("artist") or ""),
            comment=str(params.get("comment") or ""),
        )
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}

    if action == "audio_convert":
        out = await audio_convert(_in_file(), out_dir, target_ext=str(params.get("format") or "mp3"), bitrate=str(params.get("bitrate") or "192k"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "audio_compress":
        out = await audio_convert(_in_file(), out_dir, target_ext="mp3", bitrate=str(params.get("bitrate") or "96k"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "audio_normalize":
        out = await audio_normalize(_in_file(), out_dir)
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "audio_waveform":
        out = await audio_waveform(_in_file(), out_dir)
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "audio_tags":
        out = await audio_edit_tags(
            _in_file(), out_dir,
            title=str(params.get("title") or ""),
            artist=str(params.get("artist") or ""),
            album=str(params.get("album") or ""),
        )
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "audio_cover_embed":
        out = await audio_embed_cover(_in_file("file"), _in_file("cover_file"), out_dir)
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "audio_cover_extract":
        out = await audio_extract_cover(_in_file(), out_dir)
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "audio_trim":
        out = await audio_trim(_in_file(), out_dir, start_sec=float(params.get("start_sec") or 0), end_sec=float(params.get("end_sec") or 5))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "audio_merge":
        out = await audio_merge(_in_files(), out_dir, out_name=str(params.get("out_name") or "merged.mp3"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}

    if action == "image_convert":
        out = await asyncio.to_thread(image_convert_sync, _in_file(), out_dir, target_fmt=str(params.get("format") or "webp"), quality=int(params.get("quality") or 85))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "image_compress":
        out = await asyncio.to_thread(image_convert_sync, _in_file(), out_dir, target_fmt=str(params.get("format") or "webp"), quality=int(params.get("quality") or 70))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "image_resize":
        out = await asyncio.to_thread(
            image_resize_sync, _in_file(), out_dir,
            width=int(params.get("width") or 800),
            height=int(params.get("height") or 600),
            keep_aspect=bool(params.get("keep_aspect", True)),
        )
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "image_thumbnail":
        out = await asyncio.to_thread(image_resize_sync, _in_file(), out_dir, width=320, height=320, keep_aspect=True)
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "image_metadata_view":
        info = await asyncio.to_thread(image_inspect_metadata_sync, _in_file())
        return {"op": action, "info": info}
    if action == "image_metadata_strip":
        out = await asyncio.to_thread(image_strip_metadata_sync, _in_file(), out_dir)
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "images_to_pdf":
        out = await asyncio.to_thread(images_to_pdf_sync, _in_files(), out_dir, out_name=str(params.get("out_name") or "bundle.pdf"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}

    if action == "pdf_metadata":
        info = await asyncio.to_thread(pdf_metadata_sync, _in_file())
        return {"op": action, "info": info}
    if action == "pdf_merge":
        out = await asyncio.to_thread(pdf_merge_sync, _in_files(), out_dir, out_name=str(params.get("out_name") or "merged.pdf"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "pdf_extract_pages":
        out = await asyncio.to_thread(
            pdf_extract_pages_sync, _in_file(), out_dir,
            start_page=int(params.get("start_page") or 1),
            end_page=int(params.get("end_page") or 1),
        )
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "archive_zip":
        out = await asyncio.to_thread(archive_create_zip_sync, _in_files(), out_dir, out_name=str(params.get("out_name") or "archive.zip"))
        return {"op": action, "output": _rel(out), "size_bytes": os.path.getsize(out)}
    if action == "archive_extract":
        outs = await asyncio.to_thread(archive_extract_safe_sync, _in_file(), out_dir)
        return {"op": action, "outputs": [_rel(p) for p in outs]}
    if action == "checksum":
        info = await asyncio.to_thread(file_checksums_sync, _in_file(), expected=str(params.get("expected") or ""))
        return {"op": action, "info": info}

    raise MediaStudioError(f"Unsupported Media Studio operation '{action}'.")

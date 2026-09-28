"""
Hardware encoder detection (NVIDIA NVENC · Intel QSV · VAAPI) and AV1 software encoder choice.

HW_ENCODER=auto (default) probes once: an encoder only counts as available when ffmpeg lists it
AND a tiny test encode succeeds (a listed nvenc without a GPU / driver fails instantly).
HW_ENCODER=nvenc|qsv|vaapi forces one kind, HW_ENCODER=off disables GPUs.

pick_encoder() falls back to software whenever the GPU can't do what the settings ask for
(e.g. 10-bit H.264, or a kind with no AV1 encoder).
"""
import logging
import os
import subprocess

import config

from . import ffcmd

log = logging.getLogger("VideoEncoder.hw")

_CAPS: dict | None = None


def _ffmpeg_encoders() -> set:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, timeout=30).stdout
    except Exception as e:
        log.warning(f"ffmpeg -encoders failed: {e}")
        return set()
    names = set()
    for line in out.decode(errors="ignore").splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return names


def _test_encode(encoder: str, kind: str) -> bool:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    if kind == "vaapi":
        if not os.path.exists(config.VAAPI_DEVICE):
            return False
        cmd += ["-vaapi_device", config.VAAPI_DEVICE]
    cmd += ["-f", "lavfi", "-i", "color=black:s=256x144:d=0.2"]
    if kind == "vaapi":
        cmd += ["-vf", "format=nv12,hwupload"]
    elif kind == "qsv":
        cmd += ["-pix_fmt", "nv12"]
    else:
        cmd += ["-pix_fmt", "yuv420p"]
    cmd += ["-frames:v", "3", "-c:v", encoder, "-f", "null", os.devnull]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=40).returncode == 0
    except Exception:
        return False


def detect(force: bool = False) -> dict:
    """{'kind': 'nvenc'|'qsv'|'vaapi'|None, 'encoders': {codec: name}, 'av1_sw': name|None, 'listed': set}"""
    global _CAPS
    if _CAPS is not None and not force:
        return _CAPS
    listed = _ffmpeg_encoders()
    av1_sw = next((e for e in ffcmd.AV1_SOFTWARE if e in listed), None)
    want = config.HW_ENCODER
    kinds = [] if want in ("off", "none", "false", "0", "cpu") else (
        [want] if want in ffcmd.HW_ENCODERS else list(ffcmd.HW_ENCODERS))
    found_kind, found = None, {}
    for kind in kinds:
        ok = {codec: enc for codec, enc in ffcmd.HW_ENCODERS[kind].items()
              if enc in listed and _test_encode(enc, kind)}
        if ok:
            found_kind, found = kind, ok
            break
    _CAPS = {"kind": found_kind, "encoders": found, "av1_sw": av1_sw, "listed": listed}
    log.info(f"🎛 encoders: GPU={found_kind or 'none'} {sorted(found.values())} · AV1 software={av1_sw or 'none'}")
    return _CAPS


def cached() -> dict | None:
    """The capability table if detection already ran (never blocks – for menus)."""
    return _CAPS


def set_caps(caps: dict | None):
    """Tests / tooling: inject a capability table (None → detect again next time)."""
    global _CAPS
    _CAPS = caps


def pick_encoder(settings: dict, caps: dict | None = None) -> tuple[str | None, str]:
    """(ffmpeg encoder, note). encoder None = the codec can't be encoded on this server."""
    s = ffcmd.merge(settings)
    caps = caps if caps is not None else detect()
    codec = ffcmd.video_codec(s)
    hw = caps.get("encoders") or {}
    if s["hw"] and codec in hw:
        kind = caps.get("kind")
        if s["bits"] and codec == "h264":
            pass                                  # no GPU does 10-bit H.264 reliably → software
        elif ffcmd.twopass_applies(s):
            pass                                  # exact-size 2-pass → software (x264 / x265)
        else:
            return hw[codec], f"GPU ({kind})"
    if codec == "av1":
        sw = caps.get("av1_sw")
        if not sw:
            return None, "no AV1 encoder in this ffmpeg build"
        return sw, "CPU"
    return ffcmd.SOFTWARE[codec], "CPU"


def summary() -> str:
    caps = detect()
    gpu = f"{caps['kind'].upper()} ({', '.join(sorted(caps['encoders']))})" if caps.get("kind") else "none (CPU only)"
    return f"GPU: {gpu} · AV1: {caps.get('av1_sw') or 'unavailable'}"

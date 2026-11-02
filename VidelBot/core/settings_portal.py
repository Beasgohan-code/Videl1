"""
Safe user preferences Export / Import / Reset portal (`core/settings_portal.py`).

Inspired by WZML-X's `SettingsPortal` `BLOCKED` allowlist architecture, adapted natively
for Videl1's three user-facing preference engines (`encoder`, `renamer`, and `saver`):
  • Exports a clean `videl-user-settings/1` JSON envelope containing ONLY portable,
    non-privileged user preferences.
  • Explicitly strips and blocks secrets (`session`, tokens, API keys), privilege flags
    (`is_premium`, `premium_expiry`, `is_banned`, `sudo`, `adult_ok`), bot-specific file_ids
    (`thumbnail`, `logo_id`), and usage counters (`daily_usage`, `enc_count`, `count`).
  • Validates schema version, byte size (`<= 256 KiB`), field types, enum sets, and numeric
    bounds (`CRF 12..40`, `MAX_WORD_RULES`) BEFORE persisting to MongoDB, updating caches
    strictly AFTER persistence succeeds.
"""
from __future__ import annotations

import datetime
import json
from typing import Any

FORMAT = "videl-user-settings/1"
VERSION = 1
MAX_SETTINGS_BYTES = 256 * 1024

# Keys that must NEVER be exported or accepted on import
BLOCKED_KEYS = frozenset({
    "_id",
    "id",
    "join_date",
    "session",
    "api_id",
    "api_hash",
    "phone",
    "two_step",
    "token",
    "is_premium",
    "premium_expiry",
    "is_banned",
    "sudo",
    "auth",
    "daily_usage",
    "limit_reset_time",
    "total_saves",
    "enc_count",
    "enc_in",
    "enc_out",
    "count",
    "last_ts",
    "adult_ok",
    "thumbnail",
    "thumb",
    "logo_id",
    "dump_chat",
})

_ENC_BOOL_KEYS = frozenset({
    "hevc",
    "av1",
    "aspect",
    "cabac",
    "tune",
    "bits",
    "drive",
    "metadata",
    "hardsub",
    "watermark",
    "subtitles",
    "upload_as_doc",
    "resize",
    "motion_watermark",
    "deinterlace",
    "denoise",
    "loudnorm",
    "dedup",
    "size_guard",
    "twopass",
    "logo",
    "hw",
})

_ENC_ENUMS: dict[str, tuple[str, ...]] = {
    "extensions": ("MP4", "MKV", "AVI"),
    "reframe": ("pass", "4", "8", "16"),
    "frame": ("source", "pal", "film", "23.976", "30", "60", "ntsc"),
    "audio": ("dd", "copy", "aac", "opus", "alac", "vorbis"),
    "sample": ("44.1K", "48K", "source"),
    "bitrate": ("400", "320", "256", "224", "192", "160", "128", "source"),
    "channels": ("source", "1.0", "2.0", "2.1", "5.1", "7.1"),
    "preset": ("uf", "sf", "vf", "f", "m", "s", "sl", "vs"),
    "resolution": ("OG", "1080", "720", "576", "480", "360"),
    "motion_opacity": ("50", "75", "100"),
    "mode": ("crf", "size"),
    "wm_pos": ("tl", "tr", "bl", "br", "c"),
    "wm_size": ("s", "m", "l"),
    "wm_opacity": ("25", "50", "75", "100"),
    "upload_dest": ("telegram", "gdrive", "rclone", "gofile"),
}


class SettingsPortalError(ValueError):
    """Raised when an imported settings document fails validation."""


def _sanitize_encoder_section(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    clean: dict[str, Any] = {}
    skipped: list[str] = []
    for k, v in raw.items():
        if k in BLOCKED_KEYS:
            skipped.append(f"encoder.{k}")
            continue
        if k in _ENC_BOOL_KEYS:
            if isinstance(v, bool):
                clean[k] = v
            else:
                skipped.append(f"encoder.{k}")
        elif k in _ENC_ENUMS:
            sv = str(v)
            if sv in _ENC_ENUMS[k]:
                clean[k] = sv
            else:
                skipped.append(f"encoder.{k}")
        elif k == "crf":
            if isinstance(v, int) and not isinstance(v, bool) and 12 <= v <= 40:
                clean[k] = int(v)
            else:
                skipped.append("encoder.crf")
        elif k == "target_mb":
            if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 4000:
                clean[k] = int(v)
            else:
                skipped.append("encoder.target_mb")
        elif k == "wm_text":
            if isinstance(v, str) and len(v) <= 80:
                clean[k] = v.strip()
            else:
                skipped.append("encoder.wm_text")
        else:
            skipped.append(f"encoder.{k}")
    return clean, skipped


def _sanitize_renamer_section(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    from renamer import store as rn_store

    clean: dict[str, Any] = {}
    skipped: list[str] = []
    for k, v in raw.items():
        if k in BLOCKED_KEYS:
            skipped.append(f"renamer.{k}")
            continue
        if k == "template":
            if isinstance(v, str) and len(v) <= 200:
                clean["template"] = v
            else:
                skipped.append("renamer.template")
        elif k in ("auto", "clean", "mkv", "meta_on"):
            if isinstance(v, bool):
                clean[k] = v
            else:
                skipped.append(f"renamer.{k}")
        elif k == "mode":
            if v in rn_store.MODES:
                clean["mode"] = v
            else:
                skipped.append("renamer.mode")
        elif k == "media":
            if v in rn_store.MEDIA_TYPES or v in ("", None):
                clean["media"] = v or ""
            else:
                skipped.append("renamer.media")
        elif k == "words":
            if isinstance(v, list) and len(v) <= rn_store.MAX_WORD_RULES:
                rules = []
                ok_rules = True
                for pair in v:
                    if (
                        isinstance(pair, (list, tuple))
                        and len(pair) == 2
                        and all(isinstance(x, str) and len(x) <= 80 for x in pair)
                    ):
                        rules.append([pair[0], pair[1]])
                    else:
                        ok_rules = False
                        break
                if ok_rules:
                    clean["words"] = rules
                else:
                    skipped.append("renamer.words")
            else:
                skipped.append("renamer.words")
        elif k == "meta":
            if isinstance(v, dict):
                meta_out = {}
                for mk, mv in v.items():
                    if mk in rn_store.META_FIELDS and isinstance(mv, str) and len(mv) <= 120:
                        meta_out[mk] = mv
                    else:
                        skipped.append(f"renamer.meta.{mk}")
                clean["meta"] = meta_out
            else:
                skipped.append("renamer.meta")
        else:
            skipped.append(f"renamer.{k}")
    return clean, skipped


def _sanitize_saver_section(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    clean: dict[str, Any] = {}
    skipped: list[str] = []
    for k, v in raw.items():
        if k in BLOCKED_KEYS:
            skipped.append(f"saver.{k}")
            continue
        if k == "caption":
            if v is None or (isinstance(v, str) and len(v) <= 1024):
                clean["caption"] = v or None
            else:
                skipped.append("saver.caption")
        elif k == "delete_words":
            if isinstance(v, list) and len(v) <= 50 and all(isinstance(w, str) and len(w) <= 64 for w in v):
                clean["delete_words"] = [w for w in v if w]
            else:
                skipped.append("saver.delete_words")
        elif k == "replace_words":
            if (
                isinstance(v, dict)
                and len(v) <= 50
                and all(
                    isinstance(src, str) and isinstance(dst, str) and 0 < len(src) <= 64 and len(dst) <= 64
                    for src, dst in v.items()
                )
            ):
                clean["replace_words"] = dict(v)
            else:
                skipped.append("saver.replace_words")
        else:
            skipped.append(f"saver.{k}")
    return clean, skipped


async def export_user_settings(user_id: int) -> bytes:
    """Export portable, secret-free preferences for `user_id` as UTF-8 JSON bytes."""
    from database.db import db as saver_db
    from renamer import store as rn_store
    from VideoEncoder.utils.database.access_db import db as enc_db

    uid = int(user_id)
    enc_raw = await enc_db.get_settings(uid) or {}
    enc_clean, _ = _sanitize_encoder_section(
        {k: v for k, v in enc_raw.items() if k not in BLOCKED_KEYS and v is not None}
    )

    rn_raw = await rn_store.get(uid) or {}
    rn_clean, _ = _sanitize_renamer_section(
        {k: v for k, v in rn_raw.items() if k not in BLOCKED_KEYS and v is not None}
    )

    sv_doc = await saver_db.col.find_one({"id": uid}) or {}
    sv_candidates = {
        k: sv_doc.get(k)
        for k in ("caption", "delete_words", "replace_words")
        if sv_doc.get(k) not in (None, [], {})
    }
    sv_clean, _ = _sanitize_saver_section(sv_candidates)

    envelope = {
        "format": FORMAT,
        "version": VERSION,
        "exported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "encoder": enc_clean,
        "renamer": rn_clean,
        "saver": sv_clean,
    }
    return json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")


async def import_user_settings(user_id: int, raw_bytes: bytes | str) -> dict[str, Any]:
    """Validate and apply a `videl-user-settings/1` bundle for `user_id` (persist first, invalidate cache after)."""
    from database.db import db as saver_db
    from renamer import store as rn_store
    from VideoEncoder.utils.database.access_db import db as enc_db

    data_bytes = raw_bytes.encode("utf-8") if isinstance(raw_bytes, str) else bytes(raw_bytes)
    if len(data_bytes) > MAX_SETTINGS_BYTES:
        raise SettingsPortalError(f"Settings file exceeds {MAX_SETTINGS_BYTES // 1024} KiB limit.")
    try:
        doc = json.loads(data_bytes.decode("utf-8"))
    except Exception as exc:
        raise SettingsPortalError("Invalid JSON file – expected a Videl settings export.") from exc

    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise SettingsPortalError(f"Unsupported settings format (expected '{FORMAT}').")
    if int(doc.get("version") or 0) > VERSION:
        raise SettingsPortalError("Settings file was created by a newer version of Videl.")

    for sec_name in ("encoder", "renamer", "saver"):
        sec_val = doc.get(sec_name)
        if sec_val is not None and not isinstance(sec_val, dict):
            raise SettingsPortalError(f"Section '{sec_name}' must be a JSON object.")

    enc_clean, sk_enc = _sanitize_encoder_section(doc.get("encoder") or {})
    rn_clean, sk_rn = _sanitize_renamer_section(doc.get("renamer") or {})
    sv_clean, sk_sv = _sanitize_saver_section(doc.get("saver") or {})
    skipped = sk_enc + sk_rn + sk_sv

    applied = len(enc_clean) + len(rn_clean) + len(sv_clean)
    if applied == 0:
        raise SettingsPortalError("No valid portable settings found to import.")

    uid = int(user_id)
    if enc_clean:
        await enc_db.add_user(uid)
        await enc_db.update_settings(uid, **enc_clean)
    if rn_clean:
        await rn_store.update(uid, **rn_clean)
    if sv_clean:
        await saver_db.col.update_one({"id": uid}, {"$set": sv_clean}, upsert=True)

    return {
        "applied": applied,
        "encoder_keys": sorted(enc_clean.keys()),
        "renamer_keys": sorted(rn_clean.keys()),
        "saver_keys": sorted(sv_clean.keys()),
        "skipped": skipped,
    }


async def reset_user_preferences(user_id: int) -> None:
    """Reset portable user preferences across Encoder, Auto-Rename, and Saver while preserving account/session/premium."""
    from database.db import db as saver_db
    from renamer import store as rn_store
    from VideoEncoder.utils.database.access_db import db as enc_db

    uid = int(user_id)
    defaults = enc_db.new_user(uid)
    defaults.pop("id", None)
    defaults.pop("join_date", None)
    await enc_db.add_user(uid)
    await enc_db.col.update_one({"id": uid}, {"$set": defaults}, upsert=True)

    await rn_store.unset(uid, "template", "auto", "mode", "clean", "words", "media", "mkv", "meta_on", "meta")
    await saver_db.col.update_one(
        {"id": uid},
        {"$unset": {"caption": "", "delete_words": "", "replace_words": ""}},
    )

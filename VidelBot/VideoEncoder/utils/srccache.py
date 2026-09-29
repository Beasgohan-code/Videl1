"""
Source cache – a file downloaded for one task is reused by the next task on the same file.

Typical flows that used to download the same video 2-3 times:  /sample → /dl,  /dl → change settings →
/dl again,  /screens then /dl,  /mux then /convert.

Downloads are hard-linked into <DOWNLOAD_DIR>/.srccache/<file_unique_id>/<name> (no extra disk while the
task's own copy exists; the dot keeps the watchdog's age sweep and the idle wipe away from it) and
linked back into a new task's folder on a hit. Entries expire after SOURCE_CACHE_MIN minutes, the cache is
capped at SOURCE_CACHE_GB, and it is emptied whenever free disk drops under 10 %.
"""
import logging
import os
import shutil
import time

log = logging.getLogger("VideoEncoder.srccache")
DIRNAME = ".srccache"
STATS = {"hits": 0, "stored": 0, "pruned": 0}


def _cfg(name, default):
    try:
        import config
        return getattr(config, name, default)
    except Exception:
        return default


def root() -> str:
    from .. import download_dir
    return os.path.join(download_dir, DIRNAME)


def enabled() -> bool:
    return _cfg("SOURCE_CACHE_MIN", 60) > 0


def _key(media) -> str | None:
    u = getattr(media, "file_unique_id", None)
    if not isinstance(u, str) or not u:
        return None
    return "".join(ch for ch in u if ch.isalnum() or ch in "-_")[:80] or None


def _entry(media):
    key = _key(media)
    if not key:
        return None
    d = os.path.join(root(), key)
    if not os.path.isdir(d):
        return None
    files = [f for f in os.listdir(d) if not f.endswith(".temp")]
    return os.path.join(d, files[0]) if files else None


def _link(src: str, dst: str) -> bool:
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.link(src, dst)
        return True
    except OSError:
        return False


def get(media, dest_dir: str) -> str | None:
    """A cached copy of `media` linked into dest_dir, or None."""
    if not enabled():
        return None
    path = _entry(media)
    if not path:
        return None
    size = int(getattr(media, "file_size", 0) or 0)
    try:
        if size and os.path.getsize(path) != size:          # partial / different file – drop it
            shutil.rmtree(os.path.dirname(path), ignore_errors=True)
            return None
        os.makedirs(dest_dir, exist_ok=True)
        dst = os.path.join(dest_dir, os.path.basename(path))
        if not _link(path, dst):
            shutil.copy2(path, dst)                         # other filesystem – copying still beats Telegram
        now = time.time()
        os.utime(os.path.dirname(path), (now, now))         # recently used → kept longer
    except OSError as e:
        log.debug(f"cache get failed: {e}")
        return None
    STATS["hits"] += 1
    log.info(f"♻️ source cache hit: {os.path.basename(path)}")
    return dst


def put(media, path: str) -> bool:
    """Remember a finished download (hard link – free while the task's copy exists)."""
    if not enabled() or not path or not os.path.isfile(path):
        return False
    key = _key(media)
    if not key:
        return False
    d = os.path.join(root(), key)
    try:
        os.makedirs(d, exist_ok=True)
        for old in os.listdir(d):
            os.remove(os.path.join(d, old))
        if not _link(path, os.path.join(d, os.path.basename(path))):
            shutil.rmtree(d, ignore_errors=True)            # no hard links here – don't double the disk use
            return False
    except OSError as e:
        log.debug(f"cache put failed: {e}")
        return False
    STATS["stored"] += 1
    prune()
    return True


def _entries():
    base = root()
    if not os.path.isdir(base):
        return []
    out = []
    for name in os.listdir(base):
        d = os.path.join(base, name)
        try:
            size = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d))
            out.append((os.path.getmtime(d), size, d))
        except OSError:
            continue
    return sorted(out)                                       # oldest first


def prune(now: float | None = None, free_ratio=None) -> int:
    """Expire old entries, keep under the size cap, empty the cache when the disk is nearly full."""
    now = now or time.time()
    ttl = _cfg("SOURCE_CACHE_MIN", 60) * 60
    cap = _cfg("SOURCE_CACHE_GB", 10) * 1024 ** 3
    if free_ratio is None:
        try:
            du = shutil.disk_usage(root() if os.path.isdir(root()) else ".")
            free_ratio = du.free / du.total if du.total else 1.0
        except OSError:
            free_ratio = 1.0
    entries, removed = _entries(), 0
    total = sum(e[1] for e in entries)
    for mtime, size, d in entries:
        if now - mtime > ttl or total > cap or free_ratio < 0.10:
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            removed += 1
    STATS["pruned"] += removed
    return removed


def clear() -> int:
    n = len(_entries())
    shutil.rmtree(root(), ignore_errors=True)
    return n

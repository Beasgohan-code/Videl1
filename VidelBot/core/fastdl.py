"""
Parallel Telegram downloads – a drop-in for `client.download_media(message, file_name=…, progress=…)`.

Why: pyrofork's downloader fetches one 1 MiB chunk, waits for it, then asks for the next – so a file
comes down at roughly 1 MiB per round trip, whatever the server's bandwidth. It also opens a fresh
media connection for every file (plus a full key exchange when the file lives on another DC).

Here N requests are kept in flight on one media session that's cached per DC and reused, and each chunk
is written straight to its offset in the file. Anything unusual – small files, photos/stickers, CDN
redirects, expired file references, network trouble – falls back to the stock downloader, so the worst
case is the old speed, never a failed download.

Same contract as download_media: returns the final path, or None when cancelled (StopTransmission
raised by the progress callback) – the file is written as "<name>.temp" and renamed at the end.
"""
import asyncio
import inspect
import logging
import math
import os
import time
from datetime import datetime

log = logging.getLogger("videl.fastdl")

CHUNK = 1024 * 1024                    # Telegram's max GetFile limit; 1 MiB-aligned offsets are always valid
MIN_SIZE = 10 * 1024 * 1024            # below this the handshake costs more than parallelism saves
_KINDS = ("video", "document", "audio", "animation", "voice", "video_note")

_sessions: dict = {}                   # (id(client), dc_id) → started media Session
_locks: dict = {}
STATS = {"fast": 0, "fallback": 0, "bytes": 0}


def _workers() -> int:
    try:
        import config
        return max(1, min(16, int(getattr(config, "FAST_DL_WORKERS", 6))))
    except Exception:
        return 6


def enabled() -> bool:
    try:
        import config
        return bool(getattr(config, "FAST_DL", True))
    except Exception:
        return True


def media_of(message):
    for kind in _KINDS:
        m = getattr(message, kind, None)
        if m is not None:
            return kind, m
    return None, None


def target_path(client, message, media, kind: str, file_name: str) -> str:
    """Where download_media would have put it (same directory rules, same generated names)."""
    directory, name = os.path.split(file_name or "")
    name = os.path.basename((name or getattr(media, "file_name", None) or "").replace("\x00", ""))
    if name in (".", ".."):
        name = ""
    parent = getattr(client, "PARENT_DIR", None)
    if parent is not None:
        directory = os.path.join(str(parent), directory or "downloads/")
    else:
        directory = directory or "downloads/"
    if not name:
        mime = getattr(media, "mime_type", None) or ""
        guess = None
        try:
            guess = client.guess_extension(mime) if mime else None
        except Exception:
            guess = None
        ext = guess or {"voice": ".ogg", "audio": ".mp3", "document": ".zip"}.get(kind, ".mp4")
        when = getattr(media, "date", None) or datetime.now()
        name = f"{kind}_{when.strftime('%Y-%m-%d_%H-%M-%S')}_{getattr(message, 'id', 0)}{ext}"
    return os.path.abspath(os.path.join(directory, name))


async def _session(client, dc_id: int):
    """A started media session for `dc_id`, shared by every download of this client."""
    from pyrogram import raw
    from pyrogram.session import Auth, Session
    key = (id(client), dc_id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        s = _sessions.get(key)
        if s is not None and getattr(s, "is_started", None) is not None and s.is_started.is_set():
            return s
        home = await client.storage.dc_id()
        test = await client.storage.test_mode()
        auth = await client.storage.auth_key() if dc_id == home else await Auth(client, dc_id, test).create()
        s = Session(client, dc_id, auth, test, is_media=True)
        await s.start()
        if dc_id != home:
            exported = await client.invoke(raw.functions.auth.ExportAuthorization(dc_id=dc_id))
            await s.invoke(raw.functions.auth.ImportAuthorization(id=exported.id, bytes=exported.bytes))
        _sessions[key] = s
        return s


async def drop_session(client, dc_id: int):
    s = _sessions.pop((id(client), dc_id), None)
    if s is not None:
        try:
            await s.stop()
        except Exception:
            pass


class _Fallback(Exception):
    """Use the stock downloader for this file."""


async def _call(progress, current, total, args):
    r = progress(current, total, *args)
    if inspect.isawaitable(r):
        await r


async def fetch(message, file_name: str = "downloads/", progress=None, progress_args: tuple = ()):
    """message.download(…) with the parallel path for big files."""
    _kind, media = media_of(message)
    size = int(getattr(media, "file_size", 0) or 0) if media is not None else 0
    client = getattr(message, "_client", None)
    if client is None or size < MIN_SIZE or not enabled():
        return await message.download(file_name=file_name, progress=progress, progress_args=progress_args)
    return await download(client, message, file_name=file_name, progress=progress, progress_args=progress_args)


async def download(client, message, file_name: str = "downloads/", progress=None, progress_args: tuple = (),
                   workers: int | None = None):
    """Download `message`'s media. Same arguments / return value as client.download_media."""
    from pyrogram import StopTransmission
    kind, media = media_of(message)
    size = int(getattr(media, "file_size", 0) or 0) if media is not None else 0
    if media is None or size < MIN_SIZE or not enabled() or not hasattr(client, "storage"):
        return await client.download_media(message, file_name=file_name, progress=progress,
                                           progress_args=progress_args)
    try:
        return await _parallel(client, message, kind, media, size, file_name, progress, progress_args,
                               workers or _workers())
    except StopTransmission:
        return None
    except _Fallback as e:
        log.info(f"fast download → normal download ({e})")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log.warning(f"fast download failed, retrying normally: {type(e).__name__}: {e}")
    STATS["fallback"] += 1
    return await client.download_media(message, file_name=file_name, progress=progress, progress_args=progress_args)


async def _parallel(client, message, kind, media, size, file_name, progress, progress_args, workers):
    from pyrogram import raw
    from pyrogram.errors import FileReferenceExpired
    from pyrogram.file_id import FileId, FileType
    fid = FileId.decode(media.file_id)
    if fid.file_type in (FileType.PHOTO, FileType.CHAT_PHOTO, FileType.THUMBNAIL):
        raise _Fallback("photo")
    location = raw.types.InputDocumentFileLocation(id=fid.media_id, access_hash=fid.access_hash,
                                                   file_reference=fid.file_reference,
                                                   thumb_size=fid.thumbnail_size)
    path = target_path(client, message, media, kind, file_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = path + ".temp"
    total = math.ceil(size / CHUNK)
    pending = iter(range(total))                  # shared by the workers – next() never awaits
    state = {"done": 0, "busy": False, "last": 0.0}
    session = await _session(client, fid.dc_id)
    fd = os.open(temp, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
    started = time.time()

    async def report(final=False):
        if progress is None or state["busy"]:
            return
        now = time.monotonic()
        if not final and now - state["last"] < 1:
            return
        state["busy"], state["last"] = True, now
        try:
            await _call(progress, min(state["done"], size), size, progress_args)
        finally:
            state["busy"] = False

    async def worker():
        for index in pending:
            offset = index * CHUNK
            try:
                r = await session.invoke(raw.functions.upload.GetFile(location=location, offset=offset,
                                                                       limit=CHUNK), sleep_threshold=30)
            except FileReferenceExpired:
                raise _Fallback("file reference expired")
            if not isinstance(r, raw.types.upload.File):
                raise _Fallback("CDN redirect")
            data = r.bytes
            expect = min(CHUNK, size - offset)
            if len(data) != expect:
                raise _Fallback(f"short chunk {index}: {len(data)} of {expect} bytes")
            os.pwrite(fd, data, offset)
            state["done"] += len(data)
            await report()

    tasks = [asyncio.ensure_future(worker()) for _ in range(min(workers, total))]
    ok = False
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for t in done:
            if t.exception() is not None:
                raise t.exception()
        if state["done"] != size:
            raise _Fallback(f"got {state['done']} of {size} bytes")
        await report(final=True)
        ok = True
    except (OSError, ConnectionError, asyncio.TimeoutError):
        await drop_session(client, fid.dc_id)      # the next download starts on a fresh connection
        raise
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        os.close(fd)
        if not ok and os.path.exists(temp):
            os.remove(temp)
    os.replace(temp, path)
    took = max(time.time() - started, 0.001)
    STATS["fast"] += 1
    STATS["bytes"] += size
    log.info(f"⚡ fast download {size / 1048576:.1f} MiB in {took:.1f}s ({size / 1048576 / took:.1f} MiB/s, "
             f"{min(workers, total)} streams)")
    return path


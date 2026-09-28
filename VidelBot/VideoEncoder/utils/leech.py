"""
/leech – download a link and upload it to Telegram as-is (split into parts above the upload limit).

Supported links
  • Mega.nz file links   https://mega.nz/file/<id>#<key>   (and legacy  https://mega.nz/#!<id>!<key>)
                         downloaded + AES-128-CTR decrypted on the fly (the `cryptography` package)
  • Google Drive files   public "anyone with the link" files (no Drive credentials needed)
  • direct links         http(s) – plus every host the encoder's direct-link generator knows

Everything streams to disk in chunks with a live progress card and honours ❌ Cancel.
"""
import asyncio
import base64
import html
import json
import os
import re
import struct
import time
from urllib.parse import unquote, urlparse

import aiohttp
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.style import hdr, row
from . import jobs
from .display_progress import TimeFormatter, humanbytes

MEGA_API = "https://g.api.mega.co.nz/cs"
CHUNK = 1024 * 1024
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
MEGA_ERRORS = {-2: "bad link", -9: "file not found (deleted?)", -11: "access denied", -14: "wrong decryption key",
               -16: "the uploader's account is blocked", -17: "over quota", -18: "Mega is temporarily unavailable"}


class LeechError(Exception):
    pass


class Cancelled(Exception):
    pass


# ─────────────────────────── link types ───────────────────────────
def kind_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith(("mega.nz", "mega.co.nz", "mega.io")):
        return "mega"
    if host.endswith(("drive.google.com", "docs.google.com", "drive.usercontent.google.com")):
        return "gdrive"
    return "direct"


def safe_name(name: str, fallback: str = "file") -> str:
    name = os.path.basename(unquote(str(name or "")).replace("\\", "/")).strip().lstrip(".")
    name = "".join(ch for ch in name if ch not in '<>:"|?*\x00\n\r\t')[:200]
    return name or fallback


# ─────────────────────────── Mega crypto ───────────────────────────
def b64url_decode(data: str) -> bytes:
    data = data.replace("-", "+").replace("_", "/").replace(",", "")
    return base64.b64decode(data + "=" * (-len(data) % 4))


def parse_mega_url(url: str) -> tuple[str, str]:
    """(file_id, key) of a Mega FILE link. Folder links raise LeechError."""
    if "/folder/" in url or "#F!" in url:
        raise LeechError("Mega folder links aren't supported – open the folder and copy a single file's link.")
    m = re.search(r"/file/([\w-]+)#([\w,-]+)", url) or re.search(r"#!([\w-]+)!([\w,-]+)", url)
    if not m:
        raise LeechError("This doesn't look like a Mega file link (it needs the #key part).")
    return m.group(1), m.group(2)


def mega_keys(key_b64: str) -> tuple[bytes, bytes]:
    """File key (32 bytes, base64url) → (AES key 16 bytes, CTR initial counter block 16 bytes)."""
    raw = b64url_decode(key_b64)
    if len(raw) != 32:
        raise LeechError("The Mega key in this link is incomplete.")
    k = struct.unpack(">8I", raw)
    aes = struct.pack(">4I", k[0] ^ k[4], k[1] ^ k[5], k[2] ^ k[6], k[3] ^ k[7])
    iv = struct.pack(">2I", k[4], k[5]) + b"\0" * 8
    return aes, iv


def mega_attributes(attr_b64: str, aes_key: bytes) -> dict:
    """Decrypt the 'at' field (AES-CBC, zero IV) → {'n': file name, …}."""
    data = b64url_decode(attr_b64)
    data += b"\0" * (-len(data) % 16)
    dec = Cipher(algorithms.AES(aes_key), modes.CBC(b"\0" * 16)).decryptor()
    plain = (dec.update(data) + dec.finalize()).rstrip(b"\0")
    if not plain.startswith(b"MEGA{"):
        raise LeechError("Couldn't decrypt the Mega file info – the key is wrong.")
    return json.loads(plain[4:].decode("utf-8", "ignore"))


def mega_decryptor(aes_key: bytes, iv: bytes):
    return Cipher(algorithms.AES(aes_key), modes.CTR(iv)).decryptor()


async def mega_info(session: aiohttp.ClientSession, file_id: str, key_b64: str, api: str = MEGA_API) -> dict:
    """{'url', 'size', 'name', 'aes', 'iv'} for a Mega file."""
    aes, iv = mega_keys(key_b64)
    async with session.post(f"{api}?id={int(time.time())}", json=[{"a": "g", "g": 1, "ssl": 1, "p": file_id}]) as r:
        body = await r.json(content_type=None)
    res = body[0] if isinstance(body, list) and body else body
    if isinstance(res, int):
        raise LeechError(f"Mega: {MEGA_ERRORS.get(res, f'error {res}')}")
    if not isinstance(res, dict) or "g" not in res:
        raise LeechError("Mega didn't return a download link (quota exceeded or the file was removed).")
    attrs = mega_attributes(res.get("at", ""), aes)
    return {"url": res["g"], "size": int(res.get("s") or 0), "name": safe_name(attrs.get("n"), file_id),
            "aes": aes, "iv": iv}


# ─────────────────────────── Google Drive ───────────────────────────
def gdrive_id(url: str) -> str | None:
    for pat in (r"/file/d/([\w-]{10,})", r"[?&]id=([\w-]{10,})", r"/d/([\w-]{10,})"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def gdrive_download_url(file_id: str) -> str:
    return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"


# ─────────────────────────── progress ───────────────────────────
def progress_card(name: str, done: int, total: int, started: float, title: str = "Downloading") -> str:
    elapsed = max(0.1, time.time() - started)
    speed = done / elapsed
    lines = [hdr("🌐", title), f"<code>{html.escape(name[:60])}</code>", ""]
    if total:
        pct = min(100, done * 100 // total)
        filled = pct // 5
        lines.append(f"<code>[{'█' * filled}{'░' * (20 - filled)}]</code> <b>{pct}%</b>")
        lines.append(row("Done", f"{humanbytes(done) or '0 B'} of {humanbytes(total)}"))
        eta = TimeFormatter(int((total - done) / speed)) if speed > 0 and total > done else "-"
    else:
        lines.append(row("Done", humanbytes(done) or "0 B"))
        eta = "-"
    lines.append(row("Speed", f"{humanbytes(speed) or '0 B'}/s · ETA {eta or '-'}"))
    return "\n".join(lines)


# ─────────────────────────── download ───────────────────────────
def _name_from_headers(resp, url: str) -> str:
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=(?:UTF-8'')?([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if m:
        return safe_name(m.group(1).strip())
    return safe_name(os.path.basename(urlparse(str(resp.url) or url).path), "download")


async def stream(session, url: str, dest_dir: str, name: str | None, *, key=None, msg=None, max_bytes: int = 0,
                 expected_size: int = 0, decryptor=None, edit_every: float = 6.0) -> str:
    """Stream `url` into dest_dir. Returns the file path. Raises LeechError / Cancelled."""
    started, last_edit = time.time(), 0.0
    async with session.get(url, allow_redirects=True) as resp:
        if resp.status >= 400:
            raise LeechError(f"The server answered HTTP {resp.status}.")
        ctype = resp.headers.get("Content-Type", "")
        if "text/html" in ctype and decryptor is None:
            raise LeechError("That link opens a web page, not a file. Send a direct download link.")
        total = expected_size or int(resp.headers.get("Content-Length") or 0)
        if max_bytes and total and total > max_bytes:
            raise LeechError(f"The file is {humanbytes(total)} – your limit is {humanbytes(max_bytes)}.")
        name = safe_name(name or _name_from_headers(resp, url))
        path = os.path.join(dest_dir, name)
        done = 0
        with open(path, "wb") as fh:
            async for chunk in resp.content.iter_chunked(CHUNK):
                if key is not None and jobs.is_cancelled(key):
                    raise Cancelled()
                if decryptor is not None:
                    chunk = decryptor.update(chunk)
                fh.write(chunk)
                done += len(chunk)
                if max_bytes and done > max_bytes:
                    raise LeechError(f"The file is bigger than your limit of {humanbytes(max_bytes)}.")
                if msg is not None and time.time() - last_edit >= edit_every:
                    last_edit = time.time()
                    try:
                        from .encoding import cancel_markup
                        await msg.edit(progress_card(name, done, total, started),
                                       reply_markup=cancel_markup(getattr(msg, "id", 0)))
                    except Exception:
                        pass
            if decryptor is not None:
                fh.write(decryptor.finalize())
    if total and done < total:
        raise LeechError(f"The download stopped early ({humanbytes(done)} of {humanbytes(total)}).")
    if done == 0:
        raise LeechError("The server sent an empty file.")
    return path


async def download(url: str, dest_dir: str, *, name: str | None = None, key=None, msg=None, max_bytes: int = 0,
                   mega_api: str = MEGA_API) -> str:
    """Download any supported link into dest_dir → file path."""
    os.makedirs(dest_dir, exist_ok=True)
    kind = kind_of(url)
    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=120)
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": UA}) as session:
        if kind == "mega":
            file_id, key_b64 = parse_mega_url(url)
            info = await mega_info(session, file_id, key_b64, api=mega_api)
            if max_bytes and info["size"] > max_bytes:
                raise LeechError(f"The file is {humanbytes(info['size'])} – your limit is {humanbytes(max_bytes)}.")
            return await stream(session, info["url"], dest_dir, name or info["name"], key=key, msg=msg,
                                max_bytes=max_bytes, expected_size=info["size"],
                                decryptor=mega_decryptor(info["aes"], info["iv"]))
        if kind == "gdrive":
            fid = gdrive_id(url)
            if not fid:
                raise LeechError("Couldn't find the file ID in this Google Drive link.")
            return await stream(session, gdrive_download_url(fid), dest_dir, name, key=key, msg=msg,
                                max_bytes=max_bytes)
        direct = None
        from .direct_link_generator import direct_link_generator
        try:
            direct = await asyncio.to_thread(direct_link_generator, url)
        except Exception as e:
            if type(e).__name__ == "DirectDownloadLinkException":
                raise LeechError(str(e).replace("ERROR: ", "")) from None
            direct = None
        return await stream(session, direct or url, dest_dir, name, key=key, msg=msg, max_bytes=max_bytes)

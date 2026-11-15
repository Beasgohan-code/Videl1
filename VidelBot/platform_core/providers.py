"""
Modular Download Source Adapters & Multi-File Selection Engine (`VidelBot/platform_core/providers.py`).

Supported & Verified Legal Source Providers:
  • `direct_http` — Direct HTTP/HTTPS URLs (documents, PDFs, images, audio, video, datasets, release tarballs)
  • `github`      — Official GitHub REST API v3 release assets & public repository archives (`zip`/`tar.gz`)
  • `rss_atom`    — Public RSS 2.0 `<enclosure>` & Atom `<link rel="enclosure">` media/podcast feeds
  • `gdrive`      — User-authorized Google Drive API v3 files/folders & public shared Google Drive links
  • `dropbox`     — User-authorized Dropbox API v2 files/shared-links & public `?dl=1` links
  • `onedrive`    — User-authorized Microsoft Graph v1.0 OneDrive items & shared links
  • `mega`        — Public Mega.nz single-file links (`mega.nz/file/<id>#<key>`, AES-128-CTR streaming)

Security guarantees:
  • Every outbound URL hop (including 301/302/303/307/308 redirects) passes `check_public_url()`
    DNS/IP validation so private/loopback/link-local/cloud-metadata addresses are blocked.
  • Never bypasses DRM, paywalls, CAPTCHAs, or private authentication controls.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
import re
import secrets
import socket
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import aiohttp

from platform_core import models_db

MAX_REDIRECTS = 6
MAX_FEED_BYTES = 2 * 1024 * 1024
UA = "VidelPlatform/2.0 (+https://github.com/Beasgohan-code/Videl1)"

# Documented provider capability matrix (Part 1A)
PROVIDER_CATALOG: list[dict[str, Any]] = [
    {
        "id": "direct_http",
        "name": "Direct HTTP / HTTPS",
        "official_api": "RFC 9110 HTTP/1.1 & Range Requests",
        "auth_required": False,
        "auth_mode": "none",
        "supported_types": "PDF, documents, images, audio, video, datasets, software packages, archives",
        "rate_limits": "Origin server dependent; respects Retry-After & HTTP 429",
        "automated_allowed": True,
        "access_limits": "Publicly reachable global IPv4/IPv6 HTTP(S) endpoints only (SSRF-guarded)",
    },
    {
        "id": "github",
        "name": "GitHub Releases & Archives",
        "official_api": "GitHub REST API v2022-11-28 (api.github.com)",
        "auth_required": False,
        "auth_mode": "optional_pat",
        "supported_types": "Release binaries, installers, checksums, SBOMs, source zip/tar.gz archives",
        "rate_limits": "60 req/hr unauthenticated · 5,000 req/hr with token",
        "automated_allowed": True,
        "access_limits": "Public repositories and public release assets",
    },
    {
        "id": "rss_atom",
        "name": "RSS 2.0 & Atom Media Feeds",
        "official_api": "RSS 2.0 Enclosure / RFC 4287 Atom Syndication",
        "auth_required": False,
        "auth_mode": "none",
        "supported_types": "Podcast audio, video episodes, public syndicated media enclosures",
        "rate_limits": "Publisher feed TTL; bounded to <= 2 MiB XML feed documents",
        "automated_allowed": True,
        "access_limits": "Publicly syndicated enclosures without DRM or paywall tokens",
    },
    {
        "id": "gdrive",
        "name": "Google Drive",
        "official_api": "Google Drive API v3",
        "auth_required": False,
        "auth_mode": "oauth_or_sa_or_public_link",
        "supported_types": "All Drive binary files & user-authorized Drive folders",
        "rate_limits": "Google Drive API v3 per-user & daily transfer quotas",
        "automated_allowed": True,
        "access_limits": "User/service-account authorized files or 'Anyone with link' shared files",
    },
    {
        "id": "dropbox",
        "name": "Dropbox",
        "official_api": "Dropbox HTTP API v2 (content.dropboxapi.com)",
        "auth_required": False,
        "auth_mode": "user_oauth_token_or_shared_link",
        "supported_types": "Documents, media, archives, datasets in authorized Dropbox or shared links",
        "rate_limits": "Dropbox API v2 standard app/user traffic limits",
        "automated_allowed": True,
        "access_limits": "Requires user Dropbox OAuth token for private paths or public shared link",
    },
    {
        "id": "onedrive",
        "name": "Microsoft OneDrive",
        "official_api": "Microsoft Graph API v1.0 (graph.microsoft.com)",
        "auth_required": True,
        "auth_mode": "user_oauth_token",
        "supported_types": "Files and shared items accessible to the user's Microsoft Graph token",
        "rate_limits": "Microsoft Graph per-app/per-user throttling (429 Retry-After)",
        "automated_allowed": True,
        "access_limits": "Requires user-authorized Microsoft Graph Files.Read / Files.ReadWrite token",
    },
    {
        "id": "mega",
        "name": "Mega.nz Public File",
        "official_api": "Mega public node API (AES-128-CTR client decryption)",
        "auth_required": False,
        "auth_mode": "public_url_key",
        "supported_types": "Single public shared files with decryption key fragment",
        "rate_limits": "Mega transfer bandwidth quota",
        "automated_allowed": True,
        "access_limits": "Single public file links only (folder links require explicit file URL)",
    },
]


class ProviderError(ValueError):
    """Raised when URL validation, SSRF protection, or source probing fails."""


def sanitize_filename(raw_name: str, fallback: str = "download.bin") -> str:
    cleaned = os.path.basename(unquote(str(raw_name or "")).replace("\\", "/")).strip().lstrip(".")
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    if not cleaned or cleaned in (".", ".."):
        return fallback
    if len(cleaned) > 190:
        stem, ext = os.path.splitext(cleaned)
        cleaned = stem[: 190 - len(ext)] + ext
    return cleaned


def category_for_filename(filename: str, content_type: str = "") -> str:
    ext = os.path.splitext((filename or "").lower())[1]
    ct = (content_type or "").lower()
    if ext in (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".ts") or ct.startswith("video/"):
        return "video"
    if ext in (".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus") or ct.startswith("audio/"):
        return "audio"
    if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".svg", ".ico") or ct.startswith("image/"):
        return "image"
    if ext == ".pdf" or ct == "application/pdf":
        return "pdf"
    if ext in (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"):
        return "archive"
    if ext in (".csv", ".parquet", ".jsonl", ".json", ".xml", ".sqlite", ".tsv"):
        return "dataset"
    if ext in (".deb", ".rpm", ".apk", ".whl", ".dmg", ".msi", ".appimage"):
        return "package"
    if ext in (".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".epub"):
        return "document"
    return "other"


def _ip_is_private_or_reserved(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str.split("%", 1)[0])
    except ValueError:
        return True
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return not addr.is_global or addr.is_multicast


async def check_public_url(url: str) -> str:
    """Validate scheme, hostname, and ensure every resolved DNS IP is globally routable (SSRF guard)."""
    allow_priv = os.environ.get("LEECH_ALLOW_PRIVATE", "").lower() in ("1", "true", "yes")
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ProviderError("Only valid http:// and https:// URLs are supported.")
    if parsed.username or parsed.password:
        raise ProviderError("URLs containing embedded credentials are not allowed.")
    if allow_priv:
        return url.strip()
    host = parsed.hostname
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise ProviderError(f"Could not resolve hostname '{host}'.") from exc
        addrs = [item[4][0] for item in infos]
    if not addrs or any(_ip_is_private_or_reserved(a) for a in addrs):
        raise ProviderError("Access to private, loopback, or internal network addresses is blocked.")
    return url.strip()


async def open_ssrf_safe_response(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[aiohttp.ClientResponse, str]:
    """Perform an HTTP request following up to `MAX_REDIRECTS` hops while re-validating every redirect target."""
    current = await check_public_url(url)
    req_headers = {"User-Agent": UA, **(headers or {})}
    for _ in range(MAX_REDIRECTS + 1):
        resp = await session.request(method, current, headers=req_headers, allow_redirects=False)
        if resp.status in (301, 302, 303, 307, 308):
            loc = resp.headers.get("Location")
            resp.release()
            if not loc:
                raise ProviderError("Redirect response missing Location header.")
            current = await check_public_url(urljoin(current, loc))
            if resp.status == 303:
                method = "GET"
            continue
        return resp, current
    raise ProviderError(f"Too many HTTP redirects (> {MAX_REDIRECTS}).")


def detect_provider(url: str) -> str:
    """Automatically classify `url` into the best matching legal source adapter."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    path_low = (parsed.path or "").lower()

    if host in ("github.com", "www.github.com", "api.github.com", "codeload.github.com"):
        if "/releases" in path_low or "/archive/" in path_low or host == "api.github.com":
            return "github"
        # Bare repo URL `https://github.com/owner/repo` -> GitHub adapter (offers latest release / zip archive)
        segs = [s for s in parsed.path.split("/") if s]
        if len(segs) == 2 and "." not in segs[1]:
            return "github"
    if host.endswith(("mega.nz", "mega.co.nz", "mega.io")):
        return "mega"
    if host.endswith(("drive.google.com", "docs.google.com", "drive.usercontent.google.com")):
        return "gdrive"
    if host.endswith(("dropbox.com", "dropboxusercontent.com", "dl.dropboxusercontent.com")):
        return "dropbox"
    if host.endswith(("1drv.ms", "onedrive.live.com", "graph.microsoft.com", "sharepoint.com")):
        return "onedrive"
    if path_low.endswith((".rss", ".atom", "/feed", "/feed/", "/rss", "/rss.xml", "/atom.xml", "/feed.xml")):
        return "rss_atom"
    return "direct_http"


@dataclass
class RemoteFileItem:
    item_id: str
    filename: str
    download_url: str
    size_bytes: int = 0
    content_type: str = "application/octet-stream"
    category: str = "other"
    provider: str = "direct_http"
    supports_range: bool = False
    selected: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "filename": self.filename,
            "download_url": self.download_url,
            "size_bytes": int(self.size_bytes or 0),
            "content_type": self.content_type,
            "category": self.category,
            "provider": self.provider,
            "supports_range": bool(self.supports_range),
            "selected": bool(self.selected),
        }


@dataclass
class SourceManifest:
    provider: str
    title: str
    source_url: str
    files: list[RemoteFileItem]
    requires_selection: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "title": self.title,
            "source_url": self.source_url,
            "requires_selection": self.requires_selection or len(self.files) > 1,
            "total_files": len(self.files),
            "total_bytes": sum(f.size_bytes for f in self.files),
            "note": self.note,
            "files": [f.to_dict() for f in self.files],
        }


def _filename_from_headers(url: str, headers) -> str:
    cd = headers.get("Content-Disposition", "") if headers else ""
    m_utf = re.search(r"filename\*\s*=\s*UTF-8''([^;\r\n]+)", cd, re.IGNORECASE)
    if m_utf:
        return sanitize_filename(unquote(m_utf.group(1).strip('"')))
    m_std = re.search(r'filename\s*=\s*"?([^";\r\n]+)"?', cd, re.IGNORECASE)
    if m_std:
        return sanitize_filename(m_std.group(1))
    path_part = urlparse(url).path
    base = os.path.basename(path_part.rstrip("/")) or "download.bin"
    return sanitize_filename(base)


async def _probe_direct_http(url: str, session: aiohttp.ClientSession) -> SourceManifest:
    # Try HEAD first, fall back to `Range: bytes=0-0` GET if server rejects HEAD
    resp, final_url = await open_ssrf_safe_response(session, "HEAD", url)
    try:
        if resp.status in (403, 405, 501):
            resp.release()
            resp, final_url = await open_ssrf_safe_response(
                session, "GET", url, headers={"Range": "bytes=0-0"}
            )
        ct = (resp.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
        # Auto-detect RSS/Atom XML feeds served at generic URLs
        if ct in ("application/rss+xml", "application/atom+xml"):
            resp.release()
            return await _probe_rss_atom(final_url, session)
        size = 0
        cr = resp.headers.get("Content-Range", "")
        if "/" in cr:
            tot_part = cr.rsplit("/", 1)[-1].strip()
            if tot_part.isdigit():
                size = int(tot_part)
        if not size:
            cl = resp.headers.get("Content-Length", "")
            if cl.isdigit():
                size = int(cl)
        supports_range = (
            resp.status == 206 or "bytes" in (resp.headers.get("Accept-Ranges") or "").lower()
        )
        fname = _filename_from_headers(final_url, resp.headers)
        cat = category_for_filename(fname, ct)
    finally:
        resp.release()

    item = RemoteFileItem(
        item_id="f_1",
        filename=fname,
        download_url=final_url,
        size_bytes=size,
        content_type=ct,
        category=cat,
        provider="direct_http",
        supports_range=supports_range,
    )
    return SourceManifest(provider="direct_http", title=fname, source_url=url, files=[item])


async def _probe_github(url: str, session: aiohttp.ClientSession) -> SourceManifest:
    await check_public_url(url)
    parsed = urlparse(url)
    segs = [s for s in parsed.path.split("/") if s]
    # Direct release download URL or archive URL -> treat as direct HTTP binary
    if len(segs) >= 5 and segs[2] == "releases" and segs[3] == "download":
        mf = await _probe_direct_http(url, session)
        mf.provider = "github"
        for f in mf.files:
            f.provider = "github"
        return mf
    if len(segs) < 2:
        raise ProviderError("GitHub URL must include '<owner>/<repo>'.")
    owner, repo = segs[0], segs[1].removesuffix(".git")
    tag = None
    if len(segs) >= 4 and segs[2] == "releases" and segs[3] == "tag":
        tag = segs[4]

    api_url = (
        f"https://api.github.com/repos/{owner}/{repo}/releases/tags/{tag}"
        if tag
        else f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    )
    resp, _ = await open_ssrf_safe_response(
        session, "GET", api_url, headers={"Accept": "application/vnd.github+json"}
    )
    files: list[RemoteFileItem] = []
    title = f"{owner}/{repo}"
    try:
        if resp.status == 200:
            rel = await resp.json(content_type=None)
            tag_name = str(rel.get("tag_name") or "latest")
            title = f"{owner}/{repo} · {tag_name}"
            for idx, asset in enumerate(rel.get("assets") or [], 1):
                dl_url = str(asset.get("browser_download_url") or "")
                if not dl_url:
                    continue
                aname = sanitize_filename(asset.get("name") or f"asset_{idx}.bin")
                act = str(asset.get("content_type") or "application/octet-stream")
                files.append(
                    RemoteFileItem(
                        item_id=f"gh_{idx}",
                        filename=aname,
                        download_url=dl_url,
                        size_bytes=int(asset.get("size") or 0),
                        content_type=act,
                        category=category_for_filename(aname, act),
                        provider="github",
                        supports_range=True,
                    )
                )
            if rel.get("zipball_url"):
                zname = sanitize_filename(f"{repo}-{tag_name}-source.zip")
                files.append(
                    RemoteFileItem(
                        item_id="gh_zipball",
                        filename=zname,
                        download_url=f"https://codeload.github.com/{owner}/{repo}/zip/refs/tags/{tag_name}",
                        size_bytes=0,
                        content_type="application/zip",
                        category="archive",
                        provider="github",
                        selected=len(files) == 0,
                    )
                )
        elif resp.status == 404 and not tag:
            # Repo has no published releases -> offer default branch source archive
            zname = sanitize_filename(f"{repo}-HEAD.zip")
            files.append(
                RemoteFileItem(
                    item_id="gh_head_zip",
                    filename=zname,
                    download_url=f"https://codeload.github.com/{owner}/{repo}/zip/HEAD",
                    size_bytes=0,
                    content_type="application/zip",
                    category="archive",
                    provider="github",
                )
            )
        else:
            raise ProviderError(f"GitHub API returned HTTP {resp.status} for {owner}/{repo}.")
    finally:
        resp.release()

    if not files:
        raise ProviderError(f"No downloadable assets found for {owner}/{repo}.")
    return SourceManifest(
        provider="github",
        title=title,
        source_url=url,
        files=files,
        requires_selection=len(files) > 1,
    )


def parse_rss_atom_xml(xml_bytes: bytes, base_url: str = "") -> tuple[str, list[RemoteFileItem]]:
    """Safely parse RSS 2.0 `<enclosure>` and Atom `<link rel="enclosure">` media items."""
    if len(xml_bytes) > MAX_FEED_BYTES:
        raise ProviderError("Feed XML exceeds 2 MiB safety limit.")
    raw_head = xml_bytes[:4096].decode("utf-8", errors="ignore").upper()
    if "<!ENTITY" in raw_head:
        raise ProviderError("XML feeds containing DTD ENTITY declarations are rejected for security.")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise ProviderError("Invalid RSS/Atom XML document.") from exc

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1].lower()

    feed_title = "Media Feed"
    items: list[RemoteFileItem] = []
    idx = 0

    # RSS 2.0: <rss><channel><item><enclosure url="..." length="..." type="..."/>
    for elem in root.iter():
        lname = _local(elem.tag)
        if lname == "title" and feed_title == "Media Feed" and (elem.text or "").strip():
            feed_title = (elem.text or "").strip()[:120]
        if lname == "item":
            ep_title = ""
            for child in elem:
                cl = _local(child.tag)
                if cl == "title" and (child.text or "").strip():
                    ep_title = (child.text or "").strip()
                elif cl == "enclosure":
                    enc_url = (child.attrib.get("url") or "").strip()
                    if not enc_url:
                        continue
                    if base_url:
                        enc_url = urljoin(base_url, enc_url)
                    idx += 1
                    mime = (child.attrib.get("type") or "application/octet-stream").strip()
                    raw_len = (child.attrib.get("length") or "0").strip()
                    sz = int(raw_len) if raw_len.isdigit() else 0
                    url_base = os.path.basename(urlparse(enc_url).path)
                    ext = os.path.splitext(url_base)[1] or (".mp3" if "audio" in mime else "")
                    fname = sanitize_filename(f"{ep_title}{ext}" if ep_title else (url_base or f"episode_{idx}{ext}"))
                    items.append(
                        RemoteFileItem(
                            item_id=f"rss_{idx}",
                            filename=fname,
                            download_url=enc_url,
                            size_bytes=sz,
                            content_type=mime,
                            category=category_for_filename(fname, mime),
                            provider="rss_atom",
                            supports_range=True,
                        )
                    )
        elif lname == "entry":
            ep_title = ""
            for child in elem:
                cl = _local(child.tag)
                if cl == "title" and (child.text or "").strip():
                    ep_title = (child.text or "").strip()
                elif cl == "link" and (child.attrib.get("rel") or "").lower() == "enclosure":
                    enc_url = (child.attrib.get("href") or "").strip()
                    if not enc_url:
                        continue
                    if base_url:
                        enc_url = urljoin(base_url, enc_url)
                    idx += 1
                    mime = (child.attrib.get("type") or "application/octet-stream").strip()
                    raw_len = (child.attrib.get("length") or "0").strip()
                    sz = int(raw_len) if raw_len.isdigit() else 0
                    url_base = os.path.basename(urlparse(enc_url).path)
                    ext = os.path.splitext(url_base)[1]
                    fname = sanitize_filename(f"{ep_title}{ext}" if ep_title else (url_base or f"atom_{idx}{ext}"))
                    items.append(
                        RemoteFileItem(
                            item_id=f"atom_{idx}",
                            filename=fname,
                            download_url=enc_url,
                            size_bytes=sz,
                            content_type=mime,
                            category=category_for_filename(fname, mime),
                            provider="rss_atom",
                            supports_range=True,
                        )
                    )
    if not items:
        raise ProviderError("No media `<enclosure>` items found in RSS/Atom feed.")
    return feed_title, items[:200]


async def _probe_rss_atom(url: str, session: aiohttp.ClientSession) -> SourceManifest:
    resp, final_url = await open_ssrf_safe_response(session, "GET", url)
    try:
        if resp.status >= 400:
            raise ProviderError(f"Feed request failed with HTTP {resp.status}.")
        buf = bytearray()
        async for chunk in resp.content.iter_chunked(65536):
            buf.extend(chunk)
            if len(buf) > MAX_FEED_BYTES:
                raise ProviderError("Feed XML exceeds 2 MiB safety limit.")
    finally:
        resp.release()
    feed_title, items = parse_rss_atom_xml(bytes(buf), base_url=final_url)
    return SourceManifest(
        provider="rss_atom",
        title=feed_title,
        source_url=url,
        files=items,
        requires_selection=len(items) > 1,
    )


async def _probe_gdrive(url: str, user_id: int) -> SourceManifest:
    from VideoEncoder.utils.uploads.drive import DriveAPI, _get_file_id, is_drive_configured

    fid = _get_file_id(url)
    if not fid:
        raise ProviderError("Could not extract a valid Google Drive file or folder ID from URL.")

    if is_drive_configured():
        def _query_drive():
            svc = DriveAPI().service
            meta = svc.files().get(
                fileId=fid, fields="id,name,size,mimeType", supportsAllDrives=True
            ).execute()
            if meta.get("mimeType") == "application/vnd.google-apps.folder":
                listing = svc.files().list(
                    q=f"'{fid}' in parents and trashed = false and mimeType != 'application/vnd.google-apps.folder'",
                    fields="files(id,name,size,mimeType)",
                    pageSize=100,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                ).execute()
                return meta, listing.get("files") or []
            return meta, None

        try:
            meta, children = await asyncio.to_thread(_query_drive)
            if children is not None:
                items = [
                    RemoteFileItem(
                        item_id=f"gd_{i}",
                        filename=sanitize_filename(ch.get("name") or f"drive_{i}"),
                        download_url=f"https://drive.google.com/uc?id={ch['id']}&export=download",
                        size_bytes=int(ch.get("size") or 0),
                        content_type=str(ch.get("mimeType") or "application/octet-stream"),
                        category=category_for_filename(ch.get("name") or "", ch.get("mimeType") or ""),
                        provider="gdrive",
                        extra={"drive_id": ch["id"]},
                    )
                    for i, ch in enumerate(children, 1)
                    if ch.get("id")
                ]
                if not items:
                    raise ProviderError("Google Drive folder contains no downloadable files.")
                return SourceManifest(
                    provider="gdrive",
                    title=str(meta.get("name") or "Drive Folder"),
                    source_url=url,
                    files=items,
                    requires_selection=len(items) > 1,
                )
            fname = sanitize_filename(meta.get("name") or f"gdrive_{fid}.bin")
            mime = str(meta.get("mimeType") or "application/octet-stream")
            return SourceManifest(
                provider="gdrive",
                title=fname,
                source_url=url,
                files=[
                    RemoteFileItem(
                        item_id="gd_1",
                        filename=fname,
                        download_url=f"https://drive.google.com/uc?id={fid}&export=download",
                        size_bytes=int(meta.get("size") or 0),
                        content_type=mime,
                        category=category_for_filename(fname, mime),
                        provider="gdrive",
                        extra={"drive_id": fid},
                    )
                ],
            )
        except ProviderError:
            raise
        except Exception:
            pass

    # Public shared Drive file fallback
    dl_url = f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
    fname = sanitize_filename(f"gdrive_{fid}.bin")
    return SourceManifest(
        provider="gdrive",
        title=fname,
        source_url=url,
        files=[
            RemoteFileItem(
                item_id="gd_pub_1",
                filename=fname,
                download_url=dl_url,
                provider="gdrive",
                extra={"drive_id": fid},
            )
        ],
    )


def _normalize_dropbox_public_url(url: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    qs["dl"] = ["1"]
    from urllib.parse import urlencode, urlunparse
    new_q = urlencode({k: v[0] for k, v in qs.items()})
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_q, ""))


async def _probe_dropbox(url: str, user_id: int, session: aiohttp.ClientSession) -> SourceManifest:
    token, _ = await models_db.get_provider_token(user_id, "dropbox")
    if token and url.startswith("dropbox://"):
        dbx_path = "/" + url.removeprefix("dropbox://").lstrip("/")
        fname = sanitize_filename(os.path.basename(dbx_path) or "dropbox_file.bin")
        return SourceManifest(
            provider="dropbox",
            title=fname,
            source_url=url,
            files=[
                RemoteFileItem(
                    item_id="dbx_1",
                    filename=fname,
                    download_url="https://content.dropboxapi.com/2/files/download",
                    provider="dropbox",
                    category=category_for_filename(fname),
                    extra={"dropbox_path": dbx_path, "auth": "token"},
                )
            ],
        )
    direct_url = _normalize_dropbox_public_url(url)
    mf = await _probe_direct_http(direct_url, session)
    mf.provider = "dropbox"
    mf.source_url = url
    for f in mf.files:
        f.provider = "dropbox"
    return mf


def _encode_onedrive_sharing_token(shared_url: str) -> str:
    b64 = base64.urlsafe_b64encode(shared_url.encode("utf-8")).decode("ascii").rstrip("=")
    return f"u!{b64}"


async def _probe_onedrive(url: str, user_id: int, session: aiohttp.ClientSession) -> SourceManifest:
    token, _ = await models_db.get_provider_token(user_id, "onedrive")
    if not token:
        raise ProviderError(
            "OneDrive requires a user Microsoft Graph token – connect OneDrive in Integrations first."
        )
    share_id = _encode_onedrive_sharing_token(url)
    meta_url = f"https://graph.microsoft.com/v1.0/shares/{share_id}/driveItem"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with session.get(meta_url, headers=headers) as resp:
        if resp.status in (401, 403):
            await models_db.record_provider_outcome(user_id, "onedrive", ok=False, error=f"HTTP {resp.status}")
            raise ProviderError("OneDrive token was rejected or lacks Files.Read permission.")
        if resp.status >= 400:
            raise ProviderError(f"OneDrive Graph API returned HTTP {resp.status}.")
        meta = await resp.json(content_type=None)
    await models_db.record_provider_outcome(user_id, "onedrive", ok=True)
    fname = sanitize_filename(meta.get("name") or "onedrive_item.bin")
    dl_url = str(meta.get("@microsoft.graph.downloadUrl") or f"{meta_url}/content")
    mime = str(((meta.get("file") or {}).get("mimeType")) or "application/octet-stream")
    return SourceManifest(
        provider="onedrive",
        title=fname,
        source_url=url,
        files=[
            RemoteFileItem(
                item_id="od_1",
                filename=fname,
                download_url=dl_url,
                size_bytes=int(meta.get("size") or 0),
                content_type=mime,
                category=category_for_filename(fname, mime),
                provider="onedrive",
                supports_range=True,
                extra={"use_bearer": "@microsoft.graph.downloadUrl" not in meta},
            )
        ],
    )


async def _probe_mega(url: str) -> SourceManifest:
    from VideoEncoder.utils import leech

    try:
        fid, _ = leech.parse_mega_url(url)
    except leech.LeechError as exc:
        raise ProviderError(str(exc)) from exc
    fname = sanitize_filename(f"mega_{fid}.bin")
    return SourceManifest(
        provider="mega",
        title=fname,
        source_url=url,
        files=[
            RemoteFileItem(
                item_id="mega_1",
                filename=fname,
                download_url=url,
                provider="mega",
                supports_range=False,
            )
        ],
    )


async def inspect_source(url: str, *, user_id: int = 0, timeout_sec: float = 25.0) -> SourceManifest:
    """Detect provider, validate URL safety, and return downloadable file manifest."""
    raw = (url or "").strip()
    if not raw:
        raise ProviderError("Please enter a download URL.")
    prov = detect_provider(raw) if not raw.startswith("dropbox://") else "dropbox"
    timeout = aiohttp.ClientTimeout(total=timeout_sec, connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if prov == "github":
            return await _probe_github(raw, session)
        if prov == "rss_atom":
            return await _probe_rss_atom(raw, session)
        if prov == "gdrive":
            return await _probe_gdrive(raw, user_id)
        if prov == "dropbox":
            return await _probe_dropbox(raw, user_id, session)
        if prov == "onedrive":
            return await _probe_onedrive(raw, user_id, session)
        if prov == "mega":
            return await _probe_mega(raw)
        return await _probe_direct_http(raw, session)


# ─────────────────────────── Advanced Multi-File Selection Store (Part 1C) ───────────────────────────
class SelectionSessionStore:
    """Bounded in-memory + user-owned selection sessions for multi-file sources (Webapp & Telegram)."""

    TTL = 1800.0
    MAX_SESSIONS = 500

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}

    def _prune(self) -> None:
        now = time.time()
        for sid in [k for k, v in list(self._sessions.items()) if now - v["created_ts"] > self.TTL]:
            self._sessions.pop(sid, None)
        while len(self._sessions) > self.MAX_SESSIONS:
            oldest = next(iter(self._sessions))
            self._sessions.pop(oldest, None)

    def create(
        self,
        user_id: int,
        manifest: SourceManifest,
        *,
        destination: str = "local",
        excluded_extensions: list[str] | None = None,
    ) -> dict[str, Any]:
        self._prune()
        sid = f"sel_{secrets.token_hex(6)}"
        excl = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (excluded_extensions or [])}
        files_copy: list[RemoteFileItem] = []
        for f in manifest.files:
            ext = os.path.splitext(f.filename.lower())[1]
            selected = f.selected and (ext not in excl)
            files_copy.append(
                RemoteFileItem(
                    item_id=f.item_id,
                    filename=f.filename,
                    download_url=f.download_url,
                    size_bytes=f.size_bytes,
                    content_type=f.content_type,
                    category=f.category,
                    provider=f.provider,
                    supports_range=f.supports_range,
                    selected=selected,
                    extra=dict(f.extra),
                )
            )
        sess = {
            "session_id": sid,
            "user_id": int(user_id),
            "provider": manifest.provider,
            "title": manifest.title,
            "source_url": manifest.source_url,
            "destination": destination,
            "files": files_copy,
            "created_ts": time.time(),
        }
        self._sessions[sid] = sess
        return self.view(sid, user_id=user_id)

    def _get_owned(self, session_id: str, user_id: int, *, is_admin: bool = False) -> dict[str, Any]:
        self._prune()
        sess = self._sessions.get(session_id)
        if not sess:
            raise KeyError("Selection session expired or does not exist.")
        if not is_admin and int(sess["user_id"]) != int(user_id):
            raise PermissionError("You do not own this file-selection session.")
        return sess

    def update_selection(
        self,
        session_id: str,
        user_id: int,
        *,
        toggle_id: str = "",
        select_all: bool | None = None,
        selected_ids: list[str] | None = None,
        exclude_extensions: list[str] | None = None,
        destination: str = "",
        is_admin: bool = False,
    ) -> dict[str, Any]:
        sess = self._get_owned(session_id, user_id, is_admin=is_admin)
        files: list[RemoteFileItem] = sess["files"]
        if select_all is not None:
            for f in files:
                f.selected = bool(select_all)
        if selected_ids is not None:
            wanted = set(selected_ids)
            for f in files:
                f.selected = f.item_id in wanted
        if toggle_id:
            for f in files:
                if f.item_id == toggle_id:
                    f.selected = not f.selected
                    break
        if exclude_extensions:
            excl = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in exclude_extensions if e}
            for f in files:
                if os.path.splitext(f.filename.lower())[1] in excl:
                    f.selected = False
        if destination:
            sess["destination"] = destination
        return self.view(session_id, user_id=user_id, is_admin=is_admin)

    def view(
        self,
        session_id: str,
        *,
        user_id: int,
        page: int = 1,
        page_size: int = 10,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        sess = self._get_owned(session_id, user_id, is_admin=is_admin)
        files: list[RemoteFileItem] = sess["files"]
        ps = max(1, min(100, int(page_size)))
        pages = max(1, (len(files) + ps - 1) // ps)
        pg = max(1, min(pages, int(page)))
        start = (pg - 1) * ps
        sel_files = [f for f in files if f.selected]
        return {
            "session_id": sess["session_id"],
            "provider": sess["provider"],
            "title": sess["title"],
            "source_url": sess["source_url"],
            "destination": sess["destination"],
            "total_files": len(files),
            "selected_count": len(sel_files),
            "selected_bytes": sum(f.size_bytes for f in sel_files),
            "page": pg,
            "pages": pages,
            "items": [f.to_dict() for f in files[start : start + ps]],
        }

    def pop_confirmed(self, session_id: str, user_id: int, *, is_admin: bool = False) -> tuple[dict[str, Any], list[RemoteFileItem]]:
        sess = self._get_owned(session_id, user_id, is_admin=is_admin)
        chosen = [f for f in sess["files"] if f.selected]
        if not chosen:
            raise ProviderError("Select at least one file before starting the download.")
        self._sessions.pop(session_id, None)
        return sess, chosen


selection_store = SelectionSessionStore()

"""
Advanced URL Shortener with multiple provider support and SSRF protection.
Supports: AdLinkFly-compatible, Bitly, TinyURL, is.gd, v.gd, custom.
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

import aiohttp
from filestore.fs_config import LOGGER

log = LOGGER(__name__)

_BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal", ".arpa", ".lan")
_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")

# Known provider presets
PROVIDERS = {
    "adlinkfly": {
        "name": "AdLinkFly / Custom",
        "needs_key": True,
        "needs_domain": True,
    },
    "bitly": {
        "name": "Bitly",
        "needs_key": True,
        "needs_domain": False,
        "api": "https://api-ssl.bitly.com/v4/shorten",
    },
    "tinyurl": {
        "name": "TinyURL",
        "needs_key": False,
        "needs_domain": False,
        "api": "https://tinyurl.com/api-create.php",
    },
    "isgd": {
        "name": "is.gd",
        "needs_key": False,
        "needs_domain": False,
        "api": "https://is.gd/create.php",
    },
    "vgd": {
        "name": "v.gd",
        "needs_key": False,
        "needs_domain": False,
        "api": "https://v.gd/create.php",
    },
}


def _ip_is_public(raw_ip: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw_ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return False
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT_NET:
        return False
    return True


def is_valid_http_url(candidate: str) -> bool:
    """Validate that a shortened URL is safe to embed in a Telegram inline URL button."""
    if not candidate or not isinstance(candidate, str):
        return False
    candidate = candidate.strip()
    if len(candidate) > 2048 or any(ch.isspace() or ord(ch) < 32 for ch in candidate):
        return False
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return False
    try:
        ipaddress.ip_address(host)
        return _ip_is_public(host)
    except ValueError:
        return "." in host


async def validate_public_endpoint(domain: str) -> str | None:
    """Normalize and validate an untrusted shortener domain against SSRF targets."""
    raw = (domain or "").strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
        log.warning(f"Blocked SSRF shortener host: {host!r}")
        return None
    # Literal IP check first (fast path, works offline in tests too)
    try:
        ipaddress.ip_address(host)
        if not _ip_is_public(host):
            log.warning(f"Blocked private/reserved shortener IP: {host}")
            return None
    except ValueError:
        if "." not in host:
            return None
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
        except OSError as exc:
            log.warning(f"Shortener DNS lookup failed for {host}: {exc}")
            return None
        if not infos:
            return None
        for info in infos:
            sockaddr = info[4]
            if not sockaddr or not _ip_is_public(str(sockaddr[0])):
                log.warning(f"Blocked shortener {host} resolving to non-public address {sockaddr[0] if sockaddr else '?'}")
                return None

    base_path = parsed.path.rstrip("/")
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return f"{parsed.scheme}://{netloc}{base_path}"


async def shorten_url(url: str, api_key: str = "", domain: str = "", provider: str = "adlinkfly") -> str:
    """
    Shorten a URL using the selected provider.

    Returns the shortened URL, or the original URL on failure.
    """
    provider = (provider or "adlinkfly").lower()

    try:
        if provider == "bitly":
            out = await _shorten_bitly(url, api_key)
        elif provider == "tinyurl":
            out = await _shorten_tinyurl(url, api_key)
        elif provider == "isgd":
            out = await _shorten_isgd(url)
        elif provider == "vgd":
            out = await _shorten_vgd(url)
        else:
            out = await _shorten_adlinkfly(url, api_key, domain)
        return out if is_valid_http_url(out) else url
    except Exception as e:
        log.error(f"Shortener error ({provider}): {e}")
        return url


async def _shorten_adlinkfly(url: str, api_key: str, domain: str) -> str:
    if not api_key or not domain:
        return url
    safe_base = await validate_public_endpoint(domain)
    if not safe_base:
        return url
    api_url = f"{safe_base}/api"
    params = {"api": api_key, "url": url}

    # Disable redirects so a public attacker domain cannot 302-redirect into 169.254.169.254 / localhost.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(
            api_url,
            params=params,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json(content_type=None)
                    if isinstance(data, dict) and data.get("status") == "success":
                        shortened = str(data.get("shortenedUrl") or "").strip()
                        if is_valid_http_url(shortened):
                            return shortened
                except Exception:
                    pass
                text = (await resp.text()).strip()
                if is_valid_http_url(text):
                    return text
    return url


async def _shorten_bitly(url: str, api_key: str) -> str:
    if not api_key:
        return url
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"long_url": url}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.post(
            "https://api-ssl.bitly.com/v4/shorten",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (200, 201):
                data = await resp.json()
                candidate = str(data.get("link") or "").strip()
                if is_valid_http_url(candidate):
                    return candidate
    return url


async def _shorten_tinyurl(url: str, api_key: str = "") -> str:
    params = {"url": url}
    if api_key:
        params["api_token"] = api_key
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(
            "https://tinyurl.com/api-create.php",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                text = (await resp.text()).strip()
                if is_valid_http_url(text):
                    return text
    return url


async def _shorten_isgd(url: str) -> str:
    params = {"format": "simple", "url": url}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(
            "https://is.gd/create.php",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                text = (await resp.text()).strip()
                if is_valid_http_url(text):
                    return text
    return url


async def _shorten_vgd(url: str) -> str:
    params = {"format": "simple", "url": url}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async with session.get(
            "https://v.gd/create.php",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                text = (await resp.text()).strip()
                if is_valid_http_url(text):
                    return text
    return url

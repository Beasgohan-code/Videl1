"""
Advanced URL Shortener with multiple provider support.
Supports: AdLinkFly-compatible, Bitly, TinyURL, is.gd, v.gd, custom
"""

import aiohttp
from filestore.fs_config import LOGGER

log = LOGGER(__name__)

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


async def shorten_url(url: str, api_key: str = "", domain: str = "", provider: str = "adlinkfly") -> str:
    """
    Shorten a URL using the selected provider.

    Returns the shortened URL, or the original URL on failure.
    """
    provider = (provider or "adlinkfly").lower()

    try:
        if provider == "bitly":
            return await _shorten_bitly(url, api_key)
        elif provider == "tinyurl":
            return await _shorten_tinyurl(url, api_key)
        elif provider == "isgd":
            return await _shorten_isgd(url)
        elif provider == "vgd":
            return await _shorten_vgd(url)
        else:
            # Default: AdLinkFly-compatible
            return await _shorten_adlinkfly(url, api_key, domain)
    except Exception as e:
        log.error(f"Shortener error ({provider}): {e}")
        return url


async def _shorten_adlinkfly(url: str, api_key: str, domain: str) -> str:
    if not api_key or not domain:
        return url
    if not domain.startswith("http"):
        domain = f"https://{domain}"
    domain = domain.rstrip("/")
    api_url = f"{domain}/api"
    params = {"api": api_key, "url": url}

    async with aiohttp.ClientSession() as session:
        async with session.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                try:
                    data = await resp.json()
                    if data.get("status") == "success":
                        shortened = data.get("shortenedUrl", "")
                        if shortened:
                            return shortened
                except Exception:
                    pass
                text = await resp.text()
                if text.startswith("http"):
                    return text.strip()
    return url


async def _shorten_bitly(url: str, api_key: str) -> str:
    if not api_key:
        return url
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {"long_url": url}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api-ssl.bitly.com/v4/shorten",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status in (200, 201):
                data = await resp.json()
                return data.get("link", url)
    return url


async def _shorten_tinyurl(url: str, api_key: str = "") -> str:
    params = {"url": url}
    if api_key:
        params["api_token"] = api_key
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://tinyurl.com/api-create.php",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                text = await resp.text()
                if text.startswith("http"):
                    return text.strip()
    return url


async def _shorten_isgd(url: str) -> str:
    params = {"format": "simple", "url": url}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://is.gd/create.php",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                text = await resp.text()
                if text.startswith("http"):
                    return text.strip()
    return url


async def _shorten_vgd(url: str) -> str:
    params = {"format": "simple", "url": url}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            "https://v.gd/create.php",
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                text = await resp.text()
                if text.startswith("http"):
                    return text.strip()
    return url

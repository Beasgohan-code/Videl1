"""Public-only outbound webhook requests, with DNS validation at connection time."""
from __future__ import annotations

import ipaddress
import socket

import aiohttp
from yarl import URL

from platform_core import providers


def validate_webhook_url(raw: str) -> str:
    """Reject credentials and private literal addresses, regardless of leech settings."""
    try:
        url = URL(raw.strip())
        host = url.host
        if (url.scheme not in ("http", "https") or not host or url.user is not None
                or url.password is not None or url.query_string or url.fragment
                or "%" in host or "\\" in raw):
            raise ValueError
        # Force port validation even when the URL is only being saved.
        _ = url.port
    except (TypeError, ValueError):
        raise providers.ProviderError("Webhook must be an HTTP(S) URL without credentials, query, or fragment.") from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # Hostname is checked by PublicResolver at connection time.
    else:
        if providers._ip_is_private_or_reserved(host):
            raise providers.ProviderError("Webhook address is not publicly routable.")
    return str(url)


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate all answers returned to aiohttp; never resolve again after validation."""

    def __init__(self) -> None:
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC):
        records = await self._resolver.resolve(host, port, family)
        if not records or any(providers._ip_is_private_or_reserved(r["host"]) for r in records):
            raise OSError("Webhook DNS returned a non-public address.")
        return records

    async def close(self) -> None:
        await self._resolver.close()


async def ping(raw_url: str, body: dict) -> int:
    """Connect only to the filtered DNS result; do not follow any redirect."""
    url = validate_webhook_url(raw_url)
    resolver = PublicResolver()
    connector = aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False)
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10, connect=5), connector=connector, trust_env=False
        ) as session:
            async with session.post(url, json=body, allow_redirects=False) as response:
                return response.status
    finally:
        await resolver.close()

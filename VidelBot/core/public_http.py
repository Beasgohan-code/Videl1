"""Public-only outbound HTTP for untrusted media URLs.

This helper is intentionally independent of the legacy LEECH_ALLOW_PRIVATE
setting. It is used for the /mediainfo header fetch, not all network features.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import aiohttp
from yarl import URL


class UnsafeMediaURL(ValueError):
    """A URL or redirect target is not suitable for a public media fetch."""


def _is_public_ip(value: str) -> bool:
    try:
        addr = ipaddress.ip_address(value)
        if getattr(addr, 'ipv4_mapped', None):
            addr = addr.ipv4_mapped
        return addr.is_global and not addr.is_multicast
    except ValueError:
        return False


def validate_url(raw: str) -> str:
    """Parse once as aiohttp will, rejecting dangerous schemes and literals."""
    if (not isinstance(raw, str) or not raw or raw != raw.strip() or len(raw) > 8192 or '\\' in raw
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw)):
        raise UnsafeMediaURL('Invalid public media URL.')
    try:
        # yarl normalizes away empty userinfo (e.g. http://@host).
        if '@' in urlsplit(raw).netloc:
            raise ValueError
        url = URL(raw)
        host = url.host
        if (url.scheme not in ('http', 'https') or not host or url.user is not None
                or url.password is not None or '%' in host):
            raise ValueError
        _ = url.port  # Force malformed/out-of-range port rejection.
    except (ValueError, TypeError):
        raise UnsafeMediaURL('Invalid public media URL.') from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        # aiohttp treats digit-and-dot hosts as IPs and bypasses custom DNS;
        # reject non-canonical forms such as 127.1, octal and integer hosts.
        if host.replace('.', '').isdigit() or ':' in host:
            raise UnsafeMediaURL('Invalid public media URL.') from None
        # Other hostnames are checked by PublicResolver at connection time.
    else:
        if not _is_public_ip(host):
            raise UnsafeMediaURL('Media URL must point to a public address.')
    return str(url.with_fragment(None))


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Reject mixed or private DNS answers, returning only the checked addresses."""

    def __init__(self):
        self._resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_UNSPEC):
        records = await self._resolver.resolve(host, port, family)
        if not records or any(not _is_public_ip(record.get('host', '')) for record in records):
            raise OSError('Media hostname did not resolve exclusively to public addresses.')
        return records

    async def close(self):
        await self._resolver.close()


async def get_public(session, url: str, *, headers: dict, max_redirects: int = 6):
    """GET a public URL, validating each redirect *before* the next request.

    Caller must use a session with PublicResolver, trust_env=False and disabled
    DNS caching, and must release the returned response.
    """
    current = validate_url(url)
    for _ in range(max_redirects + 1):
        response = await session.get(current, headers=headers, allow_redirects=False)
        if response.status not in (301, 302, 303, 307, 308):
            return response, current
        location = response.headers.get('Location')
        response.release()
        if not location:
            raise UnsafeMediaURL('Media redirect is missing a destination.')
        current = validate_url(urljoin(current, location))
    raise UnsafeMediaURL('Too many media redirects.')

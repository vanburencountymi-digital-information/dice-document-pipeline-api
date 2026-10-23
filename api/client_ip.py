from __future__ import annotations

import ipaddress
from functools import lru_cache

from django.conf import settings
from rest_framework.request import Request

from common.throttle_scopes import ThrottleScope

UNKNOWN_IP = "unknown"


@lru_cache(maxsize=8)
def _networks(
    ranges: tuple[str, ...],
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Takes: CIDR ranges as text. Returns: them parsed (cached, since settings rarely change)."""
    return tuple(ipaddress.ip_network(cidr) for cidr in ranges)


def _valid_ip(value: str | None) -> str | None:
    """Takes: text. Returns: the address in standard form, or None if it isn't an IP address."""
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def _caller_key(address: str) -> str:
    """Takes: a valid address (or "unknown"). Returns: the part of it that identifies a caller.

    An IPv6 caller usually controls a whole /64 (billions of addresses) and could send each
    request from a different one, so IPv6 is identified by its /64. An IPv4-in-IPv6 address
    is treated as the IPv4 address.
    """
    if address == UNKNOWN_IP:
        return address
    ip = ipaddress.ip_address(address)
    if ip.version == 4:
        return address
    if ip.ipv4_mapped is not None:
        return str(ip.ipv4_mapped)
    return str(ipaddress.ip_network(f"{ip}/64", strict=False).network_address)


def _is_cloudflare(address: str) -> bool:
    """Takes: an address or "unknown". Returns: True if it is in Cloudflare's published ranges."""
    if address == UNKNOWN_IP:
        return False
    ip = ipaddress.ip_address(address)
    return any(ip in network for network in _networks(tuple(settings.CLOUDFLARE_IP_RANGES)))


def _connecting_address(request: Request) -> str:
    """The address that actually connected to the hosting platform's front end.

    The platform adds the connecting address to the end of `X-Forwarded-For`, so that entry
    (and only that one) can't be set by the caller. Anything before it can be. With
    `TRUSTED_PROXY_HOPS` at 0 the header is ignored and `REMOTE_ADDR` is used.

    Takes: the request. Returns: a valid IP address, or "unknown" if there isn't one.
    """
    hops = settings.TRUSTED_PROXY_HOPS
    forwarded = [
        part.strip() for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if part
    ]
    # Too few entries means the request didn't come through the proxy we expect, so an entry
    # here may have been written by the caller. Fall back to the real connection instead of
    # trusting it (which is what DRF's own NUM_PROXIES would do).
    if hops > 0 and len(forwarded) >= hops:
        address = _valid_ip(forwarded[-hops])
        if address:
            return address
    return _valid_ip(request.META.get("REMOTE_ADDR")) or UNKNOWN_IP


def client_ip(request: Request) -> str:
    """Works out the caller's real IP address, however the request reached the app.

    - Behind the hosting platform's proxy only (e.g. Cloud Run without Cloudflare): the
      address the platform saw connecting.
    - Through Cloudflare: that address belongs to Cloudflare, so use the address Cloudflare
      puts in `CF-Connecting-IP`. A caller can send that header themselves, so it is only
      believed when the connecting address is in Cloudflare's published ranges.
    - Not behind a proxy at all (local development): `REMOTE_ADDR`.

    The result is always a valid IP address (or "unknown"), never raw header text, so it is
    safe to use in a database key. IPv6 callers come back as their /64 network (see
    `_caller_key`).

    Why not DRF's `NUM_PROXIES`: with fewer `X-Forwarded-For` entries than expected it falls
    back to an entry the caller wrote, and it doesn't check the entry is an IP address. Both
    matter here, since the result is a counter key.

    Takes: the request. Returns: the caller's IP address (or IPv6 /64) as text.
    """
    connecting = _connecting_address(request)
    if _is_cloudflare(connecting):
        return _caller_key(_valid_ip(request.META.get("HTTP_CF_CONNECTING_IP")) or connecting)
    return _caller_key(connecting)


def build_counter_key(scope: ThrottleScope, request: Request) -> str:
    """Takes: a throttle scope and the request. Returns: the key that scope counts this caller
    under (e.g. `client_ip:9.9.9.9`), short enough for `ThrottleCounter.key`."""
    return f"{scope}:{client_ip(request)}"

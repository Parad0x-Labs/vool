"""Where a turn from another device may fetch: the public internet only.

A turn that is not the owner's own session (a paired phone, a channel, any other device on the
network; see ``core.request_trust.turn_is_remote``) may still read public web pages. It must not
use this computer as a bridge into the owner's own network: loopback services (a local model
server, a browser debugging port, development servers), link-local and private ranges, and private
DNS namespaces are refused.

The check runs after DNS resolution, every address the name resolves to must be public, and the
connection is pinned to those addresses so the name cannot resolve again to somewhere else.
Redirects are not followed blindly: each hop is resolved and checked the same way before its socket
opens.
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.parse
from collections.abc import Callable
from typing import Any

#: Redirect hops one fetch may take, each re-validated.
MAX_HOPS = 5
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_PRIVATE_NAMESPACES = (".local", ".localhost", ".internal", ".lan", ".home.arpa", ".intranet", ".corp")


class RemoteTurnTargetRefused(Exception):
    """A target a turn from another device may not reach. ``reason`` is a short code."""

    def __init__(self, reason: str, url: str) -> None:
        super().__init__(f"{reason}: {url}")
        self.reason = reason
        self.url = url


def _address_is_public(address_text: str) -> bool:
    try:
        address = ipaddress.ip_address(str(address_text).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(address.is_global) and not (address.is_multicast or address.is_reserved)


def resolve_public_target(url: str) -> tuple[str, ...]:
    """The public addresses ``url``'s host resolves to, or :class:`RemoteTurnTargetRefused`."""

    parts = urllib.parse.urlsplit(str(url or "").strip())
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if parts.scheme not in {"http", "https"} or not host:
        raise RemoteTurnTargetRefused("scheme_or_host_invalid", url)
    if host == "localhost" or host.endswith(_PRIVATE_NAMESPACES):
        raise RemoteTurnTargetRefused("private_namespace", url)
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise RemoteTurnTargetRefused("dns_failure", url) from None
    addresses = tuple(sorted({str(info[4][0]) for info in infos}))
    if not addresses:
        raise RemoteTurnTargetRefused("dns_no_addresses", url)
    if not all(_address_is_public(address) for address in addresses):
        raise RemoteTurnTargetRefused("non_public_address", url)
    return addresses


def open_public_only(
    url: str,
    *,
    make_request: Callable[[str], Any],
    timeout: float,
) -> Any:
    """Open ``url`` through the one outbound door, reaching only public addresses.

    ``make_request(url)`` builds the request for each hop. A redirect is followed only after its
    target passes the same check; more than :data:`MAX_HOPS` hops refuse.
    """

    from core.remote_fetch_policy import open_remote

    current = str(url)
    for _hop in range(MAX_HOPS + 1):
        addresses = resolve_public_target(current)
        try:
            return open_remote(make_request(current), timeout=timeout, redirect_policy="refuse", pinned_addresses=addresses)
        except urllib.error.HTTPError as exc:
            message = str(getattr(exc, "msg", "") or "")
            if int(exc.code or 0) not in _REDIRECT_CODES or not message.startswith("redirect_refused:"):
                raise
            location = message[len("redirect_refused:"):].strip()
            if not location:
                raise
            current = urllib.parse.urljoin(current, location)
    raise RemoteTurnTargetRefused("too_many_redirects", current)


__all__ = ["MAX_HOPS", "RemoteTurnTargetRefused", "open_public_only", "resolve_public_target"]

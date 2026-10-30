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


#: Refused whatever ``ipaddress`` says about them. ``is_global`` follows the IANA registries, which
#: leave deprecated ranges such as IPv6 site-local (fec0::/10) "global", and its answers have
#: changed between Python releases; this list does not depend on either.
_DENIED_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    # IPv4: this network, private, shared (CGNAT), loopback, link-local, IETF protocol assignments,
    # documentation, the 6to4 relay anycast, benchmarking, multicast, reserved and broadcast
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.0.0.0/24", "192.0.2.0/24", "192.88.99.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4",
    # IPv6: unspecified and loopback, IPv4-compatible (deprecated), discard-only, NAT64 (both
    # prefixes carry an IPv4 address a gateway would reach), Teredo, ORCHID, documentation, 6to4
    # (it carries an IPv4 address), site-local (deprecated), unique-local, link-local and multicast
    "::/128", "::1/128", "::/96", "100::/64", "64:ff9b::/96", "64:ff9b:1::/48", "2001::/32", "2001:10::/28",
    "2001:20::/28", "2001:db8::/32", "2002::/16", "3fff::/20", "fec0::/10", "fc00::/7", "fe80::/10",
    "ff00::/8",
))


def _address_is_public(address_text: str) -> bool:
    try:
        address = ipaddress.ip_address(str(address_text).split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if any(address in network for network in _DENIED_NETWORKS if network.version == address.version):
        return False
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


def refuse_local_target(url: str, *, resolve: bool = True) -> None:
    """Raise :class:`RemoteTurnTargetRefused` when ``url`` names this computer or its local network.

    The owner's own research reads pages a search engine returned. Those are public by nature, so
    a hit (or the page a redirect lands on) that points here is a poisoned result. Unlike
    :func:`resolve_public_target` nothing is pinned, so an HTTP proxy the owner relies on keeps
    working, and a name that does not resolve on this computer is left to the normal door: it
    cannot be reached directly from here, and a proxy that resolves it is another machine.
    Private names, non-public literal addresses and names that resolve here to a non-public
    address are refused. ``resolve=False`` checks names and literals only, for a URL whose page was
    already read over checked, pinned addresses: looking its name up again could only answer
    differently, never more truly.
    """

    parts = urllib.parse.urlsplit(str(url or "").strip())
    host = (parts.hostname or "").strip().lower().rstrip(".")
    if not host:
        return
    if host == "localhost" or host.endswith(_PRIVATE_NAMESPACES):
        raise RemoteTurnTargetRefused("private_namespace", url)
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        pass
    else:
        if not _address_is_public(host):
            raise RemoteTurnTargetRefused("non_public_address", url)
        return
    if not resolve:
        return
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        return
    if not all(_address_is_public(str(info[4][0])) for info in infos):
        raise RemoteTurnTargetRefused("non_public_address", url)


def open_public_only(
    url: str,
    *,
    make_request: Callable[[str], Any],
    timeout: float,
    lookup: bool = False,
) -> Any:
    """Open ``url`` through the one outbound door, reaching only public addresses.

    ``make_request(url)`` builds the request for each hop. A redirect is followed only after its
    target passes the same check; more than :data:`MAX_HOPS` hops refuse. ``lookup`` is the door's
    (see `open_remote`); a refused lookup is refused before the target is resolved.
    """

    from core.remote_fetch_policy import open_remote, refuse_lookup_before_dns

    current = str(url)
    for _hop in range(MAX_HOPS + 1):
        if lookup:
            refuse_lookup_before_dns(current)
        addresses = resolve_public_target(current)
        try:
            return open_remote(
                make_request(current), timeout=timeout, redirect_policy="refuse", pinned_addresses=addresses, lookup=lookup
            )
        except urllib.error.HTTPError as exc:
            message = str(getattr(exc, "msg", "") or "")
            if int(exc.code or 0) not in _REDIRECT_CODES or not message.startswith("redirect_refused:"):
                raise
            location = message[len("redirect_refused:"):].strip()
            if not location:
                raise
            current = urllib.parse.urljoin(current, location)
    raise RemoteTurnTargetRefused("too_many_redirects", current)


__all__ = ["MAX_HOPS", "RemoteTurnTargetRefused", "open_public_only", "refuse_local_target", "resolve_public_target"]

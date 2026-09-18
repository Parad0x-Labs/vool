"""This runtime's own local HTTP service, as the model's outbound tools see it: never a destination.

The always-on API server serves the chat surface and every owner door (Contacts, Settings, wallet, email recovery) on a
loopback port. The browser lane (core.vool_browser) and ``web.fetch`` are tools a model -- and a skill steering a model --
can aim at any URL. Aimed at this service they would let tool arguments or page content drive the owner's own surface, so a
navigation or fetch whose host is a loopback name or address and whose port is a port this runtime serves is refused
before any socket opens.

Ports: ``DEFAULT_API_PORT`` plus every port the API server registers when it binds (apps/vool_api_server.py).
Loopback: ``localhost`` and ``*.localhost``, 127.0.0.0/8 and ::1 in every spelling ``inet_aton`` accepts (127.1,
2130706433, 0x7f.1), IPv4-mapped IPv6, and the unspecified addresses 0.0.0.0 and ::.

Limits, stated: a DNS name that resolves to a loopback address is not recognised here (rebinding); the owner doors' own
same-origin and JSON checks refuse a mutation that carries another origin.
"""
from __future__ import annotations

import ipaddress
import socket
import threading
from urllib.parse import urlsplit

DEFAULT_API_PORT = 11435
_LOCK = threading.Lock()
_PORTS: set[int] = set()


def register_served_port(port: int) -> None:
    with _LOCK:
        _PORTS.add(int(port))


def served_ports() -> frozenset[int]:
    with _LOCK:
        return frozenset({DEFAULT_API_PORT, *_PORTS})


def is_loopback_host(host: str) -> bool:
    name = str(host or "").strip().strip("[]").rstrip(".").lower()
    if not name:
        return False
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        try:
            address = ipaddress.IPv4Address(socket.inet_aton(name))
        except (OSError, ValueError):
            return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return bool(address.is_loopback or address.is_unspecified)


def runtime_origin_refusal(url: str) -> str:
    """'' when the URL is not this runtime's own service; otherwise the refusal to report."""
    try:
        parts = urlsplit(str(url or "").strip())
        port = parts.port
    except ValueError:
        return ""
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https", "ws", "wss"):
        return ""
    if port is None:
        port = 443 if scheme in ("https", "wss") else 80
    host = parts.hostname or ""
    if is_loopback_host(host) and port in served_ports():
        return (f"refused: {scheme}://{host}:{port} is this runtime's own local service (its chat and owner doors); "
                "tools do not open it, so nothing was requested.")
    return ""


__all__ = ["DEFAULT_API_PORT", "is_loopback_host", "register_served_port", "runtime_origin_refusal", "served_ports"]

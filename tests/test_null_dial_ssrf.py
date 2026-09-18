from __future__ import annotations

import socket
from unittest import mock

from core.null_dial import is_ssrf_safe_url


def _addrinfo(ip: str) -> list:
    """One getaddrinfo entry shaped like the stdlib returns (family-agnostic)."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    sockaddr = (ip, 0, 0, 0) if family == socket.AF_INET6 else (ip, 0)
    return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)]


def test_rejects_private_10_literal() -> None:
    assert is_ssrf_safe_url("https://10.0.0.5/x402") is False


def test_rejects_private_192_168_literal() -> None:
    assert is_ssrf_safe_url("https://192.168.1.10/pay") is False


def test_rejects_private_172_16_literal() -> None:
    assert is_ssrf_safe_url("https://172.16.4.4/x402") is False


def test_rejects_link_local_169_254_literal() -> None:
    # 169.254.169.254 is the AWS/cloud metadata endpoint.
    assert is_ssrf_safe_url("https://169.254.169.254/latest/meta-data/") is False
    assert is_ssrf_safe_url("https://169.254.1.1/x402") is False


def test_rejects_loopback_literal() -> None:
    assert is_ssrf_safe_url("https://127.0.0.1/x402") is False
    assert is_ssrf_safe_url("https://[::1]/x402") is False


def test_rejects_unspecified_literal() -> None:
    assert is_ssrf_safe_url("https://0.0.0.0/x402") is False


def test_rejects_multicast_literal() -> None:
    assert is_ssrf_safe_url("https://224.0.0.1/x402") is False


def test_rejects_cgnat_100_64_literal() -> None:
    # RFC 6598 carrier-grade NAT space — ipaddress does NOT flag it private,
    # so the explicit _CGNAT_V4 check is what rejects it.
    assert is_ssrf_safe_url("https://100.64.0.1/x402") is False
    assert is_ssrf_safe_url("https://100.127.255.254/x402") is False


def test_rejects_dns_rebinding_to_cgnat_ip() -> None:
    with mock.patch("core.null_dial.socket.getaddrinfo", return_value=_addrinfo("100.64.1.2")):
        assert is_ssrf_safe_url("https://carrier.example.com/x402") is False


def test_accepts_100_below_cgnat_range() -> None:
    # 100.63.x and 100.128.x are public — the /10 boundary must not over-reject.
    with mock.patch("core.null_dial.socket.getaddrinfo", return_value=_addrinfo("100.63.255.255")):
        assert is_ssrf_safe_url("https://just-below.example.com/x402") is True


def test_rejects_missing_host() -> None:
    assert is_ssrf_safe_url("https:///x402") is False
    assert is_ssrf_safe_url("") is False
    assert is_ssrf_safe_url("not a url") is False


def test_rejects_dns_rebinding_to_private_ip() -> None:
    # A perfectly public-looking hostname that resolves to a private IP must be
    # rejected (DNS rebinding defense).
    with mock.patch("core.null_dial.socket.getaddrinfo", return_value=_addrinfo("10.1.2.3")):
        assert is_ssrf_safe_url("https://evil.example.com/x402") is False


def test_rejects_dns_rebinding_to_metadata_ip() -> None:
    with mock.patch("core.null_dial.socket.getaddrinfo", return_value=_addrinfo("169.254.169.254")):
        assert is_ssrf_safe_url("https://innocent.example.com/x402") is False


def test_rejects_when_any_resolved_address_is_private() -> None:
    # If even one A/AAAA record is internal, reject the whole host.
    mixed = _addrinfo("93.184.216.34") + _addrinfo("10.0.0.9")
    with mock.patch("core.null_dial.socket.getaddrinfo", return_value=mixed):
        assert is_ssrf_safe_url("https://mixed.example.com/x402") is False


def test_fails_closed_on_resolution_error() -> None:
    with mock.patch("core.null_dial.socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
        assert is_ssrf_safe_url("https://nope.example.com/x402") is False


def test_fails_closed_on_empty_resolution() -> None:
    with mock.patch("core.null_dial.socket.getaddrinfo", return_value=[]):
        assert is_ssrf_safe_url("https://empty.example.com/x402") is False


def test_accepts_public_https_host() -> None:
    with mock.patch("core.null_dial.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert is_ssrf_safe_url("https://example.com/x402") is True


def test_accepts_public_https_host_with_port() -> None:
    with mock.patch("core.null_dial.socket.getaddrinfo", return_value=_addrinfo("93.184.216.34")):
        assert is_ssrf_safe_url("https://pay.parad0xlabs.com:8443/x402?asset=usdc") is True

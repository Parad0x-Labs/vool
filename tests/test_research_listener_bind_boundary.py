"""Research opt-in is checked at the listener, before opening a socket."""
from __future__ import annotations

import socket
from types import SimpleNamespace

import pytest

from network import stream_transport, transport


@pytest.mark.parametrize("kind", ["udp", "stream"])
def test_non_loopback_listener_refuses_before_socket_creation(monkeypatch, kind):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    module = transport if kind == "udp" else stream_transport
    server_type = transport.UDPTransportServer if kind == "udp" else stream_transport.StreamTransportServer
    server = server_type(host="0.0.0.0", port=0)

    def unexpected(*args, **kwargs):
        pytest.fail("non-loopback listener reached socket creation without research opt-in")

    monkeypatch.setattr(module, "socket", SimpleNamespace(
        socket=unexpected, AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM, SOCK_DGRAM=socket.SOCK_DGRAM,
    ))
    with pytest.raises(PermissionError, match="VOOL_RESEARCH_NETWORKING"):
        server.start()


def test_prebound_socket_cannot_bypass_the_host_check(monkeypatch):
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    class Socket:
        def getsockname(self):
            return ("0.0.0.0", 32100)
        def listen(self, backlog):
            pytest.fail("prebound wildcard socket reached listen without research opt-in")
        def close(self):
            pass
    with pytest.raises(PermissionError, match="VOOL_RESEARCH_NETWORKING"):
        stream_transport.StreamTransportServer(host="127.0.0.1").start(prebound_socket=Socket())


def test_default_udp_bind_is_loopback():
    assert transport.UDPTransportServer().host == "127.0.0.1"


@pytest.mark.parametrize("host", ["", "::", "192.168.1.1", "example.com", "localhost.attacker", "127.0.0.1.attacker"])
def test_non_loopback_or_ambiguous_hosts_require_opt_in(monkeypatch, host):
    from core.runtime_mode import checked_research_listener_host
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    with pytest.raises(PermissionError):
        checked_research_listener_host(host)


@pytest.mark.parametrize("host,expected", [("127.0.0.2", "127.0.0.2"), ("localhost", "127.0.0.1"), ("::1", "::1")])
def test_literal_loopback_hosts_remain_available(monkeypatch, host, expected):
    from core.runtime_mode import checked_research_listener_host
    monkeypatch.delenv("VOOL_RESEARCH_NETWORKING", raising=False)
    assert checked_research_listener_host(host) == expected


def test_explicit_research_invocation_can_listen_on_prebound_wildcard(monkeypatch):
    monkeypatch.setenv("VOOL_RESEARCH_NETWORKING", "1")
    events = []
    class Socket:
        def getsockname(self):
            return ("0.0.0.0", 32100)
        def listen(self, backlog):
            events.append("listen")
        def close(self):
            events.append("close")
    server = stream_transport.StreamTransportServer(host="0.0.0.0")
    monkeypatch.setattr(stream_transport, "threading", SimpleNamespace(Thread=lambda **kwargs: SimpleNamespace(
        start=lambda: None, is_alive=lambda: False,
    )))
    try:
        endpoint = server.start(prebound_socket=Socket())
        assert endpoint.host == "0.0.0.0"
        assert events == ["listen"]
    finally:
        server.stop()
    assert events == ["listen", "close"]

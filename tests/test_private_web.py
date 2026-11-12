"""Private web: with Tor or a VPN chosen, VOOL's web traffic goes that way or not at all.

These tests use real sockets on this machine and nothing outside it:

* a tiny SOCKS5 server stands in for Tor / the WireGuard tunnel. It records every CONNECT it is
  asked for and relays it to a local HTTP server, whatever name was asked for;
* the HTTP server stands in for a public site and counts the requests that reach it.

What each test settles is whether a request reached the site, by which path, and whether any
DNS question for the site's name was asked on this machine.
"""

from __future__ import annotations

import http.server
import json
import socket
import socketserver
import struct
import threading
import urllib.request
from collections.abc import Iterator

import pytest

from core import private_web
from core.private_web import PrivateWebBlocked, RouteState
from core.remote_fetch_policy import PrivateWebDownError, open_remote, remote_fetch_policy_scope

ALLOWED = {"allow_remote_fetch": True}


class _Site(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        type(self).hits.append(f"{self.headers.get('Host')}{self.path}")
        body = json.dumps({"host": self.headers.get("Host"), "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


class _ThreadingServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def site() -> Iterator[tuple[int, list[str]]]:
    handler = type("Site", (_Site,), {"hits": []})
    server = _ThreadingServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], handler.hits
    server.shutdown()
    server.server_close()


class _FakeSocks:
    """SOCKS5, no auth or user/pass. Every CONNECT is relayed to the local site."""

    def __init__(self, site_port: int) -> None:
        self.site_port = site_port
        self.requests: list[tuple[int, str, int]] = []  # (address type, host, port)
        self.users: list[str] = []
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        self.listener.settimeout(0.2)
        while not self._stop.is_set():
            try:
                client, _ = self.listener.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    @staticmethod
    def _recv(sock: socket.socket, n: int) -> bytes:
        data = b""
        while len(data) < n:
            part = sock.recv(n - len(data))
            if not part:
                raise ConnectionError("closed")
            data += part
        return data

    def _handle(self, client: socket.socket) -> None:
        try:
            _ver, count = self._recv(client, 2)
            methods = self._recv(client, count)
            if 2 in methods:
                client.sendall(b"\x05\x02")
                _v, ulen = self._recv(client, 2)
                user = self._recv(client, ulen).decode()
                (plen,) = self._recv(client, 1)
                self._recv(client, plen)
                self.users.append(user)
                client.sendall(b"\x01\x00")
            else:
                client.sendall(b"\x05\x00")
            header = client.recv(4)
            if len(header) < 4:
                return  # a health probe that only greets
            _v, _cmd, _rsv, atyp = header
            if atyp == 3:
                (length,) = self._recv(client, 1)
                host = self._recv(client, length).decode()
            elif atyp == 1:
                host = socket.inet_ntoa(self._recv(client, 4))
            else:
                host = socket.inet_ntop(socket.AF_INET6, self._recv(client, 16))
            (port,) = struct.unpack("!H", self._recv(client, 2))
            self.requests.append((atyp, host, port))
            upstream = socket.create_connection(("127.0.0.1", self.site_port))
            client.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack("!H", 0))
            self._pipe(client, upstream)
        except (ConnectionError, OSError):
            pass
        finally:
            client.close()

    @staticmethod
    def _pipe(a: socket.socket, b: socket.socket) -> None:
        def pump(src: socket.socket, dst: socket.socket) -> None:
            try:
                while True:
                    data = src.recv(65536)
                    if not data:
                        break
                    dst.sendall(data)
            except OSError:
                pass
            finally:
                for s in (src, dst):
                    try:
                        s.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        t = threading.Thread(target=pump, args=(b, a), daemon=True)
        t.start()
        pump(a, b)
        t.join(timeout=5)
        b.close()

    def stop(self) -> None:
        self._stop.set()
        self.listener.close()
        self._thread.join(timeout=2)


@pytest.fixture
def route(monkeypatch):
    """Set the route the way the setting would, and put Private web back to off afterwards."""
    holder: dict[str, RouteState] = {"state": RouteState()}
    monkeypatch.setattr(private_web, "_route_from_policy", lambda: holder["state"])
    monkeypatch.setattr(private_web, "_probe_ok_until", 0.0)
    private_web.install_egress_guard()

    def use(kind: str, port: int = 0) -> None:
        holder["state"] = RouteState(kind=kind, socks_port=port)
        private_web.refresh()

    yield use
    holder["state"] = RouteState()
    private_web.refresh()
    private_web.clear_system_vpn_verified()


def _get(url: str) -> dict:
    with remote_fetch_policy_scope(ALLOWED):
        with open_remote(urllib.request.Request(url), timeout=5.0) as response:
            return json.loads(response.read())


# --------------------------------------------------------------------------- through the route


def test_through_tor_the_site_is_reached_by_name_and_never_looked_up_here(site, route) -> None:
    site_port, hits = site
    socks = _FakeSocks(site_port)
    try:
        route("tor", socks.port)
        answer = _get("http://news.example.test/today")
    finally:
        socks.stop()

    assert answer["path"] == "/today"
    # Address type 3 = a NAME: the lookup happened at the far end. The egress guard is on, so a
    # local lookup of news.example.test would have raised before any of this.
    assert socks.requests == [(3, "news.example.test", 80)]
    assert hits == ["news.example.test/today"]
    assert socks.users and socks.users[0].startswith("vool"), "Tor gets a per-chat user for circuit isolation"


def test_a_redirect_is_followed_through_the_same_route(site, route, monkeypatch) -> None:
    site_port, _hits = site

    class Redirect(_Site):
        hits: list[str] = []

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/old":
                self.send_response(302)
                self.send_header("Location", "http://other.example.test/new")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            super().do_GET()

    server = _ThreadingServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    socks = _FakeSocks(server.server_address[1])
    try:
        route("wireguard", socks.port)
        answer = _get("http://first.example.test/old")
    finally:
        socks.stop()
        server.shutdown()
        server.server_close()

    assert answer["path"] == "/new"
    assert [r[1] for r in socks.requests] == ["first.example.test", "other.example.test"]


# --------------------------------------------------------------------------------- fail closed


def test_route_on_but_not_running_refuses_before_any_socket(site, route) -> None:
    _site_port, hits = site
    route("tor", 0)
    with pytest.raises(PrivateWebDownError) as refusal:
        _get("http://news.example.test/today")
    assert "not running" in str(refusal.value)
    assert hits == []


def test_route_down_refuses_and_never_goes_direct(site, route) -> None:
    _site_port, hits = site
    dead = socket.create_server(("127.0.0.1", 0))
    dead_port = dead.getsockname()[1]
    dead.close()  # nothing listens there now
    route("tor", dead_port)
    with pytest.raises(PrivateWebDownError) as refusal:
        _get("http://news.example.test/today")
    assert "down" in str(refusal.value)
    assert hits == []


def test_the_route_dropping_mid_turn_ends_in_a_failure_not_a_direct_request(site, route) -> None:
    site_port, hits = site
    socks = _FakeSocks(site_port)
    route("tor", socks.port)
    with remote_fetch_policy_scope(ALLOWED):
        with open_remote(urllib.request.Request("http://a.example.test/1"), timeout=5.0) as response:
            response.read()
        socks.stop()
        with pytest.raises(OSError):  # refused at the door, or the dead SOCKS port refuses
            open_remote(urllib.request.Request("http://a.example.test/2"), timeout=5.0)
    assert hits == ["a.example.test/1"]


def test_an_unknown_route_value_is_never_treated_as_off(site, route) -> None:
    _site_port, hits = site
    route("carrier-pigeon", 0)
    with pytest.raises(PrivateWebDownError):
        _get("http://news.example.test/today")
    with pytest.raises(PrivateWebBlocked):
        socket.getaddrinfo("example.com", 443)
    assert hits == []


def test_system_vpn_refuses_while_the_vpn_is_not_carrying_traffic(site, route, monkeypatch) -> None:
    from core import system_vpn_check

    monkeypatch.setattr(system_vpn_check, "default_route_interface", lambda: "en0")
    _site_port, hits = site
    route("system_vpn")
    with pytest.raises(PrivateWebDownError) as refusal:
        _get("http://news.example.test/today")
    assert "confirm your VPN" in str(refusal.value)
    with pytest.raises(PrivateWebBlocked):
        socket.create_connection(("192.0.2.10", 443), timeout=0.2)
    assert hits == []


# ------------------------------------------------------------------- the guard, outside the door


def test_with_a_route_on_direct_lookups_and_connections_are_stopped(route) -> None:
    route("tor", 9)
    with pytest.raises(PrivateWebBlocked):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(PrivateWebBlocked):
        socket.create_connection(("192.0.2.10", 443), timeout=0.2)
    with pytest.raises(PrivateWebBlocked):
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x", ("192.0.2.53", 53))
    # any library that never used the door (cloud model calls, email, ...) fails like an
    # unreachable host, before a DNS question or a packet leaves
    import http.client

    with pytest.raises(ConnectionRefusedError):
        http.client.HTTPSConnection("example.com", timeout=0.5).connect()


def test_with_a_route_on_this_machine_and_the_local_network_stay_reachable(site, route) -> None:
    site_port, hits = site
    route("tor", 9)
    socket.getaddrinfo("localhost", site_port)
    with socket.create_connection(("127.0.0.1", site_port), timeout=2):
        pass
    for lan in ("192.168.1.20", "10.0.0.5", "fd00::1"):
        assert private_web._ip_stays_local(lan)
    assert not private_web._ip_stays_local("93.184.216.34")
    # a loopback URL through the door goes direct, never into the route
    assert _get(f"http://127.0.0.1:{site_port}/local")["path"] == "/local"


def test_with_private_web_off_nothing_is_stopped(route) -> None:
    route("off")
    assert private_web.route_problem() is None
    private_web._audit("socket.getaddrinfo", ("example.com", 443, 0, 0, 0))
    private_web._audit("socket.connect", (None, ("93.184.216.34", 443)))


# -------------------------------------------------------------------------- the callers around it


def test_public_target_checks_skip_the_local_lookup_under_a_route(route) -> None:
    from core.remote_turn_fetch import RemoteTurnTargetRefused, refuse_local_target, resolve_public_target

    route("tor", 9)
    assert resolve_public_target("https://news.example.test/a") == ()
    refuse_local_target("https://news.example.test/a")
    with pytest.raises(RemoteTurnTargetRefused):
        resolve_public_target("http://localhost/x")
    with pytest.raises(RemoteTurnTargetRefused):
        resolve_public_target("http://192.168.1.1/admin")


def test_browser_engines_get_the_route_or_are_not_launched(route) -> None:
    route("tor", 0)
    assert "not running" in (private_web.browser_route().problem or "")

    socks = socket.create_server(("127.0.0.1", 0))
    port = socks.getsockname()[1]

    def greet() -> None:
        conn, _ = socks.accept()
        conn.recv(16)
        conn.sendall(b"\x05\x00")
        conn.close()

    threading.Thread(target=greet, daemon=True).start()
    route("tor", port)
    browser = private_web.browser_route()
    socks.close()
    assert browser.problem is None
    assert f"--proxy-server=socks5://127.0.0.1:{port}" in browser.flags
    assert any(flag.startswith("--host-resolver-rules=MAP * ~NOTFOUND") for flag in browser.flags)
    assert browser.playwright_proxy == {"server": f"socks5://127.0.0.1:{port}"}


def test_chrome_render_refuses_when_the_route_is_down(route, monkeypatch) -> None:
    from tools.browser import browser_render

    launched: list[list[str]] = []
    monkeypatch.setattr(browser_render, "_run_chrome", lambda argv, timeout_s: launched.append(argv) or (0, b"", b""))
    route("tor", 0)
    answer = browser_render._chrome_render_unchecked("https://duckduckgo.com/?q=x", binary="/bin/true")
    assert answer["status"] == "private_web_down"
    assert launched == []


def test_a_local_searxng_is_not_asked_under_a_route(route) -> None:
    from tools.web.searxng_client import SearXNGClient, SearXNGUnavailableError

    route("tor", 9)
    with pytest.raises(SearXNGUnavailableError) as refusal:
        SearXNGClient(base_url="http://127.0.0.1:8080").search("weather")
    assert refusal.value.reason == "private_web"


def test_model_downloads_wait_while_private_web_is_on(route) -> None:
    from core.web.api import runtime

    route("tor", 9)

    class Progress:
        base_url = "http://127.0.0.1:11434"

    with pytest.raises(RuntimeError, match="Model downloads wait"):
        runtime._stream_ollama_pull("qwen3:8b", progress=Progress())


def test_cloud_model_worker_goes_through_the_route(site, route) -> None:
    from core import provider_http

    site_port, hits = site
    socks = _FakeSocks(site_port)
    try:
        route("tor", socks.port)
        response = provider_http.get("http://api.example.test/v1/models", timeout=10)
        payload = response.json()
    finally:
        socks.stop()
    assert payload["path"] == "/v1/models"
    assert socks.requests == [(3, "api.example.test", 80)]
    assert hits == ["api.example.test/v1/models"]


def test_cloud_model_worker_is_not_started_when_the_route_is_down(route) -> None:
    from core import provider_http

    route("tor", 0)
    with pytest.raises(provider_http.PrivateWebUnavailable):
        provider_http.get("https://api.example.test/v1/models", timeout=5)


def test_system_vpn_lets_traffic_out_only_while_the_vpn_carries_it(site, route, monkeypatch) -> None:
    from core import system_vpn_check

    site_port, hits = site
    iface = {"name": "utun4"}
    monkeypatch.setattr(system_vpn_check, "default_route_interface", lambda: iface["name"])
    monkeypatch.setattr(private_web, "_SYSTEM_VPN_RECHECK_SECONDS", 0.1)
    monkeypatch.setattr(private_web, "_SYSTEM_VPN_VALID_SECONDS", 0.3)
    route("system_vpn")
    assert private_web.route_problem() is None
    private_web._audit("socket.connect", (None, ("93.184.216.34", 443)))  # allowed while VPN is up

    iface["name"] = "en0"  # the VPN app drops
    import time as _time

    _time.sleep(0.8)
    with pytest.raises(PrivateWebBlocked):
        private_web._audit("socket.connect", (None, ("93.184.216.34", 443)))
    with pytest.raises(PrivateWebDownError):
        _get("http://news.example.test/today")
    assert hits == []


@pytest.mark.parametrize(
    "platform_name, output, expected",
    [
        ("darwin", "   route to: default\ndestination: default\n  interface: utun3\n", "utun3"),
        ("darwin", "   route to: default\n    gateway: 192.168.1.1\n  interface: en0\n", "en0"),
        ("linux", "1.1.1.1 dev wg0 table 51820 src 10.66.1.2 uid 1000\n", "wg0"),
        ("linux", "1.1.1.1 via 192.168.1.1 dev wlp2s0 src 192.168.1.20\n", "wlp2s0"),
    ],
)
def test_the_vpn_check_reads_the_route_the_computer_would_use(platform_name, output, expected, monkeypatch) -> None:
    from core import system_vpn_check

    monkeypatch.setattr(system_vpn_check.sys, "platform", platform_name)
    monkeypatch.setattr(system_vpn_check.shutil, "which", lambda name: "/sbin/ip")
    monkeypatch.setattr(system_vpn_check, "_run", lambda argv: output)
    assert system_vpn_check.default_route_interface() == expected
    assert system_vpn_check.is_tunnel_interface(expected) == expected.startswith(("utun", "wg"))


def test_a_weather_question_while_the_route_is_down_says_so_plainly(route, monkeypatch) -> None:
    """Measured on the Mac: with Tor down, "weather in Berlin" answered "no current conditions
    found", as if VOOL had looked. It must say the route is down and nothing was sent."""
    from core import policy_engine
    from core.agent_runtime.fast_live_info_runtime_preflight import prepare_live_info_request

    class Agent:
        def _live_info_mode(self, text, interpretation=None):
            return "weather"

        def _recover_price_lookup_query(self, text, source_context=None):
            return ""

        def _normalize_live_info_query(self, text, mode=""):
            return text

        def _requires_ultra_fresh_insufficient_evidence(self, text):
            return False

        def _fast_path_result(self, **kwargs):
            return dict(kwargs)

    monkeypatch.setattr(policy_engine, "allow_web_fallback", lambda: True)
    question = "What is the weather in Berlin right now?"

    route("tor", 0)
    mode, _query, result = prepare_live_info_request(
        Agent(), question, session_id="s", source_context={}, interpretation=None
    )
    assert mode == "weather" and result is not None
    assert result["live_info_refusal"] is True
    assert result["response"].startswith("Private web through Tor is not running, so nothing was sent.")
    assert "Settings" in result["response"]

    route("off")
    mode, query, result = prepare_live_info_request(
        Agent(), question, session_id="s", source_context={}, interpretation=None
    )
    assert mode == "weather" and query and result is None, "with Private web off the lookup goes ahead"

"""Private web through the person's own WireGuard file.

The file checks run everywhere. The tunnel tests run a REAL WireGuard pair on this machine: two
wireproxy processes, one as the person's VPN client (VOOL's side) and one standing in for the VPN
server, which forwards what reaches it inside the tunnel to a local web server. They need the
wireproxy binary (VOOL_WIREPROXY_BINARY or on the PATH), the same binary VOOL ships.
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import stat
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from core import private_web, wireguard_runtime
from core.remote_fetch_policy import PrivateWebDownError, open_remote, remote_fetch_policy_scope
from core.wireguard_runtime import WireGuardFileProblem, WireGuardRuntime, parse_wireguard_file

KEY_A = "yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk="
KEY_B = "HIgo9xNzJMWLKASShiTqIybxZ0U3wGLiUeJ1PKf8ykw="

GOOD = f"""[Interface]
PrivateKey = {KEY_A}
Address = 10.66.1.2/32
DNS = 10.64.0.1

[Peer]
PublicKey = {KEY_B}
AllowedIPs = 0.0.0.0/0
Endpoint = vpn.example.test:51820
"""


def test_a_provider_file_is_accepted_and_its_dns_kept() -> None:
    profile = parse_wireguard_file(GOOD)
    assert profile.dns == ("10.64.0.1",)
    assert profile.endpoint == "vpn.example.test:51820"


@pytest.mark.parametrize(
    "broken, says",
    [
        ("hello", "does not look like a WireGuard file"),
        (GOOD.replace(f"PrivateKey = {KEY_A}", "PrivateKey = nope"), "PrivateKey"),
        (GOOD.replace("Endpoint = vpn.example.test:51820\n", ""), "Endpoint"),
        (GOOD.replace("DNS = 10.64.0.1\n", ""), "no DNS line"),
    ],
)
def test_a_broken_file_is_refused_in_plain_words(broken: str, says: str) -> None:
    with pytest.raises(WireGuardFileProblem, match=says):
        parse_wireguard_file(broken)


def test_the_file_is_kept_owner_only(tmp_path, monkeypatch) -> None:
    from core import runtime_paths

    monkeypatch.setattr(runtime_paths, "data_path", lambda *parts: tmp_path.joinpath(*parts))
    wireguard_runtime.save_wireguard_file(GOOD)
    kept = tmp_path / "private-web" / "wireguard.conf"
    assert stat.S_IMODE(os.stat(kept).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(kept.parent).st_mode) == 0o700


def test_no_file_yet_is_a_plain_refusal(tmp_path) -> None:
    rt = WireGuardRuntime(config_path=tmp_path / "missing.conf", binary="/bin/true")
    with pytest.raises(wireguard_runtime.WireGuardUnavailable, match="No VPN file"):
        rt.start()


# ------------------------------------------------------------------------- a real tunnel


WIREPROXY = wireguard_runtime.find_wireproxy_binary()
needs_wireproxy = pytest.mark.skipif(not WIREPROXY, reason="wireproxy binary not available on this machine")


def _keypair() -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    import base64

    key = X25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode(), base64.b64encode(pub).decode()


class _Site(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits.append(self.path)
        body = json.dumps({"path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def vpn(tmp_path, monkeypatch):
    """A WireGuard 'VPN server' on this machine, and the person's file for it."""
    site = _Server(("127.0.0.1", 0), type("Site", (_Site,), {"hits": []}))
    threading.Thread(target=site.serve_forever, daemon=True).start()
    client_private, client_public = _keypair()
    server_private, server_public = _keypair()
    udp = _free_udp_port()
    server_conf = tmp_path / "server.conf"
    server_conf.write_text(
        f"""[Interface]
PrivateKey = {server_private}
Address = 100.64.0.2/32
ListenPort = {udp}

[Peer]
PublicKey = {client_public}
AllowedIPs = 100.64.0.1/32

[TCPServerTunnel]
ListenPort = 80
Target = 127.0.0.1:{site.server_address[1]}

[TCPServerTunnel]
ListenPort = 53
Target = 127.0.0.1:{site.server_address[1]}
"""
    )
    server = subprocess.Popen([WIREPROXY, "-s", "-c", str(server_conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    person_file = tmp_path / "my-vpn.conf"
    person_file.write_text(
        f"""[Interface]
PrivateKey = {client_private}
Address = 100.64.0.1/32
DNS = 100.64.0.2

[Peer]
PublicKey = {server_public}
AllowedIPs = 0.0.0.0/0
Endpoint = 127.0.0.1:{udp}
PersistentKeepalive = 1
"""
    )
    yield {"file": person_file, "hits": site.RequestHandlerClass.hits, "server": server}
    if server.poll() is None:
        server.terminate()
        server.wait(timeout=5)
    site.shutdown()
    site.server_close()


@pytest.fixture
def wg_route(vpn, monkeypatch):
    rt = WireGuardRuntime(config_path=vpn["file"], binary=WIREPROXY)
    monkeypatch.setattr(wireguard_runtime, "shared", lambda: rt)
    from core import policy_engine

    real_get = policy_engine.get
    monkeypatch.setattr(
        policy_engine, "get",
        lambda path, default=None: "wireguard" if path == "network.private_web.route" else real_get(path, default),
    )
    private_web.install_egress_guard()
    yield rt
    rt.stop()
    monkeypatch.undo()
    private_web.refresh()


def _get(url: str) -> dict:
    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        with open_remote(urllib.request.Request(url), timeout=10.0) as response:
            return json.loads(response.read())


@needs_wireproxy
def test_through_a_real_wireguard_tunnel_the_site_is_reached(vpn, wg_route) -> None:
    wg_route.start(timeout=20)
    assert private_web.route_problem() is None
    assert _get("http://100.64.0.2/hello")["path"] == "/hello"
    assert "/hello" in vpn["hits"]


@needs_wireproxy
def test_when_the_vpn_server_goes_away_nothing_is_sent(vpn, wg_route) -> None:
    wg_route.start(timeout=20)
    vpn["server"].terminate()
    vpn["server"].wait(timeout=5)
    time.sleep(wireguard_runtime._HEALTH_TTL_SECONDS + 0.5)
    before = list(vpn["hits"])
    with pytest.raises((PrivateWebDownError, OSError)):
        _get("http://100.64.0.2/after")
    assert vpn["hits"] == before


@needs_wireproxy
def test_a_file_for_a_server_that_does_not_answer_never_comes_up(vpn, monkeypatch, tmp_path) -> None:
    vpn["server"].terminate()
    vpn["server"].wait(timeout=5)
    rt = WireGuardRuntime(config_path=vpn["file"], binary=WIREPROXY)
    with pytest.raises(wireguard_runtime.WireGuardUnavailable, match="did not answer"):
        rt.start(timeout=4)
    assert not rt.running

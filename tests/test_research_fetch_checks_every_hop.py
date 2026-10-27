"""Research pages cannot redirect or re-resolve into a service on this computer.

Both endpoints are real HTTP servers. The fixture gives the public origin a DNS
answer and maps that address onto its owned public server; loopback is delivered
unchanged to the separate private server. No production fetch or pin is replaced.
"""
from __future__ import annotations

import copy
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from core import policy_engine
from core.remote_fetch_policy import remote_fetch_turn_scope
from tests import _network_seal
from tools.web.http_fetch import http_fetch_text

PUBLIC_HOST = "research-origin.example.test"
PUBLIC_ADDRESS = "8.8.8.8"
OWNER = {"surface": "api", "_http_ingress": True, "_owner_local": True, "allow_remote_fetch": True}


@pytest.fixture
def research_origins(monkeypatch):
    policy = copy.deepcopy(policy_engine.load())
    policy["system"].update(local_only_mode=False, allow_web_fallback=True)
    policy.setdefault("network", {})["outbound_enabled"] = True
    monkeypatch.setattr(policy_engine, "_POLICY_CACHE", policy)
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setattr(urllib.request, "_opener", None)
    state = SimpleNamespace(private_hits=[], public_hits=[], lookups=0, rebind=False, connections=[])

    class Private(BaseHTTPRequestHandler):
        def do_GET(self):
            state.private_hits.append(self.path)
            body = b"<p>private service content</p>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    private = ThreadingHTTPServer(("127.0.0.1", 0), Private)

    class Public(BaseHTTPRequestHandler):
        def do_GET(self):
            state.public_hits.append(self.path)
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{private.server_port}/private")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"<p>public research fixture content</p>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    public = ThreadingHTTPServer(("127.0.0.1", 0), Public)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (private, public)]
    for thread in threads:
        thread.start()
    resolve = socket.getaddrinfo

    def dns(host, port, *args, **kwargs):
        if str(host) == PUBLIC_HOST:
            state.lookups += 1
            address = "127.0.0.1" if state.rebind and state.lookups > 1 else PUBLIC_ADDRESS
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, int(port)))]
        return resolve(host, port, *args, **kwargs)

    def deliver(sock, address):
        if isinstance(address, tuple) and address[0] == PUBLIC_ADDRESS:
            state.connections.append(address)
            address = ("127.0.0.1", public.server_port)
        # Reassertion may seat this map beneath the seal. Consult the complete
        # policy stack, then use the socket implementation rather than recursing
        # through the guard that delegated to this map.
        _network_seal._run(_network_seal.KIND_CONNECT, address)
        return _network_seal._PRISTINE_CONNECT(sock, address)

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(socket.socket, "connect", deliver)
    guarded_urlopen = urllib.request.urlopen
    owned_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def owned_urlopen(request, *args, **kwargs):
        # The suite blocks public URL spellings before DNS. Permit only this
        # fixture's spelling; its sockets still pass the owned address map.
        target = getattr(request, "full_url", request)
        if urlsplit(str(target)).hostname == PUBLIC_HOST:
            return owned_opener.open(request, *args, **kwargs)
        return guarded_urlopen(request, *args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", owned_urlopen)
    state.url = f"http://{PUBLIC_HOST}:{private.server_port}"
    try:
        yield state
    finally:
        for server in (private, public):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)


def test_a_research_redirect_is_refused_before_the_private_socket_opens(research_origins):
    with remote_fetch_turn_scope(OWNER):
        result = http_fetch_text(research_origins.url + "/redirect", not_local=True, timeout_s=2)
    assert research_origins.public_hits == ["/redirect"], result
    assert research_origins.private_hits == [], "the redirect reached a service on this computer"
    assert result["status"] == "private_target_refused", result


def test_a_research_name_cannot_rebind_after_its_public_check(research_origins):
    research_origins.rebind = True
    with remote_fetch_turn_scope(OWNER):
        result = http_fetch_text(research_origins.url + "/article", not_local=True, timeout_s=2)
    assert research_origins.private_hits == [], "DNS changed after the check and the private service was reached"
    assert research_origins.public_hits == ["/article"], result
    assert result["status"] == "ok" and "public research fixture" in result["text"], result


def test_a_public_research_page_still_reaches_the_owned_transport(research_origins):
    with remote_fetch_turn_scope(OWNER):
        result = http_fetch_text(research_origins.url + "/article", not_local=True, timeout_s=2)
    assert result["status"] == "ok" and "public research fixture" in result["text"], result
    assert research_origins.public_hits == ["/article"] and research_origins.private_hits == []

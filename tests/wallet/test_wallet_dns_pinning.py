"""Wallet validation must pin the DNS answer through the actual socket connection."""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.wallet import outbound
from core.wallet.errors import WalletFault


@pytest.mark.parametrize("proxy_bypass", ["rotating.localhost", ""])
def test_wallet_connects_without_resolving_the_validated_host_again(wallet_env, monkeypatch, proxy_bypass):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append((self.path, self.headers["Host"]))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    resolutions = []
    original = socket.getaddrinfo

    def changing_dns(host, port, *args, **kwargs):
        if host == "rotating.localhost":
            resolutions.append(host)
            assert len(resolutions) == 1, "transport resolved the attacker-controlled host after validation"
            return original("127.0.0.1", port, *args, **kwargs)
        return original(host, port, *args, **kwargs)

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setattr(socket, "getaddrinfo", changing_dns)
    # Even a configured proxy must not take over resolution of a wallet target.
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("no_proxy", proxy_bypass)
    port = server.server_address[1]
    try:
        result = outbound.fetch(f"http://rotating.localhost:{port}/resource")
        assert result["body"] == b"ok"
        assert requests == [("/resource", f"rotating.localhost:{port}")]
        assert resolutions == ["rotating.localhost"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("addresses", [[], ["8.8.8.8", "127.0.0.1"], ["100.64.0.1"], ["::ffff:127.0.0.1"]])
def test_wallet_refuses_empty_or_non_global_answers(monkeypatch, addresses):
    monkeypatch.delenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", raising=False)
    monkeypatch.setattr(outbound, "resolve_host_addresses", lambda host: addresses)
    with pytest.raises(WalletFault):
        outbound.validate_target("https://resource.example/pay")


def test_pinned_https_preserves_sni_and_checks_the_hostname(tmp_path, monkeypatch):
    import datetime
    import ssl
    import urllib.error

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from core.remote_fetch_policy import _no_redirect_opener

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "resource.example")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("resource.example")]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    sni = []
    hosts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            hosts.append(self.headers["Host"])
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_2
    server_context.load_cert_chain(cert_path, key_path)
    server_context.set_servername_callback(lambda sock, hostname, ctx: sni.append(hostname))
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client_context = ssl.create_default_context(cafile=str(cert_path))
    opener = _no_redirect_opener(pinned_addresses=("127.0.0.1",), context=client_context)
    port = server.server_address[1]

    def no_dns(*args, **kwargs):
        raise AssertionError("Pinned transport must never resolve a hostname")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    try:
        with opener.open(f"https://resource.example:{port}/", timeout=2) as response:
            assert response.read() == b"ok"
        assert hosts == [f"resource.example:{port}"]
        assert sni == ["resource.example"]
        with pytest.raises(urllib.error.URLError) as caught:
            opener.open(f"https://wrong.example:{port}/", timeout=2)
        assert isinstance(caught.value.reason, ssl.SSLCertVerificationError)
        assert hosts == [f"resource.example:{port}"]  # No HTTP request under a mismatched certificate.
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

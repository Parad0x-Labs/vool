"""Packaged CA roots and real HTTPS redirect-refusal context isolation."""
import datetime
import http.server
import ipaddress
import ssl
import threading
import urllib.error

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from core.effect_gateway import named_background_effect_scope
from core.remote_fetch_policy import open_remote_url
from core.updater.download import _release_tls_context


def test_packaged_ca_roots_work_without_a_host_ca_installation(tmp_path, monkeypatch):
    monkeypatch.setenv('SSL_CERT_FILE', str(tmp_path / 'absent.pem'))
    monkeypatch.setenv('SSL_CERT_DIR', str(tmp_path / 'absent-directory'))
    assert ssl.create_default_context().cert_store_stats()['x509_ca'] == 0
    context = _release_tls_context()
    assert context.cert_store_stats()['x509_ca'] > 0
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED


def test_refusing_redirects_honors_tls_context_and_never_reuses_another_trust_set(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'disposable loopback TLS')])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(key, hashes.SHA256()))
    cert, private = tmp_path / 'tls.crt', tmp_path / 'tls.key'
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()))
    private.chmod(0o600)
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(301 if self.path == '/redirect' else 200)
            if self.path == '/redirect':
                self.send_header('Location', '/ok')
            self.end_headers()
            self.wfile.write(b'ok')

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, private)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    origin = f'https://127.0.0.1:{server.server_port}'
    trusted = ssl.create_default_context(cafile=str(cert))
    try:
        with named_background_effect_scope('self_update.manifest_check'):
            with open_remote_url(origin + '/ok', context=trusted, redirect_policy='refuse', no_proxy=True) as response:
                assert response.read() == b'ok'
        with named_background_effect_scope('self_update.manifest_check'), pytest.raises(urllib.error.URLError):
            open_remote_url(origin + '/ok', context=_release_tls_context(), redirect_policy='refuse', no_proxy=True)
        with named_background_effect_scope('self_update.manifest_check'), pytest.raises(urllib.error.HTTPError) as refusal:
            open_remote_url(origin + '/redirect', context=trusted, redirect_policy='refuse', no_proxy=True)
        assert refusal.value.code == 301
        assert requests == ['/ok', '/redirect']
    finally:
        server.shutdown(); server.server_close(); private.unlink()

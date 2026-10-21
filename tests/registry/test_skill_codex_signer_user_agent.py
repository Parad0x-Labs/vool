"""The signer names itself, so a site firewall that refuses Python's default client lets its signed requests through.

On 2026-10-20 the live site's firewall answered every signer request with 403 "error code: 1010" because urllib
sent "Python-urllib/3.12". This server answers the same way; the real signer transport must get through it."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from registry_service.authority import HEADER
from registry_service.mac_authority import Authority


def test_the_signer_gets_past_a_firewall_that_refuses_pythons_default_client_name():
    seen = []

    class Firewall(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            agent = self.headers.get("User-Agent", "")
            seen.append((agent, self.headers.get(HEADER)))
            refused = agent.startswith("Python-urllib")
            self.send_response(403 if refused else 200)
            self.end_headers()
            self.wfile.write(b"error code: 1010" if refused else b"{}")

        def do_POST(self):
            self.do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Firewall)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/registry/api/authority/moderation"
        assert Authority._http(None, "GET", url, b"", {HEADER: "fixture-signature"}) == (200, b"{}")
        assert Authority._http(None, "POST", url, b"{}", {HEADER: "fixture-signature",
                                                           "Content-Type": "application/json"}) == (200, b"{}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
    assert seen == [("skill-codex-signer", "fixture-signature")] * 2

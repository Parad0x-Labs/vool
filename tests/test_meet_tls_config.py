from __future__ import annotations

import unittest

from apps.meet_and_greet_server import MeetAndGreetServerConfig, build_server
from core.meet_and_greet_replication import HttpMeetClient


class MeetTlsConfigTests(unittest.TestCase):
    def test_server_tls_requires_cert_and_key_pair(self) -> None:
        with self.assertRaises(ValueError):
            build_server(
                MeetAndGreetServerConfig(
                    host="127.0.0.1",
                    port=0,
                    tls_certfile="/tmp/fake-cert.pem",
                    tls_keyfile=None,
                )
            )

    def test_http_client_builds_tls_context_for_https(self) -> None:
        client = HttpMeetClient(timeout_seconds=1, tls_insecure_skip_verify=True)
        ctx = client._ssl_context_for_url("https://example.test")
        self.assertIsNotNone(ctx)

    def test_http_client_skips_tls_context_for_http(self) -> None:
        client = HttpMeetClient(timeout_seconds=1)
        ctx = client._ssl_context_for_url("http://127.0.0.1:8766")
        self.assertIsNone(ctx)


if __name__ == "__main__":
    unittest.main()

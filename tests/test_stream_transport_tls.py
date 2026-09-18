from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from network.stream_transport import (
    _LEN_STRUCT,
    StreamClientTlsConfig,
    StreamEndpoint,
    StreamServerTlsConfig,
    StreamTransportServer,
    _build_server_tls_context,
    send_frame,
)


class StreamTransportTlsTests(unittest.TestCase):
    def test_server_closes_socket_when_bind_fails(self) -> None:
        sock = MagicMock()
        sock.bind.side_effect = OSError("bind failed")

        with patch("network.stream_transport.socket.socket", return_value=sock):
            with self.assertRaisesRegex(OSError, "bind failed"):
                StreamTransportServer(host="127.0.0.1", port=9001).start()

        sock.close.assert_called_once_with()

    def test_server_tls_requires_cert_and_key(self) -> None:
        with self.assertRaises(ValueError):
            _build_server_tls_context(StreamServerTlsConfig(enabled=True, certfile="/tmp/cert.pem", keyfile=None))

    def test_send_frame_uses_tls_client_context_when_enabled(self) -> None:
        endpoint = StreamEndpoint(host="mesh.local", port=9443)
        tls_cfg = StreamClientTlsConfig(enabled=True, insecure_skip_verify=True)

        raw_sock = MagicMock()
        tls_sock = MagicMock()
        tls_ctx = MagicMock()
        tls_ctx.wrap_socket.return_value = tls_sock

        with patch("network.stream_transport.socket.create_connection") as create_conn, patch(
            "network.stream_transport.ssl.create_default_context", return_value=tls_ctx
        ), patch("network.stream_transport._recv_exact") as recv_exact:
            create_conn.return_value.__enter__.return_value = raw_sock
            recv_exact.side_effect = [_LEN_STRUCT.pack(2), b"ok"]
            out = send_frame(endpoint, b"payload", tls_config=tls_cfg)

        self.assertEqual(out, b"ok")
        tls_ctx.wrap_socket.assert_called_once_with(raw_sock, server_hostname=None)
        tls_sock.sendall.assert_called()

    def test_send_frame_without_tls_skips_tls_wrap(self) -> None:
        endpoint = StreamEndpoint(host="127.0.0.1", port=9001)
        raw_sock = MagicMock()
        with patch("network.stream_transport.socket.create_connection") as create_conn, patch(
            "network.stream_transport.ssl.create_default_context"
        ) as create_ctx, patch("network.stream_transport._recv_exact") as recv_exact:
            create_conn.return_value.__enter__.return_value = raw_sock
            recv_exact.side_effect = [_LEN_STRUCT.pack(2), b"ok"]
            out = send_frame(endpoint, b"payload", tls_config=StreamClientTlsConfig(enabled=False))

        self.assertEqual(out, b"ok")
        create_ctx.assert_not_called()
        raw_sock.sendall.assert_called()

    def test_send_frame_rejects_payload_above_max_frame_limit(self) -> None:
        endpoint = StreamEndpoint(host="127.0.0.1", port=9001)
        payload = b"x" * 2048

        def fake_get(key: str, default=None):
            if key == "network.stream.max_frame_bytes":
                return 1024
            return default

        with patch("network.stream_transport.policy_engine.get", side_effect=fake_get), patch(
            "network.stream_transport.socket.create_connection"
        ) as create_conn:
            out = send_frame(endpoint, payload, tls_config=StreamClientTlsConfig(enabled=False))

        self.assertIsNone(out)
        create_conn.assert_not_called()


if __name__ == "__main__":
    unittest.main()

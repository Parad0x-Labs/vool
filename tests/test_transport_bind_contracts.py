from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from network.transport import UDPTransportServer


class TransportBindContractsTests(unittest.TestCase):
    def test_udp_bind_conflict_raises_helpful_error_when_fallback_bind_also_fails(self) -> None:
        bind_error = OSError(10048, "Only one usage of each socket address")
        bind_error.winerror = 10048
        first_sock = Mock()
        first_sock.bind.side_effect = bind_error
        second_sock = Mock()
        second_sock.bind.side_effect = bind_error

        with patch("network.transport.socket.socket", side_effect=[first_sock, second_sock]), patch(
            "network.transport._kill_stale_udp_holder",
            return_value=False,
        ):
            server = UDPTransportServer(host="127.0.0.1", port=49152)
            with self.assertRaises(OSError) as raised:
                server.start()

        self.assertIn("port already in use", str(raised.exception))
        first_sock.bind.assert_called_once_with(("127.0.0.1", 49152))
        second_sock.bind.assert_called_once_with(("127.0.0.1", 0))
        first_sock.close.assert_called_once()
        second_sock.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()

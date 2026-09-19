from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from network.transport import UDPTransportServer


class TransportBindContractsTests(unittest.TestCase):
    def test_udp_bind_conflict_fails_cleanly_and_never_kills(self) -> None:
        """A held port is REPORTED, this subsystem fails to start, and no unrelated
        process is ever terminated (the historical lsof+SIGKILL recovery is gone)."""
        bind_error = OSError(10048, "Only one usage of each socket address")
        bind_error.winerror = 10048
        first_sock = Mock()
        first_sock.bind.side_effect = bind_error

        with patch("network.transport.socket.socket", return_value=first_sock), patch(
            "network.transport._report_udp_port_conflict",
        ) as report:
            server = UDPTransportServer(host="127.0.0.1", port=49152)
            with self.assertRaises(OSError) as raised:
                server.start()

        self.assertIn("already in use", str(raised.exception))
        first_sock.bind.assert_called_once_with(("127.0.0.1", 49152))
        first_sock.close.assert_called_once()
        report.assert_called_once_with(49152)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import errno
import socket
import threading
import time
import unittest
from unittest.mock import Mock, patch

from network.chunk_protocol import chunk_payload, chunk_to_dict, decode_frame, encode_frame, manifest_to_dict
from network.stream_transport import StreamTransportServer
from network.transport import UDPTransportServer, _configure_udp_socket_buffers, send_message
from storage.migrations import run_migrations


class TransportLargePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()

    @staticmethod
    def _transport_policy(path: str, default=None):
        overrides = {
            "system.max_datagram_bytes": 32768,
            "system.max_fragment_datagram_bytes": 1400,
            "system.max_message_bytes": 262144,
            "system.stream_transfer_threshold_bytes": 24576,
            "system.enable_stream_data_plane": True,
            "system.enable_udp_fragmentation": True,
            "system.max_fragment_buckets": 2048,
            "system.fragment_timeout_seconds": 30.0,
            "system.udp_socket_buffer_bytes": 524288,
            "system.fragment_burst_packets": 1,
            "system.fragment_pause_seconds": 0.002,
            "system.fragment_send_passes": 2,
            "system.require_mesh_encryption": False,
            "system.stream_tls_enabled": False,
            "system.stream_tls_certfile": "",
            "system.stream_tls_keyfile": "",
            "system.stream_tls_ca_file": "",
            "system.stream_tls_require_client_cert": False,
            "system.stream_tls_insecure_skip_verify": False,
            "network.stream_send_retries": 1,
        }
        return overrides.get(path, default)

    def test_fragmented_udp_delivery_for_large_payload(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payload = b"A" * 70000
            with patch("network.transport._stream_enabled", return_value=False), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                ok = send_message("127.0.0.1", runtime.port, payload)
            self.assertTrue(ok)
            self.assertTrue(signal.wait(timeout=5.0))
            self.assertEqual(received[-1], payload)
        finally:
            server.stop()

    def test_fragmented_udp_delivery_survives_small_burst_sequence(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payloads = [b"C" * 70000, b"D" * 71000, b"E" * 72000]
            for payload in payloads:
                with patch("network.transport._stream_enabled", return_value=False), patch(
                    "network.transport.policy_engine.get",
                    side_effect=self._transport_policy,
                ):
                    ok = send_message("127.0.0.1", runtime.port, payload)
                self.assertTrue(ok)
            deadline = time.time() + 6.0
            while len(received) < len(payloads) and time.time() < deadline:
                signal.wait(timeout=0.25)
                signal.clear()
            self.assertEqual(received, payloads)
        finally:
            server.stop()

    def test_stream_delivery_for_large_payload(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payload = b"B" * 90000
            self.assertIsNotNone(runtime.stream_port)
            with patch("network.transport._fragment_enabled", return_value=False), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                ok = send_message("127.0.0.1", runtime.port, payload)
            self.assertTrue(ok)
            self.assertTrue(signal.wait(timeout=6.0))
            self.assertEqual(received[-1], payload)
        finally:
            server.stop()
            time.sleep(0.1)

    def test_ephemeral_port_start_reports_real_udp_and_stream_ports(self) -> None:
        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=lambda *_args: None)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            self.assertGreater(runtime.port, 0)
            self.assertEqual(server.port, runtime.port)
            self.assertEqual(runtime.public_port, runtime.port)
            self.assertIsNotNone(runtime.stream_port)
            self.assertEqual(runtime.stream_port, runtime.port + 1)
        finally:
            server.stop()

    def test_ephemeral_port_retries_until_adjacent_stream_port_is_available(self) -> None:
        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=lambda *_args: None)
        real_start = StreamTransportServer.start
        attempts = 0

        def _start_with_first_pair_occupied(stream_server, *, prebound_socket=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError(errno.EADDRINUSE, "address already in use")
            return real_start(stream_server, prebound_socket=prebound_socket)

        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ), patch.object(StreamTransportServer, "start", _start_with_first_pair_occupied):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            self.assertEqual(attempts, 2)
            self.assertTrue(runtime.running)
            self.assertEqual(runtime.stream_port, runtime.port + 1)
        finally:
            server.stop()

    def test_ephemeral_port_retries_windows_access_denied_pair(self) -> None:
        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=lambda *_args: None)
        real_start = StreamTransportServer.start
        attempts = 0

        def _start_with_first_pair_denied(stream_server, *, prebound_socket=None):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PermissionError(13, "socket access denied")
            return real_start(stream_server, prebound_socket=prebound_socket)

        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ), patch.object(StreamTransportServer, "start", _start_with_first_pair_denied):
                runtime = server.start()

            self.assertEqual(attempts, 2)
            self.assertTrue(runtime.running)
            self.assertEqual(runtime.stream_port, runtime.port + 1)
        finally:
            server.stop()

    def test_ephemeral_port_fails_instead_of_reporting_missing_stream(self) -> None:
        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=lambda *_args: None)
        with patch("network.transport._EPHEMERAL_PAIR_RETRIES", 2), patch.object(
            StreamTransportServer,
            "start",
            side_effect=OSError(errno.EADDRINUSE, "address already in use"),
        ):
            with self.assertRaisesRegex(OSError, "adjacent UDP/stream port pair"):
                server.start()

        self.assertIsNone(server._sock)
        self.assertIsNone(server._thread)

    def test_explicit_port_preserves_fail_soft_stream_start(self) -> None:
        reservation = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        reservation.bind(("127.0.0.1", 0))
        port = int(reservation.getsockname()[1])
        reservation.close()
        server = UDPTransportServer(host="127.0.0.1", port=port, on_message=lambda *_args: None)

        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ), patch.object(StreamTransportServer, "start", side_effect=RuntimeError("stream unavailable")):
                runtime = server.start()
            self.assertTrue(runtime.running)
            self.assertEqual(runtime.port, port)
            self.assertIsNone(runtime.stream_port)
        finally:
            server.stop()

    def test_server_can_restart_after_stop_with_a_live_stream_endpoint(self) -> None:
        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=lambda *_args: None)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                first = server.start()
                server.stop()
                second = server.start()

            self.assertTrue(first.running)
            self.assertTrue(second.running)
            self.assertIsNotNone(second.stream_port)
            self.assertEqual(second.stream_port, second.port + 1)
        finally:
            server.stop()

    def test_a_restart_asks_for_what_the_caller_asked_for_not_last_runs_port(self) -> None:
        """An ephemeral server restarts ephemeral, so it allocates a FRESH adjacent pair.

        The restart test above cannot protect this. It passes on macOS whichever branch runs, because
        macOS tolerates rebinding the adjacent stream port while it is still in TIME_WAIT; Linux
        refuses, the stream server fail-softs, and the restarted transport silently comes back with
        no stream endpoint. So that test was green on every developer machine and red on every CI
        run. This one asserts the ROUTE instead of the symptom and therefore bites on any platform.
        """
        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=lambda *_args: None)
        pair_calls: list[int] = []
        real_pair = server._bind_ephemeral_pair

        def _counting_pair():
            pair_calls.append(1)
            return real_pair()

        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ), patch.object(server, "_bind_ephemeral_pair", _counting_pair):
                server.start()
                served_first = server.port
                server.stop()
                # self.port still reports what was served -- callers read it after stop.
                self.assertEqual(server.port, served_first)
                self.assertGreater(served_first, 0)
                server.start()

            self.assertEqual(
                len(pair_calls),
                2,
                "the restart did not allocate a fresh ephemeral pair; it pinned to last run's port",
            )
        finally:
            server.stop()

    def test_bind_conflict_falls_back_to_ephemeral_udp_port(self) -> None:
        primary = UDPTransportServer(host="127.0.0.1", port=0, on_message=lambda *_args: None)
        secondary: UDPTransportServer | None = None
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                primary_runtime = primary.start()
                secondary = UDPTransportServer(
                    host="127.0.0.1",
                    port=primary_runtime.port,
                    on_message=lambda *_args: None,
                )
                secondary_runtime = secondary.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")
        finally:
            if secondary is not None:
                secondary.stop()
            primary.stop()

        self.assertEqual(primary.port, primary_runtime.port)
        self.assertNotEqual(secondary_runtime.port, primary_runtime.port)
        self.assertGreater(secondary_runtime.port, 0)
        self.assertEqual(secondary.port, secondary_runtime.port)
        self.assertEqual(secondary_runtime.public_port, secondary_runtime.port)

    def test_stream_frame_reassembly_dispatches_completed_payload(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        payload = b"stream-frame-reassembly-test" * 10
        manifest, chunks = chunk_payload("transfer-stream-test", payload, chunk_size=32)

        with patch("network.transport.policy_engine.get", side_effect=self._transport_policy):
            ack = server._on_stream_frame(encode_frame("manifest", manifest_to_dict(manifest)), ("127.0.0.1", 9000))
        self.assertIsNotNone(ack)
        msg_type, _ = decode_frame(ack or b"")
        self.assertEqual(msg_type, "ack")

        with patch("network.transport.policy_engine.get", side_effect=self._transport_policy):
            for chunk in chunks:
                server._on_stream_frame(encode_frame("chunk", chunk_to_dict(chunk)), ("127.0.0.1", 9000))

        self.assertTrue(signal.wait(timeout=1.0))
        self.assertEqual(received[-1], payload)

    def test_udp_socket_buffers_are_raised_for_large_transfers(self) -> None:
        sock = Mock()

        with patch("network.transport.policy_engine.get", side_effect=self._transport_policy):
            _configure_udp_socket_buffers(sock)

        self.assertEqual(sock.setsockopt.call_count, 2)

    def test_fragment_retransmit_passes_do_not_duplicate_delivery(self) -> None:
        received: list[bytes] = []
        signal = threading.Event()

        def _on_message(data: bytes, _addr: tuple[str, int]) -> None:
            received.append(data)
            signal.set()

        server = UDPTransportServer(host="127.0.0.1", port=0, on_message=_on_message)
        try:
            with patch("network.stun_client.discover_public_endpoint", return_value=None), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                runtime = server.start()
        except PermissionError:
            self.skipTest("Local UDP socket binds are not permitted in this sandbox.")

        try:
            payload = b"Z" * 70000
            with patch("network.transport._stream_enabled", return_value=False), patch(
                "network.transport.policy_engine.get",
                side_effect=self._transport_policy,
            ):
                ok = send_message("127.0.0.1", runtime.port, payload)
            self.assertTrue(ok)
            self.assertTrue(signal.wait(timeout=6.0))
            time.sleep(0.2)
            self.assertEqual(received, [payload])
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()

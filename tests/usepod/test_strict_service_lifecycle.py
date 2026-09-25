"""The strict service rig's stop() must actually TERMINATE its server, not merely return.

stop() is deliberately non-blocking (a full CI shard's thread contention was measured
stretching even bounded joins past 600s -- runs 36063857499 shard 1 and 36079948280
shard 8), so THESE tests carry the proof: after stop(), within a bounded poll, the serve
thread is dead, the port no longer accepts, and a fresh server of the rig's own class
rebinds it immediately.
"""
from __future__ import annotations

import json
import socket
import time
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from tests.usepod.strict_usepod_service import StrictUsePodService


def _service() -> StrictUsePodService:
    return StrictUsePodService(tokens={}, models={}).start()


def _port_accepts(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        try:
            probe.connect(("127.0.0.1", port))
        except (ConnectionRefusedError, OSError):
            return False
        return True
    finally:
        probe.close()


def test_stop_terminates_the_server_and_releases_the_port() -> None:
    service = _service()
    port = service.port
    serve_thread = service._thread

    # The server must genuinely be serving before the stop: one real request round-trip.
    with urlopen(f"{service.origin}/v1/marketplace/models", timeout=5) as response:
        assert response.status == 200
        assert isinstance(json.loads(response.read()), dict)

    started = time.monotonic()
    service.stop()
    assert time.monotonic() - started < 2.0, "stop() must not block on thread scheduling"

    # Bounded proof poll: the serve loop observes the shutdown flag on its next poll
    # interval; a port that still accepts or a thread still alive past this bound is a
    # real leak, not a scheduling artifact.
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and (serve_thread.is_alive() or _port_accepts(port)):
        time.sleep(0.1)
    assert not serve_thread.is_alive(), "the serve thread must be dead after stop()"
    assert not _port_accepts(port), f"port {port} still accepts connections after stop()"

    # Release in the rig's own reuse semantics: ThreadingHTTPServer sets allow_reuse_address,
    # so a fresh server of the same class binds the same port immediately.
    class _Noop:
        def process_request(self, *args):  # never used; construction is the assertion
            raise AssertionError("unreachable")

    replacement = ThreadingHTTPServer(("127.0.0.1", port), type(_Noop))
    try:
        assert replacement.server_address[1] == port
    finally:
        replacement.server_close()


def test_the_lifecycle_is_repeatable_back_to_back() -> None:
    first = _service()
    first.stop()

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and first._thread.is_alive():
        time.sleep(0.1)
    assert not first._thread.is_alive()

    second = _service()
    try:
        assert second._thread.is_alive()
        assert second.requests == []
    finally:
        second.stop()
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline and second._thread.is_alive():
        time.sleep(0.1)
    assert not second._thread.is_alive()

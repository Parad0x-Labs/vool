"""The strict service rig's stop() must actually TERMINATE its server, not merely return.

Bounded containment without a postcondition proof is a hidden leak (run 36063857499 shard 1:
teardown sat 26 minutes inside socketserver.shutdown()'s unbounded Event.wait). These tests pin
the postconditions the rig owes every borrower: stop() returns promptly, the serve THREAD is
dead, the port no longer ACCEPTS, and a fresh server of the rig's own class rebinds it at once.
"""
from __future__ import annotations

import json
import time
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

from tests.usepod.strict_usepod_service import StrictUsePodService


def _service() -> StrictUsePodService:
    return StrictUsePodService(tokens={}, models={}).start()


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
    stopped_in = time.monotonic() - started

    assert not serve_thread.is_alive(), "the serve thread must be dead after stop()"
    assert stopped_in < 10.0, f"stop() took {stopped_in:.1f}s; the bound is being ignored"

    # Listener gone: a connect to the port is REFUSED. A bare bind() without SO_REUSEADDR
    # would conflate TIME_WAIT residue from the served request with a live listener, so the
    # refusal is the semantic proof that nothing accepts on this port anymore.
    import socket

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.settimeout(2.0)
    try:
        try:
            probe.connect(("127.0.0.1", port))
        except ConnectionRefusedError:
            pass
        else:
            raise AssertionError(f"port {port} still accepts connections after stop()")
    finally:
        probe.close()

    # Release in the rig's own reuse semantics: ThreadingHTTPServer sets allow_reuse_address,
    # so a fresh server of the same class binds the same port immediately.
    class _Noop:
        def handle_request(self):  # never used; construction is the assertion
            raise AssertionError("unreachable")

    replacement = ThreadingHTTPServer(("127.0.0.1", port), type(_Noop))
    try:
        assert replacement.server_address[1] == port
    finally:
        replacement.server_close()


def test_the_lifecycle_is_repeatable_back_to_back() -> None:
    first = _service()
    first.stop()
    assert not first._thread.is_alive()

    second = _service()
    try:
        assert second._thread.is_alive()
        assert second.requests == []
    finally:
        second.stop()
    assert not second._thread.is_alive()

"""FakeProviderServer's own lifecycle: teardown stays bounded even when readiness vanishes.

CI run 36759227592 (PR104, shard 2) lost the whole job inside the discovery rig's teardown:
the main thread waited in ``socketserver.shutdown()`` while the fake's serve thread sat in
``get_request -> accept()`` that could not return — watchdog exit 124 after 600s. The proven
mechanism, established by direct measurement on this repository's bytes with no client
involved at all: on a BLOCKING listening socket that single accept is unbounded, so once the
loop is inside it with nothing left to accept — through a selector readiness that vanished
before the accept ran — ``shutdown()`` waits forever. WHICH kernel event produced the CI
readiness was not captured (an aborted pending connection and a spurious wakeup are both
plausible) and stays a hypothesis; this file pins the mechanism and the bounded lifecycle,
not the historical trigger.

The frozen contract is kernel-independent: the serve loop's accept step falls through when
there is nothing to accept, an aborted or reset client never holds the teardown hostage,
teardown actually releases the owned serve thread, and the digest-only request evidence keeps
flowing through the real sockets the whole time.
"""
from __future__ import annotations

import http.client
import json
import socket
import struct
import threading
import time

from tests._credential_intelligence_support import ODD_KEY, FakeProviderServer, isolated_home

#: The hard fail-fast bound for every lifecycle step below. The repaired accept budget is 0.5s
#: and serve_forever's own poll cadence is 0.5s, so this is an order of magnitude of headroom —
#: a call that outlives it is the CI-stall shape (a step that can never return), reported as a
#: fast failure instead of a wedged session.
_DEADLINE_S = 5.0


def _run_bounded(label: str, target) -> float:
    """Run ``target()`` under a hard deadline and return the elapsed seconds.

    An exception inside the bounded worker propagates to the caller, chained under its label —
    it must surface as the failure it is, never be mislabeled as the deadline overrun. The
    helper thread is a daemon on purpose: on UNCORRECTED bytes the call never returns, and the
    failure must be the assert below (fast, classified) — not a second wedge waiting for it.
    """
    done = threading.Event()
    failure: list[BaseException] = []

    def _run() -> None:
        try:
            target()
        except BaseException as exc:  # re-raised in the caller below, never swallowed
            failure.append(exc)
        finally:
            done.set()

    started = time.monotonic()
    threading.Thread(target=_run, daemon=True).start()
    assert done.wait(_DEADLINE_S), (
        f"{label} did not return within {_DEADLINE_S}s — the CI-stall shape: "
        "teardown waiting on a serve step that cannot come back"
    )
    if failure:
        raise AssertionError(f"{label} failed inside the bounded worker") from failure[0]
    return time.monotonic() - started


def _abort_pending_connection(host: str, port: int) -> None:
    """Connect and close with SO_LINGER 0: the client sends RST while the connection may still
    be sitting unaccepted in the listen backlog — the readiness that vanishes."""
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    client.connect((host, port))
    client.close()


def test_an_accept_with_nothing_to_accept_falls_through(isolated_home):
    """The frozen regression for run 36759227592: serve_forever's one request step — the exact
    frame the faulthandler captured — must return on its own when the queue is empty, instead of
    parking the serve thread inside an unbounded accept() that shutdown() waits on forever."""
    with FakeProviderServer([(200, {"data": []})]) as server:
        # The loop's own step, driven directly so the blocked-accept state is induced
        # deterministically on every kernel, without depending on any race. (An aborted pending
        # connection was OBSERVED on this macOS host to be handed to accept() — a single-host
        # observation that says nothing about the CI runners' kernels.)
        elapsed = _run_bounded(
            "the serve loop's accept step with nothing to accept",
            lambda: server._server._handle_request_noblock(),
        )
        assert elapsed < 2.0, (
            f"the accept step took {elapsed:.2f}s to fall through; the listener's accept budget "
            "is 0.5s, so anything near this deadline is the stall returning"  # 4x budget headroom
        )


def test_an_aborted_pending_connection_never_holds_teardown(isolated_home):
    """One real keyed request through the real socket (digest evidence recorded), then a client
    aborts while its connection may still be pending: teardown must complete, must release the
    owned serve thread, and the request evidence must already be safely recorded."""
    server = FakeProviderServer(lambda record: (200, {"data": []})).__enter__()
    try:
        conn = http.client.HTTPConnection(server.host, server.port, timeout=5.0)
        conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {ODD_KEY}"})
        assert json.loads(conn.getresponse().read()) == {"data": []}
        conn.close()
        assert server.saw_bearer(ODD_KEY), "the digest evidence for the served request is missing"

        _abort_pending_connection(server.host, server.port)
    finally:
        _run_bounded("FakeProviderServer teardown after an aborted pending connection", server.__exit__)
    assert not server._thread.is_alive(), "teardown must release the owned serve thread"


def test_a_client_that_aborts_after_its_request_is_served_still_records_and_tears_down(isolated_home):
    """A client whose request the handler has ALREADY read and recorded, and which then aborts
    before reading the answer (RST with the response unread), is a distinct post-request error
    lifecycle the fake must survive: the request evidence stands, and nothing about the dead
    client holds the teardown."""
    server = FakeProviderServer([(200, {"ok": True})]).__enter__()
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        client.settimeout(5.0)
        client.connect((server.host, server.port))
        request = (
            f"GET /v1/key HTTP/1.0\r\nHost: {server.host}\r\n"
            f"Authorization: Bearer {ODD_KEY}\r\nContent-Length: 0\r\n\r\n"
        ).encode()
        client.sendall(request)
        # sendall only queued the bytes — it is not server acceptance. The abort below is
        # genuinely POST-request only once the handler has observed and recorded the request.
        deadline = time.monotonic() + _DEADLINE_S
        while server.request_count < 1 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.request_count == 1, "the handler never recorded the request before the abort"
        assert server.saw_bearer(ODD_KEY), "the recorded request is missing its digest evidence"
        client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        client.close()  # RST with the response on its way: the reset client, not a clean FIN
    finally:
        client.close()
        _run_bounded("FakeProviderServer teardown after a client aborted post-request", server.__exit__)
    assert not server._thread.is_alive(), "teardown must release the owned serve thread"

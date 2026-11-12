"""The boot gate: an exact-SHA, clean, capability-verified served app — or no demo.

The studio never films "whatever is running on the port". Boot is refused
unless the staged app answers /healthz with the EXACT staged commit, dirty
false, and a capability manifest containing every row the journey depends on.
These tests drive that gate against a loopback HTTP server speaking the real
healthz/capabilities shapes — the studio's seam, not the product, is under
test here (the product-side gate is ops.served_reality's, already landed).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from ops.demo_studio.boot import StudioBootRefused, verify_served_boot
from ops.demo_studio.wire import DemoWireClient

SHA_OK = "a7b78e2ab523570e2a97c907d0728ea64bcd7bc3"
SHA_OTHER = "0123456789abcdef0123456789abcdef01234567"


def _healthz_payload(*, commit_full: str = SHA_OK, dirty: bool = False) -> dict[str, Any]:
    return {
        "ok": True,
        "agent": "VOOL",
        "daemon": False,
        "runtime": {
            "branch": "build/demo-studio-p1-20260902",
            "commit": commit_full[:12],
            "commit_full": commit_full,
            "dirty": dirty,
            "app_version": "1.0.0",
            "protocol_version": "1",
            "build_id": "demo-studio-test",
        },
        "capabilities": {"capabilities": [{"name": "local_runtime", "state": "implemented"}]},
    }


class _FakeDaemon:
    """Loopback server speaking the real /healthz + /api/runtime/capabilities shapes."""

    def __init__(self, *, healthz: dict[str, Any], capabilities: dict[str, Any] | None = None):
        self.healthz = healthz
        self.capabilities = capabilities or {
            "capabilities": [
                {"name": "local_runtime", "state": "implemented"},
                {"name": "workspace_write_tools", "state": "implemented"},
            ]
        }
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.port = self._server.server_address[1]

    def _handler(self):  # inner class
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # http.server API
                if self.path.startswith("/healthz"):
                    body = json.dumps(outer.healthz).encode("utf-8")
                elif self.path.startswith("/api/runtime/capabilities"):
                    body = json.dumps(outer.capabilities).encode("utf-8")
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a) -> None:  # silence
                return

        return H

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def wire_ok():
    daemon = _FakeDaemon(healthz=_healthz_payload())
    try:
        yield DemoWireClient(f"http://127.0.0.1:{daemon.port}")
    finally:
        daemon.close()


def test_exact_sha_healthz_passes_the_gate(wire_ok: DemoWireClient) -> None:
    report = verify_served_boot(
        wire_ok, expected_sha=SHA_OK, required_capabilities=("local_runtime",)
    )
    assert report.commit_full == SHA_OK
    assert report.dirty is False
    assert report.verified_capabilities == ("local_runtime",)


def test_sha_mismatch_refuses_boot_naming_both_hashes(wire_ok: DemoWireClient) -> None:
    with pytest.raises(StudioBootRefused) as excinfo:
        verify_served_boot(wire_ok, expected_sha=SHA_OTHER, required_capabilities=())
    message = str(excinfo.value)
    # The refusal must come from the FULL-SHA layer specifically: a healthy
    # gate catches the mismatch here first. If this layer is ever blinded
    # (sabotage), a backstop layer would refuse with a different message and
    # this assertion fails — the sabotage is caught, not absorbed silently.
    assert "full-SHA mismatch" in message, (
        f"the full-SHA layer must be the one refusing: {message}"
    )
    assert SHA_OK[:12] in message, "the refusal must name the expected SHA"
    assert SHA_OTHER[:12] in message, (
        "the refusal must name the SHA the daemon actually reported"
    )


def test_dirty_healthz_refuses_boot() -> None:
    daemon = _FakeDaemon(healthz=_healthz_payload(dirty=True))
    try:
        wire = DemoWireClient(f"http://127.0.0.1:{daemon.port}")
        with pytest.raises(StudioBootRefused) as excinfo:
            verify_served_boot(wire, expected_sha=SHA_OK, required_capabilities=())
        assert "dirty" in str(excinfo.value).lower()
    finally:
        daemon.close()


def test_missing_capability_refuses_boot_naming_the_row(wire_ok: DemoWireClient) -> None:
    with pytest.raises(StudioBootRefused) as excinfo:
        verify_served_boot(
            wire_ok,
            expected_sha=SHA_OK,
            required_capabilities=("workspace_write_tools", "never_shipped_capability"),
        )
    assert "never_shipped_capability" in str(excinfo.value)


def test_capabilities_manifest_is_verified_against_its_own_endpoint() -> None:
    # healthz embeds a capabilities snapshot too; the gate must verify the
    # journey's rows against the DEDICATED manifest endpoint, not trust the
    # smaller healthz embed.
    daemon = _FakeDaemon(
        healthz=_healthz_payload(),
        capabilities={"capabilities": [{"name": "workspace_write_tools", "state": "implemented"}]},
    )
    try:
        wire = DemoWireClient(f"http://127.0.0.1:{daemon.port}")
        with pytest.raises(StudioBootRefused) as excinfo:
            verify_served_boot(wire, expected_sha=SHA_OK, required_capabilities=("local_runtime",))
        assert "local_runtime" in str(excinfo.value)
    finally:
        daemon.close()


def test_healthz_that_lies_about_ok_refuses_boot() -> None:
    payload = _healthz_payload()
    payload["ok"] = False
    daemon = _FakeDaemon(healthz=payload)
    try:
        wire = DemoWireClient(f"http://127.0.0.1:{daemon.port}")
        with pytest.raises(StudioBootRefused):
            verify_served_boot(wire, expected_sha=SHA_OK, required_capabilities=())
    finally:
        daemon.close()

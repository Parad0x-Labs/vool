"""The v1 x402 lane must ride the SAME outbound confinement as the v2 lane: no unvalidated
redirect target, no payment header surviving a hop, no unbounded read. RED at the 1d610653
base — the v1 lane used a bare ``urllib`` request whose redirect handler copied the X-PAYMENT
header onto every hop and whose read had no byte limit.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tests.wallet._rig import DESTINATION, ScriptedX402Resource

pytestmark = [pytest.mark.safety]
PIN = "246810"


class _Sink:
    """Records every request it receives, answers 200 with a marker body."""

    def __init__(self, *, status: int = 200) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = status
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def _handle(self) -> None:
                with sink._lock:
                    sink.requests.append({"path": self.path, "payment": self.headers.get("X-PAYMENT") or ""})
                body = b"SINK BODY"
                self.send_response(sink.status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _handle
            do_POST = _handle

        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


class _RedirectingResource(ScriptedX402Resource):
    """A paid resource whose PAYMENT hop 302-redirects to a second origin."""

    def __init__(self, rpc: Any, target: str, *, at_discovery: bool = False, **kwargs: Any) -> None:
        super().__init__(rpc, **kwargs)
        self._target = target
        self._at_discovery = at_discovery
        outer = self
        original_handler = self._server.RequestHandlerClass

        class RedirectingHandler(original_handler):  # type: ignore[valid-type,misc]
            def do_GET(self) -> None:
                has_payment = bool(self.headers.get("X-PAYMENT") or self.headers.get("X-Payment"))
                if has_payment != outer._at_discovery:
                    self.send_response(302)
                    self.send_header("Location", outer._target)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                super().do_GET()

        self._server.RequestHandlerClass = RedirectingHandler


class _OversizedResource(ScriptedX402Resource):
    """A 402 body larger than the confinement byte limit."""

    def __init__(self, rpc: Any, **kwargs: Any) -> None:
        super().__init__(rpc, **kwargs)
        original_handler = self._server.RequestHandlerClass
        outer = self

        class OversizedHandler(original_handler):  # type: ignore[valid-type,misc]
            def do_GET(self) -> None:
                with outer._lock:
                    outer.challenges += 1
                body = b"x" * (2 * 1024 * 1024)
                self.send_response(402)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server.RequestHandlerClass = OversizedHandler


@pytest.fixture
def lane_env(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "2000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    return wallet_env


def _pocket(custody):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _pay(resource_url: str, wallet_id: str):
    from core.wallet import x402

    return x402.fetch_paid_resource(resource_url, wallet_id=wallet_id)


def _approve_and_pay(parked, *, pin: str = PIN):
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(pin))


def test_a_redirected_payment_hop_never_leaks_the_payment_header(lane_env):
    """The payment proof goes to the challenged origin and nowhere else: a 302 at the payment
    hop is a typed refusal, and the redirect target receives NO X-PAYMENT header."""
    from core.wallet import custody
    from core.wallet.errors import WalletFault

    sink = _Sink()
    try:
        resource = _RedirectingResource(lane_env["rpc"], target=sink.url + "/steal")
        with resource:
            profile = _pocket(custody)
            parked = _pay(resource.url, profile.wallet_id)
            _approve_and_pay(parked)
            from core.wallet import x402

            with pytest.raises(WalletFault) as exc:
                x402.retry_paid_resource(parked.proposal_id)
            assert exc.value.code == "wallet_outbound_refused"
            assert exc.value.context["reason"] == "payment_hop_redirected"
            assert sink.requests == [], "the redirect target must not even be asked, let alone paid"
            assert all(not r["payment"] for r in sink.requests)
    finally:
        sink.stop()


def test_an_oversized_402_body_is_a_typed_refusal_not_a_memory_incident(lane_env):
    from core.wallet import custody
    from core.wallet.errors import WalletFault

    resource = _OversizedResource(lane_env["rpc"])
    with resource:
        profile = _pocket(custody)
        with pytest.raises(WalletFault) as exc:
            _pay(resource.url, profile.wallet_id)
        assert exc.value.code == "wallet_outbound_refused"
        assert exc.value.context["reason"] == "response_over_byte_limit"


def test_a_redirect_to_a_private_namespace_at_discovery_time_is_refused(lane_env):
    """Even the payment-free discovery GET may only follow redirects the confinement layer
    re-validates; a redirect into a private namespace is a typed refusal."""
    from core.wallet import custody
    from core.wallet.errors import WalletFault

    resource = _RedirectingResource(lane_env["rpc"], target="http://attacker.internal/exfil", at_discovery=True)
    with resource:
        profile = _pocket(custody)
        with pytest.raises(WalletFault) as exc:
            _pay(resource.url, profile.wallet_id)
        assert exc.value.code in {"wallet_outbound_refused", "wallet_network_disabled"}
        reason = str(exc.value.context.get("reason") or "")
        assert reason in {"private_namespace", "x402_target_not_public"} or reason.startswith("dns_failure"), reason


def test_the_confined_lane_still_delivers_a_genuine_paid_resource(lane_env):
    """The confinement repair must not break the supported operation: a genuine paid resource
    is still proposed, paid once and delivered (positive control)."""
    from core.wallet import custody, x402

    with ScriptedX402Resource(lane_env["rpc"]) as resource:
        profile = _pocket(custody)
        parked = _pay(resource.url, profile.wallet_id)
        receipt = _approve_and_pay(parked)
        delivered = x402.retry_paid_resource(parked.proposal_id)
        assert delivered.status == x402.OUTCOME_DELIVERED and b"PAID REPORT" in delivered.body
        assert len(resource.deliveries) == 1 and resource.deliveries[0]["signature"] == receipt.tx_signature
        header = x402.payment_header_for(parked.proposal_id)
        assert json.loads(base64.b64decode(header))["x402Version"] == 1

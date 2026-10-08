"""A canonical Solana x402 offer is refused by name at every door, never turned into a plain transfer.

A canonical Solana offer names the resource's fee payer: the resource settles a transaction that payer co-signs, so
a plain transfer of this wallet's own would leave the wallet and never be accepted (the money reaches the payee and
nothing is delivered). The ordinary lanes build that plain transfer, so the fetch door, the model's ``x402.propose``
tool and the API's ``x402/propose`` door refuse such an offer (``x402_scheme_unavailable``) before any proposal exists,
on v1 (the offer in the 402's body) and v2 alike. VOOL's own v1 Solana offers (no fee payer) and EVM entries keep
their lanes.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._rig import OTHER_DESTINATION, x402_body

pytestmark = [pytest.mark.safety]
PIN = "246810"
DEVNET_CAIP2 = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
MAINNET_CAIP2 = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
API_HEADERS = {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}


@pytest.fixture
def env(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "2000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    return wallet_env


def _pocket():
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _fee_payer() -> str:
    from solders.keypair import Keypair

    return str(Keypair().pubkey())


def _canonical_v1(network: str) -> dict:
    body = x402_body(amount_minor=1500, network=network)
    body["accepts"][0]["extra"] = {"feePayer": _fee_payer()}
    return body


def _canonical_v1_fee_payer_key(network: str) -> dict:
    """The same offer naming its fee payer the other way the v1 parser reads it: a top-level ``feePayerKey``."""
    body = x402_body(amount_minor=1500, network=network)
    body["accepts"][0]["feePayerKey"] = _fee_payer()
    return body


def _canonical_v2(network: str) -> dict:
    return {"x402Version": 2, "resource": {"url": "https://api.example.test/paid"}, "accepts": [{
        "scheme": "exact", "network": network, "amount": "1500", "asset": "SOL", "payTo": OTHER_DESTINATION,
        "maxTimeoutSeconds": 60, "extra": {"feePayer": _fee_payer()}}]}


class _CanonicalResource:
    """A resource that answers every request with one canonical Solana offer: v1 in the 402's JSON body only, v2 in
    its PAYMENT-REQUIRED header (and body). Records each request and whether it carried payment."""

    def __init__(self, body: dict) -> None:
        self.requests: list[bool] = []
        resource = self
        raw = json.dumps(body).encode()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def do_GET(self):
                resource.requests.append(bool(self.headers.get("X-PAYMENT") or self.headers.get("PAYMENT-SIGNATURE")))
                self.send_response(402)
                if int(body.get("x402Version") or 1) == 2:
                    self.send_header("PAYMENT-REQUIRED", base64.b64encode(raw).decode())
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}/paid"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._server.shutdown()
        self._server.server_close()


@pytest.mark.parametrize(("wire", "network"), [
    ("v1", "solana-devnet"), ("v1", DEVNET_CAIP2), ("v1", "solana"), ("v2", DEVNET_CAIP2), ("v2", MAINNET_CAIP2),
    ("v1_fee_payer_key", "solana-devnet"),
])
def test_a_canonical_offer_at_the_fetch_door_is_refused_before_anything_is_proposed(env, wire, network):
    from core.wallet import proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    body = {"v1": _canonical_v1, "v1_fee_payer_key": _canonical_v1_fee_payer_key, "v2": _canonical_v2}[wire](network)
    with _CanonicalResource(body) as resource:
        with pytest.raises(WalletFault) as exc:
            x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert exc.value.code == "x402_scheme_unavailable"
        assert resource.requests == [False], "the unpaid request that met the 402 is the only one"
    assert proposals.list_proposals() == [], "no proposal exists, so nothing can be approved into a plain transfer"
    assert env["rpc"].send_count() == 0


@pytest.mark.parametrize("offer", ["v1", "v2"])
def test_the_proposal_doors_refuse_a_canonical_offer(env, offer):
    from apps.vool_api_server import create_app
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.wallet import proposals
    from core.web.api.runtime import RuntimeServices

    _pocket()
    body = _canonical_v1("solana-devnet") if offer == "v1" else _canonical_v2(DEVNET_CAIP2)
    result = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": body}, source_context={"session_id": "s1"})
    assert result.handled and not result.ok and result.status == "x402_scheme_unavailable"
    app = create_app(RuntimeServices(display_name="VOOL"))
    _status, _headers, raw = asgi_request(app, method="POST", path="/api/wallet/x402/propose", headers=API_HEADERS,
                                         body=json.dumps({"status": 402, "headers": {}, "body": body}).encode())
    assert json.loads(raw)["error"] == "x402_scheme_unavailable"
    assert proposals.list_proposals() == []


def test_vools_own_v1_solana_offer_without_a_fee_payer_keeps_its_lane(env):
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.wallet import proposals

    _pocket()
    result = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": x402_body(amount_minor=1500)}, source_context={"session_id": "s1"})
    assert result.handled and result.ok
    assert [p.origin for p in proposals.list_proposals()] == [proposals.ORIGIN_X402]


def test_an_evm_entry_beside_a_solana_entry_is_still_selected(env):
    from core.wallet import x402_v2

    evm = {"scheme": "exact", "network": "eip155:84532", "amount": "1500", "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
           "payTo": "0x" + "2" * 40, "maxTimeoutSeconds": 60, "extra": {"name": "USDC", "version": "2", "assetTransferMethod": "eip3009"}}
    both = _canonical_v2(DEVNET_CAIP2)
    both["accepts"].append(evm)
    offer = x402_v2.parse_payment_required({}, json.dumps(both).encode(), status=402)
    picked, reason = x402_v2.select_offer(offer, wallet_id="wallet-any")
    assert picked is not None and picked.network == "eip155:84532" and reason.startswith("selected:eip155:84532")
    only_solana = x402_v2.parse_payment_required({}, json.dumps(_canonical_v2(DEVNET_CAIP2)).encode(), status=402)
    assert x402_v2.select_offer(only_solana, wallet_id="wallet-any") == (None, x402_v2.REFUSED_SOLANA_NEEDS_PAYKIT)

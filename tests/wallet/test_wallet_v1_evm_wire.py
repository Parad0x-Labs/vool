"""The EVM lanes speak BOTH official x402 wire versions on Base Sepolia.

RED at the 6dad8b7a tip: a v1 offer (HTTP 402 body with ``accepts[]``, official
``X-PAYMENT`` EIP-3009 payload) parsed, proposed and prepared fine, then died at
``request_external_signature`` with ``no_x402_v2_binding`` — an installed-but-unreachable
capability for every v1-speaking Base service. The wire version is now a property of the
binding: v2 bindings submit ``PAYMENT-SIGNATURE``, v1 bindings submit the official
``X-PAYMENT`` ``{signature, authorization}`` payload and parse ``X-PAYMENT-RESPONSE``.
"""
from __future__ import annotations

import base64
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tests.wallet._rig_evm import EvmExtensionSigner, FacilitatorSimulator, ScriptedEvmRpc, pad_address_topic

pytestmark = [pytest.mark.safety]

USDC = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
NETWORK = "eip155:84532"
AMOUNT = 1000


class X402V1EvmResource:
    """A paid resource speaking the OFFICIAL v1 wire: a 402 body with accepts[], a paid 200
    carrying X-PAYMENT-RESPONSE. Settlement runs through the facilitator simulator and
    seeds the chain receipt the wallet's independent (payer-bound) verification reads."""

    def __init__(self, facilitator: FacilitatorSimulator, rpc: ScriptedEvmRpc, *, amount_minor: int = AMOUNT, network_name: str = "base-sepolia", extra: dict[str, Any] | None = None, asset: str = USDC) -> None:
        self.facilitator = facilitator
        self.rpc = rpc
        self.amount_minor = amount_minor
        self.network_name = network_name
        self.asset = asset
        self.extra = extra if extra is not None else {"name": "USDC", "version": "2"}
        self.deliveries: list[dict[str, Any]] = []
        self.payment_headers: list[dict[str, Any]] = []
        self.challenges = 0
        self._lock = threading.Lock()
        resource = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                header = self.headers.get("X-PAYMENT") or ""
                if not header:
                    return self._challenge()
                try:
                    payload = json.loads(base64.b64decode(header))
                except Exception:
                    payload = {}
                with resource._lock:
                    resource.payment_headers.append(payload)
                authorization = (payload.get("payload") or {}).get("authorization") or {}
                signature = str((payload.get("payload") or {}).get("signature") or "")
                payer = str(authorization.get("from") or "")
                if not signature or not payer or int(str(authorization.get("value") or 0)) != resource.amount_minor:
                    return self._challenge()
                settled = resource._settle_via_facilitator(payload, signature, payer)
                if not settled:
                    return self._challenge()
                with resource._lock:
                    resource.deliveries.append({"from": payer, "value": int(str(authorization.get("value"))), "nonce": str(authorization.get("nonce"))})
                response = {"success": True, "errorReason": "", "payer": payer, "transaction": resource._last_tx, "network": resource.network_name}
                body = b"PAID V1 EVM REPORT: quarterly numbers"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("X-PAYMENT-RESPONSE", base64.b64encode(json.dumps(response).encode()).decode())
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _challenge(self) -> None:
                with resource._lock:
                    resource.challenges += 1
                accept = {
                    "scheme": "exact", "network": resource.network_name, "maxAmountRequired": str(resource.amount_minor),
                    "asset": resource.asset, "payTo": PAY_TO, "resource": self.path, "description": "one paid report", "maxTimeoutSeconds": 60,
                    "extra": resource.extra,
                }
                body = json.dumps({"x402Version": 1, "error": "X-PAYMENT header is required", "accepts": [accept]}).encode()
                self.send_response(402)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._last_tx = ""
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _settle_via_facilitator(self, payload: dict[str, Any], signature: str, payer: str) -> bool:
        import urllib.request

        requirements = {"scheme": "exact", "network": self.network_name, "maxAmountRequired": str(self.amount_minor), "asset": self.asset, "payTo": PAY_TO, "maxTimeoutSeconds": 60, "extra": self.extra}
        body = json.dumps({"x402Version": 1, "paymentPayload": payload, "paymentRequirements": requirements}).encode()
        try:
            for stage in ("verify", "settle"):
                request = urllib.request.Request(f"{self.facilitator.url.rstrip('/')}/{stage}", data=body, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(request, timeout=10) as response:
                    answer = json.loads(response.read() or b"{}")
                if stage == "verify" and not answer.get("isValid"):
                    return False
                if stage == "settle" and not answer.get("success"):
                    return False
            tx = "0x" + hashlib.sha256(signature.encode()).hexdigest()
        except Exception:
            return False
        self._last_tx = tx
        self.rpc.add_transfer_receipt(tx, contract_address=self.asset, from_address=payer, to_address=PAY_TO, amount_int=self.amount_minor)
        return True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/paid/v1/report"

    def __enter__(self) -> X402V1EvmResource:
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def rig(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from core.wallet import chains, config, facilitators

    with ScriptedEvmRpc() as rpc, FacilitatorSimulator(networks=(NETWORK,)) as facilitator:
        monkeypatch.setenv(config.NETWORK_RPC_URLS_ENV, json.dumps({NETWORK: rpc.url}))
        chains.invalidate_chain_identity(None)
        facilitators.discover(facilitator.url, facilitator_id="fac-v1wire")
        yield rpc, facilitator


def test_a_v1_base_sepolia_offer_rides_the_official_v1_wire_end_to_end(rig):
    from core.wallet import custody, lifecycle, proposals, x402

    rpc, facilitator = rig
    with X402V1EvmResource(facilitator, rpc) as resource, EvmExtensionSigner() as signer:
        profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
        outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert outcome.status == x402.OUTCOME_PAYMENT_REQUIRED and outcome.proposal_id
        proposal = proposals.get_proposal(outcome.proposal_id)
        assert proposal.asset == "USDC" and proposal.amount_minor == AMOUNT

        engine = lifecycle.default_lifecycle()
        engine.prepare(proposal.proposal_id)
        view = engine.request_external_signature(proposal.proposal_id)  # RED today: no_x402_v2_binding
        typed = json.loads(view["transports"]["eip1193"]["params"][1])
        signature = _sign(signer, typed)

        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert receipt.state == proposals.STATE_CONFIRMED, f"settlement must be chain-proven: {receipt.to_dict()}"

        # the wire the server saw is the OFFICIAL v1 shape
        assert resource.payment_headers and resource.payment_headers[0]["x402Version"] == 1
        seen = resource.payment_headers[0]["payload"]
        assert seen["signature"] == signature
        authorization = seen["authorization"]
        assert authorization["from"].lower() == signer.address.lower() and authorization["to"].lower() == PAY_TO.lower()
        assert int(authorization["value"]) == AMOUNT and authorization["nonce"].startswith("0x")
        assert resource.deliveries and resource.deliveries[0]["from"].lower() == signer.address.lower()
        assert rpc.methods().count("eth_getTransactionReceipt") >= 1, "settlement was proven from the chain, not the header"


def test_a_v1_offer_without_a_pinned_domain_on_an_unpinned_asset_refuses(rig):
    """Ethereum Sepolia's USDC is deliberately unpinned in the registry: a v1 offer that
    carries no extra.name/version is a typed refusal (never a guessed domain)."""
    from core.wallet import custody, x402
    from core.wallet.errors import WalletFault

    rpc, facilitator = rig
    from core.wallet import config as wallet_config

    with X402V1EvmResource(facilitator, rpc, network_name="ethereum-sepolia", extra={}, asset="0x1c7d4b196cb0c7b01d743fbc6116a902379c7238") as resource, EvmExtensionSigner() as signer:
        import os

        os.environ[wallet_config.NETWORK_RPC_URLS_ENV] = json.dumps({"eip155:84532": rpc.url, "eip155:11155111": rpc.url})
        rpc.chain_id = 11155111
        from core.wallet import chains, facilitators

        chains.invalidate_chain_identity(None)
        with FacilitatorSimulator(networks=("eip155:11155111",)) as sepolia_facilitator:
            facilitators.discover(sepolia_facilitator.url, facilitator_id="fac-v1wire-sepolia")
        profile = custody.register_external_signer_wallet(signer.address, network="eip155:11155111")
        outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert outcome.status == x402.OUTCOME_PAYMENT_REQUIRED
        from core.wallet import lifecycle

        engine = lifecycle.default_lifecycle()
        prepared = engine.prepare(outcome.proposal_id)
        with pytest.raises(WalletFault) as exc:
            engine.request_external_signature(prepared.proposal_id)
        assert exc.value.code == "x402_scheme_unavailable" and exc.value.context["reason"] == "eip712_domain_unpinned"
        assert resource.deliveries == []


def _sign(signer: EvmExtensionSigner, typed: dict[str, Any]) -> str:
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(signer.url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        connection.request("POST", parts.path, body=json.dumps({"typed_data": typed}).encode(), headers={"Content-Type": "application/json"})
        answer = json.loads(connection.getresponse().read())
    finally:
        connection.close()
    assert "signature" in answer, answer
    return answer["signature"]

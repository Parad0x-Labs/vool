"""Stage E — x402 v2 and outbound safety (RED corpus).

The official v2 wire (PAYMENT-REQUIRED / PAYMENT-SIGNATURE / PAYMENT-RESPONSE), one typed
parser, deterministic selection, SSRF/DNS-rebinding/redirect/header-exfiltration attacks
refused, no payment before approval, exactly one approved submission and at-most-one
resource retry, UNKNOWN blocking blind retry, restart and rewind never repaying, and the
response bound to the payment by digest.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.wallet.errors import WalletFault

BASE_SEPOLIA = "eip155:84532"
ETHEREUM_SEPOLIA = "eip155:11155111"
BSC_TESTNET = "eip155:97"
USDC_BASE = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_SEPOLIA = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"

pytestmark = [pytest.mark.safety]


@pytest.fixture
def wallet_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "20000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield
    store_module.reset_default_store()


# --- E1: the typed v2 parser and deterministic selection ---------------------------------------------


def _official_body(*, network: str = BASE_SEPOLIA, asset: str = USDC_BASE, amount: str = "10000", accepts=None) -> dict:
    return {
        "x402Version": 2,
        "error": "PAYMENT-SIGNATURE header is required",
        "resource": {"url": "https://api.example.test/premium-data", "description": "Access to premium market data", "mimeType": "application/json"},
        "accepts": accepts if accepts is not None else [
            {"scheme": "exact", "network": network, "amount": amount, "asset": asset, "payTo": PAY_TO, "maxTimeoutSeconds": 60, "extra": {"name": "USDC", "version": "2", "assetTransferMethod": "eip3009"}},
        ],
        "extensions": {},
    }


def test_e1a_v2_offer_from_header_and_body():
    from core.wallet import x402_v2

    body = _official_body()
    header = base64.b64encode(json.dumps(body).encode()).decode()
    parsed = x402_v2.parse_payment_required({x402_v2.HEADER_PAYMENT_REQUIRED: header}, status=402)
    assert parsed is not None and parsed.accepts[0].network == BASE_SEPOLIA
    assert parsed.accepts[0].amount_minor == 10000
    assert parsed.accepts[0].eip712_name == "USDC"
    from_body = x402_v2.parse_payment_required({}, body, status=402)
    assert from_body is not None and from_body.resource_url == "https://api.example.test/premium-data"
    assert x402_v2.parse_payment_required({}, body, status=200) is None  # not a 402
    assert x402_v2.parse_payment_required({}, {**body, "x402Version": 1}, status=402) is None  # v1 is not v2
    assert x402_v2.parse_payment_required({}, {"x402Version": 2, "accepts": []}, status=402) is None
    assert x402_v2.parse_payment_required({}, {"x402Version": 2, "accepts": [{"scheme": "exact"}]}, status=402) is None


def test_e1b_multiple_and_conflicting_offers_are_selected_deterministically(wallet_env):
    from core.wallet import x402_v2

    accepts = [
        {"scheme": "exact", "network": "eip155:1", "amount": "1", "asset": USDC_BASE, "payTo": PAY_TO},  # mainnet: never
        {"scheme": "exact", "network": BASE_SEPOLIA, "amount": "1", "asset": USDC_BASE, "payTo": PAY_TO, "extra": {"assetTransferMethod": "permit2", "name": "USDC", "version": "2"}},  # permit2: unavailable
        {"scheme": "upto", "network": BASE_SEPOLIA, "amount": "1", "asset": USDC_BASE, "payTo": PAY_TO},  # unknown scheme
        {"scheme": "exact", "network": BASE_SEPOLIA, "amount": "999999", "asset": USDC_BASE, "payTo": PAY_TO},  # above cap
        {"scheme": "exact", "network": BASE_SEPOLIA, "amount": "7000", "asset": USDC_SEPOLIA, "payTo": PAY_TO},  # another chain's token
        {"scheme": "exact", "network": BASE_SEPOLIA, "amount": "7000", "asset": USDC_BASE, "payTo": PAY_TO, "extra": {"name": "USDC", "version": "2", "assetTransferMethod": "eip3009"}},
    ]
    offer = x402_v2.parse_payment_required({}, _official_body(accepts=accepts), status=402)
    picked, reason = x402_v2.select_offer(offer, wallet_id="wallet-any")
    assert picked is offer.accepts[5] and picked.amount_minor == 7000, reason
    again, _ = x402_v2.select_offer(offer, wallet_id="wallet-any")
    assert again is picked  # deterministic
    only_bad = x402_v2.parse_payment_required({}, _official_body(accepts=accepts[:5]), status=402)
    none, refusal = x402_v2.select_offer(only_bad, wallet_id="wallet-any")
    assert none is None and refusal.startswith("refused:")
    # a v2-shaped challenge that cannot be parsed never falls through to the v1 lane
    from core.wallet import x402 as wallet_x402

    assert wallet_x402._looks_like_v2({}, {"x402Version": 2, "accepts": [{"scheme": "exact"}]}) is True
    assert wallet_x402._looks_like_v2({}, {"x402Version": 1, "accepts": [{}]}) is False


# --- E2: outbound confinement attacks ----------------------------------------------------------------


class _EchoServer:
    """Records every request header it receives; answers 200 or a redirect."""

    def __init__(self, *, location: str = ""):
        self.seen_headers: list[dict[str, str]] = []
        self.location = location
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a):
                return

            def do_GET(self):
                outer.seen_headers.append({k.lower(): v for k, v in self.headers.items()})
                if outer.location:
                    self.send_response(302)
                    self.send_header("Location", outer.location)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = b"echo-ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_address[1]}/x"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_a):
        self._server.shutdown()
        self._server.server_close()


def test_e2a_ssrf_and_dns_rebinding_targets_refuse(wallet_env):
    from core.wallet import outbound

    with pytest.raises(WalletFault) as link_local:
        outbound.validate_target("http://169.254.169.254/latest/meta-data")
    assert link_local.value.code == "wallet_outbound_refused"
    with pytest.raises(WalletFault) as private:
        outbound.validate_target("https://10.1.2.3/pay")
    assert private.value.code == "wallet_outbound_refused"
    with pytest.raises(WalletFault) as namespace:
        outbound.validate_target("https://db.internal/secret")
    assert namespace.value.code == "wallet_outbound_refused"
    with pytest.raises(WalletFault) as insecure:
        outbound.validate_target("http://example.test/pay")
    assert insecure.value.code in {"wallet_outbound_refused", "wallet_network_disabled"}
    import core.wallet.outbound as outbound_module

    original = outbound_module.resolve_host_addresses
    try:
        outbound_module.resolve_host_addresses = lambda host: ["192.168.1.50"]  # a DNS rebinding answer
        with pytest.raises(WalletFault) as rebinding:
            outbound_module.validate_target("https://rebind.example.test/pay")
        assert rebinding.value.code == "wallet_outbound_refused"
    finally:
        outbound_module.resolve_host_addresses = original


def test_e2b_redirects_cannot_change_origin_or_carry_payment_headers(wallet_env):
    from core.wallet import outbound

    with _EchoServer() as evil, _EchoServer(location="REDIRECT") as hop:
        evil_url = evil.url.replace("/x", "/steal")
        hop.location = evil_url
        answer = outbound.fetch(
            hop.url, method="GET", headers={"Accept": "*/*"},
            payment_headers={x402_header_name(): "base64payload"}, payment_origin=outbound_origin(hop.url),
            timeout=10.0,
        )
        assert answer["status"] == 200
        assert answer["url"] == evil_url  # the hop was followed (loopback allowed)
        assert len(evil.seen_headers) == 1
        assert x402_header_name() not in evil.seen_headers[0]  # the payment header did NOT travel
        assert "x-payment" not in " ".join(evil.seen_headers[0].keys())


def x402_header_name() -> str:
    from core.wallet import x402_v2

    return x402_v2.HEADER_PAYMENT_SIGNATURE


def outbound_origin(url: str) -> str:
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def test_e2c_payment_headers_only_to_the_approved_origin(wallet_env):
    from core.wallet import outbound

    with _EchoServer() as approved, _EchoServer() as other:
        answer = outbound.fetch(
            other.url, method="GET",
            payment_headers={x402_header_name(): "base64payload"}, payment_origin=outbound_origin(approved.url),
            timeout=10.0,
        )
        assert answer["status"] == 200
        assert x402_header_name() not in other.seen_headers[0]


def test_e2d_responses_are_byte_bounded(wallet_env):
    from core.wallet import outbound

    with _EchoServer() as echo:
        with pytest.raises(WalletFault) as capped:
            outbound.fetch(echo.url, byte_limit=4, timeout=10.0)
        assert capped.value.code == "wallet_outbound_refused"


# --- E3/E4: the full v2 lane on two EVM chains, distinct identity, no double payment ------------------


def _wire_chain(monkeypatch, evm_rig, network: str, asset: str, *, chain_id: int):
    """Point the chain's approved RPC origin at the scripted endpoint, retarget its chain
    id, and stand up a facilitator that ACTUALLY declares this network."""
    import contextlib

    from core.wallet import config as wallet_config

    monkeypatch.setenv(wallet_config.NETWORK_RPC_URLS_ENV, json.dumps({network: evm_rig.rpc.url}))
    monkeypatch.setattr(evm_rig.rpc, "chain_id", chain_id, raising=False)
    from core.wallet import chains

    chains.invalidate_chain_identity(None)
    from tests.wallet._rig_evm import FacilitatorSimulator

    _stack = contextlib.ExitStack()
    facilitator = _stack.enter_context(FacilitatorSimulator(networks=(network,)))
    monkeypatch.callback(lambda: _stack.close()) if hasattr(monkeypatch, "callback") else None
    from core.wallet import facilitators

    facilitators.discover(facilitator.url, facilitator_id=f"fac-{chain_id}")
    _stack.close()  # discovery done; the simulator itself can stop now
    return evm_rig


def signer_sign(signer, typed_data: dict) -> str:
    """What the page does: hand the typed data to the wallet extension's own process.
    A raw http.client call — the extension is its own process, not a fetch through the
    runtime's door."""
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(signer.url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        connection.request("POST", parts.path, body=json.dumps({"typed_data": typed_data}).encode(), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        answer = json.loads(response.read())
    finally:
        connection.close()
    if "signature" not in answer:
        raise AssertionError(f"signer refused: {answer}")
    return answer["signature"]


def _full_v2_journey(monkeypatch, evm_rig, signer, *, network: str, asset: str, chain_id: int, amount: int = 10000):
    """Fetch -> park -> sign -> ONE submission -> chain-proven settlement -> receipt."""
    from core.wallet import custody, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import X402V2Resource

    _wire_chain(monkeypatch, evm_rig, network, asset, chain_id=chain_id)
    with X402V2Resource(evm_rig.facilitator, network=network, asset=asset, pay_to=PAY_TO, amount_minor=amount, eip712_name="USDC", eip712_version="2") as resource:

        settlement_tx = "0x" + f"{chain_id:064x}"[:64]
        resource.settlement_tx = settlement_tx
        evm_rig.rpc.add_transfer_receipt(settlement_tx, contract_address=asset, from_address=signer.address, to_address=PAY_TO, amount_int=amount)
        profile = custody.register_external_signer_wallet(signer.address, network=network)
        outcome = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert outcome.status == "payment_required" and outcome.proposal_id
        proposal = proposals.get_proposal(outcome.proposal_id)
        assert proposal.state == proposals.STATE_PENDING_APPROVAL and proposal.origin == "x402"
        assert evm_rig.facilitator.verify_count() == 0 and resource.challenges == 1

        # nothing paid before approval: a retry call refuses without touching the resource
        with pytest.raises(WalletFault) as early:
            wallet_x402.retry_paid_resource(proposal.proposal_id)
        assert early.value.code == "wallet_duplicate_payment"
        assert resource.deliveries == []

        engine = wallet_x402.lifecycle_default_engine()
        view = engine.request_external_signature(proposal.proposal_id)
        transport = view["transports"]["eip1193"]
        assert transport["method"] == "eth_signTypedData_v4"
        typed_data = json.loads(transport["params"][1])
        assert typed_data["domain"]["chainId"] == chain_id

        signature = signer_sign(signer, typed_data)
        resource.settled_signatures.add(signature)
        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert receipt.state == "confirmed"
        assert receipt.tx_signature == settlement_tx
        from core.wallet import receipts

        stored = [r for r in receipts.list_receipts() if r["proposal_id"] == proposal.proposal_id][-1]
        assert stored["x402_v2"]["settlement"] == "settled"
        assert stored["x402_v2"]["resource_bytes"] > 0
        # the minted resource digest round-trips the redaction boundary unchanged: the wallet
        # vouches for it at delivery, so the stored receipt still proves what was delivered
        import hashlib

        assert stored["x402_v2"]["resource_digest"] == hashlib.sha256(b"PAID V2 REPORT: quarterly numbers").hexdigest()
        return proposal, receipt, resource, engine


def test_e3_full_v2_journey_on_base_sepolia(wallet_env, evm_rig, monkeypatch):
    from tests.wallet._rig_evm import EvmExtensionSigner

    with EvmExtensionSigner() as signer:
        proposal, _receipt, resource, engine = _full_v2_journey(monkeypatch, evm_rig, signer, network=BASE_SEPOLIA, asset=USDC_BASE, chain_id=84532)
    # exactly one delivery of the exact payment
    assert len(resource.deliveries) == 1
    # a second submission is a duplicate: the signing request was consumed
    with pytest.raises(WalletFault) as twice:
        engine.submit_external_signature("another-request", signature_hex="0x" + "1" * 130)
    assert twice.value.code == "wallet_not_found"
    # a refetch never repays: the v2 lane refuses a second delivery for the same binding
    from core.wallet import x402 as wallet_x402

    with pytest.raises(WalletFault) as refetch:
        wallet_x402.retry_paid_resource(proposal.proposal_id)
    assert refetch.value.code == "wallet_duplicate_payment"


def test_e4_ethereum_sepolia_traverses_with_distinct_identity(wallet_env, evm_rig, monkeypatch):
    from tests.wallet._rig_evm import EvmExtensionSigner

    with EvmExtensionSigner() as signer:
        _proposal, receipt, resource, _engine = _full_v2_journey(monkeypatch, evm_rig, signer, network=ETHEREUM_SEPOLIA, asset=USDC_SEPOLIA, chain_id=11155111)
        assert receipt.tx_signature.startswith("0x")
        assert len(resource.deliveries) == 1
    # the chain identity served for THIS chain only
    from core.wallet import chains

    assert chains.resolve_network(ETHEREUM_SEPOLIA).chain_id == "11155111"


def test_e4b_bnb_testnet_identity_is_distinct_and_its_unavailable_token_refuses(wallet_env, evm_rig, monkeypatch):
    from core.wallet import custody, lifecycle, proposals

    _wire_chain(monkeypatch, evm_rig, BSC_TESTNET, "BNB", chain_id=97)
    profile = custody.register_external_signer_wallet("0x" + "d" * 40, network=BSC_TESTNET)
    engine = lifecycle.default_lifecycle()
    proposal = proposals.propose_transaction(
        wallet_id=profile.wallet_id, destination="0x" + "e" * 40, amount_minor=1_000_000_000_000_000_000,
        asset="BNB", origin="user", network=BSC_TESTNET,
    )
    with pytest.raises(WalletFault) as refused:
        engine.prepare(proposal.proposal_id)
    # the identity read DID hit the BNB endpoint (chain 97), then the native asset refused
    assert evm_rig.rpc.chain_id == 97 and "eth_chainId" in evm_rig.rpc.methods()
    assert refused.value.code == "wallet_simulation_failed"
    # a v2 offer with a non-registered token is never admissible
    from core.wallet import x402_v2

    bogus = x402_v2.V2Requirements(
        scheme="exact", network=BSC_TESTNET, amount_minor=1000, asset="0x" + "5" * 40, pay_to=PAY_TO,
        resource_url="https://paid.example.test/bnb", eip712_name="X", eip712_version="1",
    )
    picked, reason = x402_v2.select_offer(x402_v2.V2Offer(resource_url=bogus.resource_url, resource_description="", accepts=(bogus,)), wallet_id=profile.wallet_id)
    assert picked is None and reason.startswith("refused:")


def test_e5_unknown_settlement_blocks_blind_retry_and_restart_never_repays(wallet_env, evm_rig, monkeypatch):
    """A submission whose settlement never proves stays honestly unconfirmed, the one
    signing request is consumed (no blind retry), and nothing is served twice."""
    import contextlib

    from tests.wallet._rig_evm import EvmExtensionSigner, FacilitatorSimulator, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    _stack = contextlib.ExitStack()
    failing = _stack.enter_context(FacilitatorSimulator(networks=(BASE_SEPOLIA,), settle_ok=False))
    with EvmExtensionSigner() as signer, X402V2Resource(failing, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2") as resource:
        from core.wallet import custody
        from core.wallet import x402 as wallet_x402

        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        outcome = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        engine = wallet_x402.lifecycle_default_engine()
        view = engine.request_external_signature(outcome.proposal_id)
        typed_data = json.loads(view["transports"]["eip1193"]["params"][1])
        signature = signer_sign(signer, typed_data)
        # the resource cannot settle this payment: the challenge comes back (402), the
        # submission stays UNPROVEN and the proposal does NOT confirm
        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert receipt.state in {receipts_broadcast := "broadcast", "failed"}
        assert receipt.state == receipts_broadcast
        # exactly one submission: the signing request is consumed, a replay is typed-dup
        with pytest.raises(WalletFault) as again:
            engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert again.value.code == "wallet_duplicate_payment"
        # the resource served nothing
        assert resource.deliveries == []
        # no confirmed receipt exists for this proposal
        from core.wallet import receipts

        assert all(r["state"] != "confirmed" for r in receipts.list_receipts() if r["proposal_id"] == outcome.proposal_id)


def test_e5b_database_rewind_cannot_repay_a_broadcast_payment(wallet_env, evm_rig, monkeypatch):
    """The signature fence: a rewound state column cannot re-enter the signing or claim
    doors once broadcast evidence exists."""
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2") as resource:
        from core.wallet import custody
        from core.wallet import x402 as wallet_x402

        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        outcome = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        engine = wallet_x402.lifecycle_default_engine()
        view = engine.request_external_signature(outcome.proposal_id)
        typed_data = json.loads(view["transports"]["eip1193"]["params"][1])
        signature = signer_sign(signer, typed_data)
        resource.settled_signatures.add(signature)
        settlement_tx = "0x" + ("ab" * 32)
        resource.settlement_tx = settlement_tx
        evm_rig.rpc.add_transfer_receipt(settlement_tx, contract_address=USDC_BASE, from_address=signer.address, to_address=PAY_TO, amount_int=10000)
        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert receipt.state == "confirmed"
        # rewind the state column to pending_approval behind the engine's back
        from core.wallet.store import connection

        with connection() as conn:
            conn.execute("UPDATE wallet_proposals SET state = 'pending_approval' WHERE proposal_id = ?", (outcome.proposal_id,))
        # the door sees the recorded signature and refuses the re-request before any signer
        with pytest.raises(WalletFault) as replayed:
            engine.request_external_signature(outcome.proposal_id)
        assert replayed.value.code == "wallet_duplicate_payment"
        assert len(resource.deliveries) == 1  # exactly one delivery ever


def test_e6_facilitator_discovery_is_typed_and_network_scoped(wallet_env, evm_rig):
    from core.wallet import facilitators
    from tests.wallet._rig_evm import FacilitatorSimulator

    with FacilitatorSimulator(supported_shapes_ok=True) as broken:
        with pytest.raises(WalletFault) as malformed:
            facilitators.discover(broken.url, facilitator_id="fac-broken")
        assert malformed.value.code == "x402_scheme_unavailable"
    assert malformed.value.code == "x402_scheme_unavailable"
    with pytest.raises(WalletFault) as missing:
        facilitators.require_capability("eip155:11155111", "exact")
    assert missing.value.code == "x402_scheme_unavailable"

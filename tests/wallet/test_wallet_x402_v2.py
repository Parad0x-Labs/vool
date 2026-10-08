"""Stage E — x402 v2 and outbound safety (RED corpus).

The official v2 wire (PAYMENT-REQUIRED / PAYMENT-SIGNATURE / PAYMENT-RESPONSE), one typed
parser, deterministic selection, SSRF/DNS-rebinding/redirect/header-exfiltration attacks
refused, no payment before approval, exactly one approved submission and at-most-one
resource retry, UNKNOWN blocking blind retry, restart and rewind never repaying, and the
response bound to the payment by digest.
"""
from __future__ import annotations

import base64
import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core.wallet.errors import WalletFault
from tests.wallet._rig_evm import ANSWERS_AFTER_PAYMENT

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


# --- E5c-E5e: what a paid request's answer can release, and what can be paid again ------------------



def _parked_and_signed(signer, resource):
    """Park one v2 payment for ``resource`` and sign it: (wallet_id, proposal_id, engine, request_id, signature)."""
    from core.wallet import custody
    from core.wallet import x402 as wallet_x402

    profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
    outcome = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    engine = wallet_x402.lifecycle_default_engine()
    view = engine.request_external_signature(outcome.proposal_id)
    signature = signer_sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
    return profile.wallet_id, outcome.proposal_id, engine, view["request_id"], signature


@contextlib.contextmanager
def _settled_but_undelivered(monkeypatch, evm_rig, answer: str):
    """Submit one v2 payment that the resource settles and then answers ``answer`` instead of delivering; yields
    (wallet_id, proposal_id, the submission's fault, the resource) with the resource still serving."""
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", paid_answer=answer) as resource:
        wallet_id, proposal_id, engine, request_id, signature = _parked_and_signed(signer, resource)
        resource.settled_signatures.add(signature)
        with pytest.raises(WalletFault) as submitted:
            engine.submit_external_signature(request_id, signature_hex=signature)
        assert resource.unanswered == [signature]  # the payment reached the resource, which settled it
        yield wallet_id, proposal_id, submitted.value, resource


@pytest.mark.parametrize("answer", ANSWERS_AFTER_PAYMENT)
def test_e5c_a_payment_that_left_keeps_its_hold_whatever_the_resource_answers(wallet_env, evm_rig, monkeypatch, answer):
    """Once the payment header has left this machine the resource may have settled it, whatever it answers. An answer
    that delivers nothing leaves the payment UNKNOWN: the hold stays and the proposal is not failed."""
    from core.wallet import limits, proposals, receipts

    with _settled_but_undelivered(monkeypatch, evm_rig, answer) as (_wallet_id, proposal_id, fault, _resource):
        assert fault.code == "wallet_broadcast_failed"
        assert str(fault.context.get("reason") or "").startswith("submit_unknown:")
        assert proposals.get_proposal(proposal_id).state == proposals.STATE_BROADCAST
        assert limits.reservation_state(proposal_id) == limits.RESERVATION_RESERVED
        assert [r["state"] for r in receipts.list_receipts() if r["proposal_id"] == proposal_id] == [proposals.STATE_BROADCAST]


@pytest.mark.parametrize("answer", ANSWERS_AFTER_PAYMENT)
def test_e5d_a_request_whose_payment_may_have_settled_is_never_paid_again(wallet_env, evm_rig, monkeypatch, answer):
    """While a payment's outcome is unknown, fetching the same request again refuses before anything is sent: it
    neither parks a second payment nor rebinds the request to one."""
    from core.wallet import x402 as wallet_x402

    with _settled_but_undelivered(monkeypatch, evm_rig, answer) as (wallet_id, proposal_id, _fault, resource):
        with pytest.raises(WalletFault) as again:
            wallet_x402.fetch_paid_resource(resource.url, wallet_id=wallet_id)
        assert again.value.code == "wallet_duplicate_payment"
        assert resource.challenges == 1  # the first fetch's challenge is the only request without a payment
        assert (wallet_x402.binding_for_proposal(proposal_id) or {}).get("proposal_id") == proposal_id


def test_e5d2_new_terms_for_the_same_request_do_not_park_a_second_payment(wallet_env, evm_rig, monkeypatch):
    """The proposal key covers the offer's terms, so a resource that asks again on other terms (here another price)
    would get a fresh proposal and the request rebound to it, one approval away from a second payment. While the first
    payment's outcome is unknown the request is refused before it is sent, whatever the resource would ask."""
    from core.wallet import x402 as wallet_x402

    with _settled_but_undelivered(monkeypatch, evm_rig, "drop") as (wallet_id, proposal_id, _fault, resource):
        resource.amount_minor = 12000
        with pytest.raises(WalletFault) as again:
            wallet_x402.fetch_paid_resource(resource.url, wallet_id=wallet_id)
        assert again.value.code == "wallet_duplicate_payment"
        assert resource.challenges == 1
        assert (wallet_x402.binding_for_proposal(proposal_id) or {}).get("proposal_id") == proposal_id


@pytest.mark.parametrize("outcome", ["unknown", "confirmed"])
def test_e5d3_a_late_challenge_cannot_rebind_a_request_its_payment_holds(wallet_env, evm_rig, monkeypatch, outcome):
    """Two callers fetch the same request; the second passed its own check before the first one's payment was parked,
    and its 402, on other terms, arrives after that payment left (outcome unknown) or settled. The binding refuses
    the second proposal in the same statement that would rebind the request: no second payment is parked, and the
    proposal minted for the late challenge is rejected, never approvable."""
    from core.wallet import proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    paid_answer = "drop" if outcome == "unknown" else ""
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", paid_answer=paid_answer) as resource:
        wallet_id, proposal_id, engine, request_id, signature = _parked_and_signed(signer, resource)
        resource.settled_signatures.add(signature)
        if outcome == "confirmed":
            resource.settlement_tx = "0x" + ("ab" * 32)
            evm_rig.rpc.add_transfer_receipt(resource.settlement_tx, contract_address=USDC_BASE, from_address=signer.address, to_address=PAY_TO, amount_int=10000)
            assert engine.submit_external_signature(request_id, signature_hex=signature).state == proposals.STATE_CONFIRMED
        else:
            with pytest.raises(WalletFault):
                engine.submit_external_signature(request_id, signature_hex=signature)
        resource.amount_minor = 12000
        real = wallet_x402._bound_outcome
        calls = []

        def stale_once(binding, **kwargs):
            calls.append(binding)
            return None if len(calls) == 1 else real(binding, **kwargs)

        monkeypatch.setattr(wallet_x402, "_bound_outcome", stale_once)
        with pytest.raises(WalletFault) as late:
            wallet_x402.fetch_paid_resource(resource.url, wallet_id=wallet_id)
        assert late.value.code == "wallet_duplicate_payment"
        assert resource.challenges == 2, "the late caller's request did go out: its first check was stale"
        assert (wallet_x402.binding_for_proposal(proposal_id) or {}).get("proposal_id") == proposal_id
        others = [p for p in proposals.list_proposals() if p.proposal_id != proposal_id]
        assert [(p.state, p.amount_minor) for p in others] == [(proposals.STATE_REJECTED, 12000)]


def test_e5d4_a_payment_whose_state_says_failed_while_its_spend_is_held_keeps_its_request_closed(wallet_env, evm_rig, monkeypatch):
    """The dispatch record decides, not the state column or a transaction id: a payment that left with no proof still
    holds its spend, so its request stays closed even when its proposal row says failed (a rewound or corrupted row)."""
    from core.wallet import limits
    from core.wallet import x402 as wallet_x402
    from core.wallet.store import connection

    with _settled_but_undelivered(monkeypatch, evm_rig, "drop") as (wallet_id, proposal_id, _fault, resource):
        with connection() as conn:
            conn.execute("UPDATE wallet_proposals SET state = 'failed' WHERE proposal_id = ?", (proposal_id,))
        assert limits.reservation_state(proposal_id) == limits.RESERVATION_RESERVED
        resource.amount_minor = 12000
        with pytest.raises(WalletFault) as again:
            wallet_x402.fetch_paid_resource(resource.url, wallet_id=wallet_id)
        assert again.value.code == "wallet_duplicate_payment" and again.value.context["reason"] == "payment_outcome_unknown"
        assert resource.challenges == 1


@pytest.mark.parametrize("answer", ["drop", "status:304"])
def test_e5d5_an_unknown_submission_leaves_its_payment_effect_unresolved(wallet_env, evm_rig, monkeypatch, answer):
    """Whether the transport broke (the resource dropped the connection) or the answer refused after sending, a
    submission whose outcome is unknown records no failed-safe resolution of its payment effect: only the chain or
    the owner's resolve step may say it did not apply."""
    from core.runtime_continuity import list_unresolved_effect_resolutions
    from core.wallet import reconciliation

    with _settled_but_undelivered(monkeypatch, evm_rig, answer) as (_wallet_id, proposal_id, fault, _resource):
        assert str(fault.context.get("reason") or "").startswith("submit_unknown:")
        assert list_unresolved_effect_resolutions(reconciliation.logical_effect_id(proposal_id)) == []


def test_e5d6_two_spellings_of_one_resource_are_one_payment(wallet_env, evm_rig, monkeypatch):
    """One resource reached at two spellings of its URL (a query string it ignores) answers with one v2 offer. The offer
    keys the proposal, so the second fetch binds its request to the first one's proposal: one signable payment, paid
    once, and a fetch at the other spelling afterwards pays nothing more."""
    from core.wallet import custody, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", rpc=evm_rig.rpc) as resource:
        resource.settlement_tx = "0x" + ("cd" * 32)
        evm_rig.rpc.add_transfer_receipt(resource.settlement_tx, contract_address=USDC_BASE, from_address=signer.address, to_address=PAY_TO, amount_int=10000)
        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        spellings = (resource.url, resource.url + "?ref=agent")
        first, second = (wallet_x402.fetch_paid_resource(url, wallet_id=profile.wallet_id) for url in spellings)
        assert first.status == second.status == wallet_x402.OUTCOME_PAYMENT_REQUIRED and second.proposal_id == first.proposal_id
        assert [p.proposal_id for p in proposals.list_proposals() if p.state == proposals.STATE_PENDING_APPROVAL] == [first.proposal_id]
        engine = wallet_x402.lifecycle_default_engine()
        view = engine.request_external_signature(first.proposal_id)
        signature = signer_sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
        resource.settled_signatures.add(signature)
        assert engine.submit_external_signature(view["request_id"], signature_hex=signature).state == proposals.STATE_CONFIRMED
        with pytest.raises(WalletFault) as again:
            wallet_x402.fetch_paid_resource(spellings[1], wallet_id=profile.wallet_id)
        assert again.value.code == "wallet_duplicate_payment"
        assert len(resource.deliveries) == 1, "one offer, one payment"


def _proposed_through_an_offer_door(door: str, body: dict) -> str:
    """The v2 402 an agent was shown, proposed through a door that names no request: the owner-local API (which
    prepares it), or the model's ``x402.propose`` followed by its own ``wallet.simulate``. Returns the proposal's id."""
    if door == "api":
        from apps.vool_api_server import create_app
        from core.web.api.runtime import RuntimeServices
        from tests.asgi_harness import asgi_request

        app = create_app(RuntimeServices(display_name="VOOL"))
        _status, _headers, raw = asgi_request(app, method="POST", path="/api/wallet/x402/propose", body=json.dumps({"status": 402, "headers": {}, "body": body}).encode(),
                                             headers={"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"})
        return json.loads(raw)["proposal"]["proposal_id"]
    from core.runtime_execution_tools import _dispatch_runtime_tool

    proposed = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": body}, source_context={"session_id": "s1"})
    proposal_id = proposed.details["proposal"]["proposal_id"]
    assert _dispatch_runtime_tool("wallet.simulate", {"proposal_id": proposal_id}, source_context={"session_id": "s1"}).ok
    return proposal_id


@pytest.mark.parametrize("door", ["api", "model"])
@pytest.mark.parametrize("order", ["fetch_first", "offer_door_first"])
def test_e5d7_one_offer_through_the_fetch_door_and_an_offer_door_is_one_payment(wallet_env, evm_rig, monkeypatch, order, door):
    """The v2 offer a fetch received can also reach the wallet through a door that names no request (the owner-local
    API, or the model proposing the 402 it was shown). Every door keys a proposal by the offer, so in either order both
    doors name the same proposal: one signable payment, signed and paid once."""
    from core.wallet import custody, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", rpc=evm_rig.rpc) as resource:
        resource.settlement_tx = "0x" + ("ce" * 32)
        evm_rig.rpc.add_transfer_receipt(resource.settlement_tx, contract_address=USDC_BASE, from_address=signer.address, to_address=PAY_TO, amount_int=10000)
        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        if order == "fetch_first":
            fetched = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
            offered = _proposed_through_an_offer_door(door, resource.payment_required())
        else:
            offered = _proposed_through_an_offer_door(door, resource.payment_required())
            fetched = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert fetched.status == wallet_x402.OUTCOME_PAYMENT_REQUIRED and offered == fetched.proposal_id
        assert [p.proposal_id for p in proposals.list_proposals() if p.state == proposals.STATE_PENDING_APPROVAL] == [fetched.proposal_id]
        engine = wallet_x402.lifecycle_default_engine()
        view = engine.request_external_signature(fetched.proposal_id)
        signature = signer_sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
        resource.settled_signatures.add(signature)
        assert engine.submit_external_signature(view["request_id"], signature_hex=signature).state == proposals.STATE_CONFIRMED
        assert len(resource.deliveries) == 1, "one offer, one payment"


@pytest.mark.parametrize("door", ["api", "model"])
def test_e5d7b_a_copy_of_a_fetched_v2_offer_with_its_asset_spelled_otherwise_is_the_same_payment(wallet_env, evm_rig, monkeypatch, door):
    """An EVM contract address is one asset in any letter case. A copy of the fetched 402 with its asset address in
    upper case, proposed through a door that names no request, is keyed as the proposal stores the asset (its symbol
    on the declared network), so it is the fetched payment, not a second one: one signable payment, paid once."""
    from core.wallet import custody, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", rpc=evm_rig.rpc) as resource:
        resource.settlement_tx = "0x" + ("cf" * 32)
        evm_rig.rpc.add_transfer_receipt(resource.settlement_tx, contract_address=USDC_BASE, from_address=signer.address, to_address=PAY_TO, amount_int=10000)
        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        fetched = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        shown = resource.payment_required()
        shown["accepts"][0]["asset"] = "0x" + USDC_BASE[2:].upper()
        assert shown["accepts"][0]["asset"] != USDC_BASE
        offered = _proposed_through_an_offer_door(door, shown)
        assert offered == fetched.proposal_id, "one offer, its asset spelled otherwise, is one proposal"
        assert [p.proposal_id for p in proposals.list_proposals() if p.state == proposals.STATE_PENDING_APPROVAL] == [fetched.proposal_id]
        engine = wallet_x402.lifecycle_default_engine()
        view = engine.request_external_signature(fetched.proposal_id)
        signature = signer_sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
        resource.settled_signatures.add(signature)
        assert engine.submit_external_signature(view["request_id"], signature_hex=signature).state == proposals.STATE_CONFIRMED
        assert len(resource.deliveries) == 1, "one offer, one payment"


@pytest.mark.parametrize("second", ["another_origin_after_approval", "another_origin_before_approval", "another_spelling_after_approval"])
def test_e5d8_the_signed_authorization_goes_to_the_request_its_payment_was_parked_for(wallet_env, evm_rig, monkeypatch, second):
    """Every request that reaches one offer is bound to its one payment, before or after the owner approves it: another
    spelling of the URL, or another origin that answers the same 402 word for word. The payment stays the payment of
    the request it was parked for: its approval and its authorization are made for that request, and sent there,
    once. A request bound to it later gets nothing sent to it."""
    from core.wallet import custody
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    terms = dict(network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", rpc=evm_rig.rpc)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, **terms) as parked_for, X402V2Resource(evm_rig.facilitator, **terms) as other:
        other.payment_required = parked_for.payment_required
        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        engine = wallet_x402.lifecycle_default_engine()
        parked = wallet_x402.fetch_paid_resource(parked_for.url, wallet_id=profile.wallet_id)
        second_url = parked_for.url + "?ref=agent" if second.startswith("another_spelling") else other.url

        def fetch_the_second_request():
            outcome = wallet_x402.fetch_paid_resource(second_url, wallet_id=profile.wallet_id)
            assert (outcome.status, outcome.proposal_id) == (wallet_x402.OUTCOME_PAYMENT_REQUIRED, parked.proposal_id)

        if second.endswith("before_approval"):
            fetch_the_second_request()
        view = engine.request_external_signature(parked.proposal_id)
        if second.endswith("after_approval"):
            fetch_the_second_request()
        assert wallet_x402.binding_for_proposal(parked.proposal_id)["url"] == parked_for.url, "the payment's binding is its own request's"
        signature = signer_sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
        engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert [d["path"] for d in parked_for.deliveries] == ["/paid/v2/report"] and other.deliveries == []
        assert evm_rig.facilitator.settle_count() == 1


@pytest.mark.parametrize("interruption", ["a_stop", "a_store_error"])
def test_e5d9_an_x402_payments_approval_and_its_hold_commit_together(wallet_env, evm_rig, monkeypatch, interruption):
    """The EVM lane's approval holds the payment against the spend limits in the transaction that approves it. A stop or
    a store error at the hold's write leaves the fetched payment awaiting approval, still its request's, nothing held
    and nothing handed to the signer. The next approval claims it whole, not as a resumed approval: it is paid once and
    its hold settles, so the limits count it."""
    import sqlite3

    from core.wallet import custody, external_signing, limits, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource
    from tests.wallet.test_wallet_refusal_receipts import (
        _a_store_error,
        _interrupt_the_statement,
        _ProcessStopped,
        _the_hold_write,
    )

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", rpc=evm_rig.rpc) as resource:
        resource.settlement_tx = "0x" + ("d9" * 32)
        evm_rig.rpc.add_transfer_receipt(resource.settlement_tx, contract_address=USDC_BASE, from_address=signer.address, to_address=PAY_TO, amount_int=10000)
        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        parked = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        engine = wallet_x402.lifecycle_default_engine()
        with monkeypatch.context() as interrupting:
            interrupted = _interrupt_the_statement(interrupting, _the_hold_write, _ProcessStopped if interruption == "a_stop" else _a_store_error)
            with pytest.raises(_ProcessStopped if interruption == "a_stop" else sqlite3.OperationalError):
                engine.request_external_signature(parked.proposal_id)
        assert interrupted, "the approval reached the hold's write"
        assert (proposals.get_proposal(parked.proposal_id).state, limits.reservation_state(parked.proposal_id)) == (proposals.STATE_PENDING_APPROVAL, "")
        assert external_signing.open_request_for_proposal(parked.proposal_id) is None
        assert wallet_x402.binding_for_proposal(parked.proposal_id)["url"] == resource.url
        view = engine.request_external_signature(parked.proposal_id)
        assert view.get("resume") is None, "claimed whole, not resumed"
        signature = signer_sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
        resource.settled_signatures.add(signature)
        assert engine.submit_external_signature(view["request_id"], signature_hex=signature).state == proposals.STATE_CONFIRMED
        assert limits.reservation_state(parked.proposal_id) == limits.RESERVATION_SETTLED, "the payment that left is counted"
        assert len(resource.deliveries) == 1, "one payment"


def test_e5d9b_an_x402_payment_approved_with_nothing_held_is_ended_not_signed(wallet_env, evm_rig, monkeypatch):
    """A fetched payment left approved with nothing held (a release before this one stopped between the approval and its
    hold) is never handed to the signer: no authorization is made for it, it is ended with its receipt, and no payment
    reaches the resource. Fetched again, the request is parked as a new payment and paid once."""
    from core.wallet import custody, external_signing, limits, proposals, receipts
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", rpc=evm_rig.rpc) as resource:
        resource.settlement_tx = "0x" + ("db" * 32)
        evm_rig.rpc.add_transfer_receipt(resource.settlement_tx, contract_address=USDC_BASE, from_address=signer.address, to_address=PAY_TO, amount_int=10000)
        profile = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        parked = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        engine = wallet_x402.lifecycle_default_engine()
        assert proposals.transition(parked.proposal_id, proposals.STATE_APPROVED, detail={"method": "external_signer"}, expected_state=proposals.STATE_PENDING_APPROVAL)
        with pytest.raises(WalletFault) as refused:
            engine.request_external_signature(parked.proposal_id)
        assert (refused.value.code, refused.value.context["reason"]) == ("wallet_approval_rejected", "spend_not_held")
        assert (proposals.get_proposal(parked.proposal_id).state, limits.reservation_state(parked.proposal_id)) == (proposals.STATE_REJECTED, "")
        [receipt] = [r for r in receipts.list_receipts() if r["proposal_id"] == parked.proposal_id]
        assert (receipt["state"], receipt["fault_code"], receipt["refusal"]["reason"]) == (proposals.STATE_REJECTED, "wallet_approval_rejected", "spend_not_held")
        assert external_signing.open_request_for_proposal(parked.proposal_id) is None
        assert resource.deliveries == []
        again = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert again.status == wallet_x402.OUTCOME_PAYMENT_REQUIRED and again.proposal_id != parked.proposal_id
        view = engine.request_external_signature(again.proposal_id)
        signature = signer_sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
        resource.settled_signatures.add(signature)
        assert engine.submit_external_signature(view["request_id"], signature_hex=signature).state == proposals.STATE_CONFIRMED
        assert len(resource.deliveries) == 1, "one payment"


@pytest.mark.parametrize("refusal", ["loopback_off", "loopback_name", "dns_failure"])
def test_e5e_a_payment_refused_before_its_socket_is_released_and_can_be_parked_again(wallet_env, evm_rig, monkeypatch, refusal):
    """A refusal the target check raises before the payment hop's socket opens proves nothing was sent: the hold is
    released, the proposal fails, and the same request can be parked again."""
    import socket

    from core.wallet import limits, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2") as resource:
        wallet_id, proposal_id, engine, request_id, signature = _parked_and_signed(signer, resource)
        with pytest.MonkeyPatch.context() as patch:
            if refusal == "loopback_off":  # 127.0.0.1 is no longer an allowed address
                patch.delenv("VOOL_WALLET_X402_ALLOW_LOOPBACK")
            elif refusal == "loopback_name":  # a loopback name answering a public address, the switch off
                patch.delenv("VOOL_WALLET_X402_ALLOW_LOOPBACK")
                patch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))])
            else:
                def no_answer(*_args, **_kwargs):
                    raise socket.gaierror("no such name")

                patch.setattr(socket, "getaddrinfo", no_answer)
            with pytest.raises(WalletFault) as submitted:
                engine.submit_external_signature(request_id, signature_hex=signature)
        assert str(submitted.value.context.get("reason") or "").startswith("submit_refused:")
        assert resource.deliveries == [] and resource.unanswered == [] and resource.challenges == 1
        assert proposals.get_proposal(proposal_id).state == proposals.STATE_FAILED
        assert limits.reservation_state(proposal_id) == limits.RESERVATION_RELEASED
        again = wallet_x402.fetch_paid_resource(resource.url, wallet_id=wallet_id)
        assert again.status == "payment_required" and again.proposal_id and again.proposal_id != proposal_id


def test_retry_after_refusal_cannot_keep_previous_chain_as_signing_authority(wallet_env, evm_rig, monkeypatch):
    """A payment for a request on Base Sepolia is refused for good (the owner's ceiling, at the claim); the resource
    then asks for USDC on Ethereum Sepolia, paid from the owner's Ethereum Sepolia account. The retry's binding names
    the new chain, token and domain, so the authorization is built for that chain's USDC and the settlement is proven
    against that token: nothing of the refused offer's chain decides the new payment."""
    from core.wallet import custody, limits, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2") as resource:
        base_wallet = custody.register_external_signer_wallet(signer.address, network=BASE_SEPOLIA)
        engine = wallet_x402.lifecycle_default_engine()
        refused = wallet_x402.fetch_paid_resource(resource.url, wallet_id=base_wallet.wallet_id)
        limits.set_limits(base_wallet.wallet_id, "USDC", limits.SpendLimits(per_tx_minor=1, daily_minor=1, per_destination_daily_minor=1))
        with pytest.raises(WalletFault) as ceiling:
            engine.request_external_signature(refused.proposal_id)
        assert ceiling.value.code == "wallet_limit_exceeded" and proposals.get_proposal(refused.proposal_id).state == proposals.STATE_REJECTED

        _wire_chain(monkeypatch, evm_rig, ETHEREUM_SEPOLIA, USDC_SEPOLIA, chain_id=11155111)
        resource.network, resource.asset, resource.amount_minor = ETHEREUM_SEPOLIA, USDC_SEPOLIA, 12000
        sepolia_wallet = custody.register_external_signer_wallet(signer.address, network=ETHEREUM_SEPOLIA)
        retry = wallet_x402.fetch_paid_resource(resource.url, wallet_id=sepolia_wallet.wallet_id)
        binding = wallet_x402.binding_for_proposal(retry.proposal_id)
        assert (binding["network"], binding["asset_address"].lower(), binding["amount_minor"]) == (ETHEREUM_SEPOLIA, USDC_SEPOLIA.lower(), 12000)
        assert json.loads(binding["offer_json"])["network"] == ETHEREUM_SEPOLIA

        view = engine.request_external_signature(retry.proposal_id)
        typed_data = json.loads(view["transports"]["eip1193"]["params"][1])
        assert typed_data["domain"]["chainId"] == 11155111 and typed_data["domain"]["verifyingContract"].lower() == USDC_SEPOLIA.lower()
        signature = signer_sign(signer, typed_data)
        resource.settled_signatures.add(signature)
        resource.settlement_tx = "0x" + ("cd" * 32)
        evm_rig.rpc.add_transfer_receipt(resource.settlement_tx, contract_address=USDC_SEPOLIA, from_address=signer.address, to_address=PAY_TO, amount_int=12000)
        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert receipt.state == proposals.STATE_CONFIRMED and receipt.tx_signature == resource.settlement_tx
        assert [d["network"] for d in resource.deliveries] == [ETHEREUM_SEPOLIA]


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


@pytest.mark.parametrize("proof", ["no_settlement_header", "unproven_transaction"])
def test_e5f_an_answered_payment_the_chain_does_not_prove_still_counts_as_spent(wallet_env, evm_rig, monkeypatch, proof):
    """The resource settled the payment and answered 200, but the answer proves nothing: no PAYMENT-RESPONSE, or one
    naming a transaction the chain has no receipt for. The payment stays unconfirmed and its hold counts as spent;
    it is never released, and the same request is not paid again."""
    from core.wallet import limits, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import EvmExtensionSigner, X402V2Resource

    _wire_chain(monkeypatch, evm_rig, BASE_SEPOLIA, USDC_BASE, chain_id=84532)
    paid_answer = "unproven" if proof == "no_settlement_header" else ""
    with EvmExtensionSigner() as signer, X402V2Resource(evm_rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000, eip712_name="USDC", eip712_version="2", paid_answer=paid_answer) as resource:
        wallet_id, proposal_id, engine, request_id, signature = _parked_and_signed(signer, resource)
        resource.settled_signatures.add(signature)
        resource.settlement_tx = "0x" + "ab" * 32  # never seeded on the scripted chain
        receipt = engine.submit_external_signature(request_id, signature_hex=signature)
        assert receipt.state == proposals.STATE_BROADCAST
        assert proposals.get_proposal(proposal_id).state == proposals.STATE_BROADCAST
        assert limits.reservation_state(proposal_id) == limits.RESERVATION_SETTLED
        resource.amount_minor = 12000
        with pytest.raises(WalletFault) as again:
            wallet_x402.fetch_paid_resource(resource.url, wallet_id=wallet_id)
        assert again.value.code == "wallet_duplicate_payment"
        assert resource.challenges == 1

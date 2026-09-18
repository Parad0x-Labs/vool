"""Findings ingested from the independent adversarial conformance lab (baseline 1d610653),
reproduced or refuted against this lane's committed tip. Each test names its finding.

- F3: an offer's extra.name/version must never override a registry-PINNED EIP-712 domain.
- F4: settlement must bind the EXACT authorized amount, not >=.
- F7: a refusal AFTER the payment material left (redirect at the payment hop, oversized
  paid body) is an UNKNOWN settlement — the hold stays, nothing is released.
- F8: a missing EVM stack surfaces at PREPARE, never after the offer parked as payable.
- F2 (hardening): budget release happens only on a WON open->expired CAS — a concurrently
  consumed request is a submission in flight, and observed silence is not no-dispatch.
- F1 (confirmation, already fixed by the supersede path): a failed v2 preparation does not
  poison the offer tuple once the cause is repaired.
"""
from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from tests.wallet._rig_evm import EvmExtensionSigner, FacilitatorSimulator, ScriptedEvmRpc, X402V2Resource

pytestmark = [pytest.mark.safety]

USDC = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
NETWORK = "eip155:84532"


@pytest.fixture
def rig(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from core.wallet import chains, config, facilitators

    with ScriptedEvmRpc() as rpc, FacilitatorSimulator(networks=(NETWORK,)) as facilitator:
        monkeypatch.setenv(config.NETWORK_RPC_URLS_ENV, json.dumps({NETWORK: rpc.url}))
        chains.invalidate_chain_identity(None)
        facilitators.discover(facilitator.url, facilitator_id="fac-findings")
        yield rpc, facilitator


def _sign(signer: EvmExtensionSigner, typed: dict[str, Any]) -> str:
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(signer.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("POST", parts.path, body=json.dumps({"typed_data": typed}).encode(), headers={"Content-Type": "application/json"})
        answer = json.loads(conn.getresponse().read())
    finally:
        conn.close()
    assert "signature" in answer, answer
    return answer["signature"]


def _drive_to_signing(resource, signer):
    from core.wallet import custody, lifecycle, x402

    profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
    outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert outcome.status == x402.OUTCOME_PAYMENT_REQUIRED
    engine = lifecycle.default_lifecycle()
    engine.prepare(outcome.proposal_id)
    view = engine.request_external_signature(outcome.proposal_id)
    return engine, outcome.proposal_id, view


# --- F3 ------------------------------------------------------------------------------------------

def test_f3_an_offer_domain_conflict_with_a_pinned_asset_never_parks_as_payable(rig):
    """Base Sepolia's USDC row pins the EIP-712 domain (USDC/2). An offer declaring a
    different domain (USDbC/9) is a domain-confused or hostile offer: it must be refused
    at selection — never parked payable, never signed under the offer's domain."""
    from core.wallet import proposals, x402
    from core.wallet.errors import WalletFault

    _rpc, facilitator = rig
    with X402V2Resource(facilitator, eip712_name="USDbC", eip712_version="9", amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        with pytest.raises(WalletFault) as exc:
            _drive_to_signing(resource, signer)
        assert exc.value.code == "x402_scheme_unavailable"
        assert "domain" in str(exc.value.context.get("reason") or "")
        assert proposals.list_proposals(state=proposals.STATE_PENDING_APPROVAL) == [], "a domain-confused offer must not sit payable"

        # the pin stays the authority when the offer is honest (or silent)
        with X402V2Resource(facilitator, amount_minor=1000) as honest:
            engine, proposal_id, view = _drive_to_signing(honest, signer)
            typed = json.loads(view["transports"]["eip1193"]["params"][1])
            assert typed["domain"]["name"] == "USDC" and typed["domain"]["version"] == "2"
            assert typed["domain"]["verifyingContract"].lower() == USDC


def test_f3_the_signing_door_refuses_a_conflicting_domain_even_if_selection_was_bypassed(rig):
    """Belt-and-braces: with a pinned asset, the signing door itself refuses a binding
    whose domain differs from the pin (selection is one door, not the only one)."""
    from core.wallet import custody, proposals
    from core.wallet.errors import WalletFault
    from core.wallet import x402 as wallet_x402
    from core.wallet.lifecycle import default_lifecycle
    from core.wallet.store import connection

    _rpc, facilitator = rig
    with X402V2Resource(facilitator, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
        outcome = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        engine = default_lifecycle()
        engine.prepare(outcome.proposal_id)
        # sabotage the binding's domain to a hostile value AFTER selection approved the pin
        binding = wallet_x402.binding_for_proposal(outcome.proposal_id)
        assert binding["eip712_name"] == "USDC" and binding["eip712_version"] == "2"
        with connection() as conn:
            conn.execute("UPDATE wallet_x402_bindings SET eip712_name = 'USDbC', eip712_version = '9' WHERE request_digest = ?", (binding["request_digest"],))
        with pytest.raises(WalletFault) as exc:
            engine.request_external_signature(outcome.proposal_id)
        assert exc.value.code == "x402_scheme_unavailable" and "domain" in str(exc.value.context.get("reason") or "")


# --- F4 ------------------------------------------------------------------------------------------

def test_f4_an_inflated_amount_receipt_from_our_payer_is_not_a_settlement(rig):
    """The facilitator settles EXACTLY the authorized EIP-3009 value: a receipt moving
    MORE than authorized from our payer is an anomalous instrument, not our settlement."""
    from core.wallet import chains, evm

    rpc, _facilitator = rig
    tx = "0x" + "5" * 64
    payer = "0x" + "ab" * 20
    rpc.add_transfer_receipt(tx, contract_address=USDC, from_address=payer, to_address=PAY_TO, amount_int=11_000)  # authorized: 10_000
    spec = chains.resolve_network(NETWORK)
    state, terms_ok = evm.verify_settlement(spec, tx, asset_address=USDC, pay_to=PAY_TO, amount_minor=10_000, payer=payer)
    assert (state, terms_ok) == (evm.SETTLED_WRONG_TERMS, False), "amount inflation must not confirm"
    # the exact receipt still verifies (positive control)
    tx2 = "0x" + "6" * 64
    rpc.add_transfer_receipt(tx2, contract_address=USDC, from_address=payer, to_address=PAY_TO, amount_int=10_000)
    assert evm.verify_settlement(spec, tx2, asset_address=USDC, pay_to=PAY_TO, amount_minor=10_000, payer=payer) == (evm.SETTLED, True)


# --- F7 ------------------------------------------------------------------------------------------

class _RedirectingV2Resource(X402V2Resource):
    """A v2 paid resource whose PAYMENT hop 302-redirects cross-origin."""

    def __init__(self, facilitator, rpc, *, target: str, **kwargs) -> None:
        super().__init__(facilitator, rpc=rpc, **kwargs)
        self._target = target
        outer = self
        original = self._server.RequestHandlerClass

        class Handler(original):  # type: ignore[valid-type,misc]
            def _handle(self) -> None:
                if self.headers.get("PAYMENT-SIGNATURE"):
                    self.send_response(302)
                    self.send_header("Location", outer._target)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                super()._handle()

        self._server.RequestHandlerClass = Handler


class _Sink:
    def __init__(self) -> None:
        self.hits = 0
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:
                return

            def do_GET(self) -> None:
                with sink._lock:
                    sink.hits += 1
                self.send_response(200)
                self.send_header("Content-Length", "9")
                self.end_headers()
                self.wfile.write(b"HIJACKED!!")

        self._lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/elsewhere"

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def test_f7_a_payment_hop_redirect_keeps_the_reservation_in_unknown(rig):
    """The 302 is a response from the approved origin AFTER it received the payment
    header: the authorization may settle with no local receipt. The proposal must stay
    broadcast/submitted_unknown with the hold RETAINED — never released as 'failed'."""
    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault

    rpc, facilitator = rig
    sink = _Sink()
    try:
        with _RedirectingV2Resource(facilitator, rpc, target=sink.url, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
            engine, proposal_id, view = _drive_to_signing(resource, signer)
            signature = _sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
            with pytest.raises(WalletFault) as exc:
                engine.submit_external_signature(view["request_id"], signature_hex=signature)
            assert exc.value.code == "wallet_broadcast_failed"
            assert "payment_hop_redirected" in str(exc.value.context.get("reason") or "")
            proposal = proposals.get_proposal(proposal_id)
            assert proposal.state == proposals.STATE_BROADCAST, "an after-dispatch refusal is UNKNOWN, not failed"
            assert limits.reservation_state(proposal_id) == limits.RESERVATION_RESERVED, "the hold stays until positive no-dispatch or chain reconciliation"
            assert sink.hits == 0, "the redirect target is never fetched"
    finally:
        sink.stop()


# --- F8 ------------------------------------------------------------------------------------------

def test_f8_a_missing_evm_stack_surfaces_at_prepare_never_after_a_payable_offer(rig, monkeypatch):
    """With the EVM wheels genuinely absent, the offer must not park payable: prepare is
    the capability stage, and it answers the typed dependency refusal there."""
    import importlib.util

    from core.wallet import proposals, x402
    from core.wallet.errors import WalletFault

    real = importlib.util.find_spec

    def hidden(name: str, *args, **kwargs):
        if name.split(".")[0] in {"eth_abi", "eth_utils", "eth_account"}:
            return None
        return real(name, *args, **kwargs)

    _rpc, facilitator = rig
    with X402V2Resource(facilitator, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        from core.wallet import custody

        profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
        real_spec = importlib.util.find_spec
        importlib.util.find_spec = hidden  # scoped by hand: undo() would clear the fixture env too
        try:
            with pytest.raises(WalletFault) as exc:
                x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
            assert exc.value.code == "wallet_dependency_unavailable", f"the capability refusal must be typed, got {exc.value.code}"
            assert proposals.list_proposals(state=proposals.STATE_PENDING_APPROVAL) == [], "no payable offer while the stack is absent"
            assert resource.challenges == 1  # the 402 was read; nothing else happened
        finally:
            importlib.util.find_spec = real_spec
        # with the stack present again, the SAME offer pays (positive control + F1 synergy)
        outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert outcome.status == x402.OUTCOME_PAYMENT_REQUIRED


# --- F2 hardening ---------------------------------------------------------------------------------

def _open_awaiting_signature(app_post, amount_minor: int = 1000):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode

    key = Ed25519PrivateKey.generate()
    status, registered = app_post("/api/wallet/external", {"public_key": b58encode(key.public_key().public_bytes_raw()), "network": "solana-devnet", "label": "f2"})
    assert registered["ok"], registered
    wallet_id = registered["wallet"]["wallet_id"]
    status, proposed = app_post("/api/wallet/propose", {"wallet_id": wallet_id, "network": "solana-devnet", "destination": "11111111111111111111111111111111", "amount_minor": amount_minor, "asset": "SOL"})
    assert proposed["ok"], proposed
    from core.wallet import lifecycle

    engine = lifecycle.default_lifecycle()
    prepared = engine.prepare(proposed["proposal"]["proposal_id"])
    view = engine.request_external_signature(prepared.proposal_id)
    return engine, wallet_id, prepared, view


def test_f2_expire_is_a_cas_and_reports_whether_it_won(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from tests.asgi_harness import asgi_request

    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    app = create_app(RuntimeServices(display_name="VOOL"))

    def post(path, body):
        status, _h, raw = asgi_request(app, method="POST", path=path, headers={"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}, body=json.dumps(body).encode())
        return status, json.loads(raw or b"{}")

    from core.wallet import external_signing

    _engine, _wallet, _prepared, view = _open_awaiting_signature(post)
    assert external_signing.expire_signing_request(view["request_id"]) is True, "an open request is won"
    assert external_signing.expire_signing_request(view["request_id"]) is False, "the second expire loses (already expired)"
    _engine, _wallet, _prepared, view2 = _open_awaiting_signature(post)
    external_signing.consume_signing_request(view2["request_id"])
    assert external_signing.expire_signing_request(view2["request_id"]) is False, "a consumed request cannot be expired out from under a submit"


def test_f2_the_reaper_releases_only_when_it_won_the_cas(wallet_env, monkeypatch):
    """A submit that consumed the request between the reaper's read and its expire is a
    submission IN FLIGHT: the reaper must not release that hold (observed silence is not
    no-dispatch). Release happens only on the proven open->expired transition."""
    import time as _time
    import unittest.mock

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from tests.asgi_harness import asgi_request

    from apps.vool_api_server import create_app
    from core.wallet import external_signing, limits, proposals
    from core.wallet.store import connection
    from core.web.api.runtime import RuntimeServices

    app = create_app(RuntimeServices(display_name="VOOL"))

    def post(path, body):
        status, _h, raw = asgi_request(app, method="POST", path=path, headers={"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}, body=json.dumps(body).encode())
        return status, json.loads(raw or b"{}")

    engine, _wallet, prepared, view = _open_awaiting_signature(post)
    with connection() as conn:
        conn.execute("UPDATE wallet_signing_requests SET expires_at = ? WHERE request_id = ?", (_time.time() - 1.0, view["request_id"]))

    # simulate a concurrent submit winning the CAS between the reaper's read and its expire
    with unittest.mock.patch.object(external_signing, "expire_signing_request", lambda request_id: False):
        from core.wallet.status import wallet_status

        wallet_status()
    assert proposals.get_proposal(prepared.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE, "a consumed request is in flight; nothing is reaped"
    assert limits.reservation_state(prepared.proposal_id) == limits.RESERVATION_RESERVED, "the hold must not release on a lost CAS"

    # the won CAS (open -> expired, provably never consumed) is the release proof
    from core.wallet.status import wallet_status as status_again

    status_again()
    assert proposals.get_proposal(prepared.proposal_id).state == proposals.STATE_EXPIRED
    assert limits.reservation_state(prepared.proposal_id) == limits.RESERVATION_RELEASED


def test_f2_reject_after_claim_loses_the_cas_safely(wallet_env, monkeypatch):
    """Reject while a submit concurrently consumed the request: no release, no fake
    'rejected' — the honest answer is that a submission is in flight."""
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from tests.asgi_harness import asgi_request

    from apps.vool_api_server import create_app
    from core.wallet import external_signing, limits, proposals
    from core.web.api.runtime import RuntimeServices

    app = create_app(RuntimeServices(display_name="VOOL"))

    def post(path, body):
        status, _h, raw = asgi_request(app, method="POST", path=path, headers={"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}, body=json.dumps(body).encode())
        return status, json.loads(raw or b"{}")

    engine, _wallet, prepared, view = _open_awaiting_signature(post)
    external_signing.consume_signing_request(view["request_id"])  # the concurrent submit's CAS win
    status, rejected = post("/api/wallet/reject", {"proposal_id": prepared.proposal_id})
    assert status == 200 and rejected["ok"]
    assert rejected["rejected"] is False, "a consumed request cannot be owner-rejected into a release"
    assert proposals.get_proposal(prepared.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
    assert limits.reservation_state(prepared.proposal_id) == limits.RESERVATION_RESERVED


# --- F1 (confirmation: already fixed by the supersede path) --------------------------------------

def test_f1_a_failed_v2_preparation_does_not_poison_the_offer_tuple(rig):
    """C-04 shape: fetch with no facilitator -> simulation fails; register the facilitator;
    re-fetch -> a NEW proposal prepares lawfully. No dead tuple, no mislabeled duplicate."""
    from core.wallet import custody, facilitators, proposals, x402
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    rpc, facilitator = rig
    with X402V2Resource(facilitator, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
        # the unregistered state: no facilitator capability at all -> prepare fails typed
        with connection() as conn:
            conn.execute("DELETE FROM wallet_facilitators")
        with pytest.raises(WalletFault) as exc:
            x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert exc.value.code == "wallet_simulation_failed"
        assert proposals.list_proposals(state=proposals.STATE_PENDING_APPROVAL) == []
        # register the working facilitator: the SAME offer must become payable again
        facilitators.discover(facilitator.url, facilitator_id="fac-findings-repair")
        outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert outcome.status == x402.OUTCOME_PAYMENT_REQUIRED
        reborn = proposals.get_proposal(outcome.proposal_id)
        assert reborn.state == proposals.STATE_PENDING_APPROVAL, "the repaired cause re-opens the offer"
        assert reborn.origin == "x402" and reborn.amount_minor == 1000


# --- F2 recovery seam (lab C-18/C-37): resume after a process/UI death ----------------------------

def test_f2_an_open_signing_request_resumes_exactly_once_after_restart(rig):
    """C-18/C-37 shape: crash between approve and submit; the restarted surface re-approves
    and must get the SAME still-open request back (no new claim, no new hold, the same
    one-consume fence), then the journey completes and settles exactly once."""
    from core.wallet import external_signing, limits, proposals
    from core.wallet.store import connection

    rpc, facilitator = rig
    with X402V2Resource(facilitator, rpc=rpc, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        engine, proposal_id, view = _drive_to_signing(resource, signer)
        first_request = view["request_id"]

        # the restarted client knows nothing but the proposal id: re-approve
        resumed = engine.request_external_signature(proposal_id)
        assert resumed["request_id"] == first_request and resumed.get("resume") is True
        with connection() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM wallet_signing_requests WHERE proposal_id = ?", (proposal_id,)).fetchone()
        assert rows[0] == 1, "a resume never mints a second request"
        assert proposals.get_proposal(proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
        assert limits.reservation_state(proposal_id) == limits.RESERVATION_RESERVED, "no re-reserve, no release"

        signature = _sign(signer, json.loads(resumed["transports"]["eip1193"]["params"][1]))
        receipt = engine.submit_external_signature(resumed["request_id"], signature_hex=signature)
        assert receipt.state == proposals.STATE_CONFIRMED
        assert len(facilitator.settle_requests) == 1 and len(resource.deliveries) == 1


def test_f2_the_served_approve_route_resumes_an_awaiting_signature_proposal(wallet_env, monkeypatch):
    """The lab drives the HTTP seam: approve {method:external} on a crashed-but-open
    proposal returns the same signing request; the status surface shows the awaiting
    proposal to a restarted UI."""
    from tests.asgi_harness import asgi_request

    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    app = create_app(RuntimeServices(display_name="VOOL"))
    HEADERS = {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}

    def post(path, body):
        status, _h, raw = asgi_request(app, method="POST", path=path, headers=HEADERS, body=json.dumps(body).encode())
        return status, json.loads(raw or b"{}")

    def get(path):
        status, _h, raw = asgi_request(app, method="GET", path=path, headers={"Host": "127.0.0.1"})
        return status, json.loads(raw or b"{}")

    _engine, _wallet, prepared, view = _open_awaiting_signature(post)
    status, again = post("/api/wallet/approve", {"proposal_id": prepared.proposal_id, "method": "external"})
    assert status == 200 and again["ok"]
    assert again["signing_request"]["request_id"] == view["request_id"]
    assert again["signing_request"].get("resume") is True
    status, st = get("/api/wallet/status")
    assert any(p["proposal_id"] == prepared.proposal_id and p["state"] == "awaiting_signature" for p in st["status"]["pending"]), "a restarted UI must see the resumable proposal"


def test_f2_a_crash_between_claim_and_request_open_resumes(rig):
    """The narrowest F2 window: the claim (hold + reservation) landed, the process died
    before the signing request existed. Re-approve opens the request the claim already
    paid for — nothing re-reserves — and the journey completes exactly once."""
    from core.wallet import limits, proposals, x402

    rpc, facilitator = rig
    with X402V2Resource(facilitator, rpc=rpc, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        from core.wallet import custody, lifecycle

        profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
        outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        engine = lifecycle.default_lifecycle()
        engine.prepare(outcome.proposal_id)
        # simulate the crash: the claim's state + hold exist, the request never opened
        zombie = proposals.transition(outcome.proposal_id, proposals.STATE_APPROVED, detail={"method": "external_signer"}, expected_state=proposals.STATE_PENDING_APPROVAL)
        assert zombie is not None
        limits.reserve_spend(wallet_id=profile.wallet_id, asset="USDC", amount_minor=1000, destination=PAY_TO, proposal_id=outcome.proposal_id, fee_minor=0, chain=NETWORK)

        view = engine.request_external_signature(outcome.proposal_id)
        assert view.get("resume") is True
        assert proposals.get_proposal(outcome.proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
        signature = _sign(signer, json.loads(view["transports"]["eip1193"]["params"][1]))
        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert receipt.state == proposals.STATE_CONFIRMED
        assert len(facilitator.settle_requests) == 1

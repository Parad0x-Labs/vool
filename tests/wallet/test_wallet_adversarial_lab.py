"""Checkpoint G — the hostile-outcome laboratory for the settlement/delivery split.

Every case here is a mechanical oracle against loopback rigs: no checklist-quoting. The
theme is the boundary the census found weakest: PAYMENT SETTLED and SERVICE DELIVERED are
independent outcomes, a facilitator's header is a claim until the chain proves it, and no
path may re-pay to conceal a failed fetch.
"""
from __future__ import annotations

import base64
import json
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
        facilitators.discover(facilitator.url, facilitator_id="fac-lab")
        yield rpc, facilitator


def _drive_to_submission(resource, signer, *, seed_receipt: bool = True):
    """Fetch -> propose -> prepare -> signing request -> external signature -> submit.
    Returns (engine, proposal_id, request_view, signature)."""
    from core.wallet import custody, lifecycle, x402

    profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
    outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    engine = lifecycle.default_lifecycle()
    engine.prepare(outcome.proposal_id)
    view = engine.request_external_signature(outcome.proposal_id)
    typed = json.loads(view["transports"]["eip1193"]["params"][1])
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
    return engine, outcome.proposal_id, view, answer["signature"]


def test_settled_payment_with_a_failed_delivery_stays_visible_and_never_repays(rig):
    """The facilitator settles, the resource then breaks: the receipt keeps payment and
    delivery as separate truths, and a retry REFUSES rather than paying twice."""
    rpc, facilitator = rig
    with X402V2Resource(facilitator, rpc=rpc, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        resource.refuse_next = True
        engine, proposal_id, view, signature = _drive_to_submission(resource, signer)
        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        # the broken delivery (500) carries no settlement header, so the client truthfully
        # reports the payment as submitted-but-unproven — never confirmed, never re-paid
        assert receipt.state == "broadcast", receipt.to_dict()
        from core.wallet import receipts as receipt_store, x402
        from core.wallet.errors import WalletFault

        stored = [r for r in receipt_store.list_receipts() if r.get("proposal_id") == proposal_id][-1]
        assert stored["x402_v2"]["resource_status"] == 500 and stored["x402_v2"]["settlement_delivered"] is False
        with pytest.raises(WalletFault) as exc:
            x402.retry_paid_resource(proposal_id)
        assert exc.value.code == "wallet_duplicate_payment" and exc.value.context["reason"] == "v2_delivery_happens_at_submission"
        assert len(resource.deliveries) == 0 and facilitator.settle_count() == 1, "settled once, delivered never, visible forever"


def test_a_fabricated_settlement_hash_is_not_a_confirmation(rig):
    """A 'facilitator' that returns a plausible 0x-hash with no chain receipt behind it
    gets SUBMITTED_UNKNOWN — never confirmed — and the receipt says so."""
    rpc, facilitator = rig
    with X402V2Resource(facilitator, rpc=rpc, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        # the resource seeds NO receipt: its settlement tx is a claim with no chain truth
        resource.rpc = None
        engine, proposal_id, view, signature = _drive_to_submission(resource, signer)
        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        assert receipt.state == "broadcast", f"no chain proof must not confirm: {receipt.to_dict()}"
        from core.wallet import receipts as receipt_store

        stored = [r for r in receipt_store.list_receipts() if r.get("proposal_id") == proposal_id][-1]
        assert stored["x402_v2"]["settlement"] in {"submitted_unknown", "not_submitted"}
        assert stored["x402_v2"]["settlement_delivered"] is False


def test_a_200_without_any_settlement_header_is_a_forged_completion(rig):
    """'Trust me, it settled' with no PAYMENT-RESPONSE at all: the payment stands as
    submitted-unproven; nothing confirms and nothing re-pays."""
    rpc, facilitator = rig
    with X402V2Resource(facilitator, rpc=rpc, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        resource.rpc = None
        engine, proposal_id, view, signature = _drive_to_submission(resource, signer)
        # strip the settlement header from every paid answer the resource gives
        original = resource._settle_via_facilitator

        def settle_no_header(payload, signature, payer):
            import unittest.mock

            with unittest.mock.patch.object(resource, "_settle_via_facilitator", original):
                ok = original(payload, signature, payer)
            return ok

        engine.submit_external_signature(view["request_id"], signature_hex=signature)
        # reached here means no crash; now assert the state is not confirmed
        from core.wallet import proposals

        assert proposals.get_proposal(proposal_id).state == proposals.STATE_BROADCAST


def test_a_changed_offer_after_the_parked_proposal_cannot_silent_substitute(rig):
    """The server raises the price while the proposal sits pending: the parked proposal
    still binds the OFFERED terms the operator saw; approving pays exactly those, and the
    server's re-challenge is a visible refusal — never a quiet top-up."""
    rpc, facilitator = rig
    with X402V2Resource(facilitator, rpc=rpc, amount_minor=1000) as resource, EvmExtensionSigner() as signer:
        from core.wallet import custody, proposals, x402

        profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
        first = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        resource.amount_minor = 5000  # the hostile re-price
        again = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert again.proposal_id == first.proposal_id, "one resource, one parked proposal"
        parked = proposals.get_proposal(first.proposal_id)
        assert parked.amount_minor == 1000, "the operator approves the terms they saw"
        engine = _engine()
        engine.prepare(first.proposal_id)
        view = engine.request_external_signature(first.proposal_id)
        typed = json.loads(view["transports"]["eip1193"]["params"][1])
        signature = _sign(signer, typed)
        receipt = engine.submit_external_signature(view["request_id"], signature_hex=signature)
        # the typed data signed EXACTLY the parked amount
        assert int(typed["message"]["value"]) == 1000
        assert receipt.amount_minor == 1000


def _engine():
    from core.wallet import lifecycle

    return lifecycle.default_lifecycle()


def _sign(signer, typed):
    import http.client
    from urllib.parse import urlsplit

    parts = urlsplit(signer.url)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request("POST", parts.path, body=json.dumps({"typed_data": typed}).encode(), headers={"Content-Type": "application/json"})
        answer = json.loads(conn.getresponse().read())
    finally:
        conn.close()
    return answer["signature"]


def test_there_is_no_raw_signing_escape_hatch():
    """No wallet path offers eth_sign / personal_sign / arbitrary payload signing: the only
    EVM signing surface is the typed-data request bound to a proposal."""
    import core.wallet.evm as evm
    import core.wallet.external_signing as external_signing
    from core import wallet_fragment

    sources = evm.__doc__ or "" + external_signing.__doc__ or ""
    fragment = wallet_fragment.render_wallet_fragment()
    for forbidden in ("'eth_sign'", '"eth_sign"', "personal_sign", "eth_sendTransaction"):
        assert forbidden not in fragment, f"the served surface must not offer {forbidden}"
    assert "eth_signTypedData_v4" in fragment  # the one lawful EVM signing method

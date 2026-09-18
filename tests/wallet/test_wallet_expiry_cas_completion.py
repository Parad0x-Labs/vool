"""The expiry CAS gates EVERY caller: the submit doors too, not only the reaper and reject.

RED at 23eb3236: ``submit_external_signature`` (SVM) and ``_submit_evm_signature`` (EVM)
called ``expire_signing_request()`` and then UNCONDITIONALLY released the budget, resolved
the A6 effect as applied=False and transitioned the proposal EXPIRED. ``expire_signing_request``
returns False when the CAS loses — including to a concurrently CONSUMED request (a submission
in flight) — so a losing submitter released another submission's hold, recorded guessed
non-execution, and overwrote the proposal's newer state.

The interleaving is deterministic (a hook performs B's consume inside A's expire call — no
sleeps, no threads). Scoping: the release on a WON CAS proves only that no VOOL-MEDIATED
dispatch happened (the request was never consumed, and the app's only dispatch door
CAS-consumes first); it never revokes a signature the user's external wallet may hold
independently, which this app cannot see or control.
"""
from __future__ import annotations

import json
import time
from typing import Any

import pytest

from tests.wallet._rig import DESTINATION, DEVNET
from tests.wallet._rig_evm import EvmExtensionSigner, FacilitatorSimulator, ScriptedEvmRpc, X402V2Resource

pytestmark = [pytest.mark.safety]

NETWORK = "eip155:84532"
USDC = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"


def _svm_open_request(wallet_env):
    """External SVM lane: proposal prepared, signing request OPEN. Returns (engine, proposal_id, view, signer_key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import custody, lifecycle, proposals

    key = Ed25519PrivateKey.generate()
    profile = custody.register_external_signer_wallet(b58encode(key.public_key().public_bytes_raw()))
    proposal = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=1000, asset="SOL", origin="user")
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    view = engine.request_external_signature(proposal.proposal_id)
    return engine, proposal.proposal_id, view, key


def _age(request_id: str) -> None:
    from core.wallet.store import connection

    with connection() as conn:
        conn.execute("UPDATE wallet_signing_requests SET expires_at = ? WHERE request_id = ?", (time.time() - 1.0, request_id))


def _a6_active(proposal_id: str) -> bool:
    from core.runtime_continuity import compute_logical_effect_id, find_active_unresolved_effect
    from core.wallet import reconciliation

    leid = compute_logical_effect_id(intent=reconciliation.EFFECT_INTENT, arguments={"proposal_id": proposal_id})
    return find_active_unresolved_effect(leid) is not None


def _interleaved_expire(monkeypatch, *, b_past_transition: bool, proposal_id: str = ""):
    """Deterministic interleave: B's consume (and optional signed transition) happens INSIDE
    A's expire call, so A's CAS provably loses against a submission in flight."""
    from core.wallet import external_signing, proposals

    real = external_signing.expire_signing_request

    def b_wins_then_a_loses(request_id: str) -> bool:
        external_signing.consume_signing_request(request_id)  # B reaches the submission boundary
        if b_past_transition:
            proposals.transition(proposal_id, proposals.STATE_SIGNED, detail={"signer": "external_signer"}, expected_state=proposals.STATE_AWAITING_SIGNATURE)
        return real(request_id)  # A loses: the row is no longer open

    monkeypatch.setattr(external_signing, "expire_signing_request", b_wins_then_a_loses)
    return real


def test_svm_expiry_cas_loss_never_releases_records_nonexecution_or_overwrites(wallet_env, monkeypatch):
    """A read OPEN; B consumed and sits at the submission boundary; A's expire loses the
    CAS. A must refuse typed with NO side effects: the hold stays, the A6 reservation stays
    active (no guessed non-execution), the proposal's state is not overwritten."""
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import external_signing, limits, proposals
    from core.wallet.errors import WalletFault

    engine, proposal_id, view, key = _svm_open_request(wallet_env)
    _age(view["request_id"])
    signature = key.sign(b58decode(view["message_b58"]))

    _interleaved_expire(monkeypatch, b_past_transition=False, proposal_id=proposal_id)
    with pytest.raises(WalletFault) as exc:
        engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))

    assert exc.value.code == "wallet_duplicate_payment" and exc.value.context["reason"] == "signing_request_in_flight", exc.value.to_dict()
    assert limits.reservation_state(proposal_id) == limits.RESERVATION_RESERVED, "a lost expiry CAS must not release another submission's hold"
    assert _a6_active(proposal_id), "a lost expiry CAS must not record guessed non-execution"
    assert proposals.get_proposal(proposal_id).state == proposals.STATE_AWAITING_SIGNATURE, "a lost expiry CAS must not overwrite the newer state"
    assert wallet_env["rpc"].send_count() == 0


def test_svm_expiry_cas_loss_after_b_signed_keeps_the_hold(wallet_env, monkeypatch):
    """The same loss one boundary later: B consumed AND transitioned to signed (its dispatch
    is imminent). A's expire loses; nothing may release, resolve or move the proposal."""
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault

    engine, proposal_id, view, key = _svm_open_request(wallet_env)
    _age(view["request_id"])
    signature = key.sign(b58decode(view["message_b58"]))

    _interleaved_expire(monkeypatch, b_past_transition=True, proposal_id=proposal_id)
    with pytest.raises(WalletFault):
        engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))
    assert limits.reservation_state(proposal_id) == limits.RESERVATION_RESERVED
    assert _a6_active(proposal_id)
    assert proposals.get_proposal(proposal_id).state == proposals.STATE_SIGNED, "B's newer state survives A's lost expiry"
    assert wallet_env["rpc"].send_count() == 0


@pytest.fixture
def evm_rig(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from core.wallet import chains, config, facilitators

    with ScriptedEvmRpc() as rpc, FacilitatorSimulator(networks=(NETWORK,)) as facilitator:
        monkeypatch.setenv(config.NETWORK_RPC_URLS_ENV, json.dumps({NETWORK: rpc.url}))
        chains.invalidate_chain_identity(None)
        facilitators.discover(facilitator.url, facilitator_id="fac-cas")
        yield rpc, facilitator


def _evm_open_request(rpc, facilitator, signer):
    from core.wallet import custody, lifecycle, x402

    profile = custody.register_external_signer_wallet(signer.address, network=NETWORK)
    with X402V2Resource(facilitator, rpc=rpc, amount_minor=1000) as resource:
        outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        engine = lifecycle.default_lifecycle()
        engine.prepare(outcome.proposal_id)
        view = engine.request_external_signature(outcome.proposal_id)
        return engine, outcome.proposal_id, view, resource


def test_evm_expiry_cas_loss_never_releases_or_records_nonexecution(evm_rig, monkeypatch):
    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault

    rpc, facilitator = evm_rig
    with EvmExtensionSigner() as signer:
        engine, proposal_id, view, resource = _evm_open_request(rpc, facilitator, signer)
        _age(view["request_id"])
        _interleaved_expire(monkeypatch, b_past_transition=False, proposal_id=proposal_id)
        with pytest.raises(WalletFault) as exc:
            engine.submit_external_signature(view["request_id"], signature_hex="0x" + "ab" * 65)
        assert exc.value.code == "wallet_duplicate_payment" and exc.value.context["reason"] == "signing_request_in_flight"
        assert limits.reservation_state(proposal_id) == limits.RESERVATION_RESERVED
        assert _a6_active(proposal_id)
        assert proposals.get_proposal(proposal_id).state == proposals.STATE_AWAITING_SIGNATURE
        assert facilitator.settle_count() == 0 and resource.deliveries == []


def test_a_genuinely_undispatched_expiry_still_releases_on_the_won_cas(wallet_env):
    """Positive control: nobody consumed, the request is honestly expired — the submit door
    WINS the CAS, and only then releases, records non-execution and expires the proposal.
    (Scope: this proves no VOOL-mediated dispatch; it does not revoke a signature the
    user's external wallet may hold independently.)"""
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault

    engine, proposal_id, view, key = _svm_open_request(wallet_env)
    _age(view["request_id"])
    signature = key.sign(b58decode(view["message_b58"]))
    with pytest.raises(WalletFault) as exc:
        engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))
    assert exc.value.code == "wallet_approval_rejected" and exc.value.context["reason"] == "signing_request_expired"
    assert limits.reservation_state(proposal_id) == limits.RESERVATION_RELEASED
    assert not _a6_active(proposal_id), "the won CAS proves non-dispatch: the effect resolves"
    assert proposals.get_proposal(proposal_id).state == proposals.STATE_EXPIRED
    assert wallet_env["rpc"].send_count() == 0


def test_ordinary_submission_and_restart_controls_still_hold(wallet_env):
    """Ordinary-submission control: an unexpired request with a valid answer completes and
    settles once. The restart control (reaper win/loss) is pinned by
    test_wallet_freeze_cancel_reap.py and sabotage S17."""
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import proposals

    engine, proposal_id, view, key = _svm_open_request(wallet_env)
    signature = key.sign(b58decode(view["message_b58"]))
    receipt = engine.submit_external_signature(view["request_id"], signature_b58=b58encode(signature))
    assert receipt.state in {proposals.STATE_CONFIRMED, proposals.STATE_BROADCAST}
    assert wallet_env["rpc"].send_count() == 1

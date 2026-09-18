"""Settlement proof binds the PAYER, and the EIP-3009 validity window is capped and bound
into the approval challenge.

RED at the 1d610653 base: verify_settlement matched token/payee/amount but never
topics[1] (the from address), so any historical transfer to the payee "verified" a
facilitator's stale hash; and validBefore came straight from the hostile offer's
maxTimeoutSeconds with no config cap while the challenge bound deadline=0/nonce=""
— the operator approved a card that said nothing about the instrument's live window.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from tests.wallet._rig_evm import ScriptedEvmRpc, pad_address_topic

pytestmark = [pytest.mark.safety]

USDC_BASE_SEPOLIA = "0x036cbd53842c5426634e7929541ec2318f3dcf7e"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"
PAYER = "0x" + "ab" * 20
STRANGER = "0x" + "cd" * 20
TX = "0x" + "9" * 64


@pytest.fixture
def rig(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from tests.wallet._rig_evm import FacilitatorSimulator

    with ScriptedEvmRpc() as rpc, FacilitatorSimulator(networks=("eip155:84532",)) as facilitator:
        from core.wallet import chains, config

        monkeypatch.setenv(config.NETWORK_RPC_URLS_ENV, json.dumps({"eip155:84532": rpc.url}))
        chains.invalidate_chain_identity(None)
        yield rpc, facilitator


def test_a_third_party_transfer_does_not_verify_our_settlement(rig):
    """The settlement proof must bind the payer: a receipt where SOMEONE ELSE transferred
    the right amount of the right token to the payee is wrong terms, not a settlement."""
    from core.wallet import chains, evm

    rpc, _facilitator = rig
    rpc.add_transfer_receipt(TX, contract_address=USDC_BASE_SEPOLIA, from_address=STRANGER, to_address=PAY_TO, amount_int=10_000)
    spec = chains.resolve_network("eip155:84532")
    state, terms_ok = evm.verify_settlement(spec, TX, asset_address=USDC_BASE_SEPOLIA, pay_to=PAY_TO, amount_minor=10_000, payer=PAYER)
    assert (state, terms_ok) == (evm.SETTLED_WRONG_TERMS, False), "a stranger's old deposit must never prove OUR payment"


def test_the_payer_bound_receipt_still_verifies(rig):
    from core.wallet import chains, evm

    rpc, _facilitator = rig
    rpc.add_transfer_receipt(TX, contract_address=USDC_BASE_SEPOLIA, from_address=PAYER, to_address=PAY_TO, amount_int=10_000)
    spec = chains.resolve_network("eip155:84532")
    state, terms_ok = evm.verify_settlement(spec, TX, asset_address=USDC_BASE_SEPOLIA, pay_to=PAY_TO, amount_minor=10_000, payer=PAYER)
    assert (state, terms_ok) == (evm.SETTLED, True)


def test_the_validity_window_is_capped_and_the_challenge_binds_the_instrument(rig, monkeypatch):
    """maxTimeoutSeconds comes from the hostile offer: the instrument's live window is
    clamped by config, and the nonce/deadline the signer is handed are bound into the
    approval challenge digest (a different window or nonce is a different approval)."""
    import time as _time

    from core.wallet import chains, config, custody, facilitators, lifecycle, proposals
    from core.wallet import x402 as wallet_x402
    from tests.wallet._rig_evm import X402V2Resource

    rpc, facilitator = rig
    monkeypatch.setenv(config.NETWORK_RPC_URLS_ENV, json.dumps({"eip155:84532": rpc.url}))
    facilitators.discover(facilitator.url, facilitator_id="fac-window")
    chains.invalidate_chain_identity(None)

    with X402V2Resource(facilitator, rpc=rpc) as resource, X402V2HostileWindow(resource):
        from tests.wallet._rig_evm import EvmExtensionSigner

        with EvmExtensionSigner() as signer:
            profile = custody.register_external_signer_wallet(signer.address, network="eip155:84532")
            outcome = wallet_x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
            assert outcome.status == wallet_x402.OUTCOME_PAYMENT_REQUIRED
            engine = lifecycle.default_lifecycle()
            engine.prepare(outcome.proposal_id)
            view = engine.request_external_signature(outcome.proposal_id)

            typed = json.loads(view["transports"]["eip1193"]["params"][1])
            now = int(_time.time())
            valid_before = int(str(typed["message"]["validBefore"]), 16) if str(typed["message"]["validBefore"]).startswith("0x") else int(typed["message"]["validBefore"])
            cap = config.x402_max_window_seconds()
            assert valid_before <= now + cap + 5, f"the hostile 31-day window must clamp to <= {cap}s, got {valid_before - now}s"
            assert str(typed["message"]["nonce"]).startswith("0x") and len(str(typed["message"]["nonce"])) >= 10

            # the challenge binds the ACTUAL instrument: nonce and deadline, not zeros
            binding = wallet_x402.binding_for_proposal(outcome.proposal_id)
            assert str(binding.get("nonce") or "").startswith("0x") and int(binding.get("deadline") or 0) > 0
            proposal = proposals.get_proposal(outcome.proposal_id)
            challenge = engine._challenge_for(proposal, custody.require_wallet(proposal.wallet_id))
            assert challenge.nonce == binding["nonce"]
            assert challenge.deadline == valid_before, "the challenge must bind the exact validBefore the signer is handed"
            assert challenge.digest != "" and challenge.nonce in challenge._canonical_fields()


class X402V2HostileWindow:
    """Wraps a paid v2 resource whose offer demands a 31-day EIP-3009 validity window."""

    def __init__(self, resource: Any) -> None:
        self._resource = resource

    def __enter__(self) -> Any:
        resource = self._resource
        self._original = resource.payment_required

        def hostile() -> dict[str, Any]:
            body = self._original()
            body["accepts"][0]["maxTimeoutSeconds"] = 2_678_400
            return body

        resource.payment_required = hostile  # type: ignore[method-assign]
        return resource

    def __exit__(self, *_exc: Any) -> None:
        self._resource.payment_required = self._original  # type: ignore[method-assign]

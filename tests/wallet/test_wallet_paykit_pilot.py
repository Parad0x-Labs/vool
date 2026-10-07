"""A pay-kit payment never moves through the Crypto Pilot lane.

The Pilot lane pays a plain transfer from its quote sheet. A pay-kit payment reaches its resource only as the request
pay-kit builds and the resource settles, so a plain transfer to the same payee would buy nothing. A Pilot wallet (and a
row whose native coin moves only through that lane) is refused before any proposal; a pay-kit proposal that reached
such a row anyway is refused by every door before any quote, credential or send.
"""
from __future__ import annotations

import pytest

from tests.wallet._rig_paykit import ScriptedMppResource, ScriptedPayKitResource
from tests.wallet.test_crypto_pilot_dispatch import (
    PIN,
    SOLANA_DEVNET,
    _ready_pilot_wallet,
    _sol_key,
    node,
    pilot,
)

pytest.importorskip("solana_pay_kit", reason="the pay-kit lane is the optional `pay` extra")

pytestmark = [pytest.mark.safety]
AMOUNT = 500_000


@pytest.fixture
def capped(pilot, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", str(AMOUNT * 2))
    return pilot


@pytest.mark.parametrize("protocol", ["x402", "mpp"])
def test_a_pilot_wallet_is_refused_before_any_proposal(capped, protocol):
    from core.wallet import paykit_x402, proposals
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    payee = _sol_key()
    resource = ScriptedPayKitResource(capped, amount_minor=AMOUNT, pay_to=payee) if protocol == "x402" else ScriptedMppResource(capped, amount_minor=AMOUNT, recipient=payee)
    with resource:
        with pytest.raises(WalletFault) as exc:
            paykit_x402.fetch_paid(resource.url, wallet_id=wallet["wallet_id"], method="GET", body=b"")
        assert exc.value.code == "x402_scheme_unavailable" and exc.value.context["reason"] == "paykit_pilot_lane_not_supported"
        assert proposals.list_proposals() == [] and resource.paid_requests == []
    assert capped.send_count() == 0


def test_every_door_refuses_a_paykit_proposal_on_a_pilot_wallet(capped):
    """However a pay-kit proposal reached a Pilot wallet, no door pays it: the quote sheet mints nothing, the Pilot
    approval refuses before the credential, and the legacy door names no sheet to go to."""
    from core.wallet import approval, lifecycle, proposals, quotes
    from core.wallet.errors import WalletFault

    wallet = _ready_pilot_wallet()
    proposal = proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=_sol_key(), amount_minor=AMOUNT, asset="SOL",
                                             origin=proposals.ORIGIN_X402_PAYKIT, network=SOLANA_DEVNET)
    engine = lifecycle.default_lifecycle()
    assert engine.prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    with pytest.raises(WalletFault) as quote:
        quotes.mint_quote(proposal.proposal_id)
    assert quote.value.context["reason"] == "paykit_pilot_lane_not_supported"
    with pytest.raises(WalletFault) as pilot_door:
        engine.approve_pilot_transfer(proposal.proposal_id, quote_id="q-none", quote_digest="0" * 64, approver=approval.PinApprover(PIN))
    assert pilot_door.value.context["reason"] == "paykit_pilot_lane_not_supported"
    with pytest.raises(WalletFault) as legacy:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert legacy.value.context["reason"] == "paykit_pilot_lane_not_supported"
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert capped.send_count() == 0

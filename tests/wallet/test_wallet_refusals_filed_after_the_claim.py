"""A wallet refusal raised inside a claim's write transaction is filed, not lost.

The fault plane files each refusal on its own connection to the same database. Filed while a claim's BEGIN IMMEDIATE
still held the write lock, the record waited out the store's busy timeout (about five seconds, the approval sheet
frozen meanwhile) and was then dropped: the refused approval left no fault record and no security observation. A
refusal made inside a wallet transaction is now filed the moment that transaction ends, before it reaches its caller.

SIMULATED CHAIN for the Crypto Pilot case: the scripted node answers as Solana Devnet.
"""
from __future__ import annotations

import pytest

from core.wallet.errors import WalletFault
from tests.wallet.test_crypto_pilot_dispatch import (
    _approve,
    _hold,
    _pending,
    _quote,
    _ready_pilot_wallet,
    node,  # the scripted node the pilot fixture runs on
    pilot,  # Crypto on, Test networks, Solana Devnet's lane ready
)

pytestmark = [pytest.mark.safety]


def test_a_refusal_made_inside_a_wallet_transaction_is_filed_when_the_transaction_ends(wallet_env):
    from core.faults.recorder import fault_by_id
    from core.wallet import limits
    from core.wallet.security import wallet_fault
    from core.wallet.store import connection

    outside = wallet_fault("wallet_limit_exceeded", authority="core.wallet.lifecycle", context={"reason": "outside_any_transaction"})
    assert outside.fault_id and fault_by_id(outside.fault_id) is not None, "outside a transaction it is filed at once"

    with pytest.raises(WalletFault) as refused:
        with connection() as conn:
            limits._begin_immediate(conn)
            with connection():  # a nested wallet connection closing does not file it while the claim is still open
                pass
            inside = wallet_fault("wallet_limit_exceeded", authority="core.wallet.lifecycle", context={"reason": "inside_the_claim"})
            assert inside.fault_id == "", "not filed while the claim holds the write lock"
            raise inside
    assert refused.value.fault_id, "filed once the claim rolled back, before the refusal reached its caller"
    record = fault_by_id(refused.value.fault_id)
    assert record is not None and record.code == "wallet_limit_exceeded" and record.context.get("reason") == "inside_the_claim"


def test_a_crypto_pilot_claim_refused_by_a_freeze_files_its_fault_record(pilot, monkeypatch):
    """The owner hits Freeze between the PIN and the claim: the claim's own transaction refuses. The refusal is filed
    with its proposal, so the owner's record says why nothing was sent."""
    from core.faults.recorder import fault_by_id
    from core.wallet import limits, pilot_custody, proposals, quotes

    node = pilot
    wallet = _ready_pilot_wallet()
    engine, proposal = _pending(wallet)
    quote = _quote(proposal.proposal_id)
    real_verify = pilot_custody._verify

    def verify_then_freeze(row, credential, *, source_context=None):
        secret = real_verify(row, credential, source_context=source_context)
        limits.set_frozen(True)
        return secret

    monkeypatch.setattr(pilot_custody, "_verify", verify_then_freeze)
    try:
        with pytest.raises(WalletFault) as frozen:
            _approve(engine, proposal.proposal_id, quote)
    finally:
        limits.set_frozen(False)
    assert (frozen.value.code, frozen.value.context.get("reason")) == ("wallet_limit_exceeded", "panic_freeze_blocks_pilot_approval")
    record = fault_by_id(frozen.value.fault_id)
    assert record is not None and record.context.get("proposal_id") == proposal.proposal_id and record.context.get("reason") == "panic_freeze_blocks_pilot_approval"
    assert _hold(proposal.proposal_id) is None and quotes.get_quote(quote["quote_id"])["state"] == "open"
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL and node.send_count() == 0

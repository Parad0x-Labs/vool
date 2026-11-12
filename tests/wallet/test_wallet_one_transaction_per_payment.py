"""One transaction per payment: two payments never sign, send or count ONE Solana transaction.

A Solana transfer this wallet builds is a pure function of the payer, the payee, the amount and the recent blockhash.
Two payments that agree on all four (two invoices of the same amount to the same payee, approved before the chain
moved on) are byte-identical; signed by one key they are ONE transaction, which the chain runs once while both
proposals would read as paid and both would count against the limits. Each payment's exact message is bound to it when
it is claimed, and a message another payment holds is never claimed: the claim rolls back and the approval reads the
chain again until the next blockhash makes the payment distinct; when the chain does not move on in time it is refused
with nothing held. A spend limit answers first, without waiting. A payment whose bytes never left frees its message.

SIMULATED CHAIN: the scripted node answers as Solana Devnet and stands still until a test moves it on. It moves on
about every 400 ms on a real cluster, so the refusal paths shorten the wallet's wait; the rule itself is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import threading

import pytest

from tests.wallet._rig import DESTINATION, DEVNET, RIG_BLOCKHASH, ExtensionSigner

pytestmark = [pytest.mark.safety]

PIN = "246810"
AMOUNT = 47_000


@pytest.fixture
def short_wait(monkeypatch):
    from core.wallet import lifecycle

    monkeypatch.setattr(lifecycle, "_DISTINCT_MESSAGE_BUDGET_SECONDS", 0.3)
    monkeypatch.setattr(lifecycle, "_DISTINCT_MESSAGE_POLL_SECONDS", 0.05)


@pytest.fixture
def chain(wallet_env):
    rpc = wallet_env["rpc"]
    rpc.real_signature = True  # the node answers each transaction's own signature, as a real node does
    return rpc


@pytest.fixture
def ext():
    with ExtensionSigner() as signer:
        yield signer


def _pocket():
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _pending(wallet_id: str, reference: str):
    """A payment of AMOUNT to DESTINATION awaiting approval; ``reference`` makes it a payment of its own (an invoice)."""
    from core.wallet import lifecycle, proposals

    proposal = proposals.propose_transaction(wallet_id=wallet_id, destination=DESTINATION, amount_minor=AMOUNT, asset="SOL", origin=proposals.ORIGIN_USER, idempotency_key=reference)
    assert lifecycle.default_lifecycle().prepare(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    return proposal


def _approve(proposal_id: str):
    from core.wallet import approval, lifecycle

    return lifecycle.default_lifecycle().approve_and_execute(proposal_id, approver=approval.PinApprover(PIN))


def _counted() -> dict[str, tuple[int, str]]:
    """Every hold the spend limits keep, by proposal: (amount, state); a released hold counts nothing."""
    from core.wallet.store import connection

    with connection() as conn:
        return {str(row[0]): (int(row[1]), str(row[2])) for row in conn.execute("SELECT proposal_id, amount_minor, state FROM wallet_spend_ledger").fetchall()}


def _blockhashes(rpc) -> list[str]:
    from solders.transaction import Transaction

    return [str(Transaction.from_bytes(raw).message.recent_blockhash) for raw in rpc.sent]


def _same_transaction(fault, other_proposal_id: str, *, code: str = "wallet_quote_expired") -> None:
    assert (fault.code, fault.context.get("reason"), fault.context.get("other_proposal_id")) == (code, "transaction_matches_another_payment", other_proposal_id)


def _filed(proposal_id: str) -> list[tuple[str, str]]:
    """The fault records filed for one proposal: (code, reason)."""
    from core.wallet.store import connection

    with connection() as conn:
        contexts = [(str(row[0]), json.loads(row[1] or "{}")) for row in conn.execute("SELECT code, context_json FROM fault_records").fetchall()]
    return [(code, str(context.get("reason") or "")) for code, context in contexts if context.get("proposal_id") == proposal_id]


# --- the PIN door ------------------------------------------------------------------------------------------------------

def test_two_equal_payments_in_one_slot_are_never_one_transaction_counted_twice(chain, short_wait):
    from core.wallet import proposals, receipts
    from core.wallet.errors import WalletFault

    wallet_id = _pocket().wallet_id
    first, second = _pending(wallet_id, "invoice-week-01"), _pending(wallet_id, "invoice-week-02")
    paid = _approve(first.proposal_id)
    assert paid.state == proposals.STATE_CONFIRMED and chain.send_count() == 1

    with pytest.raises(WalletFault) as refused:
        _approve(second.proposal_id)
    _same_transaction(refused.value, first.proposal_id)
    assert _filed(second.proposal_id) == [("wallet_quote_expired", "transaction_matches_another_payment")]
    assert proposals.get_proposal(second.proposal_id).state == proposals.STATE_PENDING_APPROVAL, "still the owner's to approve"
    assert _counted() == {first.proposal_id: (AMOUNT, "settled")}, "nothing held or counted for the refused payment"
    assert chain.send_count() == 1 and not [r for r in receipts.list_receipts() if r["proposal_id"] == second.proposal_id and r["state"] == proposals.STATE_CONFIRMED]

    chain.advance_blockhash()  # the cluster moves on: the same approval now signs this payment's own transaction
    again = _approve(second.proposal_id)
    assert again.state == proposals.STATE_CONFIRMED and again.tx_signature != paid.tx_signature
    assert chain.recorded_signatures() == {paid.tx_signature, again.tx_signature} and chain.distinct_transactions() == chain.send_count() == 2
    assert _blockhashes(chain) == [RIG_BLOCKHASH, chain.blockhash]
    assert _counted() == {first.proposal_id: (AMOUNT, "settled"), second.proposal_id: (AMOUNT, "settled")}, "two transfers on the chain, each counted once"


def test_an_equal_payment_waits_for_the_chain_to_move_on_and_then_sends_its_own_transaction(chain, monkeypatch):
    from core.wallet import lifecycle, proposals

    monkeypatch.setattr(lifecycle, "_DISTINCT_MESSAGE_POLL_SECONDS", 0.05)  # the shipped wait budget, read more often
    holders: list[str] = []  # each claim's binding: '' when it bound, else the payment that holds the message
    bind = proposals._claim_message

    def recorded(conn, proposal_id: str, *, network: str, message_digest: str) -> None:
        try:
            bind(conn, proposal_id, network=network, message_digest=message_digest)
        except proposals.MessageClaimedError as taken:
            holders.append(taken.other_proposal_id)
            raise
        holders.append("")

    monkeypatch.setattr(proposals, "_claim_message", recorded)
    wallet_id = _pocket().wallet_id
    first, second = _pending(wallet_id, "rent-november"), _pending(wallet_id, "rent-december")
    paid = _approve(first.proposal_id)
    assert holders == [""]
    holders.clear()

    chain.blockhash_moves_after_reads = 1  # the cluster moves on just after the approval's first read
    again = _approve(second.proposal_id)
    assert holders == [first.proposal_id, ""], "its claim rolled back, then it read the chain again until this payment was a transaction of its own"
    assert again.state == proposals.STATE_CONFIRMED and again.tx_signature != paid.tx_signature
    assert chain.distinct_transactions() == chain.send_count() == 2 and _blockhashes(chain) == [RIG_BLOCKHASH, chain.blockhash]
    assert _counted() == {first.proposal_id: (AMOUNT, "settled"), second.proposal_id: (AMOUNT, "settled")}


def test_two_approvals_racing_for_one_transaction_bind_it_to_one_payment(chain, monkeypatch, short_wait):
    """Both approvals build the same transaction before either claims (forced here): the claim's own write transaction
    binds it to one of them. The other's claim rolls back and waits for the chain, which stands still here, and it is
    refused with nothing held."""
    from core.wallet import lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    both_built = threading.Barrier(2, timeout=30)
    first_build = threading.local()
    build = lifecycle._build_message

    def built_together(*args, **kwargs):
        message = build(*args, **kwargs)
        if not getattr(first_build, "done", False):
            first_build.done = True
            both_built.wait()
        return message

    wallet_id = _pocket().wallet_id
    racing = [_pending(wallet_id, "split-bill-a"), _pending(wallet_id, "split-bill-b")]
    monkeypatch.setattr(lifecycle, "_build_message", built_together)
    outcomes: dict[str, object] = {}

    def approve(proposal) -> None:
        try:
            outcomes[proposal.proposal_id] = _approve(proposal.proposal_id).state
        except WalletFault as exc:
            outcomes[proposal.proposal_id] = exc

    threads = [threading.Thread(target=approve, args=(proposal,)) for proposal in racing]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    winners = [pid for pid, outcome in outcomes.items() if outcome == proposals.STATE_CONFIRMED]
    losers = [pid for pid, outcome in outcomes.items() if isinstance(outcome, WalletFault)]
    assert len(winners) == 1 and len(losers) == 1, outcomes
    _same_transaction(outcomes[losers[0]], winners[0])
    assert _filed(losers[0]) == [("wallet_quote_expired", "transaction_matches_another_payment")], "filed once the claim's write lock was free, not lost"
    assert proposals.get_proposal(losers[0]).state == proposals.STATE_PENDING_APPROVAL and limits.reservation_state(losers[0]) == ""
    assert chain.send_count() == 1 and _counted() == {winners[0]: (AMOUNT, "settled")}


def test_a_spend_limit_refuses_an_equal_payment_at_once_without_waiting_for_the_chain(chain):
    """Under a daily limit of one such payment, the second equal payment is refused by the limit, at once and with its
    receipt: the owner is never kept waiting for a chain that could only bring the same refusal."""
    import time

    from core.wallet import limits, proposals
    from core.wallet.errors import WalletFault

    wallet_id = _pocket().wallet_id
    limits.set_limits(wallet_id, "SOL", limits.SpendLimits(per_tx_minor=1_000_000, daily_minor=AMOUNT + 5_000, per_destination_daily_minor=1_000_000))
    first, second = _pending(wallet_id, "subscription-a"), _pending(wallet_id, "subscription-b")
    assert _approve(first.proposal_id).state == proposals.STATE_CONFIRMED
    started = time.monotonic()
    with pytest.raises(WalletFault) as refused:
        _approve(second.proposal_id)
    waited = time.monotonic() - started
    assert refused.value.code == "wallet_limit_exceeded", refused.value.context
    assert waited < 2.0, f"refused after {waited:.2f}s: it waited for the chain before the limit answered"
    assert proposals.get_proposal(second.proposal_id).state == proposals.STATE_REJECTED
    assert chain.send_count() == 1 and _counted() == {first.proposal_id: (AMOUNT, "settled")}


def test_a_payment_whose_bytes_never_left_frees_its_transaction_for_an_equal_one(chain, monkeypatch, short_wait):
    """The first payment is claimed, then its signing fails: it ends with nothing sent. Its transaction never left this
    device and never will, so an equal payment approved right after, before the chain moves on, signs and sends it."""
    from core.wallet import proposals, signers
    from core.wallet.errors import WalletFault

    real = signers.signer_for
    failed: list[bool] = []

    class _FailsOnce:
        def __init__(self, inner):
            self._inner = inner
            self.public_key = inner.public_key

        def sign(self, message):
            if not failed:
                failed.append(True)
                raise WalletFault("wallet_signing_unavailable", context={"reason": "signer_unavailable"})
            return self._inner.sign(message)

    wallet_id = _pocket().wallet_id
    first, second = _pending(wallet_id, "deposit-a"), _pending(wallet_id, "deposit-b")
    monkeypatch.setattr(signers, "signer_for", lambda *a, **k: _FailsOnce(real(*a, **k)))
    with pytest.raises(WalletFault) as unsigned:
        _approve(first.proposal_id)
    assert unsigned.value.code == "wallet_signing_unavailable" and chain.send_count() == 0
    assert proposals.get_proposal(first.proposal_id).state == proposals.STATE_FAILED
    paid = _approve(second.proposal_id)
    assert paid.state == proposals.STATE_CONFIRMED and chain.send_count() == 1
    assert _blockhashes(chain) == [RIG_BLOCKHASH], "the same blockhash: the first payment's transaction, never sent, is the second's"
    assert _counted() == {first.proposal_id: (AMOUNT, "released"), second.proposal_id: (AMOUNT, "settled")}, "the first holds nothing"


# --- the external signer door ------------------------------------------------------------------------------------------

def _external_proposal(wallet_id: str):
    from core.wallet import lifecycle, proposals

    proposal = proposals.propose_transaction(wallet_id=wallet_id, destination=DESTINATION, amount_minor=AMOUNT, asset="SOL", origin=proposals.ORIGIN_USER)
    lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    return proposal


def _sign_and_submit(ext, request: dict):
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import lifecycle

    signature = ext.sign_message(b58decode(request["message_b58"]))
    return lifecycle.default_lifecycle().submit_external_signature(request["request_id"], signature_b58=b58encode(signature))


def test_an_external_signer_is_never_handed_one_transaction_for_two_payments(chain, ext, short_wait):
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    profile = custody.register_external_signer_wallet(ext.public_key, network=DEVNET, label="phantom")
    engine = lifecycle.default_lifecycle()
    first, second = _external_proposal(profile.wallet_id), _external_proposal(profile.wallet_id)
    request = engine.request_external_signature(first.proposal_id)
    with pytest.raises(WalletFault) as refused:
        engine.request_external_signature(second.proposal_id)
    _same_transaction(refused.value, first.proposal_id)
    assert proposals.get_proposal(second.proposal_id).state == proposals.STATE_PENDING_APPROVAL and second.proposal_id not in _counted()
    assert ext.signed == [], "no second copy of the bytes was handed to the wallet"

    paid = _sign_and_submit(ext, request)
    assert paid.state == proposals.STATE_CONFIRMED
    chain.advance_blockhash()
    own = engine.request_external_signature(second.proposal_id)
    assert own["message_b58"] != request["message_b58"]
    again = _sign_and_submit(ext, own)
    assert again.state == proposals.STATE_CONFIRMED and again.tx_signature != paid.tx_signature
    assert chain.distinct_transactions() == chain.send_count() == 2
    assert _counted() == {first.proposal_id: (AMOUNT, "settled"), second.proposal_id: (AMOUNT, "settled")}


def test_one_signer_registered_as_two_wallets_never_signs_one_transaction_for_both(chain, ext, short_wait):
    """The same extension key added twice (two wallets, one key): the payer of both is that key, so equal payments from
    the two wallets are the same transaction. The binding is per chain, never per wallet."""
    from core.wallet import custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    one = custody.register_external_signer_wallet(ext.public_key, network=DEVNET, label="phantom")
    two = custody.register_external_signer_wallet(ext.public_key, network=DEVNET, label="phantom again")
    assert one.wallet_id != two.wallet_id
    engine = lifecycle.default_lifecycle()
    first, second = _external_proposal(one.wallet_id), _external_proposal(two.wallet_id)
    request = engine.request_external_signature(first.proposal_id)
    with pytest.raises(WalletFault) as refused:
        engine.request_external_signature(second.proposal_id)
    _same_transaction(refused.value, first.proposal_id)
    assert proposals.get_proposal(second.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert _sign_and_submit(ext, request).state == proposals.STATE_CONFIRMED and chain.send_count() == 1
    assert _counted() == {first.proposal_id: (AMOUNT, "settled")}


# --- the store an earlier release wrote -----------------------------------------------------------------------------

def test_a_wallet_store_from_before_the_binding_gains_it_and_pays_a_proposal_it_already_held(chain):
    """An install upgraded with a payment awaiting approval: the first connection adds the column and its unique index
    in place, the waiting payment keeps no binding until it is claimed, and its claim binds exactly the bytes it sent."""
    from solders.transaction import Transaction

    from core.wallet import proposals
    from core.wallet.store import _raw_conn, connection

    waiting = _pending(_pocket().wallet_id, "before-the-upgrade")
    raw = _raw_conn()  # the wallet store as the previous release left it: no message_digest column, no index on it
    try:
        raw.execute("DROP INDEX idx_wallet_proposals_message")
        raw.execute("ALTER TABLE wallet_proposals DROP COLUMN message_digest")
        raw.commit()
        assert "message_digest" not in {row[1] for row in raw.execute("PRAGMA table_info(wallet_proposals)").fetchall()}
    finally:
        raw.close()

    with connection() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(wallet_proposals)").fetchall()}
        unique = {row[1]: int(row[2]) for row in conn.execute("PRAGMA index_list(wallet_proposals)").fetchall()}
        kept = conn.execute("SELECT message_digest FROM wallet_proposals WHERE proposal_id = ?", (waiting.proposal_id,)).fetchone()[0]
    assert "message_digest" in columns and unique.get("idx_wallet_proposals_message") == 1 and kept == ""

    assert _approve(waiting.proposal_id).state == proposals.STATE_CONFIRMED and chain.send_count() == 1
    with connection() as conn:
        bound = conn.execute("SELECT message_digest FROM wallet_proposals WHERE proposal_id = ?", (waiting.proposal_id,)).fetchone()[0]
    assert bound == hashlib.sha256(bytes(Transaction.from_bytes(chain.sent[0]).message)).hexdigest(), "the binding is the exact transaction that left"

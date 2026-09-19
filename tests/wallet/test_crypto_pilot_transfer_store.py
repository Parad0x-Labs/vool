"""Crypto Pilot, stage 5 step 2: the transfer store and its projection (delivery/STAGE5-DISPATCH-RECEIPTS-DESIGN-v5.md §1, §2.1–§2.2).

One transfer row per proposal. Every transition is one transaction on the caller's connection: the transfer CAS, the
proposal projection, the hold and a receipt. These tests pin every row of the projection, refuse every other edge, and
cover the worst cases: a cancel request racing a release, a foreign owner token, a rollback mid-transition, a transfer
the owner stopped waiting on that must keep counting against the caps, and nonce and signature reuse.

Assumed API (step 2):
    core.wallet.transfers.insert_claim(conn, *, proposal, quote_id, quote_digest, challenge_digest, environment, family, from_address,
        fee_max_minor, owner_token, lease_until, dispatch_deadline, epochs, enabled_generation, now, nonce=None, ...) -> dict
    core.wallet.transfers.transition(conn, proposal_id, new_state, *, expected_state, now, owner_token=None, charged_fee_minor=None,
        detail=None, **columns) -> dict
    core.wallet.transfers.get_transfer(conn, proposal_id); PROJECTION; TransferTransitionError; the STATE_* names
    core.wallet.limits._resettle(conn, proposal_id, *, charged_fee_minor, amount_moved)
    core.wallet.proposals._get(conn, proposal_id)
"""
from __future__ import annotations

import itertools
import json
import sqlite3
import time
import uuid

import pytest

from tests.wallet._rig import DESTINATION

pytestmark = [pytest.mark.safety]

PIN = "730461"
_PATH = ("proposed", "simulated", "limits_checked", "pending_approval")


class _AbortError(Exception):
    pass


def _pending_proposal(amount_minor: int = 4_000):
    from core.wallet import custody, proposals

    profile = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile
    proposal = proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=amount_minor, asset="SOL", origin=proposals.ORIGIN_USER)
    current = proposal.state
    for following in _PATH[_PATH.index(current) + 1 :]:
        assert proposals.transition(proposal.proposal_id, following, expected_state=current) is not None
        current = following
    return proposals.get_proposal(proposal.proposal_id)


def _claim(proposal, *, family: str = "svm", nonce: int | None = None, fee_max: int = 5_000, from_address: str = "from-account"):
    from core.wallet import controls, limits, transfers
    from core.wallet.store import connection

    now = time.time()
    with connection() as conn:
        limits._begin_immediate(conn)
        return transfers.insert_claim(
            conn, proposal=proposal, quote_id=f"q-{uuid.uuid4().hex}", quote_digest="quote-digest", challenge_digest="challenge-digest",
            environment="testnet", family=family, from_address=from_address, fee_max_minor=fee_max, owner_token="token-1",
            lease_until=now + 30, dispatch_deadline=now + 60, epochs=controls.epochs(conn), enabled_generation=0, now=now, nonce=nonce,
        )


def _move(proposal_id: str, new_state: str, expected_state: str, **kwargs):
    from core.wallet import transfers
    from core.wallet.store import connection

    with connection() as conn:
        return transfers.transition(conn, proposal_id, new_state, expected_state=expected_state, now=time.time(), **kwargs)


def _walk(proposal_id: str, *steps: tuple[str, dict]) -> None:
    from core.wallet import transfers
    from core.wallet.store import connection

    for new_state, kwargs in steps:
        with connection() as conn:
            current = transfers.get_transfer(conn, proposal_id)["state"]
        _move(proposal_id, new_state, current, **kwargs)


def _row(proposal_id: str) -> dict:
    from core.wallet import transfers
    from core.wallet.store import connection

    with connection() as conn:
        return transfers.get_transfer(conn, proposal_id)


def _hold(proposal_id: str):
    from core.wallet.store import connection

    with connection() as conn:
        row = conn.execute("SELECT amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return tuple(row) if row is not None else None


def _receipts(proposal_id: str) -> list[dict]:
    from core.wallet.store import connection

    with connection() as conn:
        rows = conn.execute("SELECT receipt_id, state, tx_signature, payload_json FROM wallet_receipts WHERE proposal_id = ? ORDER BY rowid", (proposal_id,)).fetchall()
    return [{"receipt_id": r[0], "state": r[1], "tx_signature": r[2], "payload": json.loads(r[3] or "{}")} for r in rows]


def _proposal_state(proposal_id: str) -> str:
    from core.wallet import proposals

    return proposals.get_proposal(proposal_id).state


SIGNED = ("signed", {"tx_id": "sig-1", "raw_b64": "cmF3LWJ5dGVz"})


# --- the claim and the dispatch path ------------------------------------------------------------------------

def test_a_claim_moves_the_proposal_holds_amount_and_fee_maximum_and_writes_one_receipt(wallet_env):
    proposal = _pending_proposal()
    row = _claim(proposal)
    assert (row["state"], _proposal_state(proposal.proposal_id), _hold(proposal.proposal_id)) == ("claimed", "approved", (4_000, 5_000, "reserved"))
    receipts = _receipts(proposal.proposal_id)
    assert [(r["state"], r["payload"]["transfer_state"]) for r in receipts] == [("approved", "claimed")]
    assert row["last_receipt_id"] == receipts[-1]["receipt_id"]


def test_a_second_claim_on_the_same_proposal_writes_nothing(wallet_env):
    from core.wallet import proposals

    proposal = _pending_proposal()
    _claim(proposal)
    with pytest.raises(proposals.ProposalTransitionError):
        _claim(proposal)
    assert (_hold(proposal.proposal_id), len(_receipts(proposal.proposal_id))) == ((4_000, 5_000, "reserved"), 1)


def test_a_transfer_that_lands_projects_every_step_and_settles_what_the_chain_charged(wallet_env):
    from core.wallet import proposals

    proposal = _pending_proposal()
    _claim(proposal)
    _walk(proposal.proposal_id, SIGNED, ("dispatching", {}), ("unknown", {"evidence_kind": "submitted"}), ("pending", {}),
          ("confirmed", {"charged_fee_minor": 4_321, "block_ref": 88}))
    row = _row(proposal.proposal_id)
    current = proposals.get_proposal(proposal.proposal_id)
    assert (row["state"], row["raw_b64"], current.state, current.tx_signature) == ("confirmed", None, "confirmed", "sig-1")
    assert _hold(proposal.proposal_id) == (4_000, 4_321, "settled")
    receipts = _receipts(proposal.proposal_id)
    assert [r["payload"]["transfer_state"] for r in receipts] == ["claimed", "signed", "dispatching", "unknown", "pending", "confirmed"]
    assert [r["state"] for r in receipts] == ["approved", "signed", "broadcast", "broadcast", "broadcast", "confirmed"]
    assert receipts[-1]["tx_signature"] == "sig-1" and receipts[3]["payload"]["evidence_kind"] == "submitted"


@pytest.mark.parametrize(
    ("steps", "final", "proposal_state", "reason"),
    [
        ((("released", {}),), "released", "failed", "approval_not_completed"),
        ((SIGNED, ("signed_revoked", {}), ("released", {})), "released", "failed", "signed_not_sent"),
        ((SIGNED, ("signed_revoked", {}), ("discarded", {})), "discarded", "failed", "signed_not_sent"),
        ((("cancelled", {}),), "cancelled", "rejected", "owner_cancelled_before_signing"),
        ((SIGNED, ("cancelled", {})), "cancelled", "rejected", "owner_cancelled_before_sending"),
        ((SIGNED, ("signed_revoked", {}), ("cancelled", {})), "cancelled", "rejected", "owner_cancelled_before_sending"),
    ],
)
def test_every_never_sent_ending_releases_the_hold_drops_the_bytes_and_says_why(wallet_env, steps, final, proposal_state, reason):
    from core.wallet import proposals

    proposal = _pending_proposal()
    _claim(proposal)
    _walk(proposal.proposal_id, *steps)
    row = _row(proposal.proposal_id)
    assert (row["state"], row["raw_b64"], _proposal_state(proposal.proposal_id), _hold(proposal.proposal_id)[2]) == (final, None, proposal_state, "released")
    assert proposals.proposal_events(proposal.proposal_id)[-1]["detail"]["reason"] == reason
    payload = _receipts(proposal.proposal_id)[-1]["payload"]
    assert (payload["transfer_state"], payload["reason"], payload["evidence_kind"]) == (final, reason, "")


def test_a_transfer_that_failed_on_chain_counts_only_its_fee(wallet_env):
    proposal = _pending_proposal()
    _claim(proposal)
    _walk(proposal.proposal_id, SIGNED, ("dispatching", {}), ("pending", {}), ("failed_on_chain", {"charged_fee_minor": 2_000}))
    assert (_row(proposal.proposal_id)["state"], _proposal_state(proposal.proposal_id), _hold(proposal.proposal_id)) == ("failed_on_chain", "failed", (0, 2_000, "settled"))


# --- refusals ----------------------------------------------------------------------------------------------

def test_every_edge_outside_the_projection_table_is_refused_before_anything_is_read(wallet_env):
    from core.wallet import transfers

    states = [value for name, value in vars(transfers).items() if name.startswith("STATE_")]
    refused = 0
    for old, new in itertools.product(states, states):
        if (old, new) in transfers.PROJECTION:
            continue
        with pytest.raises(transfers.TransferTransitionError, match=f"edge_not_allowed:{old}->{new}"):
            _move("no-such-transfer", new, old)
        refused += 1
    assert refused == len(states) ** 2 - len(transfers.PROJECTION)
    assert ("unknown", "released") not in transfers.PROJECTION and ("pending", "released") not in transfers.PROJECTION, "no release after a dispatch"


def test_a_foreign_owner_token_or_a_stale_expected_state_moves_nothing(wallet_env):
    from core.wallet import transfers

    proposal = _pending_proposal()
    _claim(proposal)
    with pytest.raises(transfers.TransferTransitionError, match="owner_token_mismatch"):
        _move(proposal.proposal_id, "signed", "claimed", owner_token="token-of-a-dead-request", tx_id="sig-x", raw_b64="eA==")
    with pytest.raises(transfers.TransferTransitionError, match="expected_signed_found_claimed"):
        _move(proposal.proposal_id, "dispatching", "signed")
    with pytest.raises(transfers.TransferTransitionError, match="unknown_transfer"):
        _move("missing-proposal", "signed", "claimed")
    assert (_row(proposal.proposal_id)["state"], _row(proposal.proposal_id)["tx_id"], len(_receipts(proposal.proposal_id))) == ("claimed", None, 1)


def test_a_cancel_request_wins_over_signing_dispatch_and_every_release(wallet_env):
    from core.wallet import transfers
    from core.wallet.store import connection

    proposal = _pending_proposal()
    _claim(proposal)
    with connection() as conn:
        conn.execute("UPDATE wallet_transfers SET cancel_requested_at = ? WHERE proposal_id = ?", (time.time(), proposal.proposal_id))
    for new_state, kwargs in (("signed", {"tx_id": "s", "raw_b64": "eA=="}), ("released", {})):
        with pytest.raises(transfers.TransferTransitionError, match="cancel_requested"):
            _move(proposal.proposal_id, new_state, "claimed", **kwargs)
    _move(proposal.proposal_id, "cancelled", "claimed")
    assert (_row(proposal.proposal_id)["state"], _proposal_state(proposal.proposal_id)) == ("cancelled", "rejected")


def test_a_transition_rolled_back_by_its_caller_leaves_transfer_proposal_hold_and_receipts_unchanged(wallet_env):
    from core.wallet import limits, transfers
    from core.wallet.store import connection

    proposal = _pending_proposal()
    _claim(proposal)
    with pytest.raises(_AbortError), connection() as conn:
        limits._begin_immediate(conn)
        transfers.transition(conn, proposal.proposal_id, "released", expected_state="claimed", now=time.time())
        raise _AbortError()
    assert (_row(proposal.proposal_id)["state"], _proposal_state(proposal.proposal_id), _hold(proposal.proposal_id)[2], len(_receipts(proposal.proposal_id))) == (
        "claimed", "approved", "reserved", 1)


def test_an_unlisted_column_is_refused(wallet_env):
    proposal = _pending_proposal()
    _claim(proposal)
    with pytest.raises(ValueError, match="not a writable transfer column"):
        _move(proposal.proposal_id, "signed", "claimed", amount_minor=1)


# --- stop waiting: the liability keeps counting ---------------------------------------------------------------

def test_a_transfer_the_owner_stopped_waiting_on_keeps_counting_until_the_chain_answers(wallet_env):
    from core.wallet import limits

    proposal = _pending_proposal()
    limits.set_limits(proposal.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=10_000, daily_minor=12_000, per_destination_daily_minor=12_000))
    _claim(proposal)
    _walk(proposal.proposal_id, SIGNED, ("dispatching", {}), ("unknown", {"evidence_kind": "no_answer"}), ("stopped_waiting", {"stopped_waiting_at": time.time()}))
    assert (_row(proposal.proposal_id)["state"], _proposal_state(proposal.proposal_id), _hold(proposal.proposal_id)) == ("stopped_waiting", "broadcast", (4_000, 5_000, "settled"))
    assert not limits.check_limits(proposal.wallet_id, "SOL", 4_000, DESTINATION, chain=proposal.network).ok, "the stopped transfer still counts"

    _move(proposal.proposal_id, "confirmed", "stopped_waiting", charged_fee_minor=1_000)
    assert (_proposal_state(proposal.proposal_id), _hold(proposal.proposal_id)) == ("confirmed", (4_000, 1_000, "settled"))
    assert limits.check_limits(proposal.wallet_id, "SOL", 4_000, DESTINATION, chain=proposal.network).ok


def test_a_stopped_transfer_that_later_failed_on_chain_counts_only_its_fee(wallet_env):
    proposal = _pending_proposal()
    _claim(proposal)
    _walk(proposal.proposal_id, SIGNED, ("dispatching", {}), ("unknown", {}), ("stopped_waiting", {}), ("failed_on_chain", {"charged_fee_minor": 700}))
    assert (_proposal_state(proposal.proposal_id), _hold(proposal.proposal_id)) == ("failed", (0, 700, "settled"))


def test_resettle_changes_only_a_settled_hold(wallet_env):
    from core.wallet import limits
    from core.wallet.store import connection

    proposal = _pending_proposal()
    _claim(proposal)
    with pytest.raises(limits.HoldStateConflictError), connection() as conn:
        limits._resettle(conn, proposal.proposal_id, charged_fee_minor=1, amount_moved=True)
    assert _hold(proposal.proposal_id) == (4_000, 5_000, "reserved")


# --- identity reuse -----------------------------------------------------------------------------------------

def test_an_evm_nonce_is_reused_only_after_the_owner_stopped_waiting_on_the_transfer_holding_it(wallet_env):
    first, second = _pending_proposal(), _pending_proposal()
    _claim(first, family="evm", nonce=7)
    with pytest.raises(sqlite3.IntegrityError):
        _claim(second, family="evm", nonce=7)
    assert (_proposal_state(second.proposal_id), _hold(second.proposal_id), _receipts(second.proposal_id)) == ("pending_approval", None, []), "the refused claim wrote nothing"

    _walk(first.proposal_id, SIGNED, ("dispatching", {}), ("unknown", {}), ("stopped_waiting", {}))
    assert _claim(second, family="evm", nonce=7)["state"] == "claimed", "only one of the two can land"


def test_identical_signed_bytes_are_accepted_again_only_after_a_never_sent_ending(wallet_env):
    first, second, third = _pending_proposal(), _pending_proposal(), _pending_proposal()
    for proposal in (first, second, third):
        _claim(proposal)
    _walk(first.proposal_id, SIGNED, ("signed_revoked", {}), ("discarded", {}))
    _walk(second.proposal_id, SIGNED)
    with pytest.raises(sqlite3.IntegrityError):
        _walk(third.proposal_id, SIGNED)
    assert _row(third.proposal_id)["state"] == "claimed"


# --- store upgrade ----------------------------------------------------------------------------------------------

def test_an_existing_store_gains_the_transfer_table_and_keeps_its_rows(wallet_env):
    from core.wallet import store
    from core.wallet.store import connection

    proposal = _pending_proposal()
    with connection() as conn:
        conn.execute("DROP TABLE IF EXISTS wallet_transfers")
    with connection() as conn:
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(wallet_transfers)").fetchall()}
        kept = conn.execute("SELECT state FROM wallet_proposals WHERE proposal_id = ?", (proposal.proposal_id,)).fetchone()[0]
    assert {"idx_wallet_transfers_tx", "idx_wallet_transfers_nonce", "idx_wallet_transfers_state"} <= indexes
    assert kept == "pending_approval" and "wallet_transfers" in store.TABLES

"""Crypto Pilot, stage 5 step 1: connection-scoped primitives and control epochs (delivery/STAGE5-DISPATCH-RECEIPTS-DESIGN-v4.md §3, §4 step 4, §9).

A claim is one transaction, so each primitive takes the caller's connection and raises on refusal. These tests pin:
- a refusal raises and writes nothing;
- a rolled-back claim leaves no consumed quote, moved proposal or hold behind;
- concurrent claims on one quote or one budget produce exactly one winner;
- the legacy helpers keep their answers;
- every freeze, owner environment and Crypto-switch change moves its epoch, including a change that was undone.

Assumed API (step 1):
    core.wallet.controls.epochs(conn) -> {"freeze", "enabled", "environment"}; controls.bump(conn, name)
    core.wallet.limits._reserve(conn, *, ...) raises LimitRefusedError(verdict) / HoldStateConflictError
    core.wallet.limits._settle(conn, proposal_id, *, charged_fee_minor, amount_moved, now); limits._release(conn, proposal_id)
    core.wallet.receipts._record(conn, proposal, *, state, tx_signature="", fault_code="", extra=None)
    core.wallet.proposals._transition(conn, proposal_id, new_state, *, expected_state, detail=None, **columns)
    core.wallet.proposals.PILOT_ONLY_EDGES; ProposalTransitionError
    core.wallet.quotes._consume(conn, quote_id, digest, now); QuoteConsumeError
    core.wallet.environment._active(conn)
    core.wallet.config.enabled_generation(); config.record_enabled_change()
"""
from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

from tests.wallet._rig import DESTINATION

pytestmark = [pytest.mark.safety]

PIN = "635241"
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
_PATH = ("proposed", "simulated", "limits_checked", "pending_approval", "approved", "signed")


class _AbortClaimError(Exception):
    pass


@pytest.fixture
def home(monkeypatch, tmp_path):
    """No operator override, isolated blackbox and preference file."""
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core import user_preferences
    from core.blackbox import store as store_module

    prefs_file = tmp_path / "user_preferences.json"
    monkeypatch.setattr(user_preferences, "_prefs_path", lambda: prefs_file)
    store_module.reset_default_store()
    yield tmp_path
    store_module.reset_default_store()


def _epochs() -> dict[str, int]:
    from core.wallet import controls
    from core.wallet.store import connection

    with connection() as conn:
        return controls.epochs(conn)


def _pocket_proposal(amount_minor: int = 4_000):
    from core.wallet import custody, proposals

    profile = custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile
    return proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=amount_minor, asset="SOL", origin=proposals.ORIGIN_USER)


def _walk_to(proposal_id: str, target: str) -> None:
    from core.wallet import proposals

    current = proposals.get_proposal(proposal_id).state
    for following in _PATH[_PATH.index(current) + 1 : _PATH.index(target) + 1]:
        assert proposals.transition(proposal_id, following, expected_state=current) is not None, (current, following)
        current = following


def _events(proposal_id: str) -> list[tuple[str, dict]]:
    from core.wallet.store import connection

    with connection() as conn:
        rows = conn.execute("SELECT state, detail_json FROM wallet_proposal_events WHERE proposal_id = ? ORDER BY rowid", (proposal_id,)).fetchall()
    return [(str(state), json.loads(detail or "{}")) for state, detail in rows]


def _open_quote(proposal, *, digest: str, expires_at: float, state: str = "open") -> str:
    from core.wallet.store import connection

    quote_id = f"q-{uuid.uuid4().hex}"
    with connection() as conn:
        conn.execute(
            "INSERT INTO wallet_quotes (quote_id, proposal_id, wallet_id, network, environment, digest, fields_json, state, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (quote_id, proposal.proposal_id, proposal.wallet_id, proposal.network, "testnet", digest, "{}", state, time.time(), float(expires_at)),
        )
    return quote_id


def _quote_state(quote_id: str) -> str:
    from core.wallet.store import connection

    with connection() as conn:
        return str(conn.execute("SELECT state FROM wallet_quotes WHERE quote_id = ?", (quote_id,)).fetchone()[0])


def _receipts(proposal_id: str) -> int:
    from core.wallet.store import connection

    with connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM wallet_receipts WHERE proposal_id = ?", (proposal_id,)).fetchone()[0])


def _hold(proposal_id: str) -> tuple | None:
    from core.wallet.store import connection

    with connection() as conn:
        row = conn.execute("SELECT amount_minor, fee_minor, state, spent_at, settled_at FROM wallet_spend_ledger WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return tuple(row) if row is not None else None  # the wallet connection answers sqlite3.Row, which never equals a tuple


# --- proposals ----------------------------------------------------------------------------------------------

def test_the_cancel_after_signing_edge_is_taken_only_through_the_connection_primitive(wallet_env):
    from core.wallet import proposals
    from core.wallet.store import connection

    legacy, pilot = _pocket_proposal(), _pocket_proposal()
    _walk_to(legacy.proposal_id, "signed")
    _walk_to(pilot.proposal_id, "signed")

    assert proposals.TRANSITIONS[proposals.STATE_SIGNED] == (proposals.STATE_BROADCAST, proposals.STATE_FAILED), "the legacy table is unchanged"
    assert proposals.transition(legacy.proposal_id, "rejected", expected_state="signed") is None
    assert proposals.get_proposal(legacy.proposal_id).state == "signed"

    with connection() as conn:
        proposals._transition(conn, pilot.proposal_id, "rejected", expected_state="signed", detail={"reason": "cancelled_before_dispatch"}, fault_code="wallet_transfer_cancelled")
    moved = proposals.get_proposal(pilot.proposal_id)
    assert (moved.state, moved.fault_code) == ("rejected", "wallet_transfer_cancelled")
    assert _events(pilot.proposal_id)[-1] == ("rejected", {"reason": "cancelled_before_dispatch"})


def test_a_connection_transition_raises_on_every_refusal_and_writes_nothing(wallet_env):
    from core.wallet import proposals
    from core.wallet.store import connection

    proposal = _pocket_proposal()
    _walk_to(proposal.proposal_id, "pending_approval")
    before = _events(proposal.proposal_id)

    refusals = (
        ("missing", "approved", "pending_approval", "unknown_proposal"),
        (proposal.proposal_id, "signed", "approved", "expected_approved_found_pending_approval"),
        (proposal.proposal_id, "confirmed", "pending_approval", "edge_not_allowed:pending_approval->confirmed"),
        (proposal.proposal_id, "rejected", "signed", "expected_signed_found_pending_approval"),
    )
    for proposal_id, new_state, expected, reason in refusals:
        with pytest.raises(proposals.ProposalTransitionError, match=reason), connection() as conn:
            proposals._transition(conn, proposal_id, new_state, expected_state=expected)
    assert proposals.get_proposal(proposal.proposal_id).state == "pending_approval"
    assert _events(proposal.proposal_id) == before


# --- quotes -------------------------------------------------------------------------------------------------

def test_a_quote_is_consumed_once_only_with_its_digest_and_only_strictly_before_it_expires(wallet_env):
    from core.wallet import quotes
    from core.wallet.store import connection

    proposal, other = _pocket_proposal(), _pocket_proposal()
    now = time.time()
    good = _open_quote(proposal, digest="d-good", expires_at=now + 60)
    at_expiry = _open_quote(other, digest="d-edge", expires_at=now)  # one open quote per proposal (idx_wallet_quotes_open)
    superseded = _open_quote(proposal, digest="d-old", expires_at=now + 60, state="superseded")

    for quote_id, digest in ((good, "d-other"), (at_expiry, "d-edge"), (superseded, "d-old"), ("q-missing", "d-good")):
        with pytest.raises(quotes.QuoteConsumeError), connection() as conn:
            quotes._consume(conn, quote_id, digest, now)
    assert (_quote_state(good), _quote_state(at_expiry), _quote_state(superseded)) == ("open", "open", "superseded")

    with connection() as conn:
        quotes._consume(conn, good, "d-good", now)
    assert _quote_state(good) == "consumed"
    with pytest.raises(quotes.QuoteConsumeError), connection() as conn:
        quotes._consume(conn, good, "d-good", now)


def test_two_claims_racing_for_one_quote_consume_it_exactly_once(wallet_env):
    from core.wallet import limits, quotes
    from core.wallet.store import connection

    proposal = _pocket_proposal()
    now = time.time()
    quote_id = _open_quote(proposal, digest="d-race", expires_at=now + 60)
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def claim():
        barrier.wait()
        try:
            with connection() as conn:
                limits._begin_immediate(conn)
                quotes._consume(conn, quote_id, "d-race", now)
            result = "consumed"
        except quotes.QuoteConsumeError:
            result = "refused"
        except Exception as exc:  # an unexpected error is an outcome too, never a silently missing thread
            result = f"error:{type(exc).__name__}:{exc}"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes) == ["consumed", "refused"]


# --- holds --------------------------------------------------------------------------------------------------

def test_a_connection_reservation_refuses_exactly_like_the_legacy_check_and_never_holds_twice(wallet_env):
    from core.wallet import limits
    from core.wallet.store import connection

    proposal = _pocket_proposal()
    now = time.time()
    common = {"wallet_id": proposal.wallet_id, "asset": "SOL", "destination": DESTINATION, "now": now, "chain": proposal.network}

    legacy_verdict = limits.check_limits(proposal.wallet_id, "SOL", 10**15, DESTINATION, now=now, chain=proposal.network)
    assert not legacy_verdict.ok
    with pytest.raises(limits.LimitRefusedError) as refused, connection() as conn:
        limits._reserve(conn, amount_minor=10**15, proposal_id="p-too-large", **common)
    assert refused.value.verdict == legacy_verdict
    assert _hold("p-too-large") is None

    with connection() as conn:
        assert limits._reserve(conn, amount_minor=4_000, fee_minor=5_000, proposal_id="p-held", **common).ok
    with pytest.raises(limits.HoldStateConflictError), connection() as conn:
        limits._reserve(conn, amount_minor=4_000, fee_minor=5_000, proposal_id="p-held", **common)
    legacy_again = limits.reserve_spend(amount_minor=4_000, fee_minor=5_000, proposal_id="p-held", **common)
    assert (legacy_again.ok, legacy_again.reason) == (True, "already held"), "the legacy helper keeps its answer"

    limits.set_frozen(True)
    try:
        with pytest.raises(limits.LimitRefusedError) as frozen, connection() as conn:
            limits._reserve(conn, amount_minor=1_000, proposal_id="p-frozen", **common)
        assert frozen.value.verdict.limit == limits.LIMIT_FROZEN
    finally:
        limits.set_frozen(False)


def test_two_claims_racing_for_the_last_of_a_daily_budget_hold_it_once(wallet_env):
    from core.wallet import limits
    from core.wallet.store import connection

    proposal = _pocket_proposal()
    limits.set_limits(proposal.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=10_000, daily_minor=12_000, per_destination_daily_minor=12_000))
    now = time.time()
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def claim(proposal_id: str):
        barrier.wait()
        try:
            with connection() as conn:
                limits._begin_immediate(conn)
                limits._reserve(conn, wallet_id=proposal.wallet_id, asset="SOL", amount_minor=8_000, destination=DESTINATION, proposal_id=proposal_id, now=now, chain=proposal.network)
            result = "held"
        except limits.LimitRefusedError as exc:
            result = f"refused:{exc.verdict.limit}"
        except Exception as exc:  # an unexpected error is an outcome too, never a silently missing thread
            result = f"error:{type(exc).__name__}:{exc}"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=claim, args=(f"p-race-{index}",)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(outcomes)[0] == "held" and sorted(outcomes)[1].startswith("refused:"), outcomes


def test_settling_counts_what_the_chain_charged_and_a_failed_transfer_keeps_only_its_fee(wallet_env):
    from core.wallet import limits
    from core.wallet.store import connection

    proposal = _pocket_proposal()
    reserved_at = time.time() - 120
    settled_at = time.time()
    common = {"wallet_id": proposal.wallet_id, "asset": "SOL", "destination": DESTINATION, "now": reserved_at, "chain": proposal.network, "amount_minor": 4_000, "fee_minor": 5_000}
    with connection() as conn:
        for proposal_id in ("p-landed", "p-failed", "p-unpriced", "p-released"):
            limits._reserve(conn, proposal_id=proposal_id, **common)

    with connection() as conn:
        limits._settle(conn, "p-landed", charged_fee_minor=4_321, amount_moved=True, now=settled_at)
        limits._settle(conn, "p-failed", charged_fee_minor=2_000, amount_moved=False, now=settled_at)
        limits._settle(conn, "p-unpriced", charged_fee_minor=None, amount_moved=True, now=settled_at)
        limits._release(conn, "p-released")
    assert _hold("p-landed") == (4_000, 4_321, "settled", reserved_at, settled_at), "spent_at stays at reservation time; settled_at records the settle"
    assert _hold("p-failed") == (0, 2_000, "settled", reserved_at, settled_at)
    assert _hold("p-unpriced") == (4_000, 5_000, "settled", reserved_at, settled_at), "an absent fee keeps the reserved maximum counted"
    assert _hold("p-released")[2] == "released"

    for operation, proposal_id in ((limits._settle, "p-landed"), (limits._release, "p-landed"), (limits._release, "p-released"), (limits._settle, "p-missing")):
        kwargs = {"charged_fee_minor": 1, "amount_moved": True, "now": settled_at} if operation is limits._settle else {}
        with pytest.raises(limits.HoldStateConflictError), connection() as conn:
            operation(conn, proposal_id, **kwargs)
    assert _hold("p-landed") == (4_000, 4_321, "settled", reserved_at, settled_at)


def test_a_claim_that_aborts_leaves_no_consumed_quote_moved_proposal_or_hold(wallet_env):
    from core.wallet import limits, proposals, quotes, receipts
    from core.wallet.store import connection

    proposal = _pocket_proposal()
    _walk_to(proposal.proposal_id, "pending_approval")
    now = time.time()
    quote_id = _open_quote(proposal, digest="d-claim", expires_at=now + 60)

    def claim(conn):
        limits._begin_immediate(conn)
        quotes._consume(conn, quote_id, "d-claim", now)
        proposals._transition(conn, proposal.proposal_id, "approved", expected_state="pending_approval")
        limits._reserve(conn, wallet_id=proposal.wallet_id, asset="SOL", amount_minor=proposal.amount_minor, destination=DESTINATION, proposal_id=proposal.proposal_id, now=now, chain=proposal.network)
        receipts._record(conn, proposal, state="approved")

    with pytest.raises(_AbortClaimError), connection() as conn:
        claim(conn)
        raise _AbortClaimError()
    assert (_quote_state(quote_id), proposals.get_proposal(proposal.proposal_id).state, _hold(proposal.proposal_id), _receipts(proposal.proposal_id)) == ("open", "pending_approval", None, 0)

    with connection() as conn:
        claim(conn)
    assert (_quote_state(quote_id), proposals.get_proposal(proposal.proposal_id).state, _hold(proposal.proposal_id)[2], _receipts(proposal.proposal_id)) == ("consumed", "approved", "reserved", 1)


def test_the_legacy_helpers_keep_their_answers_while_sharing_the_primitives(wallet_env):
    from core.wallet import limits, proposals, receipts

    proposal = _pocket_proposal()
    reserved_at, settled_at = time.time() - 60, time.time()
    common = {"wallet_id": proposal.wallet_id, "asset": "SOL", "destination": DESTINATION, "chain": proposal.network, "amount_minor": 4_000}
    assert limits.reserve_spend(proposal_id="p-legacy", now=reserved_at, **common).ok
    limits.settle_spend("p-legacy", now=settled_at)
    assert _hold("p-legacy") == (4_000, 0, "settled", settled_at, settled_at), "the legacy settle still dates the hold at settlement"
    limits.release_spend("p-legacy")
    limits.release_spend("p-never-held")
    assert _hold("p-legacy")[2] == "settled" and _hold("p-never-held") is None, "releasing nothing stays silent"

    assert proposals.transition("missing", "approved") is None, "the legacy transition still answers None"
    receipt = receipts.record_receipt(proposal, state="proposed")
    assert receipt.proposal_id == proposal.proposal_id and _receipts(proposal.proposal_id) == 1


# --- epochs -------------------------------------------------------------------------------------------------

def test_every_freeze_change_moves_its_epoch_even_when_it_was_undone(home):
    from core.wallet import limits

    assert _epochs() == {"freeze": 0, "enabled": 0, "environment": 0}
    limits.set_frozen(True)
    limits.set_frozen(True)
    assert _epochs()["freeze"] == 1, "setting the same value is not a change"
    limits.set_frozen(False)
    assert _epochs() == {"freeze": 2, "enabled": 0, "environment": 0}


def test_every_owner_environment_switch_moves_its_epoch_and_a_derived_pin_does_not(home):
    from core.wallet import environment

    environment.require_active(SOLANA_MAINNET)
    assert _epochs()["environment"] == 0
    environment.set_active_environment("testnet")
    environment.set_active_environment("mainnet")
    assert _epochs() == {"freeze": 0, "enabled": 0, "environment": 2}


def test_the_connection_environment_read_pins_a_derived_default_exactly_like_the_gate(home, monkeypatch):
    from core.wallet import environment, limits
    from core.wallet.store import connection

    def stored_value():
        with connection() as conn:
            row = conn.execute("SELECT value FROM wallet_controls WHERE key = ?", (environment._CONTROL_KEY,)).fetchone()
        return json.loads(row[0]) if row else None

    def forget():
        with connection() as conn:
            conn.execute("DELETE FROM wallet_controls WHERE key = ?", (environment._CONTROL_KEY,))

    with pytest.raises(_AbortClaimError), connection() as conn:
        limits._begin_immediate(conn)
        assert environment._active(conn).source == environment.SOURCE_FRESH
        raise _AbortClaimError()
    assert stored_value() is None, "the pin belongs to the caller's transaction"

    with connection() as conn:
        limits._begin_immediate(conn)
        assert environment._active(conn).environment == "mainnet"
    pinned_by_claim = stored_value()
    with connection() as conn:
        assert environment._active(conn).source == environment.SOURCE_STORED
    forget()
    environment.require_active(SOLANA_MAINNET)
    assert pinned_by_claim == stored_value() == {"environment": "mainnet", "set_by": environment.SOURCE_FRESH}

    environment.set_active_environment("testnet")
    with connection() as conn:
        assert (environment._active(conn).environment, environment._active(conn).source) == ("testnet", environment.SOURCE_STORED)
    assert stored_value() == {"environment": "testnet", "set_by": "owner"}, "a stored choice is never re-pinned"

    forget()
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    with connection() as conn:
        assert environment._active(conn).source == environment.SOURCE_OVERRIDE
    assert stored_value() is None, "the operator override is never written"


def test_the_settings_switch_counts_each_change_and_moves_the_enabled_epoch(home):
    from core.wallet import config
    from core.web.api.registry_authorities import set_prefs_authority
    from core.web.api.runtime import RuntimeServices

    def door(body: dict) -> int:
        return set_prefs_authority(body, {"Content-Type": "application/json"}, RuntimeServices(display_name="VOOL")).status

    (home / "user_preferences.json").write_text(json.dumps({"wallet_enabled": False, "show_workflow": False}), encoding="utf-8")
    assert (config.enabled_generation(), _epochs()["enabled"]) == (0, 0), "an older preference file has no generation"

    steps = (
        ({"wallet_enabled": True}, 200, 1),
        ({"wallet_enabled": True}, 200, 1),
        ({"show_workflow": True}, 200, 1),
        ({"wallet_enabled": "yes"}, 400, 1),
        ({"wallet_enabled": False, "show_workflow": False}, 200, 2),
        ({"wallet_enabled": True}, 200, 3),
    )
    for body, status, expected in steps:
        assert door(body) == status, body
        assert (config.enabled_generation(), _epochs()["enabled"]) == (expected, expected), body
    assert json.loads((home / "user_preferences.json").read_text(encoding="utf-8"))["wallet_enabled_generation"] == 3
    assert _epochs()["freeze"] == _epochs()["environment"] == 0

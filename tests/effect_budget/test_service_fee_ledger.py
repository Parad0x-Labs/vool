"""The service-fee ledger: exact sub-atomic accounting, durability, exactly-once accrual, bounded reversal and the
collection lifecycle, on a real SQLite file. No chain, no provider, no float anywhere.

Failure-first: the first test reproduces the captured defect class (per-call flooring loses every whole unit) as a
control and proves the ledger accounts for exactly 112 whole atomic units after 1,000 accepted payments of 112 at
10 bps. Every other test is an adversarial or concurrent case, not a greeting.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from core import service_fee_ledger as ledger

NETWORK = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
ASSET_KEY = f"{NETWORK}|USDC|6"
PAYER = "PayerFixture" + "1" * 32
#: a clearly labelled disposable fixture treasury owner -- never the designated production owner
TREASURY = "FixtureTreasury" + "2" * 29
POLICY = ledger.DNA_NATIVE_FEE_POLICY_ID
RATE = ledger.DNA_NATIVE_FEE_BPS


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    ledger.ensure_tables(conn)
    return conn


def _key() -> str:
    return ledger.identity_key(payer_account=PAYER, network=NETWORK, asset_key=ASSET_KEY, treasury_owner=TREASURY, policy_id=POLICY)


def _accrue(conn: sqlite3.Connection, liability_id: str, basis: int, *, payer: str = PAYER, treasury: str = TREASURY, evidence: str = "") -> dict:
    conn.execute("BEGIN IMMEDIATE")
    try:
        out = ledger.accrue(
            conn, liability_id=liability_id, operation_id=f"op-{liability_id}", payer_account=payer, network=NETWORK, asset_key=ASSET_KEY,
            treasury_owner=treasury, policy_id=POLICY, basis_atomic=basis, rate_bps=RATE, evidence_id=evidence or f"ev-{liability_id}",
            evidence_kind="provider_usage_receipt", source="provider",
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return out


def _floor_per_call(basis: int, rate_bps: int) -> int:
    """CONTROL: the per-call flooring the captured DNA implementation does (feePolicy.calculateFeeAtomic)."""
    return basis * rate_bps // 10_000


def test_a_thousand_dust_payments_account_for_112_whole_units_where_per_call_flooring_accounts_for_zero(tmp_path) -> None:
    conn = _connect(tmp_path / "fees.db")
    floored = sum(_floor_per_call(112, RATE) for _ in range(1000))
    assert floored == 0, "the control reproduces the defect: per-call flooring loses everything"
    newly = 0
    for index in range(1000):
        newly += _accrue(conn, f"li-{index}", 112)["newly_representable_atomic"]
    position = ledger.owed(conn, _key())
    assert position["accrued_numerator"] == 112 * 1000 * RATE == 1_120_000
    assert position["owed_atomic"] == 112 and position["carry_numerator"] == 0 and position["collectible_atomic"] == 112
    assert newly == 112, "the whole units the settlements made collectible sum to exactly the aggregate fee"
    assert ledger.numerator_decimal(position["accrued_numerator"], 6) == "0.000112", "112 µUSDC of fees on 0.112 USDC of payments"
    assert ledger.numerator_atomic_text(position["accrued_numerator"]) == "112"
    assert ledger.numerator_decimal(RATE * 112, 6) == "0.000000112", "one payment's exact fee is never shown as zero"
    assert ledger.numerator_atomic_text(RATE * 112) == "0.112"


def test_the_fraction_survives_a_restart_and_only_whole_units_are_collectible(tmp_path) -> None:
    path = tmp_path / "fees.db"
    conn = _connect(path)
    for index in range(7):  # 7 x 0.112 = 0.784 atomic: nothing whole yet
        _accrue(conn, f"li-{index}", 112)
    first = ledger.owed(conn, _key())
    assert (first["owed_atomic"], first["carry_numerator"], first["collectible_atomic"]) == (0, 7_840, 0)
    conn.close()
    conn = _connect(path)  # a new process: the carry is on disk, not in memory
    again = ledger.owed(conn, _key())
    assert again == first
    for index in range(7, 9):  # 9 x 0.112 = 1.008 atomic: one whole unit, 80 numerator units of carry
        _accrue(conn, f"li-{index}", 112)
    after = ledger.owed(conn, _key())
    assert (after["owed_atomic"], after["carry_numerator"], after["collectible_atomic"]) == (1, 80, 1)


def test_a_retried_settlement_of_the_same_liability_accrues_once(tmp_path) -> None:
    conn = _connect(tmp_path / "fees.db")
    first = _accrue(conn, "li-a", 250_000)
    second = _accrue(conn, "li-a", 250_000)
    assert first["idempotent"] is False and second["idempotent"] is True and second["newly_representable_atomic"] == 0
    assert ledger.owed(conn, _key())["accrued_numerator"] == 250_000 * RATE
    with pytest.raises(ledger.ServiceFeeLedgerError) as conflict:
        _accrue(conn, "li-a", 250_001)
    assert conflict.value.code == "service_fee_accrual_conflict"


def test_identities_never_mix_payer_treasury_or_asset(tmp_path) -> None:
    conn = _connect(tmp_path / "fees.db")
    _accrue(conn, "li-1", 1_000_000)  # 1,000 numerator units = 1 whole unit? no: 1_000_000 * 10 = 10_000_000 -> 1000 atomic
    _accrue(conn, "li-2", 1_000_000, payer="OtherPayer" + "3" * 34)
    _accrue(conn, "li-3", 1_000_000, treasury="OtherTreasury" + "4" * 31)
    mine = ledger.owed(conn, _key())
    assert mine["owed_atomic"] == 1_000 and mine["accrued_numerator"] == 10_000_000
    other = ledger.owed(conn, ledger.identity_key(payer_account="OtherPayer" + "3" * 34, network=NETWORK, asset_key=ASSET_KEY, treasury_owner=TREASURY, policy_id=POLICY))
    assert other["owed_atomic"] == 1_000
    old_treasury = ledger.owed(conn, ledger.identity_key(payer_account=PAYER, network=NETWORK, asset_key=ASSET_KEY, treasury_owner="OtherTreasury" + "4" * 31, policy_id=POLICY))
    assert old_treasury["owed_atomic"] == 1_000, "accrual bound to one treasury never migrates to another"
    with pytest.raises(ledger.ServiceFeeLedgerError):
        ledger.identity_key(payer_account=PAYER, network=NETWORK, asset_key=ASSET_KEY, treasury_owner="", policy_id=POLICY)


def test_concurrent_settlements_across_connections_are_exactly_once(tmp_path) -> None:
    path = tmp_path / "fees.db"
    _connect(path).close()
    errors: list[BaseException] = []

    def worker(offset: int) -> None:
        conn = _connect(path)
        try:
            for index in range(50):
                _accrue(conn, f"li-{offset}-{index}", 112)
                _accrue(conn, f"li-{offset}-{index}", 112)  # every worker retries every settlement
        except BaseException as exc:  # recorded for the assertion
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(offset,)) for offset in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    assert not errors, errors
    position = ledger.owed(_connect(path), _key())
    assert position["accrued_numerator"] == 200 * 112 * RATE, "200 distinct accrued payments, each exactly once"


def test_a_reversal_is_bounded_by_the_operations_own_accrual_and_creates_no_spendable_credit(tmp_path) -> None:
    conn = _connect(tmp_path / "fees.db")
    _accrue(conn, "li-x", 5_000_000)  # fee 5,000 atomic
    _accrue(conn, "li-y", 3_000_000)  # fee 3,000 atomic
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ledger.ServiceFeeLedgerError) as too_much:
        ledger.reverse(conn, liability_id="li-x", refunded_basis_atomic=5_000_001, evidence_id="refund-1", evidence_kind="provider_billing_statement", source="provider")
    conn.execute("ROLLBACK")
    assert too_much.value.code == "service_fee_reversal_exceeds_accrual"
    conn.execute("BEGIN IMMEDIATE")
    partial = ledger.reverse(conn, liability_id="li-x", refunded_basis_atomic=2_000_000, evidence_id="refund-1", evidence_kind="provider_billing_statement", source="provider")
    replay = ledger.reverse(conn, liability_id="li-x", refunded_basis_atomic=2_000_000, evidence_id="refund-1", evidence_kind="provider_billing_statement", source="provider")
    conn.execute("COMMIT")
    assert partial["numerator"] == -2_000_000 * RATE and replay["idempotent"] is True
    position = ledger.owed(conn, _key())
    assert position["owed_atomic"] == 6_000, "x's remaining 3,000 plus y's untouched 3,000"
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ledger.ServiceFeeLedgerError) as beyond:
        ledger.reverse(conn, liability_id="li-x", refunded_basis_atomic=3_000_001, evidence_id="refund-2", evidence_kind="provider_billing_statement", source="provider")
    conn.execute("ROLLBACK")
    assert beyond.value.code == "service_fee_reversal_exceeds_accrual", "a second refund cannot reach into y's debt"
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ledger.ServiceFeeLedgerError) as nothing:
        ledger.reverse(conn, liability_id="li-never", refunded_basis_atomic=1, evidence_id="refund-3", evidence_kind="provider_billing_statement", source="provider")
    conn.execute("ROLLBACK")
    assert nothing.value.code == "service_fee_nothing_to_reverse"
    # a refund after a confirmed collection leaves an over-collected remainder that is visible and never collectible
    conn.execute("BEGIN IMMEDIATE")
    offer = ledger.offer_collection(conn, key=_key(), amount_atomic=6_000, expires_epoch=10**12)
    ledger.bind_collection(conn, offer["collection_id"], proposal_id="pay-c1", wallet_id="w1")
    ledger.claim_collection(conn, proposal_id="pay-c1", quote_id="q1", amount_atomic=6_000, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=5_000)
    ledger.mark_submitted(conn, proposal_id="pay-c1")
    ledger.confirm_collection(conn, proposal_id="pay-c1", tx_signature="sig" + "5" * 80)
    ledger.reverse(conn, liability_id="li-y", refunded_basis_atomic=3_000_000, evidence_id="refund-4", evidence_kind="provider_billing_statement", source="provider")
    conn.execute("COMMIT")
    position = ledger.owed(conn, _key())
    assert (position["owed_atomic"], position["collectible_atomic"], position["overcollected_numerator"]) == (0, 0, 3_000 * ledger.SCALE)


def test_a_collection_holds_its_amount_retires_once_and_a_competing_proposal_cannot_release_it(tmp_path) -> None:
    conn = _connect(tmp_path / "fees.db")
    for index in range(3):
        _accrue(conn, f"li-{index}", 400_000)  # 3 x 400 = 1,200 atomic owed
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ledger.ServiceFeeLedgerError) as too_big:
        ledger.offer_collection(conn, key=_key(), amount_atomic=1_201, expires_epoch=10**12)
    conn.execute("ROLLBACK")
    assert too_big.value.code == "service_fee_not_collectible"
    conn.execute("BEGIN IMMEDIATE")
    offer = ledger.offer_collection(conn, key=_key(), amount_atomic=1_000, expires_epoch=10**12, network_fee_max_atomic=5_000, network_fee_asset="SOL")
    with pytest.raises(ledger.ServiceFeeLedgerError) as second:
        ledger.offer_collection(conn, key=_key(), amount_atomic=200, expires_epoch=10**12)
    conn.execute("COMMIT")
    assert second.value.code == "service_fee_collection_open", "exactly one open collection per identity"
    held = ledger.owed(conn, _key())
    assert (held["held_by_open_collections_atomic"], held["collectible_atomic"], held["owed_atomic"]) == (1_000, 200, 1_200), "preparing a batch never erases debt"
    conn.execute("BEGIN IMMEDIATE")
    ledger.bind_collection(conn, offer["collection_id"], proposal_id="pay-winner", wallet_id="w1")
    with pytest.raises(ledger.ServiceFeeLedgerError) as unbound:
        ledger.claim_collection(conn, proposal_id="pay-loser", quote_id="q", amount_atomic=1_000, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=5_000)
    assert unbound.value.code == "service_fee_collection_unbound"
    with pytest.raises(ledger.ServiceFeeLedgerError) as wrong_treasury:
        ledger.claim_collection(conn, proposal_id="pay-winner", quote_id="q", amount_atomic=1_000, treasury_owner="Other" + "9" * 39, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=5_000)
    assert wrong_treasury.value.code == "service_fee_collection_binding_changed"
    with pytest.raises(ledger.ServiceFeeLedgerError) as fee_too_high:
        ledger.claim_collection(conn, proposal_id="pay-winner", quote_id="q", amount_atomic=1_000, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=5_001)
    assert fee_too_high.value.code == "service_fee_collection_fee_ceiling_exceeded"
    claimed = ledger.claim_collection(conn, proposal_id="pay-winner", quote_id="q", amount_atomic=1_000, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=5_000)
    assert claimed["state"] == "claimed"
    ledger.mark_submitted(conn, proposal_id="pay-winner")
    assert ledger.release_collection(conn, proposal_id="pay-loser", reason="loser") is None, "a proposal that never bound the collection releases nothing"
    with pytest.raises(ledger.ServiceFeeLedgerError) as custody:
        ledger.release_collection(conn, proposal_id="pay-winner", reason="give up")
    assert custody.value.code == "service_fee_collection_in_custody", "a submitted transfer keeps custody until the chain answers"
    confirmed = ledger.confirm_collection(conn, proposal_id="pay-winner", tx_signature="sig" + "7" * 80)
    again = ledger.confirm_collection(conn, proposal_id="pay-winner", tx_signature="sig" + "7" * 80)
    conn.execute("COMMIT")
    assert confirmed["state"] == again["state"] == "confirmed"
    retired = ledger.owed(conn, _key())
    assert (retired["retired_numerator"], retired["owed_atomic"], retired["collectible_atomic"], retired["held_by_open_collections_atomic"]) == (1_000 * ledger.SCALE, 200, 200, 0)
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(ledger.ServiceFeeLedgerError) as other_sig:
        ledger.confirm_collection(conn, proposal_id="pay-winner", tx_signature="sig" + "8" * 80)
    conn.execute("ROLLBACK")
    assert other_sig.value.code == "service_fee_collection_conflict", "a confirmed collection retires its amount exactly once"


def test_a_failed_or_lapsed_collection_gives_the_debt_back_and_a_stale_offer_is_refused_at_the_claim(tmp_path) -> None:
    conn = _connect(tmp_path / "fees.db")
    _accrue(conn, "li-1", 500_000)  # 500 atomic
    conn.execute("BEGIN IMMEDIATE")
    offer = ledger.offer_collection(conn, key=_key(), amount_atomic=500, expires_epoch=10**12)
    ledger.bind_collection(conn, offer["collection_id"], proposal_id="pay-f", wallet_id="w1")
    ledger.claim_collection(conn, proposal_id="pay-f", quote_id="q", amount_atomic=500, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=0)
    ledger.mark_submitted(conn, proposal_id="pay-f")
    failed = ledger.fail_collection(conn, proposal_id="pay-f", reason="failed_on_chain")
    conn.execute("COMMIT")
    assert failed["state"] == "failed" and ledger.owed(conn, _key())["collectible_atomic"] == 500, "a failed execution collected nothing"
    conn.execute("BEGIN IMMEDIATE")
    lapsed = ledger.offer_collection(conn, key=_key(), amount_atomic=500, expires_epoch=1.0)
    ledger.bind_collection(conn, lapsed["collection_id"], proposal_id="pay-l", wallet_id="w1")
    with pytest.raises(ledger.ServiceFeeLedgerError) as expired:
        ledger.claim_collection(conn, proposal_id="pay-l", quote_id="q", amount_atomic=500, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=0, now=2.0)
    assert expired.value.code == "service_fee_collection_expired"
    released = ledger.release_collection(conn, proposal_id="pay-l", reason="offer_expired")
    conn.execute("COMMIT")
    assert released["state"] == "released" and ledger.owed(conn, _key())["collectible_atomic"] == 500
    # the accrual position shrinks under an offer (a proven refund): the claim refuses the stale amount
    conn.execute("BEGIN IMMEDIATE")
    stale = ledger.offer_collection(conn, key=_key(), amount_atomic=500, expires_epoch=10**12)
    ledger.bind_collection(conn, stale["collection_id"], proposal_id="pay-s", wallet_id="w1")
    ledger.reverse(conn, liability_id="li-1", refunded_basis_atomic=100_000, evidence_id="refund", evidence_kind="provider_billing_statement", source="provider")
    with pytest.raises(ledger.ServiceFeeLedgerError) as changed:
        ledger.claim_collection(conn, proposal_id="pay-s", quote_id="q", amount_atomic=500, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=0)
    conn.execute("ROLLBACK")
    assert changed.value.code == "service_fee_accrual_changed"


def test_money_never_rides_a_float_or_a_negative_basis() -> None:
    with pytest.raises(ledger.ServiceFeeLedgerError):
        ledger.fee_numerator(112.0, RATE)  # type: ignore[arg-type]
    with pytest.raises(ledger.ServiceFeeLedgerError):
        ledger.fee_numerator(-1, RATE)
    with pytest.raises(ledger.ServiceFeeLedgerError):
        ledger.fee_numerator(True, RATE)  # type: ignore[arg-type]
    assert ledger.fee_ceiling_atomic(112, RATE) == 1 and ledger.fee_ceiling_atomic(1_000, RATE) == 1 and ledger.fee_ceiling_atomic(1_001, RATE) == 2
    assert ledger.fee_ceiling_atomic(0, RATE) == 0 and ledger.fee_numerator(0, RATE) == 0


# --- REVIEW F2: expiration may retire only the still-unclaimed offer it observed; a claim that lands first wins ------


def test_a_stale_expiry_snapshot_never_releases_a_collection_claimed_in_between(tmp_path) -> None:
    """The concurrent schedule of the finding: connection A reads a lapsed offer, connection B claims it (the owner's
    approval lands), then A tries to expire. The compare-and-set retires nothing (the claim moved the row), and a
    fenced release on A's snapshot refuses; B's custody, version and binding stand exactly as B left them."""
    path = tmp_path / "fees.db"
    a, b = _connect(path), _connect(path)
    _accrue(a, "li-1", 500_000)
    a.execute("BEGIN IMMEDIATE")
    offer = ledger.offer_collection(a, key=_key(), amount_atomic=500, expires_epoch=100.0)
    ledger.bind_collection(a, offer["collection_id"], proposal_id="pay-c", wallet_id="w1", payment_proposal_id="pay-p")
    a.execute("COMMIT")
    snapshot = ledger.collection(a, offer["collection_id"])  # A's read: offered, lapsed at now=150
    assert snapshot["state"] == "offered" and snapshot["expires_epoch"] == 100.0
    # B claims first (its approval was still inside the window it saw; the ledger's own expiry check uses B's now)
    b.execute("BEGIN IMMEDIATE")
    claimed = ledger.claim_collection(b, proposal_id="pay-c", quote_id="q", amount_atomic=500, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=5_000, now=90.0, expected_version=snapshot["state_version"])
    b.execute("COMMIT")
    assert claimed["state"] == "claimed" and claimed["state_version"] == snapshot["state_version"] + 1
    # A's expiry, on its stale snapshot: the CAS finds no offered-and-lapsed row and touches nothing
    a.execute("BEGIN IMMEDIATE")
    assert ledger.expire_offer(a, collection_id=offer["collection_id"], now=150.0) is None
    with pytest.raises(ledger.ServiceFeeLedgerError) as fenced:
        ledger.release_collection(a, collection_id=offer["collection_id"], reason="offer_expired", expected_state="offered", expected_version=snapshot["state_version"])
    a.execute("ROLLBACK")
    assert fenced.value.code == "service_fee_collection_conflict"
    after = ledger.collection(b, offer["collection_id"])
    assert (after["state"], after["state_version"], after["proposal_id"], after["quote_id"], after["payment_proposal_id"]) == ("claimed", claimed["state_version"], "pay-c", "q", "pay-p"), "the winner's custody, version and binding are untouched"
    position = ledger.owed(b, _key())
    assert (position["held_by_open_collections_atomic"], position["collectible_atomic"]) == (500, 0)
    # the transfer owner's legitimate release of its own claimed, proven-never-sent collection passes no fence and works
    b.execute("BEGIN IMMEDIATE")
    released = ledger.release_collection(b, proposal_id="pay-c", reason="transfer_released")
    b.execute("COMMIT")
    assert released["state"] == "released" and ledger.owed(b, _key())["collectible_atomic"] == 500
    # a claim that raced a version the approval never saw is refused too
    b.execute("BEGIN IMMEDIATE")
    fresh = ledger.offer_collection(b, key=_key(), amount_atomic=500, expires_epoch=10**12)
    ledger.bind_collection(b, fresh["collection_id"], proposal_id="pay-d", wallet_id="w1", payment_proposal_id="pay-q")
    with pytest.raises(ledger.ServiceFeeLedgerError) as stale_version:
        ledger.claim_collection(b, proposal_id="pay-d", quote_id="q2", amount_atomic=500, treasury_owner=TREASURY, network=NETWORK, asset_key=ASSET_KEY, network_fee_max_atomic=0, expected_version=fresh["state_version"] + 7)
    b.execute("ROLLBACK")
    assert stale_version.value.code == "service_fee_collection_version_changed"


def test_expire_offer_retires_only_a_lapsed_unclaimed_offer_and_the_debt_returns(tmp_path) -> None:
    conn = _connect(tmp_path / "fees.db")
    _accrue(conn, "li-1", 500_000)
    conn.execute("BEGIN IMMEDIATE")
    offer = ledger.offer_collection(conn, key=_key(), amount_atomic=500, expires_epoch=100.0)
    ledger.bind_collection(conn, offer["collection_id"], proposal_id="pay-e", wallet_id="w1", payment_proposal_id="pay-r")
    # Harness repair (Goal 2 stage 2, 2026-09-17, on the never-executed correction 98b06ed9): the original
    # assertion compared the post-expiry version to offer["state_version"] + 1, ignoring that THIS TEST's own
    # bind_collection is a fencing mutation (INSERT=1, bind CAS=2, expiry CAS=3 — the ledger's discipline of
    # one bump per applied mutation and none on a refused CAS is correct; first-ever run failed 3 != 2).
    # Equivalent and stronger: pin the refused (not-lapsed) expiry as a no-write, and the lapsed expiry as
    # exactly one bump from the version observed AFTER the bind.
    bound = ledger.collection(conn, offer["collection_id"])
    assert ledger.expire_offer(conn, collection_id=offer["collection_id"], now=99.0) is None, "not lapsed yet: nothing retired"
    assert ledger.collection(conn, offer["collection_id"])["state_version"] == bound["state_version"], "a refused expiry writes nothing, not even a version bump"
    assert ledger.collection_for_payment(conn, "pay-r")["collection_id"] == offer["collection_id"], "the open collection of a payment is found by that payment"
    expired = ledger.expire_offer(conn, collection_id=offer["collection_id"], now=100.0)
    conn.execute("COMMIT")
    assert expired["state"] == "released" and expired["close_reason"] == "offer_expired"
    assert expired["state_version"] == bound["state_version"] + 1, "expiry is exactly one fencing bump from the bound state"
    assert ledger.owed(conn, _key())["collectible_atomic"] == 500, "the debt is back"
    assert ledger.collection_for_payment(conn, "pay-r") is None and ledger.collection_for_payment(conn, "pay-r", open_only=False)["state"] == "released"
    conn.execute("BEGIN IMMEDIATE")
    assert ledger.expire_offer(conn, collection_id=offer["collection_id"], now=200.0) is None, "a released offer is never expired twice"
    conn.execute("ROLLBACK")

"""Proof-of-settlement reputation in the mesh bid-selection trust score.

Covers, per the tranche spec:
  (a) settlement_mode stamping: 'mainnet'/'devnet' when a real receipt_hash is
      present, 'simulated' otherwise;
  (b) the real-settled aggregate math + neutral 0.5 when settled == 0;
  (c) a peer with REAL receipt-backed payouts outranks an equal-trust peer with
      simulated-only credits;
  (d) wash-trade pairs are discounted;
  (e) REGRESSION GUARD: equal trust + equal load, one SIMULATED-ONLY helper vs
      one IDLE helper -> patched selection order EQUALS baseline (simulated-only
      is NOT pushed below idle); and with NO real receipts anywhere the selection
      order is byte-for-byte the baseline.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.credit_ledger import (
    award_credits,
    escrow_credits_for_task,
    is_real_receipt_hash,
    release_escrow_to_helper,
    settlement_mode_for_receipt_hash,
)
from core.reputation_graph import (
    peer_settlement_reputation,
    settled_ratio,
    settlement_reputation_for_pair,
)
from network.assist_router import _pick_best_claim
from storage.db import get_connection, reset_default_connection
from storage.migrations import run_migrations

_REAL_HASH_A = "a" * 64  # 64-char hex SHA-256-shaped digest -> a "real" receipt
_REAL_HASH_B = "b" * 64


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    reset_default_connection()
    run_migrations()
    conn = get_connection()
    try:
        for table in (
            "compute_credit_ledger",
            "dispatch_credit_escrow",
            "task_claims",
            "task_offers",
            "contribution_ledger",
            "peers",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()
    reset_default_connection()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_peer(peer_id: str, trust_score: float) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO peers (peer_id, trust_score, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (peer_id, trust_score, _now_iso(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_task_offer(task_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type,
                summary, input_capsule_hash, required_capabilities_json,
                deadline_ts, created_at, updated_at
            ) VALUES (?, 'parent', 'capsule', 'compute', 'subtask', 'summary',
                      'hash', '[]', ?, ?, ?)
            """,
            (task_id, _now_iso(), _now_iso(), _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_claim(
    task_id: str,
    helper_peer_id: str,
    *,
    current_load: int = 0,
    claimed_at: str | None = None,
) -> str:
    claim_id = f"claim-{uuid.uuid4().hex}"
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_claims (
                claim_id, task_id, helper_peer_id, declared_capabilities_json,
                current_load, status, claimed_at, updated_at
            ) VALUES (?, ?, ?, '[]', ?, 'pending', ?, ?)
            """,
            (
                claim_id,
                task_id,
                helper_peer_id,
                current_load,
                claimed_at or _now_iso(),
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return claim_id


def _insert_ledger_row(peer_id: str, amount: float, settlement_mode: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO compute_credit_ledger (
                peer_id, amount, reason, receipt_id, settlement_mode, timestamp
            ) VALUES (?, ?, 'task_reward:test', ?, ?, ?)
            """,
            (peer_id, amount, f"r-{uuid.uuid4().hex}", settlement_mode, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _baseline_pick(task_id: str) -> tuple[str, str] | None:
    """The pre-patch ORDER BY (trust DESC, current_load ASC, claimed_at ASC)."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT c.claim_id, c.helper_peer_id
            FROM task_claims c
            LEFT JOIN peers p ON p.peer_id = c.helper_peer_id
            WHERE c.task_id = ? AND c.status = 'pending'
            ORDER BY COALESCE(p.trust_score, 0.5) DESC, c.current_load ASC, c.claimed_at ASC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        return (row["claim_id"], row["helper_peer_id"]) if row else None
    finally:
        conn.close()


def _full_baseline_order(task_id: str) -> list[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT c.claim_id
            FROM task_claims c
            LEFT JOIN peers p ON p.peer_id = c.helper_peer_id
            WHERE c.task_id = ? AND c.status = 'pending'
            ORDER BY COALESCE(p.trust_score, 0.5) DESC, c.current_load ASC, c.claimed_at ASC
            """,
            (task_id,),
        ).fetchall()
        return [r["claim_id"] for r in rows]
    finally:
        conn.close()


# (a) settlement_mode stamping ------------------------------------------------


def test_is_real_receipt_hash_distinguishes_real_vs_stub() -> None:
    assert is_real_receipt_hash(_REAL_HASH_A) is True
    assert is_real_receipt_hash(_REAL_HASH_A.upper()) is True
    assert is_real_receipt_hash(None) is False
    assert is_real_receipt_hash("") is False
    assert is_real_receipt_hash("stub-deadbeef") is False
    assert is_real_receipt_hash("abc123") is False  # too short
    assert is_real_receipt_hash("g" * 64) is False  # non-hex


def test_settlement_mode_for_receipt_hash() -> None:
    # No real hash -> stays simulated.
    assert settlement_mode_for_receipt_hash(None) == "simulated"
    assert settlement_mode_for_receipt_hash("stub-x") == "simulated"
    # Real hash, no hint -> defaults to mainnet.
    assert settlement_mode_for_receipt_hash(_REAL_HASH_A) == "mainnet"
    # Real hash honors a valid cluster hint.
    assert settlement_mode_for_receipt_hash(_REAL_HASH_A, mode_hint="devnet") == "devnet"
    assert settlement_mode_for_receipt_hash(_REAL_HASH_A, mode_hint="MAINNET") == "mainnet"
    # Invalid hint on a real hash -> mainnet default.
    assert settlement_mode_for_receipt_hash(_REAL_HASH_A, mode_hint="bogus") == "mainnet"


def _ledger_row_for_task(task_id: str) -> tuple[str, str]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT settlement_mode, receipt_hash
            FROM compute_credit_ledger
            WHERE reason = ?
            ORDER BY id DESC LIMIT 1
            """,
            (f"task_reward:{task_id}",),
        ).fetchone()
        return str(row["settlement_mode"]), str(row["receipt_hash"])
    finally:
        conn.close()


def test_release_stamps_simulated_without_real_receipt() -> None:
    tag = uuid.uuid4().hex[:6]
    poster, helper, task_id = f"poster-{tag}", f"helper-{tag}", f"task-{tag}"
    award_credits(poster, 100.0, "seed", receipt_id=f"seed-{tag}")
    escrow_credits_for_task(poster, task_id, 30.0)

    assert release_escrow_to_helper(task_id, helper, 10.0, receipt_id=f"rel-{tag}")
    mode, stamped = _ledger_row_for_task(task_id)
    assert mode == "simulated"
    assert stamped == ""


def test_release_stamps_mainnet_with_real_receipt() -> None:
    tag = uuid.uuid4().hex[:6]
    poster, helper, task_id = f"poster-{tag}", f"helper-{tag}", f"task-{tag}"
    award_credits(poster, 100.0, "seed", receipt_id=f"seed-{tag}")
    escrow_credits_for_task(poster, task_id, 30.0)

    assert release_escrow_to_helper(
        task_id, helper, 10.0, receipt_id=f"rel-{tag}", receipt_hash=_REAL_HASH_A
    )
    mode, stamped = _ledger_row_for_task(task_id)
    assert mode == "mainnet"
    assert stamped == _REAL_HASH_A


def test_release_honors_devnet_hint_with_real_receipt() -> None:
    tag = uuid.uuid4().hex[:6]
    poster, helper, task_id = f"poster-{tag}", f"helper-{tag}", f"task-{tag}"
    award_credits(poster, 100.0, "seed", receipt_id=f"seed-{tag}")
    escrow_credits_for_task(poster, task_id, 30.0)

    assert release_escrow_to_helper(
        task_id,
        helper,
        10.0,
        receipt_id=f"rel-{tag}",
        receipt_hash=_REAL_HASH_A,
        settlement_mode_hint="devnet",
    )
    mode, stamped = _ledger_row_for_task(task_id)
    assert mode == "devnet"
    assert stamped == _REAL_HASH_A


# (b) real-settled aggregate math + neutral 0.5 -------------------------------


def test_settled_ratio_is_neutral_when_no_real_settled() -> None:
    # Idle: no rewards at all -> neutral.
    assert settled_ratio(0.0, 0.0) == 0.5
    # Simulated-only: many simulated, ZERO real settled -> STILL neutral 0.5.
    # (Never 0.0 — that was the prior regression.)
    assert settled_ratio(0.0, 100.0) == 0.5
    assert settled_ratio(0.0, 1.0) == 0.5


def test_settled_ratio_rises_above_neutral_with_real_settled() -> None:
    # Fully real-settled -> max signal.
    assert settled_ratio(10.0, 0.0) == 1.0
    # Half real, half simulated -> above neutral but below max.
    val = settled_ratio(10.0, 10.0)
    assert 0.5 < val < 1.0
    assert val == pytest.approx(0.75)


def test_peer_settlement_reputation_neutral_for_simulated_only() -> None:
    sim_peer = f"sim-{uuid.uuid4().hex[:6]}"
    idle_peer = f"idle-{uuid.uuid4().hex[:6]}"
    real_peer = f"real-{uuid.uuid4().hex[:6]}"
    _insert_ledger_row(sim_peer, 50.0, "simulated")
    _insert_ledger_row(real_peer, 50.0, "mainnet")

    # Simulated-only and idle both neutral.
    assert peer_settlement_reputation(sim_peer) == 0.5
    assert peer_settlement_reputation(idle_peer) == 0.5
    # Real-receipt-backed peer is above neutral.
    assert peer_settlement_reputation(real_peer) > 0.5


# (c) real receipt-backed peer outranks equal-trust simulated-only peer --------


def test_real_settled_peer_outranks_equal_trust_simulated_peer() -> None:
    tag = uuid.uuid4().hex[:6]
    task_id = f"task-{tag}"
    real_helper = f"real-{tag}"
    sim_helper = f"sim-{tag}"
    _insert_task_offer(task_id)

    # Equal trust, equal load.
    _insert_peer(real_helper, 0.70)
    _insert_peer(sim_helper, 0.70)
    # Real helper has on-chain settled credits; sim helper has simulated-only.
    _insert_ledger_row(real_helper, 25.0, "mainnet")
    _insert_ledger_row(sim_helper, 25.0, "simulated")

    # sim_helper claims FIRST (earlier claimed_at) so under baseline it would win
    # the trust+load tie; the settlement tiebreak must promote the real helper.
    earlier = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    later = datetime.now(timezone.utc).isoformat()
    _insert_claim(task_id, sim_helper, current_load=0, claimed_at=earlier)
    _insert_claim(task_id, real_helper, current_load=0, claimed_at=later)

    picked = _pick_best_claim(task_id, parent_peer_id="parent")
    assert picked is not None
    assert picked[1] == real_helper


# (d) wash-trade pairs are discounted -----------------------------------------


def test_wash_trade_pair_is_discounted() -> None:
    tag = uuid.uuid4().hex[:6]
    parent = f"parent-{tag}"
    helper = f"helper-{tag}"

    # Give the helper a real-settled advantage so there is something to discount.
    _insert_ledger_row(helper, 100.0, "mainnet")
    base = peer_settlement_reputation(helper)
    assert base > 0.5

    # Build a closed-loop wash-trading history: many repeated parent<->helper
    # interactions and no other counterparties.
    conn = get_connection()
    try:
        for i in range(12):
            conn.execute(
                """
                INSERT INTO contribution_ledger (
                    entry_id, task_id, helper_peer_id, parent_peer_id,
                    contribution_type, helpfulness_score, outcome,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'subtask', 1.0, 'released', ?, ?)
                """,
                (
                    f"contrib-{tag}-{i}",
                    f"wt-task-{tag}-{i}",
                    helper,
                    parent,
                    _now_iso(),
                    _now_iso(),
                ),
            )
        conn.commit()
    finally:
        conn.close()

    discounted = settlement_reputation_for_pair(parent, helper)
    # The wash-traded advantage is pulled back toward neutral.
    assert discounted < base
    assert discounted >= 0.5  # never below neutral


# (e) REGRESSION GUARD --------------------------------------------------------


def test_regression_simulated_only_not_pushed_below_idle() -> None:
    """Prior bug: settled_ratio sank a simulated-only helper (0.0) below an idle
    helper (0.5). With the real-settled-total tiebreak both score 0, so the
    patched pick EQUALS the baseline pick."""
    tag = uuid.uuid4().hex[:6]
    task_id = f"task-{tag}"
    sim_helper = f"sim-{tag}"
    idle_helper = f"idle-{tag}"
    _insert_task_offer(task_id)

    # Identical trust, identical load.
    _insert_peer(sim_helper, 0.60)
    _insert_peer(idle_helper, 0.60)
    # Simulated-only credits for one; the other has NONE.
    _insert_ledger_row(sim_helper, 40.0, "simulated")

    # sim_helper claims first -> baseline selects sim_helper.
    earlier = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    later = datetime.now(timezone.utc).isoformat()
    _insert_claim(task_id, sim_helper, current_load=0, claimed_at=earlier)
    _insert_claim(task_id, idle_helper, current_load=0, claimed_at=later)

    baseline = _baseline_pick(task_id)
    patched = _pick_best_claim(task_id, parent_peer_id="parent")
    assert patched == baseline
    assert patched is not None
    assert patched[1] == sim_helper  # NOT demoted below the idle newcomer


def test_regression_no_real_receipts_order_is_byte_for_byte_baseline() -> None:
    """With NO real receipts anywhere, the patched selection order must equal the
    baseline order exactly across a mixed field of helpers."""
    tag = uuid.uuid4().hex[:6]
    task_id = f"task-{tag}"
    _insert_task_offer(task_id)

    # A spread of trust / load / simulated-credit profiles, no real receipts.
    helpers = [
        (f"h0-{tag}", 0.90, 2, 30.0),
        (f"h1-{tag}", 0.90, 0, 0.0),
        (f"h2-{tag}", 0.50, 1, 10.0),
        (f"h3-{tag}", 0.75, 0, 5.0),
        (f"h4-{tag}", 0.50, 0, 0.0),
    ]
    base_time = datetime.now(timezone.utc) - timedelta(minutes=30)
    for i, (peer, trust, load, sim_amt) in enumerate(helpers):
        _insert_peer(peer, trust)
        if sim_amt > 0:
            _insert_ledger_row(peer, sim_amt, "simulated")
        _insert_claim(
            task_id,
            peer,
            current_load=load,
            claimed_at=(base_time + timedelta(minutes=i)).isoformat(),
        )

    # Patched top-1 equals baseline top-1.
    patched = _pick_best_claim(task_id, parent_peer_id="parent")
    baseline = _baseline_pick(task_id)
    assert patched == baseline

    # And the patched ORDER BY produces the byte-for-byte baseline ordering.
    conn = get_connection()
    try:
        patched_order = [
            r["claim_id"]
            for r in conn.execute(
                """
                SELECT c.claim_id,
                       (
                         SELECT COALESCE(SUM(l.amount), 0)
                         FROM compute_credit_ledger l
                         WHERE l.peer_id = c.helper_peer_id
                           AND l.amount > 0
                           AND l.settlement_mode IN ('mainnet', 'devnet')
                       ) AS settled_amount
                FROM task_claims c
                LEFT JOIN peers p ON p.peer_id = c.helper_peer_id
                WHERE c.task_id = ? AND c.status = 'pending'
                ORDER BY COALESCE(p.trust_score, 0.5) DESC,
                         settled_amount DESC,
                         c.current_load ASC,
                         c.claimed_at ASC
                """,
                (task_id,),
            ).fetchall()
        ]
    finally:
        conn.close()
    assert patched_order == _full_baseline_order(task_id)

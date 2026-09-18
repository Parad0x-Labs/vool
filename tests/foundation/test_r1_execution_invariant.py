"""R-1 / H-1 three-layer execution invariant tests.

INV-1 authority = fence generation+epoch CAS · INV-2 executing exclusivity =
PLANNED∪RUNNING index + aborting claim CAS · INV-3 supersession = durable
transition written by the successor's mint unit.
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.runtime_continuity import (
    AttemptClaimRefused,
    AttemptLifecycle,
    MintRefused,
    claim_runtime_attempt,
    create_runtime_attempt,
    get_runtime_attempt,
    mark_effect_dispatched,
    reserve_logical_effect,
    resolve_unresolved_effect,
    update_runtime_attempt,
)
from core.runtime_continuity import compute_logical_effect_id


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "r1.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _retry(parent, *, trigger="turn-r1", gen=2):
    return create_runtime_attempt(
        session_id="s1",
        original_request="req",
        answer_mode="LIVE_DATA",
        parent_attempt_id=parent["attempt_id"],
        root_attempt_id=parent["root_attempt_id"],
        trigger_user_turn_id=trigger,
        execution_generation=gen,
    )


# --- INV-3 supersession ------------------------------------------------------

def test_retry_mint_supersedes_nonterminal_predecessor(fresh_store):
    parent = create_runtime_attempt(session_id="s1", original_request="req")
    assert parent["lifecycle_state"] == "RECEIVED"
    child = _retry(parent)
    assert child["idempotent_replay"] is False
    # RED MUTATION R1 target: predecessor must be durably SUPERSEDED.
    row = get_runtime_attempt(parent["attempt_id"])
    assert row["lifecycle_state"] == AttemptLifecycle.SUPERSEDED.value


def test_mint_refused_over_pending_reconciliation(fresh_store):
    parent = create_runtime_attempt(session_id="s1", original_request="req")
    child = _retry(parent, trigger="turn-a")
    update_runtime_attempt(
        child["attempt_id"], lifecycle_state="PENDING_RECONCILIATION",
        terminal_reason="unknown effect",
    )
    with pytest.raises(MintRefused, match="PENDING_RECONCILIATION"):
        _retry(child, trigger="turn-b")


def test_mint_refused_over_unreconciled_unknown_effect(fresh_store):
    parent = create_runtime_attempt(session_id="s1", original_request="req")
    leid = compute_logical_effect_id(intent="email.send", arguments={"to": "x@y.z"})
    reserve_logical_effect(
        intent="email.send", arguments={"to": "x@y.z"},
        session_id="s1", attempt_id=parent["attempt_id"],
    )
    mark_effect_dispatched(
        logical_effect_id=leid,
        effect_instance_id=reserve_logical_effect.__defaults__ and "" or "",  # placeholder
    ) if False else None
    from core.runtime_continuity import find_active_unresolved_effect

    active = find_active_unresolved_effect(leid)
    assert active is not None
    mark_effect_dispatched(
        logical_effect_id=leid,
        effect_instance_id=str(active["effect_instance_id"]),
        claimed_by="r1-test",
    )
    # RED MUTATION R6 target: UNKNOWN effects block successor minting.
    with pytest.raises(MintRefused, match="UNRESOLVED_EFFECTS"):
        _retry(parent)
    resolve_unresolved_effect(
        logical_effect_id=leid,
        resolution="CONFIRMED_FAILED_SAFE_TO_RETRY",
        source="mechanical",
    )
    # After reconciliation the mint proceeds.
    child = _retry(parent)
    assert child["attempt_id"]


# --- INV-2 executing exclusivity ---------------------------------------------

def test_claim_cas_second_claimant_aborts(fresh_store):
    """RED MUTATION R2: two claimants, one EXECUTING slot; the loser's refusal
    is a raised typed error — never a swallowed write."""
    import uuid as _uuid

    ex = f"ex-r2-{_uuid.uuid4().hex[:8]}"
    conn = sdb.get_connection()
    try:
        for aid in (f"{ex}-a", f"{ex}-b"):
            conn.execute(
                "INSERT INTO runtime_attempts (attempt_id, session_id, execution_id,"
                " lifecycle_state, created_at, updated_at)"
                " VALUES (?, 's1', ?, 'RECEIVED', 't', 't')",
                (aid, ex),
            )
        conn.commit()
    finally:
        conn.close()
    claim_runtime_attempt(f"{ex}-a", to_state="RUNNING")
    with pytest.raises(AttemptClaimRefused):
        claim_runtime_attempt(f"{ex}-b", to_state="RUNNING")


def test_two_planned_rows_on_one_execution_violate_index(fresh_store):
    """RED MUTATION R4: the widened PLANNED∪RUNNING predicate fires."""
    import sqlite3 as _sq
    import uuid as _uuid

    ex = f"ex-r4-{_uuid.uuid4().hex[:8]}"
    conn = sdb.get_connection()
    try:
        for aid in (f"{ex}-a", f"{ex}-b"):
            conn.execute(
                "INSERT INTO runtime_attempts (attempt_id, session_id, execution_id,"
                " lifecycle_state, created_at, updated_at)"
                " VALUES (?, 's1', ?, 'RECEIVED', 't', 't')",
                (aid, ex),
            )
        conn.commit()
        # RECEIVED rows coexist legally...
        with pytest.raises(_sq.IntegrityError):
            # ...but two PLANNED rows on one execution are refused by the index.
            conn.execute(
                "UPDATE runtime_attempts SET lifecycle_state='PLANNED'"
            )
        conn.rollback()
    finally:
        conn.close()


# --- TERMINAL ABSORBS ---------------------------------------------------------

def test_terminal_row_refuses_resurrection(fresh_store):
    a = create_runtime_attempt(session_id="s1", original_request="req")
    update_runtime_attempt(a["attempt_id"], lifecycle_state="SUCCEEDED")
    # RED MUTATION R7 target: resurrecting SUCCEEDED to RUNNING must refuse.
    result = update_runtime_attempt(a["attempt_id"], lifecycle_state="RUNNING")
    assert result is not None and result.get("transition_refused") == "terminal_absorbs"
    assert get_runtime_attempt(a["attempt_id"])["lifecycle_state"] == "SUCCEEDED"

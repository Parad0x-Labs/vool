"""Residue-3: SQL-folded fence predicates on delivery writes.

A delivery write from an onboarded lane presents (execution_id, generation,
runtime_epoch); a stale tuple is refused BEFORE any row mutates. Red mutation:
removing the predicate lets a stale writer upgrade delivery truth.
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.invocation.ledger import (
    accept_invocation,
    bump_generation,
    current_runtime_epoch,
    open_execution,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "res3.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _commit_with_identity():
    from core.conductor import obligation_ledger as ol
    from core.finalization import finalize_answer
    from core.semantic.semantic_admissions import set_execution_context
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    obset = ol.open_obligation_set(
        obligations=[{"obligation_id": "ob:answer", "text": "t", "kind": "prose"}]
    )
    ol.bind_active_set(obset["set_id"], obset["version"])
    ol.record_disposition(
        obset["set_id"], obset["version"], "ob:answer",
        "satisfied", evidence_source="served_bytes",
    )
    req = accept_invocation(
        external_kind="http", external_value="res3", principal="owner_local", raw_digest="d"
    )["request_id"]
    ex = open_execution(request_id=req, root_attempt_id="attempt-res3")
    identity = {
        "execution_id": ex["execution_id"],
        "generation": int(ex["generation"]),
        "runtime_epoch": current_runtime_epoch(),
        "request_id": req,
    }
    set_execution_context(identity)
    reset_admission()
    admit_semantic_result({"response": "res3 bytes", "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t", canonical_content="res3 bytes")
    return commit, identity


def test_stale_generation_delivery_write_refused_row_unchanged(fresh_store):
    from core.finalization import (
        DELIVERY_ATTEMPTED_UNKNOWN,
        DELIVERY_DELIVERED,
        set_delivery_status,
    )

    commit, identity = _commit_with_identity()
    fid = commit["finalization_id"]
    bump_generation(identity["execution_id"])  # fence moves; we are stale
    stale = {**identity, "generation": 0}
    # RED MUTATION target: without the folded predicate this write succeeds.
    with pytest.raises(Exception, match="fence mismatch"):
        set_delivery_status(
            fid, DELIVERY_DELIVERED, evidence_class="PLATFORM_ACK",
            execution_identity=stale,
        )
    # Row untouched by the refused write.
    conn = sdb.get_connection()
    try:
        row = conn.execute(
            "SELECT delivery_status FROM a7_finalizations WHERE finalization_id = ?", (fid,)
        ).fetchone()
        assert row["delivery_status"] == "NOT_ATTEMPTED"
    finally:
        conn.close()


def test_current_identity_delivery_write_succeeds(fresh_store):
    from core.finalization import DELIVERY_ATTEMPTED_UNKNOWN, set_delivery_status

    commit, identity = _commit_with_identity()
    fid = commit["finalization_id"]
    assert set_delivery_status(
        fid, DELIVERY_ATTEMPTED_UNKNOWN, execution_identity=identity
    )


def test_unbound_writer_still_allowed_legacy_bridge(fresh_store):
    from core.conductor import obligation_ledger as _ol
    from core.semantic.semantic_admissions import (
        clear_execution_context,
        set_request_context,
    )

    _ol.clear_active_set()
    clear_execution_context()
    set_request_context("")
    """Bridges/legacy writers carry no fence tuple — the write proceeds under
    the monotone machine only (no permanent fail-open for onboarded lanes,
    which always pass identity via the sealed payload)."""
    from core.finalization import DELIVERY_ATTEMPTED_UNKNOWN, finalize_answer, set_delivery_status
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    reset_admission()
    admit_semantic_result({"response": "legacy bytes", "route_reason": "model_lane"})
    commit = finalize_answer(turn_id="t2", canonical_content="legacy bytes")
    assert set_delivery_status(commit["finalization_id"], DELIVERY_ATTEMPTED_UNKNOWN)

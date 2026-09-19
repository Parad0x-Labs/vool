"""R-4 / H-2: fence predicates at outcome-bearing writers.

RED MUTATION R5 target: stale-generation finalize must be refused.
RM5-class: stale-epoch dispatch claim must be refused.
"""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.conductor import obligation_ledger as _ol_teardown
from core.invocation.ledger import (
    accept_invocation,
    bump_generation,
    open_execution,
)
from core.semantic.semantic_admissions import (
    clear_execution_context,
    enforce_fence_if_onboarded,
    set_execution_context,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "r4.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    _ol_teardown.clear_active_set()
    sdb.configure_default_db_path(None)
    clear_execution_context()


def _bind_turn_obligations(attempt_id="attempt-r4"):
    from core.conductor import obligation_ledger as ol

    obset = ol.open_obligation_set(
        obligations=[{"obligation_id": f"ob:{attempt_id}:answer", "text": "t", "kind": "prose"}]
    )
    ol.bind_active_set(obset["set_id"], obset["version"])
    ol.record_disposition(
        obset["set_id"], obset["version"], f"ob:{attempt_id}:answer",
        "satisfied", evidence_source="served_bytes",
    )
    return obset


def _bound_identity(req_value="r4-fence"):
    req = accept_invocation(
        external_kind="http", external_value=req_value, principal="owner_local",
        raw_digest="d",
    )["request_id"]
    ex = open_execution(request_id=req, root_attempt_id=f"attempt-{req_value}")
    return {
        "execution_id": ex["execution_id"],
        "generation": int(ex["generation"]),
        "runtime_epoch": __import__(
            "core.invocation.ledger", fromlist=["current_runtime_epoch"]
        ).current_runtime_epoch(),
        "request_id": req,
    }


def test_stale_generation_finalize_refused_zero_rows(fresh_store):
    from core.finalization import FinalizationRejected, finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    identity = _bound_identity("r4-stale")
    bump_generation(identity["execution_id"])  # fence moves on: we become stale
    set_execution_context({**identity, "generation": 0})
    try:
        reset_admission()
        admit_semantic_result({"response": "stale bytes", "route_reason": "model_lane"})
        # RED MUTATION R5: removing the fence check would let this succeed.
        with pytest.raises(FenceRefused_type(), match="fence mismatch"):
            finalize_answer(turn_id="t", canonical_content="stale bytes")
    finally:
        clear_execution_context()


def FenceRefused_type():
    from core.invocation.ledger import FenceRefused

    return FenceRefused


def test_current_generation_finalize_succeeds(fresh_store):
    _bind_turn_obligations("attempt-r4-current")
    from core.finalization import finalize_answer
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    identity = _bound_identity("r4-current")
    set_execution_context(identity)
    try:
        reset_admission()
        admit_semantic_result({"response": "fresh bytes", "route_reason": "model_lane"})
        commit = finalize_answer(turn_id="t", canonical_content="fresh bytes")
        assert commit["binding_outcome"] in ("ACCEPTED_FIRST", "ACCEPTED_IDENTICAL")
    finally:
        clear_execution_context()


def test_stale_epoch_dispatch_claim_refused(fresh_store):
    from core.runtime_continuity import (
        mark_effect_dispatched,
        reserve_logical_effect,
    )

    reserve_logical_effect(
        intent="email.send", arguments={"to": "a@b.c"},
        session_id="s", attempt_id="attempt-r4",
    )
    stale = _bound_identity("r4-epoch")
    stale_identity = {**stale, "runtime_epoch": "proc-dead-process"}
    with pytest.raises(Exception, match="fence mismatch"):
        leid = __import__(
            "core.runtime_continuity", fromlist=["compute_logical_effect_id"]
        ).compute_logical_effect_id(intent="email.send", arguments={"to": "a@b.c"})
        from core.runtime_continuity import find_active_unresolved_effect

        active = find_active_unresolved_effect(leid)
        mark_effect_dispatched(
            logical_effect_id=leid,
            effect_instance_id=str(active["effect_instance_id"]),
            claimed_by="r4-test",
            execution_identity=stale_identity,
        )


def test_unbound_lane_skips_fence_check(fresh_store):
    """Not-yet-onboarded (legacy) lanes skip — no permanent fail-open limbo,
    but also no false refusals while wiring is staged."""
    assert enforce_fence_if_onboarded() is None
